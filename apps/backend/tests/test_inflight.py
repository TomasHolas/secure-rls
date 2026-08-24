"""Suite for the in-flight turn broker (issue #143, ADR 0012 as amended).

The property under test is the one the defect was: a turn must reach its end whatever its reader
does. Closing the returned generator is exactly what Starlette does to the response body when a
browser disconnects - the SPA switching threads, reloading or signing out - so a test that closes
it is testing the disconnect and not an imitation of it.

The scripted turn is a generator that reports what it produced and sets an event when it reaches
its own end, so "the turn finished" is asserted from the turn rather than from a sleep. Every wait
is on an event with a timeout, so a broken claim or a lost frame fails the suite instead of
hanging it.

Network-free and model-free by construction: nothing here builds an app, opens a database or
reaches an endpoint - the broker takes an iterator of trace events and hands back an iterator of
trace events.
"""

import threading
import time
from dataclasses import dataclass, field, replace

import pytest

import inflight
import runtime as runtime_module
from inflight import InFlightTurns, TurnBusy

THREAD = "thread-1"
OTHER_THREAD = "thread-2"
# Long enough that a broken wait fails the suite; never reached when the code is correct.
TIMEOUT_S = 5.0


@dataclass
class ScriptedTurn:
    """A turn of `frames` events that records what it produced and says when it is over."""

    frames: int = 4
    produced: list[dict] = field(default_factory=list)
    finished: threading.Event = field(default_factory=threading.Event)
    gate: threading.Event | None = None

    def __iter__(self):
        """Yield the frames, holding at the gate after the first one when one was given."""
        try:
            for index in range(self.frames):
                event = {"type": "token", "text": f"frame {index}"}
                self.produced.append(event)
                yield event
                if index == 0 and self.gate is not None:
                    assert self.gate.wait(TIMEOUT_S)
            done = {"type": "done", "status": "ok", "answer": "the answer"}
            self.produced.append(done)
            yield done
        finally:
            self.finished.set()


@dataclass
class CrashingTurn:
    """A turn that yields one frame and then raises, the way a broken run reaches the worker."""

    finished: threading.Event = field(default_factory=threading.Event)

    def __iter__(self):
        """Yield one frame, then fail; the worker must still release the thread's claim."""
        try:
            yield {"type": "token", "text": "half a turn"}
            raise RuntimeError("the run broke")
        finally:
            self.finished.set()


@pytest.fixture
def turns() -> InFlightTurns:
    """A broker with nothing in flight."""
    return InFlightTurns()


def _small_queue(monkeypatch, frames: int) -> None:
    """Shrink `api.turn_queue_frames` for one test, the way a deployment's config would."""
    config = runtime_module.runtime()
    bounded = replace(config, api=replace(config.api, turn_queue_frames=frames))
    monkeypatch.setattr(inflight, "runtime", lambda: bounded)


def test_the_reader_sees_every_frame_in_order(turns):
    turn = ScriptedTurn()
    read = list(turns.start(THREAD, iter(turn)))
    assert turn.finished.wait(TIMEOUT_S)
    assert read == turn.produced


def test_the_turn_finishes_after_its_reader_disconnects(turns):
    """Closing the window is what Starlette does on disconnect; the turn must not notice."""
    turn = ScriptedTurn(frames=6)
    window = turns.start(THREAD, iter(turn))
    assert next(window)["text"] == "frame 0"
    window.close()
    assert turn.finished.wait(TIMEOUT_S)
    assert turn.produced[-1]["type"] == "done"
    assert len(turn.produced) == turn.frames + 1


def test_a_departed_reader_frees_the_thread_for_the_next_turn(turns):
    turn = ScriptedTurn()
    window = turns.start(THREAD, iter(turn))
    next(window)
    window.close()
    assert turn.finished.wait(TIMEOUT_S)
    _settled(turns, THREAD)
    assert list(turns.start(THREAD, iter(ScriptedTurn(frames=1))))[-1]["type"] == "done"


def test_a_second_turn_on_the_same_thread_is_refused_while_one_is_in_flight(turns):
    gate = threading.Event()
    turn = ScriptedTurn(gate=gate)
    window = turns.start(THREAD, iter(turn))
    assert next(window)["text"] == "frame 0"
    assert turns.running(THREAD) is True
    with pytest.raises(TurnBusy):
        turns.start(THREAD, iter(ScriptedTurn()))
    gate.set()
    assert list(window)[-1]["type"] == "done"


def test_another_thread_runs_its_own_turn_at_the_same_time(turns):
    gate = threading.Event()
    held = ScriptedTurn(gate=gate)
    window = turns.start(THREAD, iter(held))
    assert next(window)["text"] == "frame 0"
    assert list(turns.start(OTHER_THREAD, iter(ScriptedTurn(frames=1))))[-1]["type"] == "done"
    gate.set()
    assert list(window)[-1]["type"] == "done"


def test_a_turn_that_raises_releases_its_thread(turns):
    turn = CrashingTurn()
    assert [event["text"] for event in turns.start(THREAD, iter(turn))] == ["half a turn"]
    assert turn.finished.wait(TIMEOUT_S)
    _settled(turns, THREAD)
    assert turns.running(THREAD) is False


def test_a_reader_that_does_not_drain_is_cut_and_the_turn_runs_on(turns, monkeypatch):
    """Overflow stops forwarding whole rather than sampling; the turn is untouched by it."""
    backlog = 2
    _small_queue(monkeypatch, backlog)
    turn = ScriptedTurn(frames=20)
    window = turns.start(THREAD, iter(turn))
    assert turn.finished.wait(TIMEOUT_S)
    assert len(turn.produced) == turn.frames + 1
    read = list(window)
    assert [event["text"] for event in read] == [
        event["text"] for event in turn.produced[:backlog]
    ]


def _settled(turns: InFlightTurns, thread_id: str) -> None:
    """Wait for the worker to release its claim; the release trails the turn's own end."""
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        if not turns.running(thread_id):
            return
        time.sleep(0.01)
    raise AssertionError(f"{thread_id} is still claimed")
