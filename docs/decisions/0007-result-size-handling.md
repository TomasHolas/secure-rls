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
- Wolff and Hulsebos, "How well do LLMs reason over tabular data, really?"
  (tabular-reasoning deficits) — https://arxiv.org/abs/2505.07453
