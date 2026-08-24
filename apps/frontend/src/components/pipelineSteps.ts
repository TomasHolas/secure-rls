/**
 * The query pipeline's six steps, in order - the ONE owner of that list.
 *
 * Two bricks draw the same pipeline and neither may keep a copy of it: `PipelineCanvas` is the
 * MAP, the flowchart on the chat's empty state saying what the path is, and `chat/PipelineStrip`
 * is the JOURNEY, the compact row under one executed statement saying where that statement
 * actually got. A label edited here changes both, which is the point: two drawings of one
 * enforcement path that disagreed would be worse than one drawing.
 *
 * The copy is one line per layer from the README's security table, so the screen and the docs
 * cannot drift, and the order is the layer order the path is defended in - fixed in `security.py`
 * and `db.py`, identical for every question, derived from the docs rather than from any turn.
 */

/** The steps by name, so a consumer maps onto one rather than onto a string it invented. */
export type PipelineStepId = "sql" | "validate" | "authorizer" | "scope" | "egress" | "rows";

export interface PipelineStep {
  id: PipelineStepId;
  /** Centre of the canvas card as a 0-1 fraction of the canvas width, so the flow scales with it. */
  x: number;
  /** The step's kind pill: the layer this step is, where it is one. */
  kind: string;
  /** The chip label in the strip, where six steps share one text line - and never a graph node's own name, which the trace may not show (ADR 0012 as amended after issue #87). */
  short: string;
  icon: string;
  /** A token holding this step's hue; only colours our own token set already ships. */
  hue: string;
  title: string;
  /** The mechanism in one line, from the README's five-layer table. */
  mechanism: string;
}

export const PIPELINE_STEPS: readonly PipelineStep[] = [
  {
    id: "sql",
    x: 0.5,
    kind: "input",
    short: "SQL",
    icon: "bot",
    hue: "var(--caution-500)",
    title: "The model writes SQL",
    mechanism:
      "Layer 1 bound the tenant from the verified JWT by closure - it is never in this SQL.",
  },
  {
    id: "validate",
    x: 0.42,
    kind: "layer 2",
    short: "validation",
    icon: "filter",
    hue: "var(--chart-1)",
    title: "Validation",
    mechanism: "sqlglot parse plus an allowlist: one SELECT over employees, and nothing else.",
  },
  {
    id: "authorizer",
    x: 0.58,
    kind: "layer 2.5",
    short: "authorizer",
    icon: "database",
    hue: "var(--chart-5)",
    title: "Engine authorizer",
    mechanism:
      "SQLite set_authorizer re-applies that allowlist in the engine, on a file opened mode=ro.",
  },
  {
    id: "scope",
    x: 0.42,
    kind: "layer 3",
    short: "scope",
    icon: "git-branch",
    hue: "var(--chart-6)",
    title: "Scoped execution",
    mechanism:
      "Every employees reference becomes a tenant-scoped subquery, the tenant bound, never interpolated.",
  },
  {
    id: "egress",
    x: 0.58,
    kind: "layer 4",
    short: "egress",
    icon: "check",
    hue: "var(--chart-7)",
    title: "Egress check",
    mechanism:
      "Proven structurally before the query runs, every returned tenant_id re-checked after. Fail closed.",
  },
  {
    id: "rows",
    x: 0.5,
    kind: "result",
    short: "rows",
    icon: "users",
    hue: "var(--positive-500)",
    title: "Only the caller's rows",
    mechanism: "Each layer above refuses on its own, so nothing foreign can reach the answer.",
  },
];
