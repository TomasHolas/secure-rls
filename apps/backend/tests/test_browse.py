"""Suite for the Records and Notes browse templates (issue #88, ADR 0014).

The fixture is a tiny inline dataset loaded through `init_db` into tmp_path, never the committed
employees.csv, and beta's rows are planted to be caught: its names contain the substrings acme's
name filter searches for ("Adalovelace" answers "ada", "Grace Beta" answers "grace"), its
departments are acme's departments, and its salaries sit far outside acme's range. So every
filter, sort and page below has a foreign row that would match it, and each assertion is about
the executor keeping that row out rather than about the filter being narrow.

The hostile-input cases are the point of the module: quotes, comment markers, a UNION, LIKE
wildcards and a template placeholder are typed into the filter boxes, and each must come back as
an ordinary empty result over the caller's own rows - no foreign row, no raised error, no leaked
statement. That they cannot do anything else is structural, since the values never reach the SQL
as text: they are bound, and `db.py`'s layer 4a proves the arrangement (ADR 0002 as amended).
"""

import csv

import pytest

import browse
import db
from browse import (
    DEFAULT_SORT,
    Filters,
    browse_notes,
    browse_records,
    departments,
    flagged_user_ids,
)
from runtime import runtime
from security import QueryRejected

ACME = "acme"
BETA = "beta"

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
_ROWS = (
    (1, ACME, "Ada Lovelace", "Engineering", 100, 4.5, "2019-01-01", "shipped the compiler"),
    (2, ACME, "Alan Turing", "Engineering", 200, 3.5, "2020-02-02", "strong on theory"),
    (3, ACME, "Grace Hopper", "Sales", 300, 2.5, "2021-03-03", "owns the pipeline"),
    (4, ACME, "Ada Byron", "Sales", 400, 5.0, "2022-04-04", "top closer this year"),
    (5, ACME, "Katherine Johnson", "Finance", 500, 1.5, "2023-05-05", "needs support"),
    (6, BETA, "Adalovelace Beta", "Engineering", 999999, 5.0, "2019-01-01", "beta secret"),
    (7, BETA, "Beta Closer", "Sales", 1, 1.0, "2024-06-06", "beta secret"),
    (8, BETA, "Grace Beta", "Finance", 42, 3.0, "2020-01-01", "beta secret"),
)

_ACME_ROWS = 5
_BETA_ROWS = 3
_ACME_NAMES = ("Ada Lovelace", "Alan Turing", "Grace Hopper", "Ada Byron", "Katherine Johnson")
_SCOPED = "(SELECT * FROM employees WHERE employees.tenant_id = ?)"
_BETA_SECRET = "beta secret"

# Every one of these is typed into a filter box; none may be read as anything but text.
_HOSTILE = (
    "' OR 1=1 --",
    "'; DROP TABLE employees; --",
    "x' UNION SELECT * FROM employees WHERE tenant_id='beta' --",
    "%",
    "_",
    "%%",
    "\\",
    "?",
    ":tenant",
    "Ada' AND tenant_id='beta",
    "*",
    "[a-z]",
)


@pytest.fixture
def db_path(tmp_path):
    """A database loaded from the inline dataset; the committed employees.csv is never read."""
    csv_path = tmp_path / "employees.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(_HEADER)
        writer.writerows(_ROWS)
    path = tmp_path / "data.db"
    db.init_db(csv_path, path)
    return path


def _column(page, name):
    """The values of one column of a page, in the order the page serves them."""
    return [row[page.columns.index(name)] for row in page.rows]


def test_a_records_page_serves_only_the_callers_rows(db_path):
    """The tenant is bound by the executor, so the same call reads two disjoint sets of rows."""
    acme = browse_records(ACME, db_path=db_path)
    beta = browse_records(BETA, db_path=db_path)
    assert acme.total == _ACME_ROWS
    assert beta.total == _BETA_ROWS
    assert set(_column(acme, "tenant_id")) == {ACME}
    assert set(_column(beta, "tenant_id")) == {BETA}
    assert sorted(_column(acme, "name")) == sorted(_ACME_NAMES)


def test_a_records_page_runs_the_scoped_template_through_the_executor(db_path):
    """The executed statement is the rewritten one: the tenant is a bound subquery, not a value."""
    page = browse_records(ACME, filters=Filters(name="ada"), db_path=db_path)
    assert _SCOPED in page.executed_sql
    assert ACME not in page.executed_sql
    assert page.executed_sql.count("?") == 2


def test_every_browse_query_is_audited_like_any_other(db_path):
    """The same audit trail: a page costs two approved rows, its total and its window."""
    browse_records(ACME, db_path=db_path)
    entries = db.audit_entries(db_path)
    assert [entry.verdict for entry in entries] == [db.VERDICT_APPROVED] * 2
    assert {entry.tenant for entry in entries} == {ACME}


