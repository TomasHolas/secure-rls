# Challenges and known limitations

What was actually hard, per wave, and what this build does not do. Both are
recorded here in full; the [README](../README.md) carries the short form.

## Challenges

Per wave, from what the ADRs and the issue history actually record.

**Design (M0).** The interesting problem was not "add a WHERE clause" but
deciding what counts as a security boundary. Prompt instructions were the
tempting answer and are demonstrably not one — an 89% attack success rate on
GPT-4o for persistent attackers is the number OWASP cites. The layers had to be
things a model cannot influence, which is why the tenant is a closure rather
than a parameter, and why the egress check exists at all: it is the layer that
catches our own bugs.

**RLS core (M1).** SQLite has no native row-level security — there is no
`CREATE POLICY`, so RLS had to be *emulated* and made impossible to route
around. The choice was per-tenant views versus rewriting the query. AST rewrite
won: one schema for the model to prompt against, no per-tenant DDL, and
enforcement that is a pure testable function of (SQL, tenant). Two things only
showed up in the writing: an aggregate-only result has no `tenant_id` column for
the egress check to inspect, so a *structural* check had to prove the rewrite
applied before execution rather than after; and a placeholder written by the
model would shift which value the engine binds where, so bare parameters are
rejected outright ([#45](https://github.com/TomasHolas/secure-rls/issues/45)).

**Retrieval (M2).** `sqlite-vec` 0.1.9 does not check the result of preparing its
own `_rowids` shadow statement, so an authorizer that **denies** that read
segfaults the process — exit 139, not an exception. Discovered by mapping the
authorizer callbacks a `vec0` KNN read actually makes. Rather than allowlisting
shadow tables on the serving connection, the vector index moved into its own
`vectors.db` and the connection that runs model-generated SQL caps attached
databases at zero. The crash is now unreachable from any generated query by
construction rather than by filter.

**Toolchain (M2).** Retrieval worked on one machine and not another. Cause:
`sqlite-vec` must be loaded through the `sqlite3` Python API, which needs an
interpreter built with loadable-extension support, and the python.org macOS
framework build compiles it out. The choice of interpreter was silently deciding
whether the RAG path existed at all, so the project pins
`python-preference = "only-managed"` and the container uses uv's own image.

**Live testing (M4/M5).** The demo failed in a way no test had covered: a tool
raised an exception nobody anticipated, it escaped the graph, and the SSE
response died mid-flight. The UI left every announced step at "running" forever
and then, on the next turn, the model invented a confident and false explanation
for a failure it could not see. The fix was three invariants rather than one
patch — a catch-all that turns an unexpected tool failure into a retry, every
`tool_call` closed by exactly one terminal event, every stream ending in exactly
one `done` frame — plus replaying the *text* of a partial turn so the transcript
stops hiding what the graph still remembers
([#66](https://github.com/TomasHolas/secure-rls/issues/66), amending ADRs
0010-0012).

**Session expiry (M4).** The session signed users out mid-demo, and the
30-minute expiry it did that on turned out to be justified by nothing but a code
sample in a tutorial. It became a sliding session with a sourced lifetime
([#71](https://github.com/TomasHolas/secure-rls/issues/71),
[ADR 0009](decisions/0009-auth-implementation.md)) — whose own limitation is
below.

**Model selection (M2/M5).** No local fallback exists — the dev laptop cannot run
a useful model — so the endpoint is a second machine and the pick had to be
measured rather than assumed. A shootout over the tailnet found a 30B MoE model
2.6x faster per ask at the median than a 27B dense one at comparable quality.
Two faster candidates were excluded for cause: `gpt-oss` has open tool-calling
bugs in exactly this LangChain/Ollama stack, and `qwen3-next:80b-a3b` has
documented llama.cpp MoE inefficiency with no verified Apple Silicon benchmark.
The gate also caught that the endpoint served four chat models and no embedding
model, which is why the pre-call health check verifies **two** models rather than
one.

## Known limitations

- **The sliding session is an idle timeout with no absolute cap.** A
  continuously used session renews indefinitely. A stateless token cannot be
  capped without a server-side record of when the session began; the stateless
  fix is a first-issued-at claim the refresh refuses to extend past, noted in
  [ADR 0009](decisions/0009-auth-implementation.md) rather than built.
  There is no revocation.
- **Prompt rules are UX guidance, not enforcement.** The injection-refusal, tenant
  scope and no-emoji rules shape what the user reads. They stop nothing. Every
  security claim in this repo is independent of them.
- **Demo credentials are in the README on purpose.** Hardcoded users are what
  the assignment asked for. Demo identities are not secrets; the signing key is,
  and it has no committed default.
- **Passwords use PBKDF2, not Argon2id.** OWASP's first choice is Argon2id; PBKDF2
  at 600,000 iterations is also sanctioned and was chosen to stay stdlib-only.
  Argon2id is a one-dependency upgrade.
- **Trace detail is deliberately visible to the authenticated tenant** — which
  layer fired, and why. This reveals that defenses exist; it never reveals
  another tenant's data. Recorded as a product judgment in
  [ADR 0012](decisions/0012-api-and-chat-ux.md), against OWASP's
  generic-error default.
- **Replay is not the live turn, in two ways.** A replayed turn cannot show how
  long a thought took — that span is measured in the browser, not sent — and
  history is capped per turn and per thread, so a long turn replays trimmed and
  says so on a pill ([ADR 0012](decisions/0012-api-and-chat-ux.md), issue #90).
- **Groundedness is enforced by a nudge, not a proof.** The evaluation run
  caught a turn answering from conversation context without querying anything —
  nothing can leak that way, since whatever is in context is already the
  caller's own data, but a reader cannot tell a computed figure from a recalled
  one. A turn that would answer with no successful tool result now has its
  answer dropped and the model re-asked once, at the cost of one tool round, and
  the `done` frame reports whether the turn was grounded. What that does not do
  is verify that a figure in the answer *matches* the tool result it came from;
  the model could still mis-transcribe a number it did fetch. Charts are exempt
  by construction — `plot` fetches its own data, so charted values never pass
  through the model at all.
- **`sqlite-vec` is pre-v1.** The vector extension warns of breaking changes, so
  its version is pinned exactly and the tenant-isolation invariant is covered by
  tests that re-run on any bump. It also segfaults if its `_rowids` shadow read
  is denied, which is why the vector index lives in its own database file the
  model-generated-SQL connection cannot reach — see
  [ADR 0010](decisions/0010-tenant-filtered-rag.md).
- **The dataset is one table.** Real HR data spans many related tables, and
  cross-table joins would exercise the scoping rewrite harder than a single
  `employees` table does. The synthetic data is also generated, not real: its
  distributions are calibrated to cited sources
  ([ADR 0008](decisions/0008-dataset-generation.md)) rather than observed.
- **Not built for scale.** One SQLite file, one process, no rate limiting, no
  connection pooling. PostgreSQL with native `CREATE POLICY` and a dedicated
  vector store are the production evolution, noted in ADRs
  [0003](decisions/0003-sqlite-scoped-execution.md) and
  [0010](decisions/0010-tenant-filtered-rag.md).
