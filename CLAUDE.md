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
- **Security model**: five RLS layers with no single point of trust — layer 3
  scopes every query, layer 4 independently proves it applied, and layers 2 and
  2.5 remove the shapes that could sidestep layer 3
  (ADR 0002). `tenant_id` comes only from the verified JWT — never from the
  LLM, never from the request body.

## Core design

Five ideas drive everything (each has an ADR in `docs/decisions/`):

1. **React + FastAPI split** (ADR 0001). The SPA is a pure HTTP client of the
   REST API; all logic lives server-side. Ports follow the sibling scheme:
   backend `:8002`, frontend `:3002` (KB owns 8000/3000, modelbench 8001/3001).
2. **Defense-in-depth RLS** (ADR 0002 as amended). Identity layer (JWT claim),
   SQL validation layer (sqlglot allowlist), engine authorizer (SQLite
   `set_authorizer`), scoped-execution layer (query rewrite + bound tenant
   parameter + read-only connection), egress layer (post-execution row check) —
   plus query timeouts, a result-row cap with truncation signaling (ADR 0007),
   and a persistent audit log. Prompt instructions exist but are UX guidance,
   not a security boundary — and `agent.prompt_guardrails` can switch the
   self-policing ones off, so the layers are watched holding on their own
   (ADR 0002 as amended).
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
- `[x]` **M2 — Agent.** `agent.py`: explicit LangGraph graph on Ollama with
  RLS-enforced tools (`query_db`, `get_stats`, `plot`, `search_notes`, bonus
  `detect_anomalies`); multi-turn memory; two-tier retry policy and the per-turn
  bounds that stop a runaway turn — generation and context caps on the model
  client, a wall-clock deadline, a tool-round cap, and the bound on what a turn
  SENDS — a thread too long for the context window drops its oldest whole turns
  and reports `history_trimmed`, while the checkpointer keeps everything
  (ADR 0011 as amended); the grounding
  nudge that keeps an answer tied to a tool call of its own turn and reports
  `grounded` when it is not (ADR 0011, answer quality rather than enforcement);
  tenant-filtered RAG via `rag.py` (ADR 0010); schema card + sample rows in
  the prompt; the switchable prompt guardrails, whose off position drops the
  two self-policing blocks so an attack is attempted and a layer refuses it
  (ADR 0011 as amended, visible on `done` and `/health`); empirical model pick
  (ADR 0005).
- `[x]` **M3 — REST API + auth.** `app.py` (thin handlers: `/login`, `/chat` as
  an SSE stream of typed trace events, `/conversations` JWT-scoped CRUD,
  `/health`, `/models` proxying the endpoint's chat-capable model list and the
  default resolved from it — ADR 0005 as amended, ADR 0012),
  `auth.py` (hardcoded tenant users, JWT with tenant claim, ADR 0009).
- `[x]` **M4 — Frontend.** React SPA on the KB design system: login, streaming
  chat with live trace (the generated and the executed SQL side by side, tenant
  scoping highlighted inside the one that ran), conversation
  history sidebar, tenant badge, charts, transparent security-refusal and
  truncation states, cross-tenant isolation demo via login switch (ADR 0012).
  Three shell tabs (ADR 0014 as rewritten): **Chat**, **Records** (the whole
  dataset — all 1000 rows, all three tenants — filtered, sorted and paged
  server-side with `tenant_id` a filter like any other, and the executed
  statement shown and labelled as unscoped) and **Notes** (the whole corpus,
  poisoned badges across every tenant, plus a search that runs the agent's own
  SCOPED retrieval path and shows the distances). The tabs are the control
  group: they show what exists, so the agent reaching only its own tenant is
  checkable rather than self-reported. Reading beta's planted payload in the
  list and then failing to retrieve it as acme is the demonstration.
- `[x]` **M5 — Evaluation harness.** `evals/`: 25 correctness questions vs
  pandas ground truth (1% tolerance) + 20 single-turn and 5 multi-turn
  adversarial cases + retrieval/poisoned-notes attacks, every suite run for
  every tenant over the API's own bounded model client; `--mocked` mode for CI;
  committed scored report at `evals/report.md` (ADR 0004 as amended).
