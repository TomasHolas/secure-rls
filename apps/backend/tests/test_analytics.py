"""Suite for the structured-analytics tools (issue #17, ADRs 0007, 0011).

The fixture is a tiny inline dataset loaded through init_db into tmp_path, never the committed
employees.csv, and its numbers are chosen so every expectation below is an exact value rather
than a tolerance. Acme's Engineering salaries are a smooth right-skewed tail (100 to 340) plus
one planted outlier (5000); Sales sits an order of magnitude higher with one planted low row
(200), which is an anomaly only against Sales' own quartiles. Beta's rows are deliberately
extreme (1, 2, 99999, 50000) so any leak into an acme statistic would move it visibly.
"""

import csv
from dataclasses import replace

import pytest
from sqlglot import exp

import analytics
import db
from analytics import Anomaly, detect_anomalies, get_stats, plot_data
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
    (1, ACME, "Ada", "Engineering", 100, 4.0, "2019-01-01", "solid quarter"),
    (2, ACME, "Alan", "Engineering", 110, 3.0, "2019-02-02", "steady delivery"),
    (3, ACME, "Amir", "Engineering", 120, 3.0, "2019-03-03", "improving"),
    (4, ACME, "Ann", "Engineering", 130, 3.0, "2019-04-04", "reliable"),
    (5, ACME, "Axel", "Engineering", 150, 3.0, "2019-05-05", "ramping up"),
    (6, ACME, "Ayo", "Engineering", 180, 3.0, "2020-01-01", "hiring lead"),
    (7, ACME, "Abe", "Engineering", 220, 3.0, "2020-02-02", "shipped the migration"),
    (8, ACME, "Aria", "Engineering", 300, 3.0, "2020-03-03", "tech lead"),
    (9, ACME, "Anil", "Engineering", 340, 3.0, "2020-04-04", "principal"),
    (10, ACME, "Ove", "Engineering", 5000, 2.0, "2020-05-05", "planted outlier"),
    (11, ACME, "Aiko", "Sales", 200, 3.0, "2021-01-01", "planted low row"),
    (12, ACME, "Adam", "Sales", 1000, 3.0, "2021-02-02", "steady pipeline"),
    (13, ACME, "Anya", "Sales", 1050, 3.0, "2021-03-03", "top closer"),
    (14, ACME, "Alva", "Sales", 1100, 3.0, "2021-04-04", "renewals"),
    (15, ACME, "Arno", "Sales", 1200, 3.0, "2021-05-05", "enterprise"),
    (16, BETA, "Bo", "Engineering", 1, 4.0, "2019-06-06", "beta secret"),
    (17, BETA, "Bea", "Engineering", 2, 4.0, "2020-06-06", "beta secret"),
    (18, BETA, "Ben", "Engineering", 99999, 4.0, "2021-06-06", "beta secret"),
    (19, BETA, "Bibi", "Sales", 50000, 4.0, "2021-07-07", "beta secret"),
    (20, BETA, "Bram", "Finance", 7, 4.0, "2021-08-08", "beta secret"),
)

_ACME_ROWS = 15
_ACME_SALARY_SUM = 11200
_BETA_SALARY_SUM = 150009
_ENGINEERING_AVG = 665.0
_SALES_AVG = 910.0
_ENGINEERING_FENCES = (-113.75, 516.25)
_SALES_FENCES = (850.0, 1250.0)
_SMOOTH_TAIL_TOP = 340.0

_SCOPED = "(SELECT * FROM employees WHERE employees.tenant_id = ?)"


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


@pytest.fixture
def tuned(monkeypatch):
    """Override the tunables analytics reads for one test, without editing runtime.json."""

    def apply(*, page_size=None, **overrides):
        config = analytics.runtime()
        patched = replace(
            config,
            db=replace(config.db, max_result_rows=page_size or config.db.max_result_rows),
            analytics=replace(config.analytics, **overrides),
        )
        monkeypatch.setattr(analytics, "runtime", lambda: patched)

    return apply


