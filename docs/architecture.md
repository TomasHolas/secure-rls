# Architecture

secure-rls is a conversational data-analyst agent over multi-tenant HR data.
A logged-in tenant user asks natural-language questions ("average salary in
Engineering?"); a LangGraph agent reasons, calls RLS-enforced tools, and answers
with the reasoning trace and executed SQL visible. Row-level security is the
product: no path exists from the LLM to another tenant's rows.

## System overview

```
                    login (tenant user)          chat (NL question)
browser ──────────────────┬───────────────────────────┬────────────
                          v                           v
React SPA (:3002)   POST /login                 POST /chat  (JWT in header)
                          │                           │
FastAPI (:8002)      auth.py ── issues JWT       app.py (thin handler)
                     {sub, tenant_id}                 │
                                                agent.py — LangGraph agent
                                                  │  tools bound with tenant_id
                                                  │  from the JWT, by closure
                                                  v
                         query_db / get_stats / plot / detect_anomalies / search_notes
                                                  │
                                          security.py — SQL validator (sqlglot)
                                                  │
                                          db.py — scoped executor
                                            rewrite: employees -> (SELECT * FROM
                                              employees WHERE tenant_id = ?)
                                            read-only connection
                                            egress check: every row's tenant_id
                                              == session tenant, else raise
                                                  │
                                               SQLite (employees)
                                                  
agent LLM calls ──> Ollama endpoint (OLLAMA_BASE_URL — Tailscale machine or localhost)
```

`search_notes` is the one tool that does not pass the SQL validator: it takes the
retrieval path instead — `rag.py`'s tenant-partitioned KNN, whose storage and
queries still go through `db.py` (ADR 0010).

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
agent asks most. Because 4a runs on every call, that no-op is safe: the scoping
is proven structurally, from the AST that is about to execute, rather than
assumed from the fact that the rewrite was called. The placeholder count is part
of the proof (ADR 0002, "Declared filter parameters"): a `?` the model smuggled
past layer 2 would shift which value the engine binds where, so an undeclared
placeholder fails the count and the query never runs. A template that binds its
own filter values must additionally keep them in the root WHERE clause, which
SQL renders after the FROM that carries the tenant.

Prompt-level instructions ("only discuss your tenant's data") exist for answer
quality, and are explicitly NOT counted as a security layer.

Hardening around the layers (ADR 0002 as amended): a progress-handler query
timeout and `sqlite3_limit` caps (DoS control), a hard result-row cap with an
explicit truncation signal and aggregation push-down (ADR 0007), and a
persistent audit log of every generated SQL, validation verdict, rewritten SQL,
and tenant context — which also feeds the UI trace and the eval leakage checks.

## Components

| Component | Responsibility |
|---|---|
| `apps/backend/app.py` | FastAPI edge: `/login`, `/chat` (SSE stream of typed trace events, ADR 0012), `/conversations` (JWT-scoped list/create/replay/rename/delete), `/records` and `/notes` (the browse tabs, ADR 0014), `/models` (the endpoint's chat-capable models, ADR 0012), `/health`. Thin handlers, no logic. |
| `apps/backend/auth.py` | Hardcoded demo users (one+ per tenant), password check, JWT issue/verify with `tenant_id` claim. |
| `apps/backend/agent.py` | Explicit LangGraph graph: system prompt with schema card + per-tenant sample rows, tool definitions, retry policy, multi-turn checkpointer, trace collection, transcript replay from the checkpointer. |
| `apps/backend/rag.py` | Note embedding (Ollama `/api/embed`) and tenant-partitioned vector search (ADR 0010); storage and queries go through `db.py`. |
| `apps/backend/security.py` | The SQL validator brick (layer 2). Pure function: SQL text in, validated AST or a typed rejection out. |
| `apps/backend/db.py` | CSV load, schema, the scoped executor (layers 3+4). The only module that opens a SQLite connection. |
| `apps/backend/browse.py` | The Records and Notes tabs' two fixed templates (ADR 0014): allowlisted filters bound as parameters, allowlisted sorts, paging on the ADR 0007 row cap - all through `db.py`, with the notes search delegating to `rag.py`. |
| `apps/backend/evals/` | Correctness + adversarial suites over the same bricks, run per tenant, plus the M2 model gate; `harness.py` owns the plumbing they share and `report.md` is the committed scorecard (ADR 0004). |
| `apps/frontend/` | React SPA on the KB design system (ADR 0006): login, streaming chat with live reasoning/SQL trace (the generated and executed statement side by side, tenant scoping highlighted inside the one that ran), conversation history sidebar, tenant badge, charts, transparent security-refusal and truncation states (ADR 0012), and the Chat / Records / Notes tabs that make the isolation checkable without the agent (ADR 0014). |

## Data model

`employees.csv`, generated deterministically (seeded) by
`scripts/generate_dataset.py`, loaded into a single SQLite table:

```
employees(user_id, tenant_id, name, department, salary,
          performance_score, hire_date, notes)
```

~1000 rows across tenants `acme` (~45%), `beta` (~35%), `gamma` (~20%); five
departments (Engineering, Sales, Marketing, HR, Finance) with distributions
calibrated to cited sources — BLS salary medians, documented rating-inflation
shape, BLS tenure (ADR 0008). About 1-2% of rows are deliberate second-order
prompt-injection payloads in `notes`, openly listed in `poisoned_manifest.json`
— red-team data for the eval suite and the live demo.

## Agent tool set

| Tool | Description | RLS enforcement |
|---|---|---|
| `query_db(sql)` | LLM-generated SQL, validated then executed. Results hard-capped with an explicit truncation signal (ADR 0007). | Layers 2+2.5+3+4; SQL shown in the UI trace |
| `get_stats(metric, column, group_by?)` | Typed args (metric/column/group_by from allowlists); fixed parameterized query — zero generated SQL. | Built on the scoped executor |
| `plot(kind, column, metric?, group_by?, series_by?, bins?)` | Fetches its own data via the scoped executor; returns one `chart_spec` (bar, line, grouped bar, histogram, scatter or box; `bins` is the histogram's bin count, `series_by` the grouped bar's second dimension) to the SPA — charted values never pass through the model. | Built on the scoped executor |
| `detect_anomalies(column, group_by?)` | Tukey IQR fences per group (default: department); robust to the lognormal salary shape (bonus). | Built on the scoped executor |
| `search_notes(query)` | Semantic search over embedded `notes` (ADR 0010): sqlite-vec `vec0` table with `tenant_id` as partition key — the KNN pre-filter runs before any vector comparison; neutral "no matching notes found" on empty results. | L1 closure + partition-key pre-filter + egress check |

Every tool receives `tenant_id` by closure at bind time. The agent is an
explicit LangGraph graph with multi-turn memory (checkpointer keyed by an
identity-derived `thread_id`; a login switch starts a fresh thread) and a
two-tier retry policy: security rejections are terminal, honest errors retry
up to 3 times (ADR 0011). The system prompt embeds the schema card plus a few
own-tenant sample rows; retrieval over `notes` is the RAG component
(ADR 0010).

## Assignment compliance map

| Assignment requirement | Where satisfied |
|---|---|
| Public GitHub repo, README | repo root; [README.md](../README.md) |
| Python 3.10+, open-source libs, Ollama local LLM | backend is Python 3.12; Ollama endpoint via `OLLAMA_BASE_URL` |
| Commit history showing iteration | branch → PR → merge per milestone, small commits, issue-linked |
| RLS: LLM never accesses unauthorized rows | the five layers above + adversarial tests + eval suite |
| Agentic development tools used | this repo is built with Claude Code; `CLAUDE.md`, ADR-driven waves, issue queue — demoed live |
| GitHub CI/CD pipeline | `.github/workflows/ci.yml` (M6) |
| React/Dash/Streamlit app | React SPA (ADR 0001) |
| Load CSV to SQLite; no raw SQL passthrough | `db.py`; generated SQL passes validator + rewrite, never raw |
| Agent (LangChain/LangGraph), schema + sample rows embedded | `agent.py` (LangGraph), schema card in prompt |
| Tools: Query DB / Stats / Plot / bonus Anomaly | tool set above |
| Login with hardcoded tenant users | `auth.py`; creds documented in README |
| Security demo: switch users, prove isolation | SPA login switch + adversarial eval report |
| Evaluation of model performance | `evals/` (M5) |
| Deliverables: app.py, db.py, agent.py, employees.csv, requirements.txt | all in `apps/backend/` (requirements.txt exported from pyproject via `uv export`) |