- `[x]` **M6 — CI/CD + README.** GitHub Actions: CI (ruff, pytest, dataset
  regen diff, frontend build, mocked eval dry run, compose build) + CD (images
  to GHCR on main; `docker compose up` as the deployment — ADR 0013); README
  with architecture, setup, tenant creds, challenges, time spent.

## How to run

> Target commands; each arrives with its milestone. Prerequisites: `uv`, Node 20+,
> and a reachable Ollama endpoint (M2+ only — not needed for tests or CI):
> `cp .env.example .env`, set `OLLAMA_BASE_URL` (Tailscale machine or localhost)
> and set `JWT_SECRET` (`openssl rand -hex 32`) — the API refuses to boot without
> it (ADR 0009).

```bash
# The deployment (M6b+, ADR 0013) - needs a repo-root .env with both variables set:
cp apps/backend/.env.example .env    # then fill both; compose refuses to run otherwise
docker compose up --build            # backend :8002, frontend :3002

# The backend's state lives on the named volume backend-state, mounted at the image's data
# directory (ADR 0013 as amended): conversations and their turn history, the LangGraph memory,
# the audit trail and the note embeddings. So a rebuild keeps them:
docker compose down && docker compose up --build -d   # same data, new code
docker compose down -v                                # DESTRUCTIVE: wipes the whole volume,
                                                      # next boot reloads the CSV and re-embeds

# Backend dev (M3+). The first start builds employees.db from the committed CSV and the note
# index from those notes (ADR 0003 as amended, ADR 0010); later starts find both and are instant.
# No new variable: the data directory defaults to apps/backend itself (SECURE_RLS_DATA_DIR).
cd apps/backend && uv sync && uv run uvicorn app:app --reload --port 8002

# Frontend dev (M4+, talks to VITE_API_URL, default http://localhost:8002):
cd apps/frontend && npm install && npm run dev   # http://localhost:3002

# Tests (M1+; network-free, key-free, no Ollama — mocked LLM only):
cd apps/backend && uv run pytest -q
cd apps/frontend && npm test      # vitest + jsdom: bricks, session, HTTP client

# Eval harness (M5+; live needs an Ollama model, --mocked and --dry-run need nothing):
cd apps/backend && uv run python -m evals            # writes evals/report.md
cd apps/backend && uv run python -m evals --mocked   # scripted model, network-free (what CI runs)
cd apps/backend && uv run python -m evals --no-guardrails   # writes evals/report-no-guardrails.md

# Model gate (M2+; needs a live Ollama model, --dry-run needs nothing):
cd apps/backend && uv run python -m evals.model_gate --model <id>   # --no-guardrails grades the off position

# Dataset regeneration (M1+; deterministic, seeded):
cd apps/backend && uv run python scripts/generate_dataset.py
```

## Repo layout / where to make which change

