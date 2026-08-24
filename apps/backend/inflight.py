"""In-flight turns (ADR 0012 as amended): the turn outlives the stream that watches it.

A turn used to be chained to its own HTTP response. `POST /chat` handed the runner's generator
straight to `StreamingResponse`, so the only thing pulling the graph forward was the client
reading the body. A reader who switched threads, reloaded or signed out mid-generation stopped
pulling, Starlette closed the generator, and the turn died wherever it happened to be - with no
terminal frame, so the thread replayed as a question whose answer was never stored. An
interrupted viewer must not be able to corrupt the record: the audit trail and the history are
the product, not a side effect of somebody watching.

So the turn runs in its own worker thread, drained to completion whatever the reader does, and
the stream is a window onto it. The worker feeds two consumers in this order: the recorder,
which is the source of the history and the audit trail, and a bounded queue the HTTP generator
reads frames from. Only forwarding is coupled to the reader - never running.

Two ways forwarding stops, and neither stops the turn:

- The reader goes away. The HTTP generator is closed by Starlette on disconnect and says so on
  its way out (`close`), after which every frame is dropped rather than queued.
- The reader cannot keep up. The queue is bounded by `api.turn_queue_frames`; a `put` that finds
  it full means nobody is draining, so forwarding is cut for the rest of the turn. It is cut
  whole rather than sampled: a reader given a stream with holes in the middle would render a
  turn that never happened, while a stream that stops is a state the SPA already models and
  states ("the stream ended before the turn finished"). The history, written from the worker,
  stays complete either way.

One turn per thread, enforced here (issue #143). Backgrounding a turn makes a second one on the
same thread reachable - the reader returns to a thread that is still answering and asks again -
and two turns interleaving on one checkpointer thread would corrupt the very memory this module
exists to protect. `start` claims the thread or raises `TurnBusy`, and the claim is released in
the worker's `finally`, so a turn that raised frees its thread exactly like one that answered.
`running` reports the claim, which is how `GET /conversations/{id}` can say a thread is still
answering instead of replaying a turn that has not been stored yet.

Nothing here bounds a turn: the wall-clock deadline and the tool-round cap of ADR 0011 are
inside the graph, so a backgrounded turn ends on its own schedule. The workers are daemon
threads - a process that is shutting down is not made to wait for a turn nobody is reading.
"""

import logging
import queue
import threading
from collections.abc import Iterator
from typing import cast

from agent import TraceEvent
from runtime import runtime

_LOG = logging.getLogger(__name__)

_TURN_BUSY = "this conversation is already answering a question"
_TURN_CRASHED = "the background turn for thread %s ended in an unhandled exception"
_READER_CUT = "the reader of thread %s is not draining its stream; forwarding stopped"

# Put on the queue when the worker is done, so the reader ends without polling for it.
_END = object()


class TurnBusy(Exception):
    """Raised when a thread already has a turn in flight; the API answers 409."""


class InFlightTurns:
    """The turns running right now, one worker each and at most one per thread."""

    def __init__(self) -> None:
        """Start with no turn in flight; the claims are guarded by one lock."""
        self._lock = threading.Lock()
        self._claimed: set[str] = set()

    def start(self, thread_id: str, turn: Iterator[TraceEvent]) -> Iterator[TraceEvent]:
        """Run turn in its own worker and return the reader's window onto its frames.

        The claim is taken before the worker starts, so two requests racing on one thread cannot
        both win it, and `turn` is not iterated here at all - a runner's generator body first
        executes on the worker thread, which is also the thread that opens its checkpointer.
        """
        window = _Window(thread_id)
        with self._lock:
            if thread_id in self._claimed:
                raise TurnBusy(_TURN_BUSY)
            self._claimed.add(thread_id)
        threading.Thread(
            target=self._drain, args=(thread_id, turn, window), daemon=True
        ).start()
        return window.frames()

    def running(self, thread_id: str) -> bool:
        """Whether a turn is in flight for that thread right now."""
        with self._lock:
            return thread_id in self._claimed

    def _drain(self, thread_id: str, turn: Iterator[TraceEvent], window: "_Window") -> None:
        """Pull the turn to its end, forwarding what the reader is still there for.

        The claim is released before the window is closed, in that order: the reader's stream
        ending is the signal a client asks its next question on, so a thread must never still
        read as busy at the moment its last frame lands.
        """
        try:
            for event in turn:
                window.forward(event)
        except Exception:
            _LOG.exception(_TURN_CRASHED, thread_id)
        finally:
            with self._lock:
                self._claimed.discard(thread_id)
            window.finish()


class _Window:
    """One turn's bounded view for its reader: frames in, frames out, and no back pressure.

    The queue is unbounded and the producer enforces the bound before each put, rather than the
    queue enforcing it by blocking: a full queue must never be able to hold up the turn, and a
    window that stops forwarding must still be able to tell its reader that it stopped. One
    thread writes it, so the size it reads is its own.
    """

    def __init__(self, thread_id: str) -> None:
        """Bound the reader's backlog by `api.turn_queue_frames` and open forwarding."""
        self._thread_id = thread_id
        self._limit = runtime().api.turn_queue_frames
        self._frames: queue.Queue[object] = queue.Queue()
        self._forwarding = True

    def forward(self, event: TraceEvent) -> None:
        """Offer one frame to the reader, dropping it once there is no reader to offer it to."""
        if not self._forwarding:
            return
        if self._frames.qsize() >= self._limit:
            _LOG.warning(_READER_CUT, self._thread_id)
            self.finish()
            return
        self._frames.put(event)

    def finish(self) -> None:
        """Close the reader's window once; the turn behind it runs on regardless."""
        if not self._forwarding:
            return
        self._forwarding = False
        self._frames.put(_END)

    def frames(self) -> Iterator[TraceEvent]:
        """Yield the turn's frames until it ends, and stop forwarding when the reader leaves."""
        try:
            while True:
                event = self._frames.get()
                if event is _END:
                    return
                yield cast(TraceEvent, event)
        finally:
            self._forwarding = False
