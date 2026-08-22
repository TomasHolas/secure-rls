/**
 * Turn state: scripted trace sequences folded into what the view renders, and the same fold
 * from the other side - what `GET /conversations/{id}` still remembers of a reopened thread.
 */

import { describe, expect, it } from "vitest";

import { applyEvent, failTurn, replayTurns, startTurn, tokensPerSecond } from "./trace";
import type { CallItem, ReasoningItem, Turn } from "./trace";
import type { ChartSpec } from "../components/charts";
import type { DoneEvent, TraceEvent, TurnStatus } from "./sse";

const GENERATED = "SELECT department, AVG(salary) FROM employees GROUP BY department";
const EXECUTED =
  "SELECT department, AVG(salary) FROM (SELECT * FROM employees WHERE tenant_id = ?) GROUP BY department";
const COST = { input_tokens: 250, output_tokens: 28, duration_s: 2 };

/** A terminal frame as the backend sends it: how the turn ended, plus what it cost. */
function done(status: TurnStatus, answer: string, model = "m"): DoneEvent {
  return { type: "done", status, answer, model, ...COST };
}

function fold(events: TraceEvent[], question = "average salary per department"): Turn {
  return events.reduce(applyEvent, startTurn(question));
}

function calls(turn: Turn): CallItem[] {
  return turn.items.filter((item): item is CallItem => item.kind === "call");
}

function thoughts(turn: Turn): ReasoningItem[] {
  return turn.items.filter((item): item is ReasoningItem => item.kind === "reasoning");
}

