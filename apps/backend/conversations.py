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

Turn history (ADR 0012 as amended, issue #90). Beside the thread rows this module keeps the whole
turn: the trace events it produced, in order - the model's reasoning per round, every tool call
with the arguments the model wrote, each call's one outcome (its result payload, the retry that
fed an error back, or the security refusal that ended it), and the terminal frame with the turn's
status, its telemetry and the prompt-guardrail position that produced it. A reopened thread
therefore replays as the conversation that happened, not as a tidied answer, which is the whole
auditability claim of this product.

One row per turn, `events` a JSON array of what history keeps, `cut` how many pieces the caps
refused. What reduces a live event stream to that row is `turns.py`, which owns the caps as well;
this module stores what it is handed and never inspects it. Rows are scoped by the same
`sub` + `tenant_id` clause as the thread rows, on both the read and the write, and only the newest
`conversations.max_history_turns` turns of a thread keep their history - an older turn replays as
text, as it did before any of this was stored. Deleting a thread deletes its history with it.

A read never repairs: a row whose JSON cannot be parsed raises rather than replaying a partial
turn as if it were whole. The lenient half is the write (`turns.py`), where the answer has already
streamed and losing the history is not worth failing a good turn over.

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
from collections.abc import Callable
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

_TURN_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS turn_history (
    thread_id TEXT NOT NULL,
    sub       TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    turn      INTEGER NOT NULL,
    events    TEXT NOT NULL,
    cut       INTEGER NOT NULL,
    created   TEXT NOT NULL,
    PRIMARY KEY (thread_id, turn)
)
"""

# Discards the payloads issue #70 stored: turn history replaced them, no code reads them any more,
# and leaving tenant-derived rows on disk unreachable is worse (owner-approved, issue #90).
_SUPERSEDED_SCHEMA = "DROP TABLE IF EXISTS tool_results"

_NOT_FOUND_MESSAGE = "conversation not found"
# Cc and Cf: NUL and escape sequences, and the bidi overrides that could reorder the rail.
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf"})


class NotFound(Exception):
    """Raised identically for a missing thread and for another identity's thread."""


@dataclass(frozen=True)
class Thread:
    """A conversation as the API returns it; the owning identity stays server-side."""

    thread_id: str
    title: str
    created: str


@dataclass(frozen=True)
class TurnHistory:
    """One past turn as the store keeps it: its trace events in order, and what the caps refused.

    `turn` is the ordinal of the question that opened the turn, counted from one, so a replayed
    transcript can put each turn's history back above the answer it produced. `events` are ADR
    0012 trace events, keyed exactly as the live stream keys them, so one fold renders a replayed
    turn and a live one. `cut` is how many pieces of the turn the caps refused, which is what lets
    a replay state that it is partial instead of reading as whole.
    """

    turn: int
    events: list[dict[str, object]]
    cut: int


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
            conn.execute(_TURN_HISTORY_SCHEMA)
            conn.execute(_SUPERSEDED_SCHEMA)
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

    def record_turn(self, identity: Identity, thread_id: str, history: TurnHistory) -> None:
        """Store one turn's history under the identity's own thread, pruning the older turns.

        The write is scoped by the same clause as every read, so a thread this identity does not
        own - or one deleted while its turn was still streaming - is written nothing and raises
        nothing: a foreign write is as indistinguishable from a missing thread as a foreign read
        is, and the caller is a stream teardown that has no reader left to tell.

        The turn is one row and is rewritten as a whole, so recording it twice cannot leave half of
        an older attempt behind, and the turns older than the newest `max_history_turns` are
        dropped as the new one lands - a long thread keeps its recent history, not all of it.
        """
        if not history.events:
            return
        scope = (thread_id, identity.sub, identity.tenant_id)
        with closing(sqlite3.connect(self._state_db)) as conn:
            if not self._owns(conn, identity, thread_id):
                return
            conn.execute(
                "INSERT OR REPLACE INTO turn_history "
                "(thread_id, sub, tenant_id, turn, events, cut, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    *scope,
                    history.turn,
                    json.dumps(history.events),
                    history.cut,
                    self._clock().isoformat(),
                ),
            )
            conn.execute(
                "DELETE FROM turn_history "
                "WHERE thread_id = ? AND sub = ? AND tenant_id = ? AND turn <= ?",
                (*scope, history.turn - runtime().conversations.max_history_turns),
            )
            conn.commit()

    def thread_turns(self, identity: Identity, thread_id: str) -> list[TurnHistory]:
        """Replay the stored turns of the identity's own thread, oldest first.

        Scoped like every other read: another identity's thread reads as empty, which is exactly
        what a thread that was never chatted in reads as. Nothing here decides whether the thread
        may be read at all - the caller asks the registry for it first, as it does for the
        transcript. A row that cannot be parsed raises: a replay that cannot be reconstructed says
        so rather than rendering a partial turn as a whole one.
        """
        with closing(sqlite3.connect(self._state_db)) as conn:
            rows = conn.execute(
                "SELECT turn, events, cut FROM turn_history "
                "WHERE thread_id = ? AND sub = ? AND tenant_id = ? ORDER BY turn",
                (thread_id, identity.sub, identity.tenant_id),
            ).fetchall()
        return [TurnHistory(turn, json.loads(events), cut) for turn, events, cut in rows]

    def delete_thread(
        self,
        identity: Identity,
        thread_id: str,
        cleanup: Callable[[str], None] | None = None,
    ) -> None:
        """Delete the identity's own thread and its stored history, then let `cleanup` run.

        `cleanup` drops the thread's checkpointer rows; the turn history is this store's own
        and goes in the same transaction as the thread row, under the same scoping clause.
        """
        with closing(sqlite3.connect(self._state_db)) as conn:
            deleted = conn.execute(
                "DELETE FROM threads WHERE thread_id = ? AND sub = ? AND tenant_id = ?",
                (thread_id, identity.sub, identity.tenant_id),
            ).rowcount
            conn.execute(
                "DELETE FROM turn_history WHERE thread_id = ? AND sub = ? AND tenant_id = ?",
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


def _normalize_title(title: str) -> str:
    """The title as the registry stores it: displayable, one line, cut to the configured cap."""
    return plain_one_line(title)[: runtime().conversations.title_max_chars]
