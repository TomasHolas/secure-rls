/**
 * PipelineStrip - where ONE statement actually got down the pipeline, in one text line.
 *
 * `PipelineCanvas` on the empty chat is the map: the six steps of the enforcement path, the same
 * for every question, drawn from the docs. This is the journey: the same six steps, compacted to
 * chips, under a statement that has already run or already been refused. Both read
 * `pipelineSteps.ts`, so the vocabulary is one list in one place.
 *
 * **It is never a prediction.** Nothing here is drawn while a call is in flight: a chip lights
 * only from an outcome the server already reported, which is what keeps this on the right side of
 * the line issue #91 drew against plan-shaped displays of a live run (`docs/ui-pattern-review.md`).
 *
 * **The lighting is justified, not decorative.** A `tool_result` carrying `executed_sql` is
 * itself the proof: `db.execute_scoped` returns a result only after `validate_sql` approved the
 * statement (layer 2), the rewrite scoped every employees reference and was structurally proven to
 * have done so (layer 3), the statement ran on a read-only connection under the employees-only
 * `set_authorizer` (layer 2.5), and every returned `tenant_id` was re-checked against the session
 * tenant (layer 4). Any one of those failing raises instead of returning, so there is no path on
 * which a payload with `executed_sql` skipped a layer.
 *
 * **A refusal lights only what provably ran.** `REFUSALS` maps what `agent.py` puts on the wire -
 * the `layer` string and, under `scoped execution`, the `kind` of the `SecurityViolation` - onto
 * the step that stopped the statement and onto the steps that had already passed it on. A layer
 * identifier not in that table renders NO strip: a wrong picture of an enforcement path is worse
 * than none. The passed lists are not "everything to the left": the canvas orders the steps by
 * layer number, while the executor opens the engine at execution time, so a statement refused
 * before it ran leaves the authorizer chip unreached even though it sits earlier in the row.
 *
 * The strip adds no fact the trace does not already state in words - the blocked notice above it
 * names the layer, the kind and the reason - so it is a summary a reader can take in at a glance
 * rather than the only place something is said.
 *
 * The chips are the `Pill` brick, so the strip implements no chip of its own: the state picks the
 * tone, and the glyph beside it is that same distinction in a second channel (WCAG 1.4.1) - a
 * check for a step that passed, an x for the layer that stopped the statement, and no glyph at
 * all for a step it never reached, whose pill takes the dashed outline this strip's own rule adds.
 * Nothing here transitions, so reduced motion has nothing to switch off.
 */

import { Pill } from "../Pill";
import type { PillTone } from "../Pill";
import { PIPELINE_STEPS } from "../pipelineSteps";
import type { PipelineStep, PipelineStepId } from "../pipelineSteps";
import { formatNumber } from "../../lib/format";
import type { SecurityEvent, ToolResultData } from "../../lib/sse";

/** What one chip claims about its step, and the glyph that says it without colour. */
type ChipState = "passed" | "stopped" | "unreached";

const GLYPHS: Record<ChipState, string | undefined> = {
  passed: "check",
  stopped: "x",
  unreached: undefined,
};

/** The `Pill` tone each state takes; the glyph above is the same distinction without colour. */
const TONES: Record<ChipState, PillTone> = {
  passed: "ok",
  stopped: "danger",
  unreached: "neutral",
};

const STATE_WORDS: Record<ChipState, string> = {
  passed: "passed",
  stopped: "refused the statement",
  unreached: "not reached - the statement was refused before this ran",
};

const STRIP_LABEL = "pipeline";
const MODEL_NOTE = "model";
const TEMPLATE_NOTE = "template";
const MODEL_TITLE = "The model wrote this statement.";
const TEMPLATE_TITLE =
  "A fixed server template - the model wrote no SQL here, and layer 2 validated the template like any other statement.";

/** One step as the strip draws it: the label, what it claims, and why on hover. */
interface Chip {
  id: PipelineStepId;
  label: string;
  state: ChipState;
  title: string;
  note?: string;
}

