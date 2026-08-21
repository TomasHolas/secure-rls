"""Adversarial suite for layer 2, the sqlglot allowlist validator (issue #15, ADR 0002).

Three corpora: queries the analyst agent must be able to run, hostile queries that are
terminal policy violations (ADR 0011: zero retries), and malformed SQL that is an honest
error the agent may retry.
"""

import ast
import sys
from pathlib import Path

import pytest
from sqlglot import exp

from security import QueryRejected, validate_sql

_SECURITY_SOURCE = Path(__file__).resolve().parents[1] / "security.py"

VALID = [
    "SELECT * FROM employees",
    "select name, salary from employees where department = 'Engineering'",
    "SELECT AVG(salary) FROM employees",
    "SELECT department, COUNT(*) FROM employees GROUP BY department "
    "HAVING COUNT(*) > 5 ORDER BY 2 DESC LIMIT 10",
    "SELECT e.name, m.name AS manager FROM employees e JOIN employees m ON e.manager_id = m.id",
    "WITH dept_avg AS (SELECT department, AVG(salary) AS avg_salary FROM employees "
    "GROUP BY department) SELECT e.name, e.salary, d.avg_salary FROM employees e "
    "JOIN dept_avg d ON e.department = d.department WHERE e.salary > d.avg_salary",
    "WITH a AS (SELECT * FROM employees), b AS (SELECT * FROM a) SELECT count(*) FROM b",
    "WITH RECURSIVE chain(id, mgr) AS (SELECT id, manager_id FROM employees "
    "WHERE manager_id IS NULL UNION ALL SELECT e.id, e.manager_id FROM employees e "
    "JOIN chain c ON e.manager_id = c.id) SELECT * FROM chain",
    "SELECT * FROM (SELECT name FROM employees) AS sub",
    "SELECT name, RANK() OVER (PARTITION BY department ORDER BY salary DESC) FROM employees",
    "SELECT CASE WHEN salary > (SELECT AVG(salary) FROM employees) THEN 'high' ELSE 'low' END "
    "AS band FROM employees",
    "SELECT * FROM EMPLOYEES",
    'SELECT * FROM "employees"',
    "SELECT * FROM employees;",
    "SELECT * FROM employees -- trailing comment",
    "SELECT * FROM employees; -- trailing comment after the semicolon",
    "SELECT * FROM employees /* ; DROP TABLE employees */",
    "SELECT * /* inline */ FROM employees",
]

MUTATIONS = [
    "DROP TABLE employees",
    "UPDATE employees SET salary = 999999",
    "INSERT INTO employees (id) VALUES (1)",
    "DELETE FROM employees",
    "CREATE TABLE evil (a int)",
    "CREATE VIEW v AS SELECT * FROM employees",
    "ALTER TABLE employees RENAME TO stolen",
    "REPLACE INTO employees VALUES (1)",
]

MULTI_STATEMENT = [
    "SELECT * FROM employees; DROP TABLE employees",
    "SELECT 1; SELECT 2",
    "SELECT * FROM employees WHERE 1=1; UPDATE employees SET salary = 0",
    "SELECT * FROM employees -- comment\n; DROP TABLE employees",
    "SELECT * FROM eMpLoYeEs; PRAGMA foo",
    "SELECT * FROM employees;;SELECT 1",
    "BEGIN; SELECT * FROM employees; COMMIT",
]

ENGINE_COMMANDS = [
    "PRAGMA table_info(employees)",
    "PRAGMA query_only = false",
    "ATTACH DATABASE '/tmp/evil.db' AS evil",
    "DETACH DATABASE evil",
    "VACUUM",
    "EXPLAIN SELECT * FROM employees",
]

FORBIDDEN_TABLES = [
    "SELECT * FROM sqlite_master",
    "SELECT name FROM sqlite_master WHERE type = 'table'",
    'SELECT * FROM "sqlite_master"',
    "SELECT * FROM employees WHERE id IN (SELECT id FROM sqlite_master)",
    "SELECT (SELECT count(*) FROM sqlite_master) AS n FROM employees",
    "SELECT * FROM employees, sqlite_master",
    "SELECT * FROM /* employees */ sqlite_master",
    "WITH x AS (SELECT * FROM sqlite_master) SELECT * FROM x",
    "SELECT * FROM notes",
    "SELECT * FROM employees WHERE tenant_id = (SELECT tenant_id FROM notes)",
    "SELECT * FROM main.employees",
    "SELECT * FROM other.employees",
    "SELECT * FROM temp.sqlite_master",
    "SELECT * FROM pragma_table_info('employees')",
]

FORBIDDEN_FUNCTIONS = [
    "SELECT load_extension('evil.so')",
    "SELECT * FROM employees WHERE load_extension('evil.so') IS NULL",
    "SELECT LOAD_EXTENSION('evil.so') FROM employees",
    "SELECT/**/load_extension('evil.so') FROM employees",
    "SELECT readfile('/etc/passwd') FROM employees",
    "SELECT writefile('/tmp/x', 'y') FROM employees",
]

