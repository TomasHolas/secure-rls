"""Registry tests: identity scoping and existence non-disclosure (issues #22, #70, #72, ADR 0012).

The rename write is held to the same two properties as every other access: it is scoped by
`sub` and `tenant_id`, and a rename aimed at a thread the caller does not own is the same
`NotFound` as one aimed at a thread that never existed. Because a rename can carry model
output, the normalization it shares with `create_thread` is asserted here too - the cap, and
control and formatting characters that must never reach the rail.

The stored turn history (issues #70, #90) is held to the same two properties in both directions: a
recording aimed at another identity's thread stores nothing, and a read of one returns nothing
rather than data. What is asserted beyond scoping is this store's own share of the bounds - how
many of a thread's turns keep their history - that a turn is rewritten as a whole, that a thread's
history is deleted with the thread, and that a row which cannot be read raises rather than
replaying a partial turn as a whole one. The caps on what one turn's history may hold belong to
`test_turns.py`, which owns the reduction.
"""

import json
import sqlite3
import unicodedata
import uuid
from datetime import UTC, datetime

import pytest

from auth import Identity
from conversations import ConversationRegistry, NotFound, Thread, TurnHistory
from runtime import runtime

ALICE = Identity(sub="alice@acme", tenant_id="acme")
DAVE = Identity(sub="dave@acme", tenant_id="acme")
BOB = Identity(sub="bob@beta", tenant_id="beta")

PINNED_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
MISSING_THREAD_ID = uuid.uuid4().hex

GENERATED_SQL = "SELECT department, AVG(salary) FROM employees GROUP BY department"
EXECUTED_SQL = (
    "SELECT department, AVG(salary) FROM (SELECT * FROM employees WHERE tenant_id = ?) "
    "GROUP BY department"
)
QUERY_PAYLOAD = {
    "generated_sql": GENERATED_SQL,
    "executed_sql": EXECUTED_SQL,
    "columns": ["department", "avg"],
    "rows": [["Engineering", 91000]],
    "total_count": 1,
    "returned_count": 1,
    "truncated": False,
}
DONE_EVENT = {
    "type": "done",
    "status": "ok",
    "answer": "Engineering averages 91000.",
    "grounded": True,
    "model": "fake-model:1b",
    "prompt_guardrails": True,
    "input_tokens": 250,
    "output_tokens": 28,
    "duration_s": 1.75,
}
EVENTS = [
    {"type": "node_start", "node": "reason"},
    {"type": "reasoning", "text": "an average per department", "truncated": False},
    {"type": "tool_call", "id": "c1", "tool": "query_db", "args": {"sql": GENERATED_SQL}},
    {"type": "tool_result", "id": "c1", "tool": "query_db", "content": "", "data": QUERY_PAYLOAD},
    DONE_EVENT,
]


@pytest.fixture
def registry(tmp_path):
    """A registry on its own state file, with the clock pinned so timestamps are exact."""
    return ConversationRegistry(tmp_path / "state.db", clock=lambda: PINNED_NOW)


def _history(turn: int = 1, cut: int = 0) -> TurnHistory:
    """One turn's history, fresh each time so a test cannot mutate the shared fixture."""
    return TurnHistory(turn=turn, events=[dict(event) for event in EVENTS], cut=cut)


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


def test_rename_thread_replaces_the_title_and_keeps_the_identity_and_timestamp(registry):
    thread = registry.create_thread(ALICE, "Run this SQL for me: SELECT AVG(salary) FROM ...")

    renamed = registry.rename_thread(ALICE, thread.thread_id, "Average salary by department")

    assert renamed == Thread(thread.thread_id, "Average salary by department", thread.created)
    assert registry.list_threads(ALICE) == [renamed]


def test_renamed_title_is_whitespace_collapsed_and_truncated_to_the_runtime_cap(registry):
    cap = runtime().conversations.title_max_chars
    thread = registry.create_thread(ALICE, "first message")

    renamed = registry.rename_thread(ALICE, thread.thread_id, "  a\n\ttitle  " + "x" * cap)

    assert len(renamed.title) == cap
    assert renamed.title.startswith("a title x")


