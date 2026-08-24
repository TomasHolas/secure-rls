/**
 * The Records tab: every row of the dataset, all three tenants, filtered, sorted and paged
 * server-side (ADR 0014 as rewritten by issue #117). It is the demo's control group, not a tenant
 * view — a reader sees exactly what exists, and then watches the agent in the same app reach only
 * the tenant its token names. Showing one tenant's 450 with nothing saying 1000 exist threw that
 * comparison away and made the number look like a bug.
 *
 * `tenant_id` is therefore a filter of the same kind as `department`: a `ChipRow` in the same grid,
 * its options read off the data by `GET /records/tenants`, its value bound server-side like every
 * other filter value. What no request can choose is the tenant the AGENT sees — that comes from
 * the verified token and reaches the tools by closure (ADR 0002, layer 1).
 *
 * The view owns no query logic: the filter controls and the sort headers turn into query
 * parameters, and `GET /records` answers with the page plus the true total and the statement it
 * ran. Filters are applied on submit rather than on every keystroke — a filter row of nine boxes
 * would otherwise fire a request per character, and the reader is composing a question, not
 * narrowing live. The executed SQL is still under the table, for the same reason the chat trace
 * shows it, and here it is evidence of the opposite thing: this listing is the one read in the app
 * that carries no tenant scoping. That fact is a caption a reader passes over and the statement
 * itself is one click behind a `Disclosure` (issue #139) — an always-open block of monospace led
 * the tab with the least readable thing on it, and shouted a label promising no rewrite twice
 * over. It is never deleted: it is what makes the claim checkable rather than asserted.
 *
 * Every total states what it is a total of. "1000 rows · all tenants" and "450 rows · tenant
 * acme" are different facts, and a pager that said only "450" would be the orphaned number the
 * old design shipped.
 *
 * The grid is six cells - three single filters and three `FieldPair`s - because a bound pair
 * that is one cell cannot be split across rows by the wrap, and six is full at three, two and
 * one column, so no control is ever stranded beside dead space (issue #115). The dates are ISO
 * text rather than native date inputs: a native picker prints `dd.mm.yyyy` or `mm/dd/yyyy`
 * depending on the viewer's machine, while the table cells, the executed statement and the
 * server's own refusal all speak ISO, so what a reader types now matches what they read.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "../components/Button";
import { CodeBlock } from "../components/CodeBlock";
import { DataTable } from "../components/DataTable";
import { Disclosure } from "../components/Disclosure";
import { Loader } from "../components/Loader";
import { Pill } from "../components/Pill";
import { ChipRow, FieldPair, SelectField, TextField } from "../components/forms";
import { EmptyState, Page, PageHeader, Section } from "../components/layout";
import { ApiError, browseRecords, listDepartments, listTenants } from "../lib/api";
import type { BrowsePage, FilterOption } from "../lib/api";
import { formatCount, formatNumber } from "../lib/format";

const LOAD_FAILURE = "The rows could not be loaded.";
const EXECUTED_LABEL = "executed without tenant scoping";
const UNSCOPED_NOTE =
  "This listing is the whole dataset, unscoped by design - the agent's queries never are.";
const SHOW_SQL = "show the SQL this page ran";
const HIDE_SQL = "hide the SQL this page ran";
const ALL_TENANTS = "all tenants";
const SORTABLE = [
  "user_id",
  "tenant_id",
  "name",
  "department",
  "salary",
  "performance_score",
  "hire_date",
];
const FIRST_PAGE = 1;
// The same example date the server's own refusal names, so the hint and the error agree.
const ISO_DATE_HINT = "2020-01-31";

interface Draft {
  tenant_id: string;
  name: string;
  department: string;
  salary_min: string;
  salary_max: string;
  score_min: string;
  score_max: string;
  hired_from: string;
  hired_to: string;
}

const EMPTY: Draft = {
  tenant_id: "",
  name: "",
  department: "",
  salary_min: "",
  salary_max: "",
  score_min: "",
  score_max: "",
  hired_from: "",
  hired_to: "",
};

export function RecordsView({ tenant }: { tenant: string }) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [applied, setApplied] = useState<Draft>(EMPTY);
  const [sort, setSort] = useState(SORTABLE[0]);
  const [direction, setDirection] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(FIRST_PAGE);
  const [rows, setRows] = useState<BrowsePage | null>(null);
  const [tenants, setTenants] = useState<FilterOption[]>([]);
  const [departments, setDepartments] = useState<FilterOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    listTenants()
      .then((list) => {
        if (live) setTenants(list);
      })
      .catch(() => {
        // Without the list there is nothing to pick, which is what an empty select says.
        if (live) setTenants([]);
      });
    return () => {
      live = false;
    };
  }, []);

  // The department counts follow the applied tenant, so no count describes a set nobody asked for.
  useEffect(() => {
    let live = true;
    listDepartments(applied.tenant_id)
      .then((list) => {
        if (live) setDepartments(list);
      })
      .catch(() => {
        if (live) setDepartments([]);
      });
    return () => {
      live = false;
    };
  }, [applied.tenant_id]);

  useEffect(() => {
    let live = true;
    setLoading(true);
    browseRecords({ ...applied, sort, direction, page })
      .then((serverPage) => {
        if (live) {
          setRows(serverPage);
          setError(null);
        }
      })
      .catch((cause) => {
        if (live) setError(cause instanceof ApiError ? cause.message : LOAD_FAILURE);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [applied, sort, direction, page]);

  const sortBy = useCallback(
    (column: string) => {
      setPage(FIRST_PAGE);
      setDirection(column === sort && direction === "asc" ? "desc" : "asc");
      setSort(column);
    },
    [sort, direction],
  );

  const apply = useCallback(() => {
    setPage(FIRST_PAGE);
    setApplied(draft);
  }, [draft]);

  const reset = useCallback(() => {
    setPage(FIRST_PAGE);
    setDraft(EMPTY);
    setApplied(EMPTY);
  }, []);

  const field = useCallback(
    (key: keyof Draft) => (value: string) => setDraft((previous) => ({ ...previous, [key]: value })),
    [],
  );

  const pages = rows ? Math.max(1, Math.ceil(rows.total / rows.page_size)) : 1;
  const scope = applied.tenant_id ? `tenant ${applied.tenant_id}` : ALL_TENANTS;

  return (
    <Page className="section-stack">
      <PageHeader
        eyebrow="Records"
        title="Employee rows"
        subtitle={`Every row of the dataset, all tenants - the control group. The agent, signed in as ${tenant}, can only ever reach ${tenant}'s part of it.`}
      />

      <Section
        title="Filters"
        aside={
          rows ? (
            <Pill tone="accent">
              {formatCount(rows.total, "matching row")} · {scope}
            </Pill>
          ) : null
        }
      >
        <form
          className="filter-grid control-row"
          onSubmit={(event) => {
            event.preventDefault();
            apply();
          }}
        >
          <ChipRow
            id="records-tenant"
            label="Tenant"
            value={draft.tenant_id}
            options={tenants.map((entry) => entry.value)}
            onChange={field("tenant_id")}
          />
          <TextField
            id="records-name"
            label="Name contains"
            value={draft.name}
            onChange={field("name")}
            placeholder="substring"
          />
          <SelectField
            id="records-department"
            label="Department"
            value={draft.department}
            options={departments.map((entry) => ({
              value: entry.value,
              label: `${entry.value} (${formatNumber(entry.employees)})`,
            }))}
            onChange={field("department")}
            placeholder="any department"
          />
          <FieldPair>
            <TextField
              id="records-salary-min"
              label="Salary from"
              type="number"
              value={draft.salary_min}
              onChange={field("salary_min")}
            />
            <TextField
              id="records-salary-max"
              label="Salary to"
              type="number"
              value={draft.salary_max}
              onChange={field("salary_max")}
            />
          </FieldPair>
          <FieldPair>
            <TextField
              id="records-score-min"
              label="Score from"
              type="number"
              value={draft.score_min}
              onChange={field("score_min")}
            />
            <TextField
              id="records-score-max"
              label="Score to"
              type="number"
              value={draft.score_max}
              onChange={field("score_max")}
            />
          </FieldPair>
          <FieldPair>
            <TextField
              id="records-hired-from"
              label="Hired from"
              value={draft.hired_from}
              onChange={field("hired_from")}
              placeholder={ISO_DATE_HINT}
            />
            <TextField
              id="records-hired-to"
              label="Hired to"
              value={draft.hired_to}
              onChange={field("hired_to")}
              placeholder={ISO_DATE_HINT}
            />
          </FieldPair>
          <div className="filter-actions">
            <Button onClick={reset} disabled={loading}>
              Reset
            </Button>
            <Button variant="primary" type="submit" disabled={loading}>
              Apply
            </Button>
          </div>
        </form>
      </Section>

      <Section
        title="Rows"
        aside={
          rows ? (
            <Pill tone="neutral">
              page {formatNumber(rows.page)} of {formatNumber(pages)}
            </Pill>
          ) : null
        }
      >
        {error ? <p className="form-error">{error}</p> : null}
        {rows === null ? (
          error ? (
            <EmptyState>Nothing to show.</EmptyState>
          ) : (
            <Loader scale="page" label="Loading rows…" />
          )
        ) : (
          <>
            <DataTable
              columns={rows.columns}
              rows={rows.rows}
              maxRows={rows.page_size}
              empty="No row of the dataset matches those filters."
              sortable={SORTABLE}
              sort={rows.sort}
              direction={rows.direction === "desc" ? "desc" : "asc"}
              onSort={sortBy}
            />
            <div className="pager">
              <Button
                onClick={() => setPage((current) => Math.max(FIRST_PAGE, current - 1))}
                disabled={loading || rows.page <= FIRST_PAGE}
              >
                Previous
              </Button>
              <span className="pager-state">
                showing {formatNumber(rows.rows.length)} of {formatCount(rows.total, "row")} ·{" "}
                {scope}
              </span>
              <Button
                onClick={() => setPage((current) => current + 1)}
                disabled={loading || rows.page >= pages}
              >
                Next
              </Button>
            </div>
            <p className="data-table-note">{UNSCOPED_NOTE}</p>
            <Disclosure show={SHOW_SQL} hide={HIDE_SQL}>
              <CodeBlock label={EXECUTED_LABEL} code={rows.executed_sql} tone="accent" />
            </Disclosure>
          </>
        )}
      </Section>
    </Page>
  );
}