NON_SELECT_ROOT = [
    "SELECT * FROM employees UNION SELECT * FROM sqlite_master",
    "SELECT * FROM employees INTERSECT SELECT * FROM sqlite_master",
    "SELECT * FROM employees EXCEPT SELECT * FROM sqlite_master",
]

NO_EMPLOYEES_REFERENCE = [
    "SELECT 1",
    "SELECT sqlite_version()",
]

TERMINAL = (
    MUTATIONS
    + MULTI_STATEMENT
    + ENGINE_COMMANDS
    + FORBIDDEN_TABLES
    + FORBIDDEN_FUNCTIONS
    + NON_SELECT_ROOT
    + NO_EMPLOYEES_REFERENCE
)

MALFORMED = [
    "this is not sql at all !!!",
    "SELECT FROM WHERE",
    "SELEKT * FROM employees",
    "SELECT * FROM employees WHERE (",
    ")))",
    "SELECT * FROM employees WHERE 1=1 /*",
    "```sql\nSELECT * FROM employees\n```",
    "employees",
    "",
    "   ",
    "-- just a comment",
    ";",
]


@pytest.mark.parametrize("sql", VALID)
def test_valid_analyst_queries_are_approved(sql):
    approved = validate_sql(sql)
    assert isinstance(approved, exp.Select)


@pytest.mark.parametrize("sql", TERMINAL)
def test_policy_violations_are_terminal(sql):
    with pytest.raises(QueryRejected) as caught:
        validate_sql(sql)
    assert caught.value.retryable is False
    assert caught.value.reason


@pytest.mark.parametrize("sql", MALFORMED)
def test_malformed_sql_is_retryable(sql):
    with pytest.raises(QueryRejected) as caught:
        validate_sql(sql)
    assert caught.value.retryable is True
    assert caught.value.reason


def test_approved_ast_round_trips_to_equivalent_sql():
    approved = validate_sql("SELECT name FROM employees WHERE salary > 100")
    assert approved.sql(dialect="sqlite") == "SELECT name FROM employees WHERE salary > 100"


def test_cte_and_self_join_together_are_approved():
    sql = (
        "WITH dept_avg AS (SELECT department, AVG(salary) AS avg_salary FROM employees "
        "GROUP BY department) "
        "SELECT e.name, m.name AS manager, d.avg_salary FROM employees e "
        "JOIN employees m ON e.manager_id = m.id "
        "JOIN dept_avg d ON e.department = d.department"
    )
    assert isinstance(validate_sql(sql), exp.Select)


def test_cte_alias_is_allowed_as_a_table_reference():
    sql = "WITH t AS (SELECT * FROM employees) SELECT a.name FROM t a JOIN t b ON a.id = b.id"
    assert isinstance(validate_sql(sql), exp.Select)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH employees AS (SELECT 1 AS id) SELECT * FROM employees",
        "WITH employees AS (SELECT * FROM sqlite_master) SELECT * FROM employees",
        "WITH EMPLOYEES AS (SELECT 1 AS id) SELECT * FROM employees",
        "WITH a AS (SELECT * FROM employees), employees AS (SELECT * FROM a) "
        "SELECT * FROM employees",
    ],
)
def test_cte_shadowing_the_employees_table_is_rejected(sql):
    """A CTE named employees is refused: layers 2 and 3 must agree on what the name denotes."""
    with pytest.raises(QueryRejected) as caught:
        validate_sql(sql)
    assert caught.value.retryable is False
    assert "employees" in caught.value.reason


def test_sibling_scope_cte_does_not_whitelist_a_real_table():
    """A CTE is visible only in its own scope, so it cannot launder a forbidden name."""
    sql = (
        "SELECT (SELECT count(*) FROM (WITH notes AS (SELECT 1 AS n) SELECT n FROM notes)) AS a, "
        "(SELECT count(*) FROM notes) AS b FROM employees"
    )
    with pytest.raises(QueryRejected) as caught:
        validate_sql(sql)
    assert caught.value.retryable is False
    assert "notes" in caught.value.reason


def test_rejection_reason_names_the_offending_table():
    with pytest.raises(QueryRejected) as caught:
        validate_sql("SELECT * FROM sqlite_master")
    assert "sqlite_master" in caught.value.reason


def test_query_rejected_carries_the_binding_contract():
    error = QueryRejected("nope", retryable=True)
    assert isinstance(error, Exception)
    assert error.reason == "nope"
    assert error.retryable is True
    assert "nope" in str(error)


def test_validation_is_a_pure_function_of_the_sql_string():
    sql = "SELECT department, COUNT(*) FROM employees GROUP BY department"
    first = validate_sql(sql)
    second = validate_sql(sql)
    assert first is not second
    assert first.sql(dialect="sqlite") == second.sql(dialect="sqlite")


def test_module_imports_only_sqlglot_and_stdlib():
    roots = set()
    for node in ast.walk(ast.parse(_SECURITY_SOURCE.read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= {"sqlglot"} | set(sys.stdlib_module_names)


def test_module_never_reaches_a_database_or_config():
    text = _SECURITY_SOURCE.read_text()
    assert "sqlite3" not in text
    assert "runtime" not in text
