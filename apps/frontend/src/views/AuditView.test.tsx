/**
 * The Audit tab, rendered against a faked HTTP client (ADR 0002's audit log, ADR 0014 as amended).
 *
 * What is asserted is what a reader can take away from the tab: the log as the server ordered it
 * - newest first, never reordered here - every tenant's entries side by side, the verdict of each
 * row readable at a glance, and both statements present with the full text on the cell a reader
 * hovers. The paging is a request like every other listing's, and the reload is the one extra
 * control the tab has, because a log grows while the reader is on another tab.
 *
 * The refused row is the one worth pinning twice: a `danger` verdict, its kind beside it, and no
 * executed statement at all - which is the log saying nothing ran, rather than saying nothing.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuditView } from "./AuditView";

const api = vi.hoisted(() => ({
  browseAudit: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  ...api,
}));

const SCOPED =
  "SELECT name FROM (SELECT * FROM employees WHERE employees.tenant_id = ?) AS employees LIMIT 200";
const NEWEST = {
  id: 41,
  ts: "2026-08-24T18:12:07.512000+00:00",
  tenant: "beta",
  generated_sql: "SELECT * FROM employees WHERE tenant_id = 'acme'",
  verdict: "rejected",
  executed_sql: null,
  rowcount: null,
  error_kind: "policy_violation",
};
const OLDER = {
  id: 40,
  ts: "2026-08-24T18:11:44.108000+00:00",
  tenant: "acme",
  generated_sql: "SELECT name FROM employees",
  verdict: "approved",
  executed_sql: SCOPED,
  rowcount: 3,
  error_kind: null,
};
const LOG = { entries: [NEWEST, OLDER], total: 41, page: 1, page_size: 25 };

async function show() {
  const view = render(<AuditView />);
  await screen.findByText("SELECT name FROM employees");
  return view;
}

beforeEach(() => {
  api.browseAudit.mockResolvedValue(LOG);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the audit trail", () => {
  it("shows the log in the order the server served it, newest first", async () => {
    const view = await show();

    const first = view.container.querySelectorAll("tbody tr")[0];
    expect(first?.textContent).toContain("beta");
    expect(api.browseAudit).toHaveBeenCalledWith(1);
    expect(view.container.querySelectorAll("tbody tr")).toHaveLength(2);
  });

  it("names every tenant the log holds, not the one signed in", async () => {
    const view = await show();

    const tenants = Array.from(view.container.querySelectorAll(".pill-neutral")).map(
      (pill) => pill.textContent,
    );
    expect(tenants).toContain("beta");
    expect(tenants).toContain("acme");
  });

  it("reads a refusal as one: danger, its kind, and no statement that ran", async () => {
    const view = await show();

    const refused = view.container.querySelectorAll("tbody tr")[0];
    expect(refused?.querySelector(".pill-danger")?.textContent).toBe("refused");
    expect(refused?.textContent).toContain("policy_violation");
    expect(refused?.querySelectorAll(".audit-sql")).toHaveLength(1);
  });

  it("carries both statements of an approved call, the executed one scoped", async () => {
    const view = await show();

    const approved = view.container.querySelectorAll("tbody tr")[1];
    const statements = Array.from(approved?.querySelectorAll(".audit-sql") ?? []);
    expect(statements.map((cell) => cell.getAttribute("title"))).toEqual([
      OLDER.generated_sql,
      SCOPED,
    ]);
    expect(approved?.textContent).toContain("ok");
    expect(approved?.querySelectorAll("td")[6]?.textContent).toBe("3");
  });

  it("says what the total counts and what page of it is on screen", async () => {
    await show();

    expect(screen.getByText(/41 entries · page 1 of 2/)).toBeTruthy();
    expect(screen.getByText(/showing 2 of 41 entries/)).toBeTruthy();
  });

  it("states that this is the server's record and holds no result row", async () => {
    await show();

    expect(screen.getByText(/no result row is stored here/)).toBeTruthy();
    expect(screen.getByText(/all tenants, unscoped by design/)).toBeTruthy();
  });

  it("asks the server for the next page rather than slicing the one it has", async () => {
    await show();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(api.browseAudit).toHaveBeenCalledWith(2));
  });

  it("has no filter to narrow the log with, only the pager and a reload", async () => {
    const view = await show();

    expect(view.container.querySelectorAll("input, select")).toHaveLength(0);
    expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Previous",
      "Next",
      "Reload",
    ]);
  });

  it("reloads the page it is on, because the log grew while the reader was elsewhere", async () => {
    await show();

    fireEvent.click(screen.getByRole("button", { name: "Reload" }));

    await waitFor(() => expect(api.browseAudit).toHaveBeenCalledTimes(2));
    expect(api.browseAudit).toHaveBeenLastCalledWith(1);
  });

  it("shows the server's own reason when the log cannot be read", async () => {
    const { ApiError } = await import("../lib/api");
    api.browseAudit.mockRejectedValue(new ApiError(503, "The audit store is unavailable."));

    render(<AuditView />);

    expect(await screen.findByText("The audit store is unavailable.")).toBeTruthy();
    expect(screen.getByText("Nothing to show.")).toBeTruthy();
  });

  it("holds an empty log as an empty log rather than as a failure", async () => {
    api.browseAudit.mockResolvedValue({ entries: [], total: 0, page: 1, page_size: 25 });

    render(<AuditView />);

    expect(await screen.findByText("The audit log holds no entry yet.")).toBeTruthy();
  });
});