def _fenced(anomalies):
    """The flagged names keyed by group, for assertions that care about who, not how much."""
    return {anomaly.name: anomaly.group for anomaly in anomalies}


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ("sum", _ACME_SALARY_SUM),
        ("count", _ACME_ROWS),
        ("min", 100),
        ("max", 5000),
    ],
)
def test_get_stats_computes_the_exact_aggregate_over_every_scoped_row(db_path, metric, expected):
    result = get_stats(metric, "salary", None, ACME, db_path=db_path)
    assert result.columns == (f"{metric}_salary",)
    assert result.rows == [(expected,)]


def test_get_stats_average_is_taken_over_the_full_tenant(db_path):
    result = get_stats("avg", "salary", None, ACME, db_path=db_path)
    assert result.rows == [(pytest.approx(_ACME_SALARY_SUM / _ACME_ROWS),)]


def test_get_stats_groups_by_department_in_a_stable_order(db_path):
    result = get_stats("avg", "salary", "department", ACME, db_path=db_path)
    assert result.columns == ("department", "avg_salary")
    assert result.rows == [("Engineering", _ENGINEERING_AVG), ("Sales", _SALES_AVG)]


def test_get_stats_groups_by_hire_year_chronologically(db_path):
    result = get_stats("avg", "salary", "hire_year", ACME, db_path=db_path)
    assert result.columns == ("hire_year", "avg_salary")
    assert result.rows == [("2019", 122.0), ("2020", 1208.0), ("2021", 910.0)]


def test_get_stats_groups_by_the_truncated_score_band(db_path):
    result = get_stats("count", "salary", "score_band", ACME, db_path=db_path)
    assert result.columns == ("score_band", "count_salary")
    assert result.rows == [(2, 1), (3, 13), (4, 1)]


def test_get_stats_reads_the_second_numeric_column(db_path):
    result = get_stats("avg", "performance_score", "department", ACME, db_path=db_path)
    assert result.rows == [("Engineering", 3.0), ("Sales", 3.0)]


def test_get_stats_sees_only_its_own_tenant(db_path):
    acme = get_stats("sum", "salary", None, ACME, db_path=db_path)
    beta = get_stats("sum", "salary", None, BETA, db_path=db_path)
    assert acme.rows == [(_ACME_SALARY_SUM,)]
    assert beta.rows == [(_BETA_SALARY_SUM,)]


def test_stats_sql_is_a_fixed_template_over_allowlisted_names():
    assert (
        analytics._stats_sql("avg", "salary", ())
        == "SELECT AVG(salary) AS avg_salary FROM employees"
    )
    assert analytics._stats_sql("count", "salary", ("department",)) == (
        "SELECT department AS department, COUNT(salary) AS count_salary "
        "FROM employees GROUP BY department ORDER BY department"
    )
    assert analytics._stats_sql("max", "performance_score", ("hire_year",)) == (
        "SELECT SUBSTRING(hire_date, 1, 4) AS hire_year, "
        "MAX(performance_score) AS max_performance_score "
        "FROM employees GROUP BY hire_year ORDER BY hire_year"
    )


def test_stats_sql_takes_two_dimensions_from_the_same_one_allowlist():
    assert analytics._stats_sql("avg", "salary", ("department", "score_band")) == (
        "SELECT department AS department, CAST(performance_score AS INTEGER) AS score_band, "
        "AVG(salary) AS avg_salary FROM employees "
        "GROUP BY department, score_band ORDER BY department, score_band"
    )


def test_scan_sql_is_a_fixed_template_paged_in_primary_key_order():
    assert analytics._scan_sql((exp.column("salary"),), 200, 400) == (
        "SELECT salary FROM employees ORDER BY user_id LIMIT 200 OFFSET 400"
    )