/**
 * One refusal the wire can carry, as `agent.py` emits it: `layer` alone for the validator, and
 * `layer` plus the `SecurityViolation` kind for the three checks the scoped executor raises,
 * which all travel under one layer string. `passed` is what that refusal proves had already run.
 */
interface Refusal {
  layer: string;
  kind?: string;
  stop: PipelineStepId;
  passed: PipelineStepId[];
}

const REFUSALS: readonly Refusal[] = [
  { layer: "query validation", stop: "validate", passed: ["sql"] },
  {
    layer: "scoped execution",
    kind: "authorizer_denied",
    stop: "authorizer",
    passed: ["sql", "validate"],
  },
  {
    layer: "scoped execution",
    kind: "rewrite_not_applied",
    stop: "scope",
    passed: ["sql", "validate"],
  },
  {
    layer: "scoped execution",
    kind: "egress_row_mismatch",
    stop: "egress",
    passed: ["sql", "validate", "authorizer", "scope"],
  },
];

/**
 * The strip for a result or for a refusal, and nothing in between - a call still in flight has
 * no strip, because it has no journey yet.
 */
export function PipelineStrip(
  props: { result: ToolResultData; refusal?: never } | { refusal: SecurityEvent; result?: never },
) {
  const chips = props.result === undefined ? refusedChips(props.refusal) : ranChips(props.result);
  if (!chips) return null;
  return (
    <ol className="pipeline-strip" aria-label={STRIP_LABEL}>
      <li className="pipeline-strip-lead">{STRIP_LABEL}</li>
      {chips.map((chip) => (
        <li key={chip.id} className={`pipeline-strip-step pipeline-strip-${chip.state}`}>
          <Pill tone={TONES[chip.state]} icon={GLYPHS[chip.state]} title={chip.title}>
            {chip.label}
            {chip.note ? <span className="pipeline-strip-note">{chip.note}</span> : null}
          </Pill>
        </li>
      ))}
    </ol>
  );
}

/**
 * A statement that ran: every step passed, because the payload could not exist otherwise. The
 * first chip says who wrote the statement and the last one how many rows came back.
 */
function ranChips(data: ToolResultData): Chip[] | null {
  if (!data.executed_sql) return null;
  const generated = Boolean(data.generated_sql);
  return PIPELINE_STEPS.map((step) => ({
    id: step.id,
    label: step.short,
    state: "passed" as ChipState,
    title: stepTitle(step, "passed", generated ? MODEL_TITLE : TEMPLATE_TITLE),
    note: runNote(step.id, data, generated),
  }));
}

/**
 * A statement a layer refused: what had passed it on, the layer that stopped it, and the rest
 * dark. The kind and the reason ride the stopped chip's `title` rather than the row - the notice
 * above the strip already prints both, and a chip is a glance, not a second copy of the words.
 */
function refusedChips(event: SecurityEvent): Chip[] | null {
  const refusal = REFUSALS.find(
    (candidate) =>
      candidate.layer === event.layer &&
      (candidate.kind === undefined || candidate.kind === event.kind),
  );
  if (!refusal) return null;
  return PIPELINE_STEPS.map((step) => {
    const state: ChipState =
      step.id === refusal.stop
        ? "stopped"
        : refusal.passed.includes(step.id)
          ? "passed"
          : "unreached";
    return {
      id: step.id,
      label: step.short,
      state,
      title: stepTitle(step, state, state === "stopped" ? `${event.kind}: ${event.reason}` : ""),
    };
  });
}

/** What the first and the last chip carry beside their label: who wrote the SQL, and the rows. */
function runNote(id: PipelineStepId, data: ToolResultData, generated: boolean): string | undefined {
  if (id === "sql") return generated ? MODEL_NOTE : TEMPLATE_NOTE;
  if (id === "rows" && data.returned_count !== undefined) return formatNumber(data.returned_count);
  return undefined;
}

/** The hover line of one chip: which layer it is, what it did, and the server's own words for it. */
function stepTitle(step: PipelineStep, state: ChipState, detail: string): string {
  const claim = `${step.kind} - ${step.title}: ${STATE_WORDS[state]}`;
  return detail ? `${claim}. ${detail}` : claim;
}
