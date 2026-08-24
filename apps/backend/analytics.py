"""Structured analytics with zero generated SQL: aggregates, Tukey anomalies and chart data.

The three tools the agent reaches for instead of writing SQL (ADR 0011). Every argument is
a typed value checked against an allowlist defined in this module before anything touches a
query, so a model can name a metric, a numeric column, a grouping dimension or a chart kind
and nothing else. The queries themselves are two fixed shapes built from the sqlglot AST -
an aggregate and a row scan - never a string this module concatenates, and both run through
`db.py`'s executor, which validates, scopes, caps and audits them like any other query.
This module opens no connection and reads no tenant from its own arguments beyond the
`tenant_id` its caller took from the verified JWT.

Scope (ADR 0002 as amended). The same two templates serve a one-tenant and an all-tenant
identity; what differs is which executor they run through, `db.execute_scoped` or
`db.execute_unscoped`, and `all_tenants` is the caller's verified scope saying which. It is
keyword-only and defaults to the narrow reading, so a caller that says nothing gets one tenant.
Every argument the model can write is still checked against the allowlists above, and none of
them is this one: the agent closes over it at build time from the token (`agent._build_tools`),
so no tool argument reaches it.

An invalid argument raises `QueryRejected(retryable=True)`: an honest error the agent can
correct on the next attempt (ADR 0011), never a silent fallback to a default.

Grouping dimensions are named, not raw columns: `department` is the schema's one
low-cardinality categorical column, `hire_year` is a fixed `SUBSTRING(hire_date, 1, 4)`
over the ISO date - a time axis a line chart can use - and `score_band` is a fixed
`CAST(performance_score AS INTEGER)`, the rating truncated to its whole star, which turns a
continuous score into the handful of categories a second chart dimension needs. Every other
column is an identifier, free text, or a value; grouping by one would produce a row per
employee, not a statistic.

Anomalies and histograms need every scoped row, which the executor deliberately caps at
`db.max_result_rows` to protect the model's context window (ADR 0007). `_scan` therefore
pages: each query stays inside the cap, and the pages are stitched together here, bounded by
`analytics.max_scan_rows`. That is sound because these rows never reach the model - only the
flagged anomalies and the chart points do, and those are database ground truth (ADR 0011).

Quartiles are computed in Python with `numpy.percentile`, whose default `method="linear"`
interpolates between the two order statistics straddling the quartile position (h = (n-1)p,
the R type-7 definition). Fences are Tukey's, per the NIST/SEMATECH handbook: a value is an
outlier when it lies more than 1.5 x IQR below Q1 or above Q3, computed within its own group
so a department's pay scale is judged against itself and not against the whole tenant. The
`box` chart and `detect_anomalies` share that one computation (`_quartiles`, `_fences`), so a
box plot is a picture of exactly the fences the anomaly tool flags against.

ChartSpec - the exact dict `plot_data` returns, which the frontend consumes verbatim:

    {
        "kind": "bar" | "line" | "grouped_bar" | "histogram" | "scatter" | "box",
        "title": str,                 # e.g. "avg salary by department"
        "x_label": str,               # e.g. "department", "hire year", "salary"
        "y_label": str,               # e.g. "avg salary", "employees"
        "series_label": str,          # grouped_bar only: what the series values name
        "data": [ChartPoint, ...],
    }

Every point carries `y`, its value on the value axis. Which further keys it carries is fixed
per kind, and no kind emits a key another kind's renderer would read:

    bar, line      x: the category or year label; one point per group, ordered by the
                   dimension, so `line` reads chronologically
    grouped_bar    x, plus series: the value of the second dimension this bar belongs to;
                   one point per (dimension, series) pair present in the data
    histogram      x_low, x_high: the bin's numeric edges, and y the rows in the bin. The
                   edges travel as numbers, never as a label: grouping digits into a
                   readable "155,230" is a locale decision, and the only formatter this
                   product has is the frontend's (`src/lib/format.ts`)
    scatter        x: the row's name, x_value: its value on the numeric x axis; one point per
                   scoped row, the named numeric column against the other one
    box            x: the group, y: its median, q1/q3: its quartiles, low/high: the whisker
                   ends, which are the extreme values still inside the group's Tukey fences

References: NIST/SEMATECH e-Handbook, box plots and Tukey fences -
https://www.itl.nist.gov/div898/handbook/prc/section1/prc16.htm; numpy.percentile
interpolation methods - https://numpy.org/doc/stable/reference/generated/numpy.percentile.html
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict

import numpy as np
from sqlglot import exp

from db import QueryResult, execute_scoped, execute_unscoped
from paths import DB_PATH
from runtime import runtime
from security import ALLOWED_TABLE, QueryRejected, require_allowed

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
    "score_band": exp.cast(exp.column("performance_score"), "INT"),
}

METRICS = frozenset(_METRIC_FUNCTIONS)
NUMERIC_COLUMNS = frozenset({"salary", "performance_score"})
GROUP_BY_COLUMNS = frozenset(_DIMENSION_EXPRESSIONS)
CHART_KINDS = frozenset({"bar", "line", "grouped_bar", "histogram", "scatter", "box"})

DEFAULT_GROUP_BY = "department"
_DEFAULT_METRIC = "avg"
_HISTOGRAM = "histogram"
_GROUPED_BAR = "grouped_bar"
_SCATTER = "scatter"
_BOX = "box"
_CHART_DIMENSIONS = {
    "bar": DEFAULT_GROUP_BY,
    "line": "hire_year",
    _GROUPED_BAR: DEFAULT_GROUP_BY,
    _BOX: DEFAULT_GROUP_BY,
}
_DEFAULT_SERIES_BY = "score_band"
_HISTOGRAM_Y_LABEL = "employees"

# The two numeric columns paired explicitly: a scatter needs a second axis, and this is it.
_SCATTER_AXES = {"salary": "performance_score", "performance_score": "salary"}

_NAME_COLUMN = "name"
_IDENTITY_COLUMNS = ("user_id", _NAME_COLUMN)
_ORDER_COLUMN = "user_id"

# Tukey's fences as the NIST handbook defines them: the method, not a tunable.
_QUARTILES = (25, 75)
_FENCE_MULTIPLIER = 1.5


class ChartPoint(TypedDict):
    """One plotted point; which keys beyond `y` it carries is fixed per chart kind."""

    y: float
    x: NotRequired[str]
    series: NotRequired[str]
    x_value: NotRequired[float]
    x_low: NotRequired[float]
    x_high: NotRequired[float]
    low: NotRequired[float]
    q1: NotRequired[float]
    q3: NotRequired[float]
    high: NotRequired[float]


class ChartSpec(TypedDict):
    """The chart contract the frontend renders verbatim; the module docstring shows the shape."""

    kind: str
    title: str
    x_label: str
    y_label: str
    data: list[ChartPoint]
    series_label: NotRequired[str]


@dataclass(frozen=True)
class Anomaly:
    """One scoped row beyond its group's Tukey fences, carrying the fences that flagged it."""

    group: str
    user_id: int
    name: str
    value: float
    lower_fence: float
    upper_fence: float


