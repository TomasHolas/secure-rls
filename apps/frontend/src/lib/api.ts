/**
 * The one HTTP client brick. Every call to the backend goes through here, so the
 * Bearer header and the 401 handling exist exactly once: a 401 anywhere means the
 * token is gone or expired, so the session is dropped and the SPA falls back to
 * the login view (ADR 0012). Views never call fetch directly.
 *
 * The session slides (ADR 0009 as amended): when a response carries
 * `X-Refreshed-Token`, the server re-issued the token because it was close to
 * expiring, and the session adopts it here. One place attaches the token and one
 * place replaces it, so no view has to think about expiry and an active user is
 * never signed out mid-session.
 */

import { clearSession, getSession, startSession } from "../auth";
import { API_BASE_URL } from "../config";
import type { ToolResultData } from "./sse";

/** The header a sliding-session refresh arrives on (ADR 0009 as amended). */
const REFRESHED_TOKEN_HEADER = "X-Refreshed-Token";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * The `GET /health` body: liveness, the API version, and the prompt-guardrail position.
 *
 * The position is what lets the shell state the mode before the first turn of a session; every
 * turn then carries its own on the `done` frame, which is the authoritative record of the two.
 *
 * `prompt_guardrails` has three states, not two: on, off, and `null` for a server that did not
 * say. Unknown is not off - collapsing it into off would make the UI announce the demo mode to
 * anyone behind an older backend or a body-rewriting proxy, which is the opposite of the honest
 * provenance the field exists for. Nothing is drawn for `null`.
 */
export interface Health {
  status: string;
  version: string;
  prompt_guardrails: boolean | null;
}

/** The `GET /models` body: the endpoint's live ids plus the id the server defaults to. */
export interface ModelList {
  models: string[];
  default: string;
}

/** A conversation row as `POST /conversations` returns it. */
export interface Thread {
  thread_id: string;
  title: string;
  created: string;
}

/** One replayed exchange from a thread's transcript: who spoke and what they said. */
export interface Message {
  role: string;
  content: string;
}

/**
 * One stored tool result of a past turn: the tool that ran and the payload the server produced
 * (ADR 0012 as amended). `turn` is the ordinal of the question that asked for it, counted from
 * one, which is how the fold in `lib/trace.ts` puts it back above the answer it produced.
 */
export interface ToolResultRecord {
  turn: number;
  tool: string;
  data: ToolResultData;
}

/**
 * A thread as `GET /conversations/{id}` serves it: the registry row, the replayed exchanges,
 * and the tool evidence those turns produced - their SQL pairs, tables and charts. What is not
 * served is the thinking around them: the model's reasoning, the retries and the graph steps
 * are the transport of the turn that streamed them and are session-only (ADR 0012 as amended).
 */
export interface Conversation extends Thread {
  messages: Message[];
  tool_results: ToolResultRecord[];
}

/** One `POST /chat` turn. No tenant field exists to send: the server takes it from the JWT. */
export interface ChatRequest {
  thread_id: string;
  message: string;
  model?: string;
}

/**
 * One page of the caller's own rows, as `GET /records` and `GET /notes` serve it (ADR 0014).
 * `total` is how many rows match the filters at all, counted server-side over the same scoped
 * query, and `page_size` is the size actually used — the server clamps a page larger than its
 * row cap and says so here. `executed_sql` is the tenant-scoped statement that produced the
 * page, which is the same evidence the chat trace shows for a tool call.
 */
export interface BrowsePage {
  columns: string[];
  rows: unknown[][];
  total: number;
  page: number;
  page_size: number;
  sort: string;
  direction: string;
  executed_sql: string;
  ignored: IgnoredParam[];
}

/**
 * One parameter the request carried that the listing does not read, and the server's reason.
 * The server sends the name only, never the value it carried, and `tenant_id`/`tenant` carry
 * their own reason rather than a generic one (issue #107).
 */
export interface IgnoredParam {
  name: string;
  reason: string;
}

/** The filters, sort and window a listing accepts; anything else is not a parameter it reads. */
export interface BrowseQuery {
  // Carried empty until the listings show every tenant and this becomes a filter like the rest (#117).
  tenant_id?: string;
  name?: string;
  department?: string;
  salary_min?: string;
  salary_max?: string;
  score_min?: string;
  score_max?: string;
  hired_from?: string;
  hired_to?: string;
  sort?: string;
  direction?: string;
  page?: number;
  page_size?: number;
}

/** One department of the caller's tenant and its headcount: the filter's only options. */
export interface DepartmentCount {
  department: string;
  employees: number;
}

