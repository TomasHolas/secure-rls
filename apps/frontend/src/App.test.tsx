/**
 * The signed-in shell: the conversation rail beside the chat, driven by a fake API. Covers
 * what the sidebar promises - the caller's threads listed, one open and highlighted, its
 * exchanges replayed, New chat registering a thread on the first question, the generated title
 * arriving after that first turn, delete behind a confirm, and a re-login listing only the new
 * identity's threads.
 *
 * The retitle fake echoes the title the thread already has unless a test scripts one, so only
 * the titling tests below observe a title change - everything else sees the rail the
 * `POST /conversations` title produced. It has to echo per thread rather than one remembered
 * name, because titling now runs again on the later turns of a thread the reader reopened.
 *
 * The second block covers the section tabs (issue #88, ADR 0014): that a tab a reader has never
 * opened fetches nothing, that the conversation rail belongs to the chat alone, and above all
 * that switching away and back costs the reader nothing - the streamed turn is still there,
 * because a visited tab is hidden rather than unmounted.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  login: vi.fn(),
  getHealth: vi.fn(),
  listModels: vi.fn(),
  browseRecords: vi.fn(),
  browseNotes: vi.fn(),
  listDepartments: vi.fn(),
  listFlaggedNotes: vi.fn(),
  searchNotes: vi.fn(),
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
const RETITLED = "Median salary in engineering";
/** The window the fake `/health` reports; two turns is enough to see it close. */
const TITLE_TURNS = 2;

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
const REPLAY_TURNS = [
  {
    turn: 1,
    cut: 0,
    events: [
      { type: "tool_call", id: "c1", tool: "plot", args: { kind: "bar", column: "department" } },
      {
        type: "tool_result",
        id: "c1",
        tool: "plot",
        content: "",
        data: { chart_spec: REPLAY_CHART },
      },
      {
        type: "done",
        status: "ok",
        answer: "Engineering leads at 91000.",
        grounded: true,
        model: MODEL,
        prompt_guardrails: true,
        input_tokens: 250,
        output_tokens: 28,
        duration_s: 2,
      },
    ],
  },
];

const RECORDS_PAGE = {
  columns: ["user_id", "tenant_id", "name"],
  rows: [[1, "acme", "Ada Lovelace"]],
  total: 450,
  page: 1,
  page_size: 25,
  sort: "user_id",
  direction: "asc",
  executed_sql: "SELECT user_id FROM (SELECT * FROM employees WHERE employees.tenant_id = ?)",
};
const NOTES_PAGE = {
  ...RECORDS_PAGE,
  columns: ["user_id", "tenant_id", "name", "department", "notes"],
  rows: [[1, "acme", "Ada Lovelace", "Engineering", "shipped the compiler"]],
};

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

/** The title a listed thread already carries, so an echoing retitle leaves the rail alone. */
function titleOf(threadId: string): string {
  const listed = [...ACME_THREADS, ...BETA_THREADS].find(
    (thread) => thread.thread_id === threadId,
  );
  return listed ? listed.title : registered;
}

