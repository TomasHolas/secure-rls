/** HTTP client brick: Bearer attachment, sliding-session refresh, 401 handling, login errors. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function makeToken(claims: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode(claims)}.signature`;
}

const token = makeToken({
  sub: "beta_analyst",
  tenant_id: "beta",
  exp: Math.floor(Date.now() / 1000) + 1800,
});

/** Fresh module instances so one test's session never leaks into the next. */
async function load() {
  vi.resetModules();
  const auth = await import("../auth");
  const api = await import("./api");
  return { auth, api };
}

function jsonResponse(body: unknown, status = 200, refreshedToken?: string): Response {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (refreshedToken) headers.set("X-Refreshed-Token", refreshedToken);
  return new Response(JSON.stringify(body), { status, headers });
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("login", () => {
  it("posts the credentials and returns the token", async () => {
    const { api } = await load();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ token }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.login("beta_analyst", "secret")).resolves.toBe(token);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/login$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ username: "beta_analyst", password: "secret" });
  });

  it("raises a 401 ApiError on bad credentials", async () => {
    const { api } = await load();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "nope" }, 401)));

    await expect(api.login("beta_analyst", "wrong")).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      message: "Invalid username or password.",
    });
  });

  it("raises on a success response without a token", async () => {
    const { api } = await load();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({})));

    await expect(api.login("beta_analyst", "secret")).rejects.toMatchObject({ name: "ApiError" });
  });
});

describe("apiFetch", () => {
  it("attaches the Bearer header from the session", async () => {
    const { auth, api } = await load();
    auth.startSession(token);
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.apiFetch("/health");

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe(`Bearer ${token}`);
  });

  it("sends no Authorization header when logged out", async () => {
    const { api } = await load();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.apiFetch("/health");

    expect((fetchMock.mock.calls[0][1].headers as Headers).has("Authorization")).toBe(false);
  });

  it("clears the session and throws on any 401", async () => {
    const { auth, api } = await load();
    auth.startSession(token);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "expired" }, 401)));

    await expect(api.apiFetch("/conversations")).rejects.toMatchObject({ status: 401 });
    expect(auth.getSession()).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("passes other error responses through to the caller", async () => {
    const { api } = await load();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "boom" }, 500)));

    const response = await api.apiFetch("/chat", { method: "POST" });
    expect(response.status).toBe(500);
  });
});

describe("sliding session", () => {
  const refreshed = makeToken({
    sub: "beta_analyst",
    tenant_id: "beta",
    exp: Math.floor(Date.now() / 1000) + 7200,
  });

  it("adopts a refreshed token from the response header", async () => {
    const { auth, api } = await load();
    auth.startSession(token);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ status: "ok" }, 200, refreshed)),
    );

    await api.apiFetch("/conversations");

    expect(auth.getSession()?.token).toBe(refreshed);
    expect(window.sessionStorage.getItem("secure-rls.token")).toBe(refreshed);
  });

  it("notifies subscribers so the next request carries the new token", async () => {
    const { auth, api } = await load();
    auth.startSession(token);
    const changed = vi.fn();
    auth.subscribe(changed);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ status: "ok" }, 200, refreshed))
      .mockResolvedValueOnce(jsonResponse({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);

    await api.apiFetch("/conversations");
    await api.apiFetch("/conversations");

    expect(changed).toHaveBeenCalled();
    const headers = fetchMock.mock.calls[1][1].headers as Headers;
    expect(headers.get("Authorization")).toBe(`Bearer ${refreshed}`);
  });

  it("keeps the current token when no refresh header is sent", async () => {
    const { auth, api } = await load();
    auth.startSession(token);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ status: "ok" })));

    await api.apiFetch("/conversations");

    expect(auth.getSession()?.token).toBe(token);
  });

  it("adopts the refresh a chat stream response carries", async () => {
    const { auth, api } = await load();
    auth.startSession(token);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("data: {}\n\n", {
          status: 200,
          headers: { "Content-Type": "text/event-stream", "X-Refreshed-Token": refreshed },
        }),
      ),
    );

    await api.openChatStream({ thread_id: "t1", message: "hi" });

    expect(auth.getSession()?.token).toBe(refreshed);
  });

  it("does not adopt anything from a 401", async () => {
    const { auth, api } = await load();
    auth.startSession(token);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "expired" }, 401, refreshed)),
    );

    await expect(api.apiFetch("/conversations")).rejects.toMatchObject({ status: 401 });
    expect(auth.getSession()).toBeNull();
  });
});

describe("health", () => {
  it("reports the prompt-guardrail position the server states", async () => {
    const { api } = await load();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse({ status: "ok", version: "0.1.0", prompt_guardrails: false }),
        ),
    );

    await expect(api.getHealth()).resolves.toEqual({
      status: "ok",
      version: "0.1.0",
      prompt_guardrails: false,
    });
  });

  it("reports the on position when the server states it", async () => {
    const { api } = await load();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ status: "ok", prompt_guardrails: true })),
    );

    await expect(api.getHealth()).resolves.toMatchObject({ prompt_guardrails: true });
  });

  // Unknown is its own state: reporting it as off would announce the demo mode to a reader
  // behind an older backend or a body-rewriting proxy, which is a lie about provenance.
  it.each([
    ["absent", {}],
    ["the string false", { prompt_guardrails: "false" }],
    ["the number zero", { prompt_guardrails: 0 }],
    ["null", { prompt_guardrails: null }],
  ])("reports no position at all when the field is %s", async (_label, extra) => {
    const { api } = await load();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ status: "ok", ...extra })),
    );

    await expect(api.getHealth()).resolves.toMatchObject({ prompt_guardrails: null });
  });

  it("raises an ApiError when the server cannot answer", async () => {
    const { api } = await load();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 503)));

    await expect(api.getHealth()).rejects.toMatchObject({ name: "ApiError", status: 503 });
  });
});
