/**
 * The Records tab, rendered against a faked HTTP client (issue #88, ADR 0014, issue #117).
 *
 * What is asserted is the contract between the view and the server: the filters, the sort and
 * the page it asks for, and that what it shows is what came back - the true total, the page the
 * server actually served, and the statement it ran. The view sorts and pages nothing itself,
 * which is the property these tests pin: every reorder is a request.
 *
 * The fixture page is now the dataset's 1000 rows rather than one tenant's 450, because the
 * listing is (issue #117). Two things are pinned that were not there before: the tenant chips are
 * a real filter that reaches the query, and no total on screen appears without saying what it is
 * a total of - "1000 rows · all tenants" or "450 rows · tenant acme", never a bare number.
 *
 * The statement is pinned in both of its states (issue #139): the fact that this listing carries
 * no scoping is a caption a reader cannot miss, and the SQL behind it is closed until asked for -
 * present in the document only once the disclosure is open, and never deleted.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecordsView } from "./RecordsView";
import { expectChipStripHeight, expectOneControlHeight } from "../test/styles";

const api = vi.hoisted(() => ({
  browseRecords: vi.fn(),
  listDepartments: vi.fn(),
  listTenants: vi.fn(),
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
  [2, "beta", "Alan Turing", "Engineering", 120000, 3.5, "2020-02-02"],
];
const EXECUTED = "SELECT user_id, tenant_id, name FROM employees ORDER BY user_id LIMIT 25";
const PAGE = {
  columns: COLUMNS,
  rows: ROWS,
  total: 1000,
  page: 1,
  page_size: 25,
  sort: "user_id",
  direction: "asc",
  executed_sql: EXECUTED,
  ignored: [],
};
const DEPARTMENTS = [
  { value: "Engineering", employees: 206 },
  { value: "Sales", employees: 214 },
];
const TENANTS = [
  { value: "acme", employees: 450 },
  { value: "beta", employees: 350 },
  { value: "gamma", employees: 200 },
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

/** The tenant chip a reader clicks; `All` is the chip that clears the filter. */
function tenantChip(name: string): HTMLElement {
  return screen.getByRole("button", { name, pressed: false });
}

