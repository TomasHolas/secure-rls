# ADR 0002 — Defense-in-depth RLS: four independent layers

Status: accepted

## Context

The core requirement: the LLM must never access unauthorized rows, even in
generated queries or tool calls. LLM output is untrusted by definition — prompt
injection, hallucinated SQL, and adversarial user input must all be assumed.
A single enforcement point (for example "the prompt tells the model to filter")
is not a security boundary; neither is any mechanism the LLM can influence.

## Decision

Four independent layers, each sufficient alone. A cross-tenant leak requires
all four to fail simultaneously.

1. **Identity** — `tenant_id` is read from the verified JWT server-side and
   bound into the tools by closure. It is never an LLM-fillable tool argument
   and never accepted from the request body. The LLM cannot choose the tenant
   because the tenant is not an input anywhere the LLM (or client) can reach.
2. **Validation** — `security.py` parses generated SQL with sqlglot and applies
   an allowlist: exactly one SELECT statement over the `employees` table.
   ATTACH/PRAGMA/mutations/multi-statement/table functions are rejected.
   Allowlist, not blocklist: anything not explicitly permitted fails.
3. **Scoped execution** — `db.py` rewrites every `employees` reference in the
   validated AST to `(SELECT * FROM employees WHERE tenant_id = ?)` with the
   tenant bound as a parameter, and executes on a read-only connection. Even a
   validator bypass yields only the caller's rows.
4. **Egress check** — after execution, every returned row's `tenant_id` must
   match the session tenant, else the executor raises and the response is
   refused. Fail closed: this catches a hypothetical bug in layers 1-3.

Prompt-level instructions ("only discuss your tenant") exist for answer quality
and are explicitly not counted as a layer.

## Consequences

- Each layer is a separately testable brick; the adversarial suite attacks each
  layer with the ones above it disabled where possible.
- Aggregate-only queries (no `tenant_id` column in output) are handled at the
  egress layer by verifying scope at the source-rewrite level — the check
  degrades to a no-op only when layer 3 provably applied.
- Slight latency cost per query (parse + rewrite + check) — irrelevant at this
  scale and a price worth stating in the demo.

## Alternatives

- **Prompt-only enforcement** — rejected: not a boundary.
- **Single enforcement point (just the rewrite)** — rejected: one bug from a
  breach; defense in depth is the point being evaluated.
- **Per-tenant database files** — strongest isolation, but hides the interesting
  engineering and scales poorly to real multi-tenant systems; noted as a demo
  talking point.
