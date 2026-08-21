"""The tenant-scoped executor: the only module in the repo that opens a database connection.

Every read of tenant data goes through `execute_scoped`, which walks one query through the
inner layers of the defense-in-depth stack (ADRs 0002, 0003, 0007) and audits the outcome
whatever it is. `tenant_id` arrives from the verified JWT (layer 1) and is never read from
the SQL, the model, or the client.

- layer 2, `security.validate_sql`: the sqlglot allowlist, imported, never reimplemented here.
- layer 3, `_scope_to_tenant`: every employees reference in the approved AST becomes
  `(SELECT * FROM employees WHERE employees.tenant_id = ?)`, keeping the reference's alias -
  or, unaliased, taking the table name as its alias, so a qualified `employees.salary`
  elsewhere in the query still resolves. The tenant is bound, never interpolated. Every
  reference binds the same one value, so no reference can be scoped to a different tenant and
  the order in which the engine binds the parameters cannot matter.
- layer 4a, `_verify_scope_applied`: refuses to execute unless the AST about to run carries a
  scoping subquery for every employees reference, one placeholder per subquery, and nothing
  bound but the session tenant. This runs on every call, so the row check below may safely
  degrade to a no-op for a result that has no tenant_id column: the scoping is proven
  structurally rather than assumed. A placeholder the model wrote itself trips this check too,
  because an extra `?` would shift which value the engine binds where.
- layer 2.5, `_connect`: the engine's own controls, which do not depend on any parse of ours -
  the file opened `mode=ro`, `PRAGMA query_only`, `sqlite3_limit` caps and an authorizer that
  allows nothing but reads of employees. The pragma is set before the authorizer is installed
  because the authorizer denies PRAGMA. `query_only` is reversible by SQL and is therefore the
  second belt, not the load-bearing one - `mode=ro` at file open is (ADR 0002).
- layer 4b, `_verify_rows`: any tenant_id in the output columns must equal the session tenant.

DoS controls sit on the same connection: a progress handler aborts the query once the
`db.query_timeout_ms` deadline passes, and the deadline covers the whole call rather than each
statement, so a second statement cannot buy a second budget. The row cap
(`db.max_result_rows`) is enforced by fetching no more than the cap; the total is a COUNT over
the same scoped query, paid only when the cap actually trips. `LIMIT` trims output rows only,
so an aggregate is always computed over all of the tenant's rows (ADR 0007); the truncation
message the user sees is composed by the tool layer from these fields.

Audit rows are written in a `finally` for every call - approved, rejected, errored, or
crashed - to `audit.db` beside the data file, the one writable connection here. An attempt
starts recorded as an unexplained failure and each classified outcome overwrites that, so a
path nobody anticipated still leaves a row. A failure to audit is raised, never swallowed:
data read without a trace is the fault we are protecting against.

The caller's error surface stays two exceptions: `QueryRejected` from layer 2 (reused, with
its retryable flag - a query the engine itself refuses is an honest, retryable error, while a
timeout is terminal) and `SecurityViolation` when an inner layer trips, which the agent never
retries (ADR 0011).
"""

import csv
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlglot import exp

from runtime import DbConfig, runtime
from security import ALLOWED_TABLE, FORBIDDEN_FUNCTIONS, QueryRejected, validate_sql

TENANT_COLUMN = "tenant_id"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "employees.db"
AUDIT_DB_NAME = "audit.db"

VERDICT_APPROVED = "approved"
VERDICT_REJECTED = "rejected"
VERDICT_ERRORED = "errored"

_DIALECT = "sqlite"
_COLUMNS = (
    "user_id",
    TENANT_COLUMN,
    "name",
    "department",
    "salary",
    "performance_score",
    "hire_date",
    "notes",
)
_SCHEMA = (
    f"CREATE TABLE {ALLOWED_TABLE} ("
    "user_id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, "
    "department TEXT NOT NULL, salary INTEGER NOT NULL, performance_score REAL NOT NULL, "
    "hire_date TEXT NOT NULL, notes TEXT NOT NULL)"
)
_INDEX = f"CREATE INDEX {ALLOWED_TABLE}_tenant ON {ALLOWED_TABLE} ({TENANT_COLUMN})"
_INSERT = (
    f"INSERT INTO {ALLOWED_TABLE} ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join(['?'] * len(_COLUMNS))})"
)

