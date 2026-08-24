/**
 * The debounce brick, on its own (issue #152).
 *
 * What is pinned is what a filter row depends on: a burst applies once and applies its LAST value,
 * nothing applies a millisecond early, `pending` is true for exactly the interval - that flag is
 * what keeps a half-typed value's refusal off the screen - and a cancelled or unmounted schedule
 * applies nothing at all. The applier is read at the moment the value comes due, so a view may
 * hand in a fresh closure on every render without restarting the clock.
 */

import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FILTER_DEBOUNCE_MS, useDebounced } from "./debounce";
import type { Debounced } from "./debounce";

let control: Debounced<string>;

function Probe({ apply }: { apply: (value: string) => void }) {
  control = useDebounced(apply);
  return <span>{String(control.pending)}</span>;
}

function tick(by: number): void {
  act(() => {
    vi.advanceTimersByTime(by);
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the filter debounce", () => {
  it("waits an interval a reader would not call slow", () => {
    expect(FILTER_DEBOUNCE_MS).toBeGreaterThan(0);
    expect(FILTER_DEBOUNCE_MS).toBeLessThan(1000);
  });

  it("applies the last value of a burst, once, when the burst has gone quiet", () => {
    vi.useFakeTimers();
    const apply = vi.fn();
    render(<Probe apply={apply} />);

    act(() => {
      control.schedule("a");
      control.schedule("ad");
      control.schedule("ada");
    });
    tick(FILTER_DEBOUNCE_MS - 1);
    expect(apply).not.toHaveBeenCalled();

    tick(1);
    expect(apply.mock.calls).toEqual([["ada"]]);
  });

  it("is pending for the interval and settled after it", () => {
    vi.useFakeTimers();
    const view = render(<Probe apply={vi.fn()} />);
    expect(view.container.textContent).toBe("false");

    act(() => control.schedule("ada"));
    expect(view.container.textContent).toBe("true");

    tick(FILTER_DEBOUNCE_MS);
    expect(view.container.textContent).toBe("false");
  });

  it("applies nothing once cancelled, and is no longer pending", () => {
    vi.useFakeTimers();
    const apply = vi.fn();
    const view = render(<Probe apply={apply} />);

    act(() => control.schedule("ada"));
    act(() => control.cancel());
    tick(FILTER_DEBOUNCE_MS);

    expect(apply).not.toHaveBeenCalled();
    expect(view.container.textContent).toBe("false");
  });

  it("applies through the newest callback, without restarting the interval", () => {
    vi.useFakeTimers();
    const first = vi.fn();
    const second = vi.fn();
    const view = render(<Probe apply={first} />);

    act(() => control.schedule("ada"));
    tick(FILTER_DEBOUNCE_MS - 1);
    view.rerender(<Probe apply={second} />);
    tick(1);

    expect(first).not.toHaveBeenCalled();
    expect(second.mock.calls).toEqual([["ada"]]);
  });

  it("applies nothing into a view that has gone away", () => {
    vi.useFakeTimers();
    const apply = vi.fn();
    const view = render(<Probe apply={apply} />);

    act(() => control.schedule("ada"));
    view.unmount();
    tick(FILTER_DEBOUNCE_MS);

    expect(apply).not.toHaveBeenCalled();
  });
});
