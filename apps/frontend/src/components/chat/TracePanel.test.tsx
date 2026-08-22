/**
 * What the trace panel actually renders for one turn: the reader-meaningful entries only, folded
 * from a scripted event sequence, and the same panel for the replayed turn of issue #70.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TracePanel } from "./TracePanel";
import { applyEvent, replayTurns, startTurn } from "../../lib/trace";
import type { Turn } from "../../lib/trace";
import type { TraceEvent } from "../../lib/sse";

afterEach(cleanup);

const GENERATED = "SELECT department, AVG(salary) FROM employees GROUP BY department";
const EXECUTED =
  "SELECT department, AVG(salary) FROM (SELECT * FROM employees WHERE tenant_id = ?) GROUP BY department";
const RESULT_DATA = {
  generated_sql: GENERATED,
  executed_sql: EXECUTED,
  columns: ["department", "avg"],
  rows: [["Engineering", 91000]],
  total_count: 3,
  returned_count: 3,
  truncated: false,
};

/** Every label the panel used to invent from a graph node name; none may come back. */
const NODE_LABELS = [
  "Reasoning",
  "Validating the tool call",
  "Running the tool",
  "Auditing the outcome",
  "Composing the answer",
  "reason",
  "validate",
  "execute_tool",
  "audit",
  "respond",
];

/** A two-round turn: it thinks, calls a tool, then thinks again about what came back. */
const TWO_ROUNDS: TraceEvent[] = [
  { type: "node_start", node: "reason" },
  { type: "reasoning", text: "an average " },
  { type: "reasoning", text: "per department" },
  { type: "node_start", node: "validate" },
  { type: "tool_call", id: "c1", tool: "query_db", args: { sql: GENERATED } },
  { type: "node_start", node: "execute_tool" },
  { type: "node_start", node: "audit" },
  { type: "tool_result", id: "c1", tool: "query_db", content: "department | avg", data: RESULT_DATA },
  { type: "node_start", node: "reason" },
  { type: "reasoning", text: "Engineering leads" },
  { type: "token", text: "Engineering averages 91000." },
  { type: "node_start", node: "respond" },
];

/** One scripted second per event, so a settled thinking step reports a round number of seconds. */
const TICK = 1000;

function fold(events: TraceEvent[]): Turn {
  return events.reduce(
    (turn, event, index) => applyEvent(turn, event, index * TICK),
    startTurn("average salary per department?"),
  );
}

function panel(events: TraceEvent[], streaming = false) {
  return render(<TracePanel items={fold(events).items} streaming={streaming} open />);
}

function titles(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".trace-step-title")].map(
    (title) => title.textContent ?? "",
  );
}

describe("the entries the panel renders", () => {
  it("reads thinking, tool card, thinking - and nothing else", () => {
    const { container } = panel(TWO_ROUNDS);

    expect(titles(container)).toEqual(["Thought for 3.0s", "query_db", "Thinking"]);
  });

  it("names no graph node anywhere in the rendered trace", () => {
    const { container } = panel(TWO_ROUNDS);

    for (const label of NODE_LABELS) {
      expect(container.textContent).not.toContain(label);
    }
  });

  it("groups one round's chunks into one step and marks the round after the results", () => {
    const { container } = panel(TWO_ROUNDS);
    const steps = [...container.querySelectorAll(".trace-step-muted")];

    expect(steps).toHaveLength(2);
    expect(steps[0].querySelector(".trace-step-meta")).toBeNull();
    expect(steps[1].querySelector(".trace-step-meta")?.textContent).toBe("round 2");
  });

  it("keeps the round still thinking open and folds away the one that settled", () => {
    panel(TWO_ROUNDS);

    expect(screen.getByRole("button", { name: /Thought for/ }).getAttribute("aria-expanded")).toBe(
      "false",
    );
    expect(screen.getByRole("button", { name: /Thinking/ }).getAttribute("aria-expanded")).toBe(
      "true",
    );
    expect(screen.queryByText("an average per department")).toBeNull();
    expect(screen.getByText("Engineering leads")).toBeTruthy();
  });

  it("shows a settled round's whole thinking as one block when the reader opens it", () => {
    panel(TWO_ROUNDS);

    fireEvent.click(screen.getByRole("button", { name: /Thought for/ }));

    expect(screen.getByText("an average per department")).toBeTruthy();
  });

  it("keeps a step the reader folded closed while it is still working", () => {
    const items = fold(TWO_ROUNDS).items;
    const { rerender } = render(<TracePanel items={items} streaming open />);

    fireEvent.click(screen.getByRole("button", { name: /Thinking/ }));
    rerender(<TracePanel items={[...items]} streaming open />);

    expect(screen.getByRole("button", { name: /Thinking/ }).getAttribute("aria-expanded")).toBe(
      "false",
    );
  });

  it("renders nothing at all for a turn whose only events were graph nodes", () => {
    const { container } = panel([
      { type: "node_start", node: "reason" },
      { type: "node_start", node: "validate" },
      { type: "node_start", node: "respond" },
    ]);

    expect(container.querySelector(".trace")).toBeNull();
  });

  it("counts the entries a reader can see, not the events that produced them", () => {
    const { container } = panel(TWO_ROUNDS);

    expect(container.querySelector(".trace-count")?.textContent).toBe("3");
  });
});

