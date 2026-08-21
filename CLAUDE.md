# CLAUDE.md — secure-rls

Authoritative resumption guide for this repo. Read this first; it tells you what
the project is, how it is shaped, what is built vs. still to build, how to run it,
where to make a given change, and the hard rules you must not break.

This repo is a take-home case study for an AI Engineer position,
defended in a 60-minute live demo call. Every line must be simple enough to
explain and every design decision has a written rationale in
[`docs/decisions/`](docs/decisions/). The assignment itself is summarized in
[`docs/requirements.md`](docs/requirements.md).

**This repo is developed by AI agents, design-first.** The work queue is the
[issue tracker](https://github.com/TomasHolas/secure-rls/issues): epics #2-#7
track the implementation issues #13-#32. How to work here:

1. Pick an open implementation issue; read this file plus the ADRs the issue
   references before writing code.
2. The issue's **binding contracts** (signatures, data shapes) are law for
   parallel work — to change one, amend the referenced ADR and the issue text
   FIRST, then the code.
3. Branch `feat/<issue>-<slug>`, small commits, PR body contains
   `closes #<issue>`. Rebase on `main` before merging — parallel PRs land
   often.
4. Keep everything in git: if it is not committed, the next agent (possibly on
   another machine) does not know it exists. Docs that a change makes stale are
   updated in the same PR.

## What this is

A **secure conversational data-analyst agent** over multi-tenant HR data
(`employees.csv`, ~1000 rows, tenants `acme` / `beta` / `gamma`) with
**row-level security enforced in depth**: the LLM answers natural-language
questions about the data but can never access another tenant's rows — not via
generated SQL, not via tool arguments, not via prompt injection.

- **Backend**: Python 3.12, FastAPI, SQLite, LangGraph agent on an Ollama
  model. The Ollama endpoint is config (`OLLAMA_BASE_URL`) — in this setup a
  stronger machine on the same tailnet, `localhost` by default. All data access
  goes through one tenant-scoped executor.
- **Frontend**: React SPA on the knowledgebase design system (third sibling
  product after knowledgebase and modelbench — same tokens, fonts, logo, bricks).
- **Security model**: four independent RLS layers, each sufficient alone
  (ADR 0002). `tenant_id` comes only from the verified JWT — never from the
  LLM, never from the request body.

## Core design

Four ideas drive everything (each has an ADR in `docs/decisions/`):

1. **React + FastAPI split** (ADR 0001). The SPA is a pure HTTP client of the
   REST API; all logic lives server-side. Ports follow the sibling scheme:
   backend `:8002`, frontend `:3002` (KB owns 8000/3000, modelbench 8001/3001).
2. **Defense-in-depth RLS** (ADR 0002 as amended). Identity layer (JWT claim),
   SQL validation layer (sqlglot allowlist), engine authorizer (SQLite
   `set_authorizer`), scoped-execution layer (query rewrite + bound tenant
   parameter + read-only connection), egress layer (post-execution row check) —
   plus query timeouts, a result-row cap with truncation signaling (ADR 0007),
   and a persistent audit log. Prompt instructions exist but are UX guidance,
   not a security boundary.
3. **SQLite with emulated RLS** (ADR 0003). SQLite has no native RLS; we emulate
   it in the scoped executor in `db.py` — the only module allowed to open a
   database connection.
4. **CI never needs a model** (ADR 0004). Unit and adversarial security tests run
   against the deterministic layers with a mocked LLM; the live-model eval
   harness runs locally and commits its report.
5. **We are a client of a model endpoint** (ADR 0005). Ollama runs wherever
   `OLLAMA_BASE_URL` points — here, a bigger laptop over Tailscale. The address
   lives in `.env` (never committed; `.env.example` defaults to localhost).

## Milestones (the build plan)

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done. Work is filed
as GitHub issues (one per milestone); every change lands via branch → PR → merge.

- `[x]` **M0 — Design & docs.** Architecture, ADRs, this file. (What you are reading.)
- `[x]` **M1 — Dataset + RLS core.** `scripts/generate_dataset.py` →
  `employees.csv`; `db.py` (load + tenant-scoped executor); `security.py`
  (sqlglot validator); the adversarial test suite proving isolation, red → green.
- `[ ]` **M2 — Agent.** `agent.py`: explicit LangGraph graph on Ollama with
  RLS-enforced tools (`query_db`, `get_stats`, `plot`, `search_notes`, bonus
  `detect_anomalies`); multi-turn memory; two-tier retry policy (ADR 0011);
  tenant-filtered RAG via `rag.py` (ADR 0010); schema card + sample rows in
  the prompt; empirical model pick (ADR 0005).
- `[~]` **M3 — REST API + auth.** `app.py` (thin handlers: `/login`, `/chat` as
  an SSE stream of typed trace events, `/conversations` JWT-scoped CRUD,
  `/health` — ADR 0012), `auth.py` (hardcoded tenant users, JWT with tenant
  claim, ADR 0009).
- `[~]` **M4 — Frontend.** React SPA on the KB design system: login, streaming
  chat with live trace (generated vs executed SQL side by side), conversation
  history sidebar, tenant badge, charts, transparent security-refusal and
  truncation states, cross-tenant isolation demo via login switch (ADR 0012).
- `[ ]` **M5 — Evaluation harness.** `evals/`: ~25 correctness questions vs
  pandas ground truth (1% tolerance) + ~15 single-turn and ~5 multi-turn
  adversarial cases + retrieval/poisoned-notes attacks; committed scored
  report (ADR 0004 as amended).
- `[ ]` **M6 — CI/CD + README.** GitHub Actions: CI (ruff, pytest, dataset
  regen diff, frontend build, mocked eval dry run, compose build) + CD (images
  to GHCR on main; `docker compose up` as the deployment — ADR 0013); README
  with architecture, setup, tenant creds, challenges, time spent.

## How to run

> Target commands; each arrives with its milestone. Prerequisites: `uv`, Node 20+,
> and a reachable Ollama endpoint (M2+ only — not needed for tests or CI):
> `cp .env.example .env` and set `OLLAMA_BASE_URL` (Tailscale machine or localhost).

```bash
# Backend dev (M3+):
cd apps/backend && uv sync && uv run uvicorn app:app --reload --port 8002

# Frontend dev (M4+, talks to VITE_API_URL, default http://localhost:8002):
cd apps/frontend && npm install && npm run dev   # http://localhost:3002

# Tests (M1+; network-free, key-free, no Ollama — mocked LLM only):
cd apps/backend && uv run pytest -q
cd apps/frontend && npm test      # vitest + jsdom, brick-level rendering tests

# Eval harness (M5+; needs a live Ollama model):
cd apps/backend && uv run python -m evals

# Dataset regeneration (M1+; deterministic, seeded):
cd apps/backend && uv run python scripts/generate_dataset.py
```

## Repo layout / where to make which change

| Task | File(s) |
|---|---|
| Assignment-required deliverables | `apps/backend/app.py`, `db.py`, `agent.py`, `employees.csv`, `requirements.txt` (exported from `pyproject.toml` via `uv export`) |
| REST endpoint | `apps/backend/app.py` — thin handler, one service call, no logic |
| Conversation registry (scoped threads, titles) | `apps/backend/conversations.py` (own app-state store `state.db`, beside the LangGraph checkpointer; access always verified against the JWT identity) |
| Auth / JWT / tenant users | `apps/backend/auth.py` |
| Data load + tenant-scoped execution | `apps/backend/db.py` — the ONLY module that opens a SQLite connection |
| SQL validation (allowlist) | `apps/backend/security.py` |
| Structured analytics (aggregates, Tukey IQR anomalies, chart data) | `apps/backend/analytics.py` — allowlisted args into fixed query templates through `db.py`; never generated SQL |
| Agent, tools, prompts, retry policy, memory | `apps/backend/agent.py` |
| Note embedding + tenant-partitioned vector search | `apps/backend/rag.py` (storage/queries via `db.py`) |
| Dataset generator | `apps/backend/scripts/generate_dataset.py` |
| Eval harness | `apps/backend/evals/` |
| Tests | `apps/backend/tests/` (pytest), `apps/frontend/src/**/*.test.tsx` (vitest) |
| Tunable knob | `apps/backend/runtime.json` (typed view in `runtime.py`) — no magic values in code |
| Frontend UI | `apps/frontend/src/` — compose the design bricks (catalogue: `src/components/README.md`); never hand-roll a table/pill/button |
| Design tokens / fonts / logo | `apps/frontend/src/styles/tokens.css` + `public/` — copied from knowledgebase, which stays the tracking source |
| CI | `.github/workflows/ci.yml` |
| A design decision | `docs/decisions/` — new ADR, linked from `docs/INDEX.md` |

## Layering

```
browser → React SPA → REST (app.py, thin) → service modules (auth, agent, db, security)
agent tools → db.py scoped executor → SQLite      (never a raw connection)
evals → the same service modules                   (no second code path)
```

## Hard rules

- **`tenant_id` never comes from the LLM or the client.** It is read from the
  verified JWT server-side and passed to tools by closure. No tool exposes a
  tenant parameter the model could fill; no endpoint accepts one in the body.
- **All data access through `db.py`'s scoped executor.** No other module —
  agent, evals, tests included — opens a SQLite connection or bypasses the
  validator + scoping + egress check (exception: `conversations.py` owns the
  separate app-state store `state.db`).
- **Never commit to `main` directly.** Every change lands via branch → commit →
  push → PR → merge (`feat/<issue>-<slug>`, `fix/<slug>`, `docs/<slug>`).
- **Everything is a lego brick — frontend and backend alike.** One concern = one
  module = one source of truth. Frontend: import the brick, never hand-roll; a
  new reusable element means creating the brick first. Backend: exactly one module
  owns SQL validation, one owns tenant scoping, one owns auth; the agent, the
  API, and the evals import those same modules, never reimplement them.
  Modifying a brick updates every consumer because there are no copies to drift.
- **uv only** — never `pip`. `uv sync`, `uv run`. `requirements.txt` is a
  generated export for assignment compliance, never edited by hand.
- **Tests stay network-free and key-free.** Mocked LLM, no Ollama, no external
  network. The deterministic RLS layers are fully testable without any model.
- **No emojis** in code, docs, comments, commit messages, or any committed file.
- **No multi-line comment blocks.** A comment is one tight single line; if it
  needs more, use a `"""` docstring (Python) or `/** */` JSDoc (TS).
- **No magic values.** Every tunable lives in `runtime.json` (typed in
  `runtime.py`). Only structural-identity literals stay in code.
- **Every design decision is grounded in published best practice, with sources
  cited in its ADR** — OWASP, RFCs, vendor architecture guidance, official docs.
  Nothing asserted from memory; when no authoritative source exists, the ADR
  says so explicitly and labels the choice a modeling/engineering judgment.
- **Commits are Conventional Commits, English**, imperative subject, no trailer.
- **Docs travel with code.** Update `CLAUDE.md`, `docs/`, and ADRs in the same
  change that makes them stale.

## Engineering standards (apply on every change, not only when asked)

- **Modularity / layering.** Thin handlers, logic in service modules; one module
  = one concern; no god-files.
- **Docstrings, not comment blocks.** Every module/class/function has a `"""`
  docstring saying what it does — never why it changed.
- **No over-engineering.** No speculative abstraction, unused params, or
  flexibility nobody asked for. This code is defended line by line in a live
  call — if a line cannot be justified out loud, delete it.
- **Prod-ready error handling — nothing silently dropped.** No bare
  `except: pass`. A blocked query returns an explicit refusal, never an empty
  success; a failed egress check raises and is logged.
- **Tests cover error paths** — especially the adversarial ones: every RLS layer
  has tests proving it blocks what the layers above it missed.
- **Delete dead code + dead docs** (grep to prove no callers).
- **Review before merge.** Run `/code-review` on the PR branch before merging.