def test_the_template_reaches_the_engine_tenant_scoped(db_path):
    result = get_stats("avg", "salary", "department", ACME, db_path=db_path)
    assert result.executed_sql == (
        f"SELECT department AS department, AVG(salary) AS avg_salary FROM {_SCOPED} AS employees "
        "GROUP BY department ORDER BY department"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"metric": "median"}, "metric must be one of"),
        ({"metric": "avg; DROP TABLE employees"}, "metric must be one of"),
        ({"column": "notes"}, "column must be one of"),
        ({"column": "salary, notes"}, "column must be one of"),
        ({"group_by": "name"}, "group_by must be one of"),
        ({"group_by": "department; --"}, "group_by must be one of"),
    ],
)
def test_get_stats_refuses_anything_outside_the_allowlists(db_path, kwargs, message):
    arguments = {"metric": "avg", "column": "salary", "group_by": "department"} | kwargs
    with pytest.raises(QueryRejected, match=message) as rejection:
        get_stats(
            arguments["metric"],
            arguments["column"],
            arguments["group_by"],
            ACME,
            db_path=db_path,
        )
    assert rejection.value.retryable is True


def test_an_allowlist_rejection_never_reaches_the_database(db_path):
    with pytest.raises(QueryRejected):
        get_stats("median", "salary", None, ACME, db_path=db_path)
    with pytest.raises(QueryRejected):
        plot_data("pie", "salary", ACME, db_path=db_path)
    with pytest.raises(QueryRejected):
        detect_anomalies("notes", ACME, db_path=db_path)
    assert db.audit_entries(db_path) == []


def test_detect_anomalies_flags_the_planted_outlier_with_its_group_fences(db_path):
    assert detect_anomalies("salary", ACME, db_path=db_path) == [
        Anomaly("Engineering", 10, "Ove", 5000.0, *_ENGINEERING_FENCES),
        Anomaly("Sales", 11, "Aiko", 200.0, *_SALES_FENCES),
    ]


def test_detect_anomalies_leaves_the_smooth_skewed_tail_alone(db_path):
    anomalies = detect_anomalies("salary", ACME, db_path=db_path)
    engineering = [anomaly for anomaly in anomalies if anomaly.group == "Engineering"]
    assert "Anil" not in _fenced(anomalies)
    assert _SMOOTH_TAIL_TOP < engineering[0].upper_fence


def test_detect_anomalies_judges_each_group_against_its_own_quartiles(db_path):
    anomalies = detect_anomalies("salary", ACME, db_path=db_path)
    low = next(anomaly for anomaly in anomalies if anomaly.group == "Sales")
    engineering_lower, engineering_upper = _ENGINEERING_FENCES
    assert (low.lower_fence, low.upper_fence) == _SALES_FENCES
    assert engineering_lower < low.value < engineering_upper


def test_detect_anomalies_can_group_by_a_time_dimension(db_path):
    anomalies = detect_anomalies("salary", ACME, group_by="hire_year", db_path=db_path)
    assert [anomaly.group for anomaly in anomalies] == ["2020", "2021"]
    assert [anomaly.name for anomaly in anomalies] == ["Ove", "Aiko"]


def test_detect_anomalies_never_sees_another_tenants_extremes(db_path):
    acme = detect_anomalies("salary", ACME, db_path=db_path)
    assert set(_fenced(acme)) == {"Ove", "Aiko"}
    assert detect_anomalies("salary", BETA, db_path=db_path) == []


def test_detect_anomalies_pages_the_scan_without_changing_the_result(db_path, tuned):
    unpaged = detect_anomalies("salary", ACME, db_path=db_path)
    tuned(page_size=2)
    assert detect_anomalies("salary", ACME, db_path=db_path) == unpaged


def test_a_scan_beyond_the_budget_is_refused_rather_than_answered_from_a_slice(db_path, tuned):
    tuned(page_size=4, max_scan_rows=4)
    with pytest.raises(QueryRejected, match="analytics scan budget") as rejection:
        detect_anomalies("salary", ACME, db_path=db_path)
    assert rejection.value.retryable is False


@pytest.mark.parametrize("group_by", ["name", "notes", "salary"])
def test_detect_anomalies_refuses_a_grouping_outside_the_allowlist(db_path, group_by):
    with pytest.raises(QueryRejected, match="group_by must be one of") as rejection:
        detect_anomalies("salary", ACME, group_by=group_by, db_path=db_path)
    assert rejection.value.retryable is True


