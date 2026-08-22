/**
 * ToolResultView — the body of a `tool_result` step: whatever the tool's `data` payload
 * carries, each key through the brick that owns it.
 *
 * The generated and the executed statement go through the `SqlRewrite` brick, which marks the
 * layer-3 rewrite inside the statement that ran: the model wrote everything unmarked, the
 * scoped executor added the tenant filter and bound the tenant to it (ADR 0012). Rows go
 * through `DataTable`, a chart through the
 * `Chart` brick verbatim, retrieved notes through the `NoteList` brick the Notes tab
 * also uses, and a payload with no structured keys falls back to the text the model itself
 * read, so no result is ever shown as nothing.
 */

import { Chart } from "../charts";
import { DataTable } from "../DataTable";
import { CodeBlock } from "../CodeBlock";
import { NoteList } from "../NoteList";
import { SqlRewrite } from "../SqlRewrite";
import type { ToolResultData } from "../../lib/sse";

const EXECUTED_ONLY_LABEL = "executed SQL (tenant-scoped template)";

export function ToolResultView({ content, data }: { content: string; data: ToolResultData }) {
  const anomalyColumns = data.anomalies?.length ? Object.keys(data.anomalies[0]) : [];
  const structured =
    Boolean(data.executed_sql) ||
    Boolean(data.columns) ||
    Boolean(data.chart_spec) ||
    Boolean(data.anomalies) ||
    Boolean(data.notes);

  return (
    <>
      {data.generated_sql && data.executed_sql ? (
        <SqlRewrite generated={data.generated_sql} executed={data.executed_sql} />
      ) : data.executed_sql ? (
        <CodeBlock label={EXECUTED_ONLY_LABEL} code={data.executed_sql} tone="accent" />
      ) : null}

      {data.columns && data.rows ? <DataTable columns={data.columns} rows={data.rows} /> : null}

      {data.chart_spec ? <Chart spec={data.chart_spec} /> : null}

      {data.anomalies ? (
        <DataTable
          columns={anomalyColumns}
          rows={data.anomalies.map((anomaly) => anomalyColumns.map((column) => anomaly[column]))}
          empty="No outliers beyond the Tukey fences."
        />
      ) : null}

      {data.notes ? <NoteList notes={data.notes} /> : null}

      {structured ? null : <pre className="trace-text">{content}</pre>}
    </>
  );
}