beforeEach(() => {
  api.browseRecords.mockResolvedValue(PAGE);
  api.listDepartments.mockResolvedValue(DEPARTMENTS);
  api.listTenants.mockResolvedValue(TENANTS);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the records tab", () => {
  it("shows the whole dataset and the true total, saying what the total counts", async () => {
    const view = await show();

    expect(screen.getByText(/Every row of the dataset, all tenants/)).toBeTruthy();
    expect(screen.getByText(/1,000 matching rows · all tenants/)).toBeTruthy();
    expect(screen.getByText(/showing 2 of 1,000 rows · all tenants/)).toBeTruthy();
    expect(view.container.querySelectorAll("tbody tr")).toHaveLength(2);
  });

  it("states that the listing carries no tenant scoping without a reader opening anything", async () => {
    const view = await show();

    expect(
      screen.getByText(/This listing is the whole dataset, unscoped by design/),
    ).toBeTruthy();
    expect(view.container.querySelector(".code-block")).toBeNull();
    expect(screen.getByRole("button", { name: /show the SQL this page ran/ })).toHaveProperty(
      "ariaExpanded",
      "false",
    );
  });

  it("hands over the statement it ran on one click, still labelled as unscoped", async () => {
    const view = await show();

    fireEvent.click(screen.getByRole("button", { name: /show the SQL this page ran/ }));

    const block = view.container.querySelector(".code-block")!;
    expect(block.textContent).toContain(EXECUTED);
    expect(block.textContent).toContain("executed without tenant scoping");

    fireEvent.click(screen.getByRole("button", { name: /hide the SQL this page ran/ }));
    expect(view.container.querySelector(".code-block")).toBeNull();
  });

  it("shows every row of the page, not the trace's visual cap", async () => {
    const many = Array.from({ length: 25 }, (_, index) => [
      index + 1,
      "beta",
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

  it("offers the departments the listing holds, with their counts", async () => {
    await show();

    const options = Array.from(
      (screen.getByLabelText("Department") as HTMLSelectElement).options,
      (option) => option.textContent,
    );
    expect(options).toEqual(["any department", "Engineering (206)", "Sales (214)"]);
  });

  it("offers the dataset's tenants as chips, with no counts on them and All pressed", async () => {
    const view = await show();

    const chips = Array.from(view.container.querySelectorAll(".chip"));
    expect(chips.map((chip) => chip.textContent)).toEqual(["All", "acme", "beta", "gamma"]);
    expect(chips.map((chip) => chip.getAttribute("aria-pressed"))).toEqual([
      "true",
      "false",
      "false",
      "false",
    ]);
  });

  it("names the chip group so the strip is not a row of unlabelled buttons", async () => {
    await show();

    expect(screen.getByRole("group", { name: "Tenant" })).toBeTruthy();
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

    api.browseRecords.mockResolvedValue({ ...PAGE, page: 40, total: 1000 });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Next" })).toHaveProperty("disabled", true),
    );
    expect(screen.getByText(/page 40 of 40/)).toBeTruthy();
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

    expect(await screen.findByText(/No row of the dataset matches those filters/)).toBeTruthy();
    expect(screen.getByText(/0 matching rows · all tenants/)).toBeTruthy();
  });

  it("keeps every filter control on one height and one baseline", async () => {
    const view = await show();

    expectOneControlHeight(view.container.querySelector(".filter-grid"), 10);
    expectChipStripHeight(view.container.querySelector(".chip-row"));
  });

  it("keeps the two bounds of a filter in one cell, so no wrap can split them", async () => {
    const view = await show();

    const pairs = Array.from(view.container.querySelectorAll(".field-pair"));
    expect(
      pairs.map((pair) => Array.from(pair.querySelectorAll("label"), (label) => label.textContent)),
    ).toEqual([
      ["Salary from", "Salary to"],
      ["Score from", "Score to"],
      ["Hired from", "Hired to"],
    ]);
  });

  it("closes the form with its actions rather than stranding them between filters", async () => {
    const view = await show();

    const grid = view.container.querySelector(".filter-grid")!;
    const actions = grid.querySelector(".filter-actions")!;
    expect(actions.parentElement).toBe(grid);
    expect(grid.lastElementChild).toBe(actions);
    expect(Array.from(actions.querySelectorAll("button"), (button) => button.textContent)).toEqual([
      "Reset",
      "Apply",
    ]);
  });

  it("asks for a date in ISO, the way the table and the server both write it", async () => {
    await show();

    const from = screen.getByLabelText("Hired from") as HTMLInputElement;
    expect(from.type).toBe("text");
    expect(from.placeholder).toBe("2020-01-31");

    fireEvent.change(from, { target: { value: "2020-01-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(lastQuery().hired_from).toBe("2020-01-31"));
  });

  it("sends the tenant chip the reader picked, and says what the filtered total counts", async () => {
    await show();

    fireEvent.click(tenantChip("acme"));
    api.browseRecords.mockResolvedValue({ ...PAGE, total: 450 });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(lastQuery().tenant_id).toBe("acme"));
    expect(lastQuery().page).toBe(1);
    expect(await screen.findByText(/450 matching rows · tenant acme/)).toBeTruthy();
    expect(screen.getByText(/showing 2 of 450 rows · tenant acme/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "acme" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("clears the tenant filter through the All chip", async () => {
    await show();

    fireEvent.click(tenantChip("acme"));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(lastQuery().tenant_id).toBe("acme"));

    fireEvent.click(tenantChip("All"));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(lastQuery().tenant_id).toBe(""));
    expect(screen.getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("re-counts the department options for the tenant the reader applied", async () => {
    await show();

    fireEvent.click(tenantChip("acme"));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(api.listDepartments).toHaveBeenLastCalledWith("acme"));
  });

  it("keeps the rows readable when either option list is unavailable", async () => {
    api.listDepartments.mockRejectedValue(new Error("boom"));
    api.listTenants.mockRejectedValue(new Error("boom"));

    const view = await show();

    expect((screen.getByLabelText("Department") as HTMLSelectElement).options).toHaveLength(1);
    expect(view.container.querySelectorAll(".chip")).toHaveLength(1);
  });
});