/**
 * One retrieved note with its distance — the agent's own retrieval result (ADR 0010), annotated
 * server-side with the tenant, department and score of the row it came from so a reader can check
 * the hit against the data rather than against the agent's account of it (ADR 0014).
 */
export interface NoteHit {
  user_id: number;
  name: string;
  note: string;
  distance: number;
  tenant_id?: string;
  department?: string;
  performance_score?: number;
}

/** `GET /notes/search`: the query, how many hits it asked for, and what came back scored. */
export interface NoteHits {
  query: string;
  k: number;
  hits: NoteHit[];
}

/** Which of the caller's rows the committed poison manifest plants a payload in, and of what kind. */
export interface FlaggedNotes {
  user_ids: number[];
  kinds: Record<string, string>;
}

/**
 * GET /records: one filtered, sorted page of the caller's own employee rows (ADR 0014).
 *
 * `probe` is the reader's own `name=value`, sent alongside the filters exactly as typed so the
 * server can answer what it does with a parameter it does not read (issue #107). It is a
 * demonstration input, not a filter: nothing here knows or cares what it says.
 */
export async function browseRecords(query: BrowseQuery, probe?: string): Promise<BrowsePage> {
  return browse("/records", query, probe);
}

/** GET /notes: one page of the caller's note corpus, filtered, sorted and probed the same way. */
export async function browseNotes(query: BrowseQuery, probe?: string): Promise<BrowsePage> {
  return browse("/notes", query, probe);
}

async function browse(path: string, query: BrowseQuery, probe?: string): Promise<BrowsePage> {
  const response = await apiFetch(`${path}${queryString(query, probe)}`);
  if (!response.ok) throw new ApiError(response.status, await detail(response, "The rows could not be loaded."));
  return (await response.json()) as BrowsePage;
}

/** GET /records/departments: the caller's departments and headcounts, for the filter's options. */
export async function listDepartments(): Promise<DepartmentCount[]> {
  const response = await apiFetch("/records/departments");
  if (!response.ok) throw new ApiError(response.status, "The department list is unavailable.");
  const body = (await response.json()) as unknown;
  return Array.isArray(body) ? (body as DepartmentCount[]) : [];
}

/** GET /notes/search: the same retrieval the agent's search_notes tool runs, scored. */
export async function searchNotes(query: string, k?: number): Promise<NoteHits> {
  const response = await apiFetch(`/notes/search${queryString({ q: query, k })}`);
  if (!response.ok) throw new ApiError(response.status, await detail(response, "The search failed. Try again."));
  return (await response.json()) as NoteHits;
}

/** GET /notes/flagged: the manifest-planted rows of this tenant, so the corpus marks them. */
export async function listFlaggedNotes(): Promise<FlaggedNotes> {
  const response = await apiFetch("/notes/flagged");
  if (!response.ok) throw new ApiError(response.status, "The flagged notes are unavailable.");
  return (await response.json()) as FlaggedNotes;
}

/**
 * The query string of a listing: a blank or absent value is not a filter, so it is left out.
 *
 * A `probe` the reader typed is appended last, split on its first `=`, and encoded by
 * `URLSearchParams` like every other parameter — never concatenated into the URL, so a typed
 * `&` or space cannot compose a request the reader did not write. A probe with no name at all
 * is nothing to send.
 */
function queryString(query: object, probe?: string): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query as Record<string, unknown>)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const [name, ...rest] = (probe ?? "").split("=");
  if (name.trim()) params.append(name.trim(), rest.join("="));
  const rendered = params.toString();
  return rendered ? `?${rendered}` : "";
}

/**
 * The server's own reason for a refusal, when it gave one. A browse is refused by an allowlist
 * — a sort it does not have, a date that is not one — and that reason is about the request the
 * reader made, so repeating it verbatim is more honest than a generic sentence.
 */
async function detail(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return isString(body.detail) ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

/** POST /login. Returns the raw JWT; throws ApiError(401) on bad credentials. */
export async function login(username: string, password: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new ApiError(
      response.status,
      response.status === 401 ? "Invalid username or password." : "Login failed. Try again.",
    );
  }
  const body = (await response.json()) as { token?: unknown };
  if (typeof body.token !== "string") throw new ApiError(response.status, "Login failed. Try again.");
  return body.token;
}

/**
 * GET /health: liveness plus the prompt-guardrail position the server is running in.
 *
 * The position is reported only when the body carries an actual boolean. A missing field, a
 * string `"false"`, a `0` - anything that is not a boolean - is `null`, unknown, and draws no
 * pill: the two things this must never do on a guess are claim the model was asked to police
 * itself, and claim it was not.
 */
