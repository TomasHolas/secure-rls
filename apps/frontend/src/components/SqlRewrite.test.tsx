/**
 * The SQL rewrite brick: what a reader sees of the tenant scoping, in the diff and in the pair
 * it falls back to.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SqlRewrite } from "./SqlRewrite";

afterEach(cleanup);

const GENERATED = "SELECT department, AVG(salary) AS avg FROM employees GROUP BY department";
const EXECUTED =
  "SELECT department, AVG(salary) AS avg FROM (SELECT * FROM employees " +
  "WHERE employees.tenant_id = ?) AS employees GROUP BY department";

function marks(container: HTMLElement, selector: string): string[] {
  return [...container.querySelectorAll(selector)].map((mark) => mark.textContent ?? "");
}

describe("the rewrite as a diff", () => {
  it("shows the statement that ran, whole", () => {
    const { container } = render(<SqlRewrite generated={GENERATED} executed={EXECUTED} />);

    expect(container.querySelector(".code-block-body")?.textContent).toBe(EXECUTED);
    expect(screen.getByText("executed after tenant scoping")).toBeTruthy();
  });

  it("marks only what the scoping layer added, tenant predicate and bound parameter", () => {
    const { container } = render(<SqlRewrite generated={GENERATED} executed={EXECUTED} />);

    expect(marks(container, ".code-block-body .sql-add")).toEqual([
      "(SELECT * FROM employees WHERE employees.tenant_id = ?) AS",
    ]);
    expect(marks(container, ".code-block-body .sql-del")).toEqual([]);
  });

  it("leaves the model's own words unmarked", () => {
    const { container } = render(<SqlRewrite generated={GENERATED} executed={EXECUTED} />);
    const body = container.querySelector(".code-block-body") as HTMLElement;
    const marked = marks(container, ".code-block-body .sql-add").join(" ");

    expect(body.textContent).toContain("SELECT department, AVG(salary) AS avg FROM");
    expect(marked).not.toContain("AVG");
    expect(marked).not.toContain("GROUP BY");
  });

  it("says what the highlight means, because a colour alone claims nothing", () => {
    render(<SqlRewrite generated={GENERATED} executed={EXECUTED} />);

    expect(screen.getByText(/added by the RLS rewrite/)).toBeTruthy();
  });
});

describe("the pair behind it", () => {
  it("shows both statements whole when the reader asks, and goes back", () => {
    const { container } = render(<SqlRewrite generated={GENERATED} executed={EXECUTED} />);

    fireEvent.click(screen.getByRole("button", { name: "show both" }));

    expect(container.querySelector(".sql-pair")).not.toBeNull();
    expect(screen.getByText(GENERATED)).toBeTruthy();
    expect(screen.getByText(EXECUTED)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "show the diff" }));

    expect(container.querySelector(".sql-rewrite")).not.toBeNull();
  });

  it("falls back to the pair, with no way back, for a statement too long to align", () => {
    const long = `SELECT ${Array.from({ length: 300 }, (_, index) => `c${index}`).join(", ")} FROM employees`;
    const { container } = render(<SqlRewrite generated={long} executed={long} />);

    expect(container.querySelector(".sql-pair")).not.toBeNull();
    expect(container.querySelector(".sql-add")).toBeNull();
    expect(screen.queryByRole("button", { name: "show the diff" })).toBeNull();
  });
});
