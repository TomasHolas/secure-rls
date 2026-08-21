"""Adversarial suite for tenant-filtered retrieval (issue #18, ADR 0010).

`FakeEmbed` is the only embedder here: a hashed bag of words, so "semantically close" means
"shares words" and every distance in this file is reproducible without a model, a network, or
Ollama. The dataset is deliberately rigged - the ONLY note that answers the acme query lives in
beta - so a passing isolation test cannot be explained by the query simply matching nothing.

The vec0 partition key makes cross-tenant retrieval structurally impossible, which means the
egress check cannot be tripped by any real query; the test that proves the check works doctors
`db.search_vectors` instead, the same way the SQL suite stands a layer down to reach the one
below it.
"""

import hashlib
import math
import re
from dataclasses import replace

import pytest
import sqlite_vec

import db
import rag
from db import SecurityViolation
from security import QueryRejected

# Reached through db so this file stays clear of the import the connection-owner guard forbids.
_ACTIONS = db.sqlite3
_OK = _ACTIONS.SQLITE_OK
_DENY = _ACTIONS.SQLITE_DENY

ACME = "acme"
BETA = "beta"
GAMMA = "gamma"

# The version pyproject pins. sqlite-vec is pre-v1: on any bump this assertion is the prompt to
# re-run this file, whose isolation tests re-prove the invariant against the new build.
PINNED_SQLITE_VEC = "0.1.9"

_DIM = 32
_HEADER = (
    "user_id",
    "tenant_id",
    "name",
    "department",
    "salary",
    "performance_score",
    "hire_date",
    "notes",
)
# Only Bo's note answers the leadership query, and Bo is in beta: the trap the isolation test needs.
_LEADERSHIP_NOTE = "exceptional leadership potential mentors the whole team"
_ROWS = (
    (1, ACME, "Ada", "Engineering", 100, 4.1, "2020-01-01", "refactored the billing pipeline"),
    (2, ACME, "Alan", "Engineering", 200, 3.2, "2021-02-02", "steady delivery on the api work"),
    (3, ACME, "Amir", "Sales", 300, 2.5, "2022-03-03", "learning the crm tooling"),
    (4, ACME, "Ann", "Sales", 400, 4.8, "2020-04-04", "closed the largest renewal"),
    (5, BETA, "Bo", "Engineering", 1000, 4.4, "2021-07-07", _LEADERSHIP_NOTE),
    (6, BETA, "Bea", "Engineering", 2000, 2.9, "2022-08-08", "shipped the migration scripts"),
    (7, GAMMA, "Gil", "Finance", 9999, 4.0, "2024-10-10", "reconciled the quarterly ledger"),
)
_ACME_NOTES = frozenset(row[7] for row in _ROWS if row[1] == ACME)
_ACME_ROW_COUNT = sum(1 for row in _ROWS if row[1] == ACME)
_LEADERSHIP_QUERY = "who shows leadership potential"
_WORD = re.compile(r"[a-z0-9]+")


class FakeEmbed:
    """A deterministic, network-free embedder: hashed bag of words, unit length, stable hash."""

    def __init__(self, dim: int = _DIM) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in input order, identical on every machine and every run."""
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        counts = [0.0] * self._dim
        for word in _WORD.findall(text.lower()):
            counts[_bucket(word, self._dim)] += 1.0
        norm = math.sqrt(sum(value * value for value in counts)) or 1.0
        return [value / norm for value in counts]


def _never_indexes(*args: object, **kwargs: object) -> int:
    """Fail the test if a store that already holds notes is embedded a second time."""
    raise AssertionError("an existing note index must not be rebuilt at startup")


def _bucket(word: str, dim: int) -> int:
    """A process-independent bucket for one word; Python's own hash() is salted per run."""
    digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


@pytest.fixture
def db_path(tmp_path):
    """A loaded database with its note index built, from the inline rows only."""
    csv_path = tmp_path / "employees.csv"
    lines = [",".join(_HEADER)]
    lines += [",".join(str(field) for field in row) for row in _ROWS]
    csv_path.write_text("\n".join(lines) + "\n")
    path = tmp_path / "data.db"
    db.init_db(csv_path, path)
    rag.index_notes(path, FakeEmbed())
    return path


@pytest.fixture
def search(db_path):
    """Run a scoped note search against the fixture index."""

    def run(query, tenant, k=None):
        return rag.search_notes_scoped(db_path, FakeEmbed(), query, tenant, k)

    return run


def test_indexing_covers_every_row_across_every_tenant(tmp_path):
    csv_path = tmp_path / "employees.csv"
    lines = [",".join(_HEADER)]
    lines += [",".join(str(field) for field in row) for row in _ROWS]
    csv_path.write_text("\n".join(lines) + "\n")
    path = tmp_path / "data.db"
    db.init_db(csv_path, path)
    assert rag.index_notes(path, FakeEmbed()) == len(_ROWS)
    assert path.with_name(db.VECTOR_DB_NAME).exists()


