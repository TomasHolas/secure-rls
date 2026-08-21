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
| `plot(kind, column, metric?, group_by?)` | Kinds: `bar`, `line`, `histogram`. **The tool fetches its own data** via the scoped executor and returns `{chart_spec, data}` to the frontend through the trace. | Charted values are database ground truth — they never pass through the model |
| `detect_anomalies(column, group_by?)` | Tukey IQR fences (outlier beyond 1.5x IQR from the quartiles), computed within each group (default: department). Chosen over z-scores because salaries are lognormal by design (ADR 0008) and z-scores assume normality — they would flag the healthy right tail. | Deterministic statistics on scoped rows |
| `search_notes(query)` | Tenant-partitioned KNN over embedded notes (ADR 0010). | Fixed parameterized shape, pre-filtered |

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
- ADRs 0002 (layers, audit), 0004 (evals), 0007 (result-size), 0008 (dataset
  distributions), 0010 (retrieval) in this repo
