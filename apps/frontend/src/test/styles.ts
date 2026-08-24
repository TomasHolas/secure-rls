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

/**
 * The computed height of every control in a row, in document order. A chip is not one of them: a
 * `ChipRow` is one cell that takes the row's height as a strip, with deliberately smaller chips
 * inside it (issue #139), and `expectChipStripHeight` pins that instead.
 */
export function controlHeights(row: Element): string[] {
  return Array.from(row.querySelectorAll("input, select, button:not(.chip), textarea")).map(
    (control) => getComputedStyle(control).height,
  );
}

/** A chip strip is one cell of a control row: the strip keeps the row's height, its chips do not. */
export function expectChipStripHeight(strip: Element | null): void {
  if (!strip) throw new Error("no chip row was rendered");
  const height = getComputedStyle(strip).minHeight;
  if (height !== SHARED_HEIGHT) throw new Error(`the chip strip is not one control tall: ${height}`);
  for (const chip of strip.querySelectorAll(".chip")) {
    if (getComputedStyle(chip).height === SHARED_HEIGHT) {
      throw new Error("a chip took the row's height instead of its own");
    }
  }
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
 * The declarations a selector picks up inside the reduced-motion block. jsdom does not evaluate a
 * media condition, so the rule is read out of the parsed stylesheet rather than computed on an
 * element - which is enough to pin what the block does and to which selector (issue #123).
 */
export function reducedMotionStyle(selector: string): CSSStyleDeclaration | null {
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules)) {
      const media = rule as CSSMediaRule;
      if (!media.conditionText?.includes("prefers-reduced-motion")) continue;
      for (const inner of Array.from(media.cssRules)) {
        const styleRule = inner as CSSStyleRule;
        if (styleRule.selectorText === selector) return styleRule.style;
      }
    }
  }
  return null;
}

/** Whether the stylesheet still carries a rule for a selector at all - a deleted idiom stays deleted. */
export function hasRule(selector: string): boolean {
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules)) {
      if ((rule as CSSStyleRule).selectorText === selector) return true;
    }
  }
  return false;
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
