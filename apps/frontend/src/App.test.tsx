/**
 * The signed-in shell: the conversation rail beside the chat, driven by a fake API. Covers
 * what the sidebar promises - the caller's threads listed, one open and highlighted, its
 * exchanges replayed, New chat registering a thread on the first question, the generated title
 * arriving after that first turn, delete behind a confirm, and a re-login listing only the new
 * identity's threads.
 *
 * The retitle fake echoes the title the thread was registered with unless a test scripts one,
 * so only the titling tests below observe a title change - everything else sees the rail the
 * `POST /conversations` title produced.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  login: vi.fn(),
  listModels: vi.fn(),
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  createConversation: vi.fn(),
  retitleConversation: vi.fn(),
  deleteConversation: vi.fn(),
  openChatStream: vi.fn(),
}));

vi.mock("./lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 500;
  },
  ...api,
}));

const MODEL = "qwen3:8b";
const NEWEST = { thread_id: "t2", title: "median salary in engineering", created: "2026-08-20T12:15:00+00:00" };
const OLDEST = { thread_id: "t1", title: "average salary per department", created: "2026-08-19T12:40:00+00:00" };
const ACME_THREADS = [NEWEST, OLDEST];
const BETA_THREADS = [
  { thread_id: "b1", title: "headcount by office", created: "2026-08-18T12:00:00+00:00" },
];

const REGISTERED = { thread_id: "t3", created: "2026-08-21T12:00:00+00:00" };
const GENERATED_TITLE = "Headcount by department";

const REPLAY = [
  { role: "user", content: "what is the average salary per department?" },
  { role: "assistant", content: "Engineering leads at 91000." },
];

const REPLAY_CHART = {
  kind: "bar",
  title: "avg salary by department",
  x_label: "department",
  y_label: "avg salary",
  data: [{ x: "Engineering", y: 91000 }],
};
const REPLAY_RESULTS = [{ turn: 1, tool: "plot", data: { chart_spec: REPLAY_CHART } }];

const TURN = [
  { type: "token", text: "There are 331 people." },
  { type: "done", status: "ok", answer: "There are 331 people.", model: MODEL },
];

function makeToken(claims: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode(claims)}.signature`;
}

function tokenFor(sub: string, tenant: string): string {
  return makeToken({ sub, tenant_id: tenant, exp: Math.floor(Date.now() / 1000) + 1800 });
}

function sseResponse(events: unknown[]): Response {
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""));
}

/** Fresh module graph per test: auth.ts is a singleton store. */
async function signIn(sub = "acme_analyst", tenant = "acme") {
  vi.resetModules();
  const auth = await import("./auth");
  const App = (await import("./App")).default;
  auth.startSession(tokenFor(sub, tenant));
  const view = render(<App />);
  await waitFor(() =>
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe(MODEL),
  );
  return { auth, view };
}

function titles(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll(".rail-item-title"), (node) => node.textContent ?? "");
}

function activeTitle(container: HTMLElement): string | null {
  return container.querySelector(".rail-item.active .rail-item-title")?.textContent ?? null;
}

function openThread(title: string): void {
  fireEvent.click(screen.getByText(title));
}

function ask(question: string): void {
  const box = screen.getByLabelText("Question");
  fireEvent.change(box, { target: { value: question } });
  fireEvent.keyDown(box, { key: "Enter" });
}

/** The title the lazily registered thread was created with; the retitle fake echoes it. */
let registered = "";

beforeEach(() => {
  registered = "";
  window.sessionStorage.clear();
  api.listModels.mockResolvedValue({ models: [MODEL], default: MODEL });
  api.listConversations.mockResolvedValue(ACME_THREADS);
  api.getConversation.mockImplementation((threadId: string) =>
    Promise.resolve({
      ...OLDEST,
      thread_id: threadId,
      messages: REPLAY,
      tool_results: REPLAY_RESULTS,
    }),
  );
  api.createConversation.mockImplementation((title: string) => {
    registered = title;
    return Promise.resolve({ ...REGISTERED, title });
  });
  api.retitleConversation.mockImplementation((threadId: string) =>
    Promise.resolve({ ...REGISTERED, thread_id: threadId, title: registered }),
  );
  api.deleteConversation.mockResolvedValue(undefined);
  api.openChatStream.mockImplementation(() => Promise.resolve(sseResponse(TURN)));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.sessionStorage.clear();
});

