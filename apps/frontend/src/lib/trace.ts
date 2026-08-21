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
 *
 * Two things a turn carries besides its items: the reasoning streamed inside a step, which
 * accumulates on that step and never on the answer, and what the turn cost, which arrives
 * with the terminal frame (ADR 0012 as amended).
 *
 * `replayTurns` folds the other source of the same shape: what `GET /conversations/{id}` still
 * remembers of a reopened thread. It produces the very same `Turn` objects a stream produces,
 * so a past turn renders through the bricks a live one does instead of through a second
 * renderer. Only what the server stores can be in them - the questions, the answers and the
 * tool evidence; the reasoning, the retries and the graph steps were the transport of that turn
 * and are gone (ADR 0012 as amended).
 */

import type { Message, ToolResultRecord } from "./api";
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

/** The node the model reasons in; the fallback owner of reasoning that arrives before a step. */
const REASON_NODE = "reason";

/** The transcript role of a question, as `agent.thread_messages` labels it. */
const USER_ROLE = "user";

/** What separates two things the assistant said inside one replayed turn. */
const ANSWER_SEPARATOR = "\n\n";

/** A graph step, holding the reasoning streamed while the agent was inside it. */
export interface NodeItem {
  kind: "node";
  node: string;
  reasoning: string;
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
 * `streaming` until a `done` event lands. `cut_short` is a turn a per-turn bound stopped, which
 * may still carry the words the model got out before it. `failed` is a turn that never reached an
 * answer - the backend saying so in its terminal frame, or a stream this client could not read.
 * `replayed` is a turn read back from the server: how it ended is not stored, so it claims
 * nothing about it.
 */
export type TurnPhase =
  | "streaming"
  | "ok"
  | "blocked"
  | "gave_up"
  | "cut_short"
  | "failed"
  | "replayed";

/** What the turn cost: the summed usage of its model calls and the seconds it ran. */
export interface TurnUsage {
  inputTokens: number;
  outputTokens: number;
  durationS: number;
}

export interface Turn {
  question: string;
  answer: string;
  items: TraceItem[];
  phase: TurnPhase;
  model: string | null;
  usage: TurnUsage | null;
  error: string | null;
}

export function startTurn(question: string): Turn {
  return {
    question,
    answer: "",
    items: [],
    phase: "streaming",
    model: null,
    usage: null,
    error: null,
  };
}

/** The turn after one trace event; the input turn is never mutated. */
export function applyEvent(turn: Turn, event: TraceEvent): Turn {
  switch (event.type) {
    case "node_start":
      return { ...turn, items: appendNode(turn.items, event.node) };
    case "token":
      return { ...turn, answer: turn.answer + event.text };
    case "reasoning":
      return { ...turn, items: appendReasoning(turn.items, event.text) };
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
 * A reopened thread as turns: each question opens one, the assistant text that follows is its
 * answer, and the tool results the server stored for that turn become its trace items.
 *
 * The turn a stored result belongs to is the ordinal of the question that asked for it, which is
 * why the questions are counted here rather than the array indexed - a transcript that somehow
 * starts mid-turn then attaches its evidence to nothing instead of to the wrong answer. A stored
 * result carries no arguments and no model-facing text, only the payload the server produced, so
 * the step shows the SQL pair, the table or the chart and nothing it would have to invent.
 */
export function replayTurns(messages: Message[], results: ToolResultRecord[]): Turn[] {
  const turns: Turn[] = [];
  const numbers: number[] = [];
  let asked = 0;
  for (const message of messages) {
    const question = message.role === USER_ROLE;
    if (question) asked += 1;
    if (question || turns.length === 0) {
      turns.push({ ...startTurn(question ? message.content : ""), phase: "replayed" });
      numbers.push(asked);
    }
    if (!question) {
      const last = turns.length - 1;
      turns[last] = { ...turns[last], answer: joinAnswer(turns[last].answer, message.content) };
    }
  }
  return turns.map((turn, index) => ({ ...turn, items: replayItems(results, numbers[index]) }));
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
    usage: {
      inputTokens: event.input_tokens,
      outputTokens: event.output_tokens,
      durationS: event.duration_s,
    },
    error: failed ? event.answer : turn.error,
  };
}

/**
 * How fast the answer came out, or null when the turn produced nothing to divide - a refusal
 * with no model output, or a duration too short to have been measured.
 */
export function tokensPerSecond(usage: TurnUsage | null): number | null {
  if (!usage || usage.outputTokens <= 0 || usage.durationS <= 0) return null;
  return usage.outputTokens / usage.durationS;
}

/** The stored results of one turn as call items, in the order the server replayed them. */
function replayItems(results: ToolResultRecord[], turn: number): TraceItem[] {
  return results
    .filter((result) => result.turn === turn)
    .map((result, index) => {
      const id = `replay-${turn}-${index}`;
      return {
        kind: "call",
        id,
        tool: result.tool,
        args: {},
        outcome: { type: "tool_result", id, tool: result.tool, content: "", data: result.data },
      };
    });
}

/** Two things the assistant said in one turn read as two paragraphs, as they did while streaming. */
function joinAnswer(answer: string, text: string): string {
  return answer ? `${answer}${ANSWER_SEPARATOR}${text}` : text;
}

function callItem(event: ToolCallEvent): CallItem {
  return { kind: "call", id: event.id, tool: event.tool, args: event.args, outcome: null };
}

/** Consecutive entries into the same node are one step; the graph re-enters `reason` often. */
function appendNode(items: TraceItem[], node: string): TraceItem[] {
  const last = items[items.length - 1];
  if (last && last.kind === "node" && last.node === node) return items;
  return [...items, { kind: "node", node, reasoning: "" }];
}

/**
 * Reasoning belongs to the step the agent was in when it thought it, so it accumulates on the
 * trailing node item. The backend announces the node before it streams a word, so the tail is
 * that step; reasoning that somehow arrives first opens the step it can only have come from.
 */
function appendReasoning(items: TraceItem[], text: string): TraceItem[] {
  const last = items[items.length - 1];
  if (!last || last.kind !== "node") {
    return [...items, { kind: "node", node: REASON_NODE, reasoning: text }];
  }
  const next = [...items];
  next[next.length - 1] = { ...last, reasoning: last.reasoning + text };
  return next;
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
