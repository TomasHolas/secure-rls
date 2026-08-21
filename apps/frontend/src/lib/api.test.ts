/** HTTP client brick: Bearer attachment, 401 handling, login errors. */

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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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