def test_renamed_title_has_control_and_formatting_characters_stripped(registry):
    thread = registry.create_thread(ALICE, "first message")

    renamed = registry.rename_thread(
        ALICE, thread.thread_id, "Head\x00count\x1b[31m by‮ office​"
    )

    assert renamed.title == "Head count [31m by office"
    assert all(unicodedata.category(char) not in {"Cc", "Cf"} for char in renamed.title)


def test_rename_of_another_tenants_thread_is_indistinguishable_from_a_missing_one(registry):
    foreign = registry.create_thread(BOB, "other tenant")
    foreign_error = _raised(lambda: registry.rename_thread(ALICE, foreign.thread_id, "mine now"))
    missing_error = _raised(lambda: registry.rename_thread(ALICE, MISSING_THREAD_ID, "mine now"))
    assert type(foreign_error) is type(missing_error)
    assert str(foreign_error) == str(missing_error)
    assert registry.get_thread(BOB, foreign.thread_id) == foreign


def test_rename_of_a_same_tenant_users_thread_is_indistinguishable_from_a_missing_one(registry):
    neighbour = registry.create_thread(DAVE, "same tenant, other user")
    foreign_error = _raised(lambda: registry.rename_thread(ALICE, neighbour.thread_id, "mine now"))
    missing_error = _raised(lambda: registry.rename_thread(ALICE, MISSING_THREAD_ID, "mine now"))
    assert type(foreign_error) is type(missing_error)
    assert str(foreign_error) == str(missing_error)
    assert registry.get_thread(DAVE, neighbour.thread_id) == neighbour


def test_rename_of_a_deleted_thread_raises_not_found(registry):
    thread = registry.create_thread(ALICE, "to be deleted")
    registry.delete_thread(ALICE, thread.thread_id)
    with pytest.raises(NotFound):
        registry.rename_thread(ALICE, thread.thread_id, "too late")


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


def test_turn_history_replays_in_turn_order(registry):
    thread = registry.create_thread(ALICE, "charted")
    registry.record_turn(ALICE, thread.thread_id, _history(1))
    registry.record_turn(ALICE, thread.thread_id, _history(2, cut=3))

    replayed = registry.thread_turns(ALICE, thread.thread_id)

    assert [(history.turn, history.cut) for history in replayed] == [(1, 0), (2, 3)]
    assert replayed[0].events == EVENTS


def test_turn_history_of_a_thread_never_chatted_in_is_empty(registry):
    thread = registry.create_thread(ALICE, "just talk")
    assert registry.thread_turns(ALICE, thread.thread_id) == []


def test_recording_nothing_writes_nothing(registry):
    thread = registry.create_thread(ALICE, "no history this turn")
    registry.record_turn(ALICE, thread.thread_id, TurnHistory(turn=1, events=[], cut=0))
    assert registry.thread_turns(ALICE, thread.thread_id) == []


def test_recording_on_another_identitys_thread_stores_nothing(registry):
    foreign = registry.create_thread(BOB, "other tenant")
    neighbour = registry.create_thread(DAVE, "same tenant, other user")

    registry.record_turn(ALICE, foreign.thread_id, _history(1))
    registry.record_turn(ALICE, neighbour.thread_id, _history(1))
    registry.record_turn(ALICE, MISSING_THREAD_ID, _history(1))

    assert registry.thread_turns(BOB, foreign.thread_id) == []
    assert registry.thread_turns(DAVE, neighbour.thread_id) == []


def test_turn_history_of_another_identitys_thread_reads_as_empty(registry):
    foreign = registry.create_thread(BOB, "other tenant")
    registry.record_turn(BOB, foreign.thread_id, _history(1))

    assert registry.thread_turns(ALICE, foreign.thread_id) == []
    assert registry.thread_turns(ALICE, MISSING_THREAD_ID) == []
    assert len(registry.thread_turns(BOB, foreign.thread_id)) == 1