def _execute(
    sql: str, tenant_id: str, all_tenants: bool, db_path: Path
) -> QueryResult:
    """Run one template through the executor the caller's verified scope selects."""
    if all_tenants:
        return execute_unscoped(sql, tenant_id, db_path=db_path)
    return execute_scoped(sql, tenant_id, db_path=db_path)


def get_stats(
    metric: str,
    column: str,
    group_by: str | None,
    tenant_id: str,
    *,
    db_path: Path = DB_PATH,
    all_tenants: bool = False,
) -> QueryResult:
    """One aggregate over the rows in scope, optionally per group, from a fixed query template."""
    require_allowed(metric, METRICS, "metric")
    require_allowed(column, NUMERIC_COLUMNS, "column")
    dimensions = ()
    if group_by is not None:
        require_allowed(group_by, GROUP_BY_COLUMNS, "group_by")
        dimensions = (group_by,)
    return _execute(_stats_sql(metric, column, dimensions), tenant_id, all_tenants, db_path)


def detect_anomalies(
    column: str,
    tenant_id: str,
    group_by: str = DEFAULT_GROUP_BY,
    *,
    db_path: Path = DB_PATH,
    all_tenants: bool = False,
) -> list[Anomaly]:
    """The rows in scope lying beyond 1.5 x IQR from their own group's quartiles (Tukey fences)."""
    require_allowed(column, NUMERIC_COLUMNS, "column")
    require_allowed(group_by, GROUP_BY_COLUMNS, "group_by")
    selections = (
        *(exp.column(name) for name in _IDENTITY_COLUMNS),
        _dimension(group_by),
        exp.column(column),
    )
    groups: dict[str, list[tuple[int, str, float]]] = defaultdict(list)
    for user_id, name, group, value in _scan(selections, tenant_id, all_tenants, db_path):
        groups[group].append((user_id, name, float(value)))
    flagged = [anomaly for group in sorted(groups) for anomaly in _flag(group, groups[group])]
    return sorted(flagged, key=lambda anomaly: (anomaly.group, anomaly.value))


