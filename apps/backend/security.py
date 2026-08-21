"""Layer 2 of the defense-in-depth RLS stack: the SQL allowlist validator (ADR 0002).

A pure function of the SQL string. It parses, judges, and returns the approved AST; it opens
no connection, reads no configuration and keeps no state, so one input always yields one
verdict and the whole layer is testable without a database or a model.

The allowlist, applied to model-generated SQL parsed in the sqlite dialect:

- exactly one statement whose root is a SELECT (a trailing semicolon or comment leaves no
  statement; UNION/INTERSECT/EXCEPT roots are not a SELECT and are refused, while the same
  set operation wrapped in a subquery is allowed, since layer 3 rewrites a SELECT root and
  the table allowlist below applies inside a set operation just the same)
- no non-SELECT statement node anywhere in the tree, which narrows the parser-differential
  gap a nested command node would open
- every table reference resolves, case-insensitively, to employees or to a CTE name visible
  in that reference's own scope; schema-qualified names and table functions are refused
- at least one reference to employees, so an approved query always reads the scoped table
- no forbidden function by name: loading extensions or reaching the filesystem

CTE shadowing: a CTE named employees is refused outright, even with an innocuous body.
Layer 3 rewrites every employees reference into a tenant-filtered subquery, so both layers
must agree on what the name denotes; under a shadow they do not - references resolve to the
CTE, and the rewrite would either change the query's meaning or leave a reference unscoped.
Shadowing the one readable table also buys a legitimate query nothing, while handing an
evasion an allowlisted name to hide behind. Fail closed.

CTE names are resolved per scope rather than collected tree-wide, so a CTE named notes in
one subquery cannot whitelist the real notes table read in a sibling scope.

Retry semantics (ADR 0011): a policy violation is terminal, because retrying would let the
agent probe the boundary. SQL that does not parse, or that parses to an expression fragment
instead of a statement (a fenced answer, a bare word), is an honest error worth a retry.
Nesting deep enough to exhaust the stack is refused as a violation rather than raised as a
crash, so hostile input always leaves this layer as an explicit verdict.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ALLOWED_TABLE = "employees"

# Dangerous when running untrusted SQL, per the checklist on sqlite.org/security.html.
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "load_extension",
        "readfile",
        "writefile",
        "edit",
        "fts3_tokenizer",
        "zipfile",
        "sqlite_dbpage",
        "sqlite_compileoption_get",
        "sqlite_compileoption_used",
    }
)

_DIALECT = "sqlite"

_STATEMENT_NODES = (
    exp.DDL,
    exp.DML,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Set,
)

_NON_SELECT_ROOTS = (*_STATEMENT_NODES, exp.SetOperation)


class QueryRejected(Exception):
    """A refused query: reason is the audit-log text, retryable marks an honest error."""

    def __init__(self, reason: str, retryable: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def validate_sql(sql: str) -> exp.Select:
    """Return the approved SELECT AST for sql, or raise QueryRejected."""
    try:
        return _validate(sql)
    except RecursionError as error:
        raise QueryRejected("query is nested too deeply to validate", retryable=False) from error


def _validate(sql: str) -> exp.Select:
    """Apply the allowlist to sql and return the approved SELECT AST."""
    statements = _parse(sql)
    if not statements:
        raise QueryRejected("no SQL statement found", retryable=True)
    if len(statements) > 1:
        raise QueryRejected(
            f"only one statement is allowed, got {len(statements)}", retryable=False
        )

    root = statements[0]
    if not isinstance(root, exp.Select):
        if isinstance(root, _NON_SELECT_ROOTS):
            raise QueryRejected(f"only SELECT is allowed, got {root.key.upper()}", retryable=False)
        raise QueryRejected("input is not a SQL statement", retryable=True)

    _reject_nested_statements(root)
    _reject_forbidden_functions(root)
    _check_table_references(root)
    return root


def _parse(sql: str) -> list[exp.Expression]:
    """Parse sql into statements, dropping the empty tail a semicolon or comment leaves."""
    try:
        parsed = sqlglot.parse(sql, dialect=_DIALECT)
    except SqlglotError as error:
        raise QueryRejected(f"SQL did not parse: {error}", retryable=True) from error
    return [
        statement
        for statement in parsed
        if statement is not None and not isinstance(statement, exp.Semicolon)
    ]


def _reject_nested_statements(root: exp.Select) -> None:
    """Refuse a non-SELECT statement node hidden anywhere inside the tree."""
    nested = next(root.find_all(*_STATEMENT_NODES), None)
    if nested is not None:
        raise QueryRejected(
            f"non-SELECT statement inside the query: {nested.key.upper()}", retryable=False
        )


def _reject_forbidden_functions(root: exp.Select) -> None:
    """Refuse calls to functions that reach beyond the query, such as extensions or files."""
    for func in root.find_all(exp.Func):
        for name in (func.name, func.sql_name()):
            if name and name.lower() in FORBIDDEN_FUNCTIONS:
                raise QueryRejected(f"function {name} is not allowed", retryable=False)


def _check_table_references(root: exp.Select) -> None:
    """Refuse any table reference outside the allowlist, and any query that reads no rows."""
    if not _check_scope(root, frozenset()):
        raise QueryRejected(f"query does not read {ALLOWED_TABLE}", retryable=False)


def _check_scope(node: exp.Expression, visible_ctes: frozenset[str]) -> bool:
    """Check this scope's table references; returns whether the subtree reads employees."""
    with_clause = node.args.get("with_")
    if isinstance(with_clause, exp.With):
        aliases = {cte.alias_or_name.lower() for cte in with_clause.expressions}
        if ALLOWED_TABLE in aliases:
            raise QueryRejected(f"a CTE may not shadow the {ALLOWED_TABLE} table", retryable=False)
        visible_ctes = visible_ctes | aliases

    reads_allowed_table = isinstance(node, exp.Table) and _check_table(node, visible_ctes)
    for child in node.iter_expressions():
        reads_allowed_table = _check_scope(child, visible_ctes) or reads_allowed_table
    return reads_allowed_table


def _check_table(table: exp.Table, visible_ctes: frozenset[str]) -> bool:
    """Judge one table reference; returns whether it is the employees table."""
    if not isinstance(table.this, exp.Identifier):
        raise QueryRejected("table functions are not allowed", retryable=False)
    if table.db or table.catalog:
        raise QueryRejected(
            f"schema-qualified table reference is not allowed: {table.name}", retryable=False
        )
    name = table.name.lower()
    if name in visible_ctes:
        return False
    if name != ALLOWED_TABLE:
        raise QueryRejected(
            f"table {table.name} is not allowed; only {ALLOWED_TABLE} may be read",
            retryable=False,
        )
    return True
