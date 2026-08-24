"""Adversarial suite for the tenant-scoped executor (issue #16, ADRs 0002, 0003, 0007).

Every test runs against a tiny inline dataset loaded through init_db into tmp_path, never
against the real employees.csv. Three acme rows would prove the happy path; the interesting
tests are the ones that disable a layer to prove the next one down still holds, which they do
by monkeypatching this module's own internals - there is no production flag that turns a layer
off. The bypass helpers below say exactly which layers a test is standing down.

Every test in this module runs twice, once in each position of `agent.prompt_guardrails`
(issue #102). The switch is prompt text and nothing else (ADR 0011 as amended), so the scoping
rewrite, the declared-parameter counting and the egress check must be byte-identical across it;
that is ADR 0002's "no prompt line is a boundary" stated as a test rather than as a sentence.
"""

import ast
import contextlib
import csv
from dataclasses import fields, replace
from datetime import UTC, datetime
from pathlib import Path
from types import CodeType

import pytest
import sqlglot

import agent
import db
from db import SecurityViolation
from security import QueryRejected

ACME = "acme"
BETA = "beta"
GAMMA = "gamma"


@pytest.fixture(autouse=True)
def _both_prompt_positions(guardrails):
    """Run this whole suite in both prompt-guardrail positions (`conftest.guardrails`)."""


def test_the_parametrization_really_moves_the_knob_db_reads(guardrails):
    """The canary, read through `db`'s own imported loader: a dead flip would pass in silence."""
    assert db.runtime().agent.prompt_guardrails is guardrails

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
    (1, ACME, "Ada", "Engineering", 100, 4.1, "2020-01-01", "solid quarter"),
    (2, ACME, "Alan", "Engineering", 200, 3.2, "2021-02-02", "steady delivery"),
    (3, ACME, "Amir", "Engineering", 300, 2.5, "2022-03-03", "improving"),
    (4, ACME, "Ann", "Sales", 400, 4.8, "2020-04-04", "top closer"),
    (5, ACME, "Axel", "Sales", 500, 3.9, "2023-05-05", "ramping up"),
    (6, ACME, "Ayo", "HR", 600, 3.3, "2019-06-06", "hiring lead"),
    (7, BETA, "Bo", "Engineering", 1000, 4.4, "2021-07-07", "beta secret"),
    (8, BETA, "Bea", "Engineering", 2000, 2.9, "2022-08-08", "beta secret"),
    (9, BETA, "Ben", "Sales", 3000, 3.6, "2023-09-09", "beta secret"),
    (10, GAMMA, "Gil", "Finance", 9999, 4.0, "2024-10-10", "gamma secret"),
)
_TENANT_ROWS = {ACME: 6, BETA: 3, GAMMA: 1}
_ACME_SALARY_AVG = 350.0

_SCOPED = "(SELECT * FROM employees WHERE employees.tenant_id = ?)"
_CTE_SQL = (
    "WITH d AS (SELECT department, AVG(salary) a FROM employees GROUP BY department) "
    "SELECT e.name FROM employees e JOIN d ON e.department = d.department"
)
_CTE_EXECUTED = (
    f"WITH d AS (SELECT department, AVG(salary) AS a FROM {_SCOPED} AS employees "
    f"GROUP BY department) SELECT e.name FROM {_SCOPED} AS e "
    "JOIN d ON e.department = d.department"
)
_NESTED_SQL = "SELECT count(*) FROM (SELECT name FROM employees WHERE salary > 250) AS sub"
_NESTED_EXECUTED = (
    f"SELECT COUNT(*) FROM (SELECT name FROM {_SCOPED} AS employees WHERE salary > 250) AS sub"
)
_CORRELATED_SQL = (
    "SELECT name FROM employees WHERE salary > (SELECT AVG(salary) FROM employees)"
)
_CORRELATED_EXECUTED = (
    f"SELECT name FROM {_SCOPED} AS employees "
    f"WHERE salary > (SELECT AVG(salary) FROM {_SCOPED} AS employees)"
)
_RUNAWAY_SQL = (
    "WITH RECURSIVE spin(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM spin) "
    "SELECT COUNT(*) FROM spin WHERE n < (SELECT COUNT(*) FROM employees)"
)

_AUDIT_TIME = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)