def test_plot_data_bar_spec_is_the_documented_shape(db_path):
    assert plot_data("bar", "salary", ACME, db_path=db_path) == {
        "kind": "bar",
        "title": "avg salary by department",
        "x_label": "department",
        "y_label": "avg salary",
        "data": [
            {"x": "Engineering", "y": _ENGINEERING_AVG},
            {"x": "Sales", "y": _SALES_AVG},
        ],
    }


def test_plot_data_line_defaults_to_the_time_axis(db_path):
    assert plot_data("line", "salary", ACME, metric="max", db_path=db_path) == {
        "kind": "line",
        "title": "max salary by hire year",
        "x_label": "hire year",
        "y_label": "max salary",
        "data": [
            {"x": "2019", "y": 150.0},
            {"x": "2020", "y": 5000.0},
            {"x": "2021", "y": 1200.0},
        ],
    }


def test_plot_data_honours_an_explicit_grouping(db_path):
    spec = plot_data(
        "line", "performance_score", ACME, metric="min", group_by="department", db_path=db_path
    )
    assert spec["title"] == "min performance score by department"
    assert spec["data"] == [{"x": "Engineering", "y": 2.0}, {"x": "Sales", "y": 3.0}]


def test_plot_data_histogram_bin_edges_and_counts_are_exact(db_path):
    assert plot_data("histogram", "salary", ACME, bins=4, db_path=db_path) == {
        "kind": "histogram",
        "title": "salary distribution",
        "x_label": "salary",
        "y_label": "employees",
        "data": [
            {"x_low": 100.0, "x_high": 1325.0, "y": 14},
            {"x_low": 1325.0, "x_high": 2550.0, "y": 0},
            {"x_low": 2550.0, "x_high": 3775.0, "y": 0},
            {"x_low": 3775.0, "x_high": 5000.0, "y": 1},
        ],
    }


def test_plot_data_histogram_bins_default_to_the_runtime_value(db_path):
    spec = plot_data("histogram", "salary", ACME, db_path=db_path)
    assert len(spec["data"]) == analytics.runtime().analytics.histogram_bins
    assert sum(point["y"] for point in spec["data"]) == _ACME_ROWS


def test_plot_data_histogram_bins_the_second_numeric_column(db_path):
    spec = plot_data("histogram", "performance_score", ACME, bins=2, db_path=db_path)
    assert spec["x_label"] == "performance score"
    assert spec["data"] == [
        {"x_low": 2.0, "x_high": 3.0, "y": 1},
        {"x_low": 3.0, "x_high": 4.0, "y": 14},
    ]


@pytest.mark.parametrize("bins", [0, -1, 51, 2.5, "4", True])
def test_plot_data_refuses_a_bin_count_outside_the_configured_range(db_path, bins):
    with pytest.raises(QueryRejected, match="bins must be a whole number") as rejection:
        plot_data("histogram", "salary", ACME, bins=bins, db_path=db_path)
    assert rejection.value.retryable is True


@pytest.mark.parametrize("kind", ["pie", "violin", "bar; DROP TABLE employees"])
def test_plot_data_refuses_a_chart_kind_outside_the_allowlist(db_path, kind):
    with pytest.raises(QueryRejected, match="kind must be one of") as rejection:
        plot_data(kind, "salary", ACME, db_path=db_path)
    assert rejection.value.retryable is True


