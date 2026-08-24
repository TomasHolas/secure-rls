/**
 * The conversation rail's own mechanisms (issue #114), driven by a hand-made store so nothing
 * here needs the API: the collapse that clips instead of re-laying out, the identity menu, the
 * inline search and the one gliding highlight. `App.test.tsx` keeps the shell-level promises
 * (threads listed, one open, delete behind a confirm) and this file the rail's behaviour.
 *
 * The claim each block pins is the one a screenshot cannot: that the collapsed rail leaves no
 * focusable control behind its own edge, that the column inside it is laid out at one width in
 * both states - which is why its icons cannot move sideways - and that Escape gets a reader out
 * of the search and the menu.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationsSidebar } from "./ConversationsSidebar";
import { getSession, startSession } from "../auth";
import type { ConversationsStore } from "../lib/conversations";
import { reducedMotionStyle } from "../test/styles";

const NEWEST = { thread_id: "t2", title: "median salary in engineering", created: "2026-08-20T12:15:00+00:00" };
const OLDEST = { thread_id: "t1", title: "average salary per department", created: "2026-08-19T12:40:00+00:00" };

const SEARCH = "Search conversations";
const CLOSE_SEARCH = "Close search conversations";
const IDENTITY = "Signed in as acme_analyst, tenant acme";

function token(): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const claims = {
    sub: "acme_analyst",
    tenant_id: "acme",
    exp: Math.floor(Date.now() / 1000) + 1800,
  };
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode(claims)}.signature`;
}

function store(overrides: Partial<ConversationsStore> = {}): ConversationsStore {
  return {
    threads: [NEWEST, OLDEST],
    activeId: null,
    replay: [],
    chatKey: 0,
    loading: false,
    error: null,
    newChat: vi.fn(),
    select: vi.fn(),
    remove: vi.fn(),
    startThread: vi.fn(),
    titleThread: vi.fn(),
    ...overrides,
  };
}

function rail(overrides: Partial<ConversationsStore> = {}) {
  return render(<ConversationsSidebar store={store(overrides)} />).container;
}

function titles(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll(".rail-item-title"), (node) => node.textContent ?? "");
}

/** The search's own control, by role: the box shares its name while closed, but not its role. */
function searchToggle(): HTMLElement {
  return screen.getByRole("button", { name: SEARCH });
}

function collapse(): void {
  fireEvent.click(screen.getByLabelText("Hide conversations"));
}

/** Every control the collapsed rail clips out of view, and whether Tab can still reach it. */
function tabbableWhenClipped(container: HTMLElement): string[] {
  const clipped = ".rail-item-open, .rail-item-delete, .rail-search-toggle, .rail-search-input";
  return Array.from(container.querySelectorAll<HTMLElement>(clipped))
    .filter((control) => control.tabIndex >= 0)
    .map((control) => control.className);
}

beforeEach(() => {
  window.sessionStorage.clear();
  startSession(token());
});

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});

describe("collapsing the rail", () => {
  it("keeps the clipped column at one width, which is what stops its icons moving", () => {
    const container = rail();
    const inner = container.querySelector(".sidebar-inner")!;

    const expanded = getComputedStyle(inner).width;
    collapse();

    expect(getComputedStyle(inner).width).toBe(expanded);
    expect(expanded).toBe("var(--rail-width)");
    expect(getComputedStyle(container.querySelector(".sidebar-collapsed")!).width).toBe(
      "var(--rail-collapsed-width)",
    );
  });

  // The defect this pins: with `overflow: hidden` the aside was still a scroll container, so
  // focusing a control the collapse had clipped made the browser scroll the column sideways -
  // icons and all - and the rail rendered as an empty strip. A clip container cannot scroll.
  it("clips the rail instead of making it a scroll container", () => {
    const container = rail();

    expect(getComputedStyle(container.querySelector(".sidebar")!).overflow).toBe("clip");
  });

  // The other defect a screenshot found: the clip cut the copy mid-word. It fades instead, and
  // the identity chip's own spans are part of that list, so this is also what catches a rename
  // inside TenantPill breaking the collapsed rail.
  it("fades the copy the clip would otherwise cut off mid-word", () => {
    const container = rail();
    const faded = () =>
      [".sidebar-title", ".sidebar-body", ".rail-search", ".tenant-pill-user"].map(
        (selector) => getComputedStyle(container.querySelector(selector)!).opacity,
      );
    expect(faded()).toEqual(["1", "1", "1", "1"]);

    collapse();

    expect(faded()).toEqual(["0", "0", "0", "0"]);
  });

  it("stops travelling under reduced motion, in the one block that owns that", () => {
    rail();

    const still = reducedMotionStyle(".sidebar, .rail-copy, .rail-search-input, .rail-glide");

    expect(still?.transition).toBe("none");
  });

  it("takes the labels out of the accessibility tree and the controls out of the Tab order", () => {
    const container = rail();
    expect(tabbableWhenClipped(container)).not.toEqual([]);

    collapse();

    expect(tabbableWhenClipped(container)).toEqual([]);
    expect(container.querySelector(".sidebar-list")?.getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector(".sidebar-title")?.getAttribute("aria-hidden")).toBe("true");
    expect(screen.queryByRole("button", { name: new RegExp(NEWEST.title) })).toBeNull();
  });

  it("keeps the controls whose icons are still on screen reachable", () => {
    rail();

    collapse();

    expect(screen.getByRole("button", { name: /new chat/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: IDENTITY })).toBeTruthy();
    expect(screen.getByLabelText("Show conversations").getAttribute("aria-expanded")).toBe("false");
  });

  it("gives every control back when the rail is expanded again", () => {
    const container = rail();
    collapse();

    fireEvent.click(screen.getByLabelText("Show conversations"));

    expect(container.querySelector(".sidebar-collapsed")).toBeNull();
    expect(tabbableWhenClipped(container)).not.toEqual([]);
    expect(titles(container)).toEqual([NEWEST.title, OLDEST.title]);
  });

  it("closes and clears a search the reader leaves open behind the collapse", () => {
    const container = rail();
    fireEvent.click(searchToggle());
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "median" } });
    expect(titles(container)).toEqual([NEWEST.title]);

    collapse();
    fireEvent.click(screen.getByLabelText("Show conversations"));

    expect(container.querySelector(".rail-search")?.getAttribute("data-open")).toBe("false");
    expect(titles(container)).toEqual([NEWEST.title, OLDEST.title]);
  });
});

