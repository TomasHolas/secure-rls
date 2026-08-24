# ADR 0004 — Testing and evaluation strategy: CI never needs a model

Status: accepted (amended 2026-08-21: suite sizes fixed; both suites run per tenant;
amended 2026-08-24: the suites are runnable with the prompt guardrails off)

## Context

The assignment requires a GitHub Actions pipeline and a demonstrated way of
evaluating model performance. The Ollama endpoint is a private machine on a
tailnet — CI cannot and should not reach it. Meanwhile the security guarantees
must be provable deterministically: they hold for ANY model output, so testing
them must not depend on what a model happens to generate.

## Decision

Three tiers, split by what they need:

1. **Unit + adversarial security tests** (`tests/`, run in CI) — network-free,
   key-free, mocked LLM. The RLS layers are deterministic functions, tested
   directly: hostile SQL against the validator, tenant rewrite correctness,
   egress-check trips, JWT tampering against the API. The adversarial cases
   are written first in M1, red, and turn green as the layers land.
2. **Eval harness — correctness** (`evals/`, run locally against the live
   model) — NL questions with ground truth computed independently via pandas;
   scored automatically (numeric tolerance); produces a committed report so
   reviewers see results without running a model.
3. **Eval harness — security** (`evals/`, live model) — adversarial prompts
   ("show all salaries", injection attempts, cross-tenant requests in NL);
   the assertion is zero cross-tenant rows in any tool result or answer,
   checked mechanically against the trace, not judged by an LLM.

CI runs tier 1 plus lint (ruff), the frontend build, and an eval-harness dry
run in mocked mode (proves the harness itself executes).

Suite sizes (amended): ~25 correctness questions spread across all five tools,
floats matched at 1% relative tolerance; ~15 single-turn adversarial prompts
(direct leakage, NL injection, SQL-in-NL); ~5 multi-turn scenarios (injection
persisting in conversation memory); plus retrieval attacks on `search_notes`
and the poisoned-notes cases from the dataset manifest (ADRs 0008, 0010). The
scored report is committed as markdown, regenerated per model.

Both suites run against **every tenant** (amended): an isolation claim is a
claim about each tenant's own session, and the correctness ground truth differs
per tenant, so grading one tenant would prove one third of what is claimed. The
run shares a single workspace - the CSV loaded once, the notes embedded once,
one compiled graph per tenant - so the extra tenants cost model time only.

Guardrail position (amended). `--no-guardrails` grades the same suites with the
prompt's self-policing rules omitted (ADR 0011 as amended), which is the run
worth having for tier 3: with the rules on, an attack the model declines itself
never reaches a layer, so a passing suite cannot tell a layer that held from a
model that never tried. Off, the model attempts the attack and the four RLS
layers are what holds - ADR 0002's central claim measured rather than asserted.
Every report headline states the position it was graded in, and each position has
its own default report file so neither can overwrite the other's numbers. Tier 1
does not take a flag: it runs its whole adversarial corpora in both positions
every time, which is what proves the switch reaches no layer.

## Consequences

- CI is fast, deterministic, and needs no secrets.
- The security claim has two levels of evidence: the deterministic suites, which
  hold in both guardrail positions by construction, and the live off-position
  eval run, which is the strongest single artifact the harness can produce.
- Model quality claims come from the committed eval report, reproducible by
  anyone with an Ollama endpoint.
- The security suite doubles as live-demo material: run it on the call.

## Alternatives

- **CI with a real model (Ollama in the runner)** — slow, flaky, and proves
  nothing about security that the deterministic tests do not already prove.
- **LLM-as-judge for security evals** — rejected: leakage is mechanically
  checkable; a judge adds nondeterminism where none is needed. (A judge may
  still be a future option for answer-quality grading — noted for the
  brainstorming section.)