describe("the conversation rail", () => {
  it("lists the caller's threads in the order the API returned them, with their created time", async () => {
    const { view } = await signIn();

    await waitFor(() => expect(titles(view.container)).toEqual([NEWEST.title, OLDEST.title]));
    const meta = view.container.querySelector(".rail-item-meta")?.textContent;
    expect(meta).toBe(
      new Date(NEWEST.created).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }),
    );
  });

  it("starts on a fresh chat with no thread highlighted", async () => {
    const { view } = await signIn();

    await screen.findByText(NEWEST.title);
    expect(activeTitle(view.container)).toBeNull();
    expect(screen.getByText(/Ask a question to start/)).toBeTruthy();
  });

  it("replays the exchanges of the thread it opens and highlights it", async () => {
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);

    openThread(OLDEST.title);

    expect(await screen.findByText("Engineering leads at 91000.")).toBeTruthy();
    expect(api.getConversation).toHaveBeenCalledWith(OLDEST.thread_id);
    expect(activeTitle(view.container)).toBe(OLDEST.title);
    expect(screen.queryByText(/Ask a question to start/)).toBeNull();
  });

  it("shows the chart a reopened thread's turn drew, not prose where a plot used to be", async () => {
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);

    openThread(OLDEST.title);

    await screen.findByText("Engineering leads at 91000.");
    expect(view.container.querySelector("svg")?.getAttribute("aria-label")).toBe(
      REPLAY_CHART.title,
    );
    expect(view.container.querySelectorAll("rect.chart-bar")).toHaveLength(1);
  });

  it("New chat clears the transcript and the next question registers and opens a thread", async () => {
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);
    openThread(OLDEST.title);
    await screen.findByText("Engineering leads at 91000.");

    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));

    expect(screen.getByText(/Ask a question to start/)).toBeTruthy();
    expect(activeTitle(view.container)).toBeNull();

    ask("how many people are there?");

    expect(await screen.findByText("There are 331 people.")).toBeTruthy();
    expect(api.createConversation).toHaveBeenCalledWith("how many people are there?");
    await waitFor(() => expect(activeTitle(view.container)).toBe("how many people are there?"));
    expect(titles(view.container)[0]).toBe("how many people are there?");
    expect(api.openChatStream).toHaveBeenCalledWith({
      thread_id: "t3",
      message: "how many people are there?",
      model: MODEL,
    });
  });

  it("shows the generated title in the rail once the first turn is over", async () => {
    api.retitleConversation.mockResolvedValue({ ...REGISTERED, title: GENERATED_TITLE });
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);

    ask("how many people are there?");
    await screen.findByText("There are 331 people.");

    await waitFor(() => expect(titles(view.container)[0]).toBe(GENERATED_TITLE));
    expect(api.retitleConversation).toHaveBeenCalledWith(REGISTERED.thread_id);
    expect(activeTitle(view.container)).toBe(GENERATED_TITLE);
    expect(titles(view.container)).toEqual([GENERATED_TITLE, NEWEST.title, OLDEST.title]);
  });

  it("titles only the first turn of a thread, not the ones after it", async () => {
    api.retitleConversation.mockResolvedValue({ ...REGISTERED, title: GENERATED_TITLE });
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);

    ask("how many people are there?");
    await screen.findByText("There are 331 people.");
    await waitFor(() => expect(titles(view.container)[0]).toBe(GENERATED_TITLE));
    ask("and the median?");

    await waitFor(() => expect(api.openChatStream).toHaveBeenCalledTimes(2));
    expect(api.retitleConversation).toHaveBeenCalledTimes(1);
    expect(titles(view.container)[0]).toBe(GENERATED_TITLE);
  });

  it("keeps the first-message title and stays silent when titling fails", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    api.retitleConversation.mockRejectedValue(new Error("boom"));
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);

    ask("how many people are there?");
    await screen.findByText("There are 331 people.");

    await waitFor(() => expect(warn).toHaveBeenCalled());
    expect(titles(view.container)[0]).toBe("how many people are there?");
    expect(screen.queryByText(/Could not/)).toBeNull();
    warn.mockRestore();
  });

  it("keeps this session's turns when the open thread is clicked again", async () => {
    await signIn();
    await screen.findByText(OLDEST.title);
    openThread(OLDEST.title);
    await screen.findByText("Engineering leads at 91000.");
    ask("how many people are there?");
    await screen.findByText("There are 331 people.");

    openThread(OLDEST.title);

    expect(api.getConversation).toHaveBeenCalledTimes(1);
    expect(screen.getByText("There are 331 people.")).toBeTruthy();
  });

  it("keeps the thread for the second question of the same chat", async () => {
    await signIn();
    await screen.findByText(OLDEST.title);

    ask("how many people are there?");
    await screen.findByText("There are 331 people.");
    ask("and the median?");

    await waitFor(() => expect(api.openChatStream).toHaveBeenCalledTimes(2));
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.openChatStream).toHaveBeenLastCalledWith({
      thread_id: "t3",
      message: "and the median?",
      model: MODEL,
    });
  });

  it("asks for confirmation before deleting, and cancelling keeps the thread", async () => {
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);

    fireEvent.click(screen.getByLabelText(`Delete conversation ${OLDEST.title}`));
    expect(screen.getByRole("dialog")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(api.deleteConversation).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(titles(view.container)).toEqual([NEWEST.title, OLDEST.title]);
  });

  it("deletes the open thread once confirmed and falls back to a fresh chat", async () => {
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);
    openThread(OLDEST.title);
    await screen.findByText("Engineering leads at 91000.");

    fireEvent.click(screen.getByLabelText(`Delete conversation ${OLDEST.title}`));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(titles(view.container)).toEqual([NEWEST.title]));
    expect(api.deleteConversation).toHaveBeenCalledWith(OLDEST.thread_id);
    expect(activeTitle(view.container)).toBeNull();
    expect(screen.getByText(/Ask a question to start/)).toBeTruthy();
  });

  it("keeps the open thread when another one is deleted", async () => {
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);
    openThread(OLDEST.title);
    await screen.findByText("Engineering leads at 91000.");

    fireEvent.click(screen.getByLabelText(`Delete conversation ${NEWEST.title}`));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(titles(view.container)).toEqual([OLDEST.title]));
    expect(activeTitle(view.container)).toBe(OLDEST.title);
    expect(screen.getByText("Engineering leads at 91000.")).toBeTruthy();
  });

  it("lists only the threads of the identity that signs in next", async () => {
    const { auth, view } = await signIn();
    await screen.findByText(NEWEST.title);

    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));
    api.listConversations.mockResolvedValue(BETA_THREADS);
    act(() => {
      auth.startSession(tokenFor("beta_analyst", "beta"));
    });

    await waitFor(() => expect(titles(view.container)).toEqual([BETA_THREADS[0].title]));
    expect(api.listConversations).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(NEWEST.title)).toBeNull();
  });

  it("says so when the thread list cannot be loaded", async () => {
    api.listConversations.mockRejectedValue(new Error("boom"));
    await signIn();

    expect(await screen.findByText(/Could not load your conversations/)).toBeTruthy();
  });

  it("collapses the rail down to its reopen control", async () => {
    const { view } = await signIn();
    await screen.findByText(NEWEST.title);

    fireEvent.click(screen.getByLabelText("Hide conversations"));

    expect(titles(view.container)).toEqual([]);
    expect(screen.queryByRole("button", { name: /new chat/i })).toBeNull();

    fireEvent.click(screen.getByLabelText("Show conversations"));

    expect(titles(view.container)).toEqual([NEWEST.title, OLDEST.title]);
  });
});
