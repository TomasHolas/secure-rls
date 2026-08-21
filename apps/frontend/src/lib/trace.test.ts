/** Turn state: scripted trace sequences folded into what the view renders. */

import { describe, expect, it } from "vitest";

import { applyEvent, failTurn, startTurn } from "./trace";
import type { CallItem, Turn } from "./trace";
import type { TraceEvent } from "./sse";

const GENERATED = "SELECT department, AVG(salary) FROM employees GROUP BY department";
const EXECUTED =
  "SELECT department, AVG(salary) FROM (SELECT * FROM employees WHERE tenant_id = ?) GROUP BY department";

function fold(events: TraceEvent[], question = "average salary per department"): Turn {
  return events.reduce(applyEvent, startTurn(question));
}

function calls(turn: Turn): CallItem[] {
  return turn.items.filter((item): item is CallItem => item.kind === "call");
}

describe("a turn that answers", () => {
  const turn = fold([
    { type: "node_start", node: "reason" },
    { type: "node_start", node: "reason" },
    { type: "node_start", node: "validate" },
    { type: "tool_call", id: "c1", tool: "query_db", args: { sql: GENERATED } },
    { type: "node_start", node: "execute_tool" },
    {
      type: "tool_result",
      id: "c1",
      tool: "query_db",
      content: "department | avg",
      data: {
        generated_sql: GENERATED,
        executed_sql: EXECUTED,
        columns: ["department", "avg"],
        rows: [["Engineering", 91000]],
        total_count: 3,
        returned_count: 3,
        truncated: false,
      },
    },
    { type: "node_start", node: "reason" },
    { type: "token", text: "Engineering " },
    { type: "token", text: "averages 91000." },
    { type: "node_start", node: "respond" },
    { type: "done", status: "ok", answer: "Engineering averages 91000.", model: "qwen3:8b" },
  ]);

  it("streams the answer from the token events", () => {
    expect(turn.answer).toBe("Engineering averages 91000.");
  });

  it("closes on the done event with its status and model", () => {
    expect(turn.phase).toBe("ok");
    expect(turn.model).toBe("qwen3:8b");
    expect(turn.error).toBeNull();
  });

  it("collapses consecutive entries into the same node but keeps a later re-entry", () => {
    const nodes = turn.items.filter((item) => item.kind === "node");
    expect(nodes).toEqual([
      { kind: "node", node: "reason" },
      { kind: "node", node: "validate" },
      { kind: "node", node: "execute_tool" },
      { kind: "node", node: "reason" },
      { kind: "node", node: "respond" },
    ]);
  });

  it("pairs the result with its call, carrying both statements", () => {
    const [call] = calls(turn);
    expect(call.tool).toBe("query_db");
    expect(call.args).toEqual({ sql: GENERATED });
    expect(call.outcome).toMatchObject({
      type: "tool_result",
      data: { generated_sql: GENERATED, executed_sql: EXECUTED },
    });
  });
});

describe("a capped result", () => {
  it("keeps the truncation facts on the call for the chip to state", () => {
    const turn = fold([
      { type: "tool_call", id: "c1", tool: "query_db", args: { sql: "SELECT * FROM employees" } },
      {
        type: "tool_result",
        id: "c1",
        tool: "query_db",
        content: "showing 200 of 543 rows",
        data: { columns: ["name"], rows: [["Ada"]], total_count: 543, returned_count: 200, truncated: true },
      },
      { type: "done", status: "ok", answer: "a", model: "m" },
    ]);

    expect(calls(turn)[0].outcome).toMatchObject({
      data: { truncated: true, returned_count: 200, total_count: 543 },
    });
  });
});

describe("a retried call", () => {
  const turn = fold([
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
    { type: "tool_call", id: "c2", tool: "query_db", args: { sql: "SELECT * FROM employees" } },
    {
      type: "tool_result",
      id: "c2",
      tool: "query_db",
      content: "name",
      data: { columns: ["name"], rows: [["Ada"]], total_count: 1, returned_count: 1, truncated: false },
    },
    { type: "done", status: "ok", answer: "Ada.", model: "m" },
  ]);

  it("attaches the retry to the call that failed, with its counter and reason", () => {
    expect(calls(turn)[0].outcome).toEqual({
      type: "retry",
      id: "c1",
      tool: "query_db",
      layer: "query validation",
      kind: "malformed_sql",
      attempt: 1,
      max_attempts: 2,
      reason: "no such table: employee",
    });
  });

  it("keeps the second attempt as its own call", () => {
    expect(calls(turn)).toHaveLength(2);
    expect(calls(turn)[1].outcome).toMatchObject({ type: "tool_result" });
    expect(turn.phase).toBe("ok");
  });
});

