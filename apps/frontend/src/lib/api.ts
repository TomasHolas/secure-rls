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
 * A thread as `GET /conversations/{id}` serves it: the registry row plus the replayed
 * exchanges. `messages` holds questions and answers only - the tool calls, SQL and
 * security events a turn streamed live are not replayable by design (ADR 0012).
 */
export interface Conversation extends Thread {
  messages: Message[];
}

/** One `POST /chat` turn. No tenant field exists to send: the server takes it from the JWT. */
export interface ChatRequest {
  thread_id: string;
  message: string;
  model?: string;
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

/** GET /models: the endpoint's live model ids plus the configured default (ADR 0005). */
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

/** GET /conversations/{id}: the thread row plus its replayed exchanges. */
export async function getConversation(threadId: string): Promise<Conversation> {
  const response = await apiFetch(`/conversations/${encodeURIComponent(threadId)}`);
  if (!response.ok) throw new ApiError(response.status, conversationFailure(response.status));
  const body = (await response.json()) as Conversation;
  return { ...body, messages: Array.isArray(body.messages) ? body.messages : [] };
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