def test_the_name_filter_matches_a_substring_of_the_callers_names_only(db_path):
    """Beta's "Adalovelace" answers the same substring and is still never served."""
    page = browse_records(ACME, filters=Filters(name="ada"), db_path=db_path)
    assert sorted(_column(page, "name")) == ["Ada Byron", "Ada Lovelace"]
    assert page.total == 2


def test_the_name_filter_ignores_case(db_path):
    assert browse_records(ACME, filters=Filters(name="ADA LOVE"), db_path=db_path).total == 1


def test_a_blank_filter_box_filters_nothing(db_path):
    """An empty or whitespace box is absent, not a match on the empty string."""
    page = browse_records(ACME, filters=Filters(name="   ", department=""), db_path=db_path)
    assert page.total == _ACME_ROWS


def test_the_department_filter_is_an_exact_case_insensitive_match(db_path):
    page = browse_records(ACME, filters=Filters(department="engineering"), db_path=db_path)
    assert sorted(_column(page, "name")) == ["Ada Lovelace", "Alan Turing"]


def test_the_range_filters_bound_both_ends_inclusively(db_path):
    page = browse_records(ACME, filters=Filters(salary_min=200, salary_max=400), db_path=db_path)
    assert sorted(_column(page, "salary")) == [200, 300, 400]


def test_the_score_and_hire_date_filters_narrow_the_same_way(db_path):
    scored = browse_records(ACME, filters=Filters(score_min=3.5), db_path=db_path)
    hired = browse_records(
        ACME, filters=Filters(hired_from="2021-01-01", hired_to="2022-12-31"), db_path=db_path
    )
    assert sorted(_column(scored, "performance_score")) == [3.5, 4.5, 5.0]
    assert sorted(_column(hired, "hire_date")) == ["2021-03-03", "2022-04-04"]


def test_filters_combine_as_a_conjunction(db_path):
    page = browse_records(
        ACME, filters=Filters(name="ada", department="sales"), db_path=db_path
    )
    assert _column(page, "name") == ["Ada Byron"]


@pytest.mark.parametrize("direction,expected", [("asc", [100, 200]), ("desc", [500, 400])])
def test_an_allowlisted_sort_orders_the_page(db_path, direction, expected):
    page = browse_records(
        ACME, sort="salary", direction=direction, page_size=2, db_path=db_path
    )
    assert _column(page, "salary") == expected


def test_paging_covers_every_row_exactly_once(db_path):
    """The primary key breaks every tie, so no page boundary can drop or repeat a row."""
    seen = []
    for number in (1, 2, 3):
        page = browse_records(
            ACME, sort="department", page=number, page_size=2, db_path=db_path
        )
        assert page.total == _ACME_ROWS
        assert page.page == number
        seen.extend(_column(page, "user_id"))
    assert sorted(seen) == [1, 2, 3, 4, 5]


def test_a_page_past_the_end_is_empty_and_still_reports_the_true_total(db_path):
    page = browse_records(ACME, page=1000, page_size=2, db_path=db_path)
    assert page.rows == []
    assert page.total == _ACME_ROWS


def test_the_page_size_is_held_between_one_row_and_the_executors_cap(db_path):
    """ADR 0007: a page larger than the row cap could not be served whole, so it is clamped."""
    cap = runtime().db.max_result_rows
    assert browse_records(ACME, page_size=10**9, db_path=db_path).page_size == cap
    assert browse_records(ACME, page_size=0, db_path=db_path).page_size == 1
    assert browse_records(ACME, page=-7, page_size=-3, db_path=db_path).page == 1


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_a_hostile_name_filter_returns_the_callers_rows_or_none_at_all(db_path, hostile):
    """Bound as text: it matches literally, so it finds nothing and leaks nothing."""
    page = browse_records(ACME, filters=Filters(name=hostile), db_path=db_path)
    assert page.rows == []
    assert page.total == 0
    assert _SCOPED in page.executed_sql


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_a_hostile_department_filter_cannot_reach_another_tenant(db_path, hostile):
    page = browse_records(ACME, filters=Filters(department=hostile), db_path=db_path)
    assert page.rows == []
    assert set(_column(browse_records(ACME, db_path=db_path), "tenant_id")) == {ACME}


def test_a_hostile_filter_cannot_widen_the_page_beyond_the_tenant(db_path):
    """The classic always-true tail: still one tenant's rows, because it is a value not a clause."""
    page = browse_records(
        ACME, filters=Filters(name="' OR '1'='1", salary_min=0), db_path=db_path
    )
    assert page.total == 0


@pytest.mark.parametrize("sort", ["notes", "tenant_id", "salary; DROP TABLE employees", ""])
def test_a_sort_outside_the_allowlist_is_refused_terminally(db_path, sort):
    """A sort is a name a template will use, so it is checked as a word, never bound as a value."""
    with pytest.raises(QueryRejected) as refused:
        browse_records(ACME, sort=sort, db_path=db_path)
    assert refused.value.retryable is False
    assert "sort must be one of" in refused.value.reason


