/**
 * ToolResultView — the body of a `tool_result` step: whatever the tool's `data` payload
 * carries, each key through the brick that owns it.
 *
 * The generated and the executed statement go through the `SqlRewrite` brick, which marks the
 * layer-3 rewrite inside the statement that ran: the model wrote everything unmarked, the
 * scoped executor added the tenant filter and bound the tenant to it (ADR 0012). A fixed-template
 * tool reports no generated statement, so its one statement goes through `SqlTemplate` - the same
 * mark and legend, on SQL the server composed rather than the model. Under either card the
 * `PipelineStrip` says how far that statement got, which for a result is all the way: a payload
 * carrying `executed_sql` could not exist unless every layer passed it (see the strip's own
 * docstring). A payload with no executed statement - a chart, an anomaly table, retrieved notes -
 * gets no strip, because none of it came down that path. Rows go
 * through `DataTable`, a chart through the
 * `Chart` brick verbatim, retrieved notes through the `NoteList` brick the Notes tab
 * also uses, and a payload with no structured keys falls back to the text the model itself
 * read, so no result is ever shown as nothing.
 */

import { Chart } from "../charts";
import { DataTable } from "../DataTable";
import { NoteList } from "../NoteList";
import { SqlRewrite, SqlTemplate } from "../SqlRewrite";
import { PipelineStrip } from "./PipelineStrip";
import type { ToolResultData } from "../../lib/sse";

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
        <SqlTemplate sql={data.executed_sql} />
      ) : null}

      <PipelineStrip result={data} />

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
