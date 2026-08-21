"""Conversation registry brick (ADR 0012) - the fifth tenant-scoped data path.

Every thread row carries the `sub` and `tenant_id` of the identity that created it, and
every read, write and delete is filtered by BOTH. A thread that belongs to another
identity raises the same `NotFound` with the same message as a thread that never existed,
so the API cannot be used to probe for foreign thread ids (existence non-disclosure).
`rename_thread` is scoped by the same clause as the rest: a rename aimed at another
identity's thread changes nothing and answers exactly like a missing one.

Titles are normalized on every write, whoever wrote them (ADR 0012 as amended): control and
formatting characters become spaces, whitespace collapses, and the result is cut to the
configured cap. A generated title is model output, so the store is the last place that can
guarantee what the sidebar renders is one line of displayable text - no NUL, no escape
sequence, no bidi override reordering the rail.

Tool evidence (ADR 0012 as amended). Beside the thread rows this module keeps what each turn's
tools returned, so a reopened thread can re-render its charts, its SQL pair and its tables
instead of showing prose where a plot used to be. What is stored is server-produced payload -
the executed statement, the row window that came back, a chart spec, anomalies, retrieved notes -
plus the `generated_sql` the model wrote inside it, because the generated-versus-executed pair is
the point of showing it at all. The live trace stays session-only: the model's reasoning, the
retries and the graph steps are the transport of the turn that produced them and are not kept.

Payload rows are scoped by the same `sub` + `tenant_id` clause as the thread rows, on both the
read and the write, and they are bounded twice over: the row-shaped lists inside a payload are
cut to the executor's result cap (ADR 0007), at most `conversations.max_stored_results_per_turn`
payloads of one turn are kept, and only the newest `conversations.max_stored_result_turns` turns
of a thread keep theirs. Deleting a thread deletes its payloads with it.

Storage: this module owns its own SQLite file (`state.db`, path injected by the caller).
That is a documented exception to the "only db.py opens a connection" rule - the registry
is application state, not tenant DATA, and it never touches the employee tables that the
scoped executor guards.

Dependencies: standard library only. LangGraph checkpointer rows for a deleted thread are
dropped by an optional `cleanup` callback the caller supplies, so nothing here imports
langgraph and the registry stays testable without it.
"""

import json
import sqlite3
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from auth import Identity
from runtime import runtime

_THREADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    sub       TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    title     TEXT NOT NULL,
    created   TEXT NOT NULL
)
"""

_TOOL_RESULTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_results (
    thread_id TEXT NOT NULL,
    sub       TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    turn      INTEGER NOT NULL,
    position  INTEGER NOT NULL,
    tool      TEXT NOT NULL,
    payload   TEXT NOT NULL,
    created   TEXT NOT NULL,
    PRIMARY KEY (thread_id, turn, position)
)
"""

_NOT_FOUND_MESSAGE = "conversation not found"
# Cc and Cf: NUL and escape sequences, and the bidi overrides that could reorder the rail.
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf"})
# The payload keys that carry one row per element, and are therefore cut to the result cap.
_ROW_KEYS = frozenset({"rows", "anomalies"})


class NotFound(Exception):
    """Raised identically for a missing thread and for another identity's thread."""


@dataclass(frozen=True)
class Thread:
    """A conversation as the API returns it; the owning identity stays server-side."""

    thread_id: str
    title: str
    created: str


@dataclass(frozen=True)
class ToolResult:
    """One turn's tool evidence: the turn it belongs to, the tool that ran, its server payload.

    `turn` is the ordinal of the question that opened the turn, counted from one, so a replayed
    transcript can put the evidence back above the answer it produced. `data` is the payload the
    tool returned, keyed exactly as the live `tool_result` trace event keys it (ADR 0012).
    """

    turn: int
    tool: str
    data: dict[str, object]