_REPO_ROOT = Path(__file__).resolve().parents[3]
# Built at runtime so this file's own text cannot satisfy the grep it performs.
_SQLITE_MODULE = "sqlite" + "3"
_CONNECT_CALL = f"{_SQLITE_MODULE}.connect"
_CONNECTION_OWNERS = frozenset({"db.py", "conversations.py", "test_conversations.py"})
# ADR 0014's single deliberate exception: defined in db.py, called from the listings, tested here.
_UNSCOPED_CALL = "execute_unscoped_browse"
_UNSCOPED_CALLERS = frozenset({"db.py", "browse.py", "test_db.py"})


def _frozen_clock() -> datetime:
    """A pinned audit clock, so a persisted timestamp is an assertable value."""
    return _AUDIT_TIME


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
def execute(db_path):
    """Run a query against the fixture database with the audit clock pinned."""

    def run(sql, tenant=ACME):
        return db.execute_scoped(sql, tenant, db_path=db_path, clock=_frozen_clock)

    return run


@pytest.fixture
def tuned(monkeypatch):
    """Override db tunables for one test, without editing runtime.json."""

    def apply(**overrides):
        config = db.runtime()
        patched = replace(config, db=replace(config.db, **overrides))
        monkeypatch.setattr(db, "runtime", lambda: patched)

    return apply


def _stand_down_validation(monkeypatch):
    """Hand the executor raw SQL as though layers 2, 3 and 4a had all approved it."""
    monkeypatch.setattr(
        db, "validate_sql", lambda sql, **_: sqlglot.parse_one(sql, dialect="sqlite")
    )
    monkeypatch.setattr(db, "_scope_to_tenant", lambda select, tenant_id, filters: (select, ()))
    monkeypatch.setattr(db, "_verify_scope_applied", lambda *_: None)


def _stand_down_structural_check(monkeypatch):
    """Let a rewrite through unverified, leaving only the egress row check (layer 4b) standing."""
    monkeypatch.setattr(db, "_verify_scope_applied", lambda *_: None)


def test_init_db_loads_every_row_under_the_declared_column_affinities(execute):
    total = execute("SELECT COUNT(*) FROM employees", GAMMA).rows
    types = execute("SELECT typeof(salary), typeof(performance_score) FROM employees", GAMMA)
    assert total == [(_TENANT_ROWS[GAMMA],)]
    assert types.rows == [("integer", "real")]


def test_init_db_reload_replaces_the_table_rather_than_appending(db_path, execute):
    db.init_db(db_path.with_name("employees.csv"), db_path)
    assert execute("SELECT COUNT(*) FROM employees").rows == [(_TENANT_ROWS[ACME],)]


def test_init_db_refuses_a_csv_that_is_not_the_employees_schema(tmp_path):
    csv_path = tmp_path / "wrong.csv"
    csv_path.write_text("user_id,tenant_id,secret\n1,acme,x\n")
    with pytest.raises(ValueError, match="not the employees schema"):
        db.init_db(csv_path, tmp_path / "data.db")


def test_init_db_creates_the_audit_store_as_a_separate_file(db_path):
    assert (db_path.parent / db.AUDIT_DB_NAME).exists()
    assert db.audit_entries(db_path) == []


def test_init_db_accepts_string_paths_as_well_as_path_objects(tmp_path):
    """The pinned contract (issue #96): a hand-typed string loads, it does not raise."""
    csv_path = tmp_path / "employees.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(_HEADER)
        writer.writerows(_ROWS)
    path = tmp_path / "data.db"

    db.init_db(str(csv_path), str(path))

    assert db.employee_rows(path) == len(_ROWS)


def test_employee_rows_counts_a_loaded_database(db_path):
    assert db.employee_rows(db_path) == len(_ROWS)


