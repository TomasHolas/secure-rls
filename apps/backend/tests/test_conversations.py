"""Registry tests: identity scoping and existence non-disclosure (issues #22, #70, #72, ADR 0012).

The rename write is held to the same two properties as every other access: it is scoped by
`sub` and `tenant_id`, and a rename aimed at a thread the caller does not own is the same
`NotFound` as one aimed at a thread that never existed. Because a rename can carry model
output, the normalization it shares with `create_thread` is asserted here too - the cap, and
control and formatting characters that must never reach the rail.

The stored tool payloads (issue #70) are held to the same two properties in both directions: a
recording aimed at another identity's thread stores nothing, and a read of one returns nothing
rather than data. What is asserted beyond scoping is what bounds them - the row window inside a
payload, the number of payloads one turn may keep, and the turns a thread keeps them for - and
that a thread's payloads are deleted with the thread.
"""

import sqlite3
import unicodedata
import uuid
from datetime import UTC, datetime

import pytest

from auth import Identity
from conversations import ConversationRegistry, NotFound, Thread, ToolResult
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
CHART_PAYLOAD = {
    "chart_spec": {
        "kind": "bar",
        "title": "Headcount by department",
        "x_label": "department",
        "y_label": "headcount",
        "data": [{"x": "Engineering", "y": 12}],
    }
}


@pytest.fixture
def registry(tmp_path):
    """A registry on its own state file, with the clock pinned so timestamps are exact."""
    return ConversationRegistry(tmp_path / "state.db", clock=lambda: PINNED_NOW)


def _chart(turn: int = 1) -> ToolResult:
    """One turn's chart payload, fresh each time so a test cannot mutate the shared fixture."""
    return ToolResult(turn, "plot", dict(CHART_PAYLOAD))


def _query(turn: int = 1) -> ToolResult:
    """One turn's query payload: the SQL pair, the columns and the row window it returned."""
    return ToolResult(turn, "query_db", dict(QUERY_PAYLOAD))


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


def test_tool_results_replay_in_turn_then_call_order(registry):
    thread = registry.create_thread(ALICE, "charted")
    registry.record_tool_results(
        ALICE,
        thread.thread_id,
        [_query(1), _chart(1)],
    )
    registry.record_tool_results(ALICE, thread.thread_id, [_chart(2)])

    replayed = registry.thread_tool_results(ALICE, thread.thread_id)

    assert [(result.turn, result.tool) for result in replayed] == [
        (1, "query_db"),
        (1, "plot"),
        (2, "plot"),
    ]
    assert replayed[0].data == QUERY_PAYLOAD
    assert replayed[1].data == CHART_PAYLOAD


def test_tool_results_of_a_thread_that_ran_no_tool_are_empty(registry):
    thread = registry.create_thread(ALICE, "just talk")
    assert registry.thread_tool_results(ALICE, thread.thread_id) == []


def test_recording_nothing_writes_nothing(registry):
    thread = registry.create_thread(ALICE, "no tools this turn")
    registry.record_tool_results(ALICE, thread.thread_id, [])
    assert registry.thread_tool_results(ALICE, thread.thread_id) == []


def test_recording_on_another_identitys_thread_stores_nothing(registry):
    foreign = registry.create_thread(BOB, "other tenant")
    neighbour = registry.create_thread(DAVE, "same tenant, other user")

    registry.record_tool_results(ALICE, foreign.thread_id, [_chart(1)])
    registry.record_tool_results(
        ALICE, neighbour.thread_id, [_chart(1)]
    )
    registry.record_tool_results(
        ALICE, MISSING_THREAD_ID, [_chart(1)]
    )

    assert registry.thread_tool_results(BOB, foreign.thread_id) == []
    assert registry.thread_tool_results(DAVE, neighbour.thread_id) == []


def test_tool_results_of_another_identitys_thread_read_as_empty(registry):
    foreign = registry.create_thread(BOB, "other tenant")
    registry.record_tool_results(BOB, foreign.thread_id, [_chart(1)])

    assert registry.thread_tool_results(ALICE, foreign.thread_id) == []
    assert registry.thread_tool_results(ALICE, MISSING_THREAD_ID) == []
    assert len(registry.thread_tool_results(BOB, foreign.thread_id)) == 1