_AUDIT_COLUMNS = (
    "ts",
    "tenant",
    "generated_sql",
    "verdict",
    "executed_sql",
    "rowcount",
    "error_kind",
)
_AUDIT_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS audit_log ("
    "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, tenant TEXT NOT NULL, "
    "generated_sql TEXT NOT NULL, verdict TEXT NOT NULL, executed_sql TEXT, "
    "rowcount INTEGER, error_kind TEXT)"
)
_AUDIT_INSERT = (
    f"INSERT INTO audit_log ({', '.join(_AUDIT_COLUMNS)}) "
    f"VALUES ({', '.join(['?'] * len(_AUDIT_COLUMNS))})"
)
_AUDIT_SELECT = f"SELECT {', '.join(_AUDIT_COLUMNS)} FROM audit_log ORDER BY id"

_QUERY_ONLY = "PRAGMA query_only = ON"
_COUNT_ALIAS = "scoped"
_UNEXPLAINED = "unexpected_error"
_MS_PER_SECOND = 1000
# VM instructions between deadline checks: the cost of asking the clock, not a policy value.
_PROGRESS_INSTRUCTIONS = 1000
# The authorizer sees these while running an approved SELECT; every other action is denied.
_ALLOWED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)
_NO_ATTACHED_DATABASES = 0

# The one subquery layer 3 emits; layer 4a recognises a scoped source by this exact rendering.
_SCOPED_SELECT = (
    exp.select(exp.Star())
    .from_(exp.table_(ALLOWED_TABLE))
    .where(exp.column(TENANT_COLUMN, table=ALLOWED_TABLE).eq(exp.Placeholder()))
)
_SCOPED_SELECT_SQL = _SCOPED_SELECT.sql(dialect=_DIALECT)


