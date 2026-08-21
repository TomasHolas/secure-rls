/**
 * The turn state brick: folds the ADR 0012 trace event stream into what one assistant
 * turn looks like on screen. Pure functions over plain data, so the whole rendering
 * contract is testable from a scripted event sequence without a model or a browser.
 *
 * The shape mirrors how the backend emits: `agent.py` writes exactly one outcome per
 * tool call id - a `tool_result`, a `retry` (the error was fed back, the model may try
 * again with a fresh call) or a `security_event` (terminal refusal) - so a call item
 * holds one nullable outcome rather than three lists. An outcome whose id was never
 * announced still becomes its own item: nothing the backend said is dropped.
 */

import type {
  DoneEvent,
  RetryEvent,
  SecurityEvent,
  ToolCallEvent,
  ToolResultEvent,
  TraceEvent,
} from "./sse";

/** The one event that closes a call: its result, the retry it triggered, or its refusal. */
export type CallOutcome = ToolResultEvent | RetryEvent | SecurityEvent;

export interface NodeItem {
  kind: "node";
  node: string;
}

export interface CallItem {
  kind: "call";
  id: string;
  tool: string;
  args: Record<string, unknown>;
  outcome: CallOutcome | null;
}

/** An outcome for a call the stream never announced - shown on its own rather than lost. */
export interface OrphanItem {
  kind: "orphan";
  outcome: CallOutcome;
}

export type TraceItem = NodeItem | CallItem | OrphanItem;

/**
 * `streaming` until a `done` event lands. `failed` is a turn that never reached an answer -
 * the backend saying so in its terminal frame, or a stream this client could not read.
 */
export type TurnPhase = "streaming" | "ok" | "blocked" | "gave_up" | "failed";

export interface Turn {
  question: string;
  answer: string;
  items: TraceItem[];
  phase: TurnPhase;
  model: string | null;
  error: string | null;
}

export function startTurn(question: string): Turn {
  return { question, answer: "", items: [], phase: "streaming", model: null, error: null };
}

/** The turn after one trace event; the input turn is never mutated. */
export function applyEvent(turn: Turn, event: TraceEvent): Turn {
  switch (event.type) {
    case "node_start":
      return { ...turn, items: appendNode(turn.items, event.node) };
    case "token":
      return { ...turn, answer: turn.answer + event.text };
    case "tool_call":
      return { ...turn, items: [...turn.items, callItem(event)] };
    case "tool_result":
    case "retry":
    case "security_event":
      return { ...turn, items: attachOutcome(turn.items, event) };
    case "done":
      return done(turn, event);
  }
}

/** A turn whose stream broke: an unreachable endpoint or a rejected request, stated as such. */
export function failTurn(turn: Turn, error: string): Turn {
  return { ...turn, phase: "failed", error };
}

/**
 * The turn as its terminal frame leaves it. A `failed` status is the backend's own diagnosis
 * of a run that never answered, so it goes to `error` and whatever text streamed before it
 * stays the answer - the view states the diagnosis instead of guessing at one.
 */
function done(turn: Turn, event: DoneEvent): Turn {
  const failed = event.status === "failed";
  return {
    ...turn,
    answer: failed ? turn.answer : turn.answer || event.answer,
    phase: event.status,
    model: event.model,
    error: failed ? event.answer : turn.error,
  };
}

function callItem(event: ToolCallEvent): CallItem {
  return { kind: "call", id: event.id, tool: event.tool, args: event.args, outcome: null };
}

/** Consecutive entries into the same node are one step; the graph re-enters `reason` often. */
function appendNode(items: TraceItem[], node: string): TraceItem[] {
  const last = items[items.length - 1];
  if (last && last.kind === "node" && last.node === node) return items;
  return [...items, { kind: "node", node }];
}

function attachOutcome(items: TraceItem[], outcome: CallOutcome): TraceItem[] {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind !== "call" || item.id !== outcome.id || item.outcome !== null) continue;
    const next = [...items];
    next[index] = { ...item, outcome };
    return next;
  }
  return [...items, { kind: "orphan", outcome }];
}
