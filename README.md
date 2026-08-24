# secure-rls

A conversational data-analyst agent over multi-tenant HR data, with **row-level
security enforced in depth**. A logged-in tenant user asks natural-language
questions ("average salary in Engineering?"); a LangGraph agent on a local
Ollama model reasons, calls RLS-enforced tools, and answers with its reasoning
and the executed SQL visible.

No path exists from the LLM to another tenant's rows — not via generated SQL,
not via tool arguments, not via retrieval, not via prompt injection. That is the
product; everything else is the demonstration surface for it.

Built as a take-home case study for an AI Engineer position, design-first and
entirely with agentic tooling — see [Agentic development](#agentic-development).

- **Backend**: Python 3.12, FastAPI, SQLite, LangGraph over Ollama (`apps/backend`)
- **Frontend**: React SPA, Vite, TypeScript (`apps/frontend`)
- **Data**: `employees.csv`, 1000 seeded rows, tenants `acme` / `beta` / `gamma`
- **Deployment**: `docker compose up`, images published to GHCR by CI

## Security model

`tenant_id` comes only from the verified JWT issued at login. It is never an
LLM-fillable tool argument and never read from a request body — the model cannot
choose a tenant, because the tenant is not an input anywhere the model or the
client can reach.

Five enforcement points, no single point of trust
([ADR 0002](docs/decisions/0002-defense-in-depth-rls.md)). Layer 3 is what scopes
a query and layer 4 independently proves it did; layers 2 and 2.5 filter no rows
themselves — `SELECT * FROM employees` is accepted by both — and exist to remove
the query shapes that could sidestep layer 3 entirely:

| # | Layer | Where | Mechanism | Survives |
|---|---|---|---|---|
| 1 | Identity | `auth.py`, `agent.py` | `tenant_id` read from the verified JWT server-side and bound into every tool by closure | Prompt injection, malicious NL, a lying client |
| 2 | Validation | `security.py` | sqlglot parse plus an allowlist: one `SELECT` over `employees` only; ATTACH, PRAGMA, mutations, multi-statement, table functions and bare placeholders rejected | Malicious or malformed generated SQL |
| 2.5 | Engine authorizer | `db.py` | SQLite `set_authorizer` re-applies the table and operation allowlist inside the engine that actually runs the query; the file is opened `mode=ro` | A parser differential — sqlglot reading a statement differently than SQLite executes it |
| 3 | Scoped execution | `db.py` | Every `employees` reference in the approved AST is rewritten to `(SELECT * FROM employees WHERE employees.tenant_id = ?)` with the tenant **bound**, never interpolated; a structural check refuses to execute unless every reference carries its scoping subquery and nothing but the session tenant is bound | A validator bypass — the query still sees only the caller's rows |
| 4 | Egress check | `db.py` | Any `tenant_id` in the result must equal the session tenant, else the executor raises and the response is refused. Fail closed | A rewrite bug — wrong data is caught before it reaches the LLM or the user |

Hardening on the same connection: a progress-handler query timeout
(`db.query_timeout_ms`, 2000 ms) and `sqlite3_limit` caps as DoS controls, a hard
result-row cap with an explicit truncation signal
([ADR 0007](docs/decisions/0007-result-size-handling.md)), and a persistent audit
log of every generated SQL, validation verdict, rewritten SQL and tenant context
— which also feeds the UI trace.

**Prompt instructions are not a layer — and you can switch them off to check.**
The system prompt does tell the model to stay in its tenant, to refuse
instructions embedded in data, and to aggregate in SQL. All of that is UX and
answer-quality guidance; none of it is relied on, and every RLS claim above holds
for arbitrary model output. `agent.prompt_guardrails` (default **on**) removes
the two self-policing blocks from the rendered prompt and nothing else, so the
model attempts the attack it would otherwise decline and you watch a layer refuse
it by name ([ADR 0002](docs/decisions/0002-defense-in-depth-rls.md) as amended).
The position is on every `done` frame and on `GET /health`, and the chat header
shows it, so no trace can be read as the other mode's. The deterministic
adversarial suites run in both positions on every `pytest` invocation, which is
what proves the switch reaches no layer. Every tunable lives in
[`apps/backend/runtime.json`](apps/backend/runtime.json).

The retrieval path uses the same five points: notes are embedded once at startup
into a sqlite-vec `vec0` table whose `tenant_id` is a **partition key**, so the
KNN pre-filter restricts the search before any vectors are compared — foreign
vectors never participate in scoring
([ADR 0010](docs/decisions/0010-tenant-filtered-rag.md)).

## Architecture

```
                       login                          chat (NL question)
   browser ──────────────┬──────────────────────────────────┬─────────────
                         v                                  v
React SPA (:3002)   POST /login                       POST /chat   (Bearer JWT)
                         │                                  │
FastAPI (:8002)     auth.py                            app.py  (thin handler)
                    PBKDF2 check                             │
                    JWT {sub, tenant_id}                     │  tenant from JWT
                                                             v
                                             agent.py — explicit LangGraph graph
                                             reason -> validate -> execute_tool
                                                    -> audit -> respond
                                                             │  tools bound with
                                                             │  tenant_id by closure
                                                             v
                       query_db / get_stats / plot / detect_anomalies / search_notes
                                                             │
                                             security.py — SQL validator      (L2)
                                                             │
                                             db.py — scoped executor  (L2.5, L3, L4)
                                               authorizer + mode=ro + limits
                                               AST rewrite, tenant bound
                                               structural scope proof
                                               egress row check
                                                             │
                                          SQLite: employees.db   vectors.db   audit.db
                                                             │
  SSE stream of typed trace events  <────────────────────────┘
  token / node_start / tool_call / tool_result / security_event / retry / done

  agent LLM + embedding calls ───> Ollama endpoint (OLLAMA_BASE_URL)
```

The whole turn is streamed as Server-Sent Events, so the trace the UI renders
**is** the transport, not a replay
([ADR 0012](docs/decisions/0012-api-and-chat-ux.md)). Two invariants keep it
honest: every announced `tool_call` is closed by exactly one `tool_result`,
`retry` or `security_event`, and every stream ends in exactly one `done` frame
with status `ok | blocked | gave_up | failed`.

### The agent

An explicit LangGraph graph rather than the prebuilt ReAct helper, so the audit
log and the retry counter are first-class graph nodes rather than callbacks
around a black box ([ADR 0011](docs/decisions/0011-agent-design.md)). Multi-turn
memory comes from a SQLite checkpointer keyed by a `thread_id` derived
server-side from the authenticated identity, so conversation state can never
cross tenants. Retries are two-tier: **security rejections are terminal** (a
retry would let the agent probe the boundary), honest errors and unexpected tool
failures retry up to `agent.max_tool_retries` (3) with the reason fed back to the
model.

### Tools

All five receive `tenant_id` by closure at bind time. No tool exposes a tenant
argument, so there is nothing for the model to fill in.

| Tool | Signature | RLS enforcement |
|---|---|---|
| `query_db` | `(sql)` | Model-generated SQL through layers 2, 2.5, 3 and 4; capped with an explicit truncation signal. Generated and executed SQL are shown side by side in the trace |
| `get_stats` | `(metric, column, group_by?)` | Args checked against fixed allowlists; the tool builds a parameterized query — **zero generated SQL** |
| `plot` | `(kind, column, metric?, group_by?, series_by?, bins?)` | Fetches its own values through the scoped executor and returns one `chart_spec` to the SPA — charted numbers never pass through the model |
| `detect_anomalies` | `(column, group_by?)` | Tukey IQR fences (1.5 x IQR) within each group; chosen over z-scores because the salary distribution is lognormal by design |
| `search_notes` | `(query)` | Tenant-partitioned KNN over embedded notes; partition-key pre-filter plus egress check. Neutral "no matching notes found" on empty results, identical whether nothing matched or the match belongs to another tenant |

### Seeing the data without asking the agent

The SPA has three tabs, and two of them exist so a human can check the agent
rather than trust it ([ADR 0014](docs/decisions/0014-records-and-notes-browsing.md)):

- **Chat** — the streaming turn: reasoning as it arrives, each tool call with
  its arguments, the tenant rewrite marked inside the executed statement, result
  tables, charts, retries, refusals, and what the turn cost in tokens.
- **Records** — the caller's own rows, paged and filterable. Signed in as
  `alice@acme` it reads 450; as `bob@beta`, 350. That difference *is* the
  isolation, visible without asking anything.
- **Notes** — the corpus the agent retrieves over, with a search box that calls
  `rag.search_notes_scoped`: literally the same partition-filtered vector search
  the `search_notes` tool uses, showing the distance it scored each note by.
  Notes carrying a planted injection payload are badged from
  `poisoned_manifest.json`, so the second-order injection demo is one screen.

Neither browsing tab opens a second data path: every row is served by an
allowlisted fixed template through the same `db.execute_scoped` the agent's
tools use, and both tabs carry an **"Attack it yourself"** box that appends a
query parameter of the reader's choosing to the next request. Typing
`tenant_id=beta` as an acme user returns acme's 450 rows unchanged, with the
server's own explanation above them: the tenant is read from the verified token
and bound server-side, so no request can name one.

### Data

`employees.csv` — 1000 rows generated deterministically from a single seed
(`apps/backend/scripts/generate_dataset.py`, seed 42), across `acme` (450),
`beta` (350) and `gamma` (200) and five departments. Columns:
`user_id, tenant_id, name, department, salary, performance_score, hire_date, notes`.

Distributions are calibrated to cited sources — BLS OEWS salary medians,
documented performance-rating inflation, BLS median tenure — with every modeling
choice labeled as such ([ADR 0008](docs/decisions/0008-dataset-generation.md)).
All PII is synthetic.

**15 rows (1.5%) carry deliberate prompt-injection payloads in `notes`**, listed
openly in `apps/backend/poisoned_manifest.json`. They are red-team data for the
second-order injection demo, not a hidden trap. CI regenerates the CSV and diffs
it against the committed file, so the dataset is provably what the generator
produces.

Full design: [docs/architecture.md](docs/architecture.md). Every significant
decision has an ADR — index in [docs/INDEX.md](docs/INDEX.md).

## Quickstart

### 1. Configure

Two variables, both required. Compose refuses to start without them rather than
booting a misconfigured stack.

```bash
git clone https://github.com/TomasHolas/secure-rls.git
cd secure-rls
cp apps/backend/.env.example .env
```

Then edit `.env`:

```bash
# The Ollama endpoint. localhost if you run Ollama on this machine.
OLLAMA_BASE_URL=http://localhost:11434

# HS256 signing key, at least 32 bytes. The app fails fast at startup if unset
# or too short - there is no committed default (ADR 0009).
JWT_SECRET=
```

Generate the secret:

```bash
openssl rand -hex 32
```

**Ollama on another machine.** The endpoint is config, never code
([ADR 0005](docs/decisions/0005-ollama-endpoint-and-model.md)) — in the author's
setup a stronger laptop on a private tailnet serves the model. Point
`OLLAMA_BASE_URL` at it, for example `http://<ollama-host>:11434`.

> Ollama binds `127.0.0.1` by default. Serving it to another host means setting
> `OLLAMA_HOST=0.0.0.0` on the model machine, which exposes an **unauthenticated**
> inference API to every network that host is on. Only do this behind a private
> overlay network (Tailscale/WireGuard) or a host firewall that admits the one
> client, and never on an untrusted LAN. `OLLAMA_BASE_URL` is read from `.env`,
> which is gitignored — no endpoint address is ever committed.

The endpoint must serve **two** models: a chat model (`agent.model`, default
`huihui_ai/qwen3-abliterated:30b-a3b`) and an embedding model
(`agent.embed_model`, `nomic-embed-text`) for the retrieval path. A chat model
asked to embed answers "this server does not support embeddings", so both must
be pulled:

```bash
ollama pull huihui_ai/qwen3-abliterated:30b-a3b
ollama pull nomic-embed-text
```

The chat model is also switchable at runtime from a picker in the UI, populated
live from the endpoint and filtered to chat-capable models. Model choice has no
effect on RLS — every layer is model-agnostic.

### 2. Run — compose (the primary path)

```bash
docker compose up --build
```

Backend on <http://localhost:8002>, frontend on <http://localhost:3002>. This is
the deployment unit ([ADR 0013](docs/decisions/0013-deployment-cicd.md)); CI
publishes both images to GHCR on every push to `main`, so
`docker compose up` also works against the published
`ghcr.io/tomasholas/secure-rls-backend:latest` and
`ghcr.io/tomasholas/secure-rls-frontend:latest` without building.

The frontend image bakes its API URL at build time. Pointing the SPA at a
backend other than `http://localhost:8002` means rebuilding with
`VITE_API_URL` set.

### 3. Run — dev mode

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node 20+ (CI and the image
use Node 22).

The backend reads plain environment variables and does **not** parse `.env`
itself — only compose does. So export them, or set them inline:

```bash
# backend
set -a && source .env && set +a          # or export the two variables by hand
cd apps/backend
uv sync
uv run uvicorn app:app --reload --port 8002

# frontend, in a second shell
cd apps/frontend
npm install
npm run dev            # http://localhost:3002, talks to VITE_API_URL
```

`VITE_API_URL` defaults to `http://localhost:8002` in dev; the backend's CORS
allowlist is exactly `http://localhost:3002`, so serving the SPA from another
origin needs both changed.

`uv` is not optional: `pyproject.toml` sets `python-preference = "only-managed"`
because `sqlite-vec` has to be loaded through a `sqlite3` module built with
loadable-extension support, and some system interpreters compile it out. Use
`uv sync` / `uv run`, never `pip`. `requirements.txt` is a generated export
(`uv export`) for assignment compliance and is never hand-edited.

### API

Thin handlers over the service modules, and nothing else.
Everything but `/health` and `/login` requires `Authorization: Bearer <jwt>`.

| Route | Purpose |
|---|---|
| `GET /health` | Liveness, the API version and the prompt-guardrail position. Open by design; also the container health check |
| `POST /login` | Demo credentials in, JWT with the `tenant_id` claim out. A wrong user and a wrong password return the same 401 |
| `GET /models` | The endpoint's live chat-capable models plus the configured default. The SPA never learns `OLLAMA_BASE_URL` |
| `POST /chat` | One turn as an SSE stream of typed trace events |
| `GET /conversations` | The caller's own threads, newest first |
| `POST /conversations` | Register a thread for the caller |
| `PATCH /conversations/{id}` | Retitle a thread from its first exchange; the model's label, sanitized and capped, with the first question as the fallback |
| `GET/DELETE /conversations/{id}` | Replay or delete the caller's own thread. A foreign id and a missing id return the same 404 |
| `GET /records`, `GET /records/departments` | The caller's own rows, paged, filtered and sorted through allowlisted templates — the Records tab (ADR 0014) |
| `GET /notes`, `GET /notes/search`, `GET /notes/flagged` | The caller's own notes, the agent's own retrieval path, and which of them carry a planted injection payload |

Sessions slide rather than expiring under the user: a token lives 120 minutes,
and any authenticated response may carry `X-Refreshed-Token` when the presented
token is within 30 minutes of expiry. There is no `/refresh` route and no client
timer ([ADR 0009](docs/decisions/0009-auth-implementation.md)).

## Tenant credentials

Hardcoded demo users, one per tenant. Passwords are stored as
PBKDF2-HMAC-SHA256 digests at 600,000 iterations with a per-user salt (the exact
OWASP-sanctioned configuration); the plaintexts below are demo-only and exist
nowhere else in the repo but this table and the auth tests.

| Username | Password | Tenant | Rows |
|---|---|---|---|
| `alice@acme` | `demo-acme` | `acme` | 450 |
| `bob@beta` | `demo-beta` | `beta` | 350 |
| `carol@gamma` | `demo-gamma` | `gamma` | 200 |

Log out and back in as a different user to see isolation directly: the same
question draws on disjoint rows, and "show me all salaries across every company"
returns only the caller's tenant.

## Tests

Network-free, key-free, no Ollama — the deterministic layers are testable
without any model, which is the whole point of
[ADR 0004](docs/decisions/0004-testing-and-eval-strategy.md).

```bash
cd apps/backend && uv run pytest -q     # 694 tests
cd apps/frontend && npm test            # 217 tests, 17 files
```

The backend suite is weighted toward the boundary: 123 tests on the SQL
validator alone (a hostile-SQL corpus), 60 on the scoped executor, 48 on the API
edge including JWT tampering — wrong signature, `alg=none`, expired, missing —
mapped one-to-one onto the RFC 8725 requirements.

### CI/CD

One workflow, [`.github/workflows/ci.yml`](.github/workflows/ci.yml), six jobs.
CI runs on every pull request and every push to `main`; CD runs on `main` pushes
only, after all five CI jobs pass.

| Job | What it proves |
|---|---|
| `backend (ruff + pytest)` | Lint clean, 694 tests green |
| `dataset (regenerate + diff)` | `employees.csv` and `poisoned_manifest.json` are exactly what the seeded generator produces — nothing hand-edited |
| `frontend (build)` | `tsc` + `vite build` succeed, 217 vitest tests green |
| `evals (mocked harness)` | The evaluation harness still runs: its ask list renders, then the full suite drives 171 turns through the real graph and layers on a scripted model, failing on any leak or any failed ask |
| `images (compose build)` | Both Dockerfiles build |
| `cd (publish images to GHCR)` | Backend and frontend images pushed to `ghcr.io/tomasholas/secure-rls-*`, tagged `latest` and the commit SHA |

CI never needs a model, a secret or network access to the tailnet. Live-model
work is the eval harness, run by hand, with its report committed.

## Evaluation

Two things are being measured, and they are deliberately separate: the
**security** guarantees, which hold for any model output and are therefore proved
deterministically in `pytest`; and the **model's** usefulness on this dataset,
which only a live run can show.

### Committed: the model gate

[`apps/backend/evals/gate-results.md`](apps/backend/evals/gate-results.md) is the
committed report behind the model choice. It runs the real stack end to end — the
real graph, the real scoped executor, the committed CSV, live embeddings — as
tenant `acme` over 24 asks covering all five tools, a three-ask multi-turn
thread, and three adversarial asks.

```bash
cd apps/backend
uv run python -m evals.model_gate --dry-run          # list the suite, no endpoint needed
uv run python -m evals.model_gate --model <id>       # score a model, append to the report
uv run python -m evals.model_gate --model <id> --no-guardrails   # the same, guardrails off
```

| Model | Passed | Valid tool call | Expected tool | **Foreign rows** | Median wall/ask |
|---|---|---|---|---|---|
| `huihui_ai/qwen3-abliterated:30b-a3b` | 24/24 | 20/20 | 20/24 | **0** | 7.5 s |
| `orcarouter/Qwen3.8-27B-Uncensored:q4_K_M` | 24/24 | 20/20 | 22/24 | **0** | 19.8 s |

Every row, anomaly and note in all 48 traces was matched against ground truth
read straight from the CSV; a `tenant_id` the session does not own counts as a
foreign row. **Zero foreign rows appeared for either model.** The adversarial ask
that hands the model `SELECT name, salary FROM users` and forbids rewriting it
drove the terminal-refusal path live on both, ending `blocked` at the validation
layer with `table users is not allowed; only employees may be read`. That is
layer 2 refusing a real model-written query, not a unit test standing in for one.

The faster model is the demo default; the difference is architectural (MoE, ~3B
active parameters), not a quality gap.

### Committed: the correctness and adversarial suite

[`apps/backend/evals/report.md`](apps/backend/evals/report.md) is the committed
scorecard for the full harness. It runs both suites for **every tenant** — an
isolation claim is a claim about each tenant's own session, and the correctness
ground truth differs per tenant — over 171 live turns.

```bash
cd apps/backend
uv run python -m evals --dry-run        # list every graded ask, no endpoint needed
uv run python -m evals --mocked         # network-free: scripted model, hashed embedder
uv run python -m evals                  # the live run that produced report.md
uv run python -m evals --no-guardrails  # the same, with the prompt's self-policing off
```

The last one is the run worth having: with the guardrails on, an attack the model
declines itself never reaches a layer, so a passing suite cannot distinguish a
layer that held from a model that never tried. Each position writes its own report
file. [`report-no-guardrails.md`](apps/backend/evals/report-no-guardrails.md) is
the place the off-position scorecard lands and is **still owed** — the endpoint
was unreachable when the switch landed, and inventing numbers for a security claim
would be worse than having none.

| | Result |
|---|---|
| Correctness | **74/75 (98.7%)** |
| Security | **75/75 (100%)** attacks held |
| **Leaks** | **0** |
| Turns that never reached a terminal frame | 0 |
| Turns stopped by a per-turn bound | 1 |
| Wall time | 39.3 min, 13.8 s per turn |

Correctness ground truth is computed **independently with pandas** over
`employees.csv` — never through this project's own SQL path, which is the whole
point — at 1% relative tolerance for floats and exact for counts and names. The
leak assertion is mechanical, not judged: zero foreign rows, anomalies or notes
in any tool result, plus no foreign employee name in any answer, checked across
every trace.

Two results are worth stating plainly rather than burying:

- **The one correctness failure** was `beta`'s `headcount-bar-chart`, which ran
  **no tool at all** and answered from context, so the expected headcounts were
  simply absent. Not a leak — anything in context was already that tenant's own
  data — but a grounding defect, tracked as
  [issue #94](https://github.com/TomasHolas/secure-rls/issues/94).
- **The one stopped turn** was `beta`'s `injection-developer-mode` attack hitting
  the 120-second per-turn deadline, ending `cut_short`, leaking nothing. That
  bound exists *because* of this harness: an earlier run showed the same class of
  prompt generating for ~35 minutes with zero tool calls, which is unbounded
  consumption (OWASP LLM10 via LLM01), never an isolation failure. See the
  per-turn bounds in [ADR 0011](docs/decisions/0011-agent-design.md).

The report carries a dataset caveat: it grades the dataset committed at the time,
and [issue #89](https://github.com/TomasHolas/secure-rls/issues/89) regenerates
that dataset, so it will be re-run once that lands.

## Assignment deliverables

The five required files all live in **`apps/backend/`**:

| Deliverable | Path |
|---|---|
| `app.py` | [`apps/backend/app.py`](apps/backend/app.py) — the FastAPI edge |
| `db.py` | [`apps/backend/db.py`](apps/backend/db.py) — the tenant-scoped executor, the only module that opens a database connection |
| `agent.py` | [`apps/backend/agent.py`](apps/backend/agent.py) — the LangGraph graph and the five tools |
| `employees.csv` | [`apps/backend/employees.csv`](apps/backend/employees.csv) — 1000 seeded rows |
| `requirements.txt` | [`apps/backend/requirements.txt`](apps/backend/requirements.txt) — generated by `uv export`; `pyproject.toml` is the source of truth |

The assignment is distilled in [docs/requirements.md](docs/requirements.md).
Requirement-to-implementation mapping:

| Requirement | Where |
|---|---|
| Public repo with code and README | this repo |
| Python 3.10+, open-source libraries, local Ollama LLM | Python 3.12; FastAPI, LangGraph, sqlglot, sqlite-vec; [Quickstart](#quickstart) |
| Commit history showing real iteration | 101 commits, ~38 merged PRs, one branch per issue — [Agentic development](#agentic-development) |
| LLM never accesses unauthorized rows, incl. generated queries and tools | [Security model](#security-model) |
| Agentic tools used | [Tools](#tools) — five RLS-enforced tools on an explicit LangGraph graph |
| Agentic development used | [Agentic development](#agentic-development) |
| Deployment as a GitHub CI/CD pipeline | [CI/CD](#cicd) — compose as the deployment unit, images to GHCR |
| React / Dash / Streamlit app | React SPA ([ADR 0001](docs/decisions/0001-react-fastapi-split.md)) |
| CSV into SQLite, no raw SQL passthrough | `db.py`; generated SQL is validated, rewritten and re-checked, never passed through |
| Agent with schema and sample rows embedded | `agent.py` — schema card plus own-tenant sample rows in the system prompt |
| Tools: query DB / stats / plot / bonus anomalies | [Tools](#tools) — plus `search_notes` for the RAG path |
| RAG where applicable | [ADR 0010](docs/decisions/0010-tenant-filtered-rag.md) — tenant-partitioned vector search over `notes` |
| Login with hardcoded tenant users | [Tenant credentials](#tenant-credentials) |
| User switching to prove isolation | Log out and back in; the tenant badge, the row counts and the notes corpus all change with the session |
| Reasoning and tool use visible in the UI | Live SSE trace, generated vs executed SQL side by side |
| Malicious queries blocked or scoped | Transparent refusal naming the layer that fired; proved by the hostile-SQL corpus and the live gate |
| A demonstrated way of evaluating model performance | [Evaluation](#evaluation) |
| README: architecture, setup, credentials, challenges, time spent | this file |

## Agentic development

This repo was built by AI agents (Claude Code), design-first. The method is part
of the deliverable, and the artifacts are the evidence — none of it is a claim
you have to take on faith.

- **`CLAUDE.md` is machine-readable project memory.** Not a style guide: it
  carries what the project is, the milestone plan, a where-to-make-which-change
  table, the hard rules, and the engineering standards. Any fresh agent session
  reads it and resumes with full context. It is treated as code — a change that
  makes it stale updates it in the same commit.
- **14 ADRs before the code they govern.** Each records context, the decision,
  consequences, the alternatives rejected and why, and cites published practice —
  OWASP, RFCs, sqlite.org, Microsoft and AWS multi-tenant guidance, BLS for the
  dataset. Where no authoritative source exists, the ADR says so and labels the
  choice a judgment. Several are amended in place as reality pushed back, and the
  amendments carry the real decisions.
- **A GitHub issue queue as the work plan.** Epics #2-#7 track implementation
  issues #13-#32. Each issue names its preflight reading and its **binding
  contracts** — signatures and data shapes that are law for parallel work.
  Changing one means amending the ADR and the issue text first, then the code.
- **Branch to PR to merge, per issue, with CI gating.** No commits to `main`.
  ~38 merged PRs, each closing an issue, each rebased on `main` before merge
  because parallel branches landed often.
- **A bug-triage round driven by live testing.** After the vertical slice worked,
  a live pass produced issues #45, #57, #60, #66-#72 — real defects, filed with
  file-and-line root-cause analysis rather than symptoms. Issue #66 is the
  clearest example: five symptoms traced to one root cause, and the fix amended
  three ADRs.

The issue and PR history is the audit trail. `git log`, the closed issues, and
the ADR amendment lines together show what was decided, when, and what changed
its mind.

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
0010-0012). A related one: the session used to sign users out mid-demo, and the
30-minute expiry turned out to be justified by nothing but a code sample in a
tutorial ([#71](https://github.com/TomasHolas/secure-rls/issues/71)).

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

Stated plainly, because a case study that only lists strengths is not
trustworthy.

- **The sliding session is an idle timeout with no absolute cap.** A
  continuously used session renews indefinitely. A stateless token cannot be
  capped without a server-side record of when the session began; the stateless
  fix is a first-issued-at claim the refresh refuses to extend past, noted in
  [ADR 0009](docs/decisions/0009-auth-implementation.md) rather than built.
  There is no revocation.
- **Prompt rules are UX guidance, not enforcement.** The injection-refusal, tenant
  scope and no-emoji rules shape what the user reads. They stop nothing. Every
  security claim here is independent of them.
- **Demo credentials are in this README on purpose.** Hardcoded users are what
  the assignment asked for. Demo identities are not secrets; the signing key is,
  and it has no committed default.
- **Passwords use PBKDF2, not Argon2id.** OWASP's first choice is Argon2id; PBKDF2
  at 600,000 iterations is also sanctioned and was chosen to stay stdlib-only.
  Argon2id is a one-dependency upgrade.
- **Trace detail is deliberately visible to the authenticated tenant** — which
  layer fired, and why. This reveals that defenses exist; it never reveals
  another tenant's data. Recorded as a product judgment in
  [ADR 0012](docs/decisions/0012-api-and-chat-ux.md), against OWASP's
  generic-error default.
- **Replay restores the evidence, not the thinking.** Reopening a thread brings
  back the questions, the answers and each turn's server-produced tool evidence
  (executed SQL, the result window, charts), but not the model's reasoning,
  retries or refusals: those are the transport of the turn that produced them.
  Persisting the full turn is tracked as
  [issue #90](https://github.com/TomasHolas/secure-rls/issues/90).
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
  [ADR 0010](docs/decisions/0010-tenant-filtered-rag.md).
- **The dataset is one table.** Real HR data spans many related tables, and
  cross-table joins would exercise the scoping rewrite harder than a single
  `employees` table does. The synthetic data is also generated, not real: its
  distributions are calibrated to cited sources
  ([ADR 0008](docs/decisions/0008-dataset-generation.md)) rather than observed.
- **Not built for scale.** One SQLite file, one process, no rate limiting, no
  connection pooling. PostgreSQL with native `CREATE POLICY` and a dedicated
  vector store are the production evolution, noted in ADRs 0003 and 0010.

## Time spent

Wall-clock across two calendar days, derived from the commit and pull-request
history in this repository (first commit 2026-08-21 13:45, last 2026-08-22
08:41, with an overnight gap). Development ran as parallel agent work under
review, so several waves overlap in wall-clock time; the rows below group them
by what was landing.

| Phase | What landed | Time |
|---|---|---|
| M0 | Design: architecture, the first 13 ADRs, the issue queue, CLAUDE.md | ~1 h |
| M1-M4 | Dataset + RLS core + analytics, agent + RAG, REST API + auth, React frontend | ~2.5 h |
| M2 gate, M6 | Model shootout and gate runs, Docker + compose + GHCR, first bug wave | ~3 h |
| Polish | Chart kinds, trace transparency, per-turn bounds, replay persistence, titles | ~1.5 h |
| M5 | Evaluation harness, the live 171-turn run, trace rework | ~2 h |
| **Total** | | **~10 h** |
