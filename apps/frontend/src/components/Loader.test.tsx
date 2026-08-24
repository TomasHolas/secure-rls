/**
 * The loader brick: what it composes, what it announces, what the clock does to it, and what the
 * reduced-motion block freezes (issue #123).
 *
 * The two things worth pinning hardest are the ones the reference implementation gets wrong. Its
 * elapsed time accumulates inside a `setInterval`, which under-reports every wait by however late
 * the timer ran; ours is subtracted from a start timestamp, which is what the clock test proves.
 * And its metrics are literals in JSX; ours are custom properties, which is why no element the
 * brick renders may carry a `style` attribute at all.
 */

import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Loader } from "./Loader";
import { formatSeconds } from "../lib/format";
import { hasRule, reducedMotionStyle } from "../test/styles";

afterEach(cleanup);

/** A fixed point on the clock, so an elapsed span is a number the test can name. */
const NOW = 1_700_000_000_000;
const WAITED_MS = 90_000;
/** The brick's own read interval: one tenth of a second, the resolution `formatSeconds` prints. */
const TICK_MS = 100;
const GRID_CELLS = 9;

function elapsed(container: HTMLElement): string | null {
  return container.querySelector(".loader-elapsed")?.textContent ?? null;
}

describe("what the loader composes", () => {
  it("is the grid alone when it is given nothing to say", () => {
    const { container } = render(<Loader />);

    expect(container.querySelectorAll(".loader-cell")).toHaveLength(GRID_CELLS);
    expect(container.querySelector(".loader-label")).toBeNull();
    expect(container.querySelector(".loader-elapsed")).toBeNull();
  });

  it("adds the label, and the elapsed time only when it is given a start", () => {
    const { container } = render(<Loader label="Loading notes…" />);

    expect(container.querySelector(".loader-label")?.textContent).toBe("Loading notes…");
    expect(container.querySelector(".loader-elapsed")).toBeNull();
  });

  it("takes the page scale where it stands alone in an empty panel", () => {
    const { container } = render(<Loader scale="page" label="Loading rows…" />);

    expect(container.querySelector(".loader.loader-page")).not.toBeNull();
    expect(container.querySelector(".loader.loader-inline")).toBeNull();
  });

  it("keeps every metric in CSS: nothing it renders carries an inline style", () => {
    const { container } = render(<Loader label="Thinking" since={NOW} scale="page" />);

    expect(container.querySelector("[style]")).toBeNull();
  });
});

describe("what the loader announces", () => {
  it("is a status region whose grid is hidden and whose label is real text", () => {
    const { container } = render(<Loader label="Loading notes…" />);
    const status = container.querySelector("[role=status]");

    expect(status).not.toBeNull();
    expect(status?.querySelector(".loader-grid")?.getAttribute("aria-hidden")).toBe("true");
    expect(status?.querySelector(".loader-label")?.hasAttribute("aria-hidden")).toBe(false);
    expect(status?.textContent).toContain("Loading notes…");
  });

  it("hides the elapsed time, which changes ten times a second, from that region", () => {
    const { container } = render(<Loader label="Thinking" since={NOW} />);

    expect(container.querySelector(".loader-elapsed")?.getAttribute("aria-hidden")).toBe("true");
  });
});

describe("the elapsed time", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("is taken off the clock, so a long wait cannot under-report itself", () => {
    const { container } = render(<Loader label="Thinking" since={NOW - WAITED_MS} />);

    // A counter that accumulated its own ticks would read 0.0s here, having only just mounted.
    expect(elapsed(container)).toBe("90.0s");

    act(() => {
      vi.advanceTimersByTime(TICK_MS);
    });

    expect(elapsed(container)).toBe("90.1s");
  });

  it("is formatted by lib/format.ts and by nothing else", () => {
    const { container } = render(<Loader since={NOW - WAITED_MS} />);

    expect(elapsed(container)).toBe(formatSeconds(WAITED_MS));
  });

  it("stops reading the clock once it is gone", () => {
    const { unmount } = render(<Loader since={NOW} />);

    unmount();

    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("reduced motion", () => {
  it("freezes the grid and the label sweep, and leaves the clock alone", () => {
    expect(reducedMotionStyle(".loader-cell")?.animation).toBe("none");
    expect(reducedMotionStyle(".loader-label")?.animation).toBe("none");
    expect(reducedMotionStyle(".loader-elapsed")).toBeNull();
  });

  it("freezes it dim, because that is the state the base rule paints", () => {
    const { container } = render(<Loader />);

    expect(getComputedStyle(container.querySelector(".loader-cell")!).opacity).toBe(
      "var(--loader-dim)",
    );
  });
});

describe("the spinner it replaced", () => {
  it("is gone from the stylesheet, so there is one loading idiom and not two", () => {
    expect(hasRule(".loader-cell")).toBe(true);
    expect(hasRule(".loader-spin")).toBe(false);
    expect(hasRule(".trace-live")).toBe(false);
  });
});