def test_the_only_matching_note_in_another_tenant_is_never_returned(search):
    """The rigged case: beta holds the one note that answers the query, acme asks it."""
    hits = search(_LEADERSHIP_QUERY, ACME)
    assert hits, "acme must still get its own nearest notes, not an error"
    assert {hit["note"] for hit in hits} <= _ACME_NOTES
    assert all(_LEADERSHIP_NOTE not in str(hit["note"]) for hit in hits)
    assert all(hit["name"] != "Bo" for hit in hits)


def test_the_owning_tenant_does_get_that_same_note(search):
    """The isolation above is scoping, not a broken index: beta finds what acme could not."""
    assert search(_LEADERSHIP_QUERY, BETA)[0]["note"] == _LEADERSHIP_NOTE


def test_the_withheld_note_was_the_closest_one_in_the_whole_corpus(search):
    """Without this the test above proves nothing: acme was denied the actual best answer."""
    beta_best = search(_LEADERSHIP_QUERY, BETA)[0]["distance"]
    acme_best = search(_LEADERSHIP_QUERY, ACME)[0]["distance"]
    assert beta_best < acme_best


def test_the_query_shape_binds_the_tenant_and_the_table_is_partitioned():
    """The ADR 0010 claim in one assertion: a pre-filter on a partition key, never interpolated."""
    assert f"{db.TENANT_COLUMN} = ?" in db._VECTOR_SEARCH
    assert "AND k = ?" in db._VECTOR_SEARCH
    assert f"{db.TENANT_COLUMN} text partition key" in db._VECTOR_SCHEMA


def test_a_tenant_with_nothing_indexed_gets_a_neutral_empty_list(search):
    assert search(_LEADERSHIP_QUERY, "nosuchtenant") == []


@pytest.mark.parametrize(
    "hostile",
    ["acme' OR '1'='1", "acme' UNION SELECT * FROM note_vectors --", "acme%", "*", "' OR 1=1 --"],
)
def test_a_tenant_that_is_itself_an_injection_matches_no_partition(search, hostile):
    """The tenant is a bound parameter, so a crafted one is a partition name that does not exist."""
    assert search(_LEADERSHIP_QUERY, hostile) == []


def test_a_result_never_reaches_beyond_the_tenants_own_rows(search):
    """k above the partition size returns the tenant's rows and stops, never borrowing others."""
    assert len(search(_LEADERSHIP_QUERY, ACME, k=50)) == _ACME_ROW_COUNT


def test_k_caps_the_returned_notes(search):
    assert len(search(_LEADERSHIP_QUERY, ACME, k=2)) == 2


def test_the_default_k_comes_from_runtime_json(search, monkeypatch):
    monkeypatch.setattr(rag, "runtime", _with_top_k(1))
    assert len(search(_LEADERSHIP_QUERY, ACME)) == 1


def test_a_k_above_the_extension_ceiling_is_capped_not_an_error(search):
    """sqlite-vec refuses k > 4096 outright; db.py clamps so a caller's number cannot error."""
    assert len(search(_LEADERSHIP_QUERY, ACME, k=10_000)) == _ACME_ROW_COUNT


def test_the_returned_shape_is_the_contract(search):
    assert set(search(_LEADERSHIP_QUERY, ACME, k=1)[0]) == {"user_id", "name", "note", "distance"}


def test_the_egress_check_refuses_a_doctored_result(db_path, monkeypatch):
    """Layer 4: if the store ever yielded a foreign chunk, the caller sees a violation."""
    foreign = db.VectorMatch(
        user_id=5, tenant_id=BETA, name="Bo", note=_LEADERSHIP_NOTE, distance=0.0
    )
    monkeypatch.setattr(db, "search_vectors", lambda *args, **kwargs: [foreign])
    with pytest.raises(SecurityViolation) as caught:
        rag.search_notes_scoped(db_path, FakeEmbed(), _LEADERSHIP_QUERY, ACME)
    assert caught.value.kind == "rag_egress_mismatch"
    assert BETA in caught.value.reason


def test_the_egress_check_passes_a_result_that_is_entirely_the_callers_own(search):
    assert search(_LEADERSHIP_QUERY, GAMMA)[0]["name"] == "Gil"


def test_generated_sql_cannot_reach_the_vector_table(db_path):
    """The index lives in its own file, and the SQL allowlist refuses to name it either way."""
    with pytest.raises(QueryRejected):
        db.execute_scoped(f"SELECT * FROM {db.VECTOR_TABLE}", ACME, db_path=db_path)


