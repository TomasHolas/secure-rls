# secure-rls

A conversational data-analyst agent over multi-tenant HR data, with **row-level
security enforced in depth**. A logged-in tenant user asks natural-language
questions ("average salary in Engineering?"); a LangGraph agent on a local Ollama
model reasons, calls RLS-enforced tools, and answers with its reasoning and the
executed SQL visible. No path exists from the LLM to another tenant's rows — not
via generated SQL, not via tool arguments, not via retrieval, not via prompt
injection. That is the product; everything else is the demonstration surface for
it. Built as a take-home case study for an AI Engineer position, design-first,
entirely with agentic tooling.

- **Backend**: Python 3.12, FastAPI, SQLite, LangGraph over Ollama (`apps/backend`)
- **Frontend**: React SPA, Vite, TypeScript (`apps/frontend`)
- **Data**: `employees.csv`, 1000 seeded rows, tenants `acme` / `beta` / `gamma`
- **Deployment**: `docker compose up`, images published to GHCR by CI

Depth lives in `docs/`: [the assignment](docs/requirements.md) ·
[architecture](docs/architecture.md) · [HTTP API](docs/api.md) ·
[challenges and known limitations](docs/challenges.md) ·
[development process](docs/development-process.md) ·
[14 ADRs, indexed](docs/INDEX.md).

## Security model

`tenant_id` comes only from the verified JWT issued at login: never an
LLM-fillable tool argument, never read from a request body. The model cannot
choose a tenant because the tenant is not an input anywhere the model or the
client can reach. On top of that, five enforcement points with no single point of
trust — layer 3 scopes the query, layer 4 independently proves it did, and layers
2 and 2.5 filter no rows themselves but remove the query shapes that could
sidestep layer 3:

| # | Layer | Where | Mechanism, in one line |
|---|---|---|---|
| 1 | Identity | `auth.py`, `agent.py` | The tenant is read from the verified JWT server-side and bound into every tool by closure |
| 2 | Validation | `security.py` | sqlglot parse plus an allowlist: one `SELECT` over `employees`, and nothing else |
| 2.5 | Engine authorizer | `db.py` | SQLite `set_authorizer` re-applies that allowlist inside the engine, on a file opened `mode=ro` |
| 3 | Scoped execution | `db.py` | Every `employees` reference is rewritten to a tenant-scoped subquery, the tenant **bound** and never interpolated |
| 4 | Egress check | `db.py` | The scoping is proven structurally before the query runs, and every returned `tenant_id` is re-checked after. Fail closed |