def test_employee_rows_is_zero_for_a_database_that_was_never_loaded(tmp_path):
    empty = tmp_path / "empty.db"
    empty.touch()
    assert db.employee_rows(tmp_path / "missing.db") == 0
    assert db.employee_rows(empty) == 0


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM employees", f"SELECT * FROM {_SCOPED} AS employees"),
        (
            "SELECT employees.name FROM employees",
            f"SELECT employees.name FROM {_SCOPED} AS employees",
        ),
        ("SELECT e.name FROM employees e", f"SELECT e.name FROM {_SCOPED} AS e"),
        ('SELECT * FROM "employees"', f"SELECT * FROM {_SCOPED} AS employees"),
        ("SELECT * FROM EMPLOYEES", f"SELECT * FROM {_SCOPED} AS EMPLOYEES"),
        ("SELECT * FROM employees AS sqlite_master", f"SELECT * FROM {_SCOPED} AS sqlite_master"),
        (
            "SELECT e.name, m.name FROM employees e JOIN employees m ON e.user_id = m.user_id",
            f"SELECT e.name, m.name FROM {_SCOPED} AS e JOIN {_SCOPED} AS m "
            "ON e.user_id = m.user_id",
        ),
        (_CTE_SQL, _CTE_EXECUTED),
        (_NESTED_SQL, _NESTED_EXECUTED),
        (_CORRELATED_SQL, _CORRELATED_EXECUTED),
    ],
)
def test_every_employees_reference_becomes_a_scoped_subquery(execute, sql, expected):
    """Layer 3: the alias survives, an unaliased reference keeps its name, the tenant is bound."""
    result = execute(sql)
    assert result.executed_sql == expected
    assert result.executed_sql.count("?") == expected.count(_SCOPED)


@pytest.mark.parametrize("tenant", [ACME, BETA, GAMMA])
def test_a_tenant_sees_exactly_its_own_rows(execute, tenant):
    result = execute("SELECT * FROM employees", tenant)
    assert result.returned_count == _TENANT_ROWS[tenant]
    assert {row[_HEADER.index("tenant_id")] for row in result.rows} == {tenant}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM employees WHERE tenant_id = 'beta'",
        "SELECT * FROM employees WHERE tenant_id != 'acme'",
        "SELECT * FROM employees e WHERE e.tenant_id IN ('beta', 'gamma')",
        "SELECT * FROM employees WHERE notes LIKE '%secret%'",
    ],
)
def test_a_cross_tenant_filter_returns_no_rows_and_no_error(execute, sql):
    """The rewrite makes another tenant unreachable, so the honest answer is an empty result."""
    result = execute(sql, ACME)
    assert result.rows == []
    assert (result.total_count, result.returned_count, result.truncated) == (0, 0, False)


def test_a_self_join_binds_the_session_tenant_to_both_references(execute):
    sql = (
        "SELECT a.name, b.name FROM employees a JOIN employees b ON a.department = b.department "
        "WHERE a.user_id < b.user_id"
    )
    result = execute(sql, BETA)
    assert result.executed_sql.count("?") == 2
    assert result.rows == [("Bo", "Bea")]


def test_an_aggregate_covers_the_whole_tenant_and_nothing_else(execute):
    assert execute("SELECT AVG(salary) FROM employees").rows == [(_ACME_SALARY_AVG,)]
    assert execute("SELECT MAX(salary) FROM employees").rows == [(600,)]


def test_a_group_by_on_tenant_id_cannot_see_a_second_tenant(execute):
    result = execute("SELECT tenant_id, COUNT(*) FROM employees GROUP BY tenant_id", BETA)
    assert result.rows == [(BETA, _TENANT_ROWS[BETA])]


def test_the_row_cap_reports_the_full_total_it_truncated(execute, tuned):
    tuned(max_result_rows=3)
    result = execute("SELECT name FROM employees ORDER BY user_id")
    assert (result.returned_count, result.total_count, result.truncated) == (3, 6, True)
    assert result.rows == [("Ada",), ("Alan",), ("Amir",)]


def test_a_result_that_exactly_fills_the_cap_is_not_reported_as_truncated(execute, tuned):
    tuned(max_result_rows=_TENANT_ROWS[ACME])
    result = execute("SELECT name FROM employees")
    assert (result.returned_count, result.total_count, result.truncated) == (6, 6, False)


def test_the_cap_does_not_distort_an_aggregate(execute, tuned):
    """ADR 0007: LIMIT trims output rows, so the engine still averages every scoped row."""
    tuned(max_result_rows=1)
    result = execute("SELECT AVG(salary) FROM employees")
    assert result.rows == [(_ACME_SALARY_AVG,)]
    assert result.truncated is False


def test_a_limit_the_model_wrote_itself_is_honoured_below_the_cap(execute, tuned):
    tuned(max_result_rows=3)
    result = execute("SELECT name FROM employees LIMIT 2")
    assert (result.returned_count, result.total_count, result.truncated) == (2, 2, False)


def test_the_columns_of_a_star_query_are_the_employees_schema(execute):
    assert execute("SELECT * FROM employees").columns == _HEADER