class SecurityViolation(Exception):
    """An inner RLS layer tripped: terminal, never retried, audited under kind."""

    def __init__(self, reason: str, kind: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


class _EngineFailure(Exception):
    """An engine-level failure, carrying how the audit should name it."""

    def __init__(self, reason: str, kind: str, retryable: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind
        self.retryable = retryable


@dataclass(frozen=True)
class QueryResult:
    """One approved query's rows, with the truncation facts the tool layer reports (ADR 0007)."""

    columns: tuple[str, ...]
    rows: list[tuple[object, ...]]
    total_count: int
    returned_count: int
    truncated: bool
    executed_sql: str


@dataclass(frozen=True)
class AuditEntry:
    """One persisted audit_log row: the trace of a single execute_scoped call."""

    ts: str
    tenant: str
    generated_sql: str
    verdict: str
    executed_sql: str | None
    rowcount: int | None
    error_kind: str | None


@dataclass
class _Attempt:
    """The audit row being assembled for one call; unexplained until an outcome overwrites it."""

    generated_sql: str
    tenant: str
    verdict: str = VERDICT_ERRORED
    error_kind: str | None = _UNEXPLAINED
    executed_sql: str | None = None
    rowcount: int | None = None

    def approve(self, rowcount: int) -> None:
        """Record a query that ran and passed every check."""
        self.verdict, self.error_kind, self.rowcount = VERDICT_APPROVED, None, rowcount

    def reject(self, error_kind: str) -> None:
        """Record a query a policy layer refused."""
        self.verdict, self.error_kind = VERDICT_REJECTED, error_kind

    def fail(self, error_kind: str) -> None:
        """Record a query the engine could not complete."""
        self.verdict, self.error_kind = VERDICT_ERRORED, error_kind


def _utc_now() -> datetime:
    """The default audit clock; a caller may pin its own."""
    return datetime.now(UTC)


def init_db(csv_path: Path, db_path: Path) -> None:
    """Load csv_path into a fresh employees table at db_path and open its audit store."""
    rows = _read_csv(csv_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(f"DROP TABLE IF EXISTS {ALLOWED_TABLE}")
        conn.execute(_SCHEMA)
        conn.execute(_INDEX)
        conn.executemany(_INSERT, rows)
    _open_audit(_audit_path(db_path)).close()


def execute_scoped(
    sql: str,
    tenant_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    clock: Callable[[], datetime] = _utc_now,
) -> QueryResult:
    """Validate, scope, run and audit sql for tenant_id; the one path from a query to the data."""
    attempt = _Attempt(generated_sql=sql, tenant=tenant_id)
    try:
        scoped, params = _scope_to_tenant(validate_sql(sql), tenant_id)
        _verify_scope_applied(scoped, params, tenant_id)
        attempt.executed_sql = scoped.sql(dialect=_DIALECT)
        result = _run(db_path, attempt.executed_sql, _count_sql(scoped), params)
        _verify_rows(result, tenant_id)
        attempt.approve(result.returned_count)
        return result
    except QueryRejected as rejected:
        attempt.reject("malformed_sql" if rejected.retryable else "policy_violation")
        raise
    except SecurityViolation as violation:
        attempt.reject(violation.kind)
        raise
    except _EngineFailure as failure:
        attempt.fail(failure.kind)
        raise QueryRejected(failure.reason, retryable=failure.retryable) from failure
    finally:
        _write_audit(_audit_path(db_path), attempt, clock)


def audit_entries(db_path: Path = DEFAULT_DB_PATH) -> list[AuditEntry]:
    """Every audit row for db_path's store, oldest first: the trace source for the UI and evals."""
    with closing(_open_audit(_audit_path(db_path))) as conn:
        return [AuditEntry(*row) for row in conn.execute(_AUDIT_SELECT)]


def _read_csv(csv_path: Path) -> list[tuple[str, ...]]:
    """Read the dataset rows, refusing a header that is not the schema this module creates."""
    with csv_path.open(newline="") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader))
        if header != _COLUMNS:
            raise ValueError(f"CSV header is not the employees schema: {header}")
        # The declared column affinities convert the numeric strings on insert.
        return [tuple(row) for row in reader]


def _scope_to_tenant(
    select: exp.Expression, tenant_id: str
) -> tuple[exp.Expression, tuple[str, ...]]:
    """Rewrite every employees reference into a tenant-filtered subquery with a bound parameter."""
    substitutions = 0

    def rewrite(node: exp.Expression) -> exp.Expression:
        nonlocal substitutions
        if isinstance(node, exp.Table) and node.name.lower() == ALLOWED_TABLE:
            substitutions += 1
            return _scoped_source(node)
        return node

    # Layer 2 refuses a CTE that shadows employees, so every such reference is the real table.
    return select.transform(rewrite, copy=True), (tenant_id,) * substitutions


def _scoped_source(table: exp.Table) -> exp.Subquery:
    """The tenant-filtered stand-in for one employees reference, keeping its name in scope."""
    return exp.Subquery(
        this=_SCOPED_SELECT.copy(),
        alias=exp.TableAlias(this=exp.to_identifier(table.alias or table.name)),
    )


def _verify_scope_applied(
    scoped: exp.Expression, params: tuple[str, ...], tenant_id: str
) -> None:
    """Refuse to run anything but a tree whose every employees reference is tenant-scoped."""
    sources = [node for node in scoped.find_all(exp.Subquery) if _is_scoping_source(node)]
    tables = [node for node in scoped.find_all(exp.Table) if node.name.lower() == ALLOWED_TABLE]
    placeholders = list(scoped.find_all(exp.Placeholder))
    counts_agree = bool(sources) and len(tables) == len(sources) == len(placeholders)
    if not counts_agree or params != (tenant_id,) * len(sources):
        raise SecurityViolation(
            f"tenant scoping did not apply: {len(tables)} employees references, "
            f"{len(sources)} scoped, {len(placeholders)} placeholders, {len(params)} bound",
            kind="rewrite_not_applied",
        )


def _is_scoping_source(node: exp.Subquery) -> bool:
    """Whether node is exactly the subquery this module emits, and nothing that resembles it."""
    return node.this.sql(dialect=_DIALECT) == _SCOPED_SELECT_SQL


def _count_sql(scoped: exp.Expression) -> str:
    """Render the total-rows COUNT over the scoped query, built from the AST rather than text."""
    wrapper = exp.select(exp.func("COUNT", exp.Star())).from_(
        exp.Subquery(this=scoped.copy(), alias=exp.TableAlias(this=exp.to_identifier(_COUNT_ALIAS)))
    )
    return wrapper.sql(dialect=_DIALECT)


def _run(db_path: Path, sql: str, count_sql: str, params: tuple[str, ...]) -> QueryResult:
    """Execute the scoped query behind the engine's own controls, naming any engine failure."""
    config = runtime().db
    guard = _EngineGuard(config)
    try:
        with closing(_connect(db_path, guard, config)) as conn:
            return _fetch(conn, sql, count_sql, params, config.max_result_rows)
    except sqlite3.Error as error:
        raise guard.explain(error) from error


def _connect(db_path: Path, guard: "_EngineGuard", config: DbConfig) -> sqlite3.Connection:
    """Open the data file read-only with every engine-level control installed (layer 2.5)."""
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.execute(_QUERY_ONLY)
    for category, value in _limit_caps(config):
        conn.setlimit(category, value)
    conn.set_authorizer(guard.authorize)
    conn.set_progress_handler(guard.interrupt, _PROGRESS_INSTRUCTIONS)
    return conn


def _limit_caps(config: DbConfig) -> tuple[tuple[int, int], ...]:
    """The sqlite3_limit caps for running untrusted SQL (ADR 0002 hardening)."""
    return (
        (sqlite3.SQLITE_LIMIT_SQL_LENGTH, config.max_sql_length),
        (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, config.max_expr_depth),
        (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, config.max_compound_select),
        (sqlite3.SQLITE_LIMIT_VDBE_OP, config.max_vdbe_ops),
        (sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, config.max_like_pattern_length),
        (sqlite3.SQLITE_LIMIT_ATTACHED, _NO_ATTACHED_DATABASES),
    )


def _fetch(
    conn: sqlite3.Connection, sql: str, count_sql: str, params: tuple[str, ...], cap: int
) -> QueryResult:
    """Run the scoped query, cap the rows, and count the total only when the cap trips."""
    cursor = conn.execute(sql, params)
    columns = tuple(column[0] for column in cursor.description)
    rows = cursor.fetchmany(cap)
    total = len(rows) if len(rows) < cap else conn.execute(count_sql, params).fetchone()[0]
    return QueryResult(
        columns=columns,
        rows=rows,
        total_count=total,
        returned_count=len(rows),
        truncated=total > len(rows),
        executed_sql=sql,
    )


def _verify_rows(result: QueryResult, tenant_id: str) -> None:
    """Refuse a result whose own tenant_id columns disagree with the session tenant (layer 4b)."""
    indexes = [
        index for index, name in enumerate(result.columns) if name.lower() == TENANT_COLUMN
    ]
    for row in result.rows:
        for index in indexes:
            if row[index] != tenant_id:
                raise SecurityViolation(
                    f"a result row carries tenant {row[index]!r}, not {tenant_id!r}",
                    kind="egress_row_mismatch",
                )


class _EngineGuard:
    """Layer 2.5 inside the engine: the authorizer allowlist and the query deadline."""

    def __init__(self, config: DbConfig) -> None:
        self.timeout_ms = config.query_timeout_ms
        self.denied: str | None = None
        self.expired = False
        self._deadline = time.monotonic() + config.query_timeout_ms / _MS_PER_SECOND

    def authorize(
        self,
        action: int,
        first: str | None,
        second: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
        """Allow reads of employees and safe functions; deny every other operation asked about."""
        # Keyed on the table alone: the engine reports database as None inside a recursive CTE.
        if action == sqlite3.SQLITE_READ and (first or "").lower() != ALLOWED_TABLE:
            return self._deny(f"a read of {first}.{second}")
        if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() in FORBIDDEN_FUNCTIONS:
            return self._deny(f"a call to {second}")
        if action in _ALLOWED_ACTIONS:
            return sqlite3.SQLITE_OK
        return self._deny(f"authorizer action {action}")

    def interrupt(self) -> int:
        """Progress handler: abort the statement once the query deadline has passed."""
        if time.monotonic() <= self._deadline:
            return 0
        self.expired = True
        return 1

    def explain(self, error: sqlite3.Error) -> Exception:
        """Name what the engine refused: this guard's own verdict where it has one."""
        if self.denied is not None:
            return SecurityViolation(
                f"the engine denied {self.denied}", kind="authorizer_denied"
            )
        if self.expired:
            return _EngineFailure(
                f"the query exceeded its {self.timeout_ms} ms budget", "timeout", retryable=False
            )
        return _EngineFailure(
            f"the database refused the query: {error}", "sqlite_error", retryable=True
        )

    def _deny(self, description: str) -> int:
        """Record the first denial, so a refusal can be reported as the security event it is."""
        self.denied = self.denied or description
        return sqlite3.SQLITE_DENY


def _audit_path(db_path: Path) -> Path:
    """The audit store beside the data file: a separate, writable database (ADR 0002)."""
    return db_path.with_name(AUDIT_DB_NAME)


def _open_audit(audit_path: Path) -> sqlite3.Connection:
    """Open the writable audit store, creating its schema if this is the first write."""
    conn = sqlite3.connect(audit_path)
    with conn:
        conn.execute(_AUDIT_SCHEMA)
    return conn


def _write_audit(audit_path: Path, attempt: _Attempt, clock: Callable[[], datetime]) -> None:
    """Persist one audit row; a failure here is loud on purpose, as untraced access is the fault."""
    with closing(_open_audit(audit_path)) as conn, conn:
        conn.execute(
            _AUDIT_INSERT,
            (
                clock().isoformat(),
                attempt.tenant,
                attempt.generated_sql,
                attempt.verdict,
                attempt.executed_sql,
                attempt.rowcount,
                attempt.error_kind,
            ),
        )
