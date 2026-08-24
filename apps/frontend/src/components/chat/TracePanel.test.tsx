/**
 * What the trace panel actually renders for one turn: the reader-meaningful entries only, folded
 * from a scripted event sequence, and the same panel for a turn replayed out of the history the
 * server stored for it (issues #70, #90) - which must come out of these same bricks.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TracePanel } from "./TracePanel";
import { applyEvent, replayTurns, startTurn } from "../../lib/trace";
import type { Turn } from "../../lib/trace";
import type { TraceEvent } from "../../lib/sse";
import type { TurnRecord } from "../../lib/api";

afterEach(cleanup);

const GENERATED = "SELECT department, AVG(salary) FROM employees GROUP BY department";
const EXECUTED =
  "SELECT department, AVG(salary) FROM (SELECT * FROM employees WHERE tenant_id = ?) GROUP BY department";
const FOREIGN = "SELECT * FROM employees WHERE tenant_id = 'beta'";
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
    const rendered = titles(container);

    expect(rendered.slice(0, 2)).toEqual(["Thought for 3.0s", "query_db"]);
    // The round still thinking counts up from the clock, so its title is the label plus that.
    expect(rendered[2]).toMatch(/^Thinking[\d.]+s$/);
    expect(rendered).toHaveLength(3);
  });

  it("shimmers and counts up on the round still thinking, with no pixel grid anywhere", () => {
    const { container } = panel(TWO_ROUNDS, true);

    const live = container.querySelector(".trace-step-muted:last-of-type .loader");
    expect(live?.querySelector(".loader-label")?.textContent).toBe("Thinking");
    expect(live?.querySelector(".loader-elapsed")?.textContent).toMatch(/^[\d.]+s$/);
    // The owner's placement ruling on #123: one grid per turn, and it is the answer card's.
    expect(container.querySelectorAll(".loader-cell")).toHaveLength(0);
    expect(container.querySelector(".trace-head .loader")).toBeNull();
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

  it("says the model read less of a result than the table beside it shows", () => {
    panel([
      { type: "tool_call", id: "c1", tool: "query_db", args: { sql: "SELECT * FROM employees" } },
      {
        type: "tool_result",
        id: "c1",
        tool: "query_db",
        content: "cut for the model",
        withheld: 183,
        data: RESULT_DATA,
      },
    ]);

    expect(screen.getByText("183 lines withheld from the model")).toBeTruthy();
    expect(screen.getByText("3 rows")).toBeTruthy();
  });

  it("says nothing about the model's copy when it was handed the whole result", () => {
    panel([
      ...CALL,
      {
        type: "tool_result",
        id: "c1",
        tool: "query_db",
        content: "d | a",
        withheld: 0,
        data: RESULT_DATA,
      },
    ]);

    expect(screen.queryByText(/withheld from the model/)).toBeNull();
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
  const TRANSCRIPT = [
    { role: "user", content: "average salary per department?" },
    { role: "assistant", content: "Engineering averages 91000." },
  ];
  /** A turn with one settled call, one retried call and one refused: no thinking, so no measured span. */
  const SETTLED: TraceEvent[] = [
    { type: "tool_call", id: "c1", tool: "query_db", args: { sql: GENERATED } },
    { type: "tool_result", id: "c1", tool: "query_db", content: "d | a", data: RESULT_DATA },
    { type: "tool_call", id: "c2", tool: "get_stats", args: { metric: "avg", column: "salary" } },
    {
      type: "retry",
      id: "c2",
      tool: "get_stats",
      layer: "tool arguments",
      kind: "malformed_arguments",
      attempt: 1,
      max_attempts: 3,
      reason: "metric must be one of count, sum, avg",
    },
    { type: "tool_call", id: "c3", tool: "query_db", args: { sql: FOREIGN } },
    {
      type: "security_event",
      id: "c3",
      tool: "query_db",
      layer: "query validation",
      kind: "policy_violation",
      reason: "the statement filters another tenant",
    },
  ];

  /** The same events, as the server stores them for replay: the model-facing text is not kept. */
  function stored(events: TraceEvent[]) {
    return [
      {
        turn: 1,
        cut: 0,
        events: events.map((event) =>
          event.type === "tool_result" ? { ...event, content: "" } : event,
        ),
      },
    ] as TurnRecord[];
  }

  function replayedItems(events: TraceEvent[]) {
    return replayTurns(TRANSCRIPT, stored(events))[0].items;
  }

  it("renders its whole trace exactly as the live turn rendered it", () => {
    const live = render(<TracePanel items={fold(SETTLED).items} streaming={false} open />);
    const liveHtml = live.container.innerHTML;
    cleanup();

    const replay = render(<TracePanel items={replayedItems(SETTLED)} streaming={false} open />);

    expect(titles(replay.container)).toEqual(["query_db", "get_stats", "query_db"]);
    expect(replay.container.innerHTML).toBe(liveHtml);
  });

  it("renders a replayed thought through the same step, minus the span this browser measured", () => {
    const thinking: TraceEvent[] = [
      { type: "node_start", node: "reason" },
      { type: "reasoning", text: "an average per department" },
      ...SETTLED,
    ];
    const live = render(<TracePanel items={fold(thinking).items} streaming={false} open />);
    const liveHtml = live.container.innerHTML.replace(/Thought for [\d.]+s/, "Thought");
    cleanup();

    const replay = render(<TracePanel items={replayedItems(thinking)} streaming={false} open />);

    expect(titles(replay.container)[0]).toBe("Thought");
    expect(replay.container.innerHTML).toBe(liveHtml);
  });

  it("marks a thought the server's history cap cut short", () => {
    const capped = [
      {
        turn: 1,
        cut: 0,
        events: [{ type: "reasoning", text: "as far as the cap", truncated: true }],
      },
    ] as TurnRecord[];

    const { container } = render(
      <TracePanel items={replayTurns(TRANSCRIPT, capped)[0].items} streaming={false} open />,
    );

    expect(screen.getByText("thinking capped")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Thought/ }));
    expect(container.querySelector(".trace-reasoning")?.textContent).toBe("as far as the cap");
  });

  it("renders a stored argument as text, never as markup", () => {
    const injected = "SELECT * FROM employees<script>alert(1)</script>";
    const events: TraceEvent[] = [
      { type: "tool_call", id: "c1", tool: "query_db", args: { sql: injected } },
    ];

    const { container } = render(
      <TracePanel items={replayedItems(events)} streaming={false} open />,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain(injected);
  });
});
