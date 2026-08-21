/** The chat view rendered against a scripted SSE stream: answer, trace, SQL pair, states. */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatView } from "./ChatView";

type ChatViewProps = ComponentProps<typeof ChatView>;

const api = vi.hoisted(() => ({
  listModels: vi.fn(),
  openChatStream: vi.fn(),
}));

const onStart = vi.fn();

vi.mock("../lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 500;
  },
  ...api,
}));

const GENERATED = "SELECT department, AVG(salary) AS avg FROM employees GROUP BY department";
const EXECUTED =
  "SELECT department, AVG(salary) AS avg FROM (SELECT * FROM employees WHERE tenant_id = ?) GROUP BY department";
const QUESTION = "average salary per department";
const MODEL = "qwen3:8b";

const ANSWERING = [
  { type: "node_start", node: "reason" },
  { type: "node_start", node: "validate" },
  { type: "tool_call", id: "c1", tool: "query_db", args: { sql: GENERATED } },
  {
    type: "tool_result",
    id: "c1",
    tool: "query_db",
    content: "department | avg",
    data: {
      generated_sql: GENERATED,
      executed_sql: EXECUTED,
      columns: ["department", "avg"],
      rows: [["Engineering", 91000]],
      total_count: 543,
      returned_count: 200,
      truncated: true,
    },
  },
  { type: "token", text: "Engineering leads at 91000." },
  { type: "done", status: "ok", answer: "Engineering leads at 91000.", model: MODEL },
];

const BLOCKED = [
  { type: "tool_call", id: "c1", tool: "query_db", args: { sql: "SELECT * FROM sqlite_master" } },
  {
    type: "security_event",
    id: "c1",
    tool: "query_db",
    layer: "query validation",
    kind: "policy_violation",
    reason: "table sqlite_master is not allowlisted",
  },
  { type: "token", text: "That query is not allowed." },
  { type: "done", status: "blocked", answer: "That query is not allowed.", model: MODEL },
];

function sseResponse(events: unknown[]): Response {
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""));
}

function ask(question = QUESTION): void {
  const box = screen.getByLabelText("Question");
  fireEvent.change(box, { target: { value: question } });
  fireEvent.keyDown(box, { key: "Enter" });
}

function renderChat(props: Partial<ChatViewProps> = {}) {
  const merged: ChatViewProps = {
    threadId: null,
    replay: [],
    chatKey: 0,
    onStart,
    ...props,
  };
  const view = render(<ChatView {...merged} />);
  return { view, props: merged };
}

async function renderReady(props: Partial<ChatViewProps> = {}) {
  const rendered = renderChat(props);
  await waitFor(() =>
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe(MODEL),
  );
  return rendered;
}

