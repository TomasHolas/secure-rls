/**
 * The control-row contract, asserted at the layer that owns it (issue #115).
 *
 * The defect this pins was a filter row whose inputs were 47px, whose native date inputs were
 * 49px and whose buttons were 32px, and a search row whose button sat 16px below the box beside
 * it. The fix is one declaration: every control in a `.control-row` takes `--control-height`, and
 * no field inside adds a margin to align against. These tests fail if a control escapes the rule,
 * if the height is declared twice, or if the rule leaks into a form that is not such a row.
 */

import { afterEach, describe, expect, it } from "vitest";

import { controlHeightDeclarations, controlHeights } from "../test/styles";

const CONTROLS = `
  <div class="field"><label for="a">A</label><input id="a" class="input"></div>
  <div class="field"><label for="b">B</label><select id="b" class="select"></select></div>
  <button class="btn btn-primary">Apply</button>
  <textarea class="input"></textarea>
`;

function row(className: string): Element {
  const element = document.createElement("form");
  element.className = className;
  element.innerHTML = CONTROLS;
  document.body.appendChild(element);
  return element;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("the control-row contract", () => {
  it("gives every control in the row one height, from one custom property", () => {
    const heights = controlHeights(row("control-row"));

    expect(heights).toHaveLength(4);
    expect(new Set(heights)).toEqual(new Set(["var(--control-height)"]));
  });

  it("resolves that property to a real length at the root", () => {
    const height = getComputedStyle(document.documentElement).getPropertyValue("--control-height");

    expect(height.trim()).toMatch(/^\d+px$/);
  });

  it("declares the height in exactly one place, so a second one cannot drift from it", () => {
    expect(controlHeightDeclarations()).toBe(1);
  });

  it("puts the controls of a row on one baseline by taking the fields' bottom margin away", () => {
    const controlRow = row("control-row");

    for (const field of controlRow.querySelectorAll(".field")) {
      expect(getComputedStyle(field).marginBottom).toBe("0px");
    }
  });

  it("leaves a form that is not a control row alone", () => {
    const notARow = row("form-card");

    expect(controlHeights(notARow)).not.toContain("var(--control-height)");
    expect(getComputedStyle(notARow.querySelector(".field")!).marginBottom).not.toBe("0px");
  });

  it("sizes a select in a control row like the box beside it, not like the model picker", () => {
    const select = row("control-row").querySelector(".select")!;

    expect(getComputedStyle(select).fontSize).toBe("var(--text-md)");
    expect(getComputedStyle(select).fontFamily).toBe("var(--font-body)");
  });
});
