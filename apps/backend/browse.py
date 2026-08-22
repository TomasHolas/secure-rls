"""Browsing what the signed-in tenant can see: the Records and Notes tabs' data path (ADR 0014).

The tabs exist so a reader can check the isolation without trusting the agent: sign in as one
tenant, see its rows and its note corpus, sign in as another and see entirely different ones.
That only proves something if the tabs are not a second way to reach the data, so this module
adds no data path at all. Every row it serves comes from one of two fixed templates built from
the sqlglot AST over the allowlists below and executed by `db.execute_scoped` - the same
validator, scoping rewrite, engine controls, egress check, row cap and audit log the agent's own
tools go through (ADRs 0002, 0003, 0007). Nothing here concatenates SQL a reader typed,
interpolates a column name, or opens a connection.

A reader's filter values travel as bound parameters, declared to the executor (ADR 0002 as
amended), so a quote, a comment marker or a UNION typed into the name box is compared as text
and never parsed as SQL. The name filter is `INSTR(LOWER(name), LOWER(?)) > 0` rather than a
LIKE on purpose: a substring search is what the box promises, and LIKE would silently give a
typed `%` or `_` a meaning the reader did not ask for. Sort column and direction are never
values - they are words checked against an allowlist by `security.require_allowed` before they
become AST nodes, terminally, because no model is there to correct a query string.

Both templates select `tenant_id` itself. It costs nothing, it puts the egress check (layer 4b)
on this path too, and it is the demo's point made visible: every row a reader sees carries the
tenant it came from.

Paging. The page ceiling IS the executor's row cap (`db.max_result_rows`, ADR 0007), because a
larger page could not be served whole; a request beyond it is clamped and the response states
the page size it actually used, so a clamp is reported rather than silent. The true total is a
COUNT over the same filters through the same executor - one extra scoped query, which is what
makes "450 rows" a fact about the tenant's data rather than about the page in hand.

Notes. The corpus listing is the second template over the same table, since a note is a column
of its employee's row. The search is not a template at all: it delegates to
`rag.search_notes_scoped`, the agent's own retrieval path (ADR 0010), so what the reader sees -
the same hits, in the same order, with the same distances - is literally what the `search_notes`
tool would have returned for that query. `flagged_user_ids` reads the committed poison manifest
so the tab can mark the planted injection payloads: it is repo metadata rather than tenant data,
and it is filtered to the caller's tenant anyway.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlglot import exp

from analytics import DEFAULT_GROUP_BY, get_stats
from db import DEFAULT_DB_PATH, execute_scoped
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
NOTE_COLUMNS = ("user_id", "tenant_id", "name", "department", "notes")
SORT_COLUMNS = frozenset(
    {"user_id", "name", "department", "salary", "performance_score", "hire_date"}
)
DIRECTIONS = frozenset({"asc", "desc"})
DEFAULT_SORT = "user_id"
DEFAULT_DIRECTION = "asc"

_DIALECT = "sqlite"
# The primary key breaks every tie, so a page boundary cannot drop or repeat a row.
_TIE_BREAK = "user_id"
_DESCENDING = "desc"
_FIRST_PAGE = 1
_TEXT_FILTERS = frozenset({"name", "department"})
_DATE_FILTERS = frozenset({"hired_from", "hired_to"})
_MANIFEST_TENANT = "tenant_id"
_MANIFEST_USER = "user_id"
_MANIFEST_KIND = "payload_kind"


@dataclass(frozen=True)
class Filters:
    """What a reader may narrow a listing by: this allowlist of comparisons and nothing else."""

    name: str | None = None
    department: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    score_min: float | None = None
    score_max: float | None = None
    hired_from: str | None = None
    hired_to: str | None = None


@dataclass(frozen=True)
class BrowsePage:
    """One page of a tenant's rows, with the true total the same executor counted."""

    columns: tuple[str, ...]
    rows: list[tuple[object, ...]]
    total: int
    page: int
    page_size: int
    sort: str
    direction: str
    executed_sql: str


@dataclass(frozen=True)
class Flagged:
    """The tenant's rows the committed poison manifest plants an injection payload in."""

    user_ids: list[int]
    kinds: dict[str, str]