describe("a tool card's own state", () => {
  const CALL: TraceEvent[] = [
    { type: "node_start", node: "validate" },
    { type: "tool_call", id: "c1", tool: "query_db", args: { sql: GENERATED } },
    { type: "node_start", node: "execute_tool" },
  ];

  it("says the call is in flight while no outcome has landed", () => {
    const { container } = panel(CALL, true);

    expect(screen.getByText("running")).toBeTruthy();
    expect(titles(container)).toEqual(["query_db"]);
  });

  it("replaces the pending chip with the evidence once it settles", () => {
    const { container } = panel([
      ...CALL,
      { type: "tool_result", id: "c1", tool: "query_db", content: "d | a", data: RESULT_DATA },
    ]);

    expect(screen.queryByText("running")).toBeNull();
    expect(screen.getByText("3 rows")).toBeTruthy();
    expect(container.querySelector(".sql-rewrite")).not.toBeNull();
    expect(container.querySelectorAll("td")).toHaveLength(2);
  });

  it("states the truncation on the card that was capped", () => {
    panel([
      { type: "tool_call", id: "c1", tool: "query_db", args: { sql: "SELECT * FROM employees" } },
      {
        type: "tool_result",
        id: "c1",
        tool: "query_db",
        content: "capped",
        data: { columns: ["name"], rows: [["Ada"]], total_count: 543, returned_count: 200, truncated: true },
      },
    ]);

    expect(screen.getByText("showing 200 of 543 rows")).toBeTruthy();
  });

  it("shows a retried call as its own card with the attempt and the fed-back reason", () => {
    const { container } = panel([
      { type: "tool_call", id: "c1", tool: "query_db", args: { sql: "SELECT * FROM employee" } },
      {
        type: "retry",
        id: "c1",
        tool: "query_db",
        layer: "query validation",
        kind: "malformed_sql",
        attempt: 1,
        max_attempts: 2,
        reason: "no such table: employee",
      },
    ]);

    expect(container.querySelector(".trace-step-warn")).not.toBeNull();
    expect(screen.getByText("attempt 1 of 2")).toBeTruthy();
    expect(screen.getByText(/no such table: employee/)).toBeTruthy();
  });

  it("shows a refused call as a blocked card naming the layer and the reason", () => {
    const { container } = panel([
      { type: "tool_call", id: "c1", tool: "query_db", args: { sql: "SELECT * FROM employees" } },
      {
        type: "security_event",
        id: "c1",
        tool: "query_db",
        layer: "query validation",
        kind: "cross_tenant",
        reason: "the statement filters another tenant",
      },
    ]);

    expect(container.querySelector(".trace-step-blocked")).not.toBeNull();
    expect(screen.getByText("blocked")).toBeTruthy();
    expect(screen.getByText(/the statement filters another tenant/)).toBeTruthy();
    expect(screen.getByText(/query validation/)).toBeTruthy();
  });
});

describe("a replayed turn", () => {
  const LIVE: TraceEvent[] = [
    { type: "tool_call", id: "c1", tool: "query_db", args: { sql: GENERATED } },
    { type: "tool_result", id: "c1", tool: "query_db", content: "d | a", data: RESULT_DATA },
  ];

  it("renders its evidence exactly as the live turn rendered it", () => {
    const live = render(<TracePanel items={fold(LIVE).items} streaming={false} open />);
    const liveHtml = live.container.innerHTML;
    cleanup();

    const [replayed] = replayTurns(
      [
        { role: "user", content: "average salary per department?" },
        { role: "assistant", content: "Engineering averages 91000." },
      ],
      [{ turn: 1, tool: "query_db", data: RESULT_DATA }],
    );
    const replay = render(<TracePanel items={replayed.items} streaming={false} open />);

    expect(titles(replay.container)).toEqual(["query_db"]);
    expect(replay.container.innerHTML).toBe(liveHtml);
  });
});
