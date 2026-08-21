/** Session brick: token persistence, display-only JWT decode, logout. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type AuthModule = typeof import("./auth");

function makeToken(claims: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode(claims)}.signature`;
}

const validToken = makeToken({
  sub: "acme_analyst",
  tenant_id: "acme",
  exp: Math.floor(Date.now() / 1000) + 1800,
});

/** Re-imports the module so its load-time read of sessionStorage runs again. */
async function loadAuth(): Promise<AuthModule> {
  vi.resetModules();
  return import("./auth");
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("decodeDisplayClaims", () => {
  it("reads sub and tenant_id out of the payload", async () => {
    const auth = await loadAuth();
    expect(auth.decodeDisplayClaims(validToken)).toMatchObject({
      sub: "acme_analyst",
      tenant_id: "acme",
    });
  });

  it("returns null for garbage, a missing payload or missing claims", async () => {
    const auth = await loadAuth();
    expect(auth.decodeDisplayClaims("not-a-jwt")).toBeNull();
    expect(auth.decodeDisplayClaims("header.%%%.sig")).toBeNull();
    expect(auth.decodeDisplayClaims(makeToken({ sub: "x" }))).toBeNull();
  });
});

describe("session lifecycle", () => {
  it("startSession exposes the identity, persists the token and notifies", async () => {
    const auth = await loadAuth();
    const listener = vi.fn();
    auth.subscribe(listener);

    const session = auth.startSession(validToken);

    expect(session).toEqual({ token: validToken, username: "acme_analyst", tenantId: "acme" });
    expect(auth.getSession()).toEqual(session);
    expect(window.sessionStorage.getItem("secure-rls.token")).toBe(validToken);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("rejects an undecodable token and stays logged out", async () => {
    const auth = await loadAuth();

    expect(auth.startSession("garbage")).toBeNull();
    expect(auth.getSession()).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("clearSession drops the token from memory and from storage", async () => {
    const auth = await loadAuth();
    auth.startSession(validToken);
    const listener = vi.fn();
    auth.subscribe(listener);

    auth.clearSession();

    expect(auth.getSession()).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("unsubscribes cleanly", async () => {
    const auth = await loadAuth();
    const listener = vi.fn();
    auth.subscribe(listener)();

    auth.startSession(validToken);

    expect(listener).not.toHaveBeenCalled();
  });
});

describe("stored token on load", () => {
  it("restores a live session from sessionStorage", async () => {
    window.sessionStorage.setItem("secure-rls.token", validToken);

    const auth = await loadAuth();

    expect(auth.getSession()).toMatchObject({ username: "acme_analyst", tenantId: "acme" });
  });

  it("discards an expired stored token", async () => {
    window.sessionStorage.setItem(
      "secure-rls.token",
      makeToken({ sub: "acme_analyst", tenant_id: "acme", exp: Math.floor(Date.now() / 1000) - 60 }),
    );

    const auth = await loadAuth();

    expect(auth.getSession()).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("survives storage that throws (private mode, storage disabled)", async () => {
    const denied = () => {
      throw new Error("storage denied");
    };
    vi.spyOn(window.sessionStorage, "getItem").mockImplementation(denied);
    vi.spyOn(window.sessionStorage, "setItem").mockImplementation(denied);
    vi.spyOn(window.sessionStorage, "removeItem").mockImplementation(denied);

    const auth = await loadAuth();

    expect(auth.getSession()).toBeNull();
    expect(auth.startSession(validToken)).toMatchObject({ tenantId: "acme" });
    expect(() => auth.clearSession()).not.toThrow();
    expect(auth.getSession()).toBeNull();
  });
});