def test_searching_before_the_index_exists_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        db.search_vectors(tmp_path / "data.db", [0.0] * _DIM, ACME, 5)


def test_a_scoped_search_before_the_index_exists_is_an_availability_error(tmp_path):
    """The tool layer needs to tell an operator condition apart from a model or security error."""
    with pytest.raises(rag.RetrievalUnavailable):
        rag.search_notes_scoped(tmp_path / "data.db", FakeEmbed(), _LEADERSHIP_QUERY, ACME)


def test_an_empty_store_counts_as_no_index_at_all(tmp_path):
    """Startup decides on rows held, not on a file existing (ADR 0010 as amended)."""
    assert db.vector_store_rows(tmp_path / "data.db") == 0


def test_ensuring_the_index_builds_it_once_and_then_leaves_it_alone(tmp_path, monkeypatch):
    """The startup build is idempotent: a store that already holds notes costs no embeddings."""
    csv_path = tmp_path / "employees.csv"
    lines = [",".join(_HEADER)]
    lines += [",".join(str(field) for field in row) for row in _ROWS]
    csv_path.write_text("\n".join(lines) + "\n")
    path = tmp_path / "data.db"
    db.init_db(csv_path, path)

    assert rag.ensure_index(path, FakeEmbed()) == len(_ROWS)
    assert db.vector_store_rows(path) == len(_ROWS)

    monkeypatch.setattr(rag, "index_notes", _never_indexes)
    assert rag.ensure_index(path, FakeEmbed()) == len(_ROWS)


def test_the_vector_authorizer_allows_only_the_stores_own_storage():
    """The allowlist the empirical probe produced: this virtual table, its shadow tables, match."""
    read = _ACTIONS.SQLITE_READ
    guard = db._VectorGuard()
    for suffix in ("", "_rowids", "_chunks", "_auxiliary"):
        assert guard.authorize(read, f"{db.VECTOR_TABLE}{suffix}", "id", "main", None) == _OK
    assert guard.authorize(read, "employees", "notes", "main", None) == _DENY
    assert "employees" in str(guard.denied)


def test_the_vector_authorizer_allows_match_and_nothing_else():
    function = _ACTIONS.SQLITE_FUNCTION
    assert db._VectorGuard().authorize(function, None, "match", None, None) == _OK
    assert db._VectorGuard().authorize(function, None, "load_extension", None, None) == _DENY


def test_the_vector_authorizer_denies_an_action_it_was_not_designed_for():
    guard = db._VectorGuard()
    assert guard.authorize(_ACTIONS.SQLITE_ATTACH, None, None, None, None) == _DENY
    assert isinstance(guard.explain(_ACTIONS.Error("x")), SecurityViolation)


def test_the_vector_authorizer_leaves_an_untripped_engine_error_alone():
    """Only a denial becomes a security event; a plain engine failure stays what it was."""
    error = _ACTIONS.Error("disk gone")
    assert db._VectorGuard().explain(error) is error


def test_a_mismatched_notes_and_vectors_batch_is_refused(tmp_path):
    notes = [db.NoteRow(tenant_id=ACME, user_id=1, name="Ada", note="a note")]
    with pytest.raises(ValueError):
        db.init_vector_store(tmp_path / "data.db", notes, [])
    with pytest.raises(ValueError):
        db.init_vector_store(tmp_path / "data.db", notes, [[0.0] * _DIM, [0.0] * _DIM])


def test_ragged_vectors_are_refused_before_the_table_is_declared(tmp_path):
    notes = [
        db.NoteRow(tenant_id=ACME, user_id=1, name="Ada", note="a note"),
        db.NoteRow(tenant_id=ACME, user_id=2, name="Alan", note="another note"),
    ]
    with pytest.raises(ValueError):
        db.init_vector_store(tmp_path / "data.db", notes, [[0.0] * _DIM, [0.0] * (_DIM - 1)])


def test_the_pinned_sqlite_vec_version_is_the_one_these_tests_proved():
    """A pre-v1 dependency: the pin is the contract, and this file is the re-proof on any bump."""
    assert sqlite_vec.__version__ == PINNED_SQLITE_VEC


def test_the_fake_embedder_is_deterministic_and_order_preserving():
    """Everything above rests on this: same text, same vector, every run and every machine."""
    first, second = FakeEmbed().embed([_LEADERSHIP_NOTE, _LEADERSHIP_QUERY])
    assert (first, second) == tuple(FakeEmbed().embed([_LEADERSHIP_NOTE, _LEADERSHIP_QUERY]))
    assert first != second
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)


def _with_top_k(top_k):
    """A runtime view with rag.top_k overridden, leaving every other knob as configured."""
    config = rag.runtime()
    patched = replace(config, rag=replace(config.rag, top_k=top_k))
    return lambda: patched
