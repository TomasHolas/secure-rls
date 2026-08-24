"""Browsing the whole dataset: the auditor surface's listings (ADR 0014 as rewritten).

The tabs are the control group for the isolation claim, not a tenant view. They list every row
of the dataset - all three tenants - so a reader can see exactly what exists, and then watch the
agent in the same app reach only the tenant its token names. Showing 450 with nothing saying
that 1000 exist throws that comparison away and makes the number look like a bug. So the
listings are deliberately unscoped, and `tenant_id` is a filter of the same kind as
`department`: pick one and the page is that tenant's, pick nothing and it is the dataset's.

That is the ONE unscoped read in the repo, and it is named as one: the listings run through
`db.execute_unscoped_browse`, which keeps layer 2's validator, layer 2.5's read-only connection,
authorizer, limit caps and deadline, the row cap and the audit row, and drops only what showing
every tenant makes meaningless (the scoping rewrite, the proof of it, and the tenant egress
comparison). The scoped executor is still what everything else here uses - the note-hit
annotation below, every agent tool, every eval. Nothing in this module concatenates SQL a reader
typed, interpolates a column name, or opens a connection.

A reader's filter values travel as bound parameters, declared to the executor (ADR 0002 as
amended), so a quote, a comment marker or a UNION typed into the name box is compared as text
and never parsed as SQL - and the tenant filter is bound exactly like the rest, because it is a
reader's UI control on an auditor surface and not a tenant the request gets to assert. The name
filter is `INSTR(LOWER(name), LOWER(?)) > 0` rather than a LIKE on purpose: a substring search is
what the box promises, and LIKE would silently give a typed `%` or `_` a meaning the reader did
not ask for. Sort column and direction are never values - they are words checked against an
allowlist by `security.require_allowed` before they become AST nodes, terminally, because no
model is there to correct a query string.

Both templates select `tenant_id` itself, and it now earns its place twice: a mixed listing has
to say which tenant each row belongs to, and the login-switch comparison is visible in the data
rather than only in the header badge.

Paging. The page ceiling IS the executor's row cap (`db.max_result_rows`, ADR 0007), because a
larger page could not be served whole; a request beyond it is clamped and the response states
the page size it actually used, so a clamp is reported rather than silent. The true total is a
COUNT over the same filters through the same read, which is what makes "1000 rows" a fact about
the dataset - and "450 rows, tenant acme" a fact about the filter - rather than about the page
in hand.

What a listing does not read, it says so about. `Filters` is the query-parameter allowlist, so a
name that is not one of its fields was never read - but silence about it is indistinguishable
from having honored it, which is the ambiguity this surface exists to remove (issue #107).
`ignored_params` reports every such name back beside the page it did not change. `tenant_id` is
no longer one of them: it is a filter here now, and the sentence about no request being able to
name a tenant was retired with the design it described - it remains true of the chat path, where
the tenant reaches the tools by closure and no argument can name one, and that is where it is
said (ADR 0002, layer 1).

Notes. The corpus listing is the second template over the same table, since a note is a column
of its employee's row, and it spans tenants for the same reason Records does. The search does
NOT: it delegates to `rag.search_notes_scoped`, the agent's own retrieval path (ADR 0010), so
what the reader sees - the same hits, in the same order, with the same distances - is literally
what the `search_notes` tool would have returned for that query, for their tenant alone. Seeing
another tenant's planted payload in the list and then searching for it and getting nothing back
IS the demonstration, so the list is unscoped and the search is not. `annotate_note_hits` reads
each hit's own row through the SCOPED executor for the fields a claim is checked against,
because a name and a paragraph cannot settle whether a retrieval was right: the note's tone is
composed coherent with the score (ADR 0008), so seeing both at once is what makes a hit
verifiable. `flagged_user_ids` reads the committed poison manifest so the tab can mark the
planted injection payloads, every tenant's, because the listing shows every tenant's: it is repo
metadata the README already points at rather than tenant data.

Audit. `browse_audit` is the third listing and the same shape: one newest-first page of the audit
log every read above already writes, read through `db.audit_window` so the store keeps its single
reader module, paged by the same default and the same row-cap ceiling. It carries no filters -
a log is read from its head, not queried - and no `reader_tenant`, because reading the log is not
a read of the dataset and a trail that recorded every look at itself would bury what it is for.
The rows it serves are statements and metadata: no result row is in that store, so it exposes
nothing the Records listing does not already show outright.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlglot import exp

from db import AuditEntry, audit_window, execute_scoped, execute_unscoped_browse
from paths import DB_PATH
from runtime import runtime
from security import ALLOWED_TABLE, QueryRejected, require_allowed

MANIFEST_PATH = Path(__file__).resolve().parent / "poisoned_manifest.json"

RECORD_COLUMNS = (
    "user_id",
    "tenant_id",
    "name",
    "department",
    "salary",
    "performance_score",
    "hire_date",
)
# What a note is checked against: its row, department and score (ADR 0008) - salary and date, no.
NOTE_COLUMNS = ("user_id", "tenant_id", "name", "department", "performance_score", "notes")
# The same fields, read for the rows a retrieval named rather than for a page of the corpus.
HIT_CONTEXT_COLUMNS = ("user_id", "tenant_id", "department", "performance_score")
SORT_COLUMNS = frozenset(
    {"user_id", "tenant_id", "name", "department", "salary", "performance_score", "hire_date"}
)
# The categorical filters whose options are read off the data rather than typed by the reader.
FILTER_OPTION_COLUMNS = frozenset({"tenant_id", "department"})
DIRECTIONS = frozenset({"asc", "desc"})
DEFAULT_SORT = "user_id"
DEFAULT_DIRECTION = "asc"

_DIALECT = "sqlite"
# The primary key breaks every tie, so a page boundary cannot drop or repeat a row.
_TIE_BREAK = "user_id"
_DESCENDING = "desc"
_FIRST_PAGE = 1
_TEXT_FILTERS = frozenset({"tenant_id", "name", "department"})
_DATE_FILTERS = frozenset({"hired_from", "hired_to"})
_MANIFEST_USER = "user_id"
_MANIFEST_KIND = "payload_kind"
_HIT_KEY = "user_id"


@dataclass(frozen=True)
class Filters:
    """What a reader may narrow a listing by: this allowlist of comparisons and nothing else.

    `tenant_id` is one of them, of the same kind as `department`, because these listings are the
    dataset's and not one tenant's (ADR 0014 as rewritten). It narrows what a READER is shown; it
    can never widen what the agent is shown, which is bound from the verified token by closure
    and takes no argument at all.
    """

    tenant_id: str | None = None
    name: str | None = None
    department: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    score_min: float | None = None
    score_max: float | None = None
    hired_from: str | None = None
    hired_to: str | None = None


LISTING_PARAMS = frozenset(Filters.__dataclass_fields__) | {
    "sort",
    "direction",
    "page",
    "page_size",
}
_UNKNOWN_REASON = "not a parameter this listing reads; it reads {accepted}"


@dataclass(frozen=True)
class Ignored:
    """One query parameter a listing did not read, and the reason it did not."""

    name: str
    reason: str


@dataclass(frozen=True)
class OptionCount:
    """One value a categorical filter can be set to, and how many rows the listing holds for it."""

    value: str
    employees: int


@dataclass(frozen=True)
class BrowsePage:
    """One page of the dataset's rows, with the true total the same read counted."""

    columns: tuple[str, ...]
    rows: list[tuple[object, ...]]
    total: int
    page: int
    page_size: int
    sort: str
    direction: str
    executed_sql: str
    ignored: tuple[Ignored, ...] = ()


