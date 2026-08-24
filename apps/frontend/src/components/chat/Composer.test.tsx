/**
 * The prompt bar's own contract (issue #159): what the box does as it is typed into, who owns
 * the ceiling it stops growing at, when send can act, and that the picker moved inside the bar
 * without changing what it lists.
 *
 * jsdom has no layout, so a textarea reports `scrollHeight` 0 whatever is in it. The tests that
 * care about growth say what the content measures instead, which is exactly the number the brick
 * reads - and the cap is asserted where it lives, in the stylesheet, rather than as a number
 * duplicated here (the stylesheet is loaded into the test document by `test/styles`).
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Composer } from "./Composer";
import "../../test/styles";

const MODELS = ["qwen3:8b", "llama3.1:8b"];
const QUESTION = "average salary per department";
/** What a five-line draft would measure if jsdom laid one out: the cap, to the pixel. */
const FIVE_LINES = 124;

const onSend = vi.fn();
const onModelChange = vi.fn();

function renderBar(props: Partial<Parameters<typeof Composer>[0]> = {}) {
  return render(
    <Composer
      onSend={onSend}
      models={MODELS}
      model={MODELS[0]}
      onModelChange={onModelChange}
      {...props}
    />,
  );
}

function box(): HTMLTextAreaElement {
  return screen.getByLabelText("Question") as HTMLTextAreaElement;
}

function sendButton(): HTMLButtonElement {
  return screen.getByLabelText("Send") as HTMLButtonElement;
}

/** Say what the draft would measure, the way a browser would, before typing it. */
function measures(height: number): void {
  Object.defineProperty(box(), "scrollHeight", { configurable: true, get: () => height });
}

function type(value: string): void {
  fireEvent.change(box(), { target: { value } });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the prompt bar", () => {
  it("grows the box to whatever the draft measures", () => {
    renderBar();
    measures(FIVE_LINES);

    type("one\ntwo\nthree\nfour\nfive");

    expect(box().style.height).toBe(`${FIVE_LINES}px`);
  });

  it("shrinks back to one line once the question is sent", () => {
    renderBar();
    measures(FIVE_LINES);
    type("one\ntwo\nthree\nfour\nfive");
    measures(24);

    fireEvent.keyDown(box(), { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("one\ntwo\nthree\nfour\nfive");
    expect(box().style.height).toBe("24px");
  });

  it("leaves the ceiling to the stylesheet, and scrolls the box inside it", () => {
    const { container } = renderBar();
    const bar = container.querySelector(".composer-bar")!;

    expect(getComputedStyle(box()).maxHeight).toBe("var(--composer-cap)");
    expect(getComputedStyle(box()).overflowY).toBe("auto");
    expect(getComputedStyle(bar).getPropertyValue("--composer-cap").trim()).toMatch(/^\d+px$/);
  });

  it("sends on Enter and starts a line on Shift+Enter", () => {
    renderBar();
    type(QUESTION);

    fireEvent.keyDown(box(), { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith(QUESTION);
  });

  it("sends what the button is clicked on, trimmed", () => {
    renderBar();
    type(`  ${QUESTION}  `);

    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith(QUESTION);
  });

  it("keeps send disabled until there is something to send", () => {
    renderBar();

    expect(sendButton().disabled).toBe(true);

    type("   ");
    expect(sendButton().disabled).toBe(true);

    type(QUESTION);
    expect(sendButton().disabled).toBe(false);
  });

  it("disables the whole bar while a turn streams, so a second turn cannot race it", () => {
    renderBar({ disabled: true });
    type(QUESTION);

    expect(box().disabled).toBe(true);
    expect(sendButton().disabled).toBe(true);
    expect((screen.getByLabelText("Model") as HTMLSelectElement).disabled).toBe(true);

    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("holds the picker inside the bar, still listing what the server sent", () => {
    const { container } = renderBar();
    const picker = screen.getByLabelText("Model") as HTMLSelectElement;

    expect(container.querySelector(".composer-bar")!.contains(picker)).toBe(true);
    expect([...picker.options].map((option) => option.value)).toEqual(MODELS);
    expect(picker.value).toBe(MODELS[0]);

    fireEvent.change(picker, { target: { value: MODELS[1] } });
    expect(onModelChange).toHaveBeenCalledWith(MODELS[1]);
  });

  it("strips the picker's own chrome, because the bar around it carries the border", () => {
    renderBar();
    const picker = getComputedStyle(screen.getByLabelText("Model"));

    expect(picker.getPropertyValue("background")).toBe("none");
    // jsdom reports a transparent border in its rgba form.
    expect(picker.borderColor).toBe("rgba(0, 0, 0, 0)");
  });

  it("puts the bar's controls in reading order: the question, the picker, then send", () => {
    const { container } = renderBar();
    const controls = [...container.querySelectorAll<HTMLElement>("textarea, select, button")];

    expect(controls).toEqual([box(), screen.getByLabelText("Model"), sendButton()]);
  });
});
