/**
 * The SSE brick: turns the streaming `POST /chat` response into typed trace events.
 *
 * The browser's `EventSource` cannot POST, so the SPA reads the body stream itself
 * (ADR 0012). This module owns exactly that: frame the byte stream, parse each
 * `data:` payload, hand the caller one typed event at a time. It never fetches -
 * `lib/api.ts` opens the response - and it never renders.
 *
 * The event shapes below mirror `apps/backend/agent.py`'s module docstring verbatim;
 * that docstring is the contract, this file is its TypeScript restatement.
 *
 * Robustness: a frame arriving split across chunks is buffered until its blank-line
 * terminator; a frame whose payload is not JSON with a `type` is reported to the
 * console and skipped, so one bad frame cannot end an otherwise healthy turn; a
 * consumer that stops early cancels the body on the way out, and a cancelled stream
 * ends the generator quietly instead of raising. A run that breaks server-side arrives
 * as a `done` event with status `failed` rather than as a body that simply stops.
 */

import type { ChartSpec } from "../components/charts";

export interface NodeStartEvent {
  type: "node_start";
  node: string;
}

export interface TokenEvent {
  type: "token";
  text: string;
}

/**
 * One chunk of the model's own thinking, streamed as it arrives. It belongs to the trace and
 * never to the answer: the backend splits it out of the text before a token is ever emitted.
 */
export interface ReasoningEvent {
  type: "reasoning";
  text: string;
}

export interface ToolCallEvent {
  type: "tool_call";
  id: string;
  tool: string;
  args: Record<string, unknown>;
}

/** One retrieved note, as `rag.search_notes_scoped` returns it. */
export interface NoteMatch {
  user_id: number;
  name: string;
  note: string;
  distance: number;
}

/** Keyed by what the tool returns; every key is optional because each tool fills its own. */
export interface ToolResultData {
  generated_sql?: string;
  executed_sql?: string;
  columns?: string[];
  rows?: unknown[][];
  total_count?: number;
  returned_count?: number;
  truncated?: boolean;
  chart_spec?: ChartSpec;
  anomalies?: Record<string, unknown>[];
  notes?: NoteMatch[];
}

export interface ToolResultEvent {
  type: "tool_result";
  id: string;
  tool: string;
  content: string;
  data: ToolResultData;
}

export interface SecurityEvent {
  type: "security_event";
  id: string;
  tool: string;
  layer: string;
  kind: string;
  reason: string;
}

export interface RetryEvent {
  type: "retry";
  id: string;
  tool: string;
  layer: string;
  kind: string;
  attempt: number;
  max_attempts: number;
  reason: string;
}

/**
 * How a turn ended. The agent composes the first four - `cut_short` is a turn one of its
 * per-turn bounds stopped, its time limit or its tool-round cap (ADR 0011 as amended) - and
 * `failed` is the API's terminal frame for a run that broke before it could answer, with the
 * reason in `answer`.
 */
export type TurnStatus = "ok" | "blocked" | "gave_up" | "cut_short" | "failed";

/**
 * How a turn ended and what it cost: the summed usage of its model calls and the wall-clock
 * seconds it ran. A `failed` frame reports the seconds it managed and no tokens.
 */
export interface DoneEvent {
  type: "done";
  status: TurnStatus;
  answer: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  duration_s: number;
}

export type TraceEvent =
  | NodeStartEvent
  | TokenEvent
  | ReasoningEvent
  | ToolCallEvent
  | ToolResultEvent
  | SecurityEvent
  | RetryEvent
  | DoneEvent;

const FRAME_SEPARATOR = /\r?\n\r?\n/;
const LINE_SEPARATOR = /\r?\n/;
const DATA_FIELD = "data:";
const NO_STREAM = "the chat response carries no body to stream";

/** Reads the response body to exhaustion, yielding each trace event as its frame completes. */
export async function* readTraceEvents(response: Response): AsyncGenerator<TraceEvent> {
  if (!response.body) throw new Error(NO_STREAM);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(FRAME_SEPARATOR);
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const event = parseFrame(frame);
        if (event) yield event;
      }
    }
    const tail = parseFrame(buffer + decoder.decode());
    if (tail) yield tail;
  } catch (error) {
    if (!isAbort(error)) throw error;
  } finally {
    // Cancelling releases the connection when the consumer stopped early; a stream that
    // already failed has nothing left to cancel.
    void reader.cancel().catch(() => undefined);
  }
}

/** One SSE frame to an event, or null when it carries no data or unreadable data. */
function parseFrame(frame: string): TraceEvent | null {
  const payload = frame
    .split(LINE_SEPARATOR)
    .filter((line) => line.startsWith(DATA_FIELD))
    .map((line) => line.slice(DATA_FIELD.length).replace(/^ /, ""))
    .join("\n");
  if (!payload) return null;
  const event = parseJson(payload);
  if (!event || typeof event.type !== "string") {
    console.warn(`secure-rls: skipping a malformed SSE frame: ${payload}`);
    return null;
  }
  return event as unknown as TraceEvent;
}

function parseJson(payload: string): { type?: unknown } | null {
  try {
    const parsed = JSON.parse(payload) as unknown;
    return typeof parsed === "object" && parsed !== null ? (parsed as { type?: unknown }) : null;
  } catch {
    return null;
  }
}

function isAbort(error: unknown): boolean {
  return (error as { name?: string } | null)?.name === "AbortError";
}
