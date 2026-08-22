/**
 * The Records tab, rendered against a faked HTTP client (issue #88, ADR 0014).
 *
 * What is asserted is the contract between the view and the server: the filters, the sort and
 * the page it asks for, and that what it shows is what came back - the true total, the page the
 * server actually served, and the tenant-scoped statement it ran. The view sorts and pages
 * nothing itself, which is the property these tests pin: every reorder is a request.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecordsView } from "./RecordsView";

const api = vi.hoisted(() => ({
  browseRecords: vi.fn(),
  listDepartments: vi.fn(),
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

const COLUMNS = [
  "user_id",
  "tenant_id",
  "name",
  "department",
  "salary",
  "performance_score",
  "hire_date",
];
const ROWS = [
  [1, "acme", "Ada Lovelace", "Engineering", 100000, 4.5, "2019-01-01"],
  [2, "acme", "Alan Turing", "Engineering", 120000, 3.5, "2020-02-02"],
];
const EXECUTED =
  "SELECT user_id, tenant_id, name FROM (SELECT * FROM employees WHERE employees.tenant_id = ?) AS employees";
const PAGE = {
  columns: COLUMNS,
  rows: ROWS,
  total: 450,
  page: 1,
  page_size: 25,
  sort: "user_id",
  direction: "asc",
  executed_sql: EXECUTED,
  ignored: [],
};
const DEPARTMENTS = [
  { department: "Engineering", employees: 95 },
  { department: "Sales", employees: 94 },
];

async function show() {
  const view = render(<RecordsView tenant="acme" />);
  await screen.findByText("Ada Lovelace");
  return view;
}

/** The query of the most recent request, which is what the view is asserted through. */
function lastQuery(): Record<string, unknown> {
  const calls = api.browseRecords.mock.calls;
  return calls[calls.length - 1][0] as Record<string, unknown>;
}

/** The extra parameter the most recent request carried, which is the second argument. */
function lastProbe(): string | undefined {
  const calls = api.browseRecords.mock.calls;
  return calls[calls.length - 1][1] as string | undefined;
}

beforeEach(() => {
  api.browseRecords.mockResolvedValue(PAGE);
  api.listDepartments.mockResolvedValue(DEPARTMENTS);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the records tab", () => {
  it("shows the tenant's rows, the true total and the statement the server ran", async () => {
    const view = await show();

    expect(screen.getByText(/Every row the acme tenant can see/)).toBeTruthy();
    expect(screen.getByText("450 matching rows")).toBeTruthy();
    expect(screen.getByText(/showing 2 of 450 rows/)).toBeTruthy();
    expect(view.container.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(view.container.querySelector(".code-block")?.textContent).toContain(EXECUTED);
  });

  it("shows every row of the page, not the trace's visual cap", async () => {
    const many = Array.from({ length: 25 }, (_, index) => [
      index + 1,
      "acme",
      `Person ${index + 1}`,
      "Sales",
      1000,
      3,
      "2020-01-01",
    ]);
    api.browseRecords.mockResolvedValue({ ...PAGE, rows: many });

    const view = render(<RecordsView tenant="acme" />);

    await screen.findByText("Person 25");
    expect(view.container.querySelectorAll("tbody tr")).toHaveLength(25);
  });

  it("asks the server for the filters the reader applied, from the first page", async () => {
    await show();

    fireEvent.change(screen.getByLabelText("Name contains"), { target: { value: "ada" } });
    fireEvent.change(screen.getByLabelText("Salary from"), { target: { value: "90000" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(lastQuery().name).toBe("ada"));
    expect(lastQuery()).toMatchObject({ name: "ada", salary_min: "90000", page: 1 });
  });

  it("does not request anything while the reader is still typing", async () => {
    await show();

    fireEvent.change(screen.getByLabelText("Name contains"), { target: { value: "ada" } });

    expect(api.browseRecords).toHaveBeenCalledTimes(1);
  });

  it("offers only the departments the tenant has, with their headcounts", async () => {
    await show();

    const options = Array.from(
      (screen.getByLabelText("Department") as HTMLSelectElement).options,
      (option) => option.textContent,
    );
    expect(options).toEqual(["any department", "Engineering (95)", "Sales (94)"]);
  });

  it("clears the filters and asks again on reset", async () => {
    await show();
    fireEvent.change(screen.getByLabelText("Name contains"), { target: { value: "ada" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(lastQuery().name).toBe("ada"));

    fireEvent.click(screen.getByRole("button", { name: "Reset" }));

    await waitFor(() => expect(lastQuery().name).toBe(""));
    expect((screen.getByLabelText("Name contains") as HTMLInputElement).value).toBe("");
  });

  it("asks the server to sort, and flips the direction on the column already sorted", async () => {
    await show();

    fireEvent.click(screen.getByRole("button", { name: /salary/ }));
    await waitFor(() => expect(lastQuery().sort).toBe("salary"));
    expect(lastQuery().direction).toBe("asc");

    api.browseRecords.mockResolvedValue({ ...PAGE, sort: "salary", direction: "asc" });
    fireEvent.click(screen.getByRole("button", { name: /salary/ }));

    await waitFor(() => expect(lastQuery().direction).toBe("desc"));
    expect(lastQuery().page).toBe(1);
  });

  it("marks the sorted column for a screen reader as well", async () => {
    api.browseRecords.mockResolvedValue({ ...PAGE, sort: "salary", direction: "desc" });
    const view = await show();

    const sorted = Array.from(view.container.querySelectorAll("th")).find(
      (cell) => cell.getAttribute("aria-sort") === "descending",
    );
    expect(sorted?.textContent).toContain("salary");
  });

  it("pages forward and back, and cannot page off either end", async () => {
    await show();
    expect(screen.getByRole("button", { name: "Previous" })).toHaveProperty("disabled", true);

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(lastQuery().page).toBe(2));

    api.browseRecords.mockResolvedValue({ ...PAGE, page: 18, total: 450 });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Next" })).toHaveProperty("disabled", true),
    );
    expect(screen.getByText(/page 18 of 18/)).toBeTruthy();
  });

  it("says what the server refused, in the server's own words", async () => {
    const { ApiError } = await import("../lib/api");
    api.browseRecords.mockRejectedValue(new ApiError(400, "sort must be one of ['name']"));

    render(<RecordsView tenant="acme" />);

    expect(await screen.findByText("sort must be one of ['name']")).toBeTruthy();
  });

  it("reports an empty result as an empty result, never as an error", async () => {
    api.browseRecords.mockResolvedValue({ ...PAGE, rows: [], total: 0 });

    render(<RecordsView tenant="acme" />);

    expect(await screen.findByText(/No row of this tenant matches those filters/)).toBeTruthy();
    expect(screen.getByText("0 matching rows")).toBeTruthy();
  });

  it("sends a parameter the reader appended and shows what the server did with it", async () => {
    await show();

    fireEvent.change(screen.getByLabelText("Extra query parameter"), {
      target: { value: "tenant_id=beta" },
    });
    api.browseRecords.mockResolvedValue({
      ...PAGE,
      ignored: [{ name: "tenant_id", reason: "read from your verified token (ADR 0002, layer 1)" }],
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(lastProbe()).toBe("tenant_id=beta"));
    expect(await screen.findByText(/read from your verified token/)).toBeTruthy();
    expect(screen.getByText("450 matching rows")).toBeTruthy();
  });

  it("keeps the rows readable when the department list is unavailable", async () => {
    api.listDepartments.mockRejectedValue(new Error("boom"));

    await show();

    expect((screen.getByLabelText("Department") as HTMLSelectElement).options).toHaveLength(1);
  });
});