describe("the inline search", () => {
  it("opens on its own control, focuses itself, and filters the titles already loaded", () => {
    const container = rail();

    fireEvent.click(searchToggle());

    const box = screen.getByRole("searchbox");
    expect(container.querySelector(".rail-search")?.getAttribute("data-open")).toBe("true");
    expect(document.activeElement).toBe(box);

    fireEvent.change(box, { target: { value: "MEDIAN" } });

    expect(titles(container)).toEqual([NEWEST.title]);
  });

  it("says so when the query matches no thread", () => {
    const container = rail();
    fireEvent.click(searchToggle());

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "headcount" } });

    expect(titles(container)).toEqual([]);
    expect(screen.getByText("No chats found.")).toBeTruthy();
  });

  it("closes on Escape, clearing the query and handing focus back to the control", () => {
    const container = rail();
    fireEvent.click(searchToggle());
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "median" } });

    fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Escape" });

    expect(titles(container)).toEqual([NEWEST.title, OLDEST.title]);
    expect(container.querySelector(".rail-search")?.getAttribute("data-open")).toBe("false");
    expect(document.activeElement).toBe(searchToggle());
  });

  it("is out of the Tab order and the accessibility tree while it is closed", () => {
    const container = rail();

    const box = container.querySelector<HTMLInputElement>(".rail-search-input")!;
    expect(box.tabIndex).toBe(-1);
    expect(box.getAttribute("aria-hidden")).toBe("true");

    fireEvent.click(searchToggle());

    expect(screen.getByRole("searchbox").getAttribute("aria-hidden")).toBeNull();
    expect(screen.getByRole("button", { name: CLOSE_SEARCH }).getAttribute("aria-expanded")).toBe("true");
  });

  it("leaves the loading and empty states of the rail alone", () => {
    rail({ threads: [], loading: true });
    expect(screen.getByText("Loading your conversations.")).toBeTruthy();

    cleanup();
    rail({ threads: [] });
    expect(screen.getByText("No conversations yet.")).toBeTruthy();
  });
});

describe("the identity menu", () => {
  it("opens on the identity chip and signs the session out", () => {
    rail();
    const trigger = screen.getByRole("button", { name: IDENTITY });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(trigger);

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(screen.getByRole("menuitem", { name: /sign out/i }));

    expect(getSession()).toBeNull();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes on Escape and hands focus back to the chip", () => {
    rail();
    const trigger = screen.getByRole("button", { name: IDENTITY });
    fireEvent.click(trigger);

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(getSession()).not.toBeNull();
  });

  it("closes on a pointerdown outside it", () => {
    rail();
    fireEvent.click(screen.getByRole("button", { name: IDENTITY }));

    fireEvent.pointerDown(document.body);

    expect(screen.queryByRole("menu")).toBeNull();
  });
});

describe("the rows", () => {
  it("carries the full title on every row, so a truncated one reads on hover", () => {
    const container = rail();

    const rows = Array.from(container.querySelectorAll(".rail-item-open"), (row) =>
      row.getAttribute("title"),
    );

    expect(rows).toEqual([NEWEST.title, OLDEST.title]);
  });

  it("moves one highlight to the row under the pointer instead of lighting each row", () => {
    const container = rail();
    const list = container.querySelector(".sidebar-list")!;
    expect(container.querySelectorAll(".rail-glide")).toHaveLength(1);

    fireEvent.pointerMove(container.querySelectorAll(".rail-item")[1]);

    expect(list.className).toContain("gliding");
    expect(container.querySelector<HTMLElement>(".rail-glide")!.style.transform).toMatch(
      /^translateY\(\d+px\)$/,
    );

    fireEvent.pointerLeave(list);

    expect(list.className).not.toContain("gliding");
  });
});
