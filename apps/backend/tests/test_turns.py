"""Turn-history reduction tests (issue #90, ADR 0012 as amended).

What a reader gets back from a reopened thread is decided here, so this suite asserts the shape
rather than describing it: which events history keeps and which it drops, that one model round's
thinking becomes one event however many frames it streamed in, that every announced call keeps its
one outcome, and that the terminal frame survives whatever else a cap refused.

The caps are asserted against the configured values, never against literals, so a knob change in
`runtime.json` moves the tests with it. `cut` is the honesty field: every piece a cap refuses is
counted in it, which is what lets a replayed turn state that it is partial.

The store is a list here, not SQLite: what belongs to this module is the reduction, and the write
it feeds is `test_conversations.py`'s. The one storage property that is this module's own is the
lenient write - a sink that raises is logged and swallowed, because by then the answer has
streamed.
"""

import logging

import pytest

from runtime import runtime
from turns import TurnLog

GENERATED_SQL = "SELECT department, COUNT(*) FROM employees GROUP BY department"
EXECUTED_SQL = (
    "SELECT department, COUNT(*) FROM (SELECT * FROM employees WHERE tenant_id = ?) "
    "GROUP BY department"
)
DONE = {
    "type": "done",
    "status": "ok",
    "answer": "six of them",
    "grounded": True,
    "model": "fake-model:1b",
    "prompt_guardrails": False,
    "input_tokens": 250,
    "output_tokens": 28,
    "duration_s": 1.75,
}


@pytest.fixture
def stored():
    """The sink `TurnLog` writes to: whatever the finished history turned out to be."""
    return []


@pytest.fixture
def log(stored):
    """A log whose store appends the events and the cut count it was handed."""
    return TurnLog(lambda events, cut: stored.append((events, cut)))


def _tool_result(call_id: str = "c1", rows: int = 1) -> dict:
    """One `tool_result` frame as the agent yields it, with a row window of the asked-for size."""
    return {
        "type": "tool_result",
        "id": call_id,
        "tool": "query_db",
        "content": "department | count",
        "data": {
            "generated_sql": GENERATED_SQL,
            "executed_sql": EXECUTED_SQL,
            "columns": ["department", "count"],
            "rows": [["Engineering", index] for index in range(rows)],
            "total_count": rows,
            "returned_count": rows,
            "truncated": False,
        },
    }


def _feed(log, events) -> None:
    """Play a whole turn through the log, the way `_sse` does frame by frame."""
    for event in events:
        log.add(event)
    log.close()


def test_a_turn_keeps_its_calls_outcomes_reasoning_and_terminal_frame(log, stored):
    _feed(
        log,
        [
            {"type": "node_start", "node": "reason"},
            {"type": "reasoning", "text": "count "},
            {"type": "reasoning", "text": "the rows"},
            {"type": "node_start", "node": "validate"},
            {"type": "tool_call", "id": "c1", "tool": "query_db", "args": {"sql": GENERATED_SQL}},
            {"type": "node_start", "node": "execute_tool"},
            _tool_result(),
            {"type": "node_start", "node": "audit"},
            {"type": "node_start", "node": "reason"},
            {"type": "reasoning", "text": "now the answer"},
            {"type": "token", "text": "six of them"},
            {"type": "node_start", "node": "respond"},
            DONE,
        ],
    )

    events, cut = stored[0]
    assert cut == 0
    assert [event["type"] for event in events] == [
        "node_start",
        "reasoning",
        "tool_call",
        "tool_result",
        "node_start",
        "reasoning",
        "done",
    ]
    assert events[1] == {"type": "reasoning", "text": "count the rows", "truncated": False}
    assert events[2]["args"] == {"sql": GENERATED_SQL}
    assert events[-1] == DONE


def test_only_the_reason_node_is_kept_because_it_is_what_groups_the_rounds(log, stored):
    _feed(
        log,
        [
            {"type": "node_start", "node": "validate"},
            {"type": "node_start", "node": "execute_tool"},
            {"type": "node_start", "node": "audit"},
            {"type": "node_start", "node": "respond"},
            DONE,
        ],
    )

    assert [event["type"] for event in stored[0][0]] == ["done"]


def test_the_answer_text_is_not_stored_a_third_time(log, stored):
    _feed(log, [{"type": "token", "text": "six "}, {"type": "token", "text": "of them"}, DONE])

    events, _ = stored[0]
    assert [event["type"] for event in events] == ["done"]
    assert events[0]["answer"] == "six of them"


def test_a_token_between_two_thoughts_does_not_split_the_round(log, stored):
    _feed(
        log,
        [
            {"type": "node_start", "node": "reason"},
            {"type": "reasoning", "text": "first "},
            {"type": "token", "text": "partial"},
            {"type": "reasoning", "text": "second"},
            DONE,
        ],
    )

    events, _ = stored[0]
    assert [event["type"] for event in events] == ["node_start", "reasoning", "done"]
    assert events[1]["text"] == "first second"