export async function getHealth(): Promise<Health> {
  const response = await apiFetch("/health");
  if (!response.ok) throw new ApiError(response.status, "The server status is unavailable.");
  const body = (await response.json()) as {
    status?: unknown;
    version?: unknown;
    prompt_guardrails?: unknown;
  };
  return {
    status: isString(body.status) ? body.status : "",
    version: isString(body.version) ? body.version : "",
    prompt_guardrails:
      typeof body.prompt_guardrails === "boolean" ? body.prompt_guardrails : null,
  };
}

/** GET /models: the endpoint's live model ids plus the default it resolves for a turn (ADR 0005). */
export async function listModels(): Promise<ModelList> {
  const response = await apiFetch("/models");
  if (!response.ok) throw new ApiError(response.status, "The model list is unavailable.");
  const body = (await response.json()) as { models?: unknown; default?: unknown };
  const models = Array.isArray(body.models) ? body.models.filter(isString) : [];
  return { models, default: isString(body.default) ? body.default : models[0] ?? "" };
}

/** POST /conversations: registers a thread titled with the first user message. */
export async function createConversation(title: string): Promise<Thread> {
  const response = await apiFetch("/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!response.ok) throw new ApiError(response.status, "Could not start a conversation.");
  return (await response.json()) as Thread;
}

/** GET /conversations: the caller's own threads, newest first as the server ordered them. */
export async function listConversations(): Promise<Thread[]> {
  const response = await apiFetch("/conversations");
  if (!response.ok) throw new ApiError(response.status, "Could not load your conversations.");
  const body = (await response.json()) as unknown;
  return Array.isArray(body) ? (body as Thread[]) : [];
}

/** GET /conversations/{id}: the thread row, its replayed exchanges and their tool evidence. */
export async function getConversation(threadId: string): Promise<Conversation> {
  const response = await apiFetch(`/conversations/${encodeURIComponent(threadId)}`);
  if (!response.ok) throw new ApiError(response.status, conversationFailure(response.status));
  const body = (await response.json()) as Conversation;
  return {
    ...body,
    messages: Array.isArray(body.messages) ? body.messages : [],
    tool_results: Array.isArray(body.tool_results) ? body.tool_results : [],
  };
}

/**
 * PATCH /conversations/{id}: the thread retitled server-side from its first exchange.
 *
 * The body carries the stored row, so the caller adopts a title rather than guessing one. The
 * server always answers with a usable title (ADR 0012 as amended) - the model's, or the
 * first-message fallback - so a resolved call never means "the title is now worse".
 */
export async function retitleConversation(threadId: string): Promise<Thread> {
  const response = await apiFetch(`/conversations/${encodeURIComponent(threadId)}`, {
    method: "PATCH",
  });
  if (!response.ok) throw new ApiError(response.status, conversationFailure(response.status));
  return (await response.json()) as Thread;
}

/** DELETE /conversations/{id}: drops the thread and its checkpointer state server-side. */
export async function deleteConversation(threadId: string): Promise<void> {
  const response = await apiFetch(`/conversations/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new ApiError(response.status, conversationFailure(response.status));
}

function conversationFailure(status: number): string {
  if (status === 404) return "That conversation no longer exists.";
  return "The conversation request failed. Try again.";
}

/**
 * POST /chat: the SSE response with its body still unread. The caller streams it through
 * `lib/sse.ts`; this module only opens it and turns a refusal into an ApiError.
 */
export async function openChatStream(request: ChatRequest): Promise<Response> {
  const response = await apiFetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(request),
  });
  if (!response.ok) throw new ApiError(response.status, chatFailure(response.status));
  return response;
}

function chatFailure(status: number): string {
  if (status === 400) return "The selected model is not available on the endpoint any more.";
  if (status === 404) return "This conversation no longer exists. Start a new one.";
  if (status === 502) return "The model endpoint is unavailable.";
  return "The chat request failed. Try again.";
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

/** Authenticated fetch: attaches the Bearer token, adopts a refresh, turns a 401 into a logout. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const session = getSession();
  const headers = new Headers(init.headers);
  if (session) headers.set("Authorization", `Bearer ${session.token}`);

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (response.status === 401) {
    clearSession();
    throw new ApiError(401, "Session expired. Sign in again.");
  }
  const refreshed = response.headers.get(REFRESHED_TOKEN_HEADER);
  if (refreshed) startSession(refreshed);
  return response;
}