def test_a_query_the_validator_refuses_never_reaches_the_database(execute, db_path):
    with pytest.raises(QueryRejected) as caught:
        execute("SELECT * FROM sqlite_master")
    assert caught.value.retryable is False
    (entry,) = db.audit_entries(db_path)
    assert (entry.verdict, entry.error_kind) == (db.VERDICT_REJECTED, "policy_violation")
    assert entry.executed_sql is None


def test_the_structural_check_refuses_a_query_the_rewrite_skipped(monkeypatch, execute, db_path):
    """Layer 4a: with layer 3 stood down, nothing unscoped is allowed to run at all."""
    monkeypatch.setattr(db, "_scope_to_tenant", lambda select, tenant_id, filters: (select, ()))
    with pytest.raises(SecurityViolation) as caught:
        execute("SELECT * FROM employees")
    assert caught.value.kind == "rewrite_not_applied"
    (entry,) = db.audit_entries(db_path)
    assert (entry.verdict, entry.error_kind) == (db.VERDICT_REJECTED, "rewrite_not_applied")
    assert entry.executed_sql is None


def test_an_interpolated_tenant_filter_is_refused_even_though_it_scopes_correctly(
    monkeypatch, execute
):
    """Layer 4a demands a bound parameter: a literal that happens to be right is still refused."""

    def interpolate(select, tenant_id, filters):
        sql = f"SELECT * FROM employees WHERE employees.tenant_id = '{tenant_id}'"
        return sqlglot.parse_one(sql, dialect="sqlite"), ()

    monkeypatch.setattr(db, "_scope_to_tenant", interpolate)
    with pytest.raises(SecurityViolation) as caught:
        execute("SELECT * FROM employees")
    assert caught.value.kind == "rewrite_not_applied"


def test_a_placeholder_in_the_generated_sql_is_refused(monkeypatch, execute):
    """An unbound ? would shift which value the engine binds where, so the counts must agree.

    Layer 2 rejects a placeholder first as a retryable honest error (issue #45); standing that
    one rule down leaves layers 3 and 4a to prove the structural backstop still fails closed.
    """
    monkeypatch.setattr(
        db, "validate_sql", lambda sql, **_: sqlglot.parse_one(sql, dialect="sqlite")
    )
    with pytest.raises(SecurityViolation) as caught:
        execute("SELECT * FROM employees WHERE name = ?")
    assert caught.value.kind == "rewrite_not_applied"


def test_a_placeholder_is_refused_as_retryable_before_it_reaches_the_backstop(execute):
    """Layer 2 turns the model's own ? into a retry it can fix, not a terminal refusal."""
    with pytest.raises(QueryRejected) as caught:
        execute("SELECT * FROM employees WHERE name = ?")
    assert caught.value.retryable is True
    assert "literal values inline" in caught.value.reason


def test_the_structural_check_refuses_a_rewrite_bound_to_another_tenant(monkeypatch, execute):
    scope = db._scope_to_tenant
    monkeypatch.setattr(
        db, "_scope_to_tenant", lambda select, tenant_id, filters: scope(select, BETA, filters)
    )
    with pytest.raises(SecurityViolation) as caught:
        execute("SELECT * FROM employees", ACME)
    assert caught.value.kind == "rewrite_not_applied"


def test_the_egress_check_refuses_rows_of_another_tenant(monkeypatch, execute, db_path):
    """Layer 4b: with layers 3 and 4a stood down, a foreign row still never reaches the caller."""
    scope = db._scope_to_tenant
    monkeypatch.setattr(
        db, "_scope_to_tenant", lambda select, tenant_id, filters: scope(select, BETA, filters)
    )
    _stand_down_structural_check(monkeypatch)
    with pytest.raises(SecurityViolation) as caught:
        execute("SELECT * FROM employees", ACME)
    assert caught.value.kind == "egress_row_mismatch"
    assert BETA in str(caught.value)
    (entry,) = db.audit_entries(db_path)
    assert (entry.verdict, entry.error_kind) == (db.VERDICT_REJECTED, "egress_row_mismatch")
    assert entry.executed_sql is not None


