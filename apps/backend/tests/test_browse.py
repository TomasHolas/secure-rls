"""Suite for the Records and Notes browse templates (issue #88, ADR 0014 as rewritten by #117).

The fixture is a tiny inline dataset loaded through `init_db` into tmp_path, never the committed
employees.csv. It holds two tenants, and beta's rows are deliberately shaped to answer acme's
filters: its names contain the substrings the name filter searches for ("Adalovelace" answers
"ada", "Grace Beta" answers "grace"), its departments are acme's departments, and its salaries sit
far outside acme's range.

That planting used to prove the executor kept beta's rows out of an acme listing. The listings are
now the dataset's, not a tenant's (issue #117), so the same planting proves the other half: an
unfiltered page HAS to show those rows, and `tenant_id` - a filter of the same kind as
`department` - is what removes them. Every filter test below therefore states which of the two it
is about, and the tenant filter is asserted to be a bound value rather than SQL text like any
other filter's.

The hostile-input cases remain the point of the module: quotes, comment markers, a UNION, LIKE
wildcards and a template placeholder are typed into the filter boxes, and each must come back as
an ordinary empty result - no error, no leaked statement, and no escape from the tenant the reader
themselves selected. That they cannot do anything else is structural, since the values never reach
the SQL as text: they are bound, and the validator still counts them (ADR 0002 as amended).

The audit listing is the third one and is covered at the end: it reads the log every test above
writes, newest first, paged by the same rules, every tenant's entries, and it writes no audit row
of its own.

What is NOT here, on purpose: any assertion that a listing hides another tenant's rows. That
property was deliberately reversed. The properties that replaced it are asserted in
`tests/test_db.py` (the unscoped read keeps validator, authorizer, row cap, deadline and audit,
and no agent tool can reach it), in the search tests below (`annotate_note_hits` stays scoped),
and across `tests/test_agent.py` (the agent still sees one tenant).
"""

import csv

import pytest