def test_stored_row_window_is_cut_to_the_executors_result_cap(registry):
    window = runtime().db.max_result_rows
    thread = registry.create_thread(ALICE, "a wide result")
    payload = {
        "columns": ["user_id"],
        "rows": [[index] for index in range(window + 25)],
        "anomalies": [{"user_id": index} for index in range(window + 25)],
    }

    registry.record_tool_results(ALICE, thread.thread_id, [ToolResult(1, "query_db", payload)])

    stored = registry.thread_tool_results(ALICE, thread.thread_id)[0].data
    assert len(stored["rows"]) == window
    assert len(stored["anomalies"]) == window
    assert stored["rows"][-1] == [window - 1]
    assert stored["columns"] == ["user_id"]


def test_only_the_capped_number_of_results_of_one_turn_is_kept(registry):
    cap = runtime().conversations.max_stored_results_per_turn
    thread = registry.create_thread(ALICE, "a busy turn")
    tools = [f"tool_{index}" for index in range(cap + 3)]

    registry.record_tool_results(
        ALICE, thread.thread_id, [ToolResult(1, tool, {"columns": []}) for tool in tools]
    )

    kept = registry.thread_tool_results(ALICE, thread.thread_id)
    assert [result.tool for result in kept] == tools[:cap]


def test_turns_older_than_the_stored_turn_ceiling_are_dropped(registry):
    ceiling = runtime().conversations.max_stored_result_turns
    thread = registry.create_thread(ALICE, "a long conversation")
    for turn in (1, 2, ceiling + 1):
        registry.record_tool_results(
            ALICE, thread.thread_id, [_chart(turn)]
        )

    kept = registry.thread_tool_results(ALICE, thread.thread_id)

    assert [result.turn for result in kept] == [2, ceiling + 1]


def test_recording_a_turn_again_replaces_what_it_stored(registry):
    thread = registry.create_thread(ALICE, "a retried turn")
    registry.record_tool_results(
        ALICE,
        thread.thread_id,
        [_query(1), _chart(1)],
    )

    registry.record_tool_results(
        ALICE, thread.thread_id, [ToolResult(1, "get_stats", {"rows": []})]
    )

    kept = registry.thread_tool_results(ALICE, thread.thread_id)
    assert [result.tool for result in kept] == ["get_stats"]


def test_deleting_a_thread_deletes_its_tool_results(registry):
    thread = registry.create_thread(ALICE, "to be deleted")
    registry.record_tool_results(ALICE, thread.thread_id, [_chart(1)])

    registry.delete_thread(ALICE, thread.thread_id)

    assert registry.thread_tool_results(ALICE, thread.thread_id) == []


def test_a_foreign_delete_leaves_the_tool_results_alone(registry):
    neighbour = registry.create_thread(DAVE, "same tenant, other user")
    registry.record_tool_results(
        DAVE, neighbour.thread_id, [_chart(1)]
    )

    with pytest.raises(NotFound):
        registry.delete_thread(ALICE, neighbour.thread_id)

    assert len(registry.thread_tool_results(DAVE, neighbour.thread_id)) == 1


def test_stored_tool_result_row_carries_the_owning_identity(registry, tmp_path):
    thread = registry.create_thread(ALICE, "owned")
    registry.record_tool_results(ALICE, thread.thread_id, [_chart(3)])

    with sqlite3.connect(tmp_path / "state.db") as conn:
        row = conn.execute(
            "SELECT sub, tenant_id, turn, position, tool, created FROM tool_results "
            "WHERE thread_id = ?",
            (thread.thread_id,),
        ).fetchone()

    assert row == (ALICE.sub, ALICE.tenant_id, 3, 0, "plot", PINNED_NOW.isoformat())


def test_tool_results_persist_in_the_same_state_file(tmp_path):
    state_db = tmp_path / "state.db"
    registry = ConversationRegistry(state_db, clock=lambda: PINNED_NOW)
    thread = registry.create_thread(ALICE, "kept")
    registry.record_tool_results(ALICE, thread.thread_id, [_chart(1)])

    reopened = ConversationRegistry(state_db).thread_tool_results(ALICE, thread.thread_id)

    assert reopened == [ToolResult(1, "plot", CHART_PAYLOAD)]


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