def browse_records(
    tenant_id: str,
    *,
    filters: Filters | None = None,
    sort: str = DEFAULT_SORT,
    direction: str = DEFAULT_DIRECTION,
    page: int = _FIRST_PAGE,
    page_size: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> BrowsePage:
    """One sorted, filtered page of the tenant's employee rows, plus how many match in all."""
    return _page(RECORD_COLUMNS, tenant_id, filters, sort, direction, page, page_size, db_path)


def browse_notes(
    tenant_id: str,
    *,
    filters: Filters | None = None,
    sort: str = DEFAULT_SORT,
    direction: str = DEFAULT_DIRECTION,
    page: int = _FIRST_PAGE,
    page_size: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> BrowsePage:
    """The same page over the note corpus: a note is a column of the employee row that owns it."""
    return _page(NOTE_COLUMNS, tenant_id, filters, sort, direction, page, page_size, db_path)


def departments(tenant_id: str, *, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, object]]:
    """The tenant's departments and their headcounts, so the filter offers real values only.

    The count comes from `analytics.get_stats`, the aggregate template the agent's own
    `get_stats` tool uses - this module adds no third query shape for a list it already has.
    """
    result = get_stats("count", "salary", DEFAULT_GROUP_BY, tenant_id, db_path=db_path)
    return [{"department": str(name), "employees": int(count)} for name, count in result.rows]


def flagged_user_ids(tenant_id: str, *, manifest_path: Path = MANIFEST_PATH) -> Flagged:
    """The tenant's planted-injection rows, from the committed manifest (repo metadata, not data).

    The manifest is generated with the dataset and committed, so naming the poisoned rows tells
    a reader nothing the repo does not already say out loud - and it lets the Notes tab point at
    a payload before the agent reads it and refuses. A missing manifest flags nothing.
    """
    if not manifest_path.exists():
        return Flagged(user_ids=[], kinds={})
    records = json.loads(manifest_path.read_text()).get("records", [])
    mine = [row for row in records if row.get(_MANIFEST_TENANT) == tenant_id]
    return Flagged(
        user_ids=[int(row[_MANIFEST_USER]) for row in mine],
        kinds={str(row[_MANIFEST_USER]): str(row.get(_MANIFEST_KIND, "")) for row in mine},
    )


def _page(
    columns: tuple[str, ...],
    tenant_id: str,
    filters: Filters | None,
    sort: str,
    direction: str,
    page: int,
    page_size: int | None,
    db_path: Path,
) -> BrowsePage:
    """Run the two scoped queries one listing needs: the page itself, and how many rows match."""
    require_allowed(sort, SORT_COLUMNS, "sort", retryable=False)
    require_allowed(direction, DIRECTIONS, "direction", retryable=False)
    predicates, values = _bind(filters or Filters())
    size = _page_size(page_size)
    number = max(page, _FIRST_PAGE)
    total = _total(predicates, values, tenant_id, db_path)
    result = execute_scoped(
        _page_sql(columns, predicates, sort, direction, size, (number - 1) * size),
        tenant_id,
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
    )


def _total(
    predicates: list[exp.Expression],
    values: tuple[object, ...],
    tenant_id: str,
    db_path: Path,
) -> int:
    """How many of the tenant's rows match the filters at all, page or no page (ADR 0007)."""
    result = execute_scoped(
        _count_sql(predicates), tenant_id, params=values, db_path=db_path
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


def _count_sql(predicates: list[exp.Expression]) -> str:
    """The total template: the same filters, counted rather than windowed."""
    return _select(exp.select(exp.func("COUNT", exp.Star())), predicates).sql(dialect=_DIALECT)


def _select(selection: exp.Select, predicates: list[exp.Expression]) -> exp.Select:
    """Both templates' shared trunk: the one table, narrowed by the bound filter predicates.

    Every placeholder ends up in the root WHERE, which is what layer 4a demands of a caller that
    binds its own values: SQL renders a FROM before its WHERE, so the tenant is bound first.
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
    "name": lambda: _contains("name"),
    "department": lambda: _equals("department"),
    "salary_min": lambda: _at_least("salary"),
    "salary_max": lambda: _at_most("salary"),
    "score_min": lambda: _at_least("performance_score"),
    "score_max": lambda: _at_most("performance_score"),
    "hired_from": lambda: _at_least("hire_date"),
    "hired_to": lambda: _at_most("hire_date"),
}