def test_the_egress_check_reads_every_tenant_id_column_a_join_exposes(monkeypatch, execute):
    scope = db._scope_to_tenant

    def scope_second_reference_to_beta(select, tenant_id, filters):
        scoped, params = scope(select, ACME, filters)
        assert params == (ACME, ACME)
        return scoped, (ACME, BETA)

    monkeypatch.setattr(db, "_scope_to_tenant", scope_second_reference_to_beta)
    _stand_down_structural_check(monkeypatch)
    sql = "SELECT a.*, b.* FROM employees a JOIN employees b ON a.department = b.department"
    with pytest.raises(SecurityViolation) as caught:
        execute(sql, ACME)
    assert caught.value.kind == "egress_row_mismatch"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sqlite_master",
        "SELECT name FROM employees UNION ALL SELECT sql FROM sqlite_master",
        "INSERT INTO employees (user_id, tenant_id) VALUES (99, 'acme')",
        "UPDATE employees SET salary = 0",
        "PRAGMA query_only = OFF",
        "ATTACH DATABASE 'other.db' AS other",
    ],
)
def test_the_authorizer_denies_what_the_layers_above_it_would_have_caught(
    monkeypatch, execute, db_path, sql
):
    """Layer 2.5: with layers 2, 3 and 4a stood down, the engine itself refuses the statement."""
    _stand_down_validation(monkeypatch)
    with pytest.raises(SecurityViolation) as caught:
        execute(sql)
    assert caught.value.kind == "authorizer_denied"
    (entry,) = db.audit_entries(db_path)
    assert (entry.verdict, entry.error_kind) == (db.VERDICT_REJECTED, "authorizer_denied")


def test_the_authorizer_denies_a_function_the_validator_forbids(monkeypatch, execute):
    _stand_down_validation(monkeypatch)
    with pytest.raises(SecurityViolation) as caught:
        execute("SELECT sqlite_compileoption_used('X') FROM employees")
    assert caught.value.kind == "authorizer_denied"


def test_the_connection_refuses_a_write_even_with_the_authorizer_widened(monkeypatch, execute):
    """mode=ro plus PRAGMA query_only, proven with layer 2.5's allowlist opened to everything."""
    _stand_down_validation(monkeypatch)
    monkeypatch.setattr(db, "_ALLOWED_ACTIONS", frozenset(range(40)))
    with pytest.raises(QueryRejected) as caught:
        execute("INSERT INTO employees (user_id, tenant_id) VALUES (99, 'acme')")
    assert "readonly" in caught.value.reason


def test_the_query_deadline_aborts_a_runaway_recursive_cte(execute, db_path, tuned):
    tuned(query_timeout_ms=200)
    with pytest.raises(QueryRejected) as caught:
        execute(_RUNAWAY_SQL)
    assert caught.value.retryable is False
    assert "budget" in caught.value.reason
    (entry,) = db.audit_entries(db_path)
    assert (entry.verdict, entry.error_kind) == (db.VERDICT_ERRORED, "timeout")


def test_an_engine_limit_refuses_an_oversized_statement(execute, tuned):
    tuned(max_sql_length=60)
    with pytest.raises(QueryRejected) as caught:
        execute("SELECT name, salary, department FROM employees WHERE salary > 100")
    assert caught.value.retryable is True


def test_an_engine_limit_refuses_an_oversized_like_pattern(execute, tuned):
    tuned(max_like_pattern_length=5)
    with pytest.raises(QueryRejected):
        execute("SELECT name FROM employees WHERE notes LIKE '%a very long pattern%'")


def test_a_query_the_engine_rejects_is_an_honest_retryable_error(execute, db_path):
    with pytest.raises(QueryRejected) as caught:
        execute("SELECT nosuchcolumn FROM employees")
    assert caught.value.retryable is True
    (entry,) = db.audit_entries(db_path)
    assert (entry.verdict, entry.error_kind) == (db.VERDICT_ERRORED, "sqlite_error")


@pytest.mark.parametrize(
    ("sql", "verdict", "error_kind"),
    [
        ("SELECT * FROM employees", db.VERDICT_APPROVED, None),
        ("DROP TABLE employees", db.VERDICT_REJECTED, "policy_violation"),
        ("here is your query", db.VERDICT_REJECTED, "malformed_sql"),
        ("SELECT nosuchcolumn FROM employees", db.VERDICT_ERRORED, "sqlite_error"),
    ],
)
def test_every_call_is_audited_with_its_verdict(execute, db_path, sql, verdict, error_kind):
    with contextlib.suppress(QueryRejected):
        execute(sql)
    (entry,) = db.audit_entries(db_path)
    assert (entry.verdict, entry.error_kind) == (verdict, error_kind)
    assert (entry.tenant, entry.generated_sql) == (ACME, sql)
    assert entry.ts == _AUDIT_TIME.isoformat()