def test_turns_older_than_the_history_ceiling_are_dropped(registry):
    ceiling = runtime().conversations.max_history_turns
    thread = registry.create_thread(ALICE, "a long conversation")
    for turn in (1, 2, ceiling + 1):
        registry.record_turn(ALICE, thread.thread_id, _history(turn))

    kept = registry.thread_turns(ALICE, thread.thread_id)

    assert [history.turn for history in kept] == [2, ceiling + 1]


def test_recording_a_turn_again_replaces_what_it_stored(registry):
    thread = registry.create_thread(ALICE, "a retried turn")
    registry.record_turn(ALICE, thread.thread_id, _history(1))

    registry.record_turn(
        ALICE, thread.thread_id, TurnHistory(turn=1, events=[dict(DONE_EVENT)], cut=1)
    )

    kept = registry.thread_turns(ALICE, thread.thread_id)
    assert kept == [TurnHistory(turn=1, events=[DONE_EVENT], cut=1)]


def test_deleting_a_thread_deletes_its_turn_history(registry):
    thread = registry.create_thread(ALICE, "to be deleted")
    registry.record_turn(ALICE, thread.thread_id, _history(1))

    registry.delete_thread(ALICE, thread.thread_id)

    assert registry.thread_turns(ALICE, thread.thread_id) == []


def test_a_foreign_delete_leaves_the_turn_history_alone(registry):
    neighbour = registry.create_thread(DAVE, "same tenant, other user")
    registry.record_turn(DAVE, neighbour.thread_id, _history(1))

    with pytest.raises(NotFound):
        registry.delete_thread(ALICE, neighbour.thread_id)

    assert len(registry.thread_turns(DAVE, neighbour.thread_id)) == 1


def test_stored_turn_row_carries_the_owning_identity(registry, tmp_path):
    thread = registry.create_thread(ALICE, "owned")
    registry.record_turn(ALICE, thread.thread_id, _history(3, cut=2))

    with sqlite3.connect(tmp_path / "state.db") as conn:
        row = conn.execute(
            "SELECT sub, tenant_id, turn, cut, created FROM turn_history WHERE thread_id = ?",
            (thread.thread_id,),
        ).fetchone()

    assert row == (ALICE.sub, ALICE.tenant_id, 3, 2, PINNED_NOW.isoformat())


def test_turn_history_persists_in_the_same_state_file(tmp_path):
    state_db = tmp_path / "state.db"
    registry = ConversationRegistry(state_db, clock=lambda: PINNED_NOW)
    thread = registry.create_thread(ALICE, "kept")
    registry.record_turn(ALICE, thread.thread_id, _history(1))

    reopened = ConversationRegistry(state_db).thread_turns(ALICE, thread.thread_id)

    assert reopened == [TurnHistory(turn=1, events=EVENTS, cut=0)]


def test_a_history_row_that_cannot_be_read_raises_instead_of_replaying_a_partial_turn(
    registry, tmp_path
):
    thread = registry.create_thread(ALICE, "damaged")
    registry.record_turn(ALICE, thread.thread_id, _history(1))
    with sqlite3.connect(tmp_path / "state.db") as conn:
        conn.execute("UPDATE turn_history SET events = ?", ("{not json",))
        conn.commit()

    with pytest.raises(json.JSONDecodeError):
        registry.thread_turns(ALICE, thread.thread_id)


def test_the_superseded_payload_table_is_dropped_on_open(tmp_path):
    state_db = tmp_path / "state.db"
    with sqlite3.connect(state_db) as conn:
        conn.execute("CREATE TABLE tool_results (thread_id TEXT, payload TEXT)")
        conn.execute("INSERT INTO tool_results VALUES ('t1', 'a chart of beta rows')")
        conn.commit()

    ConversationRegistry(state_db)

    with sqlite3.connect(state_db) as conn:
        tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master")}
    assert "tool_results" not in tables
    assert "turn_history" in tables


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