@dataclass(frozen=True)
class AuditListing:
    """One newest-first page of the audit log, with how many rows the log holds in all."""

    entries: list[AuditEntry]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class Flagged:
    """The dataset's rows the committed poison manifest plants an injection payload in."""

    user_ids: list[int]
    kinds: dict[str, str]


def browse_records(
    *,
    reader_tenant: str,
    filters: Filters | None = None,
    sort: str = DEFAULT_SORT,
    direction: str = DEFAULT_DIRECTION,
    page: int = _FIRST_PAGE,
    page_size: int | None = None,
    requested: Iterable[str] = (),
    db_path: Path = DB_PATH,
) -> BrowsePage:
    """One sorted, filtered page of the dataset's employee rows, plus how many match in all.

    Every argument is keyword-only, and `reader_tenant` says what it is for: the audit identity of
    whoever browsed. It does not narrow the page - `filters.tenant_id` is what does that, and
    leaving it unset is what shows all three tenants (ADR 0014 as rewritten).
    """
    return _page(
        RECORD_COLUMNS,
        reader_tenant,
        filters,
        sort,
        direction,
        page,
        page_size,
        requested,
        db_path,
    )


def browse_notes(
    *,
    reader_tenant: str,
    filters: Filters | None = None,
    sort: str = DEFAULT_SORT,
    direction: str = DEFAULT_DIRECTION,
    page: int = _FIRST_PAGE,
    page_size: int | None = None,
    requested: Iterable[str] = (),
    db_path: Path = DB_PATH,
) -> BrowsePage:
    """The same page over the note corpus: a note is a column of the employee row that owns it."""
    return _page(
        NOTE_COLUMNS,
        reader_tenant,
        filters,
        sort,
        direction,
        page,
        page_size,
        requested,
        db_path,
    )