@pytest.mark.parametrize(
    ("kind", "kwargs", "message"),
    [
        ("histogram", {"metric": "avg"}, "metric does not apply"),
        ("histogram", {"group_by": "department"}, "group_by does not apply"),
        ("histogram", {"series_by": "score_band"}, "series_by does not apply"),
        ("bar", {"bins": 5}, "bins does not apply"),
        ("bar", {"series_by": "score_band"}, "series_by does not apply"),
        ("line", {"bins": 5}, "bins does not apply"),
        ("line", {"series_by": "score_band"}, "series_by does not apply"),
        ("grouped_bar", {"bins": 5}, "bins does not apply"),
        ("scatter", {"metric": "avg"}, "metric does not apply"),
        ("scatter", {"group_by": "department"}, "group_by does not apply"),
        ("scatter", {"series_by": "score_band"}, "series_by does not apply"),
        ("scatter", {"bins": 5}, "bins does not apply"),
        ("box", {"metric": "avg"}, "metric does not apply"),
        ("box", {"series_by": "score_band"}, "series_by does not apply"),
        ("box", {"bins": 5}, "bins does not apply"),
    ],
)
def test_plot_data_refuses_an_argument_the_chart_kind_cannot_use(db_path, kind, kwargs, message):
    with pytest.raises(QueryRejected, match=message) as rejection:
        plot_data(kind, "salary", ACME, db_path=db_path, **kwargs)
    assert rejection.value.retryable is True


def test_plot_data_charts_only_its_own_tenants_values(db_path):
    spec = plot_data("bar", "salary", BETA, metric="max", db_path=db_path)
    assert spec["data"] == [
        {"x": "Engineering", "y": 99999.0},
        {"x": "Finance", "y": 7.0},
        {"x": "Sales", "y": 50000.0},
    ]


def test_plot_data_grouped_bar_splits_each_group_by_the_second_dimension(db_path):
    assert plot_data("grouped_bar", "salary", ACME, db_path=db_path) == {
        "kind": "grouped_bar",
        "title": "avg salary by department and score band",
        "x_label": "department",
        "y_label": "avg salary",
        "series_label": "score band",
        "data": [
            {"x": "Engineering", "series": "2", "y": 5000.0},
            {"x": "Engineering", "series": "3", "y": 193.75},
            {"x": "Engineering", "series": "4", "y": 100.0},
            {"x": "Sales", "series": "3", "y": _SALES_AVG},
        ],
    }


def test_plot_data_grouped_bar_reaches_the_engine_tenant_scoped(db_path):
    plot_data("grouped_bar", "salary", ACME, metric="count", db_path=db_path)
    assert db.audit_entries(db_path)[-1].executed_sql == (
        "SELECT department AS department, CAST(performance_score AS INTEGER) AS score_band, "
        f"COUNT(salary) AS count_salary FROM {_SCOPED} AS employees "
        "GROUP BY department, score_band ORDER BY department, score_band"
    )


def test_plot_data_grouped_bar_takes_both_dimensions_from_the_caller(db_path):
    spec = plot_data(
        "grouped_bar",
        "salary",
        ACME,
        metric="count",
        group_by="hire_year",
        series_by="department",
        db_path=db_path,
    )
    assert spec["title"] == "count salary by hire year and department"
    assert spec["series_label"] == "department"
    assert spec["data"] == [
        {"x": "2019", "series": "Engineering", "y": 5.0},
        {"x": "2020", "series": "Engineering", "y": 5.0},
        {"x": "2021", "series": "Sales", "y": 5.0},
    ]


def test_plot_data_grouped_bar_charts_only_its_own_tenants_values(db_path):
    spec = plot_data("grouped_bar", "salary", BETA, metric="max", db_path=db_path)
    assert spec["data"] == [
        {"x": "Engineering", "series": "4", "y": 99999.0},
        {"x": "Finance", "series": "4", "y": 7.0},
        {"x": "Sales", "series": "4", "y": 50000.0},
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"metric": "median"}, "metric must be one of"),
        ({"group_by": "name"}, "group_by must be one of"),
        ({"series_by": "notes"}, "series_by must be one of"),
        ({"series_by": "department"}, "series_by must name a different dimension"),
    ],
)
def test_plot_data_grouped_bar_refuses_anything_outside_the_allowlists(db_path, kwargs, message):
    with pytest.raises(QueryRejected, match=message) as rejection:
        plot_data("grouped_bar", "salary", ACME, db_path=db_path, **kwargs)
    assert rejection.value.retryable is True