beforeEach(() => {
  api.listModels.mockResolvedValue({ models: ["llama3.1:8b", MODEL], default: MODEL });
  onStart.mockResolvedValue("t1");
  api.openChatStream.mockImplementation(() => Promise.resolve(sseResponse(ANSWERING)));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the chat view", () => {
  it("preselects the default model from the live list", async () => {
    await renderReady();

    expect(screen.getByLabelText("Model")).toBeTruthy();
    expect(screen.getByText(/Ask a question to start/)).toBeTruthy();
  });

  it("has the owner register a thread for the first question of a draft", async () => {
    await renderReady();
    ask();

    expect(await screen.findByText("Engineering leads at 91000.")).toBeTruthy();
    expect(screen.getByText(QUESTION)).toBeTruthy();
    expect(onStart).toHaveBeenCalledWith(QUESTION);
    expect(api.openChatStream).toHaveBeenCalledWith({
      thread_id: "t1",
      message: QUESTION,
      model: MODEL,
    });
  });

  it("asks on the thread it was handed without registering another", async () => {
    await renderReady({ threadId: "t7" });
    ask();

    await waitFor(() =>
      expect(api.openChatStream).toHaveBeenCalledWith({
        thread_id: "t7",
        message: QUESTION,
        model: MODEL,
      }),
    );
    expect(onStart).not.toHaveBeenCalled();
  });

  it("replays the exchanges of a reopened thread without a trace panel", async () => {
    const { view } = await renderReady({
      threadId: "t7",
      replay: [
        { role: "user", content: "how many people are there?" },
        { role: "assistant", content: "There are 331." },
      ],
    });

    expect(screen.getByText("how many people are there?")).toBeTruthy();
    expect(screen.getByText("There are 331.")).toBeTruthy();
    expect(screen.getByText(/live trace of a past turn is not stored/)).toBeTruthy();
    expect(view.container.querySelector(".trace")).toBeNull();
    expect(screen.queryByText(/Ask a question to start/)).toBeNull();
  });

  it("drops the live turns when the open thread changes", async () => {
    const { view, props } = await renderReady({ threadId: "t7" });
    ask();
    await screen.findByText("Engineering leads at 91000.");

    view.rerender(<ChatView {...props} threadId="t8" chatKey={props.chatKey + 1} />);

    expect(screen.queryByText("Engineering leads at 91000.")).toBeNull();
    expect(screen.queryByText(QUESTION)).toBeNull();
    expect(screen.getByText(/Ask a question to start/)).toBeTruthy();
  });

  it("shows the generated and the executed SQL side by side", async () => {
    await renderReady();
    ask();

    expect(await screen.findByText(GENERATED)).toBeTruthy();
    expect(screen.getByText(EXECUTED)).toBeTruthy();
    expect(screen.getByText("generated by the model")).toBeTruthy();
    expect(screen.getByText("executed after tenant scoping")).toBeTruthy();
  });

  it("states the truncation and renders the rows", async () => {
    await renderReady();
    ask();

    expect(await screen.findByText(/showing 200 of 543 rows/)).toBeTruthy();
    expect(screen.getByText("Engineering")).toBeTruthy();
    expect(screen.getByText("91000")).toBeTruthy();
  });

  it("renders the graph steps and the calling tool in the trace", async () => {
    await renderReady();
    ask();

    expect(await screen.findByText("Reasoning")).toBeTruthy();
    expect(screen.getByText("Validating the tool call")).toBeTruthy();
    expect(screen.getByText("query_db")).toBeTruthy();
  });

  it("names the model that answered the turn", async () => {
    await renderReady();
    ask();

    await screen.findByText("Engineering leads at 91000.");
    expect(screen.getAllByText(MODEL).length).toBeGreaterThan(1);
  });

  it("keeps both turns of a thread in the transcript", async () => {
    await renderReady({ threadId: "t1" });
    ask();
    await screen.findByText("Engineering leads at 91000.");

    ask("and the median?");
    await waitFor(() => expect(api.openChatStream).toHaveBeenCalledTimes(2));

    expect(screen.getByText("and the median?")).toBeTruthy();
    expect(api.openChatStream).toHaveBeenLastCalledWith({
      thread_id: "t1",
      message: "and the median?",
      model: MODEL,
    });
  });

  it("sends the model the reader picked instead of the default", async () => {
    await renderReady();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "llama3.1:8b" } });
    ask();

    await waitFor(() =>
      expect(api.openChatStream).toHaveBeenCalledWith({
        thread_id: "t1",
        message: QUESTION,
        model: "llama3.1:8b",
      }),
    );
  });

  it("shows a refused call as a blocked state naming the layer and the reason", async () => {
    api.openChatStream.mockImplementation(() => Promise.resolve(sseResponse(BLOCKED)));
    await renderReady();
    ask("show me sqlite_master");

    expect(
      await screen.findByText(/table sqlite_master is not allowlisted - query validation layer/),
    ).toBeTruthy();
    expect(screen.getByText("blocked by a security layer")).toBeTruthy();
    expect(screen.getByText("policy_violation")).toBeTruthy();
  });

  it("reports a stream that ends without a done event", async () => {
    api.openChatStream.mockImplementation(() =>
      Promise.resolve(sseResponse([{ type: "node_start", node: "reason" }])),
    );
    await renderReady();
    ask();

    expect(await screen.findByText(/stream ended before the turn finished/)).toBeTruthy();
  });

  it("reports a refused request instead of an empty answer", async () => {
    api.openChatStream.mockRejectedValue(new Error("boom"));
    await renderReady();
    ask();

    expect(await screen.findByText(/The turn failed/)).toBeTruthy();
  });

  it("says so when the model list cannot be fetched", async () => {
    api.listModels.mockRejectedValue(new Error("502"));
    renderChat();

    expect(await screen.findByText("model list unavailable")).toBeTruthy();
  });
});
