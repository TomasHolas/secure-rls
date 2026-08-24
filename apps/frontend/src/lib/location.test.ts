/**
 * The location brick: the hash vocabulary and the two ways of writing it.
 *
 * What is pinned here is the parse being strict - a fragment that is not exactly `#/tab` or
 * `#/tab/thread` is nowhere rather than a half-read location - and the writes being what the
 * history says they are: a push is somewhere to come back to, a replace is not, a write of the
 * hash already showing is neither, and every write reaches subscribers by one path whether the
 * browser or this module made it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearLocation,
  getLocationHash,
  parseLocation,
  pushLocation,
  replaceLocation,
  subscribeLocation,
} from "./location";

const THREAD = "6f1e2d3c4b5a69788796a5b4c3d2e1f0";

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("parsing a fragment", () => {
  it("reads a tab on its own and a tab with a thread", () => {
    expect(parseLocation("#/chat")).toEqual({ tab: "chat", threadId: null });
    expect(parseLocation("#/records")).toEqual({ tab: "records", threadId: null });
    expect(parseLocation(`#/chat/${THREAD}`)).toEqual({ tab: "chat", threadId: THREAD });
  });

  it("unescapes each segment", () => {
    expect(parseLocation("#/chat/a%20b")).toEqual({ tab: "chat", threadId: "a b" });
  });

  it("reads anything that is not the two shapes as nowhere", () => {
    const nowhere = { tab: null, threadId: null };
    expect(parseLocation("")).toEqual(nowhere);
    expect(parseLocation("#")).toEqual(nowhere);
    expect(parseLocation("#/")).toEqual(nowhere);
    expect(parseLocation("#chat")).toEqual(nowhere);
    expect(parseLocation("#/chat/")).toEqual(nowhere);
    expect(parseLocation(`#/chat/${THREAD}/turns`)).toEqual(nowhere);
    expect(parseLocation("#/chat/%zz")).toEqual(nowhere);
  });
});

describe("writing a location", () => {
  it("pushes a place to come back to and tells its subscribers", () => {
    const listener = vi.fn();
    const stop = subscribeLocation(listener);
    const entries = window.history.length;

    pushLocation("chat", THREAD);

    expect(getLocationHash()).toBe(`#/chat/${THREAD}`);
    expect(window.history.length).toBe(entries + 1);
    expect(listener).toHaveBeenCalledTimes(1);
    stop();
  });

  it("replaces in place, leaving the history where it was", () => {
    const listener = vi.fn();
    const stop = subscribeLocation(listener);
    const entries = window.history.length;

    replaceLocation("records", null);

    expect(getLocationHash()).toBe("#/records");
    expect(window.history.length).toBe(entries);
    expect(listener).toHaveBeenCalledTimes(1);
    stop();
  });

  it("escapes what it writes, so a parse reads back what was written", () => {
    pushLocation("chat", "a b");

    expect(getLocationHash()).toBe("#/chat/a%20b");
    expect(parseLocation(getLocationHash())).toEqual({ tab: "chat", threadId: "a b" });
  });

  it("does nothing at all when the hash already says it", () => {
    replaceLocation("chat", THREAD);
    const listener = vi.fn();
    const stop = subscribeLocation(listener);
    const entries = window.history.length;

    pushLocation("chat", THREAD);

    expect(window.history.length).toBe(entries);
    expect(listener).not.toHaveBeenCalled();
    stop();
  });

  it("drops the fragment entirely, and only when there is one", () => {
    replaceLocation("chat", THREAD);
    const listener = vi.fn();
    const stop = subscribeLocation(listener);

    clearLocation();

    expect(getLocationHash()).toBe("");
    expect(window.location.pathname).toBe("/");
    expect(listener).toHaveBeenCalledTimes(1);

    clearLocation();

    expect(listener).toHaveBeenCalledTimes(1);
    stop();
  });
});

describe("subscribing", () => {
  it("passes on the browser's own navigation and stops on unsubscribe", () => {
    const listener = vi.fn();
    const stop = subscribeLocation(listener);

    window.history.replaceState(null, "", "#/notes");
    window.dispatchEvent(new Event("hashchange"));

    expect(listener).toHaveBeenCalledTimes(1);
    expect(parseLocation(getLocationHash())).toEqual({ tab: "notes", threadId: null });

    stop();
    window.dispatchEvent(new Event("hashchange"));

    expect(listener).toHaveBeenCalledTimes(1);
  });
});
