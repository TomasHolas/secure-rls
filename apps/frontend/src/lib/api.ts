/**
 * The one HTTP client brick. Every call to the backend goes through here, so the
 * Bearer header and the 401 handling exist exactly once: a 401 anywhere means the
 * token is gone or expired, so the session is dropped and the SPA falls back to
 * the login view (ADR 0012). Views never call fetch directly.
 */

import { clearSession, getSession } from "../auth";
import { API_BASE_URL } from "../config";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
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

/** Authenticated fetch: attaches the Bearer token, turns a 401 into a logout. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const session = getSession();
  const headers = new Headers(init.headers);
  if (session) headers.set("Authorization", `Bearer ${session.token}`);

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (response.status === 401) {
    clearSession();
    throw new ApiError(401, "Session expired. Sign in again.");
  }
  return response;
}
