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
 * Only what a reader of a trace cares about becomes an item: the model's thinking, the calls and
 * their evidence, the retries and the refusals. A `node_start` is transport and audit, never a row
 * (ADR 0012 as amended after issue #87) - it says which model round the turn is in, which is what
 * groups reasoning, and produces nothing on its own.
 *
 * Two things a turn carries besides its items: the round it is in, so however many reasoning
 * chunks stream inside one model call become one step and the round after the tool results becomes
 * its own, and what the turn cost, which arrives with the terminal frame (ADR 0012 as amended).
 *
 * `replayTurns` folds the other source of the same shape: the turn history
 * `GET /conversations/{id}` serves for a reopened thread. It is not a second implementation - the
 * server stores the same trace events it streamed, so each past turn is folded by running
 * `applyEvent` over them, and a replayed turn is the same `Turn` object a live one is (ADR 0012
 * as amended, issue #90). What differs is only what cannot be stored: the token-by-token arrival
 * of the answer, and the client's own measurement of how long a thought took.
 */

import type { Message, TurnRecord } from "./api";
import type {
  DoneEvent,
  ReasoningEvent,
  RetryEvent,
  SecurityEvent,
  ToolCallEvent,
  ToolResultEvent,
  TraceEvent,
} from "./sse";

/** The one event that closes a call: its result, the retry it triggered, or its refusal. */
export type CallOutcome = ToolResultEvent | RetryEvent | SecurityEvent;

/** The node the model reasons in: entering it is what opens the turn's next model round. */
const REASON_NODE = "reason";

/** The round of the first model call, and the round reasoning that arrives before one belongs to. */
export const FIRST_ROUND = 1;

/** The transcript role of a question, as `agent.thread_messages` labels it. */
const USER_ROLE = "user";

/** What separates two things the assistant said inside one replayed turn. */
const ANSWER_SEPARATOR = "\n\n";

/**
 * The model's own thinking from one model round, whatever number of chunks it streamed in.
 *
 * `startedAt` and `endedAt` are this client's clock, not the server's: the stream carries no
 * timestamps, so what is measured is the span over which the round's thinking arrived here.
 * `endedAt` is null while it is still arriving, which is what makes the step render as live, and
 * `startedAt` is null for a replayed round, which never arrived here at all - the step is settled
 * and claims no duration rather than inventing one.
 *
 * `truncated` is the server saying it kept this round's thinking up to its character cap and no
 * further, so a shortened thought is stated as shortened.
 */
export interface ReasoningItem {
  kind: "reasoning";
  round: number;
  text: string;
  truncated: boolean;
  startedAt: number | null;
  endedAt: number | null;
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

export type TraceItem = ReasoningItem | CallItem | OrphanItem;

/**
 * `streaming` until a `done` event lands. `cut_short` is a turn a per-turn bound stopped, which
 * may still carry the words the model got out before it. `failed` is a turn that never reached an
 * answer - the backend saying so in its terminal frame, or a stream this client could not read.
 * `replayed` is a turn read back from the server whose terminal frame is not among what was kept -
 * an old turn, or one whose history the retention ceiling dropped: it claims nothing about how it
 * ended, which is also why its `grounded` and `guardrails` stay null rather than false. A replayed
 * turn that does carry its frame ends in the status that frame reported, like the live turn it was.
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
  round: number;
  phase: TurnPhase;
  model: string | null;
  usage: TurnUsage | null;
  grounded: boolean | null;
  /** The prompt-guardrail position the terminal frame reported; null until it lands, and for a turn whose frame was not kept. */
  guardrails: boolean | null;
  /** Pieces of this turn's history the server's caps refused; always 0 for a turn watched live. */
  cut: number;
  error: string | null;
}

export function startTurn(question: string): Turn {
  return {
    question,
    answer: "",
    items: [],
    round: 0,
    phase: "streaming",
    model: null,
    usage: null,
    grounded: null,
    guardrails: null,
    cut: 0,
    error: null,
  };
}

/**
 * The turn after one trace event; the input turn is never mutated. `now` is the clock the
 * reasoning spans are measured against and is injectable so a scripted sequence can assert them.
 * A replay passes null: those events did not arrive here, so no span is measured for them.
 */
export function applyEvent(
  turn: Turn,
  event: TraceEvent,
  now: number | null = Date.now(),
): Turn {
  switch (event.type) {
    case "node_start":
      return event.node === REASON_NODE ? { ...turn, round: turn.round + 1 } : turn;
    case "token":
      return { ...turn, answer: turn.answer + event.text };
    case "reasoning": {
      const round = Math.max(turn.round, FIRST_ROUND);
      return { ...turn, round, items: appendReasoning(turn.items, event, round, now) };
    }
    case "tool_call":
      return { ...turn, items: [...settle(turn.items, now), callItem(event)] };
    case "tool_result":
    case "retry":
    case "security_event":
      return { ...turn, items: attachOutcome(settle(turn.items, now), event) };
    case "done":
      return done(turn, event, now);
  }
}

/** A turn whose stream broke: an unreachable endpoint or a rejected request, stated as such. */
export function failTurn(turn: Turn, error: string, now: number = Date.now()): Turn {
  return { ...turn, phase: "failed", error, items: settle(turn.items, now) };
}

/**
 * A reopened thread as turns: each question opens one, the assistant text that follows is its
 * answer, and the trace the server stored for that turn is folded onto it by the same
 * `applyEvent` a live stream is folded by - one renderer, and one fold in front of it.
 *
 * The turn a stored record belongs to is the ordinal of the question that opened it, which is why
 * the questions are counted here rather than the array indexed: a transcript that somehow starts
 * mid-turn then attaches its history to nothing instead of to the wrong answer. The answer comes
 * from the transcript and the terminal frame does not overwrite it - it is the same text, and the
 * transcript is the copy the server composed the thread's memory from.
 */
export function replayTurns(messages: Message[], history: TurnRecord[]): Turn[] {
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
  return turns.map((turn, index) =>
    replayTurn(turn, history.find((record) => record.turn === numbers[index])),
  );
}

/** One past turn: its stored events folded onto it, and what the caps cut out of them. */
function replayTurn(turn: Turn, record: TurnRecord | undefined): Turn {
  if (!record) return turn;
  const folded = record.events.reduce((state, event) => applyEvent(state, event, null), turn);
  return { ...folded, cut: record.cut };
}

/**
 * The turn as its terminal frame leaves it. A `failed` status is the backend's own diagnosis
 * of a run that never answered, so it goes to `error` and whatever text streamed before it
 * stays the answer - the view states the diagnosis instead of guessing at one.
 *
 * A frame that does not state the prompt-guardrail position leaves it null: the frames arrive as
 * unvalidated JSON, and a missing field must read as "unknown" rather than as either mode.
 */
function done(turn: Turn, event: DoneEvent, now: number | null): Turn {
  const failed = event.status === "failed";
  return {
    ...turn,
    items: settle(turn.items, now),
    answer: failed ? turn.answer : turn.answer || event.answer,
    phase: event.status,
    model: event.model,
    usage: {
      inputTokens: event.input_tokens,
      outputTokens: event.output_tokens,
      durationS: event.duration_s,
    },
    grounded: event.grounded,
    guardrails: typeof event.prompt_guardrails === "boolean" ? event.prompt_guardrails : null,
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

/** Two things the assistant said in one turn read as two paragraphs, as they did while streaming. */
function joinAnswer(answer: string, text: string): string {
  return answer ? `${answer}${ANSWER_SEPARATOR}${text}` : text;
}

function callItem(event: ToolCallEvent): CallItem {
  return { kind: "call", id: event.id, tool: event.tool, args: event.args, outcome: null };
}

/**
 * Reasoning is one step per model round: however many chunks stream inside one call to the model
 * they accumulate on that round's step, and the round the agent enters after tool results opens a
 * step of its own, so a reader can tell the thinking before the tools from the thinking about what
 * they returned. A step exists only once there is thinking in it - a round that showed none is no
 * row at all.
 */
function appendReasoning(
  items: TraceItem[],
  event: ReasoningEvent,
  round: number,
  now: number | null,
): TraceItem[] {
  const truncated = event.truncated === true;
  const last = items[items.length - 1];
  if (!last || last.kind !== "reasoning" || last.round !== round) {
    const started: ReasoningItem = {
      kind: "reasoning",
      round,
      text: event.text,
      truncated,
      startedAt: now,
      endedAt: null,
    };
    return [...settle(items, now), started];
  }
  const next = [...items];
  next[next.length - 1] = {
    ...last,
    text: last.text + event.text,
    truncated: last.truncated || truncated,
  };
  return next;
}

/**
 * Whatever thinking was still arriving stops arriving here: the next thing the turn did, or its
 * end, is the moment that round's thinking finished. Only the newest step can still be open, so
 * this closes the last item and leaves every earlier one alone. A replay measures nothing, so
 * there is nothing to close: its steps were settled before they were stored.
 */
function settle(items: TraceItem[], now: number | null): TraceItem[] {
  const last = items[items.length - 1];
  if (now === null || !last || last.kind !== "reasoning" || last.endedAt !== null) return items;
  const next = [...items];
  next[next.length - 1] = { ...last, endedAt: now };
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
