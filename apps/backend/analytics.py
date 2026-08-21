"""Structured analytics with zero generated SQL: aggregates, Tukey anomalies and chart data.

The three tools the agent reaches for instead of writing SQL (ADR 0011). Every argument is
a typed value checked against an allowlist defined in this module before anything touches a
query, so a model can name a metric, a numeric column, a grouping dimension or a chart kind
and nothing else. The queries themselves are two fixed shapes built from the sqlglot AST -
an aggregate and a row scan - never a string this module concatenates, and both run through
`db.execute_scoped`, which validates, scopes, caps and audits them like any other query.
This module opens no connection and reads no tenant from its own arguments beyond the
`tenant_id` its caller took from the verified JWT.

An invalid argument raises `QueryRejected(retryable=True)`: an honest error the agent can
correct on the next attempt (ADR 0011), never a silent fallback to a default.

Grouping dimensions are named, not raw columns: `department` is the schema's one
low-cardinality categorical column, and `hire_year` is a fixed `SUBSTRING(hire_date, 1, 4)`
over the ISO date - a time axis a line chart can use. Every other column is an identifier,
free text, or a value; grouping by one would produce a row per employee, not a statistic.

Anomalies and histograms need every scoped row, which the executor deliberately caps at
`db.max_result_rows` to protect the model's context window (ADR 0007). `_scan` therefore
pages: each query stays inside the cap, and the pages are stitched together here, bounded by
`analytics.max_scan_rows`. That is sound because these rows never reach the model - only the
flagged anomalies and the chart points do, and those are database ground truth (ADR 0011).

Quartiles are computed in Python with `numpy.percentile`, whose default `method="linear"`
interpolates between the two order statistics straddling the quartile position (h = (n-1)p,
the R type-7 definition). Fences are Tukey's, per the NIST/SEMATECH handbook: a value is an
outlier when it lies more than 1.5 x IQR below Q1 or above Q3, computed within its own group
so a department's pay scale is judged against itself and not against the whole tenant.

ChartSpec - the exact dict `plot_data` returns, which the frontend consumes verbatim:

    {
        "kind": "bar" | "line" | "histogram",
        "title": str,                 # e.g. "avg salary by department"
        "x_label": str,               # e.g. "department", "hire year", "salary"
        "y_label": str,               # e.g. "avg salary", "employees"
        "data": [{"x": str, "y": float}, ...],
    }

One point per group for `bar` and `line`, ordered by the dimension, so `line` reads
chronologically; one point per bin for `histogram`, where `x` is the bin label
`"<low>-<high>"` from the bin edges and `y` is the number of rows in the bin.

References: NIST/SEMATECH e-Handbook, box plots and Tukey fences -
https://www.itl.nist.gov/div898/handbook/prc/section1/prc16.htm; numpy.percentile
interpolation methods - https://numpy.org/doc/stable/reference/generated/numpy.percentile.html
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import numpy as np
from sqlglot import exp

from db import DEFAULT_DB_PATH, QueryResult, execute_scoped
from runtime import runtime
from security import ALLOWED_TABLE, QueryRejected

_DIALECT = "sqlite"

# The four leading characters of an ISO date are its year: a fact about the schema, not a knob.
_YEAR_CHARS = 4

_METRIC_FUNCTIONS = {"avg": "AVG", "sum": "SUM", "count": "COUNT", "min": "MIN", "max": "MAX"}
_DIMENSION_EXPRESSIONS = {
    "department": exp.column("department"),
    "hire_year": exp.func(
        "SUBSTR",
        exp.column("hire_date"),
        exp.Literal.number(1),
        exp.Literal.number(_YEAR_CHARS),
    ),
}

METRICS = frozenset(_METRIC_FUNCTIONS)
NUMERIC_COLUMNS = frozenset({"salary", "performance_score"})
GROUP_BY_COLUMNS = frozenset(_DIMENSION_EXPRESSIONS)
CHART_KINDS = frozenset({"bar", "line", "histogram"})

DEFAULT_GROUP_BY = "department"
_DEFAULT_METRIC = "avg"
_HISTOGRAM = "histogram"
_CHART_DIMENSIONS = {"bar": DEFAULT_GROUP_BY, "line": "hire_year"}
_HISTOGRAM_Y_LABEL = "employees"

_IDENTITY_COLUMNS = ("user_id", "name")
_ORDER_COLUMN = "user_id"

# Tukey's fences as the NIST handbook defines them: the method, not a tunable.
_QUARTILES = (25, 75)
_FENCE_MULTIPLIER = 1.5


class ChartPoint(TypedDict):
    """One plotted point: a category, year or bin label and the value charted for it."""

    x: str
    y: float


class ChartSpec(TypedDict):
    """The chart contract the frontend renders verbatim; the module docstring shows the shape."""

    kind: str
    title: str
    x_label: str
    y_label: str
    data: list[ChartPoint]


@dataclass(frozen=True)
class Anomaly:
    """One scoped row beyond its group's Tukey fences, carrying the fences that flagged it."""

    group: str
    user_id: int
    name: str
    value: float
    lower_fence: float
    upper_fence: float


