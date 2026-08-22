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
  bound but the session tenant and whatever filter values the caller declared. This runs on
  every call, so the row check below may safely degrade to a no-op for a result that has no
  tenant_id column: the scoping is proven structurally rather than assumed. A placeholder the
  model wrote itself trips this check too, because an undeclared `?` would shift which value the
  engine binds where.

Declared filter parameters (ADR 0002 as amended). A trusted template - `analytics.py`, `browse.py`
- may bind its own values by passing `params`, so a filter a reader typed travels as a bound
parameter and never as SQL text. Ordering is not assumed: SQL's grammar puts a SELECT's FROM
clause before its WHERE, so the scoping placeholders in the FROM subqueries are bound before the
caller's, and layer 4a proves the arrangement rather than trusting it - the caller's placeholders
must sit inside the root WHERE and nowhere else (never in the projection, which would render
ahead of FROM), and a template that binds anything must have exactly one employees reference, so
"the tenant is the first parameter" is a fact about the rendered statement. `params=()`, the
default, is the model's path: layer 2 then refuses every parameter, exactly as before.
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

The retrieval path (ADR 0010) adds a second, narrow seam, because this module owns every
`sqlite3.connect` in the repo while `rag.py` owns embedding and orchestration:

- `notes_for_indexing` reads every tenant's notes once at load time, over the same read-only
  connection and the same employees-only authorizer as a served query. It is a load-time
  admin read like `init_db`, not a serving path - nothing but `rag.index_notes` calls it.
- `init_vector_store` creates `vectors.db`'s `vec0` table on a writable connection and stamps it
  with the digest of the corpus embedded; `vector_store_rows` and `vector_store_fingerprint`
  read that back over the same writable seam - together, the startup check that makes indexing
  idempotent for an unchanged corpus and re-embedding for a changed one (ADR 0010 as amended).
  None of the three is a serving path.
- `search_vectors` runs the one fixed KNN shape read-only. The vector store is a SEPARATE
  file from the employees data on purpose: the connection that runs model-generated SQL caps
  attached databases at zero and therefore cannot reach the virtual table at all. That matters
  more than tidiness - sqlite-vec 0.1.9 does not check the result of preparing its own
  `_rowids` shadow statement, so an authorizer that DENIES that read aborts the process
  instead of raising (verified empirically; every other denial raises cleanly). Keeping vec0
  out of the untrusted-SQL connection keeps that crash unreachable from a generated query.

