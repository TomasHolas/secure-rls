/** SSE brick: frame splitting, chunk boundaries, malformed frames, early exit. */

import { afterEach, describe, expect, it, vi } from "vitest";

import { readTraceEvents } from "./sse";
import type { TraceEvent } from "./sse";

const encoder = new TextEncoder();
const COST = { input_tokens: 250, output_tokens: 28, duration_s: 2 };

function frame(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

function responseOf(...chunks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(stream);
}

async function collect(response: Response): Promise<TraceEvent[]> {
  const events: TraceEvent[] = [];
  for await (const event of readTraceEvents(response)) events.push(event);
  return events;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("readTraceEvents", () => {
  it("yields every frame of a chunk in order", async () => {
    const response = responseOf(
      frame({ type: "node_start", node: "reason" }) +
        frame({ type: "reasoning", text: "an average per group" }) +
        frame({ type: "token", text: "Average " }) +
        frame({ type: "token", text: "salary." }) +
        frame({ type: "done", status: "ok", answer: "Average salary.", model: "qwen3:8b", ...COST }),
    );

    const events = await collect(response);

    expect(events.map((event) => event.type)).toEqual([
      "node_start",
      "reasoning",
      "token",
      "token",
      "done",
    ]);
    expect(events[1]).toEqual({ type: "reasoning", text: "an average per group" });
    expect(events[4]).toMatchObject({ status: "ok", model: "qwen3:8b", ...COST });
  });

  it("carries a terminal failed frame like any other done event", async () => {
    const response = responseOf(
      frame({ type: "node_start", node: "reason" }) +
        frame({
          type: "done",
          status: "failed",
          answer: "the run broke",
          model: "qwen3:8b",
          input_tokens: 0,
          output_tokens: 0,
          duration_s: 0.4,
        }),
    );

    const events = await collect(response);

    expect(events[1]).toEqual({
      type: "done",
      status: "failed",
      answer: "the run broke",
      model: "qwen3:8b",
      input_tokens: 0,
      output_tokens: 0,
      duration_s: 0.4,
    });
  });

  it("reassembles a frame split across chunks", async () => {
    const whole = frame({ type: "token", text: "half and half" });
    const cut = Math.floor(whole.length / 2);
    const response = responseOf(whole.slice(0, cut), whole.slice(cut));

    await expect(collect(response)).resolves.toEqual([{ type: "token", text: "half and half" }]);
  });

  it("keeps a multi-byte character intact across a chunk boundary", async () => {
    const text = "café";
    const bytes = encoder.encode(frame({ type: "token", text }));
    const inside = bytes.indexOf(0xc3) + 1;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, inside));
        controller.enqueue(bytes.slice(inside));
        controller.close();
      },
    });

    await expect(collect(new Response(stream))).resolves.toEqual([{ type: "token", text }]);
  });

  it("skips a malformed frame and keeps streaming the healthy ones", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const response = responseOf(
      frame({ type: "token", text: "before" }) +
        "data: {not json at all\n\n" +
        frame({ type: "token", text: "after" }),
    );

    const events = await collect(response);

    expect(events).toEqual([
      { type: "token", text: "before" },
      { type: "token", text: "after" },
    ]);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("skips a JSON frame that carries no event type", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    await expect(collect(responseOf(frame({ node: "reason" })))).resolves.toEqual([]);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("ignores comment and blank frames without warning", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const response = responseOf(`: keepalive\n\n${frame({ type: "token", text: "x" })}\n\n`);

    await expect(collect(response)).resolves.toEqual([{ type: "token", text: "x" }]);
    expect(warn).not.toHaveBeenCalled();
  });

  it("flushes a final frame that never got its blank line", async () => {
    const payload = { type: "done", status: "ok", answer: "", model: "m", ...COST };
    const response = responseOf(`data: ${JSON.stringify(payload)}`);

    await expect(collect(response)).resolves.toEqual([payload]);
  });

  it("cancels the body when the consumer stops early", async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frame({ type: "token", text: "one" })));
        controller.enqueue(encoder.encode(frame({ type: "token", text: "two" })));
        controller.close();
      },
      cancel() {
        cancelled = true;
      },
    });

    for await (const event of readTraceEvents(new Response(stream))) {
      expect(event).toEqual({ type: "token", text: "one" });
      break;
    }

    expect(cancelled).toBe(true);
  });

  it("ends quietly when the stream is aborted mid-turn", async () => {
    const aborted = Object.assign(new Error("aborted"), { name: "AbortError" });
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frame({ type: "token", text: "partial" })));
      },
      pull(controller) {
        controller.error(aborted);
      },
    });

    await expect(collect(new Response(stream))).resolves.toEqual([
      { type: "token", text: "partial" },
    ]);
  });

  it("raises a transport failure that is not an abort", async () => {
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.error(new Error("connection reset"));
      },
    });

    await expect(collect(new Response(stream))).rejects.toThrow("connection reset");
  });

  it("refuses a response with no body to read", async () => {
    await expect(collect(new Response(null))).rejects.toThrow(/no body/);
  });
});
