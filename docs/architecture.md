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
                                    query_db / get_stats / plot / detect_anomalies
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

The eval harness (`evals/`) imports the same `agent.py` / `db.py` / `security.py`
modules — there is no second code path to the data (lego-brick rule).

## The four RLS defense layers (ADR 0002)

Each layer is independently sufficient; a breach requires all four to fail.

| # | Layer | Module | Mechanism | Survives |
|---|---|---|---|---|
| 1 | Identity | `auth.py` / `agent.py` | `tenant_id` read from the verified JWT server-side; tools receive it by closure — it is never an LLM-fillable argument and never accepted in a request body. | Prompt injection, malicious NL, a lying client |
| 2 | Validation | `security.py` | sqlglot parse; allowlist: single SELECT statement, `employees` table only; rejects ATTACH, PRAGMA, mutation, multi-statement, table functions. | Malicious or malformed generated SQL |
| 3 | Scoped execution | `db.py` | Every `employees` reference in the validated AST is rewritten to `(SELECT * FROM employees WHERE tenant_id = ?)` with the tenant bound as a parameter; runs on a read-only connection (`PRAGMA query_only`). | A validator bypass — the query still only sees the caller's rows |
| 4 | Egress check | `db.py` | Result rows carry `tenant_id`; any row not matching the session tenant raises and the response is refused. Fail closed. | A rewrite bug — wrong data is caught before it reaches the LLM or the user |

Prompt-level instructions ("only discuss your tenant's data") exist for answer
quality, and are explicitly NOT counted as a security layer.

## Components

| Component | Responsibility |
|---|---|
| `apps/backend/app.py` | FastAPI edge: `/login`, `/chat`, `/health`. Thin handlers, no logic. |
| `apps/backend/auth.py` | Hardcoded demo users (one+ per tenant), password check, JWT issue/verify with `tenant_id` claim. |
| `apps/backend/agent.py` | LangGraph agent: system prompt with schema card + per-tenant sample rows, tool definitions, reasoning loop, trace collection. |
| `apps/backend/security.py` | The SQL validator brick (layer 2). Pure function: SQL text in, validated AST or a typed rejection out. |
| `apps/backend/db.py` | CSV load, schema, the scoped executor (layers 3+4). The only module that opens a SQLite connection. |
| `apps/backend/evals/` | Correctness + adversarial suites over the same bricks (ADR 0004). |
| `apps/frontend/` | React SPA on the KB design system (ADR 0006): login, chat with reasoning/SQL trace, tenant badge, charts. |

## Data model

`employees.csv`, generated deterministically (seeded) by
`scripts/generate_dataset.py`, loaded into a single SQLite table:

```
employees(user_id, tenant_id, name, department, salary,
          performance_score, hire_date, notes)
```

~1000 rows across tenants `acme`, `beta`, `gamma` (uneven split), realistic
departments/salaries/notes so aggregate questions have interesting answers.

## Agent tool set

| Tool | Description | RLS enforcement |
|---|---|---|
| `query_db` | LLM-generated SQL, validated then executed. | Layers 2+3+4; SQL shown in the UI trace |
| `get_stats` | Named aggregates (avg/count/min/max by group). | Built on the scoped executor |
| `plot` | Returns chart data + spec; rendered by the SPA. | Data comes from `get_stats`/`query_db` paths only |
| `detect_anomalies` | Salary/performance outliers (bonus). | Built on the scoped executor |

Every tool receives `tenant_id` by closure at bind time. RAG is kept minimal and
honest: the schema card plus a few sample rows of the caller's own tenant are
embedded in the system prompt — no vector store, because retrieval over a
1000-row single-table dataset would be ornamental (defended in the README).

## Assignment compliance map

| Assignment requirement | Where satisfied |
|---|---|
| Public GitHub repo, README | repo root; README lands in M6 |
| Python 3.10+, open-source libs, Ollama local LLM | backend is Python 3.12; Ollama endpoint via `OLLAMA_BASE_URL` |
| Commit history showing iteration | branch → PR → merge per milestone, small commits, issue-linked |
| RLS: LLM never accesses unauthorized rows | the four layers above + adversarial tests + eval suite |
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
