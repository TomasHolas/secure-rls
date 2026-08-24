/**
 * The pipeline strip: what it lights, where it stops, and when it refuses to draw anything.
 *
 * Every case here is a claim about an enforcement path, so the assertions are about which step
 * carries which state rather than about pixels: the six steps come from the same list the canvas
 * draws, a result lights all of them, a refusal lights only what provably ran, and a layer
 * identifier the strip cannot place produces no strip at all.
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PipelineStrip } from "./PipelineStrip";
import { PIPELINE_STEPS } from "../pipelineSteps";
import type { SecurityEvent, ToolResultData } from "../../lib/sse";

afterEach(cleanup);

const EXECUTED =
  "SELECT name FROM (SELECT * FROM employees WHERE tenant_id = ?) WHERE department = 'Sales'";
const GENERATED = "SELECT name FROM employees WHERE department = 'Sales'";

const RAN: ToolResultData = {
  generated_sql: GENERATED,
  executed_sql: EXECUTED,
  columns: ["name"],
  rows: [["Ada"], ["Grace"], ["Alan"]],
  total_count: 3,
  returned_count: 3,
  truncated: false,
};

/** The state a step is in is its second class; the first says only that it is a step. */
const STATE_PREFIX = "pipeline-strip-";

/** The step labels in flow order, as the one step list holds them. */
const LABELS = PIPELINE_STEPS.map((step) => step.short);

function refusal(layer: string, kind: string): SecurityEvent {
  return {
    type: "security_event",
    id: "c1",
    tool: "query_db",
    layer,
    kind,
    reason: "the statement names another tenant",
  };
}

function chips(container: HTMLElement) {
  return [...container.querySelectorAll(".pipeline-strip-step")].map((step) => {
    const pill = step.querySelector(".pill");
    const note = step.querySelector(".pipeline-strip-note")?.textContent ?? "";
    const glyph = step.querySelector(".material-symbols-outlined")?.textContent ?? "";
    const text = pill?.textContent ?? "";
    return {
      label: text.slice(glyph.length, text.length - note.length),
      note,
      glyph,
      tone: [...(pill?.classList ?? [])].find((name) => name.startsWith("pill-")),
      state: (step.classList[1] ?? "").replace(STATE_PREFIX, ""),
      title: pill?.getAttribute("title") ?? "",
    };
  });
}

/** The state of each step, in flow order - the one shape every case below is asserted against. */
function states(container: HTMLElement): string[] {
  return chips(container).map((chip) => chip.state);
}

describe("a statement that ran", () => {
  it("lights every step, in the canvas's own order and with the canvas's own labels", () => {
    const { container } = render(<PipelineStrip result={RAN} />);

    expect(states(container)).toEqual(Array(PIPELINE_STEPS.length).fill("passed"));
    expect(chips(container).map((chip) => chip.label)).toEqual(LABELS);
    expect(chips(container).every((chip) => chip.tone === "pill-ok")).toBe(true);
  });

  it("carries a check on every chip, so the state survives grayscale", () => {
    const { container } = render(<PipelineStrip result={RAN} />);

    expect(chips(container).every((chip) => chip.glyph === "check")).toBe(true);
  });

  it("says who wrote the statement and how many rows came back", () => {
    const { container } = render(<PipelineStrip result={RAN} />);
    const drawn = chips(container);

    expect(drawn[0].note).toBe("model");
    expect(drawn[drawn.length - 1].note).toBe("3");
  });

  it("marks a fixed-template tool's statement as the server's, and still lights layer 2", () => {
    const { generated_sql: _generated, ...template } = RAN;
    const { container } = render(<PipelineStrip result={template} />);
    const drawn = chips(container);

    expect(drawn[0].note).toBe("template");
    expect(drawn[0].title).toContain("the model wrote no SQL here");
    expect(states(container)).toEqual(Array(PIPELINE_STEPS.length).fill("passed"));
  });

  it("draws nothing for a payload that never came down the SQL path", () => {
    const { container } = render(<PipelineStrip result={{ notes: [] }} />);

    expect(container.querySelector(".pipeline-strip")).toBeNull();
  });
});

describe("a statement a layer refused", () => {
  it("stops at the validator, with the kind on the chip and everything after it dark", () => {
    const { container } = render(
      <PipelineStrip refusal={refusal("query validation", "policy_violation")} />,
    );
    const drawn = chips(container);

    expect(states(container)).toEqual([
      "passed",
      "stopped",
      "unreached",
      "unreached",
      "unreached",
      "unreached",
    ]);
    expect(drawn[1].tone).toBe("pill-danger");
    expect(drawn[1].glyph).toBe("close");
    expect(drawn[1].title).toContain("policy_violation");
    expect(drawn[1].title).toContain("the statement names another tenant");
    expect(drawn[1].note).toBe("");
  });

  it("stops at the engine authorizer when the engine is what denied it", () => {
    const { container } = render(
      <PipelineStrip refusal={refusal("scoped execution", "authorizer_denied")} />,
    );

    expect(states(container)).toEqual([
      "passed",
      "passed",
      "stopped",
      "unreached",
      "unreached",
      "unreached",
    ]);
  });

  it("stops at the scoping, and does not claim the engine passed a query it never opened", () => {
    const { container } = render(
      <PipelineStrip refusal={refusal("scoped execution", "rewrite_not_applied")} />,
    );

    expect(states(container)).toEqual([
      "passed",
      "passed",
      "unreached",
      "stopped",
      "unreached",
      "unreached",
    ]);
  });

  it("stops at the egress check when the rows themselves disagreed", () => {
    const { container } = render(
      <PipelineStrip refusal={refusal("scoped execution", "egress_row_mismatch")} />,
    );

    expect(states(container)).toEqual([
      "passed",
      "passed",
      "passed",
      "passed",
      "stopped",
      "unreached",
    ]);
  });

  it("draws nothing rather than a wrong picture for a layer it cannot place", () => {
    const unmappable = [
      refusal("tool arguments", "malformed_arguments"),
      refusal("tool execution", "tool_error"),
      refusal("scoped execution", "a_check_that_did_not_exist_yet"),
    ];

    for (const event of unmappable) {
      const { container } = render(<PipelineStrip refusal={event} />);
      expect(container.querySelector(".pipeline-strip")).toBeNull();
      cleanup();
    }
  });
});
