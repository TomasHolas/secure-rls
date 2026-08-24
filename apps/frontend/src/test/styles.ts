/**
 * The app's real stylesheet inside the test document, so a layout contract can be asserted
 * rather than eyeballed on a screenshot (issue #115).
 *
 * Importing this module loads `styles/app.css` the same way `main.tsx` does; vitest runs with
 * `css: true`, so jsdom parses it and applies the cascade. A control's `height` then computes to
 * the declaration that actually reached it — three controls reporting the same
 * `var(--control-height)` is proof they take one height from one place, and a row a view forgot
 * to opt in reports no height at all.
 *
 * jsdom does not substitute the variable for pixels, which is the point: what is pinned is the
 * shared source, not a number a designer would then have to come back and edit in a test.
 */

import "../styles/app.css";

const SHARED_HEIGHT = "var(--control-height)";

/** The computed height of every control in a row, in document order. */
export function controlHeights(row: Element): string[] {
  return Array.from(row.querySelectorAll("input, select, button, textarea")).map(
    (control) => getComputedStyle(control).height,
  );
}

/** How many rules in the parsed stylesheet declare the shared height; one is the contract. */
export function controlHeightDeclarations(): number {
  let declarations = 0;
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules)) {
      const style = (rule as CSSStyleRule).style;
      if (style && style.getPropertyValue("--control-height")) declarations += 1;
    }
  }
  return declarations;
}

/**
 * Assert a row is one row: every control in it takes the shared height, and no field inside adds
 * a bottom margin that would drop the button below the box beside it.
 */
export function expectOneControlHeight(row: Element | null, count: number): void {
  if (!row) throw new Error("no control row was rendered");
  const heights = controlHeights(row);
  if (heights.length !== count) {
    throw new Error(`expected ${count} controls in the row, found ${heights.length}`);
  }
  const shared = new Set(heights);
  if (shared.size !== 1 || !shared.has(SHARED_HEIGHT)) {
    throw new Error(`controls disagree on their height: ${JSON.stringify(heights)}`);
  }
  for (const field of row.querySelectorAll(".field")) {
    const margin = getComputedStyle(field).marginBottom;
    if (margin !== "0px") throw new Error(`a field in a control row offsets the row: ${margin}`);
  }
}
