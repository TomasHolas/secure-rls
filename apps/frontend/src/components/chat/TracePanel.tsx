/**
 * TracePanel — the trace of one assistant turn: what the model thought, what it called, what came
 * back, and anything that was retried or refused, in the order it happened. For a live turn the
 * panel IS the transport (ADR 0012) and each step appears as its event arrives; for a turn read
 * back from the server it holds the tool evidence that was stored (ADR 0012 as amended), which is
 * the same items minus the thinking.
 *
 * The graph's own node transitions are not rows (ADR 0012 as amended after issue #87). Naming them
 * put "Validating the tool call" above the calls and "Running the tool" below them - faithful to
 * the graph, nonsense to a reader - and spent the panel on internal mechanics nobody asked about.
 * They stay in the stream and in the audit trail; here they only tell `lib/trace.ts` which model
 * round a thought belongs to.
 *
 * Collapsible, and `open` is the state it starts in - expanded while a turn streams, and
 * expanded for a replayed turn, whose evidence is the reason the panel is there at all. After
 * that it stays where the reader put it (the disclosure is KB's `Collapsible`: a chevron, a caps
 * label and a count chip). Each item is one `TraceStep`; a call carries its arguments, then its
 * result, its retry or its refusal, so the failing statement and the reason it failed read as
 * one card, and it states its own pending state until one of them lands.
 *
 * One thinking step per model round, closed to start with, labelled with its round from the second
 * one on: a turn that thought again after its tool results shows two, and which is which is on the
 * chip rather than left to the reader to infer.
 */

import { useState } from "react";

import { Icon } from "../Icon";
import { Pill } from "../Pill";
import { CodeBlock } from "../CodeBlock";
import { TraceStep } from "./TraceStep";
import type { StepTone } from "./TraceStep";
import { ToolResultView } from "./ToolResultView";
import { FIRST_ROUND } from "../../lib/trace";
import type { CallItem, CallOutcome, ReasoningItem, TraceItem } from "../../lib/trace";

const REASONING_ICON = "sparkles";
const REASONING_TITLE = "Thinking";

const TOOL_ICONS: Record<string, string> = {
  query_db: "database",
  get_stats: "activity",
  plot: "bar-chart",
  detect_anomalies: "filter",
  search_notes: "search",
};

const SQL_ARG = "sql";

export function TracePanel({
  items,
  streaming,
  open: initial = streaming,
}: {
  items: TraceItem[];
  streaming: boolean;
  open?: boolean;
}) {
  const [open, setOpen] = useState(initial);
  if (items.length === 0) return null;

  return (
    <section className="trace">
      <button
        type="button"
        className="trace-head"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name={open ? "chevron-down" : "chevron-right"} size={16} />
        <span className="trace-head-label">Trace</span>
        <span className="trace-count">{items.length}</span>
        {streaming ? <Icon name="loader" size={14} className="loader-spin" /> : null}
      </button>
      {open ? (
        <ul className="trace-body">
          {items.map((item, index) => (
            <TraceItemStep key={index} item={item} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function TraceItemStep({ item }: { item: TraceItem }) {
  if (item.kind === "reasoning") {
    return <ReasoningStep item={item} />;
  }
  if (item.kind === "orphan") {
    return (
      <TraceStep
        icon={toolIcon(item.outcome.tool)}
        title={<span className="mono-inline">{item.outcome.tool}</span>}
        meta={<OutcomeChips outcome={item.outcome} />}
        tone={outcomeTone(item.outcome)}
      >
        <OutcomeBody outcome={item.outcome} />
      </TraceStep>
    );
  }
  return <CallStep item={item} />;
}

/** One model round's thinking: closed to start with, and from the second round on it says which. */
function ReasoningStep({ item }: { item: ReasoningItem }) {
  return (
    <TraceStep
      icon={REASONING_ICON}
      title={REASONING_TITLE}
      meta={item.round > FIRST_ROUND ? <Pill tone="neutral">round {item.round}</Pill> : undefined}
      tone="muted"
      open={false}
    >
      <p className="trace-reasoning">{item.text}</p>
    </TraceStep>
  );
}

function CallStep({ item }: { item: CallItem }) {
  const { outcome } = item;
  const pairShown = outcome?.type === "tool_result" && Boolean(outcome.data.generated_sql);
  return (
    <TraceStep
      icon={toolIcon(item.tool)}
      title={<span className="mono-inline">{item.tool}</span>}
      meta={outcome ? <OutcomeChips outcome={outcome} /> : <Pill tone="accent">running</Pill>}
      tone={outcome ? outcomeTone(outcome) : "default"}
    >
      <CallArgs args={item.args} hideSql={pairShown} />
      {outcome ? <OutcomeBody outcome={outcome} /> : null}
    </TraceStep>
  );
}

/** The call as the model wrote it; `sql` is a code block, every other argument a row. */
function CallArgs({ args, hideSql }: { args: Record<string, unknown>; hideSql: boolean }) {
  const sql = typeof args[SQL_ARG] === "string" ? (args[SQL_ARG] as string) : null;
  const rest = Object.entries(args).filter(([name]) => name !== SQL_ARG || !sql);
  return (
    <>
      {sql && !hideSql ? <CodeBlock label="generated by the model" code={sql} /> : null}
      {rest.length > 0 ? (
        <dl className="trace-args">
          {rest.map(([name, value]) => (
            <div className="trace-arg" key={name}>
              <dt>{name}</dt>
              <dd className="mono-inline">{formatArg(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </>
  );
}

function OutcomeChips({ outcome }: { outcome: CallOutcome }) {
  if (outcome.type === "security_event") {
    return <Pill tone="danger">blocked</Pill>;
  }
  if (outcome.type === "retry") {
    return (
      <Pill tone="warn" icon="refresh-cw">
        attempt {outcome.attempt} of {outcome.max_attempts}
      </Pill>
    );
  }
  const { returned_count, total_count, truncated } = outcome.data;
  return (
    <>
      {truncated ? (
        <Pill tone="warn">
          showing {returned_count} of {total_count} rows
        </Pill>
      ) : null}
      {returned_count !== undefined && !truncated ? (
        <Pill tone="ok">{returned_count} rows</Pill>
      ) : null}
    </>
  );
}

function OutcomeBody({ outcome }: { outcome: CallOutcome }) {
  if (outcome.type === "tool_result") {
    return <ToolResultView content={outcome.content} data={outcome.data} />;
  }
  if (outcome.type === "security_event") {
    return (
      <div className="notice notice-alert">
        <Icon name="x" size={16} />
        <span>
          <strong>Blocked:</strong> {outcome.reason} - {outcome.layer} layer
          <span className="notice-kind">{outcome.kind}</span>
        </span>
      </div>
    );
  }
  return (
    <div className="notice notice-warn">
      <Icon name="refresh-cw" size={16} />
      <span>
        <strong>Fed back to the model:</strong> {outcome.reason} - {outcome.layer} layer
        <span className="notice-kind">{outcome.kind}</span>
      </span>
    </div>
  );
}

function outcomeTone(outcome: CallOutcome): StepTone {
  if (outcome.type === "security_event") return "blocked";
  return outcome.type === "retry" ? "warn" : "default";
}

function toolIcon(tool: string): string {
  return TOOL_ICONS[tool] ?? "wrench";
}

function formatArg(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}