def test_each_model_round_keeps_its_own_thinking_in_order(log, stored):
    _feed(
        log,
        [
            {"type": "node_start", "node": "reason"},
            {"type": "reasoning", "text": "before the tool"},
            {"type": "tool_call", "id": "c1", "tool": "get_stats", "args": {"metric": "count"}},
            {"type": "node_start", "node": "reason"},
            {"type": "reasoning", "text": "after the tool"},
            DONE,
        ],
    )

    events, _ = stored[0]
    assert [event.get("text") for event in events if event["type"] == "reasoning"] == [
        "before the tool",
        "after the tool",
    ]


def test_one_rounds_thinking_is_cut_to_the_configured_cap_and_says_so(log, stored):
    cap = runtime().conversations.max_reasoning_chars
    frames = [{"type": "reasoning", "text": "x" * 100} for _ in range(cap // 100 + 5)]

    _feed(log, [{"type": "node_start", "node": "reason"}, *frames, DONE])

    thinking = stored[0][0][1]
    assert len(thinking["text"]) == cap
    assert thinking["truncated"] is True


def test_thinking_that_fits_is_not_marked_as_cut(log, stored):
    _feed(log, [{"type": "reasoning", "text": "short"}, DONE])

    assert stored[0][0][0] == {"type": "reasoning", "text": "short", "truncated": False}


def test_a_retry_and_a_refusal_are_kept_with_their_layer_kind_and_reason(log, stored):
    retry = {
        "type": "retry",
        "id": "c1",
        "tool": "query_db",
        "layer": "query validation",
        "kind": "malformed_sql",
        "attempt": 1,
        "max_attempts": 3,
        "reason": "the statement did not parse",
    }
    refusal = {
        "type": "security_event",
        "id": "c2",
        "tool": "query_db",
        "layer": "scoped execution",
        "kind": "policy_violation",
        "reason": "table sqlite_master is not allowlisted",
    }

    _feed(log, [retry, refusal, DONE])

    assert stored[0][0][:2] == [retry, refusal]


def test_a_stored_result_drops_the_model_facing_text_and_cuts_the_row_window(log, stored):
    window = runtime().db.max_result_rows

    _feed(log, [_tool_result(rows=window + 25), DONE])

    result = stored[0][0][0]
    assert result["content"] == ""
    assert len(result["data"]["rows"]) == window
    assert result["data"]["generated_sql"] == GENERATED_SQL
    assert result["data"]["executed_sql"] == EXECUTED_SQL


def test_results_past_the_payload_cap_keep_their_outcome_and_lose_their_payload(log, stored):
    cap = runtime().conversations.max_turn_payloads
    results = [_tool_result(call_id=f"c{index}") for index in range(cap + 2)]

    _feed(log, [*results, DONE])

    events, cut = stored[0]
    kept = [event for event in events if event["type"] == "tool_result"]
    assert len(kept) == cap + 2
    assert all(event["data"] for event in kept[:cap])
    assert all(event["data"] == {} for event in kept[cap:])
    assert [event["id"] for event in kept] == [result["id"] for result in results]
    assert cut == 2


def test_events_past_the_turn_ceiling_are_refused_and_counted(log, stored):
    ceiling = runtime().conversations.max_turn_events
    calls = [
        {"type": "tool_call", "id": f"c{index}", "tool": "get_stats", "args": {}}
        for index in range(ceiling + 7)
    ]

    _feed(log, [*calls, DONE])

    events, cut = stored[0]
    assert len(events) == ceiling + 1
    assert cut == 7


def test_the_terminal_frame_survives_a_spent_ceiling(log, stored):
    ceiling = runtime().conversations.max_turn_events
    calls = [
        {"type": "tool_call", "id": f"c{index}", "tool": "get_stats", "args": {}}
        for index in range(ceiling + 1)
    ]

    _feed(log, [*calls, DONE])

    events, _ = stored[0]
    assert events[-1] == DONE
    assert events[-1]["prompt_guardrails"] is False


def test_a_turn_that_produced_nothing_stores_nothing(log, stored):
    _feed(log, [{"type": "token", "text": "just words"}])

    assert stored == []


def test_a_broken_turn_stores_what_it_did_produce(log, stored):
    log.add({"type": "tool_call", "id": "c1", "tool": "plot", "args": {"kind": "bar"}})
    log.close()

    assert [event["type"] for event in stored[0][0]] == ["tool_call"]


def test_arguments_are_stored_as_written_however_hostile_they_look(log, stored):
    args = {"sql": "SELECT * FROM employees; DROP TABLE employees --", "note": "<script>x</script>"}

    _feed(log, [{"type": "tool_call", "id": "c1", "tool": "query_db", "args": args}, DONE])

    assert stored[0][0][0]["args"] == args


def test_a_failing_store_is_logged_and_never_raised_at_the_finished_turn(caplog):
    def explode(events, cut):
        """A store that fails the way a locked state file does."""
        raise RuntimeError("the state file is locked")

    log = TurnLog(explode)
    log.add(DONE)
    with caplog.at_level(logging.ERROR):
        log.close()

    assert "the turn's history was not stored" in caplog.text