def test_an_approved_call_audits_the_executed_sql_and_row_count(execute, db_path):
    result = execute("SELECT name FROM employees")
    (entry,) = db.audit_entries(db_path)
    assert entry.executed_sql == result.executed_sql
    assert entry.rowcount == result.returned_count == _TENANT_ROWS[ACME]


def test_the_audit_log_keeps_one_row_per_call_in_order(execute, db_path):
    execute("SELECT * FROM employees")
    with pytest.raises(QueryRejected):
        execute("DROP TABLE employees")
    execute("SELECT COUNT(*) FROM employees", BETA)
    entries = db.audit_entries(db_path)
    assert [entry.verdict for entry in entries] == [
        db.VERDICT_APPROVED,
        db.VERDICT_REJECTED,
        db.VERDICT_APPROVED,
    ]
    assert [entry.tenant for entry in entries] == [ACME, ACME, BETA]


def test_the_audit_window_reads_the_newest_rows_first_with_the_log_total(execute, db_path):
    """What the Audit tab reads: a window from the head of the log, and the log's own count."""
    execute("SELECT * FROM employees")
    execute("SELECT COUNT(*) FROM employees", BETA)
    with pytest.raises(QueryRejected):
        execute("DROP TABLE employees")

    window = db.audit_window(limit=2, offset=0, db_path=db_path)

    assert window.total == 3
    assert [entry.verdict for entry in window.entries] == [
        db.VERDICT_REJECTED,
        db.VERDICT_APPROVED,
    ]
    assert [entry.tenant for entry in window.entries] == [ACME, BETA]


def test_a_later_audit_window_continues_from_where_the_first_stopped(execute, db_path):
    """Offset paging over the log's own identity, so no row repeats and none is skipped."""
    for _ in range(3):
        execute("SELECT * FROM employees")

    first = db.audit_window(limit=2, offset=0, db_path=db_path)
    second = db.audit_window(limit=2, offset=2, db_path=db_path)

    assert [entry.id for entry in first.entries] == [3, 2]
    assert [entry.id for entry in second.entries] == [1]
    assert second.total == first.total == 3


def test_an_audit_row_carries_the_statements_and_never_a_result_row(execute, db_path):
    """The security property of serving this store: what it holds is SQL and metadata."""
    execute("SELECT name FROM employees")

    (entry,) = db.audit_window(limit=1, offset=0, db_path=db_path).entries

    assert [field.name for field in fields(entry)] == [
        "id",
        "ts",
        "tenant",
        "generated_sql",
        "verdict",
        "executed_sql",
        "rowcount",
        "error_kind",
    ]


def test_an_unexpected_failure_is_still_audited_as_unexplained(monkeypatch, execute, db_path):
    """The audit row is written in a finally and starts unexplained, so no path can skip it."""

    def explode(*args):
        raise RuntimeError("engine on fire")

    monkeypatch.setattr(db, "_run", explode)
    with pytest.raises(RuntimeError):
        execute("SELECT * FROM employees")
    (entry,) = db.audit_entries(db_path)
    assert (entry.verdict, entry.error_kind) == (db.VERDICT_ERRORED, "unexpected_error")
    assert entry.executed_sql is not None


def test_the_audit_store_is_recreated_if_it_goes_missing(execute, db_path):
    (db_path.parent / db.AUDIT_DB_NAME).unlink()
    execute("SELECT * FROM employees")
    assert len(db.audit_entries(db_path)) == 1


def test_query_result_carries_the_binding_contract(execute):
    result = execute("SELECT * FROM employees")
    assert [field.name for field in fields(result)] == [
        "columns",
        "rows",
        "total_count",
        "returned_count",
        "truncated",
        "executed_sql",
    ]


def test_security_violation_carries_the_binding_contract():
    violation = SecurityViolation("nope", kind="egress_row_mismatch")
    assert isinstance(violation, Exception)
    assert (violation.reason, violation.kind) == ("nope", "egress_row_mismatch")
    assert "nope" in str(violation)


def _python_sources():
    """Every committed Python file in the repo, skipping virtualenvs and caches."""
    for path in sorted(_REPO_ROOT.rglob("*.py")):
        parts = path.relative_to(_REPO_ROOT).parts
        if any(part.startswith(".") or part == "__pycache__" for part in parts):
            continue
        yield path