`_VectorGuard` is the vector path's authorizer allowlist, sized to what the fixed query
actually asks about. Empirically, on sqlite-vec 0.1.9 a KNN read reports only SQLITE_SELECT,
SQLITE_FUNCTION for `match`, and SQLITE_READ of the virtual table plus its `_rowids`,
`_chunks` and `_auxiliary` shadow tables - the vector, metadata and info chunks are reached
through the blob API, which the authorizer never sees. The allowlist is therefore "this
virtual table and its own shadow tables, nothing else", which stays correct across a version
bump that reads one more shadow table while still denying a read of any other table.
"""

import csv
import sqlite3
import time
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlite_vec
from sqlglot import exp

from runtime import DbConfig, runtime
from security import ALLOWED_TABLE, FORBIDDEN_FUNCTIONS, QueryRejected, validate_sql

TENANT_COLUMN = "tenant_id"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "employees.db"
AUDIT_DB_NAME = "audit.db"
VECTOR_DB_NAME = "vectors.db"
VECTOR_TABLE = "note_vectors"
VECTOR_META_TABLE = "note_index_meta"

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

_NOTES_SELECT = (
    f"SELECT {TENANT_COLUMN}, user_id, name, notes FROM {ALLOWED_TABLE} ORDER BY user_id"
)
# `partition key` shards per tenant; `+` columns are payload vec0 stores but cannot filter on.
_VECTOR_SCHEMA = (
    f"CREATE VIRTUAL TABLE {VECTOR_TABLE} USING vec0("
    f"{TENANT_COLUMN} text partition key, user_id integer, embedding float[{{dim}}], "
    "+name text, +note text)"
)
_VECTOR_INSERT = (
    f"INSERT INTO {VECTOR_TABLE} ({TENANT_COLUMN}, user_id, embedding, name, note) "
    "VALUES (?, ?, ?, ?, ?)"
)
_VECTOR_COUNT = f"SELECT COUNT(*) FROM {VECTOR_TABLE}"
# The corpus digest is stored with the index it was built from, so a stale index is detectable.
_META_SCHEMA = f"CREATE TABLE {VECTOR_META_TABLE} (fingerprint TEXT NOT NULL)"
_META_INSERT = f"INSERT INTO {VECTOR_META_TABLE} (fingerprint) VALUES (?)"
_META_SELECT = f"SELECT fingerprint FROM {VECTOR_META_TABLE}"
# The one retrieval shape: k and the tenant are bound, so the partition pre-filter runs first.
_VECTOR_SEARCH = (
    f"SELECT user_id, {TENANT_COLUMN}, name, note, distance FROM {VECTOR_TABLE} "
    f"WHERE embedding MATCH ? AND k = ? AND {TENANT_COLUMN} = ?"
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

# What the fixed KNN query makes the authorizer ask about; every other action is denied.
_VECTOR_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
)
_VECTOR_FUNCTIONS = frozenset({"match"})
_SHADOW_PREFIX = f"{VECTOR_TABLE}_"
# sqlite-vec 0.1.9 errors above this k and on a negative one: the extension's own limit, not policy.
_VEC0_MAX_K = 4096

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
class NoteRow:
    """One employees row reduced to what the vector index stores about it (ADR 0010)."""

    tenant_id: str
    user_id: int
    name: str
    note: str


@dataclass(frozen=True)
class VectorMatch:
    """One KNN hit, carrying the tenant_id that rag.py's egress check verifies."""

    user_id: int
    tenant_id: str
    name: str
    note: str
    distance: float


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
    params: Sequence[object] = (),
    db_path: Path = DEFAULT_DB_PATH,
    clock: Callable[[], datetime] = _utc_now,
) -> QueryResult:
    """Validate, scope, run and audit sql for tenant_id; the one path from a query to the data.

    `params` are the filter values a trusted template declares it wrote placeholders for, bound
    after the tenant (ADR 0002 as amended); model-generated SQL passes none and may carry none.
    """
    attempt = _Attempt(generated_sql=sql, tenant=tenant_id)
    filters = tuple(params)
    try:
        scoped, bound = _scope_to_tenant(
            validate_sql(sql, parameters=len(filters)), tenant_id, filters
        )
        _verify_scope_applied(scoped, bound, tenant_id, filters)
        attempt.executed_sql = scoped.sql(dialect=_DIALECT)
        result = _run(db_path, attempt.executed_sql, _count_sql(scoped), bound)
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


def notes_for_indexing(db_path: Path = DEFAULT_DB_PATH) -> list[NoteRow]:
    """Every tenant's notes, read once at load time to build the vector index (ADR 0010).

    A load-time admin read like `init_db`, not a serving path: it is unscoped by nature because
    the index spans all tenants, but it still runs over the read-only connection and the
    employees-only authorizer, so the engine itself bounds what it can touch.
    """
    config = runtime().db
    guard = _EngineGuard(config)
    try:
        with closing(_connect(db_path, guard, config)) as conn:
            return [NoteRow(*row) for row in conn.execute(_NOTES_SELECT)]
    except sqlite3.Error as error:
        denial = _denial(guard.denied)
        if denial is not None:
            raise denial from error
        raise


def init_vector_store(
    db_path: Path,
    notes: Sequence[NoteRow],
    vectors: Sequence[Sequence[float]],
    fingerprint: str,
) -> None:
    """Rebuild the tenant-partitioned vec0 table beside db_path, stamped with the corpus digest.

    The fingerprint is written in the same transaction as the vectors, so the stamp on disk always
    describes the embeddings on disk - startup can then tell a current index from a stale one
    without re-embedding to find out (ADR 0010 as amended).
    """
    dim = _vector_dimension(notes, vectors)
    with closing(_open_vector_store(_vector_path(db_path))) as conn, conn:
        conn.execute(f"DROP TABLE IF EXISTS {VECTOR_TABLE}")
        conn.execute(f"DROP TABLE IF EXISTS {VECTOR_META_TABLE}")
        conn.execute(_VECTOR_SCHEMA.format(dim=dim))
        conn.execute(_META_SCHEMA)
        conn.executemany(
            _VECTOR_INSERT,
            [
                (note.tenant_id, note.user_id, sqlite_vec.serialize_float32(vector),
                 note.name, note.note)
                for note, vector in zip(notes, vectors, strict=True)
            ],
        )
        conn.execute(_META_INSERT, (fingerprint,))


def vector_store_fingerprint(db_path: Path) -> str | None:
    """The corpus digest the store beside db_path was built from; None when it carries none.

    A load-time admin read like `vector_store_rows`, over the same writable seam. None covers both
    a missing store and one built before fingerprints existed - either way the caller re-embeds.
    """
    path = _vector_path(db_path)
    if not path.exists():
        return None
    with closing(_open_vector_store(path)) as conn:
        try:
            row = conn.execute(_META_SELECT).fetchone()
        except sqlite3.Error:
            return None
    return None if row is None else str(row[0])


