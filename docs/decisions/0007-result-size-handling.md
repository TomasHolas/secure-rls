# ADR 0007 — Result-size handling: hard cap, truncation signal, aggregation push-down

Status: accepted

## Context

Capping result rows protects the LLM context window and latency, but a naive cap
creates the "silent truncation" failure: the model receives a partial result set,
does not know it is partial, and confidently answers from incomplete data.
LangChain's own SQL agent limits rows only via a prompt instruction (`top_k`,
API default 10, tutorial uses 5), and its issue tracker documents models ignoring
it. Field experience (pgEdge's Postgres MCP server) and Anthropic's tool-design
guidance both prescribe enforced caps WITH explicit truncation signaling.

A key mechanical fact drives the design: SQL `LIMIT` trims OUTPUT rows only —
`AVG(salary)` is computed by the engine over all matching rows and returns one
row, so aggregates are never affected by a cap. The correctness risk exists only
when the model fetches raw rows and aggregates them in-context, which research
shows LLMs do unreliably.

## Decision

Three pieces, all in the scoped executor and the agent prompt:

1. **Hard server-side cap** — the executor enforces a maximum row count
   (`runtime.json` `db.max_result_rows`, default 200), never trusting the
   model to write its own LIMIT.
2. **Truncation signal** — when the cap trips, the tool result states it
   explicitly: `showing 200 of 543 rows — refine with WHERE or use an
   aggregate query` (total via a cheap COUNT over the scoped subquery; trivial
   at this scale). Never silent.
3. **Aggregation push-down** — the system prompt instructs: compute
   COUNT/SUM/AVG/GROUP BY in SQL, select only relevant columns, never
   `SELECT *` for analytical questions. The `get_stats` tool exists so the
   common aggregates need no generated SQL at all.

## Amendment: a character cap on what one result contributes to the prompt (issue #142)

Status: accepted, amends the row cap above.

**The row cap is a row count with no relation to the model's context.** 200 rows of two narrow
columns fit a 16384-token window comfortably; 200 rows of every column do not fit it at all. The
first live off-position eval run measured exactly that failure, three single-turn injection attacks
per tenant, all dying the same way:

```
acme injection-ignore-instructions turn1/1 status=NO DONE 2.8s tools=query_db
  stream failed: ResponseError: request (16921 tokens) exceeds the available context size (16384)
```

One turn, one thread, nothing to trim. The attack steers the model into a bare listing, the
executor returns the row cap's worth of rows, and the *second* model call of the turn — the one
that has to read the result — is refused. ADR 0011's sent-history bound cannot reach it: that bound
drops whole *older* turns and stops at `min_history_turns`, precisely so the current question and
the evidence this turn fetched survive. The overflow is inside the turn the floor protects.

**Measured, on the committed dataset, per tenant, at the 200-row cap** (characters of the rendering
handed to the model, and the estimated tokens at ADR 0011's 2.5 chars/token divisor):

| Result | acme | beta | gamma | est. tokens (acme) |
|---|---|---|---|---|
| `SELECT *` | 51,910 | 51,119 | 51,076 | 20,764 |
| `SELECT name, notes` | 41,874 | 40,999 | 40,774 | 16,750 |
| `SELECT name, salary, department, hire_date` | 9,201 | 9,211 | 9,133 | 3,681 |
| `SELECT name, salary, tenant_id` (the corpus' own widest) | 6,048 | 5,992 | 6,138 | 2,420 |
| `SELECT name, salary` | 4,636 | 4,580 | 4,526 | 1,855 |
| `SELECT department, AVG(salary) GROUP BY department` | 156 | 164 | 155 | 63 |
| `detect_anomalies(salary)` | 419 | 183 | 126 | 168 |

The worst legal result is four times the whole context window on its own, while every aggregate is
one row by construction and the widest listing the eval corpora actually ask for is 6,138
characters. That gap — three orders of magnitude between the results a question needs and the
result an attack can conjure — is what makes a character cap the right instrument.

**Decision.** `agent.max_tool_reply_chars` caps what one tool result may contribute to the
model-facing prompt. Over the cap, the model's copy keeps whole lines until the next one would not
fit and ends with a notice worded the way the row cap's own signal is worded — `[showing you N of M
lines of this result so it fits your context window; the user was shown all M - refine with WHERE
or use an aggregate query]`. Four properties:

- **The reader's copy is untouched.** `data` on the `tool_result` event still carries every row the
  row cap allowed, so the table, the chart, the anomalies and the notes a reader sees are exactly
  what they were. Only the model's view shrinks, and the event says so: `withheld` is how many
  rendered lines the model's copy lost, 0 when it lost none, and `turns.py` stores it so a replayed
  turn states the same thing. That is `history_trimmed`'s sibling honesty — a result the model saw
  only part of says so, and the reader's copy never silently disagrees with the model's.
- **The cut is line-aligned.** Every rendering here is one record per line, so the model reads the
  header and whole rows, never half of one, and the text it is handed stays a well-formed table. A
  single line too long to fit is withheld rather than sliced, which is what makes the cap a bound
  rather than a target.
- **The number is derived, not picked.** ADR 0011's send budget is `context_window -
  max_output_tokens - history_headroom_tokens` = 16384 - 4096 - 1024 = **11,264 tokens**. Measured
  against it, the bound tool definitions cost 1,632 estimated tokens and the system prompt 1,028 in
  the guardrails-on position (784 off), leaving **8,604 tokens for the messages**. The ceiling: the
  floor keeps `min_history_turns` = 2 turns, so a reply must fit twice over — `8604 / 2` = 4,302
  tokens, about 10,700 characters, before anything is left for the questions, the tool calls and
  the answers those turns also carry. The floor: 6,138 characters is the widest result the eval
  corpora ask for, and a cap under it would degrade real answers to buy nothing. **8,000** sits
  between them with margin at both ends — 30% above the widest legitimate result, 25% under the
  arithmetic ceiling. At 8000 / 2.5 = 3,200 estimated tokens, two floor turns of maximal replies
  come to 6,400 of the 8,604 available, leaving about 2,200 tokens for both turns' questions, tool
  calls and answers. `tests/test_runtime.py` asserts the arithmetic against the knobs rather than
  against the number, so a re-tuned window re-checks the cap instead of quietly outgrowing it.
- **Nothing about enforcement moves.** Every layer of ADR 0002 has already run on the real result
  before it is rendered at all — the validator on the statement, the authorizer and the scoping in
  the engine, the egress comparison on the rows that came back. The cap is presentation to the
  model, applied after the last of them, and it can neither widen nor narrow what a query may
  reach. The audit row `db.py` writes describes the query, not the rendering.

**Security framing.** This is availability hardening — OWASP LLM10 (Unbounded Consumption) reached
through LLM01 (Prompt Injection): a hostile prompt steers the model into a giant `SELECT` and kills
the turn on the endpoint's own context limit. The turn already failed closed and leaked nothing, so
the cap buys availability rather than confidentiality, and it is filed here rather than in ADR 0002
for that reason.

**Residual, stated.** The cap bounds one reply, not a turn. Three maximal replies in one turn, or
two maximal replies in each of the floor's two turns, still assemble a prompt past the budget and
still meet the endpoint's refusal. That is a far narrower edge than "any 200-row listing" — it needs
the model to fetch several near-maximal wide results in one turn, which no observed run has done —
and it is the same deliberate trade ADR 0011 states for a single enormous turn. The alternative is
dividing the cap by `max_tool_iterations` = 6, which lands at about 2,900 characters and cuts every
legitimate 200-row listing to a third of itself to buy a case nothing has produced.

## Consequences

- Aggregate answers are always computed over the full (tenant-scoped) data —
  the "unlucky 200 rows" statistical concern cannot occur for them.
- Raw-row listings are capped but honestly labeled, steering the model to
  refine filters (the behavior pgEdge observed in practice).
- The cap doubles as a DoS/resource control, complementing ADR 0002's
  hardening.

## Alternatives

- **No cap** — against all sourced practice; leaves context-window and
  silent-truncation risks unmanaged.
- **Prompt-only top_k (LangChain default)** — documented as ignorable by the
  model; advisory, not enforcement.

## References

- LangChain SQL agent tutorial and `create_sql_agent` reference (top_k) —
  https://docs.langchain.com/oss/python/langchain/sql-agent,
  https://reference.langchain.com/python/langchain-community/agent_toolkits/sql/base/create_sql_agent
- LangChain, "LLMs and SQL" (context-size rationale) —
  https://www.langchain.com/blog/llms-and-sql
- langchain issue #13931 (prompt-level top_k surprising users) —
  https://github.com/langchain-ai/langchain/issues/13931
- Anthropic, "Writing tools for agents" (pagination/truncation with steering
  messages; high-signal results) —
  https://www.anthropic.com/engineering/writing-tools-for-agents
- OpenAI Cookbook, BigQuery GPT Action ("Add a limit of 100 rows") —
  https://developers.openai.com/cookbook/examples/chatgpt/gpt_actions_library/gpt_action_bigquery
- pgEdge, "Lessons learned writing an MCP server for PostgreSQL" (the
  "100 rows shown, more available" pattern and observed model behavior) —
  https://www.pgedge.com/blog/lessons-learned-writing-an-mcp-server-for-postgresql
- OWASP LLM10:2025, Unbounded Consumption — the risk the amendment hardens
  against: per-request resource limits so a single request cannot exhaust the
  model's context budget —
  https://genai.owasp.org/llmrisk/llm102025-unbounded-consumption/
- OWASP LLM01:2025, Prompt Injection — the vector that reaches it, a hostile
  instruction steering the model into the oversized request —
  https://genai.owasp.org/llmrisk/llm012025-prompt-injection/
- Ollama Modelfile parameters (`num_ctx`, `num_predict`) — the endpoint-side
  limit the amendment's arithmetic is measured against —
  https://github.com/ollama/ollama/blob/main/docs/modelfile.md#parameter
- Wolff and Hulsebos, "How well do LLMs reason over tabular data, really?"
  (tabular-reasoning deficits) — https://arxiv.org/abs/2505.07453