def test_only_the_owning_modules_open_a_database_connection():
    """CLAUDE.md hard rule: db.py owns data access. The one documented exception is
    conversations.py's own app-state store, state.db, which holds no tenant rows - so it and
    its test may connect. Anything else doing so would be a second path to the data."""
    scanned = list(_python_sources())
    assert Path(db.__file__) in scanned, "the sweep must reach the module it is guarding"
    for path in scanned:
        if path.name in _CONNECTION_OWNERS:
            continue
        source = path.read_text()
        assert _CONNECT_CALL not in source, path
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        assert _SQLITE_MODULE not in imported, path


def test_the_unscoped_browse_read_returns_every_tenants_rows(db_path):
    """The point of the exception: the auditor listings show the dataset, not one tenant of it.

    Asserted beside `execute_scoped` on the identical statement, so what the pair proves is that
    the difference between them is the scoping and nothing else (ADR 0014 as rewritten).
    """
    sql = "SELECT tenant_id FROM employees"

    everyone = db.execute_unscoped_browse(sql, ACME, db_path=db_path, clock=_frozen_clock)
    mine = db.execute_scoped(sql, ACME, db_path=db_path, clock=_frozen_clock)

    assert {row[0] for row in everyone.rows} == {ACME, BETA, GAMMA}
    assert everyone.total_count == sum(_TENANT_ROWS.values())
    assert {row[0] for row in mine.rows} == {ACME}


def test_the_unscoped_browse_read_rewrites_nothing_and_binds_no_tenant(db_path):
    """No scoping subquery and no tenant value in the statement: the SQL says what it is."""
    result = db.execute_unscoped_browse(
        "SELECT name FROM employees", ACME, db_path=db_path, clock=_frozen_clock
    )

    assert _SCOPED not in result.executed_sql
    assert "?" not in result.executed_sql
    assert ACME not in result.executed_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sqlite_master",
        "SELECT name FROM employees UNION ALL SELECT sql FROM sqlite_master",
        "INSERT INTO employees (user_id, tenant_id) VALUES (99, 'acme')",
        "UPDATE employees SET salary = 0",
        "PRAGMA query_only = OFF",
        "ATTACH DATABASE 'other.db' AS other",
        "SELECT name FROM employees; SELECT name FROM employees",
    ],
)
def test_the_unscoped_browse_read_still_answers_to_the_validator(db_path, sql):
    """Unscoped is not unvalidated: layer 2's allowlist is exactly the one it always was."""
    with pytest.raises(QueryRejected):
        db.execute_unscoped_browse(sql, ACME, db_path=db_path, clock=_frozen_clock)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sqlite_master",
        "PRAGMA query_only = OFF",
        "ATTACH DATABASE 'other.db' AS other",
    ],
)
def test_the_unscoped_browse_read_still_answers_to_the_engine_authorizer(
    monkeypatch, db_path, sql
):
    """Layer 2.5 too: with the validator stood down, the engine itself refuses the statement."""
    _stand_down_validation(monkeypatch)

    with pytest.raises(SecurityViolation) as caught:
        db.execute_unscoped_browse(sql, ACME, db_path=db_path, clock=_frozen_clock)

    assert caught.value.kind == "authorizer_denied"


def test_the_unscoped_browse_read_still_refuses_a_write_on_a_read_only_file(monkeypatch, db_path):
    """mode=ro is the load-bearing control here as everywhere, proven with 2 and 2.5 widened."""
    _stand_down_validation(monkeypatch)
    monkeypatch.setattr(db, "_ALLOWED_ACTIONS", frozenset(range(40)))

    with pytest.raises(QueryRejected) as caught:
        db.execute_unscoped_browse(
            "INSERT INTO employees (user_id, tenant_id) VALUES (99, 'acme')",
            ACME,
            db_path=db_path,
            clock=_frozen_clock,
        )

    assert "readonly" in caught.value.reason


def test_the_unscoped_browse_read_still_caps_its_rows_and_says_it_truncated(db_path, tuned):
    """ADR 0007 is untouched: the cap trims the output and the true total is still reported."""
    tuned(max_result_rows=3)

    result = db.execute_unscoped_browse(
        "SELECT name FROM employees", ACME, db_path=db_path, clock=_frozen_clock
    )

    assert result.returned_count == 3
    assert result.total_count == sum(_TENANT_ROWS.values())
    assert result.truncated is True