def plot_data(
    kind: str,
    column: str,
    tenant_id: str,
    metric: str | None = None,
    group_by: str | None = None,
    series_by: str | None = None,
    bins: int | None = None,
    *,
    db_path: Path = DB_PATH,
    all_tenants: bool = False,
) -> ChartSpec:
    """The ChartSpec for one chart, its values fetched here so no number passes through a model."""
    require_allowed(kind, CHART_KINDS, "kind")
    require_allowed(column, NUMERIC_COLUMNS, "column")
    if kind == _HISTOGRAM:
        _refuse_unused(kind, metric=metric, group_by=group_by, series_by=series_by)
        return _histogram(column, tenant_id, bins, all_tenants, db_path)
    if kind == _SCATTER:
        _refuse_unused(kind, metric=metric, group_by=group_by, series_by=series_by, bins=bins)
        return _scatter(column, tenant_id, all_tenants, db_path)
    dimension = group_by if group_by is not None else _CHART_DIMENSIONS[kind]
    if kind == _BOX:
        _refuse_unused(kind, metric=metric, series_by=series_by, bins=bins)
        return _box(column, dimension, tenant_id, all_tenants, db_path)
    _refuse_unused(kind, bins=bins)
    metric = metric if metric is not None else _DEFAULT_METRIC
    if kind == _GROUPED_BAR:
        series = series_by if series_by is not None else _DEFAULT_SERIES_BY
        return _grouped_bar(metric, column, dimension, series, tenant_id, all_tenants, db_path)
    _refuse_unused(kind, series_by=series_by)
    result = get_stats(
        metric, column, dimension, tenant_id, db_path=db_path, all_tenants=all_tenants
    )
    return ChartSpec(
        kind=kind,
        title=f"{metric} {_label(column)} by {_label(dimension)}",
        x_label=_label(dimension),
        y_label=f"{metric} {_label(column)}",
        data=[ChartPoint(x=str(group), y=float(value)) for group, value in result.rows],
    )


def _refuse_unused(kind: str, **arguments: object) -> None:
    """Refuse an argument this chart kind does not use, rather than silently dropping it."""
    for name, value in arguments.items():
        if value is not None:
            raise QueryRejected(f"{name} does not apply to a {kind} chart", retryable=True)


def _dimension(group_by: str) -> exp.Expression:
    """The allowlisted grouping expression, aliased so GROUP BY and the output share one name."""
    return _DIMENSION_EXPRESSIONS[group_by].copy().as_(group_by)


def _stats_sql(metric: str, column: str, dimensions: tuple[str, ...]) -> str:
    """The aggregate template, built from the AST over names both allowlists have approved."""
    aggregate = exp.func(_METRIC_FUNCTIONS[metric], exp.column(column)).as_(f"{metric}_{column}")
    if not dimensions:
        return exp.select(aggregate).from_(exp.table_(ALLOWED_TABLE)).sql(dialect=_DIALECT)
    return (
        exp.select(*(_dimension(name) for name in dimensions), aggregate)
        .from_(exp.table_(ALLOWED_TABLE))
        .group_by(*(exp.column(name) for name in dimensions))
        .order_by(*(exp.column(name) for name in dimensions))
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
    selections: tuple[exp.Expression, ...], tenant_id: str, all_tenants: bool, db_path: Path
) -> list[tuple[object, ...]]:
    """Every row in scope for these selections, paged so no single query exceeds the row cap."""
    config = runtime()
    page_size = config.db.max_result_rows
    rows: list[tuple[object, ...]] = []
    while True:
        page = _execute(
            _scan_sql(selections, page_size, len(rows)), tenant_id, all_tenants, db_path
        )
        rows.extend(page.rows)
        if page.returned_count < page_size:
            return rows
        if len(rows) >= config.analytics.max_scan_rows:
            raise QueryRejected(
                f"the rows in scope exceed the {config.analytics.max_scan_rows}-row analytics "
                "scan budget; aggregate the question instead",
                retryable=False,
            )


def _quartiles(values: list[float]) -> tuple[float, float]:
    """Q1 and Q3 by numpy's linear interpolation: this module's one quartile computation."""
    quartile_one, quartile_three = np.percentile(values, _QUARTILES)
    return float(quartile_one), float(quartile_three)


def _fences(quartile_one: float, quartile_three: float) -> tuple[float, float]:
    """Tukey's fences: 1.5 x IQR below Q1 and above Q3."""
    reach = _FENCE_MULTIPLIER * (quartile_three - quartile_one)
    return quartile_one - reach, quartile_three + reach


