/** Login flow and the header identity badge, rendered. */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function makeToken(claims: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode(claims)}.signature`;
}

const token = makeToken({
  sub: "acme_analyst",
  tenant_id: "acme",
  exp: Math.floor(Date.now() / 1000) + 1800,
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Fresh module graph per test: auth.ts is a singleton store. */
async function load() {
  vi.resetModules();
  const auth = await import("../auth");
  const { LoginView } = await import("./LoginView");
  const { SessionBadge } = await import("./SessionBadge");
  return { auth, LoginView, SessionBadge };
}

function fillCredentials(password = "secret") {
  fireEvent.change(screen.getByLabelText("Username"), { target: { value: "acme_analyst" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("LoginView", () => {
  it("renders a username field, a masked password field and a disabled submit", async () => {
    const { LoginView } = await load();
    const { container } = render(<LoginView />);

    expect(screen.getByLabelText("Username")).toBeTruthy();
    expect(screen.getByLabelText("Password").getAttribute("type")).toBe("password");
    expect(screen.getByRole("button", { name: /sign in/i }).hasAttribute("disabled")).toBe(true);
    expect(container.querySelector(".form-error")).toBeNull();
  });

  it("composes the bricks rather than hand-rolled markup", async () => {
    const { LoginView } = await load();
    const { container } = render(<LoginView />);

    expect(container.querySelector("form.form-card")).not.toBeNull();
    expect(container.querySelectorAll("div.field input.input")).toHaveLength(2);
    expect(container.querySelector("button.btn.btn-primary.btn-block")).not.toBeNull();
  });

  it("carries no glyph on the submit button at rest, and the loader only while pending", async () => {
    const { LoginView } = await load();
    let settle: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(
        new Promise<Response>((resolve) => {
          settle = resolve;
        }),
      ),
    );
    const { container } = render(<LoginView />);

    const glyphs = () => container.querySelectorAll("button.btn-block .material-symbols-outlined");
    const loaders = () => container.querySelectorAll("button.btn-block .loader");
    expect(glyphs()).toHaveLength(0);
    expect(loaders()).toHaveLength(0);

    fillCredentials();
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    // The grid alone at button scale: the button's own "Signing in..." is the label already.
    expect(glyphs()).toHaveLength(0);
    expect(loaders()).toHaveLength(1);
    expect(loaders()[0].querySelectorAll(".loader-cell")).toHaveLength(9);
    expect(loaders()[0].querySelector(".loader-label")).toBeNull();

    settle?.(jsonResponse({ detail: "nope" }, 401));
    await screen.findByRole("alert");
    expect(loaders()).toHaveLength(0);
  });

  it("reveals the password on the eye and masks it again, with the label and the glyph following", async () => {
    const { LoginView } = await load();
    const { container } = render(<LoginView />);
    const field = () => screen.getByLabelText("Password") as HTMLInputElement;
    const glyph = () =>
      container.querySelector(".field-reveal .material-symbols-outlined")?.textContent;

    const show = screen.getByRole("button", { name: "Show password" });
    expect(show.getAttribute("aria-pressed")).toBe("false");
    expect(show.getAttribute("aria-controls")).toBe(field().id);
    expect(glyph()).toBe("visibility");

    fireEvent.click(show);

    const hide = screen.getByRole("button", { name: "Hide password" });
    expect(field().getAttribute("type")).toBe("text");
    expect(hide.getAttribute("aria-pressed")).toBe("true");
    expect(glyph()).toBe("visibility_off");

    fireEvent.click(hide);

    expect(field().getAttribute("type")).toBe("password");
    expect(screen.getByRole("button", { name: "Show password" }).getAttribute("aria-pressed")).toBe(
      "false",
    );
    expect(glyph()).toBe("visibility");
  });

  // The reveal is state in the brick and nowhere else, so a remount cannot come back revealed.
  it("forgets the reveal when the field goes away, and stores it nowhere", async () => {
    const { LoginView } = await load();
    render(<LoginView />);
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(screen.getByLabelText("Password").getAttribute("type")).toBe("text");

    cleanup();
    render(<LoginView />);

    expect(screen.getByLabelText("Password").getAttribute("type")).toBe("password");
    expect(screen.getByRole("button", { name: "Show password" })).toBeTruthy();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("carries the eye on the password only, inside the field's own box", async () => {
    const { LoginView } = await load();
    const { container } = render(<LoginView />);

    expect(container.querySelectorAll(".field-reveal")).toHaveLength(1);
    const control = container.querySelector(".field-control")!;
    expect(control.querySelector("input")?.id).toBe("login-password");
    expect(control.querySelector(".field-reveal")).not.toBeNull();
  });

  it("disables the eye while the request is in flight, with the fields", async () => {
    const { LoginView } = await load();
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise<Response>(() => {})));
    render(<LoginView />);

    fillCredentials();
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(screen.getByRole("button", { name: "Show password" }).hasAttribute("disabled")).toBe(true);
  });

  it("names no credentials anywhere in the UI", async () => {
    const { LoginView } = await load();
    const { container } = render(<LoginView />);

    expect(container.textContent?.toLowerCase()).not.toMatch(/acme|beta|gamma|demo|analyst|password:/);
  });

  it("starts the session on a successful login", async () => {
    const { auth, LoginView } = await load();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ token })));
    render(<LoginView />);

    fillCredentials();
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(auth.getSession()).toMatchObject({ tenantId: "acme" }));
    expect(window.sessionStorage.getItem("secure-rls.token")).toBe(token);
  });

  it("shows an inline error on a 401 and stays logged out", async () => {
    const { auth, LoginView } = await load();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "nope" }, 401)));
    render(<LoginView />);

    fillCredentials("wrong");
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Invalid username or password.");
    expect(auth.getSession()).toBeNull();
  });

  it("reports an unreachable backend", async () => {
    const { LoginView } = await load();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<LoginView />);

    fillCredentials();
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect((await screen.findByRole("alert")).textContent).toMatch(/Cannot reach the backend/);
  });

  it("disables the fields and the button while the request is in flight", async () => {
    const { LoginView } = await load();
    let settle: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(
        new Promise<Response>((resolve) => {
          settle = resolve;
        }),
      ),
    );
    render(<LoginView />);

    fillCredentials();
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(screen.getByLabelText("Username").hasAttribute("disabled")).toBe(true);
    expect(screen.getByLabelText("Password").hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: /signing in/i }).hasAttribute("disabled")).toBe(true);

    settle?.(jsonResponse({ detail: "nope" }, 401));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Username").hasAttribute("disabled")).toBe(false);
  });
});

describe("SessionBadge", () => {
  it("shows the tenant and the user, and signs out on click", async () => {
    const { auth, SessionBadge } = await load();
    const session = auth.startSession(token)!;
    const { container } = render(<SessionBadge session={session} />);

    expect(container.querySelector(".tenant-pill-tenant")?.textContent).toBe("acme");
    expect(container.querySelector(".tenant-pill-user")?.textContent).toBe("acme_analyst");

    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));

    expect(auth.getSession()).toBeNull();
    expect(window.sessionStorage.length).toBe(0);
  });
});
