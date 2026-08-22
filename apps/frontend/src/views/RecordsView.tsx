/**
 * The Records tab: every row the signed-in tenant can see, filtered, sorted and paged
 * server-side (ADR 0014). It is the isolation claim made checkable without the agent — sign in
 * as one tenant and the total is one number, sign in as another and it is a different one, and
 * both come from the same request against the same table.
 *
 * The view owns no query logic: the filter boxes and the sort headers turn into query parameters,
 * and `GET /records` answers with the page plus the true total and the statement it ran. Filters
 * are applied on submit rather than on every keystroke — a filter row of eight boxes would
 * otherwise fire a request per character, and the reader is composing a question, not narrowing
 * live. The executed SQL is shown under the table for the same reason the chat trace shows it:
 * the scoping subquery with its bound tenant is the evidence, and hiding it would waste it.
 *
 * There is deliberately no tenant filter — a caller holds exactly one tenant, and offering to
 * pick one would imply otherwise. What there is instead is the `ParamProbe`: a reader may append
 * a query parameter of their own to the request and read back what the server did with it, so
 * `?tenant_id=beta` produces the tenant's own 450 rows *and* the server's sentence about why no
 * request can name a tenant, rather than an unchanged page they have to take on faith (#107).
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "../components/Button";
import { CodeBlock } from "../components/CodeBlock";
import { DataTable } from "../components/DataTable";
import { ParamProbe } from "../components/ParamProbe";
import { Pill } from "../components/Pill";
import { SelectField, TextField } from "../components/forms";
import { EmptyState, Page, PageHeader, Section } from "../components/layout";
import { ApiError, browseRecords, listDepartments } from "../lib/api";
import type { BrowsePage, DepartmentCount } from "../lib/api";
import { formatCount, formatNumber } from "../lib/format";

const LOAD_FAILURE = "The rows could not be loaded.";
const EXECUTED_LABEL = "executed after tenant scoping";
const PROBE_TITLE = "Attack it yourself";
const SORTABLE = ["user_id", "name", "department", "salary", "performance_score", "hire_date"];
const FIRST_PAGE = 1;

interface Draft {
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
  const [probe, setProbe] = useState("");
  const [rows, setRows] = useState<BrowsePage | null>(null);
  const [departments, setDepartments] = useState<DepartmentCount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    listDepartments()
      .then((list) => {
        if (live) setDepartments(list);
      })
      .catch(() => {
        // Without the list there is nothing to pick, which is what an empty select says.
        if (live) setDepartments([]);
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    browseRecords({ ...applied, sort, direction, page }, probe)
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
  }, [applied, sort, direction, page, probe]);

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

  return (
    <Page className="section-stack">
      <PageHeader
        eyebrow="Records"
        title="Employee rows"
        subtitle={`Every row the ${tenant} tenant can see, fetched through the same scoped executor the agent's tools use.`}
      />

      <Section
        title="Filters"
        aside={rows ? <Pill tone="accent">{formatCount(rows.total, "matching row")}</Pill> : null}
      >
        <form
          className="filter-grid"
          onSubmit={(event) => {
            event.preventDefault();
            apply();
          }}
        >
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
              value: entry.department,
              label: `${entry.department} (${formatNumber(entry.employees)})`,
            }))}
            onChange={field("department")}
            placeholder="any department"
          />
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
          <TextField
            id="records-hired-from"
            label="Hired from"
            type="date"
            value={draft.hired_from}
            onChange={field("hired_from")}
          />
          <TextField
            id="records-hired-to"
            label="Hired to"
            type="date"
            value={draft.hired_to}
            onChange={field("hired_to")}
          />
          <div className="filter-actions">
            <Button variant="primary" type="submit" disabled={loading}>
              Apply
            </Button>
            <Button onClick={reset} disabled={loading}>
              Reset
            </Button>
          </div>
        </form>
      </Section>

      <Section title={PROBE_TITLE}>
        <ParamProbe
          id="records-probe"
          ignored={rows?.ignored ?? []}
          onSend={setProbe}
          disabled={loading}
        />
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
          <EmptyState icon="loader">{error ? "Nothing to show." : "Loading rows…"}</EmptyState>
        ) : (
          <>
            <DataTable
              columns={rows.columns}
              rows={rows.rows}
              maxRows={rows.page_size}
              empty="No row of this tenant matches those filters."
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
                showing {formatNumber(rows.rows.length)} of {formatCount(rows.total, "row")}
              </span>
              <Button
                onClick={() => setPage((current) => current + 1)}
                disabled={loading || rows.page >= pages}
              >
                Next
              </Button>
            </div>
          </>
        )}
      </Section>

      {rows ? (
        <Section title="What the server ran">
          <CodeBlock label={EXECUTED_LABEL} code={rows.executed_sql} tone="accent" />
        </Section>
      ) : null}
    </Page>
  );
}