def vector_store_rows(db_path: Path) -> int:
    """How many notes the store beside db_path holds; 0 when it was never built (ADR 0010).

    A load-time admin read like `init_vector_store`, over the same writable seam and never from a
    serving path: it exists so startup can tell an already-indexed store from a missing one
    without re-embedding. A file that is absent, or present without the vec0 table, counts as 0.
    """
    path = _vector_path(db_path)
    if not path.exists():
        return 0
    with closing(_open_vector_store(path)) as conn:
        try:
            (count,) = conn.execute(_VECTOR_COUNT).fetchone()
        except sqlite3.Error:
            return 0
    return int(count)


def search_vectors(
    db_path: Path, query_vector: Sequence[float], tenant_id: str, k: int
) -> list[VectorMatch]:
    """The k nearest notes inside tenant_id's partition; the tenant is bound, never interpolated."""
    path = _vector_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"no vector store at {path}: index_notes has not run")
    guard = _VectorGuard()
    params = (sqlite_vec.serialize_float32(query_vector), _capped_k(k), tenant_id)
    try:
        with closing(_connect_vectors(path, guard)) as conn:
            return [VectorMatch(*row) for row in conn.execute(_VECTOR_SEARCH, params)]
    except sqlite3.Error as error:
        raise guard.explain(error) from error


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
    select: exp.Expression, tenant_id: str, filters: tuple[object, ...]
) -> tuple[exp.Expression, tuple[object, ...]]:
    """Rewrite every employees reference into a tenant-filtered subquery with a bound parameter."""
    substitutions = 0

    def rewrite(node: exp.Expression) -> exp.Expression:
        nonlocal substitutions
        if isinstance(node, exp.Table) and node.name.lower() == ALLOWED_TABLE:
            substitutions += 1
            return _scoped_source(node)
        return node

    # Layer 2 refuses a CTE that shadows employees, so every such reference is the real table.
    scoped = select.transform(rewrite, copy=True)
    return scoped, (tenant_id,) * substitutions + filters


def _scoped_source(table: exp.Table) -> exp.Subquery:
    """The tenant-filtered stand-in for one employees reference, keeping its name in scope."""
    return exp.Subquery(
        this=_SCOPED_SELECT.copy(),
        alias=exp.TableAlias(this=exp.to_identifier(table.alias or table.name)),
    )


def _verify_scope_applied(
    scoped: exp.Expression,
    bound: tuple[object, ...],
    tenant_id: str,
    filters: tuple[object, ...],
) -> None:
    """Refuse to run anything but a tree whose every employees reference is tenant-scoped."""
    sources = [node for node in scoped.find_all(exp.Subquery) if _is_scoping_source(node)]
    tables = [node for node in scoped.find_all(exp.Table) if node.name.lower() == ALLOWED_TABLE]
    placeholders = list(scoped.find_all(exp.Placeholder))
    counts_agree = (
        bool(sources)
        and len(tables) == len(sources)
        and len(placeholders) == len(sources) + len(filters)
    )
    single_source = len(sources) == 1 or not filters
    if (
        not counts_agree
        or not single_source
        or not _filters_confined(scoped, sources, len(filters))
        or bound != (tenant_id,) * len(sources) + filters
    ):
        raise SecurityViolation(
            f"tenant scoping did not apply: {len(tables)} employees references, "
            f"{len(sources)} scoped, {len(placeholders)} placeholders, {len(bound)} bound "
            f"of which {len(filters)} declared",
            kind="rewrite_not_applied",
        )


def _filters_confined(
    scoped: exp.Expression, sources: list[exp.Subquery], declared: int
) -> bool:
    """Whether every placeholder outside the scoping subqueries sits in the root WHERE clause.

    That is what makes the binding order a property of SQL's grammar rather than an assumption:
    a WHERE renders after the FROM that carries the tenant, while a placeholder anywhere else -
    a projection above all - could render before it and silently take the tenant's value.
    """
    scoping = {id(node) for source in sources for node in source.find_all(exp.Placeholder)}
    outside = [node for node in scoped.find_all(exp.Placeholder) if id(node) not in scoping]
    if len(outside) != declared:
        return False
    where = scoped.args.get("where")
    if where is None:
        return not outside
    in_where = {id(node) for node in where.find_all(exp.Placeholder)}
    return all(id(node) in in_where for node in outside)


