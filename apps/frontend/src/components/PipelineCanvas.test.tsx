/**
 * The pipeline canvas: what it renders, what a selection does to it, and what a drag must not do.
 *
 * What cannot be asserted here is geometry. jsdom performs no layout, so every measured box comes
 * back zero and the connector paths are degenerate - which is why the tests below pin the things
 * that do not need pixels: six steps in order, one selection at a time, the two connectors a
 * selected card lights, a drag that offsets a card without toggling it, and the metrics living in
 * the stylesheet rather than in JSX. The curves themselves are verified on screenshots.
 *
 * Which element takes the pointer capture is pinned here as well, and it is not cosmetic: a
 * browser retargets the compatibility mouse events - the click included - to whatever holds the
 * capture, so capturing on the node rather than on the card inside it costs every click. jsdom
 * does not model that, which is exactly why the element is asserted rather than the outcome.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { PipelineCanvas } from "./PipelineCanvas";
import { reducedMotionStyle } from "../test/styles";

/** jsdom ships no pointer capture, which the drag takes out on the card it starts on. */
const capture = vi.fn();

beforeAll(() => {
  Element.prototype.setPointerCapture = capture;
  Element.prototype.releasePointerCapture = vi.fn();
});

afterEach(() => {
  cleanup();
  capture.mockClear();
});

const STEPS = 6;
/** The steps in flow order, by the layer each card claims on its kind pill. */
const KINDS = ["input", "layer 2", "layer 2.5", "layer 3", "layer 4", "result"];

function cards(): HTMLElement[] {
  return screen.getAllByRole("button");
}

function lit(container: HTMLElement): number {
  return container.querySelectorAll(".pipeline-edge.lit").length;
}

/** A drag of one card: press, move past the slop, release, and the click a release ends with. */
function dragBy(node: Element, card: HTMLElement, dx: number, dy: number): void {
  fireEvent.pointerDown(node, { pointerId: 1, clientX: 0, clientY: 0 });
  fireEvent.pointerMove(node, { pointerId: 1, clientX: dx, clientY: dy });
  fireEvent.pointerUp(node, { pointerId: 1, clientX: dx, clientY: dy });
  fireEvent.click(card);
}

describe("what the canvas renders", () => {
  it("is one card per pipeline step, in flow order, each with its layer and its mechanism", () => {
    const { container } = render(<PipelineCanvas />);

    expect(cards()).toHaveLength(STEPS);
    const pills = Array.from(container.querySelectorAll(".pipeline-node .pill"));
    expect(pills.map((pill) => pill.textContent)).toEqual(KINDS);
    expect(container.querySelectorAll(".pipeline-mech")).toHaveLength(STEPS);
    expect(screen.getByText(/one SELECT over employees, and nothing else/)).toBeTruthy();
    expect(screen.getByText(/the tenant bound, never interpolated/)).toBeTruthy();
  });

  it("joins the steps with one connector per consecutive pair, none lit at rest", () => {
    const { container } = render(<PipelineCanvas />);

    expect(container.querySelectorAll(".pipeline-edge")).toHaveLength(STEPS - 1);
    expect(lit(container)).toBe(0);
  });

  it("carries no metric in JSX beyond the position and hue of a card", () => {
    const { container } = render(<PipelineCanvas />);

    const inline = Array.from(
      container.querySelectorAll<HTMLElement>(".pipeline-node, .pipeline-card"),
    ).flatMap((el) => Array.from(el.style));
    expect(new Set(inline)).toEqual(new Set(["--pipe-x", "--pipe-dx", "--pipe-dy", "--pipe-hue"]));
    expect(
      getComputedStyle(document.documentElement).getPropertyValue("--pipe-dot-gap").trim(),
    ).toMatch(/^\d+px$/);
  });

  it("takes the lit stroke and the card's shadow out of the reduced-motion transition", () => {
    render(<PipelineCanvas />);

    expect(reducedMotionStyle(".pipeline-edge, .pipeline-card")?.transition).toBe("none");
  });
});

describe("selecting a step", () => {
  it("presses the card and lights the connectors on both of its sides", () => {
    const { container } = render(<PipelineCanvas />);
    const middle = cards()[2];

    fireEvent.click(middle);

    expect(middle.getAttribute("aria-pressed")).toBe("true");
    expect(lit(container)).toBe(2);
  });

  it("lights one connector for a step at either end of the pipeline", () => {
    const { container } = render(<PipelineCanvas />);

    fireEvent.click(cards()[0]);
    expect(lit(container)).toBe(1);

    fireEvent.click(cards()[STEPS - 1]);
    expect(lit(container)).toBe(1);
  });

  it("releases the same card on a second click", () => {
    const { container } = render(<PipelineCanvas />);
    const card = cards()[1];

    fireEvent.click(card);
    fireEvent.click(card);

    expect(card.getAttribute("aria-pressed")).toBe("false");
    expect(lit(container)).toBe(0);
  });

  it("holds one selection at a time, so the previous card is released by the next", () => {
    render(<PipelineCanvas />);

    fireEvent.click(cards()[1]);
    fireEvent.click(cards()[4]);

    expect(cards()[1].getAttribute("aria-pressed")).toBe("false");
    expect(cards()[4].getAttribute("aria-pressed")).toBe("true");
  });

  it("is a button, so Enter and Space toggle it with no key handler of ours", () => {
    render(<PipelineCanvas />);

    for (const card of cards()) {
      expect(card.tagName).toBe("BUTTON");
      expect(card.getAttribute("aria-pressed")).toBe("false");
    }
  });
});

describe("dragging a step", () => {
  it("offsets the card and does not toggle the selection the release clicks", () => {
    const { container } = render(<PipelineCanvas />);
    const node = container.querySelectorAll<HTMLElement>(".pipeline-node")[1];
    const card = cards()[1];

    dragBy(node, card, 60, 20);

    expect(node.style.getPropertyValue("--pipe-dx")).not.toBe("0px");
    expect(card.getAttribute("aria-pressed")).toBe("false");
  });

  it("captures the pointer on the card, which is what the ending click has to reach", () => {
    const { container } = render(<PipelineCanvas />);
    const node = container.querySelectorAll<HTMLElement>(".pipeline-node")[1];

    fireEvent.pointerDown(node, { pointerId: 1, clientX: 0, clientY: 0 });

    expect(capture.mock.instances[0]).toBe(cards()[1]);
  });

  it("still selects when the pointer never left the slop, which is a click", () => {
    const { container } = render(<PipelineCanvas />);
    const node = container.querySelectorAll<HTMLElement>(".pipeline-node")[1];
    const card = cards()[1];

    dragBy(node, card, 1, 1);

    expect(node.style.getPropertyValue("--pipe-dx")).toBe("0px");
    expect(card.getAttribute("aria-pressed")).toBe("true");
  });
});