class ConversationRegistry:
    """Tenant-scoped CRUD over the thread registry in its own SQLite state file."""

    def __init__(
        self,
        state_db: str | Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Open (creating if needed) the state file and pin the clock used for timestamps."""
        self._state_db = Path(state_db)
        self._clock = clock
        with closing(sqlite3.connect(self._state_db)) as conn:
            conn.execute(_THREADS_SCHEMA)
            conn.execute(_TOOL_RESULTS_SCHEMA)
            conn.commit()

    def create_thread(self, identity: Identity, title: str) -> Thread:
        """Register a new thread owned by this identity, with the title truncated to the cap."""
        thread = Thread(
            thread_id=uuid.uuid4().hex,
            title=_normalize_title(title),
            created=self._clock().isoformat(),
        )
        with closing(sqlite3.connect(self._state_db)) as conn:
            conn.execute(
                "INSERT INTO threads (thread_id, sub, tenant_id, title, created) "
                "VALUES (?, ?, ?, ?, ?)",
                (thread.thread_id, identity.sub, identity.tenant_id, thread.title, thread.created),
            )
            conn.commit()
        return thread

    def list_threads(self, identity: Identity) -> list[Thread]:
        """Return this identity's threads, newest first; another identity's are never visible."""
        with closing(sqlite3.connect(self._state_db)) as conn:
            rows = conn.execute(
                "SELECT thread_id, title, created FROM threads "
                "WHERE sub = ? AND tenant_id = ? ORDER BY created DESC, rowid DESC",
                (identity.sub, identity.tenant_id),
            ).fetchall()
        return [Thread(*row) for row in rows]

    def get_thread(self, identity: Identity, thread_id: str) -> Thread:
        """Return the identity's own thread, or raise NotFound for missing and foreign alike."""
        with closing(sqlite3.connect(self._state_db)) as conn:
            row = conn.execute(
                "SELECT thread_id, title, created FROM threads "
                "WHERE thread_id = ? AND sub = ? AND tenant_id = ?",
                (thread_id, identity.sub, identity.tenant_id),
            ).fetchone()
        if row is None:
            raise NotFound(_NOT_FOUND_MESSAGE)
        return Thread(*row)

    def rename_thread(self, identity: Identity, thread_id: str, title: str) -> Thread:
        """Retitle the identity's own thread; a foreign or missing id raises the same NotFound."""
        with closing(sqlite3.connect(self._state_db)) as conn:
            renamed = conn.execute(
                "UPDATE threads SET title = ? WHERE thread_id = ? AND sub = ? AND tenant_id = ?",
                (_normalize_title(title), thread_id, identity.sub, identity.tenant_id),
            ).rowcount
            conn.commit()
        if not renamed:
            raise NotFound(_NOT_FOUND_MESSAGE)
        return self.get_thread(identity, thread_id)

    def record_tool_results(
        self, identity: Identity, thread_id: str, results: Sequence[ToolResult]
    ) -> None:
        """Store one turn's tool payloads under the identity's own thread, capped and pruned.

        The write is scoped by the same clause as every read, so a thread this identity does not
        own - or one deleted while its turn was still streaming - is written nothing and raises
        nothing: a foreign write is as indistinguishable from a missing thread as a foreign read
        is, and the caller is a stream teardown that has no reader left to tell.

        The turn is rewritten as a whole, so recording it twice cannot leave half of an older
        attempt behind, and the turns older than the newest `max_stored_result_turns` are dropped
        as the new one lands - a long thread keeps its recent evidence, not all of it.
        """
        limits = runtime().conversations
        kept = list(results)[: limits.max_stored_results_per_turn]
        if not kept:
            return
        scope = (thread_id, identity.sub, identity.tenant_id)
        turns = sorted({result.turn for result in kept})
        stored = self._clock().isoformat()
        with closing(sqlite3.connect(self._state_db)) as conn:
            if not self._owns(conn, identity, thread_id):
                return
            conn.executemany(
                "DELETE FROM tool_results "
                "WHERE thread_id = ? AND sub = ? AND tenant_id = ? AND turn = ?",
                [(*scope, turn) for turn in turns],
            )
            conn.executemany(
                "INSERT INTO tool_results "
                "(thread_id, sub, tenant_id, turn, position, tool, payload, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        *scope,
                        result.turn,
                        position,
                        result.tool,
                        _stored_payload(result.data),
                        stored,
                    )
                    for position, result in enumerate(kept)
                ],
            )
            conn.execute(
                "DELETE FROM tool_results "
                "WHERE thread_id = ? AND sub = ? AND tenant_id = ? AND turn <= ?",
                (*scope, turns[-1] - limits.max_stored_result_turns),
            )
            conn.commit()

    def thread_tool_results(self, identity: Identity, thread_id: str) -> list[ToolResult]:
        """Replay the tool payloads of the identity's own thread, oldest turn first.

        Scoped like every other read: another identity's thread reads as empty, which is exactly
        what a thread that never ran a tool reads as. Nothing here decides whether the thread may
        be read at all - the caller asks the registry for it first, as it does for the transcript.
        """
        with closing(sqlite3.connect(self._state_db)) as conn:
            rows = conn.execute(
                "SELECT turn, tool, payload FROM tool_results "
                "WHERE thread_id = ? AND sub = ? AND tenant_id = ? ORDER BY turn, position",
                (thread_id, identity.sub, identity.tenant_id),
            ).fetchall()
        return [ToolResult(turn, tool, json.loads(payload)) for turn, tool, payload in rows]

    def delete_thread(
        self,
        identity: Identity,
        thread_id: str,
        cleanup: Callable[[str], None] | None = None,
    ) -> None:
        """Delete the identity's own thread and its stored payloads, then let `cleanup` run.

        `cleanup` drops the thread's checkpointer rows; the tool payloads are this store's own
        and go in the same transaction as the thread row, under the same scoping clause.
        """
        with closing(sqlite3.connect(self._state_db)) as conn:
            deleted = conn.execute(
                "DELETE FROM threads WHERE thread_id = ? AND sub = ? AND tenant_id = ?",
                (thread_id, identity.sub, identity.tenant_id),
            ).rowcount
            conn.execute(
                "DELETE FROM tool_results WHERE thread_id = ? AND sub = ? AND tenant_id = ?",
                (thread_id, identity.sub, identity.tenant_id),
            )
            conn.commit()
        if not deleted:
            raise NotFound(_NOT_FOUND_MESSAGE)
        if cleanup is not None:
            cleanup(thread_id)

    def _owns(self, conn: sqlite3.Connection, identity: Identity, thread_id: str) -> bool:
        """Whether this identity's own thread exists, by the clause every access is scoped by."""
        row = conn.execute(
            "SELECT 1 FROM threads WHERE thread_id = ? AND sub = ? AND tenant_id = ?",
            (thread_id, identity.sub, identity.tenant_id),
        ).fetchone()
        return row is not None


def plain_one_line(text: str) -> str:
    """One line of displayable text: control and formatting characters out, whitespace collapsed.

    The shape a title has to be in before anything renders it, wherever the text came from. It
    is public because the titler needs the same judgment on a model's answer before it can tell
    a label from noise (`titles.py`), and two copies of "what is displayable" would drift.
    """
    displayable = "".join(
        " " if unicodedata.category(char) in _CONTROL_CATEGORIES else char for char in text
    )
    return " ".join(displayable.split())


def _stored_payload(data: Mapping[str, object]) -> str:
    """One tool payload as the store keeps it: JSON, with the row-shaped lists cut to the cap.

    The cap is the executor's own `db.max_result_rows` (ADR 0007), which already bounded what
    came back; restating it here means what this store holds is bounded by its own rule and not
    by another module continuing to apply one.
    """
    window = runtime().db.max_result_rows
    return json.dumps(
        {
            key: value[:window] if key in _ROW_KEYS and isinstance(value, list) else value
            for key, value in data.items()
        }
    )


def _normalize_title(title: str) -> str:
    """The title as the registry stores it: displayable, one line, cut to the configured cap."""
    return plain_one_line(title)[: runtime().conversations.title_max_chars]
