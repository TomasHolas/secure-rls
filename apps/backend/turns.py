"""Turn history: what one turn's trace events become in the store (ADR 0012 as amended, #90).

A reopened thread must replay the conversation that happened - the reasoning, every tool call with
the arguments the model wrote, each call's one outcome, the retries and the refusals, and how the
turn ended - because auditability is what this product claims. That is the same event stream
`/chat` puts on the wire, so this module reduces it rather than describing it a second time: what
it keeps is ADR 0012 trace events, keyed exactly as the live stream keys them, and one fold in the
SPA renders a replayed turn and a live one.

`TurnLog` is fed every frame that goes on the wire and hands the store what history keeps of it:

- `token` is dropped. The answer is already the transcript's, and the terminal frame carries it
  whole - a third copy would be the largest thing stored and would replay nothing new.
- `node_start` is kept for the `reason` node only. It is what tells a reader's fold which model
  round the thinking and the calls that follow belong to (ADR 0012 as amended after issue #87);
  the other nodes render nothing at all, so storing them would be storing our own mechanics.
- `reasoning` is kept as one event per model round, the round's chunks concatenated. A single live
  turn produced 1175 reasoning frames; the round's text is the unit a reader reads, and it is cut
  to `conversations.max_reasoning_chars` with the cut stated on the event rather than hidden.
- `tool_call`, `retry` and `security_event` are kept verbatim. The arguments are model output and
  are stored as data: nothing here executes them, and the SPA renders them as text (OWASP LLM05).
- `tool_result` keeps its id, its tool and its server-produced payload, with the row-shaped lists
  cut to the executor's own result cap (ADR 0007) and the model-facing rendering dropped - that
  text is a second copy of the same rows, for a reader who is not there any more. What is kept
  instead of that text is `withheld`, the lines the model's copy of it lost to the per-reply cap
  (ADR 0007 as amended, issue #142), so a replayed turn still states that the model read less than
  the table beside it rather than reading as though it saw all of it. The outcome
  event is kept even when its payload is not, because issue #66's invariant is that every call has
  exactly one outcome and a stored turn that hid one would read as a call still running.
- `done` is always kept, ceiling or no ceiling: it is the turn's status, its telemetry and the
  prompt-guardrail position that produced it (ADR 0011 as amended), and a turn that could not say
  how it ended is the one thing a history must not do.

Bounded, because "persist the whole turn" is otherwise unbounded growth in a store that is served
in one response. `conversations.max_turn_events` ceils the events of one turn,
`conversations.max_turn_payloads` how many of its tool results keep their data, `db.max_result_rows`
the rows inside one of those payloads, and `conversations.max_reasoning_chars` one round's
thinking. Every piece a cap refuses is counted in `cut`, so a replayed turn states that it is
partial instead of reading as whole.

Writing is the one lenient path in this codebase and stays explicit (`close`): by then the answer
has streamed, so a storage failure is logged - saying what was lost - and swallowed rather than
turning a finished turn into a failed one. The read path in `conversations.py` is not lenient: a
history that cannot be reconstructed raises.
"""

import logging
from collections.abc import Callable

from agent import (
    EVENT_DONE,
    EVENT_NODE_START,
    EVENT_REASONING,
    EVENT_TOKEN,
    EVENT_TOOL_RESULT,
    REASON,
    TraceEvent,
)
from runtime import runtime

_LOG = logging.getLogger(__name__)

_STORAGE_FAILED = "the turn's history was not stored: %d events and %d capped pieces are lost"

# The payload keys that carry one row per element, and are therefore cut to the result cap.
_ROW_KEYS = frozenset({"rows", "anomalies"})
# The model-facing rendering of a result is not stored; the key stays so the shape is complete.
_NO_CONTENT = ""


class TurnLog:
    """Collects one turn's trace events and stores what history keeps of them when it ends."""

    def __init__(self, store: Callable[[list[dict[str, object]], int], None]) -> None:
        """Take the sink the finished history is handed: the events kept, and the pieces cut."""
        self._store = store
        self._events: list[dict[str, object]] = []
        self._cut = 0
        self._payloads = 0
        self._thought = ""
        self._truncated = False

    def add(self, event: TraceEvent) -> None:
        """Fold one trace event into the turn's history, applying the caps as it goes."""
        kind = event["type"]
        if kind == EVENT_TOKEN:
            return
        if kind == EVENT_REASONING:
            self._think(str(event["text"]))
            return
        self._flush_reasoning()
        if kind == EVENT_NODE_START:
            if event["node"] == REASON:
                self._keep(dict(event))
            return
        if kind == EVENT_DONE:
            self._events.append(dict(event))
            return
        if kind == EVENT_TOOL_RESULT:
            self._keep(_result(event, self._payload_kept()))
            return
        self._keep(dict(event))

    def close(self) -> None:
        """Store the turn, logging and swallowing a failure: the answer has already streamed."""
        self._flush_reasoning()
        if not self._events:
            return
        try:
            self._store(self._events, self._cut)
        except Exception:
            _LOG.exception(_STORAGE_FAILED, len(self._events), self._cut)

    def _think(self, text: str) -> None:
        """Accumulate one model round's thinking, up to the character cap it is kept to."""
        room = runtime().conversations.max_reasoning_chars - len(self._thought)
        if len(text) > room:
            self._truncated = True
        self._thought += text[:room] if room > 0 else ""

    def _flush_reasoning(self) -> None:
        """Close the round's thinking into one event, in the position the round started at."""
        if not self._thought:
            self._truncated = False
            return
        self._keep(
            {"type": EVENT_REASONING, "text": self._thought, "truncated": self._truncated}
        )
        self._thought = ""
        self._truncated = False

    def _keep(self, event: dict[str, object]) -> None:
        """Append one event unless the turn's ceiling is spent, in which case it is counted."""
        if len(self._events) >= runtime().conversations.max_turn_events:
            self._cut += 1
            return
        self._events.append(event)

    def _payload_kept(self) -> bool:
        """Whether this tool result still keeps its payload; a refused one is counted as cut."""
        self._payloads += 1
        if self._payloads <= runtime().conversations.max_turn_payloads:
            return True
        self._cut += 1
        return False


def _result(event: TraceEvent, with_payload: bool) -> dict[str, object]:
    """One tool result as history keeps it: the call it settles, and its capped server payload."""
    return {
        "type": EVENT_TOOL_RESULT,
        "id": event["id"],
        "tool": event["tool"],
        "content": _NO_CONTENT,
        "withheld": event["withheld"],
        "data": _capped(dict(event["data"])) if with_payload else {},
    }


def _capped(data: dict[str, object]) -> dict[str, object]:
    """The payload with its row-shaped lists cut to the executor's result cap (ADR 0007)."""
    window = runtime().db.max_result_rows
    return {
        key: value[:window] if key in _ROW_KEYS and isinstance(value, list) else value
        for key, value in data.items()
    }
