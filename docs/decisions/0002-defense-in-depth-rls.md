# ADR 0002 — Defense-in-depth RLS: layered enforcement, no single point of trust

Status: accepted (amended 2026-08-21: engine authorizer, DoS controls, audit log — sourced
hardening; amended 2026-08-24: retitled, and the "each sufficient alone" characterization
corrected; amended 2026-08-24: the prompt guardrails are switchable, so the
prompt-is-not-a-layer claim is demonstrated rather than asserted)

## Context

The core requirement: the LLM must never access unauthorized rows, even in
generated queries or tool calls. LLM output is untrusted by definition — prompt
injection, hallucinated SQL, and adversarial user input must all be assumed.
A single enforcement point (for example "the prompt tells the model to filter")
is not a security boundary; neither is any mechanism the LLM can influence.

## Decision

Five layers, no single point of trust. They are not interchangeable, and the
claim is not that any one of them suffices: layers 2 and 2.5 filter no rows at
all — `SELECT * FROM employees` is accepted by both — so a cross-tenant leak
requires layer 3 to fail *and* layer 4 to miss it, with layers 2 and 2.5 closing
the routes by which a query could sidestep layer 3 entirely. Earlier revisions of
this ADR called the layers "each sufficient alone"; that was wrong for 2 and 2.5
and is corrected here.

1. **Identity** — `tenant_id` is read from the verified JWT server-side and
   bound into the tools by closure. It is never an LLM-fillable tool argument
   and never accepted from the request body. The LLM cannot choose the tenant
   because the tenant is not an input anywhere the LLM (or client) can reach.
2. **Validation** — `security.py` parses generated SQL with sqlglot and applies
   an allowlist: exactly one SELECT statement over the `employees` table.
   ATTACH/PRAGMA/mutations/multi-statement/table functions are rejected.
   Allowlist, not blocklist: anything not explicitly permitted fails. This layer
   also holds the preconditions layer 3's rewrite depends on: no CTE may shadow
   `employees`, and generated SQL may carry no bound parameter of its own.
3. **Scoped execution** — `db.py` rewrites every `employees` reference in the
   validated AST to `(SELECT * FROM employees WHERE tenant_id = ?)` with the
   tenant bound as a parameter, and executes on a read-only connection. Even a
   validator bypass yields only the caller's rows.
4. **Egress check** — after execution, every returned row's `tenant_id` must
   match the session tenant, else the executor raises and the response is
   refused. Fail closed: this catches a hypothetical bug in layers 1-3.

Additionally, between layers 2 and 3, an **engine-level authorizer** (layer 2.5):
SQLite's `Connection.set_authorizer` enforces the table/operation allowlist inside
the engine that actually executes the query. This closes the parser-differential
gap — sqlglot's parse of a statement could in principle differ from SQLite's own;
the authorizer is on sqlite.org's checklist for running untrusted SQL, which
model-generated SQL is.

Prompt-level instructions ("only discuss your tenant") exist for answer quality
and are explicitly not counted as a layer. This stance is directly supported by
OWASP: prompt-level measures "should complement — not replace — deterministic
controls" (LLM Prompt Injection Prevention Cheat Sheet, citing an 89% attack
success rate on GPT-4o for persistent attackers), and by Microsoft: "Don't rely
on the language model to propagate tenant information."

## Demonstrating the claim (amended)

"Prompt-level instructions are not a layer" was, until now, a sentence in this
ADR. It is now a switch: `runtime.json`'s `agent.prompt_guardrails`, default on,
whose off position omits the prompt's two self-policing blocks — the rule that
note text and other data-borne instructions are never followed, and the closing
tenant-scope paragraph — and changes nothing else (ADR 0011 as amended).

Turning it off is what makes the layers observable. With the rules rendered,
the model usually declines a cross-tenant or override request itself: nothing is
attempted, no layer fires, and a passing security suite cannot distinguish a
layer that held from a model that never tried. With the rules gone the model
attempts the request and the layers act on what it actually wrote — which, per
the Decision above, is two different observations rather than one. A query that
reaches outside the allowlist (`sqlite_master`, ATTACH, PRAGMA, a second table)
is *refused*, and the security event names the layer that refused it. A query
that is perfectly valid and merely asks for someone else's rows — the plainly
worded cross-tenant request, or `SELECT * FROM employees` — is not refused at
all: layer 3 rewrites it and layer 4 checks the result, so it succeeds and
returns nothing foreign. Both are the demonstration; only the first one produces
a refusal, and a demo that promises a refusal for the second would be
misdescribing its own architecture.

Two properties make the switch admissible as evidence rather than a hole:

- **It cannot reach enforcement.** `_system_prompt` is the only reader of the
  knob; `security.py`, `db.py` and `auth.py` never name it, which is asserted by
  a test over their source. The adversarial corpora of `tests/test_security.py`
  and `tests/test_db.py` run in full in both positions, so identical refusals,
  identical rewritten SQL, identical declared-parameter counts and identical
  egress verdicts are a measured result rather than a claim.
- **It cannot hide.** Every `done` frame carries the position of the turn that
  produced it and `GET /health` reports the running position, so a trace can
  always be read back to the prompt that produced it and an off-camera prompt
  swap has nowhere to happen.