def get_stats(
    metric: str,
    column: str,
    group_by: str | None,
    tenant_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> QueryResult:
    """One aggregate over the tenant's rows, optionally per group, from a fixed query template."""
    _require(metric, METRICS, "metric")
    _require(column, NUMERIC_COLUMNS, "column")
    if group_by is not None:
        _require(group_by, GROUP_BY_COLUMNS, "group_by")
    return execute_scoped(_stats_sql(metric, column, group_by), tenant_id, db_path=db_path)


def detect_anomalies(
    column: str,
    tenant_id: str,
    group_by: str = DEFAULT_GROUP_BY,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[Anomaly]:
    """The tenant's rows lying beyond 1.5 x IQR from their own group's quartiles (Tukey fences)."""
    _require(column, NUMERIC_COLUMNS, "column")
    _require(group_by, GROUP_BY_COLUMNS, "group_by")
    selections = (
        *(exp.column(name) for name in _IDENTITY_COLUMNS),
        _dimension(group_by),
        exp.column(column),
    )
    groups: dict[str, list[tuple[int, str, float]]] = defaultdict(list)
    for user_id, name, group, value in _scan(selections, tenant_id, db_path):
        groups[group].append((user_id, name, float(value)))
    flagged = [anomaly for group in sorted(groups) for anomaly in _flag(group, groups[group])]
    return sorted(flagged, key=lambda anomaly: (anomaly.group, anomaly.value))


def plot_data(
    kind: str,
    column: str,
    tenant_id: str,
    metric: str | None = None,
    group_by: str | None = None,
    bins: int | None = None,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> ChartSpec:
    """The ChartSpec for one chart, its values fetched here so no number passes through a model."""
    _require(kind, CHART_KINDS, "kind")
    _require(column, NUMERIC_COLUMNS, "column")
    if kind == _HISTOGRAM:
        _refuse_unused(kind, metric=metric, group_by=group_by)
        return _histogram(column, tenant_id, bins, db_path)
    _refuse_unused(kind, bins=bins)
    metric = metric if metric is not None else _DEFAULT_METRIC
    dimension = group_by if group_by is not None else _CHART_DIMENSIONS[kind]
    result = get_stats(metric, column, dimension, tenant_id, db_path=db_path)
    return ChartSpec(
        kind=kind,
        title=f"{metric} {_label(column)} by {_label(dimension)}",
        x_label=_label(dimension),
        y_label=f"{metric} {_label(column)}",
        data=[ChartPoint(x=str(group), y=float(value)) for group, value in result.rows],
    )


def _require(value: object, allowed: frozenset[str], what: str) -> None:
    """Refuse anything outside the allowlist, retryably, so the agent can name a valid value."""
    if value not in allowed:
        raise QueryRejected(
            f"{what} must be one of {sorted(allowed)}, not {value!r}", retryable=True
        )


def _refuse_unused(kind: str, **arguments: object) -> None:
    """Refuse an argument this chart kind does not use, rather than silently dropping it."""
    for name, value in arguments.items():
        if value is not None:
            raise QueryRejected(f"{name} does not apply to a {kind} chart", retryable=True)


def _dimension(group_by: str) -> exp.Expression:
    """The allowlisted grouping expression, aliased so GROUP BY and the output share one name."""
    return _DIMENSION_EXPRESSIONS[group_by].copy().as_(group_by)


def _stats_sql(metric: str, column: str, group_by: str | None) -> str:
    """The aggregate template, built from the AST over names both allowlists have approved."""
    aggregate = exp.func(_METRIC_FUNCTIONS[metric], exp.column(column)).as_(f"{metric}_{column}")
    if group_by is None:
        return exp.select(aggregate).from_(exp.table_(ALLOWED_TABLE)).sql(dialect=_DIALECT)
    return (
        exp.select(_dimension(group_by), aggregate)
        .from_(exp.table_(ALLOWED_TABLE))
        .group_by(exp.column(group_by))
        .order_by(exp.column(group_by))
        .sql(dialect=_DIALECT)
    )


def _scan_sql(selections: tuple[exp.Expression, ...], limit: int, offset: int) -> str:
    """The row-scan template: one page of scoped rows in primary-key order, so paging is stable."""
    return (
        exp.select(*(selection.copy() for selection in selections))
        .from_(exp.table_(ALLOWED_TABLE))
        .order_by(exp.column(_ORDER_COLUMN))
        .limit(limit)
        .offset(offset)
        .sql(dialect=_DIALECT)
    )


def _scan(
    selections: tuple[exp.Expression, ...], tenant_id: str, db_path: Path
) -> list[tuple[object, ...]]:
    """Every scoped row for these selections, paged so no single query exceeds the row cap."""
    config = runtime()
    page_size = config.db.max_result_rows
    rows: list[tuple[object, ...]] = []
    while True:
        page = execute_scoped(
            _scan_sql(selections, page_size, len(rows)), tenant_id, db_path=db_path
        )
        rows.extend(page.rows)
        if page.returned_count < page_size:
            return rows
        if len(rows) >= config.analytics.max_scan_rows:
            raise QueryRejected(
                f"the scoped rows exceed the {config.analytics.max_scan_rows}-row analytics "
                "scan budget; aggregate the question instead",
                retryable=False,
            )


def _flag(group: str, members: list[tuple[int, str, float]]) -> list[Anomaly]:
    """The group's members beyond its Tukey fences; a group with no spread flags nobody."""
    quartile_one, quartile_three = np.percentile([value for *_, value in members], _QUARTILES)
    reach = _FENCE_MULTIPLIER * (quartile_three - quartile_one)
    lower, upper = float(quartile_one - reach), float(quartile_three + reach)
    return [
        Anomaly(group, user_id, name, value, lower, upper)
        for user_id, name, value in members
        if value < lower or value > upper
    ]


def _histogram(column: str, tenant_id: str, bins: int | None, db_path: Path) -> ChartSpec:
    """Equal-width bins over the tenant's values for this column, counted with numpy."""
    config = runtime().analytics
    count = config.histogram_bins if bins is None else bins
    whole = isinstance(count, int) and not isinstance(count, bool)
    if not whole or not 1 <= count <= config.max_histogram_bins:
        raise QueryRejected(
            f"bins must be a whole number between 1 and {config.max_histogram_bins}, not {bins!r}",
            retryable=True,
        )
    values = [float(value) for (value,) in _scan((exp.column(column),), tenant_id, db_path)]
    counts, edges = np.histogram(values, bins=count)
    return ChartSpec(
        kind=_HISTOGRAM,
        title=f"{_label(column)} distribution",
        x_label=_label(column),
        y_label=_HISTOGRAM_Y_LABEL,
        data=[
            ChartPoint(x=f"{low:g}-{high:g}", y=int(rows))
            for low, high, rows in zip(edges[:-1], edges[1:], counts, strict=True)
        ],
    )


def _label(name: str) -> str:
    """The human form of a schema name, for a chart axis or title."""
    return name.replace("_", " ")
