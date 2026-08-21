"""Dataset tests: determinism and calibration of employees.csv (issue #14, ADR 0008)."""

import csv
import json
from datetime import date
from pathlib import Path
from statistics import median

import pytest

from runtime import runtime
from scripts.generate_dataset import (
    COLUMNS,
    CSV_NAME,
    DEPARTMENT_SALARY_MEDIANS,
    DEPARTMENTS,
    FIRST_USER_ID,
    MANIFEST_NAME,
    SCORE_MAX,
    SCORE_MIN,
    TENURE_CAP_YEARS,
    TENURE_REFERENCE_DATE,
    write_dataset,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TENANT_PROPORTION_TOLERANCE = 0.02
SALARY_MEDIAN_TOLERANCE = 0.15
SCORE_CLUSTER_LOW = 3.0
SCORE_CLUSTER_HIGH = 4.5
SCORE_CLUSTER_MIN_SHARE = 0.6


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    with (BACKEND_ROOT / CSV_NAME).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((BACKEND_ROOT / MANIFEST_NAME).read_text(encoding="utf-8"))


def test_two_runs_are_byte_identical(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write_dataset(first)
    write_dataset(second)
    for name in (CSV_NAME, MANIFEST_NAME):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_committed_files_match_a_fresh_generation(tmp_path):
    write_dataset(tmp_path)
    for name in (CSV_NAME, MANIFEST_NAME):
        assert (tmp_path / name).read_bytes() == (BACKEND_ROOT / name).read_bytes()


def test_shape_and_identity(rows):
    config = runtime().dataset
    assert len(rows) == config.rows
    assert tuple(rows[0]) == COLUMNS
    assert len({row["user_id"] for row in rows}) == config.rows
    assert {row["department"] for row in rows} == set(DEPARTMENTS)


def test_user_ids_are_a_contiguous_integer_range(rows):
    config = runtime().dataset
    ids = [row["user_id"] for row in rows]
    assert all(user_id.isdigit() for user_id in ids)
    assert [int(user_id) for user_id in ids] == list(
        range(FIRST_USER_ID, FIRST_USER_ID + config.rows)
    )


def test_tenant_proportions_match_the_configured_split(rows):
    config = runtime().dataset
    for tenant, share in config.tenant_split.items():
        actual = sum(row["tenant_id"] == tenant for row in rows) / len(rows)
        assert abs(actual - share) <= TENANT_PROPORTION_TOLERANCE


@pytest.mark.parametrize("department", DEPARTMENTS)
def test_department_salary_median_matches_the_anchor(rows, department):
    anchor = DEPARTMENT_SALARY_MEDIANS[department]
    salaries = [int(row["salary"]) for row in rows if row["department"] == department]
    assert abs(median(salaries) - anchor) / anchor <= SALARY_MEDIAN_TOLERANCE


def test_performance_scores_are_bounded_and_left_clustered(rows):
    scores = [float(row["performance_score"]) for row in rows]
    assert all(SCORE_MIN <= score <= SCORE_MAX for score in scores)
    clustered = sum(SCORE_CLUSTER_LOW <= score <= SCORE_CLUSTER_HIGH for score in scores)
    assert clustered / len(scores) >= SCORE_CLUSTER_MIN_SHARE


def test_hire_dates_are_iso_and_in_the_past(rows):
    dates = [date.fromisoformat(row["hire_date"]) for row in rows]
    assert max(dates) <= TENURE_REFERENCE_DATE
    assert min(dates).year >= TENURE_REFERENCE_DATE.year - int(TENURE_CAP_YEARS)


def test_poisoned_rows_exactly_match_the_manifest(rows, manifest):
    config = runtime().dataset
    assert manifest["count"] == round(config.rows * config.poisoned_fraction)
    assert manifest["count"] == len(manifest["records"])
    notes_by_id = {int(row["user_id"]): row["notes"] for row in rows}
    tenant_by_id = {int(row["user_id"]): row["tenant_id"] for row in rows}
    markers = {record["marker"] for record in manifest["records"]}
    poisoned_ids = {record["user_id"] for record in manifest["records"]}
    assert all(isinstance(user_id, int) for user_id in poisoned_ids)

    for record in manifest["records"]:
        assert record["marker"] in notes_by_id[record["user_id"]]
        assert record["tenant_id"] == tenant_by_id[record["user_id"]]

    for user_id, notes in notes_by_id.items():
        if user_id in poisoned_ids:
            continue
        assert not any(marker in notes for marker in markers)