The artifact worth having is therefore the adversarial eval suite run in the off
position (`uv run python -m evals --no-guardrails`, ADR 0004 as amended): zero
leaks with the model's self-policing disabled is the strongest available form of
the one claim this section is about — that prompt-level instructions are guidance
and never a boundary. It says nothing about the individual layers being
interchangeable or separately sufficient; the Decision above is explicit that
they are not. The two positions write separate report files, and the model gate's
appended sections state their position too, because `evals/gate-results.md` is
append-only and ADR 0005's model pick cites it.

What that run is expected to exercise, and what it is not: the model is no longer
told to decline the payroll-administrator override, the developer-mode injection
or a plainly worded cross-tenant request, so it attempts them. The first two
classes reach out of the allowlist and are refused by layer 2 or 2.5, with the
event naming the layer. The third does not: it is a valid query for someone
else's rows, so layer 3 scopes it and layer 4 checks the result, and it succeeds
returning nothing foreign. A prediction of "the layers refuse it" would be wrong
for that third class, which is why this ADR states the two outcomes separately.
That run has since happened: `evals/report-no-guardrails.md` records it live on
2026-08-24 — zero leaks over 171 turns, with the eight non-held attacks all one
context-bound failure that returned nothing foreign (issue #131).

One more surface has to be watched for this to mean anything, and it was missed
on the first attempt (issue #102 review). The system prompt is not the only text
the model receives: each tool's docstring is bound as its `description` and is
sent on every turn in both positions. A copy of the note-injection rule sat in
`search_notes`, so the off position still asked the model not to follow
instructions found in note text — on the poisoned-notes attack, the very case the
off position exists to demonstrate. Tool descriptions now carry no rule the model
is asked to follow, and the off-position assertion is checked over the system
prompt and every bound tool description together.

## Hardening (amended per sourced review)

- **DoS controls**: a hostile-but-valid SELECT (e.g. giant cross joins) passes
  the allowlist; per sqlite.org/security.html the executor sets a progress-handler
  query timeout and `sqlite3_limit` caps. Timeout and limits are `runtime.json`
  tunables.
- **Audit log**: every generated SQL, validation verdict, rewritten SQL, and
  tenant context is persisted (Microsoft secure multitenant RAG guidance) —
  also the data source for the UI trace and the eval leakage checks.
- **query_only caveat, documented**: `PRAGMA query_only` is reversible by SQL
  ("not truly read-only" — sqlite.org), so the load-bearing read-only control is
  `mode=ro` at file open; layer 2's PRAGMA block is what makes `query_only`
  meaningful as a second belt.

## Declared filter parameters (amended for ADR 0014)

Layer 2 refuses a bound parameter in model-generated SQL and layer 4a counts
placeholders to prove the tenant binding applied, so originally no query could
carry a parameter of its own. A trusted template — `analytics.py`, `browse.py` —
needs to bind values a reader typed, per the OWASP parameterization rule below,
so both layers now take a **declared count**: `execute_scoped(..., params=(...))`
passes it to `validate_sql(sql, parameters=n)`, which then demands exactly `n`
anonymous `?` placeholders and refuses every named or typed style, and layer 4a
demands `len(scoping) + n` placeholders bound to the session tenant followed by
exactly those values.

`params=()` is the default and is the model's path unchanged: generated SQL
declares nothing, so any parameter in it is still refused, and a placeholder the
model smuggles past layer 2 still trips layer 4a because the count no longer
agrees.

The binding order is a property of the grammar rather than an assumption. SQL
renders a SELECT's FROM before its WHERE, so the scoping placeholders inside the
FROM subqueries bind before the caller's; layer 4a proves the arrangement it
relies on instead of trusting it, by requiring that a template which binds
anything have exactly one `employees` reference and that its placeholders sit in
the root WHERE and nowhere else — never in the projection, which would render
ahead of FROM and silently take the tenant's value.

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

## References

- OWASP Top 10 for LLM Applications 2025: LLM01 Prompt Injection, LLM02
  Sensitive Information Disclosure, LLM06 Excessive Agency —
  https://genai.owasp.org/ (texts: github.com/OWASP/www-project-top-10-for-large-language-model-applications)
- OWASP LLM Prompt Injection Prevention Cheat Sheet —
  https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html
- OWASP SQL Injection Prevention Cheat Sheet (parameterization, allowlists,
  least privilege) —
  https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html
- Microsoft, multitenant Azure OpenAI / secure multitenant RAG ("Don't rely on
  the language model to propagate tenant information"; audit logging) —
  https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/openai,
  https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/secure-multitenant-rag
- AWS, multi-tenant agents on Bedrock AgentCore (JWT tenant identity, row-level
  filtering, result sanitization) —
  https://aws.amazon.com/blogs/machine-learning/building-multi-tenant-agents-with-amazon-bedrock-agentcore/
- LangChain security policy (layered security, read-only credentials) —
  https://docs.langchain.com/oss/python/security-policy
- sqlite.org: security checklist (`set_authorizer`, limits, interrupt), URI
  `mode=ro`, `PRAGMA query_only` — https://www.sqlite.org/security.html,
  https://www.sqlite.org/uri.html, https://www.sqlite.org/pragma.html