def test_plot_data_scatter_pairs_the_two_numeric_columns_row_by_row(db_path):
    spec = plot_data("scatter", "performance_score", ACME, db_path=db_path)
    assert spec["kind"] == "scatter"
    assert spec["title"] == "performance score against salary"
    assert spec["x_label"] == "salary"
    assert spec["y_label"] == "performance score"
    assert len(spec["data"]) == _ACME_ROWS
    assert spec["data"][0] == {"x": "Ada", "x_value": 100.0, "y": 4.0}
    assert spec["data"][-1] == {"x": "Arno", "x_value": 1200.0, "y": 3.0}


def test_plot_data_scatter_puts_the_named_column_on_the_value_axis(db_path):
    spec = plot_data("scatter", "salary", ACME, db_path=db_path)
    assert spec["title"] == "salary against performance score"
    assert spec["data"][0] == {"x": "Ada", "x_value": 4.0, "y": 100.0}


def test_plot_data_scatter_plots_only_its_own_tenants_rows(db_path):
    spec = plot_data("scatter", "salary", BETA, db_path=db_path)
    assert [point["x"] for point in spec["data"]] == ["Bo", "Bea", "Ben", "Bibi", "Bram"]
    assert max(point["y"] for point in spec["data"]) == 99999.0


def test_plot_data_scatter_pages_the_scan_without_dropping_a_row(db_path, tuned):
    tuned(page_size=4)
    spec = plot_data("scatter", "salary", ACME, db_path=db_path)
    assert len(spec["data"]) == _ACME_ROWS


def test_plot_data_box_is_the_quartiles_the_anomaly_fences_are_built_from(db_path):
    assert plot_data("box", "salary", ACME, db_path=db_path) == {
        "kind": "box",
        "title": "salary spread by department",
        "x_label": "department",
        "y_label": "salary",
        "data": [
            {
                "x": "Engineering",
                "y": 165.0,
                "q1": 122.5,
                "q3": 280.0,
                "low": 100.0,
                "high": _SMOOTH_TAIL_TOP,
            },
            {"x": "Sales", "y": 1050.0, "q1": 1000.0, "q3": 1100.0, "low": 1000.0, "high": 1200.0},
        ],
    }


def test_plot_data_box_whiskers_stop_at_the_fences_detect_anomalies_flags_against(db_path):
    spec = plot_data("box", "salary", ACME, db_path=db_path)
    boxes = {point["x"]: point for point in spec["data"]}
    flagged = {found.group: found for found in detect_anomalies("salary", ACME, db_path=db_path)}
    lower, upper = analytics._fences(boxes["Engineering"]["q1"], boxes["Engineering"]["q3"])
    assert (lower, upper) == _ENGINEERING_FENCES
    assert boxes["Engineering"]["high"] < flagged["Engineering"].value
    assert boxes["Sales"]["low"] > flagged["Sales"].value


def test_plot_data_box_groups_by_a_dimension_the_caller_names(db_path):
    spec = plot_data("box", "performance_score", ACME, group_by="hire_year", db_path=db_path)
    assert spec["title"] == "performance score spread by hire year"
    assert [point["x"] for point in spec["data"]] == ["2019", "2020", "2021"]
    assert spec["data"][2] == {"x": "2021", "y": 3.0, "q1": 3.0, "q3": 3.0, "low": 3.0, "high": 3.0}


def test_plot_data_box_summarises_only_its_own_tenants_spread(db_path):
    spec = plot_data("box", "salary", BETA, db_path=db_path)
    assert [point["x"] for point in spec["data"]] == ["Engineering", "Finance", "Sales"]
    finance = {"x": "Finance", "y": 7.0, "q1": 7.0, "q3": 7.0, "low": 7.0, "high": 7.0}
    assert spec["data"][1] == finance


@pytest.mark.parametrize("group_by", ["name", "salary; --"])
def test_plot_data_box_refuses_a_grouping_outside_the_allowlist(db_path, group_by):
    with pytest.raises(QueryRejected, match="group_by must be one of") as rejection:
        plot_data("box", "salary", ACME, group_by=group_by, db_path=db_path)
    assert rejection.value.retryable is True
