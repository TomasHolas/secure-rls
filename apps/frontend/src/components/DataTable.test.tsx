/** DataTable fixtures: the cap, the empty state, and numeric cells through the one formatter. */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DataTable } from "./DataTable";

afterEach(cleanup);

const COLUMNS = ["name", "salary", "performance_score", "notes"];
const ROWS: unknown[][] = [
  ["Ada", 155230, 4.25, "solid quarter"],
  ["Alan", 98000, 3.4666, null],
];

describe("DataTable", () => {
  it("groups thousands in numeric cells and leaves text cells verbatim", () => {
    const { container } = render(<DataTable columns={COLUMNS} rows={ROWS} />);

    const cells = [...container.querySelectorAll("tbody tr:first-child td")].map(
      (td) => td.textContent,
    );
    expect(cells).toEqual(["Ada", "155,230", "4.25", "solid quarter"]);
  });

  it("marks only numeric cells as numeric and renders a null as a dash", () => {
    const { container } = render(<DataTable columns={COLUMNS} rows={ROWS} />);

    const row = container.querySelectorAll("tbody tr:last-child td");
    expect([...row].map((td) => td.className)).toEqual(["", "num mono", "num mono", ""]);
    expect(row[1].textContent).toBe("98,000");
    expect(row[2].textContent).toBe("3.47");
    expect(row[3].textContent).toBe("-");
  });

  it("caps the rows it shows and says how many it hid", () => {
    const many = Array.from({ length: 5 }, (_, index) => ["row", index]);
    const { container } = render(<DataTable columns={["name", "n"]} rows={many} maxRows={2} />);

    expect(container.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(container.querySelector(".data-table-note")?.textContent).toContain("3 more of the 5");
  });

  it("shows the empty message instead of a headerless table", () => {
    const { container } = render(<DataTable columns={COLUMNS} rows={[]} empty="Nothing here." />);

    expect(container.querySelector("table")).toBeNull();
    expect(container.querySelector(".data-table-note")?.textContent).toBe("Nothing here.");
  });
});
