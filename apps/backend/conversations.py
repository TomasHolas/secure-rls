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

Storage: this module owns its own SQLite file (`state.db`, path injected by the caller).
That is a documented exception to the "only db.py opens a connection" rule - the registry
is application state, not tenant DATA, and it never touches the employee tables that the
scoped executor guards.

Dependencies: standard library only. LangGraph checkpointer rows for a deleted thread are
dropped by an optional `cleanup` callback the caller supplies, so nothing here imports
langgraph and the registry stays testable without it.
"""

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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    sub       TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    title     TEXT NOT NULL,
    created   TEXT NOT NULL
)
"""

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
            conn.execute(_SCHEMA)
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

    def delete_thread(
        self,
        identity: Identity,
        thread_id: str,
        cleanup: Callable[[str], None] | None = None,
    ) -> None:
        """Delete the identity's own thread, then let `cleanup` drop its checkpointer rows."""
        with closing(sqlite3.connect(self._state_db)) as conn:
            deleted = conn.execute(
                "DELETE FROM threads WHERE thread_id = ? AND sub = ? AND tenant_id = ?",
                (thread_id, identity.sub, identity.tenant_id),
            ).rowcount
            conn.commit()
        if not deleted:
            raise NotFound(_NOT_FOUND_MESSAGE)
        if cleanup is not None:
            cleanup(thread_id)


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
