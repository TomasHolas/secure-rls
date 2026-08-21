"""Registry tests: identity scoping and existence non-disclosure (issue #22, ADR 0012)."""

import sqlite3
import uuid
from datetime import UTC, datetime

import pytest

from auth import Identity
from conversations import ConversationRegistry, NotFound, Thread
from runtime import runtime

ALICE = Identity(sub="alice@acme", tenant_id="acme")
DAVE = Identity(sub="dave@acme", tenant_id="acme")
BOB = Identity(sub="bob@beta", tenant_id="beta")

PINNED_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
MISSING_THREAD_ID = uuid.uuid4().hex


@pytest.fixture
def registry(tmp_path):
    """A registry on its own state file, with the clock pinned so timestamps are exact."""
    return ConversationRegistry(tmp_path / "state.db", clock=lambda: PINNED_NOW)


def _raised(call) -> Exception:
    """Run a call that must raise NotFound and return the exception for comparison."""
    with pytest.raises(NotFound) as excinfo:
        call()
    return excinfo.value


def test_create_thread_returns_uuid4_hex_and_pinned_timestamp(registry):
    thread = registry.create_thread(ALICE, "How many engineers are in sales?")
    assert uuid.UUID(hex=thread.thread_id).version == 4
    assert thread.created == PINNED_NOW.isoformat()
    assert thread.title == "How many engineers are in sales?"
    assert registry.get_thread(ALICE, thread.thread_id) == thread


def test_title_is_whitespace_collapsed_and_truncated_to_the_runtime_cap(registry):
    cap = runtime().conversations.title_max_chars
    thread = registry.create_thread(ALICE, "  first\nuser   message  " + "x" * cap)
    assert len(thread.title) == cap
    assert thread.title.startswith("first user message x")


def test_list_threads_returns_own_threads_newest_first(tmp_path):
    stamps = iter([datetime(2026, 3, 1, h, tzinfo=UTC) for h in (9, 10, 11)])
    registry = ConversationRegistry(tmp_path / "state.db", clock=lambda: next(stamps))
    created = [registry.create_thread(ALICE, f"question {n}") for n in range(3)]
    assert registry.list_threads(ALICE) == list(reversed(created))


def test_list_threads_hides_other_tenants_and_other_users_in_the_same_tenant(registry):
    mine = registry.create_thread(ALICE, "mine")
    registry.create_thread(DAVE, "same tenant, other user")
    registry.create_thread(BOB, "other tenant")
    assert registry.list_threads(ALICE) == [mine]
    assert [t.title for t in registry.list_threads(DAVE)] == ["same tenant, other user"]
    assert [t.title for t in registry.list_threads(BOB)] == ["other tenant"]


def test_get_thread_of_another_tenant_is_indistinguishable_from_a_missing_one(registry):
    foreign = registry.create_thread(BOB, "other tenant")
    foreign_error = _raised(lambda: registry.get_thread(ALICE, foreign.thread_id))
    missing_error = _raised(lambda: registry.get_thread(ALICE, MISSING_THREAD_ID))
    assert type(foreign_error) is type(missing_error)
    assert str(foreign_error) == str(missing_error)


def test_get_thread_of_a_same_tenant_user_is_indistinguishable_from_a_missing_one(registry):
    neighbour = registry.create_thread(DAVE, "same tenant, other user")
    foreign_error = _raised(lambda: registry.get_thread(ALICE, neighbour.thread_id))
    missing_error = _raised(lambda: registry.get_thread(ALICE, MISSING_THREAD_ID))
    assert type(foreign_error) is type(missing_error)
    assert str(foreign_error) == str(missing_error)


def test_delete_thread_of_a_same_tenant_user_is_indistinguishable_from_a_missing_one(registry):
    neighbour = registry.create_thread(DAVE, "same tenant, other user")
    foreign_error = _raised(lambda: registry.delete_thread(ALICE, neighbour.thread_id))
    missing_error = _raised(lambda: registry.delete_thread(ALICE, MISSING_THREAD_ID))
    assert type(foreign_error) is type(missing_error)
    assert str(foreign_error) == str(missing_error)


def test_foreign_delete_leaves_the_thread_and_skips_the_cleanup_callback(registry):
    cleaned: list[str] = []
    neighbour = registry.create_thread(DAVE, "same tenant, other user")
    with pytest.raises(NotFound):
        registry.delete_thread(ALICE, neighbour.thread_id, cleanup=cleaned.append)
    assert registry.get_thread(DAVE, neighbour.thread_id) == neighbour
    assert cleaned == []


def test_delete_thread_removes_the_row_and_cleans_the_checkpointer(registry):
    cleaned: list[str] = []
    thread = registry.create_thread(ALICE, "to be deleted")
    registry.delete_thread(ALICE, thread.thread_id, cleanup=cleaned.append)
    assert cleaned == [thread.thread_id]
    assert registry.list_threads(ALICE) == []
    with pytest.raises(NotFound):
        registry.get_thread(ALICE, thread.thread_id)


def test_deleting_twice_raises_not_found_and_does_not_clean_up_again(registry):
    cleaned: list[str] = []
    thread = registry.create_thread(ALICE, "to be deleted")
    registry.delete_thread(ALICE, thread.thread_id, cleanup=cleaned.append)
    with pytest.raises(NotFound):
        registry.delete_thread(ALICE, thread.thread_id, cleanup=cleaned.append)
    assert cleaned == [thread.thread_id]


def test_delete_thread_works_without_a_cleanup_callback(registry):
    thread = registry.create_thread(ALICE, "no checkpointer yet")
    registry.delete_thread(ALICE, thread.thread_id)
    assert registry.list_threads(ALICE) == []


def test_registry_persists_in_its_own_state_file(tmp_path):
    state_db = tmp_path / "state.db"
    thread = ConversationRegistry(state_db, clock=lambda: PINNED_NOW).create_thread(ALICE, "kept")
    assert ConversationRegistry(state_db).list_threads(ALICE) == [thread]


def test_stored_row_carries_the_owning_identity(registry, tmp_path):
    thread = registry.create_thread(ALICE, "owned")
    with sqlite3.connect(tmp_path / "state.db") as conn:
        row = conn.execute(
            "SELECT sub, tenant_id, title, created FROM threads WHERE thread_id = ?",
            (thread.thread_id,),
        ).fetchone()
    assert row == (ALICE.sub, ALICE.tenant_id, "owned", PINNED_NOW.isoformat())
    assert Thread(thread.thread_id, row[2], row[3]) == thread