def browse_audit(
    *,
    page: int = _FIRST_PAGE,
    page_size: int | None = None,
    db_path: Path = DB_PATH,
) -> AuditListing:
    """One newest-first page of the audit log: the third listing of the auditor surface.

    The log is the server's own record of every statement the data path ran - the generated SQL,
    the verdict a layer returned, the statement that actually executed, the row count and the
    error kind (ADR 0002). It is served newest first because a log is read from its head, and
    paged by the same rules the row listings use: the same default page and the same ceiling, the
    executor's row cap (ADR 0007).

    It has no filters, deliberately: this is a log, not a workbench. A tenant chip row would be
    the same control the row listings carry and can be added when a reader asks for it.

    There is no `reader_tenant` here because there is no data read to attribute: `db.audit_window`
    reads the audit store, not the dataset, so this listing writes no audit row of its own - a
    log that recorded every look at itself would bury the rows it exists to show.
    """
    size = _page_size(page_size)
    number = max(page, _FIRST_PAGE)
    window = audit_window(limit=size, offset=(number - 1) * size, db_path=db_path)
    return AuditListing(
        entries=window.entries, total=window.total, page=number, page_size=size
    )


def ignored_params(
    names: Iterable[str], accepted: frozenset[str] = LISTING_PARAMS
) -> tuple[Ignored, ...]:
    """The parameters a request carried that this listing does not read, each with its reason.

    A stray query parameter must not break a page, so it is ignored - but ignoring it in silence
    leaves a reader unable to tell a refusal from a coincidence (issue #107). So every name a
    request carried that the listing does not read is reported back beside the page it did not
    change, the way a known filter with a bad value is already refused by name.

    Only names are reported, never the values they carried: a response that echoed a value would
    put text the server never accepted into the server's own output.

    The accepted set is matched exactly, as the web framework matches a parameter name, so a
    differently cased `Name` counts as unread because it *is* unread - and `tenant`, now that
    `tenant_id` is a real filter, is reported generically with the accepted names listed, which
    answers the reader's actual next question.
    """
    unknown = _UNKNOWN_REASON.format(accepted=", ".join(sorted(accepted)))
    return tuple(
        Ignored(name=name, reason=unknown)
        for name in dict.fromkeys(names)
        if name not in accepted
    )


def filter_options(
    column: str,
    *,
    reader_tenant: str,
    tenant_id: str | None = None,
    db_path: Path = DB_PATH,
) -> list[OptionCount]:
    """The values one categorical filter can take over the listing, each with its own row count.

    A picker that offered a value the data does not hold, or a count of something other than what
    the listing shows, would be a number attached to nothing. So the options come from the same
    dataset and the same unscoped read the listing does, narrowed by the same bound tenant filter:
    with no tenant picked the department counts sum to the whole dataset, with `acme` picked they
    are acme's. `column` is a name a template will use, so it is allowlisted rather than bound.
    """
    require_allowed(column, FILTER_OPTION_COLUMNS, "column", retryable=False)
    predicates, values = _bind(Filters(tenant_id=tenant_id))
    result = execute_unscoped_browse(
        _counts_sql(column, predicates), reader_tenant, params=values, db_path=db_path
    )
    return [OptionCount(value=str(value), employees=int(count)) for value, count in result.rows]


def flagged_user_ids(*, manifest_path: Path = MANIFEST_PATH) -> Flagged:
    """Every planted-injection row of the manifest, all tenants (repo metadata, not tenant data).

    The manifest is generated with the dataset, committed, and pointed at by the README, so naming
    the poisoned rows tells a reader nothing the repo does not already say out loud - and it lets
    the Notes tab point at a payload before the agent reads it and refuses. It covers every tenant
    because the corpus listing does: a reader seeing another tenant's planted payload and then
    finding their own search cannot retrieve it is the demonstration. A missing manifest flags
    nothing.
    """
    if not manifest_path.exists():
        return Flagged(user_ids=[], kinds={})
    records = json.loads(manifest_path.read_text()).get("records", [])
    return Flagged(
        user_ids=[int(row[_MANIFEST_USER]) for row in records],
        kinds={str(row[_MANIFEST_USER]): str(row.get(_MANIFEST_KIND, "")) for row in records},
    )


