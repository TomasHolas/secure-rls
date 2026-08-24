/**
 * TracePanel — the trace of one assistant turn: what the model thought, what it called, what came
 * back, and anything that was retried or refused, in the order it happened. For a live turn the
 * panel IS the transport (ADR 0012) and each step appears as its event arrives; a turn read back
 * from the server holds the same items, folded from the same events the server stored for it
 * (ADR 0012 as amended, issue #90). The one thing a replayed step cannot carry is how long its
 * thinking took, because that was measured here rather than sent.
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
 *
 * Nothing in the panel animates except that step's shimmering label. The header carried a pixel
 * grid of its own while a turn streamed, which put two of them on screen at once for a signal one
 * of them already gave; the owner's placement ruling on issue #123 took the grid out of the trace
 * entirely and left it to the answer card's placeholder (`docs/ui-pattern-review.md`).
 */

import { useState } from "react";

import { Icon } from "../Icon";
import { Loader } from "../Loader";
import { Pill } from "../Pill";
import { CodeBlock } from "../CodeBlock";
import { TraceStep } from "./TraceStep";
import type { StepTone } from "./TraceStep";
import { ToolResultView } from "./ToolResultView";
import { formatCount, formatNumber, formatSeconds } from "../../lib/format";
import { FIRST_ROUND } from "../../lib/trace";
import type { CallItem, CallOutcome, ReasoningItem, TraceItem } from "../../lib/trace";

const REASONING_ICON = "sparkles";
const REASONING_TITLE = "Thinking";
const SETTLED_TITLE = "Thought";
const FOR = "for";
const TRUNCATED_LABEL = "thinking capped";
const TRUNCATED_TITLE =
  "The server kept this round's thinking up to its history character cap; what came after it is not stored.";

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

/**
 * One model round's thinking. While the round's thinking is still arriving the step is open and its
 * title is the `Loader`, shimmering and counting up from the moment the first chunk landed - the
 * label alone, no pixel grid: the grid belongs to the answer card's placeholder and one turn shows
 * it once (`docs/ui-pattern-review.md`). Once the round settles the step
 * folds itself away, leaving that same span on the row as `Thought for 2.8s` - the thinking-trace
 * pattern from beautifului.dev, whose header swaps a live verb for a past-tense summary carrying
 * the cost. Which round it was is on the chip from the second one on.
 *
 * A replayed round claims no duration: the span was this client's own measurement of thinking
 * arriving, and a round read back from the server never arrived here (ADR 0012 as amended). If the
 * history cap kept only part of it, the chip says so rather than letting a cut thought read whole.
 */
function ReasoningStep({ item }: { item: ReasoningItem }) {
  const live = item.startedAt !== null && item.endedAt === null;
  const span =
    item.startedAt !== null && item.endedAt !== null ? item.endedAt - item.startedAt : null;
  const round = item.round > FIRST_ROUND ? <Pill tone="neutral">round {item.round}</Pill> : null;
  const capped = item.truncated ? (
    <Pill tone="warn" title={TRUNCATED_TITLE}>
      {TRUNCATED_LABEL}
    </Pill>
  ) : null;
  return (
    <TraceStep
      icon={REASONING_ICON}
      title={
        live ? (
          <Loader label={REASONING_TITLE} since={item.startedAt} grid={false} />
        ) : span === null ? (
          SETTLED_TITLE
        ) : (
          `${SETTLED_TITLE} ${FOR} ${formatSeconds(span)}`
        )
      }
      meta={
        round || capped ? (
          <>
            {round}
            {capped}
          </>
        ) : undefined
      }
      tone="muted"
      open={live}
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
          showing {formatNumber(returned_count ?? 0)} of {formatCount(total_count ?? 0, "row")}
        </Pill>
      ) : null}
      {returned_count !== undefined && !truncated ? (
        <Pill tone="ok">{formatCount(returned_count, "row")}</Pill>
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
