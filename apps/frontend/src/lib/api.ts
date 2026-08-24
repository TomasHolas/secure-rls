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
import type { TraceEvent } from "./sse";

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
  /** How many of a thread's first turns still ask the server for a generated title (#118). */
  title_turns: number | null;
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
 * One past turn as the server kept it (ADR 0012 as amended, issue #90): the trace events it
 * produced, keyed exactly as the live stream keys them, so `lib/trace.ts` folds a replayed turn
 * through the very same code a streaming one goes through.
 *
 * `turn` is the ordinal of the question that opened it, counted from one, which is how the fold
 * puts each turn's history back above the answer it produced. `cut` is how many pieces of it the
 * server's caps refused, so a partial turn says so instead of reading as whole.
 */
export interface TurnRecord {
  turn: number;
  events: TraceEvent[];
  cut: number;
}

/**
 * A thread as `GET /conversations/{id}` serves it: the registry row, the replayed exchanges, and
 * the trace each of those turns produced - its reasoning, its calls with the arguments the model
 * wrote, their outcomes, and the terminal frame with the turn's status and cost.
 */
export interface Conversation extends Thread {
  messages: Message[];
  turns: TurnRecord[];
}

/** One `POST /chat` turn. No tenant field exists to send: the server takes it from the JWT. */
export interface ChatRequest {
  thread_id: string;
  message: string;
  model?: string;
}

/**
 * One page of the DATASET's rows, as `GET /records` and `GET /notes` serve it (ADR 0014).
 * The listings are the demo's control group and span every tenant; `tenant_id` is a filter like
 * any other. `total` is how many rows match the filters at all, counted server-side, and
 * `page_size` is the size actually used — the server clamps a page larger than its row cap and
 * says so here. `executed_sql` is the statement that produced the page, and on this surface it
 * deliberately carries no tenant scoping — the one place in the app where that is true.
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
 * The server sends the name only, never the value it carried (issue #107).
 */
export interface IgnoredParam {
  name: string;
  reason: string;
}

/** The filters, sort and window a listing accepts; anything else is not a parameter it reads. */
export interface BrowseQuery {
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

/**
 * One value a categorical filter may be set to, and how many rows of the listing carry it.
 * Both pickers share the shape: the tenants (450/350/200 — the control group in one line) and
 * the departments, whose counts follow the tenant filter so no number is orphaned.
 */
export interface FilterOption {
  value: string;
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

/**
 * One row of the server's audit log (ADR 0002): what the data path was asked to run and what the
 * layers decided about it. `executed_sql` is the statement that actually ran — absent when a layer
 * refused before anything ran — and `error_kind` names the layer that refused or the failure.
 *
 * These are statements and metadata. No result row is in this store, so nothing here is tenant
 * data the Records tab does not already show outright.
 */
export interface AuditEntry {
  id: number;
  ts: string;
  tenant: string;
  generated_sql: string;
  verdict: string;
  executed_sql: string | null;
  rowcount: number | null;
  error_kind: string | null;
}

/** `GET /audit`: one newest-first page of that log, with how many rows it holds in all. */
export interface AuditLog {
  entries: AuditEntry[];
  total: number;
  page: number;
  page_size: number;
}

/** Which rows the committed poison manifest plants a payload in, and of what kind — all tenants. */
export interface FlaggedNotes {
  user_ids: number[];
  kinds: Record<string, string>;
}

/** GET /records: one filtered, sorted page of the dataset's employee rows, every tenant (ADR 0014). */
export async function browseRecords(query: BrowseQuery): Promise<BrowsePage> {
  return browse("/records", query);
}

/** GET /notes: one page of the whole note corpus, filtered and sorted the same way. */
export async function browseNotes(query: BrowseQuery): Promise<BrowsePage> {
  return browse("/notes", query);
}

async function browse(path: string, query: BrowseQuery): Promise<BrowsePage> {
  const response = await apiFetch(`${path}${queryString(query)}`);
  if (!response.ok) throw new ApiError(response.status, await detail(response, "The rows could not be loaded."));
  return (await response.json()) as BrowsePage;
}

/**
 * GET /records/departments: the departments the listing holds and their counts.
 *
 * The tenant filter travels with it, so the count beside an option counts the rows the reader is
 * actually looking at rather than a set they did not ask for.
 */
export async function listDepartments(tenantId?: string): Promise<FilterOption[]> {
  return options(`/records/departments${queryString({ tenant_id: tenantId })}`, "department");
}

/** GET /records/tenants: the dataset's tenants and their row counts, for the tenant filter. */
export async function listTenants(): Promise<FilterOption[]> {
  return options("/records/tenants", "tenant");
}

async function options(path: string, kind: string): Promise<FilterOption[]> {
  const response = await apiFetch(path);
  if (!response.ok) throw new ApiError(response.status, `The ${kind} list is unavailable.`);
  const body = (await response.json()) as unknown;
  return Array.isArray(body) ? (body as FilterOption[]) : [];
}

/** GET /notes/search: the same retrieval the agent's search_notes tool runs, scored. */
export async function searchNotes(query: string, k?: number): Promise<NoteHits> {
  const response = await apiFetch(`/notes/search${queryString({ q: query, k })}`);
  if (!response.ok) throw new ApiError(response.status, await detail(response, "The search failed. Try again."));
  return (await response.json()) as NoteHits;
}

/**
 * GET /audit: one newest-first page of the server's audit log, every tenant's entries.
 *
 * The log is the record of what the data path ran, so it is not narrowed by the caller — the
 * comparison between one tenant's scoped statement and another's is the reason the page exists.
 * A token is still required, exactly as on every other listing.
 */
export async function browseAudit(page: number): Promise<AuditLog> {
  const response = await apiFetch(`/audit${queryString({ page })}`);
  if (!response.ok) throw new ApiError(response.status, await detail(response, "The audit log could not be loaded."));
  return (await response.json()) as AuditLog;
}

/** GET /notes/flagged: every manifest-planted row, so the corpus listing marks them all. */
export async function listFlaggedNotes(): Promise<FlaggedNotes> {
  const response = await apiFetch("/notes/flagged");
  if (!response.ok) throw new ApiError(response.status, "The flagged notes are unavailable.");
  return (await response.json()) as FlaggedNotes;
}

/** The query string of a listing: a blank or absent value is not a filter, so it is left out. */
function queryString(query: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query as Record<string, unknown>)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
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
 * GET /health: liveness, the prompt-guardrail position, and the titling window the server keeps.
 *
 * The position is reported only when the body carries an actual boolean. A missing field, a
 * string `"false"`, a `0` - anything that is not a boolean - is `null`, unknown, and draws no
 * pill: the two things this must never do on a guess are claim the model was asked to police
 * itself, and claim it was not.
 *
 * `title_turns` is read the same way and `null` means unknown rather than any number. A caller
 * that does not know the window asks for a title anyway: the server enforces the window itself,
 * so a guess here could only silence titling that should have run.
 */
export async function getHealth(): Promise<Health> {
  const response = await apiFetch("/health");
  if (!response.ok) throw new ApiError(response.status, "The server status is unavailable.");
  const body = (await response.json()) as {
    status?: unknown;
    version?: unknown;
    prompt_guardrails?: unknown;
    title_turns?: unknown;
  };
  return {
    status: isString(body.status) ? body.status : "",
    version: isString(body.version) ? body.version : "",
    prompt_guardrails:
      typeof body.prompt_guardrails === "boolean" ? body.prompt_guardrails : null,
    title_turns: typeof body.title_turns === "number" ? body.title_turns : null,
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

/** GET /conversations/{id}: the thread row, its replayed exchanges and each turn's history. */
export async function getConversation(threadId: string): Promise<Conversation> {
  const response = await apiFetch(`/conversations/${encodeURIComponent(threadId)}`);
  if (!response.ok) throw new ApiError(response.status, conversationFailure(response.status));
  const body = (await response.json()) as Conversation;
  return {
    ...body,
    messages: Array.isArray(body.messages) ? body.messages : [],
    turns: Array.isArray(body.turns) ? body.turns : [],
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
