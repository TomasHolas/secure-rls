"""Conversation registry brick (ADR 0012) - the fifth tenant-scoped data path.

Every thread row carries the `sub` and `tenant_id` of the identity that created it, and
every read, write and delete is filtered by BOTH. A thread that belongs to another
identity raises the same `NotFound` with the same message as a thread that never existed,
so the API cannot be used to probe for foreign thread ids (existence non-disclosure).

Storage: this module owns its own SQLite file (`state.db`, path injected by the caller).
That is a documented exception to the "only db.py opens a connection" rule - the registry
is application state, not tenant DATA, and it never touches the employee tables that the
scoped executor guards.

Dependencies: standard library only. LangGraph checkpointer rows for a deleted thread are
dropped by an optional `cleanup` callback the caller supplies, so nothing here imports
langgraph and the registry stays testable without it.
"""

import sqlite3
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


def _normalize_title(title: str) -> str:
    """Collapse whitespace in the first user message and cut it to the configured length."""
    return " ".join(title.split())[: runtime().conversations.title_max_chars]
