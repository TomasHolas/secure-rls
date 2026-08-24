# Architecture

secure-rls is a conversational data-analyst agent over multi-tenant HR data.
A logged-in tenant user asks natural-language questions ("average salary in
Engineering?"); a LangGraph agent reasons, calls RLS-enforced tools, and answers
with the reasoning trace and executed SQL visible. Row-level security is the
product: no path exists from the LLM to another tenant's rows.

## System overview

```
                       login                          chat (NL question)
   browser ──────────────┬──────────────────────────────────┬─────────────
                         v                                  v
React SPA (:3002)   POST /login                       POST /chat   (Bearer JWT)
   Chat / Records /      │                                  │
   Notes / Audit tabs    v                                  v
FastAPI (:8002)     auth.py                            app.py  (thin handler)
                    PBKDF2 check                             │  tenant from JWT
                    JWT {sub, tenant_id}                     v
                                             agent.py — explicit LangGraph graph
                                             reason -> validate -> execute_tool
                                                    -> audit -> respond
                                                             │  tools bound with
                                                             │  tenant_id by closure
          ┌──────────────────────┬───────────────────────────┴────────────┐
          v                      v                                        v
     query_db             get_stats / plot /                         search_notes
     model-written SQL    detect_anomalies                           retrieval
          │                      │                                        │
  security.py (L2)         analytics.py                                rag.py
  parse + allowlist        typed args into                       tenant-partitioned
          │                fixed templates                        KNN, partition key
          │                      │                                        │
          └──────────────────────┴─────────────┬──────────────────────────┘
                                               v
                        db.py — the only module that opens a connection
                          authorizer + mode=ro + limits            (L2.5)
                          AST rewrite, tenant bound                (L3)
                          structural scope proof + egress row check (L4)
                                               │
                        SQLite: employees.db   vectors.db   audit.db
                                               │
  SSE stream of typed trace events  <──────────┘
  token / node_start / tool_call / tool_result / security_event / retry / done

  Paths that never reach the agent:
  GET /records, /notes  ─> browse.py ─> db.execute_unscoped_browse ─> employees.db
                           the control group: every tenant's rows, by design
  GET /notes/search     ─> browse.py ─> rag.py — scoped, the agent's own path
  /conversations        ─> conversations.py + turns.py + titles.py ─> state.db
                           app state, JWT-scoped; never a tenant-data path

  agent LLM + embedding calls ───> Ollama endpoint (OLLAMA_BASE_URL)
```

The whole turn is streamed as Server-Sent Events, so the trace the UI renders
**is** the transport (ADR 0012) — and those same events are what the server
keeps, so reopening the thread replays that turn through the same code rather
than a summary of it. The frames, their two invariants and every route are in
[api.md](api.md).

`search_notes` is the one tool with no SQL for layer 2 to read: its storage and
its queries still go through `db.py`, and its scoping is the partition key
(ADR 0010).

The eval harness (`evals/`) imports the same `agent.py` / `db.py` / `security.py`
modules — there is no second code path to the data (lego-brick rule).

## The five RLS defense layers (ADR 0002)

No single point of trust — and the layers are not interchangeable. Layer 1
establishes the tenant, layer 3 enforces it, and layer 4 independently catches a
layer-3 failure, so a cross-tenant leak requires 3 to fail and 4 to miss it.
Layers 2 and 2.5 filter no rows at all (`SELECT * FROM employees` is accepted by
both); they eliminate the query shapes that could sidestep layer 3 entirely, and
layer 2 additionally holds the preconditions the layer-3 rewrite depends on — no
CTE shadowing `employees`, no bound parameter in generated SQL.

| # | Layer | Module | Mechanism | Survives |
|---|---|---|---|---|
| 1 | Identity | `auth.py` / `agent.py` | `tenant_id` read from the verified JWT server-side; tools receive it by closure — it is never an LLM-fillable argument and never accepted in a request body. | Prompt injection, malicious NL, a lying client |
| 2 | Validation | `security.py` | sqlglot parse; allowlist: single SELECT statement, `employees` table only; rejects ATTACH, PRAGMA, mutation, multi-statement, table functions. CTEs and JOINs are allowed — every table reference is scoped by layer 3 regardless of query shape. | Malicious or malformed generated SQL |
| 2.5 | Engine authorizer | `db.py` | SQLite `set_authorizer` enforces the table/operation allowlist inside the engine itself. | A parser differential — sqlglot reading a statement differently than SQLite executes it |
| 3 | Scoped execution | `db.py` | Every `employees` reference in the validated AST is rewritten to `(SELECT * FROM employees WHERE tenant_id = ?)` with the tenant bound as a parameter; runs on a read-only connection (`mode=ro` at open — the load-bearing control — plus `PRAGMA query_only`). | A validator bypass — the query still only sees the caller's rows |
| 4 | Egress check | `db.py` | Two halves. 4a `_verify_scope_applied`, before execution: the tree about to run must carry the scoping subquery for every `employees` reference, one placeholder per subquery, and nothing bound but the session tenant followed by whatever filter values a trusted template declared. 4b `_verify_rows`, after execution: any `tenant_id` in the result must equal the session tenant, else the executor raises and the response is refused. Fail closed. | A rewrite bug — wrong data is caught before it reaches the LLM or the user |

Layer 4a is what makes 4b sound. An aggregate-only result (`SELECT AVG(salary)
FROM employees`) has no `tenant_id` column, so the row check has nothing to
compare and degrades to a no-op — silently absent on exactly the queries the
agent asks most. The pre-check closes that gap: it proves the scoping from the
AST that is about to execute rather than assuming it from the fact that the
rewrite was called, and it counts the bound parameters, so SQL the model smuggled
past layer 2 cannot shift which value the engine binds where. The exact
conditions, including what a trusted template may declare, are in
[ADR 0002](decisions/0002-defense-in-depth-rls.md).

The retrieval path is scoped by the same five points: notes are embedded once at
startup into a sqlite-vec `vec0` table whose `tenant_id` is a **partition key**,
so the KNN pre-filter restricts the search before any vectors are compared and
foreign vectors never participate in scoring (ADR 0010).

Hardening around the layers (ADR 0002 as amended): a progress-handler query
timeout (`db.query_timeout_ms`, 2000 ms) and `sqlite3_limit` caps (DoS control),
a hard result-row cap with an explicit truncation signal and aggregation
push-down, plus a character cap on what one result may contribute to the model's
prompt — the reader still gets every row, the model's copy is cut at a line
boundary and told how much of it it is looking at (ADR 0007 as amended), and a
persistent audit log of every generated SQL,
validation verdict, rewritten SQL, and tenant context — which also feeds the UI
trace and the eval leakage checks.

### Prompt instructions are not a layer, and the switch proves it

The system prompt does tell the model to stay in its tenant, to refuse
instructions embedded in data, and to aggregate in SQL. All of that is UX and
answer-quality guidance; none of it is relied on, and every RLS claim above holds
for arbitrary model output. `agent.prompt_guardrails` (default **on**) removes
the two self-policing blocks from the rendered prompt and nothing else, so the
model attempts the attack it would otherwise decline and a layer refuses it by
name (ADR 0002 as amended). The position is on every `done` frame and on
`GET /health`, and the chat header shows it, so no trace can be read as the other
mode's. The deterministic adversarial suites run in both positions on every
`pytest` invocation, which is what proves the switch reaches no layer. Every
tunable lives in [`apps/backend/runtime.json`](../apps/backend/runtime.json).

## Components

One module, one concern — the lego-brick rule, grouped by the layer each brick
belongs to.

| Layer | Component | Responsibility |
|---|---|---|
| Transport | `apps/backend/app.py` | FastAPI edge: `/login`, `/chat` (SSE stream of typed trace events), `/conversations`, `/records` and `/notes`, `/models`, `/health`. Thin handlers, no logic ([api.md](api.md)). |
| Transport | `apps/backend/auth.py` | Hardcoded demo users, PBKDF2 password check, JWT issue/verify with the `tenant_id` claim (ADR 0009). |
| Orchestration | `apps/backend/agent.py` | Explicit LangGraph graph: system prompt with schema card + per-tenant sample rows, tool definitions, retry policy, per-turn bounds, trace collection, transcript replay from the checkpointer (ADR 0011). |
| Orchestration | `apps/backend/titles.py` | The model's few-word label for a thread, sanitized, with the fallback to the title it already has; called by `PATCH /conversations/{id}`, never from the stream. |
| Data access | `apps/backend/security.py` | The SQL validator brick (layer 2). Pure function: SQL text in, validated AST or a typed rejection out. |
| Data access | `apps/backend/db.py` | CSV load, schema, the scoped executor (layers 2.5-4), the audit log. The only module that opens a SQLite connection, and the owner of the one unscoped browse read. |
| Data access | `apps/backend/analytics.py` | Aggregates, Tukey IQR anomalies and chart data: allowlisted arguments into fixed query templates through `db.py`, never generated SQL. |
| Data access | `apps/backend/rag.py` | Note embedding (Ollama `/api/embed`) and tenant-partitioned vector search (ADR 0010); storage and queries go through `db.py`. |
| Data access | `apps/backend/browse.py` | The Records, Notes and Audit listings (ADR 0014): allowlisted filters bound as parameters, allowlisted sorts, paging on the ADR 0007 row cap. The listings take the unscoped read; the notes search stays scoped and delegates to `rag.py`; the audit listing pages `db.audit_window` and touches no dataset row. |
| State | `apps/backend/conversations.py` | The thread registry and its per-turn history in its own app-state store `state.db`, every access verified against the JWT identity (ADR 0012 as amended). |
| State | `apps/backend/turns.py` | What one turn's trace events become in that store, and the caps on it: the same events the stream carries, reduced rather than described a second time. |
| State | `apps/backend/paths.py` | Where every state file lives: the data directory (`SECURE_RLS_DATA_DIR`, defaulting to the backend package so dev needs no variable) and the paths inside it. In the deployment that directory is a named volume, so a rebuild keeps the conversations, the memory, the audit trail and the embeddings (ADR 0013 as amended). |
| State | `apps/backend/runtime.py` | The typed view of [`runtime.json`](../apps/backend/runtime.json), where every tunable lives so none is a literal in code. |
| Presentation | `apps/frontend/` | React SPA on the KB design system (ADR 0006): login, streaming chat with the live reasoning/SQL trace (generated and executed statement side by side, tenant scoping highlighted inside the one that ran), conversation rail, tenant badge, charts, transparent refusal and truncation states (ADR 0012), and the Chat / Records / Notes / Audit tabs (ADR 0014). |
| Verification | `apps/backend/evals/` | Correctness + adversarial suites over the same bricks, run per tenant, plus the model gate; `harness.py` owns the plumbing they share and the reports are the committed scorecards (ADR 0004). |

## Data model

`employees.csv`, generated deterministically (seeded) by
`scripts/generate_dataset.py`, loaded into a single SQLite table:

```
employees(user_id, tenant_id, name, department, salary,
          performance_score, hire_date, notes)
```

1000 rows from a single seed (42) across tenants `acme` (450), `beta` (350) and
`gamma` (200); five departments (Engineering, Sales, Marketing, HR, Finance) with
distributions calibrated to cited sources — BLS salary medians, documented
rating-inflation shape, BLS tenure (ADR 0008). All PII is synthetic. **15 rows
(1.5%)** carry deliberate second-order prompt-injection payloads in `notes`,
openly listed in `poisoned_manifest.json` — red-team data for the eval suite and
the live demo, not a hidden trap. CI regenerates the CSV and the manifest and
diffs them against the committed files, so the dataset is provably what the
generator produces.

## Agent tool set

| Tool | Description | RLS enforcement |
|---|---|---|
| `query_db(sql)` | LLM-generated SQL, validated then executed. Results hard-capped with an explicit truncation signal, and the model's copy of a wide result capped again in characters so one listing cannot fill its context window (ADR 0007 as amended). | Layers 2+2.5+3+4; SQL shown in the UI trace |
| `get_stats(metric, column, group_by?)` | Typed args (metric/column/group_by from allowlists); fixed parameterized query — zero generated SQL. | Built on the scoped executor |
| `plot(kind, column, metric?, group_by?, series_by?, bins?)` | Fetches its own data via the scoped executor; returns one `chart_spec` (bar, line, grouped bar, histogram, scatter or box; `bins` is the histogram's bin count, `series_by` the grouped bar's second dimension) to the SPA — charted values never pass through the model. | Built on the scoped executor |
| `detect_anomalies(column, group_by?)` | Tukey IQR fences (1.5 x IQR) per group, default department; chosen over z-scores because the salary distribution is lognormal by design (bonus). | Built on the scoped executor |
| `search_notes(query)` | Semantic search over embedded `notes` (ADR 0010): sqlite-vec `vec0` table with `tenant_id` as partition key — the KNN pre-filter runs before any vector comparison; neutral "no matching notes found" on empty results. | L1 closure + partition-key pre-filter + egress check |

**Every tool receives `tenant_id` by closure at bind time, and no tool exposes a
tenant argument** — there is nothing there for the model to fill in, in any
position of the prompt guardrails.

The graph itself, the two-tier retry policy (security rejections terminal,
honest failures retried), the memory keyed by a server-derived `thread_id`, the
per-turn bounds and the grounding nudge are
[ADR 0011](decisions/0011-agent-design.md); the retrieval path is
[ADR 0010](decisions/0010-tenant-filtered-rag.md).

## Browsing without the agent

The Records and Notes tabs are the **control group** for the security claim: they
show the whole dataset, so a human can check the agent rather than trust it. The
boundary runs *inside* those two tabs (ADR 0014):

- **Unscoped, deliberately** — the two listings. All 1000 rows across all three
  tenants and the whole note corpus, with `tenant_id` a bound filter of the same
  kind as `department`, poisoned notes badged from `poisoned_manifest.json`.
- **Scoped, the agent's own path** — the notes search. It calls
  `rag.search_notes_scoped`, the same partition-filtered vector search the
  `search_notes` tool uses, and shows the distance it scored each hit by.

That asymmetry is the demonstration: read beta's planted injection payload in the
list, search for its exact text as `alice@acme`, get nothing back — and the tabs
show 1000 rows while the agent answers 450, or 350 as `bob@beta`.

The unscoped read is named as such — `db.execute_unscoped_browse`, called by
nothing but the two listing templates in `browse.py`. It keeps the validator, the
engine authorizer, the read-only connection, the limit caps, the query deadline,
the row cap and the audit row; it drops only the tenant scoping, its structural
proof and the tenant egress check, because returning every tenant is the point.
Tests assert that no agent tool is closed over it and that no other module can
reach it, so the claim being defended — the *agent* cannot leave its tenant — is
untouched. Every listing response also names the query parameters it did **not**
read, so a stray parameter is reported rather than silently discarded
(`browse.ignored_params`).

The third tab of that surface is **Audit**: the audit log those reads (and every
agent tool call) already write, served newest first by `GET /audit`, all tenants'
entries, no filters — the generated SQL, the verdict, the executed statement, the
error kind and the row count. It is what makes "every read was scoped, refused or
recorded" checkable instead of asserted, and it exposes nothing new: that store
holds statements and metadata, never a result row, the route needs a token like
every other listing, and it is not a tool, so no model can reach it. `db.py`
remains the only reader of `audit.db` (`audit_window`, one bound `LIMIT`/`OFFSET`
window plus the total).

## Assignment compliance map

| Assignment requirement | Where satisfied |
|---|---|
| Public GitHub repo, README | repo root; [README.md](../README.md) |
| Python 3.10+, open-source libs, Ollama local LLM | Python 3.12; FastAPI, LangGraph, sqlglot, sqlite-vec; endpoint via `OLLAMA_BASE_URL` |
| Commit history showing iteration | one branch and one PR per issue, no commit to `main` — [development-process.md](development-process.md) |
| RLS: LLM never accesses unauthorized rows, incl. generated queries and tools | the five layers above + the adversarial suites + the eval runs |
| Agentic tools used | the five RLS-enforced tools above, on an explicit LangGraph graph |
| Agentic development tools used | built with Claude Code: `CLAUDE.md`, ADR-driven waves, an issue queue — [development-process.md](development-process.md) |
| GitHub CI/CD pipeline | `.github/workflows/ci.yml`; compose is the deployment unit and images go to GHCR (ADR 0013) |
| React/Dash/Streamlit app | React SPA (ADR 0001) |
| Load CSV to SQLite; no raw SQL passthrough | `db.py`; generated SQL is validated, rewritten and re-checked, never passed through |
| Agent (LangChain/LangGraph), schema + sample rows embedded | `agent.py` (LangGraph); schema card plus own-tenant sample rows in the system prompt |
| Tools: Query DB / Stats / Plot / bonus Anomaly | the tool set above, plus `search_notes` for the RAG path |
| RAG where applicable | tenant-partitioned vector search over `notes` (ADR 0010) |
| Login with hardcoded tenant users | `auth.py`; the credentials table lives in the [README](../README.md#tenant-credentials) |
| Security demo: switch users, prove isolation | the SPA login switch, the Records/Notes control group (ADR 0014), the adversarial eval reports |
| Reasoning and tool use visible in the UI | the live SSE trace; generated versus executed SQL side by side ([api.md](api.md)) |
| Malicious queries blocked or scoped | a transparent refusal naming the layer that fired; proved by the hostile-SQL corpus and the live gate |
| Evaluation of model performance | `evals/`: correctness and adversarial suites per tenant, plus the model gate |
| Deliverables: app.py, db.py, agent.py, employees.csv, requirements.txt | all in `apps/backend/` (requirements.txt exported from pyproject via `uv export`) |
| README: architecture, setup, credentials, challenges, time spent | [README.md](../README.md) |
