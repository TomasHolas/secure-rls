import { describe, expect, it } from "vitest";

import { diffSql } from "./sqldiff";
import type { DiffSegment } from "./sqldiff";

/** The statements come from db.execute_scoped: the model's text, then sqlglot's render of the tree. */
const GENERATED = "SELECT department, AVG(salary) AS avg_salary FROM employees GROUP BY department";
const EXECUTED =
  "SELECT department, AVG(salary) AS avg_salary FROM " +
  "(SELECT * FROM employees WHERE employees.tenant_id = ?) AS employees GROUP BY department";

function text(segments: DiffSegment[]): string {
  return segments.map((segment) => segment.text).join("");
}

function added(segments: DiffSegment[]): string[] {
  return segments.filter((segment) => segment.kind === "add").map((segment) => segment.text);
}

describe("diffSql", () => {
  it("reproduces the executed statement exactly from its kept and added segments", () => {
    const segments = diffSql(GENERATED, EXECUTED) ?? [];
    expect(text(segments.filter((segment) => segment.kind !== "del"))).toBe(EXECUTED);
  });

  it("marks the scoping subquery as the only addition", () => {
    const segments = diffSql(GENERATED, EXECUTED) ?? [];
    expect(added(segments)).toEqual(["(SELECT * FROM employees WHERE employees.tenant_id = ?) AS"]);
    expect(segments.some((segment) => segment.kind === "del")).toBe(false);
  });

  it("ignores sqlglot's re-rendering: keyword case and the model's line breaks", () => {
    const generated = "select\n  name,\n  salary\nfrom employees\nwhere salary > 100";
    const executed =
      "SELECT name, salary FROM (SELECT * FROM employees WHERE employees.tenant_id = ?) " +
      "AS employees WHERE salary > 100";
    const segments = diffSql(generated, executed) ?? [];
    expect(added(segments)).toEqual(["(SELECT * FROM employees WHERE employees.tenant_id = ?) AS"]);
  });

  it("marks the tenant predicate once per scoped reference when the table is joined to itself", () => {
    const generated = "SELECT a.name FROM employees a JOIN employees b ON a.department = b.department";
    const executed =
      "SELECT a.name FROM (SELECT * FROM employees WHERE employees.tenant_id = ?) AS a " +
      "JOIN (SELECT * FROM employees WHERE employees.tenant_id = ?) AS b " +
      "ON a.department = b.department";
    const segments = diffSql(generated, executed) ?? [];
    expect(added(segments).filter((mark) => mark.includes("tenant_id"))).toHaveLength(2);
    expect(text(segments.filter((segment) => segment.kind !== "del"))).toBe(executed);
    expect(segments[segments.length - 1]).toEqual({
      kind: "same",
      text: " b ON a.department = b.department",
    });
  });

  it("shows a replaced stretch as one deletion beside its replacement, never as nothing", () => {
    const segments = diffSql("SELECT name FROM employees LIMIT 5", "SELECT name FROM x LIMIT 9") ?? [];
    expect(segments.filter((segment) => segment.kind === "del").map((s) => s.text)).toEqual([
      "employees LIMIT 5",
    ]);
    expect(text(segments.filter((segment) => segment.kind !== "del"))).toBe(
      "SELECT name FROM x LIMIT 9",
    );
  });

  it("declines to align a statement past the token cap so a huge query is shown whole", () => {
    const long = `SELECT ${Array.from({ length: 300 }, (_, index) => `c${index}`).join(", ")} FROM employees`;
    expect(diffSql(long, long)).toBeNull();
  });

  it("treats an empty executed statement as nothing to mark", () => {
    expect(diffSql("SELECT 1", "")).toEqual([{ kind: "same", text: "" }]);
  });
});