def test_the_unscoped_browse_read_still_answers_to_the_query_deadline(db_path, tuned):
    tuned(query_timeout_ms=200)

    with pytest.raises(QueryRejected) as caught:
        db.execute_unscoped_browse(_RUNAWAY_SQL, ACME, db_path=db_path, clock=_frozen_clock)

    assert "budget" in caught.value.reason


def test_the_unscoped_browse_read_declares_its_parameters_like_any_template(db_path):
    """A template binds what it declared and nothing else; an undeclared `?` is still refused."""
    bound = db.execute_unscoped_browse(
        "SELECT name FROM employees WHERE salary > ?",
        ACME,
        params=(2500,),
        db_path=db_path,
        clock=_frozen_clock,
    )

    assert [row[0] for row in bound.rows] == ["Ben", "Gil"]
    with pytest.raises(QueryRejected):
        db.execute_unscoped_browse(
            "SELECT name FROM employees WHERE salary > ?",
            ACME,
            db_path=db_path,
            clock=_frozen_clock,
        )


def test_the_unscoped_browse_read_is_audited_under_the_reader_who_asked(db_path):
    """The trail is of data access whoever caused it, and it shows this read carried no scoping."""
    db.execute_unscoped_browse(
        "SELECT name FROM employees", ACME, db_path=db_path, clock=_frozen_clock
    )

    (entry,) = db.audit_entries(db_path)
    assert (entry.verdict, entry.tenant, entry.error_kind) == (db.VERDICT_APPROVED, ACME, None)
    assert entry.ts == _AUDIT_TIME.isoformat()
    assert _SCOPED not in (entry.executed_sql or "")


def test_a_refused_unscoped_browse_read_is_audited_too(db_path):
    with pytest.raises(QueryRejected):
        db.execute_unscoped_browse(
            "SELECT * FROM sqlite_master", ACME, db_path=db_path, clock=_frozen_clock
        )

    (entry,) = db.audit_entries(db_path)
    assert entry.verdict == db.VERDICT_REJECTED


def test_only_the_browse_listings_may_name_the_unscoped_read():
    """The exception is reachable from one module, which is what keeps the agent claim a fact.

    ADR 0014 as rewritten allows exactly one unscoped read, called only by the dataset listings.
    A third module reaching it would be a second unscoped path, and nothing else here would
    notice. The sweep is over the parsed tree rather than the text, so a module may explain the
    exception in a docstring - `app.py` does - without counting as a caller of it.
    """
    scanned = list(_python_sources())
    assert Path(db.__file__) in scanned, "the sweep must reach the module it is guarding"

    named = {path.name for path in scanned if _references(path, _UNSCOPED_CALL)}

    assert named == _UNSCOPED_CALLERS


def _references(path: Path, name: str) -> bool:
    """Whether one module's code - not its prose - holds a name: the def, an import, or a use."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return True
        if isinstance(node, ast.ImportFrom) and any(alias.name == name for alias in node.names):
            return True
    return False


def test_no_agent_tool_is_closed_over_the_unscoped_read(db_path):
    """Proven at the binding rather than in prose: the model's own tool set cannot reach it.

    Every tool the graph is built with is enumerated and every name its code reaches - nested code
    objects and free variables included - is collected. `execute_scoped` is in that set, which is
    what stops this from passing vacuously; the unscoped read is not, so no argument the model
    writes and no injection it obeys has anything to call. No tool description mentions it either,
    so the model is never even told the name.
    """
    tools = agent._build_tools(ACME, _NoEmbedding(), db_path)

    reached = set().union(*(_referenced_names(tool.func) for tool in tools.values()))

    assert "execute_scoped" in reached
    assert _UNSCOPED_CALL not in reached
    assert not any(_UNSCOPED_CALL in (tool.description or "") for tool in tools.values())


def _referenced_names(function) -> set[str]:
    """Every global and free name one function's code reaches, nested code objects included."""
    seen: set[str] = set()
    pending = [function.__code__]
    while pending:
        code = pending.pop()
        seen.update(code.co_names)
        seen.update(code.co_freevars)
        pending.extend(const for const in code.co_consts if isinstance(const, CodeType))
    return seen


class _NoEmbedding:
    """An embedder the tool set can be built over: building a tool never runs it."""

    def __call__(self, texts):
        """Never reached, and loud if it ever is."""
        raise AssertionError("the tool set was built, not called")