def _is_scoping_source(node: exp.Subquery) -> bool:
    """Whether node is exactly the subquery this module emits, and nothing that resembles it."""
    return node.this.sql(dialect=_DIALECT) == _SCOPED_SELECT_SQL


def _count_sql(scoped: exp.Expression) -> str:
    """Render the total-rows COUNT over the scoped query, built from the AST rather than text."""
    wrapper = exp.select(exp.func("COUNT", exp.Star())).from_(
        exp.Subquery(this=scoped.copy(), alias=exp.TableAlias(this=exp.to_identifier(_COUNT_ALIAS)))
    )
    return wrapper.sql(dialect=_DIALECT)


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
        denial = _denial(self.denied)
        if denial is not None:
            return denial
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


def _run(db_path: Path, sql: str, count_sql: str, params: tuple[object, ...]) -> QueryResult:
    """Execute the scoped query behind the engine's own controls, naming any engine failure."""
    config = runtime().db
    guard = _EngineGuard(config)
    try:
        with closing(_connect(db_path, guard, config)) as conn:
            return _fetch(conn, sql, count_sql, params, config.max_result_rows)
    except sqlite3.Error as error:
        raise guard.explain(error) from error


def _connect(db_path: Path, guard: _EngineGuard, config: DbConfig) -> sqlite3.Connection:
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
    conn: sqlite3.Connection, sql: str, count_sql: str, params: tuple[object, ...], cap: int
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


class _VectorGuard:
    """The retrieval path's authorizer: this table's own storage and `match`, nothing else."""

    def __init__(self) -> None:
        self.denied: str | None = None

    def authorize(
        self,
        action: int,
        first: str | None,
        second: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
        """Allow the reads and the one function the fixed KNN query needs; deny everything else."""
        if action == sqlite3.SQLITE_READ and not _is_vector_storage(first):
            return self._deny(f"a read of {first}.{second}")
        if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() not in _VECTOR_FUNCTIONS:
            return self._deny(f"a call to {second}")
        if action in _VECTOR_ACTIONS:
            return sqlite3.SQLITE_OK
        return self._deny(f"authorizer action {action}")

    def explain(self, error: sqlite3.Error) -> Exception:
        """Report a denial as the security event it is; anything else stays the engine's error."""
        return _denial(self.denied) or error

    def _deny(self, description: str) -> int:
        """Record the first denial, so a refusal can be reported rather than merely observed."""
        self.denied = self.denied or description
        return sqlite3.SQLITE_DENY


def _denial(denied: str | None) -> SecurityViolation | None:
    """The security event a guard's first denial is, or None if it denied nothing at all."""
    if denied is None:
        return None
    return SecurityViolation(f"the engine denied {denied}", kind="authorizer_denied")


def _is_vector_storage(table: str | None) -> bool:
    """Whether table is the vec0 virtual table or one of the shadow tables it owns."""
    name = (table or "").lower()
    return name == VECTOR_TABLE or name.startswith(_SHADOW_PREFIX)


def _vector_path(db_path: Path) -> Path:
    """The vector index beside the data file: its own database, unreachable from generated SQL."""
    return db_path.with_name(VECTOR_DB_NAME)


def _vector_dimension(notes: Sequence[NoteRow], vectors: Sequence[Sequence[float]]) -> int:
    """The dimension the vec0 column is declared with, taken from the embedder's own output."""
    if len(notes) != len(vectors):
        raise ValueError(f"{len(notes)} notes but {len(vectors)} vectors")
    if not vectors:
        raise ValueError("the vector store needs at least one embedding to declare its dimension")
    dim = len(vectors[0])
    if any(len(vector) != dim for vector in vectors):
        raise ValueError("the embedder returned vectors of differing dimensions")
    return dim


def _capped_k(k: int) -> int:
    """Hold k inside the range sqlite-vec accepts, so a caller's number cannot become an error."""
    return max(0, min(k, _VEC0_MAX_K))


def _load_vec(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Load sqlite-vec through its Python API, never SQL load_extension, closing the door after."""
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def _open_vector_store(vector_path: Path) -> sqlite3.Connection:
    """Open the vector store writable: the load-time connection that owns the vec0 DDL."""
    return _load_vec(sqlite3.connect(vector_path))


def _connect_vectors(vector_path: Path, guard: _VectorGuard) -> sqlite3.Connection:
    """Open the vector store read-only with the retrieval allowlist installed (layer 2.5)."""
    conn = _load_vec(sqlite3.connect(f"{vector_path.resolve().as_uri()}?mode=ro", uri=True))
    conn.execute(_QUERY_ONLY)
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, _NO_ATTACHED_DATABASES)
    conn.set_authorizer(guard.authorize)
    return conn


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