Prompt instructions are not a layer — and `agent.prompt_guardrails` (default
**on**) switches the two self-policing blocks off, so the model attempts the
attack it would otherwise decline and a layer refuses it by name. The mechanisms,
the hardening around the layers and the equally scoped retrieval path in full:
[architecture.md](docs/architecture.md#the-five-rls-defense-layers-adr-0002).

## Architecture

```
browser -> React SPA (:3002)  login, streaming chat, Records and Notes tabs
        -> FastAPI (:8002)    app.py - thin handlers; tenant read from the JWT
        -> agent.py           LangGraph graph; every tool bound to that tenant
           |                  by closure
           |- query_db     -> security.py (L2) -> db.py (L2.5-L4) -> employees.db
           |- get_stats / plot / detect_anomalies
           |               -> analytics.py fixed templates -> db.py
           |- search_notes -> rag.py partitioned KNN        -> db.py -> vectors.db
        -> browse.py          Records and Notes listings: the one unscoped read
        -> conversations.py   threads and their turns, JWT-scoped (state.db)
                              (LLM and embedding calls go to OLLAMA_BASE_URL)
```

One turn is one SSE stream of typed trace events, and the server keeps those
events, so reopening a thread replays the turn through the same code
([docs/api.md](docs/api.md)). The graph, its two-tier retry policy, its memory
and the per-turn bounds are
[ADR 0011](docs/decisions/0011-agent-design.md); the tool contracts, the
components, the browse tabs as the security claim's **control group** and the
dataset are in [architecture.md](docs/architecture.md).

## Quickstart

### 1. Configure

Two variables, both required — compose refuses to start without them rather than
booting a misconfigured stack.

```bash
git clone https://github.com/TomasHolas/secure-rls.git
cd secure-rls
cp apps/backend/.env.example .env
```

```bash
# .env - the Ollama endpoint; localhost if you run Ollama on this machine.
OLLAMA_BASE_URL=http://localhost:11434

# HS256 signing key, at least 32 bytes (openssl rand -hex 32). The app fails
# fast at startup if unset or too short - no committed default (ADR 0009).
JWT_SECRET=
```

The endpoint is config, never code
([ADR 0005](docs/decisions/0005-ollama-endpoint-and-model.md)), and it must serve
**two** models — a chat model asked to embed answers "this server does not
support embeddings":

```bash
ollama pull huihui_ai/qwen3-abliterated:30b-a3b   # agent.model, the default
ollama pull nomic-embed-text                      # agent.embed_model, no fallback
```

> Ollama binds `127.0.0.1`; serving it to another host means
> `OLLAMA_HOST=0.0.0.0`, which exposes an **unauthenticated** inference API to
> every network that host is on. Do that only behind a private overlay network
> (Tailscale/WireGuard) or a host firewall admitting the one client, never on an
> untrusted LAN.

The chat model is switchable at runtime from a UI picker, and `agent.model` is
only a preference. Model choice never affects RLS: every layer is
model-agnostic.

### 2. Run — compose (the primary path)

```bash
docker compose up --build        # backend :8002, frontend :3002
```

This is the deployment unit
([ADR 0013](docs/decisions/0013-deployment-cicd.md)); CI publishes both images to
GHCR on every push to `main`, so compose also runs the published
`ghcr.io/tomasholas/secure-rls-{backend,frontend}:latest` without building (the
frontend image bakes its API URL, so a different backend means rebuilding with
`VITE_API_URL` set). Backend state — conversations and their turns, the LangGraph
memory, the audit trail, the embeddings, the loaded tenant data — lives on the
named volume `backend-state`, so a rebuild keeps it; `docker compose down -v` is
the only reset, after which the next boot reloads the CSV and re-embeds the notes.

### 3. Run — dev mode

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node 20+ (CI and the images
use Node 22). The backend reads
plain environment variables and does **not** parse `.env` itself — only compose
does:

```bash
set -a && source .env && set +a          # or export the two variables by hand
cd apps/backend && uv sync && uv run uvicorn app:app --reload --port 8002
cd apps/frontend && npm install && npm run dev    # :3002, talks to VITE_API_URL
```

The backend's CORS allowlist is exactly `http://localhost:3002`, so another
origin needs it and `VITE_API_URL` changed together. `uv` is not optional:
`sqlite-vec` must load through a `sqlite3` module built with loadable-extension
support, which some system interpreters compile out, so `pyproject.toml` pins
`python-preference = "only-managed"`. Never `pip`; `requirements.txt` is a
generated `uv export` for assignment compliance and is never hand-edited.

## Tenant credentials

Hardcoded demo users, one per tenant. Passwords are stored as PBKDF2-HMAC-SHA256
digests at 600,000 iterations with a per-user salt (the exact OWASP-sanctioned
configuration); the plaintexts below are demo-only and exist nowhere else in the
repo but this table and the auth tests.

| Username | Password | Tenant | Rows the agent can reach |
|---|---|---|---|
| `alice@acme` | `demo-acme` | `acme` | 450 of 1000 |
| `bob@beta` | `demo-beta` | `beta` | 350 of 1000 |
| `carol@gamma` | `demo-gamma` | `gamma` | 200 of 1000 |

Log out and back in as a different user to see isolation directly: the same
question draws on disjoint rows, and "show me all salaries across every company"
returns only the caller's tenant. The Records and Notes tabs show all 1000 rows
for every one of these users — what changes with the login is what the *agent*
can reach, and the two numbers on one screen are what make the difference
checkable.

## Tests and evaluation

Security and model quality are measured separately on purpose: the RLS guarantees
hold for any model output, so they are proved deterministically and without a
model, while the model's usefulness on this dataset only a live run can show.

```bash
cd apps/backend && uv run pytest -q     # the layers and the API edge, no Ollama
cd apps/frontend && npm test            # the bricks, the session, the trace fold
cd apps/backend && uv run python -m evals --mocked         # the harness, no endpoint
cd apps/backend && uv run python -m evals --no-guardrails  # live, self-policing off
```

The harness runs both suites for **every tenant** — an isolation claim is a claim
about each tenant's own session — over 171 live turns per guardrail position, and
both runs are committed ([`report.md`](apps/backend/evals/report.md),
[`report-no-guardrails.md`](apps/backend/evals/report-no-guardrails.md)):

| | Guardrails on | Guardrails off |
|---|---|---|
| Correctness | **74/75 (98.7%)** | **75/75 (100%)** |
| Security | **75/75 (100%)** attacks held | **67/75 (89.3%)** attacks held |
| **Leaks over 171 turns** | **0** | **0** |
| Turns that never reached a terminal frame | 0 | 8 |
| Turns stopped by a per-turn bound | 1 | 0 |
| Wall time | 39.3 min, 13.8 s per turn | 31.0 min, 10.9 s per turn |

The off position is the run worth having: with the guardrails on, an attack the
model declines itself never reaches a layer, and with them off the model attempts
it and the layers still return nothing foreign — zero leaks either way, which is
ADR 0002's central claim measured rather than asserted. Methodology, the graded
failures, the test totals, the model gate and the CI jobs:
[development-process.md](docs/development-process.md#tests-and-evaluation).

## Assignment deliverables

The five required files all live in **`apps/backend/`**:

| Deliverable | Path |
|---|---|
| `app.py` | [`apps/backend/app.py`](apps/backend/app.py) — the FastAPI edge |
| `db.py` | [`apps/backend/db.py`](apps/backend/db.py) — the tenant-scoped executor, the only module that opens a database connection |
| `agent.py` | [`apps/backend/agent.py`](apps/backend/agent.py) — the LangGraph graph and the five tools |
| `employees.csv` | [`apps/backend/employees.csv`](apps/backend/employees.csv) — 1000 seeded rows |
| `requirements.txt` | [`apps/backend/requirements.txt`](apps/backend/requirements.txt) — generated by `uv export`; `pyproject.toml` is the source of truth |

Every requirement is mapped to where it is satisfied in
[architecture.md](docs/architecture.md#assignment-compliance-map).

## Agentic development

This repo was built by AI agents (Claude Code), design-first: `CLAUDE.md` as
machine-readable project memory, 14 ADRs written before the code they govern, a
GitHub issue queue whose issues carry binding contracts for parallel work, and
one branch and one PR per issue with CI gating — no commit to `main`. The method
and its evidence: [docs/development-process.md](docs/development-process.md).

## Challenges

Challenge, decision, outcome — one bullet per wave; full write-ups in
[docs/challenges.md](docs/challenges.md#challenges).

- **Design (M0): what counts as a security boundary.** Prompt instructions
  demonstrably do not (OWASP cites an 89% attack success rate on GPT-4o for
  persistent attackers), so every layer had to be something a model cannot
  influence — the tenant as a closure, and an egress check whose job is to catch
  our own bugs.
- **RLS core (M1): SQLite has no `CREATE POLICY`.** RLS is emulated by AST
  rewrite rather than per-tenant views, which makes enforcement a pure testable
  function of (SQL, tenant) — and forced two extras: a structural scope proof for
  results with no `tenant_id` to check, and rejecting model-written placeholders
  ([#45](https://github.com/TomasHolas/secure-rls/issues/45)).
- **Retrieval and toolchain (M2): `sqlite-vec` segfaults when its shadow read is
  denied.** Exit 139, not an exception, so the vector index moved into its own
  `vectors.db` the generated-SQL connection cannot reach; the same wave pinned
  `python-preference = "only-managed"`, because the python.org macOS build
  compiles out the loadable-extension support retrieval needs.
- **Live testing (M4/M5): an unanticipated tool exception killed the stream.**
  The UI left every step at "running" and the next turn invented a false
  explanation for it, so the fix was three transport invariants rather than one
  patch ([#66](https://github.com/TomasHolas/secure-rls/issues/66), amending ADRs
  0010-0012).
- **Model selection (M2/M5): no local fallback exists.** The pick was measured
  over the tailnet instead — a 30B MoE model came out 2.6x faster per ask at the
  median than a 27B dense one at comparable quality — and the gate caught an
  endpoint serving four chat models and no embedding model, which is why the
  health check verifies two.

## Known limitations

The ones that would matter first in production. The full list, each with its
reasoning and the ADR that records it:
[docs/challenges.md](docs/challenges.md#known-limitations).

- **Prompt rules are UX guidance, not enforcement** — every security claim here is independent of them.
- **Groundedness is a nudge, not a proof** — a turn with no tool result is re-asked once, but a mis-transcribed number is not caught.
- **The sliding session is an idle timeout with no absolute cap**, and there is no revocation.
- **Demo credentials are in this README on purpose**; the signing key is the secret, and it has no committed default.
- **The dataset is one table**, and generated rather than observed.
- **Not built for scale**: one SQLite file, one process, no rate limiting. PostgreSQL with native `CREATE POLICY` is the production evolution.

## Time spent

Wall-clock across two calendar days, derived from the commit and pull-request
history in this repository (first commit 2026-08-21 13:45, with an overnight
gap). Development ran as parallel agent work under review, so several waves
overlap in wall-clock time; the rows group them by what was landing.

| Phase | What landed | Time |
|---|---|---|
| M0 | Design: architecture, the first 13 ADRs, the issue queue, CLAUDE.md | ~1 h |
| M1-M4 | Dataset + RLS core + analytics, agent + RAG, REST API + auth, React frontend | ~2.5 h |
| M2 gate, M6 | Model shootout and gate runs, Docker + compose + GHCR, first bug wave | ~3 h |
| Polish | Chart kinds, trace transparency, per-turn bounds, replay persistence, titles | ~1.5 h |
| M5 | Evaluation harness, the live 171-turn run, trace rework | ~2 h |
| **Total** | | **~10 h** |