def annotate_note_hits(
    tenant_id: str,
    hits: list[dict[str, object]],
    *,
    db_path: Path = DB_PATH,
) -> list[dict[str, object]]:
    """The retrieval's own hits, each carrying the department and score of the row it came from.

    The vector store holds what was embedded plus the identity of its row (ADR 0010), so the
    fields a reader checks a hit against are read from the employees row itself - one fixed
    template, the hit ids bound, through `db.execute_scoped`: this is the search path, not the
    listing, so it keeps every layer including the tenant scoping and the egress check. The
    listings are the unscoped exception; nothing here is. `tenant_id` travels with the fields for
    the same reason the listing templates select it: a hit carries the tenant it came from, on the
    surface built to show that. The retrieval is untouched: the hits, their order and their
    distances stay exactly what the `search_notes` tool returned. A hit whose row this tenant
    cannot see gains nothing, which is the scoping doing its job rather than an error.
    """
    wanted = tuple(dict.fromkeys(int(hit[_HIT_KEY]) for hit in hits))
    if not wanted:
        return hits
    result = execute_scoped(_context_sql(len(wanted)), tenant_id, params=wanted, db_path=db_path)
    context = {
        int(user_id): {"tenant_id": tenant, "department": department, "performance_score": score}
        for user_id, tenant, department, score in result.rows
    }
    return [{**hit, **context.get(int(hit[_HIT_KEY]), {})} for hit in hits]


def _page(
    columns: tuple[str, ...],
    reader_tenant: str,
    filters: Filters | None,
    sort: str,
    direction: str,
    page: int,
    page_size: int | None,
    requested: Iterable[str],
    db_path: Path,
) -> BrowsePage:
    """Run the two reads one listing needs: the page itself, and how many rows match in all."""
    require_allowed(sort, SORT_COLUMNS, "sort", retryable=False)
    require_allowed(direction, DIRECTIONS, "direction", retryable=False)
    predicates, values = _bind(filters or Filters())
    size = _page_size(page_size)
    number = max(page, _FIRST_PAGE)
    total = _total(predicates, values, reader_tenant, db_path)
    result = execute_unscoped_browse(
        _page_sql(columns, predicates, sort, direction, size, (number - 1) * size),
        reader_tenant,
        params=values,
        db_path=db_path,
    )
    return BrowsePage(
        columns=result.columns,
        rows=result.rows,
        total=total,
        page=number,
        page_size=size,
        sort=sort,
        direction=direction,
        executed_sql=result.executed_sql,
        ignored=ignored_params(requested),
    )


def _total(
    predicates: list[exp.Expression],
    values: tuple[object, ...],
    reader_tenant: str,
    db_path: Path,
) -> int:
    """How many rows match the filters at all, page or no page (ADR 0007)."""
    result = execute_unscoped_browse(
        _count_sql(predicates), reader_tenant, params=values, db_path=db_path
    )
    ((total,),) = result.rows
    return int(total)


def _page_sql(
    columns: tuple[str, ...],
    predicates: list[exp.Expression],
    sort: str,
    direction: str,
    limit: int,
    offset: int,
) -> str:
    """The page template: allowlisted columns, bound filters, an allowlisted sort, one window."""
    order = exp.column(sort)
    ordered = order.desc() if direction == _DESCENDING else order.asc()
    # The primary key breaks a tie unless it IS the sort, where a second copy says nothing.
    tie_break = () if sort == _TIE_BREAK else (exp.column(_TIE_BREAK),)
    return (
        _select(exp.select(*(exp.column(name) for name in columns)), predicates)
        .order_by(ordered, *tie_break)
        .limit(limit)
        .offset(offset)
        .sql(dialect=_DIALECT)
    )


def _context_sql(count: int) -> str:
    """The lookup template: the verification columns of the rows a retrieval named, ids bound."""
    named = exp.In(
        this=exp.column(_HIT_KEY), expressions=[exp.Placeholder() for _ in range(count)]
    )
    columns = exp.select(*(exp.column(name) for name in HIT_CONTEXT_COLUMNS))
    return _select(columns, [named]).sql(dialect=_DIALECT)