describe("a turn that answers", () => {
  const turn = fold([
    { type: "node_start", node: "reason" },
    { type: "reasoning", text: "an average " },
    { type: "reasoning", text: "per group" },
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
    { type: "reasoning", text: "the table answers it" },
    { type: "token", text: "Engineering " },
    { type: "token", text: "averages 91000." },
    { type: "node_start", node: "respond" },
    done("ok", "Engineering averages 91000.", "qwen3:8b"),
  ]);

  it("streams the answer from the token events", () => {
    expect(turn.answer).toBe("Engineering averages 91000.");
  });

  it("closes on the done event with its status and model", () => {
    expect(turn.phase).toBe("ok");
    expect(turn.model).toBe("qwen3:8b");
    expect(turn.error).toBeNull();
  });

  it("renders no step for a graph node: the items are the thinking and the call", () => {
    expect(turn.items.map((item) => item.kind)).toEqual(["reasoning", "call", "reasoning"]);
  });

  it("groups each round's thinking into one step and numbers the rounds", () => {
    expect(thoughts(turn)).toEqual([
      { kind: "reasoning", round: 1, text: "an average per group" },
      { kind: "reasoning", round: 2, text: "the table answers it" },
    ]);
    expect(turn.round).toBe(2);
  });

  it("reports what the turn cost from its terminal frame", () => {
    expect(turn.usage).toEqual({ inputTokens: 250, outputTokens: 28, durationS: 2 });
    expect(tokensPerSecond(turn.usage)).toBe(14);
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
      done("ok", "a"),
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
    done("ok", "Ada."),
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
    done("blocked", "I cannot run that query."),
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

describe("streamed reasoning", () => {
  const turn = fold([
    { type: "node_start", node: "reason" },
    { type: "reasoning", text: "the question asks for " },
    { type: "reasoning", text: "an average per group" },
    { type: "token", text: "Engineering averages 91000." },
    { type: "node_start", node: "respond" },
    done("ok", "Engineering averages 91000."),
  ]);

  it("accumulates into one step of its round, not on the answer", () => {
    expect(turn.items).toEqual([
      { kind: "reasoning", round: 1, text: "the question asks for an average per group" },
    ]);
    expect(turn.answer).toBe("Engineering averages 91000.");
  });

  it("keeps a second round's thinking as its own step, numbered", () => {
    const twice = fold([
      { type: "node_start", node: "reason" },
      { type: "reasoning", text: "first thought" },
      { type: "node_start", node: "validate" },
      { type: "node_start", node: "execute_tool" },
      { type: "node_start", node: "audit" },
      { type: "node_start", node: "reason" },
      { type: "reasoning", text: "second thought" },
    ]);

    expect(twice.items).toEqual([
      { kind: "reasoning", round: 1, text: "first thought" },
      { kind: "reasoning", round: 2, text: "second thought" },
    ]);
  });

  it("opens the first round's step for reasoning that arrives before its node was announced", () => {
    const early = fold([{ type: "reasoning", text: "thinking already" }]);

    expect(early.items).toEqual([{ kind: "reasoning", round: 1, text: "thinking already" }]);
    expect(early.answer).toBe("");
  });

  it("has nothing to show for a round that streamed no thinking", () => {
    const silent = fold([
      { type: "node_start", node: "reason" },
      { type: "node_start", node: "validate" },
      { type: "node_start", node: "respond" },
    ]);

    expect(silent.items).toEqual([]);
    expect(silent.round).toBe(1);
  });
});

describe("what a turn cost", () => {
  it("has no rate to state for a turn that generated nothing", () => {
    const turn = fold([{ type: "done", status: "blocked", answer: "no", model: "m", input_tokens: 40, output_tokens: 0, duration_s: 0.5 }]);

    expect(turn.usage).toEqual({ inputTokens: 40, outputTokens: 0, durationS: 0.5 });
    expect(tokensPerSecond(turn.usage)).toBeNull();
  });

  it("has no rate to state before the terminal frame or without a measured duration", () => {
    expect(tokensPerSecond(null)).toBeNull();
    expect(tokensPerSecond({ inputTokens: 10, outputTokens: 20, durationS: 0 })).toBeNull();
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
    const turn = fold([done("gave_up", "I gave up.")]);

    expect(turn).toMatchObject({ phase: "gave_up", answer: "I gave up." });
  });

  it("keeps the partial answer of a turn a per-turn bound cut short", () => {
    const notice = "I stopped this turn early: the turn reached its 120s time limit.";
    const turn = fold([
      { type: "node_start", node: "reason" },
      { type: "token", text: "Engineering averages " },
      { type: "token", text: `\n\n${notice}` },
      done("cut_short", `Engineering averages\n\n${notice}`),
    ]);

    expect(turn).toMatchObject({
      phase: "cut_short",
      answer: `Engineering averages \n\n${notice}`,
      error: null,
    });
  });

  it("states the backend's own diagnosis when the turn ends in a failed frame", () => {
    const diagnosis = "The turn ended in a server-side failure before an answer was composed.";
    const turn = fold([
      { type: "node_start", node: "reason" },
      { type: "token", text: "Engineering " },
      done("failed", diagnosis, "qwen3:8b"),
    ]);

    expect(turn).toMatchObject({
      phase: "failed",
      answer: "Engineering ",
      error: diagnosis,
      model: "qwen3:8b",
    });
  });

  it("does not turn a failed frame's reason into the answer of the turn", () => {
    const turn = fold([done("failed", "it broke")]);

    expect(turn.answer).toBe("");
    expect(turn.error).toBe("it broke");
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

describe("a reopened thread", () => {
  const CHART: ChartSpec = {
    kind: "bar",
    title: "headcount by department",
    x_label: "department",
    y_label: "headcount",
    data: [{ x: "Engineering", y: 12 }],
  };
  const MESSAGES = [
    { role: "user", content: "average salary per department?" },
    { role: "assistant", content: "Engineering averages 91000." },
    { role: "user", content: "plot headcount by department" },
    { role: "assistant", content: "Engineering leads on headcount." },
  ];
  const RESULTS = [
    {
      turn: 1,
      tool: "query_db",
      data: {
        generated_sql: GENERATED,
        executed_sql: EXECUTED,
        columns: ["department", "avg"],
        rows: [["Engineering", 91000]],
      },
    },
    { turn: 2, tool: "plot", data: { chart_spec: CHART } },
  ];

  it("folds the transcript into one turn per question", () => {
    const turns = replayTurns(MESSAGES, []);

    expect(turns.map((turn) => [turn.question, turn.answer])).toEqual([
      ["average salary per department?", "Engineering averages 91000."],
      ["plot headcount by department", "Engineering leads on headcount."],
    ]);
    expect(turns.every((turn) => turn.phase === "replayed")).toBe(true);
  });

  it("puts each stored payload back on the turn that asked for it", () => {
    const turns = replayTurns(MESSAGES, RESULTS);

    const first = calls(turns[0]);
    const second = calls(turns[1]);
    expect(first.map((item) => item.tool)).toEqual(["query_db"]);
    expect(second.map((item) => item.tool)).toEqual(["plot"]);
    expect(first[0].outcome).toMatchObject({
      type: "tool_result",
      data: { generated_sql: GENERATED, executed_sql: EXECUTED },
    });
    expect(second[0].outcome).toMatchObject({ data: { chart_spec: CHART } });
  });

  it("claims nothing a replayed turn cannot know: no thinking, no model, no cost", () => {
    const turns = replayTurns(MESSAGES, RESULTS);

    expect(thoughts(turns[0])).toEqual([]);
    expect(turns[0].model).toBeNull();
    expect(turns[0].usage).toBeNull();
    expect(turns[0].error).toBeNull();
    expect(calls(turns[0])[0].args).toEqual({});
  });

  it("keeps both halves of a turn that spoke twice, as two paragraphs", () => {
    const turns = replayTurns(
      [
        { role: "user", content: "and the outliers?" },
        { role: "assistant", content: "Let me check the fences." },
        { role: "assistant", content: "Three rows lie beyond them." },
      ],
      [],
    );

    expect(turns).toHaveLength(1);
    expect(turns[0].answer).toBe("Let me check the fences.\n\nThree rows lie beyond them.");
  });

  it("attaches no evidence at all to a transcript that starts without a question", () => {
    const turns = replayTurns([{ role: "assistant", content: "orphaned answer" }], RESULTS);

    expect(turns).toHaveLength(1);
    expect(turns[0].question).toBe("");
    expect(turns[0].items).toEqual([]);
  });

  it("replays a thread that never ran a tool as turns with no trace items", () => {
    const turns = replayTurns(MESSAGES.slice(0, 2), []);

    expect(turns).toHaveLength(1);
    expect(turns[0].items).toEqual([]);
  });
});
