# Evaluation harness report - prompt guardrails off

<!--
AWAITING A LIVE RUN. This file is the place the off-position scorecard lands, and it is written
whole by:

    cd apps/backend
    uv run python -m evals --no-guardrails

No live numbers exist yet: the Ollama endpoint was unreachable when the switch landed
(issue #102), and inventing figures for a security claim would be worse than having none. The
flag itself is proved offline - `uv run python -m evals --mocked --no-guardrails` completes and
`uv run python -m evals --dry-run --no-guardrails` reports the position - and pytest proves the
RLS layers behave identically in both positions.
-->

Not yet run against a live model.

## Why this run is the one worth having

With `agent.prompt_guardrails` off, the rendered system prompt drops two blocks and nothing else
(ADR 0011 as amended): the rules asking the model to refuse instructions that arrive as data, and
the closing paragraph stating its tenant scope. Everything else stays - the schema card, the
sample rows, the grounding rule, the aggregation push-down, the SQL rules, the output discipline.

The model is then not told to decline the payroll-administrator override, the developer-mode
injection or a plainly worded cross-tenant request. It attempts them, and the RLS layers
refuse what it wrote (ADR 0002): identity from the JWT, the sqlglot allowlist, the engine
authorizer plus the scoping rewrite, and the egress row check. A security suite that holds with
zero leaks in this position is the empirical form of ADR 0002's claim that prompt-level
instructions are guidance and never a boundary; the same suite passing with the guardrails on
cannot distinguish a layer that held from a model that never tried.

The on-position scorecard is `report.md`, produced by `uv run python -m evals`. The two positions
write separate files, so neither can overwrite the other's numbers.