def _count_sql(predicates: list[exp.Expression]) -> str:
    """The total template: the same filters, counted rather than windowed."""
    return _select(exp.select(exp.func("COUNT", exp.Star())), predicates).sql(dialect=_DIALECT)


def _counts_sql(column: str, predicates: list[exp.Expression]) -> str:
    """The options template: one allowlisted categorical column, counted and ordered by itself."""
    grouped = exp.select(exp.column(column), exp.func("COUNT", exp.Star()))
    return (
        _select(grouped, predicates)
        .group_by(exp.column(column))
        .order_by(exp.column(column))
        .sql(dialect=_DIALECT)
    )


def _select(selection: exp.Select, predicates: list[exp.Expression]) -> exp.Select:
    """Every template's shared trunk: the one table, narrowed by the bound filter predicates.

    Each placeholder ends up in the root WHERE, in the order `_PREDICATES` renders them, which is
    the order their values are bound in - the one thing every template here relies on.
    """
    query = selection.from_(exp.table_(ALLOWED_TABLE))
    for predicate in predicates:
        query = query.where(predicate)
    return query


def _bind(filters: Filters) -> tuple[list[exp.Expression], tuple[object, ...]]:
    """The predicates a set of filters asks for, and their values in the order they render."""
    predicates: list[exp.Expression] = []
    values: list[object] = []
    for field, build in _PREDICATES.items():
        value = _clean(field, getattr(filters, field))
        if value is None:
            continue
        predicates.append(build())
        values.append(value)
    return predicates, tuple(values)


def _clean(field: str, value: object) -> object | None:
    """Normalize one filter value, refusing what is not a filter at all; blank means unfiltered."""
    if value is None:
        return None
    if field in _TEXT_FILTERS:
        return _text(field, value)
    if field in _DATE_FILTERS:
        return _iso_date(field, _text(field, value))
    return value


def _text(field: str, value: object) -> str | None:
    """A filter box's text, trimmed and length-bounded; a blank box filters nothing."""
    text = str(value).strip()
    cap = runtime().browse.max_filter_chars
    if len(text) > cap:
        raise QueryRejected(
            f"a {field} filter is at most {cap} characters, not {len(text)}", retryable=False
        )
    return text or None


def _iso_date(field: str, text: str | None) -> str | None:
    """An ISO calendar date, refused by name rather than compared as arbitrary text."""
    if text is None:
        return None
    try:
        date.fromisoformat(text)
    except ValueError as invalid:
        raise QueryRejected(
            f"{field} must be an ISO date such as 2020-01-31, not {text!r}", retryable=False
        ) from invalid
    return text


def _page_size(requested: int | None) -> int:
    """The page actually served: the requested size held between one row and the executor's cap."""
    config = runtime()
    if requested is None:
        return min(config.browse.page_size, config.db.max_result_rows)
    return min(max(requested, 1), config.db.max_result_rows)


def _contains(column: str) -> exp.Expression:
    """A case-insensitive substring test, the reader's text bound rather than made a pattern."""
    found = exp.func(
        "INSTR", exp.func("LOWER", exp.column(column)), exp.func("LOWER", exp.Placeholder())
    )
    return exp.GT(this=found, expression=exp.Literal.number(0))


def _equals(column: str) -> exp.Expression:
    """A case-insensitive equality on a categorical column, the value bound."""
    return exp.EQ(
        this=exp.func("LOWER", exp.column(column)),
        expression=exp.func("LOWER", exp.Placeholder()),
    )


def _at_least(column: str) -> exp.Expression:
    """The lower end of a range filter, the bound inclusive."""
    return exp.GTE(this=exp.column(column), expression=exp.Placeholder())


def _at_most(column: str) -> exp.Expression:
    """The upper end of a range filter, the bound inclusive."""
    return exp.LTE(this=exp.column(column), expression=exp.Placeholder())


# Insertion order is the render order, which is the order the values are bound in.
_PREDICATES: dict[str, Callable[[], exp.Expression]] = {
    "tenant_id": lambda: _equals("tenant_id"),
    "name": lambda: _contains("name"),
    "department": lambda: _equals("department"),
    "salary_min": lambda: _at_least("salary"),
    "salary_max": lambda: _at_most("salary"),
    "score_min": lambda: _at_least("performance_score"),
    "score_max": lambda: _at_most("performance_score"),
    "hired_from": lambda: _at_least("hire_date"),
    "hired_to": lambda: _at_most("hire_date"),
}
