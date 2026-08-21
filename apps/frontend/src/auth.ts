/**
 * Auth session brick — the single source of truth for "who is logged in" on the
 * client. The token lives in memory and is mirrored to sessionStorage (every
 * access wrapped, storage can throw or be disabled). The JWT payload is decoded
 * here FOR DISPLAY ONLY: the badge shows what the server already believes.
 * Authorization is never a client decision — tenant scoping happens server-side
 * from the verified token (CLAUDE.md hard rules, ADR 0002 L1).
 */

const STORAGE_KEY = "secure-rls.token";

export interface Session {
  token: string;
  username: string;
  tenantId: string;
}

interface DisplayClaims {
  sub: string;
  tenant_id: string;
  exp?: number;
}

function base64UrlDecode(segment: string): string {
  const padded = segment.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded.padEnd(padded.length + ((4 - (padded.length % 4)) % 4), "="));
  return decodeURIComponent(
    Array.from(binary, (ch) => `%${ch.charCodeAt(0).toString(16).padStart(2, "0")}`).join(""),
  );
}

/** Reads sub/tenant_id/exp out of an unverified JWT payload. Display only. */
export function decodeDisplayClaims(token: string): DisplayClaims | null {
  const payload = token.split(".")[1];
  if (!payload) return null;
  try {
    const claims = JSON.parse(base64UrlDecode(payload)) as Partial<DisplayClaims>;
    if (typeof claims.sub !== "string" || typeof claims.tenant_id !== "string") return null;
    const exp = typeof claims.exp === "number" ? claims.exp : undefined;
    return { sub: claims.sub, tenant_id: claims.tenant_id, exp };
  } catch {
    return null;
  }
}

function sessionFromToken(token: string): Session | null {
  const claims = decodeDisplayClaims(token);
  if (!claims) return null;
  if (claims.exp !== undefined && claims.exp * 1000 <= Date.now()) return null;
  return { token, username: claims.sub, tenantId: claims.tenant_id };
}

function readStoredToken(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredToken(token: string): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, token);
  } catch {
    // storage disabled (private mode, quota): the in-memory session still works for this tab
  }
}

function removeStoredToken(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // nothing to do: the in-memory session is cleared regardless
  }
}

const stored = readStoredToken();
let current: Session | null = stored ? sessionFromToken(stored) : null;
if (stored && !current) removeStoredToken();

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

/** Current session, or null when logged out. Stable reference between changes (useSyncExternalStore). */
export function getSession(): Session | null {
  return current;
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Adopts a freshly issued token. Returns null (and stays logged out) if it is unusable. */
export function startSession(token: string): Session | null {
  const next = sessionFromToken(token);
  if (!next) return null;
  current = next;
  writeStoredToken(token);
  emit();
  return next;
}

/** Logout, and the landing point of any 401: drop the token and fall back to the login view. */
export function clearSession(): void {
  const had = current !== null;
  current = null;
  removeStoredToken();
  if (had) emit();
}
