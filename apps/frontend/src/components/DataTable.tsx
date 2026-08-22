/**
 * DataTable — rows from the backend as a compact table (KB's `.usage-table` inside its
 * `.table-scroll` wrapper, so narrow containers scroll instead of crushing a column).
 *
 * `maxRows` is a VISUAL cap only, unrelated to the server-side row cap of ADR 0007: the
 * trace stays readable while a broad result is on screen, and the footer says how many
 * of the returned rows are hidden. The truncation chip next to the table is what reports
 * the server's cap.
 *
 * Numeric cells print through `lib/format.ts`, the same formatter the chart axes use, so a
 * salary reads the same whether the trace shows it as a row or as a bar.
 *
 * Sorting is optional and server-side: pass `sortable` (the columns the server will sort by),
 * the `sort`/`direction` it is currently sorting by, and `onSort`, and those headers become
 * buttons that ask for a sort — the table never reorders rows itself, because it is holding one
 * page and the order of the rest is the server's to decide. Without those props it is the plain
 * table the chat trace shows.
 */

import { formatNumber } from "../lib/format";

const DEFAULT_MAX_ROWS = 8;
const NULL_CELL = "-";
const ASCENDING = "asc";
const ARIA_SORT = { asc: "ascending", desc: "descending" } as const;

export function DataTable({
  columns,
  rows,
  maxRows = DEFAULT_MAX_ROWS,
  empty = "No rows returned.",
  sortable,
  sort,
  direction = ASCENDING,
  onSort,
}: {
  columns: string[];
  rows: unknown[][];
  maxRows?: number;
  empty?: string;
  sortable?: string[];
  sort?: string;
  direction?: "asc" | "desc";
  onSort?: (column: string) => void;
}) {
  if (rows.length === 0) return <p className="data-table-note">{empty}</p>;
  const shown = rows.slice(0, maxRows);
  const hidden = rows.length - shown.length;

  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) =>
              sortable?.includes(column) && onSort ? (
                <th key={column} aria-sort={column === sort ? ARIA_SORT[direction] : "none"}>
                  <button type="button" className="th-sort" onClick={() => onSort(column)}>
                    {column}
                    {column === sort ? (
                      <span aria-hidden="true">{direction === ASCENDING ? " ↑" : " ↓"}</span>
                    ) : null}
                  </button>
                </th>
              ) : (
                <th key={column}>{column}</th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, index) => (
            <tr key={index}>
              {columns.map((column, cell) => (
                <td key={column} className={cellClass(row[cell])}>
                  {format(row[cell])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {hidden > 0 ? (
        <p className="data-table-note">
          {hidden} more of the {rows.length} returned rows not shown here.
        </p>
      ) : null}
    </div>
  );
}

function format(value: unknown): string {
  if (value === null || value === undefined) return NULL_CELL;
  if (typeof value === "number") return formatNumber(value);
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function cellClass(value: unknown): string {
  return typeof value === "number" ? "num mono" : "";
}
