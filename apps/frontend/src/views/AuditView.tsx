/**
 * The Audit tab: the server's own record of every statement the data path ran (ADR 0002's audit
 * log, joining the auditor surface of ADR 0014 as amended).
 *
 * The log existed from the first RLS commit and nothing served it, so the one claim a reader could
 * not check was the one the whole design rests on: that every read was scoped, refused or recorded.
 * This tab is that trail, newest first, every tenant's entries - which is the same reason Records
 * lists all 1000 rows. A trail narrowed to the caller could not show another tenant's query being
 * scoped to that other tenant, and showing that is the point.
 *
 * What is on screen is what the store holds, under the store's own column names: the generated
 * statement, the verdict a layer returned, the statement that actually executed, the error kind
 * and the row count. There are no result rows in that store, so this tab exposes no tenant data
 * that Records does not already show outright - and it is an endpoint, not a tool, so the agent
 * has no path to it.
 *
 * No filters, deliberately: a log is read from its head, not queried. The pager is the only
 * control, plus a reload, because the log keeps growing while the reader is on another tab and a
 * mounted tab does not refetch on its own. A tenant chip row is the obvious next thing and is the
 * owner's call to ask for.
 *
 * The statements are one ellipsised line each with the full text on `title`: a log is scanned down
 * the verdict column, and six columns of wrapped SQL would be unscannable. The timestamps stay
 * UTC ISO, as the server wrote them - the conversation rail localizes its own because a thread is
 * something the reader did, while a log entry is something the server recorded.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "../components/Button";
import { DataTable } from "../components/DataTable";
import { Loader } from "../components/Loader";
import { Pill } from "../components/Pill";
import type { PillTone } from "../components/Pill";
import { EmptyState, Page, PageHeader, Section } from "../components/layout";
import { ApiError, browseAudit } from "../lib/api";
import type { AuditEntry, AuditLog } from "../lib/api";
import { formatCount, formatNumber } from "../lib/format";

const LOAD_FAILURE = "The audit log could not be loaded.";
const TRAIL_NOTE =
  "The server's own record of every statement the agent path ran - all tenants, unscoped by " +
  "design. Statements and metadata only: no result row is stored here, and no tool can reach it.";
const NULL_CELL = "-";
const FIRST_PAGE = 1;
/** `2026-08-24T21:33:07`: the ISO instant cut before its fractional seconds. */
const CLOCK_CHARS = 19;
const APPROVED = "approved";
const REJECTED = "rejected";
const OK = "ok";
const REFUSED = "refused";
const ERRORED = "error";
/** The audit store's own column names, so the table is readable against `audit.db` itself. */
const COLUMNS = [
  "ts",
  "tenant",
  "generated_sql",
  "verdict",
  "error_kind",
  "executed_sql",
  "rowcount",
];
/** Neutral for a statement that ran, danger for one a layer refused, warn for one that broke. */
const TONES: Record<string, PillTone> = {
  [APPROVED]: "neutral",
  [REJECTED]: "danger",
};
const VERDICTS: Record<string, string> = {
  [APPROVED]: OK,
  [REJECTED]: REFUSED,
};

export function AuditView() {
  const [page, setPage] = useState(FIRST_PAGE);
  const [log, setLog] = useState<AuditLog | null>(null);
  const [reloads, setReloads] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    browseAudit(page)
      .then((served) => {
        if (live) {
          setLog(served);
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
  }, [page, reloads]);

  const reload = useCallback(() => setReloads((count) => count + 1), []);

  const pages = log ? Math.max(1, Math.ceil(log.total / log.page_size)) : 1;
  const rows = log ? log.entries.map(asRow) : [];

  return (
    <Page className="section-stack">
      <PageHeader
        eyebrow="Audit"
        title="What the data path ran"
        subtitle="Every call through the executor persists a row: the SQL it was given, the verdict a layer returned, the statement that actually ran, and how many rows came back. This is that log, newest first, for every tenant."
      />

      <Section
        title="Trail"
        aside={
          log ? (
            <Pill tone="accent">
              {formatCount(log.total, "entry", "entries")} · page {formatNumber(log.page)} of{" "}
              {formatNumber(pages)}
            </Pill>
          ) : null
        }
      >
        {error ? <p className="form-error">{error}</p> : null}
        {log === null ? (
          error ? (
            <EmptyState>Nothing to show.</EmptyState>
          ) : (
            <Loader scale="page" label="Loading the audit log…" />
          )
        ) : (
          <>
            <DataTable
              columns={COLUMNS}
              rows={rows}
              maxRows={log.page_size}
              empty="The audit log holds no entry yet."
              render={{
                ts: (value) => <span title={String(value)}>{asClockTime(String(value))}</span>,
                tenant: (value) => <Pill tone="neutral">{String(value)}</Pill>,
                verdict: (value) => (
                  <Pill tone={TONES[String(value)] ?? "warn"}>
                    {VERDICTS[String(value)] ?? ERRORED}
                  </Pill>
                ),
                generated_sql: statementCell,
                executed_sql: statementCell,
              }}
            />
            <div className="pager">
              <Button
                onClick={() => setPage((current) => Math.max(FIRST_PAGE, current - 1))}
                disabled={loading || log.page <= FIRST_PAGE}
              >
                Previous
              </Button>
              <span className="pager-state">
                showing {formatNumber(log.entries.length)} of {formatCount(log.total, "entry", "entries")}
              </span>
              <Button
                onClick={() => setPage((current) => current + 1)}
                disabled={loading || log.page >= pages}
              >
                Next
              </Button>
              <Button onClick={reload} disabled={loading}>
                Reload
              </Button>
            </div>
            <p className="data-table-note">{TRAIL_NOTE}</p>
          </>
        )}
      </Section>
    </Page>
  );
}

/** One statement as one scannable line, the whole of it on hover; an absent one is a dash. */
function statementCell(value: unknown) {
  if (value === null || value === undefined) return NULL_CELL;
  const sql = String(value);
  return (
    <code className="audit-sql" title={sql}>
      {sql}
    </code>
  );
}

/** The entry's fields in the order `COLUMNS` names them. */
function asRow(entry: AuditEntry): unknown[] {
  return [
    entry.ts,
    entry.tenant,
    entry.generated_sql,
    entry.verdict,
    entry.error_kind,
    entry.executed_sql,
    entry.rowcount,
  ];
}

/** The server's ISO instant trimmed to the second, which is the resolution a log is read at. */
function asClockTime(ts: string): string {
  const at = new Date(ts);
  if (Number.isNaN(at.getTime())) return ts;
  return at.toISOString().slice(0, CLOCK_CHARS).replace("T", " ");
}