@pytest.mark.parametrize("direction", ["ASC; --", "up", ""])
def test_a_direction_outside_the_allowlist_is_refused_terminally(db_path, direction):
    with pytest.raises(QueryRejected) as refused:
        browse_records(ACME, direction=direction, db_path=db_path)
    assert refused.value.retryable is False


def test_a_refused_sort_never_reaches_the_database(db_path):
    """Nothing ran: the allowlist is checked before either query is built."""
    with pytest.raises(QueryRejected):
        browse_records(ACME, sort="notes", db_path=db_path)
    assert db.audit_entries(db_path) == []


def test_filter_text_longer_than_the_cap_is_refused(db_path):
    cap = runtime().browse.max_filter_chars
    with pytest.raises(QueryRejected) as refused:
        browse_records(ACME, filters=Filters(name="a" * (cap + 1)), db_path=db_path)
    assert refused.value.retryable is False
    assert str(cap) in refused.value.reason


@pytest.mark.parametrize("value", ["not-a-date", "2020-13-40", "2020-01-01 OR 1=1", "0"])
def test_a_hire_date_filter_that_is_not_a_date_is_refused_by_name(db_path, value):
    with pytest.raises(QueryRejected) as refused:
        browse_records(ACME, filters=Filters(hired_from=value), db_path=db_path)
    assert "ISO date" in refused.value.reason


def test_the_notes_corpus_serves_the_callers_notes_only(db_path):
    """The Notes tab reads the same rows: a note is a column of the employee it belongs to."""
    page = browse_notes(ACME, page_size=_ACME_ROWS, db_path=db_path)
    assert "notes" in page.columns
    assert page.total == _ACME_ROWS
    assert _BETA_SECRET not in " ".join(_column(page, "notes"))
    assert set(_column(page, "tenant_id")) == {ACME}


def test_the_notes_corpus_takes_the_same_filters_and_sorts(db_path):
    page = browse_notes(
        ACME, filters=Filters(department="sales"), sort="name", db_path=db_path
    )
    assert _column(page, "name") == ["Ada Byron", "Grace Hopper"]


def test_the_departments_of_one_tenant_are_its_own(db_path):
    """The filter's options come from the aggregate template the get_stats tool already uses."""
    assert departments(ACME, db_path=db_path) == [
        {"department": "Engineering", "employees": 2},
        {"department": "Finance", "employees": 1},
        {"department": "Sales", "employees": 2},
    ]
    assert departments(BETA, db_path=db_path) == [
        {"department": "Engineering", "employees": 1},
        {"department": "Finance", "employees": 1},
        {"department": "Sales", "employees": 1},
    ]


def test_the_flagged_rows_are_the_callers_own_manifest_entries(tmp_path):
    """The manifest is committed repo metadata, and is still filtered to the caller's tenant."""
    manifest = tmp_path / "poisoned_manifest.json"
    manifest.write_text(
        '{"records": ['
        '{"user_id": 6, "tenant_id": "beta", "payload_kind": "role_switch"},'
        '{"user_id": 1, "tenant_id": "acme", "payload_kind": "ignore_instructions"}]}'
    )
    flagged = flagged_user_ids(ACME, manifest_path=manifest)
    assert flagged.user_ids == [1]
    assert flagged.kinds == {"1": "ignore_instructions"}


def test_a_missing_manifest_flags_nothing(tmp_path):
    flagged = flagged_user_ids(ACME, manifest_path=tmp_path / "absent.json")
    assert flagged.user_ids == []
    assert flagged.kinds == {}


def test_the_committed_manifest_is_the_default(db_path):
    """The default path is the manifest the dataset generator commits, not an argument's."""
    assert browse.MANIFEST_PATH.exists()
    assert flagged_user_ids(ACME).user_ids


def test_the_sort_carries_the_primary_key_as_its_tie_break(db_path):
    """Offset paging over a non-unique order repeats and drops rows; the key removes that."""
    sorted_page = browse_records(ACME, sort="department", db_path=db_path)
    default_page = browse_records(ACME, db_path=db_path)
    assert sorted_page.executed_sql.endswith("ORDER BY department, user_id LIMIT 25 OFFSET 0")
    assert default_page.executed_sql.endswith("ORDER BY user_id LIMIT 25 OFFSET 0")


def test_the_default_sort_is_the_primary_key(db_path):
    """A listing has to be deterministic before it can be paged; user_id is what makes it so."""
    page = browse_records(ACME, db_path=db_path)
    assert page.sort == DEFAULT_SORT
    assert _column(page, "user_id") == [1, 2, 3, 4, 5]
