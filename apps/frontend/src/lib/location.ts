/**
 * The location brick: which tab is open and, under the chat, which conversation - kept in the
 * URL hash. `#/chat/<thread_id>`, `#/chat`, `#/records`, `#/notes` is the whole vocabulary.
 *
 * It lives in the URL rather than in storage (issue #135): a reload lands where the reader was,
 * back and forward mean something, a thread is a link one demo laptop can hand another, and the
 * state is visible instead of hidden in a storage key. Nothing else is ever written here - not
 * the token, not a query, not a filter a view holds. The hash is location, and a URL is never
 * something the server is asked to trust: a thread id read out of it is still checked against
 * the caller's identity by the registry, which answers a foreign id with the same 404 as a
 * missing one (ADR 0012).
 *
 * It is a store the way `auth.ts` is one: `subscribeLocation` plus `getLocationHash` feed
 * `useSyncExternalStore`, so a hash the browser wrote (reload, back, forward) and a hash this
 * module wrote reach React through one path. The snapshot is the raw fragment because a string
 * is what stays referentially stable between changes; `parseLocation` turns it into the two
 * fields a caller wants and `useLocation` is the two together.
 *
 * Which tabs exist is not this module's business - the shell owns that, so a hash naming a tab
 * nobody has parses cleanly here and the shell is the one that falls back to the chat.
 */

import { useMemo, useSyncExternalStore } from "react";

const PREFIX = "#/";
const SEPARATOR = "/";
const MAX_SEGMENTS = 2;

export interface AppLocation {
  /** The first segment: the tab to show, or null when the hash names none. */
  tab: string | null;
  /** The second segment: under the chat tab, the conversation to open. */
  threadId: string | null;
}

const NOWHERE: AppLocation = { tab: null, threadId: null };

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

// The browser's own navigation; a traversal between two fragments fires one or the other.
window.addEventListener("hashchange", emit);
window.addEventListener("popstate", emit);

/** The fragment as it stands, which is the snapshot React can compare. */
export function getLocationHash(): string {
  return window.location.hash;
}

export function subscribeLocation(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Splits `#/tab/thread`. A bare `#`, an empty or third segment, a bad escape: all nowhere. */
export function parseLocation(hash: string): AppLocation {
  if (!hash.startsWith(PREFIX)) return NOWHERE;
  const segments = hash.slice(PREFIX.length).split(SEPARATOR);
  if (segments.length > MAX_SEGMENTS || segments.some((segment) => segment === "")) return NOWHERE;
  try {
    const [tab, threadId] = segments.map((segment) => decodeURIComponent(segment));
    return { tab, threadId: threadId ?? null };
  } catch {
    return NOWHERE;
  }
}

/** The location the browser is at now. */
export function useLocation(): AppLocation {
  const hash = useSyncExternalStore(subscribeLocation, getLocationHash, getLocationHash);
  return useMemo(() => parseLocation(hash), [hash]);
}

/** Goes somewhere, leaving a history entry: back returns to the place the reader came from. */
export function pushLocation(tab: string, threadId: string | null): void {
  write(tab, threadId, false);
}

/** Restates where the reader already is - a tab switch, a draft that just became a thread. */
export function replaceLocation(tab: string, threadId: string | null): void {
  write(tab, threadId, true);
}

/** Drops the fragment: a logged-out URL carries no location, so the login view shows none. */
export function clearLocation(): void {
  if (!getLocationHash()) return;
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  emit();
}

function write(tab: string, threadId: string | null, replace: boolean): void {
  const head = `${PREFIX}${encodeURIComponent(tab)}`;
  const hash = threadId ? `${head}${SEPARATOR}${encodeURIComponent(threadId)}` : head;
  if (hash === getLocationHash()) return;
  if (replace) window.history.replaceState(null, "", hash);
  else window.history.pushState(null, "", hash);
  emit();
}
