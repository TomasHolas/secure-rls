# secure-rls

A conversational data-analyst agent over multi-tenant HR data, with **row-level
security enforced in depth**. A logged-in tenant user asks natural-language
questions; a LangGraph agent on a local Ollama model reasons, calls RLS-enforced
tools, and answers with its reasoning and the executed SQL visible. No path
exists from the LLM to another tenant's rows — not via generated SQL, not via
tool arguments, not via retrieval, not via prompt injection. Built as a take-home
case study for an AI Engineer position, design-first, entirely with agentic
tooling.

- **Backend**: Python 3.12, FastAPI, SQLite, LangGraph over Ollama (`apps/backend`)
- **Frontend**: React SPA, Vite, TypeScript (`apps/frontend`)
- **Data**: `employees.csv`, 1000 seeded rows, tenants `acme` / `beta` / `gamma`
- **Deployment**: `docker compose up`, images published to GHCR by CI

**The docs**: [quickstart](docs/quickstart.md) ·
[architecture](docs/architecture.md) · [HTTP API](docs/api.md) ·
[challenges and known limitations](docs/challenges.md) ·
[development process](docs/development-process.md) ·
[14 ADRs, indexed](docs/INDEX.md)

## Security model

`tenant_id` comes only from the verified JWT issued at login: never an
LLM-fillable tool argument, never read from a request body. On top of that, five
enforcement points with no single point of trust
([mechanisms in full](docs/architecture.md#the-five-rls-defense-layers-adr-0002)):

| # | Layer | Where | Mechanism, in one line |
|---|---|---|---|
| 1 | Identity | `auth.py`, `agent.py` | The tenant is read from the verified JWT server-side and bound into every tool by closure |
| 2 | Validation | `security.py` | sqlglot parse plus an allowlist: one `SELECT` over `employees`, and nothing else |
| 2.5 | Engine authorizer | `db.py` | SQLite `set_authorizer` re-applies that allowlist inside the engine, on a file opened `mode=ro` |
| 3 | Scoped execution | `db.py` | Every `employees` reference is rewritten to a tenant-scoped subquery, the tenant **bound** and never interpolated |
| 4 | Egress check | `db.py` | The scoping is proven structurally before the query runs, and every returned `tenant_id` is re-checked after. Fail closed |

Prompt instructions are not a layer — and `agent.prompt_guardrails` switches the
self-policing blocks off, so the model attempts the attack it would otherwise
decline and a layer refuses it by name.

## Run it

```bash
cp apps/backend/.env.example .env   # set OLLAMA_BASE_URL and JWT_SECRET
docker compose up --build           # backend :8002, frontend :3002
```

Endpoint requirements, the network-exposure warning, dev mode and every test
command: [docs/quickstart.md](docs/quickstart.md).

## Tenant credentials

Hardcoded demo users (PBKDF2, per-user salt); the plaintexts exist only here and
in the auth tests.

| Username | Password | Tenant | Rows the agent can reach |
|---|---|---|---|
| `alice@acme` | `demo-acme` | `acme` | 450 of 1000 |
| `bob@beta` | `demo-beta` | `beta` | 350 of 1000 |
| `carol@gamma` | `demo-gamma` | `gamma` | 200 of 1000 |
| `admin` | `demo-admin` | all tenants | 1000 of 1000 |

The admin login demonstrates that scope is a **signed claim, not a second code
path**: its token carries `scope: "all"`, the same five layers run on every query
it makes, and the model still cannot influence which rows it reaches — the tools
are bound to the wider data path before it is called, from the token alone
([ADR 0009](docs/decisions/0009-auth-implementation.md),
[ADR 0002](docs/decisions/0002-defense-in-depth-rls.md)).

The Records and Notes tabs show all 1000 rows to every user — what changes with
the login is what the *agent* can reach, and the two numbers on one screen are
what make the isolation checkable. The Audit tab is the third view of the same
surface: the server's own log of every statement the data path ran, its verdict,
and the statement that actually executed.

## Evaluation

Both suites, every tenant, 171 live turns per guardrail position, both reports
committed ([on](apps/backend/evals/report.md),
[off](apps/backend/evals/report-no-guardrails.md)); methodology in
[development-process.md](docs/development-process.md#tests-and-evaluation):

| | Guardrails on | Guardrails off |
|---|---|---|
| Correctness | **74/75 (98.7%)** | **75/75 (100%)** |
| Security | **75/75 (100%)** attacks held | **67/75 (89.3%)** attacks held |
| **Leaks over 171 turns** | **0** | **0** |

The off position is the run worth having: the model attempts the attacks it
would otherwise decline, and the layers still return nothing foreign — ADR
0002's central claim measured rather than asserted.

## Assignment deliverables

The five required files all live in **`apps/backend/`**: `app.py`, `db.py`,
`agent.py`, `employees.csv`, `requirements.txt` (a generated `uv export`;
`pyproject.toml` is the source of truth). Every requirement is mapped to where
it is satisfied in
[architecture.md](docs/architecture.md#assignment-compliance-map).

## Challenges

One line per wave; the full write-ups are in
[docs/challenges.md](docs/challenges.md#challenges).

- **Design**: prompt instructions are demonstrably not a boundary, so every layer is something a model cannot influence.
- **RLS core**: SQLite has no `CREATE POLICY` — RLS is emulated by AST rewrite, a pure testable function of (SQL, tenant).
- **Retrieval**: `sqlite-vec` segfaults when its shadow read is denied, so the vector index lives in its own database file.
- **Live testing**: one unanticipated tool exception killed the SSE stream — fixed as three transport invariants, not a patch.
- **Model selection**: no local fallback exists; the pick was measured over the tailnet, and the gate now verifies both required models.

## Known limitations

The ones that would matter first in production; the full list with reasoning:
[docs/challenges.md](docs/challenges.md#known-limitations).

- **Prompt rules are UX guidance, not enforcement** — every security claim is independent of them.
- **Groundedness is a nudge, not a proof** — a mis-transcribed number is not caught.
- **The sliding session has no absolute cap** and there is no revocation.
- **Not built for scale**: one SQLite file, one process, no rate limiting. PostgreSQL with native `CREATE POLICY` is the production evolution.

## Time spent

~20 h wall-clock across three calendar days (2026-08-21 to 08-24, derived from
the commit and PR history), as parallel agent work under review:

| Phase | What landed | Time |
|---|---|---|
| M0 | Design: architecture, the first 13 ADRs, the issue queue, CLAUDE.md | ~1 h |
| M1-M4 | Dataset + RLS core + analytics, agent + RAG, REST API + auth, React frontend | ~2.5 h |
| M2 gate, M6 | Model shootout and gate runs, Docker + compose + GHCR, first bug wave | ~3 h |
| Polish | Chart kinds, trace transparency, per-turn bounds, replay persistence, titles | ~1.5 h |
| M5 | Evaluation harness, the first live 171-turn run, trace rework | ~2 h |
| Security proof | The corrected layer claim, switchable guardrails, the live off-position run (zero leaks) | ~3 h |
| Durability | Full-turn history and replay, state on a named volume, history trimming | ~3 h |
| Owner test wave | Full-dataset control group, sidebar/loader/SQL-card/chips/auto-apply UI, retitling, location restore, docs restructure | ~3.5 h |
| **Total** | | **~20 h** |

Built with AI agents (Claude Code), design-first: 14 ADRs written before the
code they govern, an issue queue with binding contracts, one branch and PR per
issue, CI gating every merge. The method and its evidence:
[docs/development-process.md](docs/development-process.md).