| Task | File(s) |
|---|---|
| Assignment-required deliverables | `apps/backend/app.py`, `db.py`, `agent.py`, `employees.csv`, `requirements.txt` (exported from `pyproject.toml` via `uv export`) |
| REST endpoint | `apps/backend/app.py` — thin handler, one service call, no logic |
| Conversation registry (scoped threads, titles, rename, per-turn history) | `apps/backend/conversations.py` (own app-state store `state.db`, beside the LangGraph checkpointer; access always verified against the JWT identity). `rename_thread` is the reader's own name and stamps the row's `renamed` flag; `retitle_thread` is the model's label and never writes over a stamped row (ADR 0012 as amended). Keeps one row per turn holding the trace events history kept, so a reopened thread replays the whole conversation — pruned to the newest `conversations.max_history_turns` turns (ADR 0012 as amended). It stores what it is handed and never inspects it; a row it cannot parse raises rather than replaying a partial turn as a whole one |
| What a turn's history keeps, and the caps on it | `apps/backend/turns.py` — `TurnLog` reduces the live trace events to the stored record: reasoning concatenated per model round, every call with the arguments the model wrote, each call's one outcome, the terminal frame always; tokens and the model-facing result text dropped. Owns all four per-turn caps and counts every piece they refuse, and is the one place a storage failure is logged and swallowed (the answer has already streamed) |
| Generated thread titles | `apps/backend/titles.py` — the model's few-word label for a thread, sanitized, falling back to the title the thread already has (the first message only while it is still unnamed); `should_title` is the window, so a thread is named again after each of its first `conversations.title_turns` turns and never after. Called by `PATCH /conversations/{id}`, never from the `/chat` stream (ADR 0012 as amended) |
| Auth / JWT / tenant users | `apps/backend/auth.py` |
| Data load + tenant-scoped execution | `apps/backend/db.py` — the ONLY module that opens a SQLite connection. `init_db` loads the CSV (accepting `str` or `Path`) and `employee_rows` is how `create_app` tells a populated database from one that was never built (ADR 0003 as amended). `execute_unscoped_browse` is the one deliberately unscoped read, for `browse.py`'s listings only — see the hard rule below |
| SQL validation (allowlist) | `apps/backend/security.py` |
| Records/Notes browsing (allowlisted filters, sorts, paging, filter options, the poison manifest) | `apps/backend/browse.py` — fixed templates with bound filter values. The LISTINGS run through `db.execute_unscoped_browse` and show the whole dataset, with `tenant_id` a bound filter and a sortable column like `department` (ADR 0014 as rewritten); the notes SEARCH stays scoped, delegating to `rag.py`, and `annotate_note_hits` joins each hit's tenant, department and score off its row through `db.execute_scoped` so a retrieval claim is checkable. Sort, direction and an options column are allowlisted words, never bound values. `filter_options` serves the tenant and department pickers from the same read, so no count describes a set nobody asked for. `ignored_params` reports the parameters a listing did not read — names only — so a discarded parameter is stated rather than swallowed |
| Structured analytics (aggregates, Tukey IQR anomalies, chart data) | `apps/backend/analytics.py` — allowlisted args into fixed query templates through `db.py`; never generated SQL |
| Agent, tools, prompts, retry policy, memory, transcript replay | `apps/backend/agent.py` (`thread_messages` reads the checkpointer back; the API layer never parses checkpoints itself). `_system_prompt` is the ONLY place the system prompt is composed and the only reader of `agent.prompt_guardrails`; no enforcement module may name that knob. A tool docstring in `_build_tools` is bound as the tool's `description` and reaches the model in BOTH guardrail positions, so it states what the tool does and never a rule the model is asked to follow (ADR 0011 as amended). `_fit_history` is the one place the history is bounded before a model call: it drops whole oldest turns, never the system prompt and never the current question, never below `agent.min_history_turns`, and only from what is SENT — the checkpointer keeps every message and replay is untouched |
| Note embedding + tenant-partitioned vector search | `apps/backend/rag.py` (storage/queries via `db.py`). `ensure_index` stamps the store with a digest of the corpus it embedded, so a regenerated dataset re-embeds instead of being searched through stale vectors (ADR 0010 as amended) |
| Dataset generator | `apps/backend/scripts/generate_dataset.py` — truncated-lognormal salaries by rejection (never clipped) and compositional notes whose clause pools are disjoint per score band (ADR 0008 as amended) |
| Eval harness | `apps/backend/evals/` — `harness.py` owns the shared bricks (workspace, trace collection, leak check, markdown) that `correctness.py`, `adversarial.py` and `model_gate.py` all import. `--no-guardrails` grades the off position - on the suites, where each position writes its own report file, and on `model_gate`, whose sections state their position because `gate-results.md` is append-only. `harness.guardrail_note` is the one owner of that wording, so all three reports say it identically (ADR 0004 as amended) |
| Tests | `apps/backend/tests/` (pytest), `apps/frontend/src/**/*.test.tsx` (vitest) |
| Where a state file lives | `apps/backend/paths.py` — the ONE owner of state-path derivation: the data directory (`SECURE_RLS_DATA_DIR`, defaulting to the package directory so dev needs no variable) and `employees.db`, `state.db`, `checkpoints.db` inside it. `db.py` still derives `audit.db` and `vectors.db` as siblings of the database it was handed, which is why a tmp database keeps its own. In the deployment the directory is a named volume (ADR 0013 as amended) |
| Tunable knob | `apps/backend/runtime.json` (typed view in `runtime.py`) — no magic values in code |
| Frontend UI | `apps/frontend/src/` — compose the design bricks (catalogue: `src/components/README.md`); never hand-roll a table/pill/button |
| Frontend session (token, display-only JWT claims, logout) | `apps/frontend/src/auth.ts` |
| Frontend HTTP calls (Bearer header, `X-Refreshed-Token` adoption, 401 -> login) | `apps/frontend/src/lib/api.ts` — the only module that calls `fetch` |
| Frontend chat stream (SSE frames -> typed trace events -> one turn's state) | `apps/frontend/src/lib/sse.ts` + `lib/trace.ts`; rendered by `views/ChatView.tsx` over the `components/chat/` bricks. `lib/trace.ts` is the ONE fold: `replayTurns` runs a reopened thread's stored events through `applyEvent` itself, so a past turn is the same `Turn` object — and therefore the same bricks — as a live one. The only differences a replayed turn carries are the two it cannot have: no token-by-token arrival, and no measured thinking span (that clock is this browser's) |
| Frontend Records / Notes tabs | `apps/frontend/src/views/RecordsView.tsx`, `views/NotesView.tsx` — the whole dataset, with `tenant_id` a filter like any other and every total naming what it counts ("1,000 rows · all tenants", "450 · tenant acme"); filters, sorts and pages are query parameters, never in-browser reordering; the tab strip is the `layout/Tabs` brick in the shell header and a visited tab stays mounted; the tenant filter is a `forms/ChipRow` (no counts on the chips) and the executed statement sits behind a closed `Disclosure` under a caption stating that the listing is unscoped by design. The reader-facing parameter probe was removed on owner review (issue #139) — `browse.ignored_params` still reports every unread parameter in the response, checkable with curl or a network tab (ADR 0014 as rewritten and amended) |
| Frontend conversation state (thread list, which thread is open, its replay) | `apps/frontend/src/lib/conversations.ts` — the one owner the rail (`views/ConversationsSidebar.tsx`) and the chat view share; `replay` is the open thread's past turns, already folded. `select` answers with the thread it opened, or null when the registry refused it, which is what lets the shell clean a URL naming a thread that is gone |
| Where the reader is (which tab, which thread) | `apps/frontend/src/lib/location.ts` — the ONE owner of the URL hash: `#/chat/<thread_id>`, `#/chat`, `#/records`, `#/notes` and nothing else ever, so a reload lands back where the reader was (issue #135). Parse, subscribe (a `hashchange`/`popstate` store like `auth.ts`, read through `useSyncExternalStore`), push for somewhere to come back to, replace for a restatement, clear on logout. `App.tsx` reconciles it with `lib/conversations.ts` through that store's own `select`, so a restored thread renders through the path a click takes; a thread the registry will not open lands on the draft with its existing message and the hash is cleaned — deleted and foreign stay indistinguishable (ADR 0012). The hash carries no token, no query and no view's filters |
| The conversation rail itself (collapse, inline search, hover glide) | `apps/frontend/src/views/ConversationsSidebar.tsx` over the `components/layout/Sidebar` collapse mechanism plus the `InlineSearch` and `GlideList` bricks; every metric and duration is a custom property in the rail's block in `styles/app.css` (issue #114, `docs/ui-pattern-review.md`). The aside clips (`overflow: clip`) while the column inside it stays at the expanded width — that is what keeps its icons from moving — so a slot with clipped controls reads `useSidebarCollapsed()` and takes them out of the Tab order rather than being unmounted |
| Number formatting a reader sees (axis ticks, bin edges, table cells, elapsed seconds, singularized counts) | `apps/frontend/src/lib/format.ts` — the only formatter; the backend emits raw numbers and never a locale-specific string |
| The generated-versus-executed SQL money shot | `apps/frontend/src/components/SqlRewrite.tsx` paints it, `lib/sqldiff.ts` aligns it — both cards always, no toggle, with the tenant scoping highlighted inside the statement that ran; the pair stacks (executed second) below 700px of its own width, a `@container` query in `styles/app.css` (ADR 0012 as amended, and `docs/ui-pattern-review.md`). The same file's `SqlTemplate` is the one-statement card for a fixed-template tool (`get_stats`, `plot`, `detect_anomalies`), where there is no generated side to diff: `sqldiff.markScoping` finds the injected subquery by its known shape and the card paints it through the same mark and the same legend, and a statement without that pattern renders unmarked |
| Design tokens / fonts / logo | `apps/frontend/src/styles/tokens.css` + `public/` — copied from knowledgebase, which stays the tracking source |
| A metric KB does not define (the shared control height, the loader's grid) | `apps/frontend/src/styles/app.css` — its own `:root` block, because `tokens.css` is KB's verbatim copy and a token added there would be lost on the next sync. A row mixing an input with a button carries `control-row` and every control in it takes `--control-height` (ADR 0014 as amended), asserted by `src/styles/controls.test.ts`; the `--loader-*` properties are the pixel-grid loader's cell, gap, rhythm and two opacities, so no metric of it is a number in JSX |
| Anything loading (a streaming turn, a pending button, a tab's first page) | `apps/frontend/src/components/Loader.tsx` — the one loading signal: a 3x3 pixel grid with a chevron wavefront, an optional shimmering label and an optional elapsed time computed from a start timestamp and formatted by `lib/format.ts`. `grid={false}` drops the 3x3 and leaves the shimmering label, which is how the chat flow carries the grid exactly once — on the answer card's pre-token placeholder, never in the trace, whose thinking row shimmers and counts instead (the owner's placement ruling on #123). There is no second spinner; a new loading state composes this brick (`docs/ui-pattern-review.md`) |
| CI / CD | `.github/workflows/ci.yml` |
| Images / the deployment unit / the state volume | `apps/backend/Dockerfile`, `apps/frontend/Dockerfile` + `nginx.conf`, `docker-compose.yml` (the `backend-state` volume; `down -v` is the destructive reset) |
| A design decision | `docs/decisions/` — new ADR, linked from `docs/INDEX.md` |
| Assignment-facing docs | `README.md` is the short form only — one-line security-layer summary, quickstart, creds, the eval headline table, the deliverables map, one paragraph per challenge wave, one-line limitations, time spent; it links every claim to its owner and stays around 250-350 lines (issue #133). The depth has exactly one home each: `docs/architecture.md` (layers, tools, components, the browse control group, the compliance map), `docs/api.md` (routes, the SSE frames and their invariants, sessions), `docs/development-process.md` (agentic method, tests, the eval harness and gate, CI/CD), `docs/challenges.md` (per-wave challenges, known limitations). A fact belongs in one of those and is summarized, never duplicated, in the README |

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
  validator + scoping + egress check. Exactly two exceptions, both named here
  because a rule that contradicts the code is worse than no rule:
  1. `conversations.py` owns the separate app-state store `state.db`, which
     holds no tenant rows.
  2. `db.execute_unscoped_browse` — the ONE deliberately unscoped read, used
     only by `browse.py`'s Records/Notes listings, which are the demo's control
     group and show the whole dataset (ADR 0014 as rewritten). It keeps the
     validator, the engine authorizer, the read-only connection, the limit caps,
     the query deadline, the row cap and the audit row; it drops only the
     tenant scoping, the structural proof of it, and the tenant egress
     comparison, because returning every tenant is its purpose. No agent tool
     is closed over it and no other module may call it — both are asserted in
     `tests/test_db.py`, and the agent's reach is unchanged.
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