import auth
import browse
import db
from browse import (
    DEFAULT_SORT,
    Filters,
    annotate_note_hits,
    browse_audit,
    browse_notes,
    browse_records,
    filter_options,
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
_ALL_ROWS = _ACME_ROWS + _BETA_ROWS
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


def _records(db_path, **kwargs):
    """A records page for a reader of acme - the audit identity, never what narrows the page."""
    return browse_records(reader_tenant=ACME, db_path=db_path, **kwargs)


def _notes(db_path, **kwargs):
    """The same, over the note corpus."""
    return browse_notes(reader_tenant=ACME, db_path=db_path, **kwargs)


def test_a_records_page_serves_the_whole_dataset(db_path):
    """The control group: with no tenant filter the listing is the dataset, all tenants of it.

    This is the assertion that replaced "a page serves only the caller's rows". The old one
    encoded a scoped listing, which issue #117 reversed: showing 5 of 8 with nothing saying 8
    exist is what made the number look like a bug and threw away the comparison the tabs are for.
    """
    page = _records(db_path, page_size=_ALL_ROWS)

    assert page.total == _ALL_ROWS
    assert set(_column(page, "tenant_id")) == {ACME, BETA}
    assert _BETA_SECRET not in page.executed_sql


def test_the_tenant_filter_narrows_the_listing_to_one_tenant(db_path):
    """The other half of the same behaviour: pick a tenant and the page is that tenant's."""
    acme = _records(db_path, filters=Filters(tenant_id=ACME))
    beta = _records(db_path, filters=Filters(tenant_id=BETA))

    assert (acme.total, beta.total) == (_ACME_ROWS, _BETA_ROWS)
    assert set(_column(acme, "tenant_id")) == {ACME}
    assert set(_column(beta, "tenant_id")) == {BETA}
    assert sorted(_column(acme, "name")) == sorted(_ACME_NAMES)


def test_the_tenant_filter_is_a_bound_value_and_never_statement_text(db_path):
    """A reader's tenant selection is a filter value, so it is bound exactly like `department`."""
    page = _records(db_path, filters=Filters(tenant_id=ACME, name="ada"))

    assert ACME not in page.executed_sql
    assert page.executed_sql.count("?") == 2
    assert "LOWER(tenant_id) = LOWER(?)" in page.executed_sql


def test_a_records_page_runs_no_scoping_subquery(db_path):
    """The listing is the named unscoped read (ADR 0014), and the statement it shows says so."""
    page = _records(db_path)

    assert _SCOPED not in page.executed_sql
    assert "?" not in page.executed_sql


def test_the_tenant_filter_is_case_insensitive_like_the_other_categorical_one(db_path):
    assert _records(db_path, filters=Filters(tenant_id="ACME")).total == _ACME_ROWS


def test_a_tenant_the_dataset_does_not_hold_matches_nothing(db_path):
    """An unknown value is an empty page, not an error: it is a filter value, not an allowlist."""
    page = _records(db_path, filters=Filters(tenant_id="nonexistent"))

    assert (page.rows, page.total) == ([], 0)


def test_every_browse_query_is_audited_under_the_reader_who_asked(db_path):
    """The same audit trail: a page costs two approved rows, its total and its window."""
    _records(db_path)
    entries = db.audit_entries(db_path)

    assert [entry.verdict for entry in entries] == [db.VERDICT_APPROVED] * 2
    assert {entry.tenant for entry in entries} == {ACME}


def test_the_name_filter_matches_a_substring_across_the_dataset(db_path):
    """Beta's "Adalovelace" answers the same substring, and an unfiltered listing must show it."""
    everyone = _records(db_path, filters=Filters(name="ada"))
    acme_only = _records(db_path, filters=Filters(tenant_id=ACME, name="ada"))

    assert sorted(_column(everyone, "name")) == ["Ada Byron", "Ada Lovelace", "Adalovelace Beta"]
    assert sorted(_column(acme_only, "name")) == ["Ada Byron", "Ada Lovelace"]
    assert acme_only.total == 2


def test_the_name_filter_ignores_case(db_path):
    assert _records(db_path, filters=Filters(name="ADA LOVE")).total == 1


def test_a_blank_filter_box_filters_nothing(db_path):
    """An empty or whitespace box is absent, not a match on the empty string."""
    page = _records(db_path, filters=Filters(tenant_id="  ", name="   ", department=""))

    assert page.total == _ALL_ROWS


def test_the_department_filter_is_an_exact_case_insensitive_match(db_path):
    page = _records(db_path, filters=Filters(tenant_id=ACME, department="engineering"))

    assert sorted(_column(page, "name")) == ["Ada Lovelace", "Alan Turing"]


def test_the_range_filters_bound_both_ends_inclusively(db_path):
    page = _records(db_path, filters=Filters(salary_min=200, salary_max=400))

    assert sorted(_column(page, "salary")) == [200, 300, 400]


def test_the_score_and_hire_date_filters_narrow_the_same_way(db_path):
    scored = _records(db_path, filters=Filters(tenant_id=ACME, score_min=3.5))
    hired = _records(
        db_path, filters=Filters(tenant_id=ACME, hired_from="2021-01-01", hired_to="2022-12-31")
    )

    assert sorted(_column(scored, "performance_score")) == [3.5, 4.5, 5.0]
    assert sorted(_column(hired, "hire_date")) == ["2021-03-03", "2022-04-04"]


def test_filters_combine_as_a_conjunction(db_path):
    page = _records(db_path, filters=Filters(tenant_id=ACME, name="ada", department="sales"))

    assert _column(page, "name") == ["Ada Byron"]


@pytest.mark.parametrize("direction,expected", [("asc", [100, 200]), ("desc", [500, 400])])
def test_an_allowlisted_sort_orders_the_page(db_path, direction, expected):
    page = _records(
        db_path,
        filters=Filters(tenant_id=ACME),
        sort="salary",
        direction=direction,
        page_size=2,
    )

    assert _column(page, "salary") == expected


def test_the_listing_can_be_sorted_by_tenant_so_a_mixed_page_reads_grouped(db_path):
    """`tenant_id` is in the sort allowlist for the same reason it is a filter: it is a column."""
    page = _records(db_path, sort="tenant_id", direction="desc", page_size=_ALL_ROWS)

    assert _column(page, "tenant_id") == [BETA] * _BETA_ROWS + [ACME] * _ACME_ROWS


def test_paging_covers_every_row_of_the_dataset_exactly_once(db_path):
    """The primary key breaks every tie, so no page boundary can drop or repeat a row."""
    seen = []
    for number in (1, 2, 3, 4):
        page = _records(db_path, sort="department", page=number, page_size=2)
        assert page.total == _ALL_ROWS
        assert page.page == number
        seen.extend(_column(page, "user_id"))

    assert sorted(seen) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_a_page_past_the_end_is_empty_and_still_reports_the_true_total(db_path):
    page = _records(db_path, page=1000, page_size=2)

    assert page.rows == []
    assert page.total == _ALL_ROWS


def test_the_page_size_is_held_between_one_row_and_the_executors_cap(db_path):
    """ADR 0007: a page larger than the row cap could not be served whole, so it is clamped."""
    cap = runtime().db.max_result_rows

    assert _records(db_path, page_size=10**9).page_size == cap
    assert _records(db_path, page_size=0).page_size == 1
    assert _records(db_path, page=-7, page_size=-3).page == 1


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_a_hostile_name_filter_is_compared_as_text_and_matches_nothing(db_path, hostile):
    """Bound as a value: the statement does not depend on it at all, so it composes nothing."""
    page = _records(db_path, filters=Filters(name=hostile))

    assert page.rows == []
    assert page.total == 0
    assert page.executed_sql == _records(db_path, filters=Filters(name="benign")).executed_sql


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_a_hostile_department_filter_leaves_every_other_page_untouched(db_path, hostile):
    """It matches nothing and changes nothing: the next listing is the same dataset it was."""
    page = _records(db_path, filters=Filters(department=hostile))

    assert page.rows == []
    assert _records(db_path).total == _ALL_ROWS


def test_a_hostile_filter_cannot_escape_the_tenant_the_reader_selected(db_path):
    """The classic always-true tail is still a value, so it cannot drop the tenant predicate.

    This is what remains of "cannot widen the page beyond the tenant" and it is the sharper
    property: with a tenant filter applied, no typed text can turn into a clause that removes it.
    """
    page = _records(
        db_path, filters=Filters(tenant_id=ACME, name="' OR '1'='1", salary_min=0)
    )
    widened = _records(db_path, filters=Filters(tenant_id="acme' OR '1'='1"))

    assert page.total == 0
    assert widened.total == 0


@pytest.mark.parametrize("sort", ["notes", "rowid", "salary; DROP TABLE employees", ""])
def test_a_sort_outside_the_allowlist_is_refused_terminally(db_path, sort):
    """A sort is a name a template will use, so it is checked as a word, never bound as a value."""
    with pytest.raises(QueryRejected) as refused:
        _records(db_path, sort=sort)

    assert refused.value.retryable is False
    assert "sort must be one of" in refused.value.reason


@pytest.mark.parametrize("direction", ["ASC; --", "up", ""])
def test_a_direction_outside_the_allowlist_is_refused_terminally(db_path, direction):
    with pytest.raises(QueryRejected) as refused:
        _records(db_path, direction=direction)

    assert refused.value.retryable is False


def test_a_refused_sort_never_reaches_the_database(db_path):
    """Nothing ran: the allowlist is checked before either query is built."""
    with pytest.raises(QueryRejected):
        _records(db_path, sort="notes")

    assert db.audit_entries(db_path) == []


def test_filter_text_longer_than_the_cap_is_refused(db_path):
    cap = runtime().browse.max_filter_chars

    with pytest.raises(QueryRejected) as refused:
        _records(db_path, filters=Filters(name="a" * (cap + 1)))

    assert refused.value.retryable is False
    assert str(cap) in refused.value.reason


def test_an_over_long_tenant_filter_is_refused_like_any_other_text_filter(db_path):
    """The tenant filter is text, so it is length-bounded and trimmed exactly like the rest."""
    cap = runtime().browse.max_filter_chars

    with pytest.raises(QueryRejected) as refused:
        _records(db_path, filters=Filters(tenant_id="a" * (cap + 1)))

    assert "tenant_id" in refused.value.reason


@pytest.mark.parametrize("value", ["not-a-date", "2020-13-40", "2020-01-01 OR 1=1", "0"])
def test_a_hire_date_filter_that_is_not_a_date_is_refused_by_name(db_path, value):
    with pytest.raises(QueryRejected) as refused:
        _records(db_path, filters=Filters(hired_from=value))

    assert "ISO date" in refused.value.reason


def test_the_notes_corpus_serves_every_tenants_notes(db_path):
    """The corpus listing is the dataset's too, which is what makes the search asymmetry visible.

    Replaces "the corpus serves the caller's notes only". A reader has to be able to READ beta's
    planted payload here; that their own search cannot RETRIEVE it is asserted where the search
    lives (`annotate_note_hits` below, and `tests/test_app.py`'s search tests).
    """
    page = _notes(db_path, page_size=_ALL_ROWS)

    assert "notes" in page.columns
    assert page.total == _ALL_ROWS
    assert _BETA_SECRET in " ".join(_column(page, "notes"))
    assert set(_column(page, "tenant_id")) == {ACME, BETA}


def test_the_notes_corpus_takes_the_tenant_filter_too(db_path):
    """Which is how a reader finds another tenant's planted note without paging the whole corpus."""
    page = _notes(db_path, filters=Filters(tenant_id=BETA), page_size=_ALL_ROWS)

    assert page.total == _BETA_ROWS
    assert set(_column(page, "notes")) == {_BETA_SECRET}


def test_the_notes_corpus_carries_what_a_note_is_verified_against(db_path):
    """A card the reader can check: whose row, which department, which score, and the text."""
    page = _notes(db_path, filters=Filters(tenant_id=ACME), sort="name", page_size=_ACME_ROWS)

    assert page.columns == (
        "user_id",
        "tenant_id",
        "name",
        "department",
        "performance_score",
        "notes",
    )
    row = page.rows[_column(page, "name").index("Ada Lovelace")]
    assert row == (1, ACME, "Ada Lovelace", "Engineering", 4.5, "shipped the compiler")


def test_a_note_hit_is_annotated_with_its_own_rows_department_and_score(db_path):
    """The retrieval is untouched; the fields a reader checks it against come off the row."""
    hits = [{"user_id": 1, "name": "Ada Lovelace", "note": "shipped the compiler", "distance": 0.2}]

    (annotated,) = annotate_note_hits(ACME, hits, db_path=db_path)

    assert annotated["tenant_id"] == ACME
    assert annotated["department"] == "Engineering"
    assert annotated["performance_score"] == 4.5
    assert annotated["distance"] == 0.2
    assert annotated["note"] == "shipped the compiler"


def test_annotating_a_hit_cannot_describe_another_tenants_row(db_path):
    """The search path stays SCOPED while the listing beside it does not: a foreign id matches
    nothing at all. This assertion is untouched by issue #117 and is the one that must not move."""
    hits = [{"user_id": 6, "name": "Adalovelace Beta", "note": _BETA_SECRET, "distance": 0.1}]

    (annotated,) = annotate_note_hits(ACME, hits, db_path=db_path)

    assert "tenant_id" not in annotated
    assert "department" not in annotated
    assert "performance_score" not in annotated


def test_the_hit_annotation_runs_the_scoped_executor_not_the_browse_read(db_path):
    """Stated as a test because the two paths differ: a TENANT identity's annotation is scoped, the
    listing is not, and a future edit that reached for the listing's read here would be a real
    leak. An all-tenant identity is the one case that may take that read, and it has to ask for it
    with the scope its token carries - which the two tests below cover, in both directions."""
    hits = [{"user_id": 1, "name": "Ada Lovelace", "note": "shipped the compiler", "distance": 0.2}]

    annotate_note_hits(ACME, hits, db_path=db_path)

    (entry,) = db.audit_entries(db_path)
    assert _SCOPED in (entry.executed_sql or "")


def test_an_all_scope_annotation_describes_the_foreign_row_a_hit_names(db_path):
    """The reason this exists: an all-scope retrieval returns foreign hits, and a lookup bound to
    one tenant would describe none of them. The Notes tab would then show a hit with no department,
    no score and no tenant - a blank card, which reads as a bug rather than as a scope."""
    hits = [{"user_id": 6, "name": "Adalovelace Beta", "note": _BETA_SECRET, "distance": 0.1}]

    (annotated,) = annotate_note_hits(
        auth.ALL_TENANTS, hits, all_tenants=True, db_path=db_path
    )

    assert annotated["tenant_id"] == BETA
    assert annotated["department"] == "Engineering"
    assert annotated["performance_score"] == 5.0
    assert annotated["distance"] == 0.1


def test_the_all_scope_annotation_is_the_listings_read_and_the_audit_shows_it(db_path):
    """It takes the one unscoped read, under the all-scope identity, and the trail says so: the
    executed statement carries no scoping subquery, exactly as a listing's does not."""
    hits = [{"user_id": 6, "name": "Adalovelace Beta", "note": _BETA_SECRET, "distance": 0.1}]

    annotate_note_hits(auth.ALL_TENANTS, hits, all_tenants=True, db_path=db_path)

    (entry,) = db.audit_entries(db_path)
    assert _SCOPED not in (entry.executed_sql or "")
    assert entry.tenant == auth.ALL_TENANTS
    assert entry.verdict == db.VERDICT_APPROVED


def test_annotating_no_hits_asks_the_database_nothing(db_path):
    assert annotate_note_hits(ACME, [], db_path=db_path) == []


def test_annotating_repeated_ids_binds_each_one_once(db_path):
    """One placeholder per distinct id, so the declared parameter count always matches."""
    hit = {"user_id": 1, "name": "Ada Lovelace", "note": "shipped the compiler", "distance": 0.2}

    annotated = annotate_note_hits(ACME, [dict(hit), dict(hit)], db_path=db_path)

    assert [entry["department"] for entry in annotated] == ["Engineering", "Engineering"]


def test_the_notes_corpus_takes_the_same_filters_and_sorts(db_path):
    page = _notes(db_path, filters=Filters(tenant_id=ACME, department="sales"), sort="name")

    assert _column(page, "name") == ["Ada Byron", "Grace Hopper"]


def test_the_tenant_options_are_the_datasets_tenants_and_their_row_counts(db_path):
    """The picker states the control group in one line: this is what the dataset holds."""
    assert filter_options("tenant_id", reader_tenant=ACME, db_path=db_path) == [
        browse.OptionCount(value=ACME, employees=_ACME_ROWS),
        browse.OptionCount(value=BETA, employees=_BETA_ROWS),
    ]


def test_the_department_options_count_the_dataset_and_narrow_with_the_tenant(db_path):
    """A count beside an option counts the rows the reader is looking at, not another set."""
    everyone = filter_options("department", reader_tenant=ACME, db_path=db_path)
    acme_only = filter_options(
        "department", reader_tenant=ACME, tenant_id=ACME, db_path=db_path
    )

    assert everyone == [
        browse.OptionCount(value="Engineering", employees=3),
        browse.OptionCount(value="Finance", employees=2),
        browse.OptionCount(value="Sales", employees=3),
    ]
    assert acme_only == [
        browse.OptionCount(value="Engineering", employees=2),
        browse.OptionCount(value="Finance", employees=1),
        browse.OptionCount(value="Sales", employees=2),
    ]


@pytest.mark.parametrize("column", ["notes", "salary", "name; --", ""])
def test_an_option_column_outside_the_allowlist_is_refused(db_path, column):
    """The column is a name a template will use, so it is allowlisted rather than bound."""
    with pytest.raises(QueryRejected) as refused:
        filter_options(column, reader_tenant=ACME, db_path=db_path)

    assert refused.value.retryable is False


def test_the_flagged_rows_are_every_tenants_manifest_entries(tmp_path):
    """The manifest is committed repo metadata, and now covers every tenant the listing shows.

    Replaces "the flagged rows are the caller's own". Filtering it to the caller made sense while
    the corpus was the caller's; with the corpus showing every tenant, filtering the badges would
    hide exactly the payload the demo points at (issue #117).
    """
    manifest = tmp_path / "poisoned_manifest.json"
    manifest.write_text(
        '{"records": ['
        '{"user_id": 6, "tenant_id": "beta", "payload_kind": "role_switch"},'
        '{"user_id": 1, "tenant_id": "acme", "payload_kind": "ignore_instructions"}]}'
    )

    flagged = flagged_user_ids(manifest_path=manifest)

    assert sorted(flagged.user_ids) == [1, 6]
    assert flagged.kinds == {"1": "ignore_instructions", "6": "role_switch"}


def test_a_missing_manifest_flags_nothing(tmp_path):
    flagged = flagged_user_ids(manifest_path=tmp_path / "absent.json")

    assert flagged.user_ids == []
    assert flagged.kinds == {}


def test_the_committed_manifest_is_the_default(db_path):
    """The default path is the manifest the dataset generator commits, not an argument's."""
    assert browse.MANIFEST_PATH.exists()
    assert flagged_user_ids().user_ids


def test_the_sort_carries_the_primary_key_as_its_tie_break(db_path):
    """Offset paging over a non-unique order repeats and drops rows; the key removes that."""
    sorted_page = _records(db_path, sort="department")
    default_page = _records(db_path)

    assert sorted_page.executed_sql.endswith("ORDER BY department, user_id LIMIT 25 OFFSET 0")
    assert default_page.executed_sql.endswith("ORDER BY user_id LIMIT 25 OFFSET 0")


def test_the_default_sort_is_the_primary_key(db_path):
    """A listing has to be deterministic before it can be paged; user_id is what makes it so."""
    page = _records(db_path)

    assert page.sort == DEFAULT_SORT
    assert _column(page, "user_id") == [1, 2, 3, 4, 5, 6, 7, 8]


def test_a_parameter_the_listing_does_not_read_is_reported_with_the_page(db_path):
    """The rows are unchanged and the discarded name is named: 200 plus a report (issue #107)."""
    silent = _records(db_path)
    probed = _records(db_path, requested=["role", "name", "page"])

    assert probed.rows == silent.rows
    assert probed.total == silent.total
    assert [param.name for param in probed.ignored] == ["role"]


def test_the_tenant_parameter_is_read_as_a_filter_and_never_reported_as_ignored(db_path):
    """The reversal, at the report: `tenant_id` is a parameter this listing reads.

    This replaces the two tests that asserted the opposite - that `tenant_id` was refused with a
    sentence about no request being able to name a tenant. That sentence was true of the scoped
    listing and is false of this one. It remains true of the chat path, where the tenant reaches
    the tools by closure and no tool argument can name one, and that is asserted in
    `tests/test_agent.py` and `tests/test_app.py`.
    """
    page = _records(db_path, filters=Filters(tenant_id=BETA), requested=["tenant_id"])

    assert page.ignored == ()
    assert page.total == _BETA_ROWS
    assert "tenant_id" in browse.LISTING_PARAMS


def test_a_misspelled_tenant_parameter_is_reported_like_any_other_unread_name(db_path):
    """`tenant` and `Tenant_ID` are not what the listing reads, and the report names what is."""
    page = _records(db_path, requested=["tenant", "Tenant_ID"])

    assert [param.name for param in page.ignored] == ["tenant", "Tenant_ID"]
    for param in page.ignored:
        assert "tenant_id" in param.reason


def test_an_unknown_parameter_is_reported_with_the_ones_the_listing_does_read(db_path):
    """The generic case answers the reader's next question: then what may I send?"""
    (param,) = _records(db_path, requested=["db_path"]).ignored

    assert param.name == "db_path"
    assert "not a parameter this listing reads" in param.reason
    assert "salary_min" in param.reason
    assert "sort" in param.reason


def test_a_report_names_parameters_and_never_their_values(db_path):
    """The names came from the request; echoing a value would print text we never accepted."""
    report = browse.ignored_params(["tenant", "department", "nonsense"])

    assert [param.name for param in report] == ["tenant", "nonsense"]
    assert not any(BETA in param.reason for param in report)


def test_the_notes_listing_reports_what_it_ignored_the_same_way(db_path):
    """The corpus takes the same filters, so it owes the same honesty about a stray one."""
    page = _notes(db_path, requested=["role"])

    assert page.total == _ALL_ROWS
    assert [param.name for param in page.ignored] == ["role"]


def test_a_known_filter_with_a_bad_value_still_refuses_terminally(db_path):
    """Nothing here softens the allowlist: a bad sort raises before a page is built at all."""
    with pytest.raises(QueryRejected):
        _records(db_path, sort="notes", requested=["role", "sort"])


def test_the_audit_listing_serves_the_log_newest_first(db_path):
    """The Audit tab's page: the head of the log, which is where a log is read from."""
    _records(db_path, filters=Filters(tenant_id=ACME))
    browse_notes(reader_tenant=BETA, db_path=db_path)

    listing = browse_audit(page_size=2, db_path=db_path)
    whole = db.audit_entries(db_path)

    assert listing.total == len(whole)
    assert [entry.id for entry in listing.entries] == [whole[-1].id, whole[-2].id]


def test_the_audit_listing_shows_every_tenants_entries(db_path):
    """No reader tenant narrows it: a trail filtered to the caller shows no comparison at all."""
    _records(db_path)
    browse_notes(reader_tenant=BETA, db_path=db_path)

    listing = browse_audit(page_size=runtime().db.max_result_rows, db_path=db_path)

    assert {entry.tenant for entry in listing.entries} == {ACME, BETA}


def test_the_audit_listing_pages_the_same_way_the_row_listings_do(db_path):
    """One default page, one ceiling - the executor's row cap (ADR 0007) - and no page zero."""
    _records(db_path)

    assert browse_audit(db_path=db_path).page_size == runtime().browse.page_size
    assert browse_audit(page_size=10**9, db_path=db_path).page_size == runtime().db.max_result_rows
    assert browse_audit(page=-7, page_size=-3, db_path=db_path).page == 1


def test_reading_the_audit_log_writes_no_audit_row_of_its_own(db_path):
    """A trail that recorded every look at itself would bury the rows it exists to show."""
    _records(db_path)
    before = len(db.audit_entries(db_path))

    browse_audit(db_path=db_path)

    assert len(db.audit_entries(db_path)) == before