def _flag(group: str, members: list[tuple[int, str, float]]) -> list[Anomaly]:
    """The group's members beyond its Tukey fences; a group with no spread flags nobody."""
    lower, upper = _fences(*_quartiles([value for *_, value in members]))
    return [
        Anomaly(group, user_id, name, value, lower, upper)
        for user_id, name, value in members
        if value < lower or value > upper
    ]


def _histogram(
    column: str, tenant_id: str, bins: int | None, all_tenants: bool, db_path: Path
) -> ChartSpec:
    """Equal-width bins over the values in scope for this column, counted with numpy."""
    config = runtime().analytics
    count = config.histogram_bins if bins is None else bins
    whole = isinstance(count, int) and not isinstance(count, bool)
    if not whole or not 1 <= count <= config.max_histogram_bins:
        raise QueryRejected(
            f"bins must be a whole number between 1 and {config.max_histogram_bins}, not {bins!r}",
            retryable=True,
        )
    values = [
        float(value)
        for (value,) in _scan((exp.column(column),), tenant_id, all_tenants, db_path)
    ]
    counts, edges = np.histogram(values, bins=count)
    return ChartSpec(
        kind=_HISTOGRAM,
        title=f"{_label(column)} distribution",
        x_label=_label(column),
        y_label=_HISTOGRAM_Y_LABEL,
        data=[
            ChartPoint(x_low=float(low), x_high=float(high), y=int(rows))
            for low, high, rows in zip(edges[:-1], edges[1:], counts, strict=True)
        ],
    )


def _grouped_bar(
    metric: str,
    column: str,
    group_by: str,
    series_by: str,
    tenant_id: str,
    all_tenants: bool,
    db_path: Path,
) -> ChartSpec:
    """One aggregate over two allowlisted dimensions at once: a bar per series within each group."""
    require_allowed(metric, METRICS, "metric")
    require_allowed(group_by, GROUP_BY_COLUMNS, "group_by")
    require_allowed(series_by, GROUP_BY_COLUMNS, "series_by")
    if series_by == group_by:
        raise QueryRejected(
            f"series_by must name a different dimension than group_by, not {series_by!r} twice",
            retryable=True,
        )
    sql = _stats_sql(metric, column, (group_by, series_by))
    result = _execute(sql, tenant_id, all_tenants, db_path)
    return ChartSpec(
        kind=_GROUPED_BAR,
        title=f"{metric} {_label(column)} by {_label(group_by)} and {_label(series_by)}",
        x_label=_label(group_by),
        y_label=f"{metric} {_label(column)}",
        series_label=_label(series_by),
        data=[
            ChartPoint(x=str(group), series=str(series), y=float(value))
            for group, series, value in result.rows
        ],
    )


def _scatter(column: str, tenant_id: str, all_tenants: bool, db_path: Path) -> ChartSpec:
    """One point per row in scope: the named numeric column against the schema's other one."""
    x_column = _SCATTER_AXES[column]
    selections = (exp.column(_NAME_COLUMN), exp.column(x_column), exp.column(column))
    return ChartSpec(
        kind=_SCATTER,
        title=f"{_label(column)} against {_label(x_column)}",
        x_label=_label(x_column),
        y_label=_label(column),
        data=[
            ChartPoint(x=str(name), x_value=float(x_value), y=float(value))
            for name, x_value, value in _scan(selections, tenant_id, all_tenants, db_path)
        ],
    )


def _box(
    column: str, group_by: str, tenant_id: str, all_tenants: bool, db_path: Path
) -> ChartSpec:
    """Each group's quartiles and whiskers: the spread detect_anomalies judges its outliers by."""
    require_allowed(group_by, GROUP_BY_COLUMNS, "group_by")
    selections = (_dimension(group_by), exp.column(column))
    groups: dict[str, list[float]] = defaultdict(list)
    for group, value in _scan(selections, tenant_id, all_tenants, db_path):
        groups[str(group)].append(float(value))
    return ChartSpec(
        kind=_BOX,
        title=f"{_label(column)} spread by {_label(group_by)}",
        x_label=_label(group_by),
        y_label=_label(column),
        data=[_box_point(group, groups[group]) for group in sorted(groups)],
    )


def _box_point(group: str, values: list[float]) -> ChartPoint:
    """One box: the group's median and quartiles, whiskered to its extremes inside the fences."""
    quartile_one, quartile_three = _quartiles(values)
    lower, upper = _fences(quartile_one, quartile_three)
    inside = [value for value in values if lower <= value <= upper]
    return ChartPoint(
        x=group,
        y=float(np.median(values)),
        q1=quartile_one,
        q3=quartile_three,
        low=min(inside),
        high=max(inside),
    )


def _label(name: str) -> str:
    """The human form of a schema name, for a chart axis or title."""
    return name.replace("_", " ")