describe("a refused call", () => {
  const turn = fold([
    { type: "tool_call", id: "c1", tool: "query_db", args: { sql: "SELECT * FROM sqlite_master" } },
    {
      type: "security_event",
      id: "c1",
      tool: "query_db",
      layer: "query validation",
      kind: "policy_violation",
      reason: "table sqlite_master is not allowlisted",
    },
    { type: "token", text: "I cannot run that query." },
    { type: "done", status: "blocked", answer: "I cannot run that query.", model: "m" },
  ]);

  it("marks the turn blocked and keeps the layer, kind and reason", () => {
    expect(turn.phase).toBe("blocked");
    expect(calls(turn)[0].outcome).toMatchObject({
      type: "security_event",
      layer: "query validation",
      kind: "policy_violation",
      reason: "table sqlite_master is not allowlisted",
    });
  });

  it("shows the refusal the graph composed as the answer", () => {
    expect(turn.answer).toBe("I cannot run that query.");
  });
});

describe("other tool payloads", () => {
  it("carries a chart spec through to the chart brick", () => {
    const chart_spec = {
      kind: "bar" as const,
      title: "Headcount by department",
      x_label: "department",
      y_label: "employees",
      data: [{ x: "Engineering", y: 12 }],
    };
    const turn = fold([
      { type: "tool_call", id: "p1", tool: "plot", args: { kind: "bar", column: "department" } },
      { type: "tool_result", id: "p1", tool: "plot", content: "chart ready", data: { chart_spec } },
    ]);

    expect(calls(turn)[0].outcome).toMatchObject({ data: { chart_spec } });
  });

  it("carries retrieved notes through", () => {
    const notes = [{ user_id: 7, name: "Ada", note: "strong quarter", distance: 0.21 }];
    const turn = fold([
      { type: "tool_call", id: "n1", tool: "search_notes", args: { query: "quarter" } },
      { type: "tool_result", id: "n1", tool: "search_notes", content: "Ada", data: { notes } },
    ]);

    expect(calls(turn)[0].outcome).toMatchObject({ data: { notes } });
  });
});

describe("edge cases", () => {
  it("keeps an outcome whose call was never announced instead of dropping it", () => {
    const turn = fold([
      {
        type: "security_event",
        id: "ghost",
        tool: "query_db",
        layer: "scoped execution",
        kind: "egress_mismatch",
        reason: "a row carries another tenant",
      },
    ]);

    expect(turn.items).toEqual([
      {
        kind: "orphan",
        outcome: {
          type: "security_event",
          id: "ghost",
          tool: "query_db",
          layer: "scoped execution",
          kind: "egress_mismatch",
          reason: "a row carries another tenant",
        },
      },
    ]);
  });

  it("does not overwrite a closed call when a second outcome repeats its id", () => {
    const turn = fold([
      { type: "tool_call", id: "c1", tool: "get_stats", args: { metric: "avg" } },
      { type: "tool_result", id: "c1", tool: "get_stats", content: "first", data: {} },
      { type: "tool_result", id: "c1", tool: "get_stats", content: "second", data: {} },
    ]);

    expect(calls(turn)[0].outcome).toMatchObject({ content: "first" });
    expect(turn.items).toHaveLength(2);
  });

  it("falls back to the done answer when no token carried text", () => {
    const turn = fold([{ type: "done", status: "gave_up", answer: "I gave up.", model: "m" }]);

    expect(turn).toMatchObject({ phase: "gave_up", answer: "I gave up." });
  });

  it("records a broken stream as a failed turn without inventing an answer", () => {
    const streaming = fold([{ type: "node_start", node: "reason" }]);
    const failed = failTurn(streaming, "The model endpoint is unavailable.");

    expect(failed).toMatchObject({
      phase: "failed",
      answer: "",
      error: "The model endpoint is unavailable.",
    });
    expect(failed.items).toEqual(streaming.items);
  });

  it("never mutates the turn it is given", () => {
    const before = startTurn("q");
    const after = applyEvent(before, { type: "token", text: "hi" });

    expect(before).toEqual(startTurn("q"));
    expect(after).not.toBe(before);
  });
});