beforeEach(() => {
  registered = "";
  window.sessionStorage.clear();
  api.getHealth.mockResolvedValue({
    status: "ok",
    version: "1",
    prompt_guardrails: true,
    title_turns: TITLE_TURNS,
  });
  api.listModels.mockResolvedValue({ models: [MODEL], default: MODEL });
  api.listConversations.mockResolvedValue(ACME_THREADS);
  api.getConversation.mockImplementation((threadId: string) =>
    Promise.resolve({
      ...OLDEST,
      thread_id: threadId,
      messages: REPLAY,
      turns: REPLAY_TURNS,
    }),
  );
  api.createConversation.mockImplementation((title: string) => {
    registered = title;
    return Promise.resolve({ ...REGISTERED, title });
  });
  api.retitleConversation.mockImplementation((threadId: string) =>
    Promise.resolve({
      ...REGISTERED,
      thread_id: threadId,
      title: threadId === REGISTERED.thread_id ? registered : titleOf(threadId),
    }),
  );
  api.deleteConversation.mockResolvedValue(undefined);
  api.openChatStream.mockImplementation(() => Promise.resolve(sseResponse(TURN)));
  api.browseRecords.mockResolvedValue(RECORDS_PAGE);
  api.browseNotes.mockResolvedValue(NOTES_PAGE);
  api.listDepartments.mockResolvedValue([{ department: "Engineering", employees: 95 }]);
  api.listFlaggedNotes.mockResolvedValue({ user_ids: [], kinds: {} });
  api.searchNotes.mockResolvedValue({ query: "", k: 5, hits: [] });
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

  it("renames the thread again on a later turn inside the window", async () => {
    api.retitleConversation.mockResolvedValue({ ...REGISTERED, title: GENERATED_TITLE });
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);

    ask("hello, how are you");
    await screen.findByText("There are 331 people.");
    await waitFor(() => expect(titles(view.container)[0]).toBe(GENERATED_TITLE));
    api.retitleConversation.mockResolvedValue({ ...REGISTERED, title: RETITLED });
    ask("and the median salary in engineering?");

    await waitFor(() => expect(titles(view.container)[0]).toBe(RETITLED));
    expect(api.retitleConversation).toHaveBeenCalledTimes(TITLE_TURNS);
    expect(activeTitle(view.container)).toBe(RETITLED);
  });

  it("stops titling a thread once it is past the window", async () => {
    api.retitleConversation.mockResolvedValue({ ...REGISTERED, title: GENERATED_TITLE });
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);

    for (let turn = 1; turn <= TITLE_TURNS + 1; turn += 1) {
      ask(`question ${turn}`);
      await waitFor(() => expect(api.openChatStream).toHaveBeenCalledTimes(turn));
      await waitFor(() => expect(screen.getByText(`question ${turn}`)).toBeTruthy());
    }

    await waitFor(() => expect(api.retitleConversation).toHaveBeenCalledTimes(TITLE_TURNS));
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

  // Rewritten with issue #114: the collapse now clips the rail rather than unmounting its
  // contents, which is what keeps the icon column from moving, so the shell's promise is a
  // narrow aside whose list is no longer reachable - not an empty DOM. New chat stays reachable
  // because its icon is still on screen. views/ConversationsSidebar.test.tsx owns the detail.
  it("collapses the rail to its icon column and expands it again", async () => {
    const { view } = await signIn();
    await screen.findByText(NEWEST.title);

    fireEvent.click(screen.getByLabelText("Hide conversations"));

    expect(view.container.querySelector(".sidebar-collapsed")).toBeTruthy();
    expect(view.container.querySelector(".sidebar-list")?.getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByRole("button", { name: /new chat/i })).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Show conversations"));

    expect(view.container.querySelector(".sidebar-collapsed")).toBeNull();
    expect(view.container.querySelector(".sidebar-list")?.getAttribute("aria-hidden")).toBeNull();
    expect(titles(view.container)).toEqual([NEWEST.title, OLDEST.title]);
  });
});


function openTab(name: string): void {
  fireEvent.click(screen.getByRole("tab", { name }));
}

describe("the section tabs", () => {
  it("starts on the chat, with nothing fetched for the tabs nobody opened", async () => {
    await signIn();
    await screen.findByText(NEWEST.title);

    expect(screen.getByRole("tab", { name: "Chat" }).getAttribute("aria-selected")).toBe("true");
    expect(api.browseRecords).not.toHaveBeenCalled();
    expect(api.browseNotes).not.toHaveBeenCalled();
  });

  it("shows the tenant's rows on the Records tab and hides the conversation rail", async () => {
    const { view } = await signIn();
    await screen.findByText(NEWEST.title);

    openTab("Records");

    expect(await screen.findByText("Ada Lovelace")).toBeTruthy();
    expect(screen.getByText(/450 matching rows/)).toBeTruthy();
    expect(titles(view.container)).toEqual([]);
    expect(screen.queryByRole("button", { name: /new chat/i })).toBeNull();
  });

  it("shows the note corpus on the Notes tab, with the rail still gone", async () => {
    const { view } = await signIn();
    await screen.findByText(NEWEST.title);

    openTab("Notes");

    expect(await screen.findByText("shipped the compiler")).toBeTruthy();
    expect(titles(view.container)).toEqual([]);
  });

  it("keeps the streamed turn and the rail when the reader comes back to the chat", async () => {
    const { view } = await signIn();
    await screen.findByText(OLDEST.title);
    ask("how many people are there?");
    await screen.findByText("There are 331 people.");

    openTab("Records");
    await screen.findByText("Ada Lovelace");
    openTab("Chat");

    expect(screen.getByText("There are 331 people.")).toBeTruthy();
    expect(titles(view.container)).toContain(NEWEST.title);
    expect(api.openChatStream).toHaveBeenCalledTimes(1);
  });

  it("keeps a filter the reader typed on a tab they left", async () => {
    await signIn();
    await screen.findByText(NEWEST.title);
    openTab("Records");
    await screen.findByText("Ada Lovelace");

    fireEvent.change(screen.getByLabelText("Name contains"), { target: { value: "ada" } });
    openTab("Chat");
    openTab("Records");

    expect((screen.getByLabelText("Name contains") as HTMLInputElement).value).toBe("ada");
    expect(api.browseRecords).toHaveBeenCalledTimes(1);
  });

  it("shows one section at a time", async () => {
    const { view } = await signIn();
    await screen.findByText(NEWEST.title);
    openTab("Records");
    await screen.findByText("Ada Lovelace");

    const panels = Array.from(view.container.querySelectorAll(".tab-panel"));
    expect(panels.map((panel) => panel.getAttribute("aria-label"))).toEqual(["chat", "records"]);
    expect(panels.filter((panel) => !panel.hasAttribute("hidden"))).toHaveLength(1);
  });
});
