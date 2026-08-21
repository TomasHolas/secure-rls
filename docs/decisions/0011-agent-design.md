# ADR 0011 — Agent design: explicit graph, retry policy, memory, tool contracts

Status: accepted

## Context

The assignment names LangChain/LangGraph. Within LangGraph the choice is the
prebuilt ReAct agent versus an explicit graph; and the tool layer needs
contracts that keep model output untrusted end to end.

## Decision

### Explicit LangGraph graph

Built from named nodes (roughly: reason -> execute-tool -> validate/audit ->
respond), not the prebuilt ReAct helper. Rationale: demonstrable LangGraph
fluency, and the audit trail (ADR 0002) hooks into first-class graph nodes
instead of callbacks around a black box.

### Retry policy

- **Security rejections are terminal — zero retries**: forbidden table,
  non-SELECT, PRAGMA/ATTACH, multi-statement, authorizer denial, egress trip.
  Retrying would let the agent probe the boundary; the event is logged as a
  security event and surfaced as an explicit refusal.
- **Honest errors retry, max 3 attempts**: SQL syntax errors, unknown
  column, execution errors, malformed tool arguments. The error reason is fed
  back to the model so it can correct. Attempt budget is a `runtime.json`
  tunable.
- **Unexpected tool failures retry too, on the same budget (amended after
  issue #66)**: the tool invocation is wrapped in a catch-all, so an exception
  no layer anticipated becomes a retry on a `tool execution` layer with kind
  `tool_error` instead of escaping the graph and killing the turn. Two rules
  keep it honest: the reason handed to the model names the failing tool and
  nothing else — no path, no stack frame, no exception class (OWASP error
  handling: no internal detail into an untrusted context, and the model is
  untrusted by ADR 0002) — and the exception itself is logged server-side, so
  nothing is silently swallowed. Security exceptions keep their terminal
  classification: the catch-all sits after them, never in front.

### Multi-turn memory

LangGraph checkpointer keyed by a `thread_id` derived server-side from the
authenticated identity (user + tenant). A login switch starts a fresh thread;
conversation state can never cross tenants. Documented property: memory makes
injection persistent (a poisoned note read in turn 1 is still in context at
turn 5) — acceptable because no layer trusts the context; covered by
multi-turn adversarial evals (ADR 0004).

### Tool contracts (all: tenant by closure, scoped executor, audit-logged)

| Tool | Contract | Trust property |
|---|---|---|
| `query_db(sql)` | Generated SQL through layers 2-4; capped + truncation signal (ADR 0007). | Model output fully validated |
| `get_stats(metric, column, group_by?)` | Typed args from allowlists (`avg/sum/count/min/max`; numeric/grouping column lists); the tool builds a fixed parameterized query. | Zero generated SQL |
| `plot(kind, column, metric?, group_by?, series_by?, bins?)` | Kinds: `bar`, `line`, `grouped_bar`, `histogram`, `scatter`, `box`. **The tool fetches its own data** via the scoped executor and returns one `chart_spec` to the frontend through the trace — the values ride inside that spec, so there is no second `data` payload beside it. | Charted values are database ground truth — they never pass through the model |
| `detect_anomalies(column, group_by?)` | Tukey IQR fences (outlier beyond 1.5x IQR from the quartiles), computed within each group (default: department). Chosen over z-scores because salaries are lognormal by design (ADR 0008) and z-scores assume normality — they would flag the healthy right tail. | Deterministic statistics on scoped rows |
| `search_notes(query)` | Tenant-partitioned KNN over embedded notes (ADR 0010). | Fixed parameterized shape, pre-filtered |

### Chart kinds, and what `plot` does not send (amended after issue #70)

Six kinds, each one an allowlisted fixed template in `analytics.py` — the arguments are
names checked against an allowlist, never SQL the model writes — and each one answering a
question this dataset actually poses:

- `bar` / `line`: one metric per named dimension; `line` defaults to the `hire_year` axis.
- `grouped_bar`: the same aggregate over **two** allowlisted dimensions, so a demo can ask
  whether pay tracks rating *within* each department. The second dimension needs to be
  low-cardinality to be readable, which is why `score_band` (a fixed
  `CAST(performance_score AS INTEGER)`, the rating truncated to its whole star) joins
  `department` and `hire_year` in the one dimension allowlist.
- `scatter`: `salary` against `performance_score`. These are the schema's only two numeric
  columns, so this is its only genuine two-variable relationship; the pairing is a fixed
  map, not an argument the model chooses freely.
- `box`: each group's quartiles, with whiskers at the extreme values still inside the
  group's Tukey fences. It shares one quartile/fence computation with `detect_anomalies`
  (`_quartiles`, `_fences`), so the box plot is a picture of exactly the fences the anomaly
  tool flags against — the same statistic told twice, never computed twice.

The tool returns numbers, never rendered text: a histogram's bins travel as numeric edges
(`x_low`, `x_high`) rather than as a `"155230-174165"` label. Grouping digits for a reader is
a locale decision and belongs to the one frontend formatter (`src/lib/format.ts`, ADR 0006),
so the backend never formats and the product never grows a second formatter to drift.

### System prompt

Schema card + a few own-tenant sample rows (the assignment's "embed schema +
sample rows"); aggregation push-down and column-selection instructions
(ADR 0007); the tenant-scope instruction retained as UX guidance, explicitly
not a security layer (ADR 0002).

Three further rules, each one line, all of them UX and answer-quality guidance
rather than enforcement — nothing in the prompt is a boundary (ADR 0002), and
none of these three restates what the four RLS layers already stop. The
data-borne-instruction rule generalizes the existing note rule to every channel
that carries untrusted text (the user's turn, note text, tool output) and asks
for a plain refusal rather than a negotiation, so an "ignore your instructions"
turn produces a clean, demonstrable answer instead of a wobbly one; the layers
would refuse the resulting query either way, this only shapes what the user
reads. The no-emoji rule mirrors the repo-wide convention the model had never
been told. The markdown rule (blank line between blocks, no bold run glued to
the following sentence) keeps answers legible with no post-processing in the
renderer.

## Consequences

- The graph nodes give natural places for the audit log, the retry counter,
  and the security-event short-circuit.
- Structured tools (`get_stats`, `plot`, `detect_anomalies`) answer most demo
  questions with no generated SQL at all; `query_db` remains for free-form
  analytics — a defensible two-lane design.
- More code than the prebuilt agent; accepted for demonstrability.

## Alternatives

- **Prebuilt ReAct agent** — one line, but audit hooks become callbacks and
  the LangGraph skill signal is weak.
- **Model passes data to `plot`** — rejected: every charted number would be an
  LLM transcription, a correctness and trust regression.
- **Z-score anomalies** — rejected for skewed salaries (normality assumption);
  would need a log-transform to be defensible.

## References

- LangGraph documentation (graphs, checkpointers/persistence) —
  https://docs.langchain.com/oss/python/langgraph/overview
- NIST/SEMATECH e-Handbook of Statistical Methods, detection of outliers
  (box plot / Tukey fences, 1.5 x IQR) —
  https://www.itl.nist.gov/div898/handbook/prc/section1/prc16.htm
- OWASP LLM01 (deterministic validation of model output; treat the model as
  untrusted) — https://genai.owasp.org/llmrisk/llm01-prompt-injection/
- OWASP Error Handling Cheat Sheet (generic messages outward, detail to the log
  only) —
  https://cheatsheetseries.owasp.org/cheatsheets/Error_Handling_Cheat_Sheet.html
- ADRs 0002 (layers, audit), 0004 (evals), 0007 (result-size), 0008 (dataset
  distributions), 0010 (retrieval) in this repo
