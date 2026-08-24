# ADR 0013 — Deployment: CI+CD to GHCR, compose as the deployment unit

Status: accepted (amended 2026-08-24: state persisted on a named volume, issue #125)

## Context

The assignment requires "deployment implemented as GitHub CI/CD pipeline."
A pipeline that only lints and tests deploys nothing; the sibling repos
(knowledgebase, modelbench) ship as Docker images with one-command compose.

## Decision

One GitHub Actions workflow, two stages:

- **CI (every PR and push to main)**: ruff + pytest (network-free, mocked
  LLM), dataset regeneration + diff against the committed `employees.csv`,
  frontend build + tests, eval-harness dry run in mocked mode,
  `docker compose build` as the image-integrity proof.
- **CD (push to main)**: build and push `backend` and `frontend` images to
  GHCR (`ghcr.io/tomasholas/secure-rls-*`), tagged `latest` + commit SHA,
  via `docker/build-push-action` with the built-in `GITHUB_TOKEN`
  (`packages: write` permission).

Deployment unit: `docker compose up` — backend `:8002`, frontend (nginx-served
SPA) `:3002`, following the sibling port scheme. The Ollama endpoint stays
external config (`OLLAMA_BASE_URL`), per ADR 0005: CI/CD never needs a model;
the deployed stack points at whatever endpoint the environment provides.
Secrets (JWT signing key) enter via environment only — the image contains
none (ADR 0009).

## Persistence: one data directory on a named volume (added after issue #125)

The compose file above declared no volumes, so every SQLite file the backend
writes sat in the container's writable layer — which Docker destroys with the
container: "When a container is destroyed, the writable layer is destroyed with
it" ([Docker, volumes](https://docs.docker.com/engine/storage/volumes/)). A
`docker compose down`, or any `up --build`, therefore erased the conversation
registry and its turn history (`state.db`), the LangGraph memory
(`checkpoints.db`), the audit trail (`audit.db`) and the note embeddings
(`vectors.db`). Two of those losses are not convenience: the persistent audit log
is listed as hardening in [ADR 0002](0002-defense-in-depth-rls.md) and feeds the
eval leakage checks — an audit log a restart erases is not an audit log — and
discarding the vector store defeats the whole point of the corpus fingerprint in
[ADR 0010](0010-tenant-filtered-rag.md), which exists so an unchanged corpus is
never re-embedded.

The decision is **one data directory, mounted as one named volume**:

- `paths.py` is the single owner of state-path derivation. The directory is
  configuration — `SECURE_RLS_DATA_DIR` — and its default is the backend package
  directory, so the dev path (`uv run uvicorn app:app`) needs no new environment
  variable and the files stay exactly where they always were. `db.py` and
  `app.py` no longer derive a location; they read the paths.
- The image sets `SECURE_RLS_DATA_DIR=/app/data`, and compose mounts the named
  volume `backend-state` on it. A rebuilt image therefore replaces code and finds
  the data it left behind, which is the twelve-factor position that a process's
  filesystem is not where state belongs ([12factor,
  processes](https://12factor.net/processes)).
- It is a directory rather than one variable per file because the stores are one
  unit of state: they are mounted, backed up and reset together, and a partial
  override — a persisted audit log beside a discarded registry — is a
  configuration nobody wants.

**`employees.db` lives in the volume too**, with its two satellites. It is the
one file that does not strictly need to: it is derived deterministically from the
committed CSV, and `create_app` already tells a populated database from an empty
one with `db.employee_rows` (ADR 0003), so either choice boots correctly. It is
in because `db.py` derives `audit.db` and `vectors.db` as siblings of whatever
database it was handed — a relation between files, not a location, and the reason
a test or an eval that passes a tmp database keeps its own audit trail instead of
writing into the real data directory. Keeping the three together means one rule
about where they sit; excluding the derived one would mean a second path scheme
for the two that must persist, which is exactly the scattering this change
removes. The cost of carrying it is 282 KB.

The image still bakes `employees.db` at build time (ADR 0003), now into the
mounted directory. That is deliberate: mounting an empty volume onto a directory
that contains files propagates those files into the volume
([Docker, volumes](https://docs.docker.com/engine/storage/volumes/)), so a first
boot on a machine with no volume starts from the baked database and the volume is
created with the non-root app user's ownership rather than coming up root-owned
and unwritable. An empty volume mounted on an empty directory would leave the
container unable to write at all. The runtime loader stays the safety net for
that case: an empty database is loaded from the CSV exactly as in dev.

Resetting is explicit, and the asymmetry is the point:

- `docker compose down` keeps the volume: a volume outlives the container using
  it, and removing one is always a separate step
  ([Docker, volumes](https://docs.docker.com/engine/storage/volumes/)).
- `docker compose down -v` removes it — the flag removes "named volumes declared
  in the `volumes` section of the Compose file"
  ([Docker, `compose down`](https://docs.docker.com/reference/cli/docker/compose/down/))
  — and is therefore **destructive**: every conversation, the memory, the audit
  trail and the embeddings go with it, and the next boot rebuilds from the
  committed CSV and re-embeds the corpus.
- Because a populated database is left alone, regenerating the dataset means
  resetting the volume (or deleting `employees.db` from it); an image rebuild
  alone will not refresh data that is already there.

Secrets are unaffected: the volume holds no configuration. `JWT_SECRET` and
`OLLAMA_BASE_URL` stay environment-only with no defaults, and compose still fails
loudly when either is missing (ADR 0009).

## Consequences

- "Deployment" is demonstrably real: pull two public images, `compose up`,
  login. The README documents it as the primary run path.
- The compose file is also the local dev convenience, keeping one description
  of the stack.
- The deployment has state, and it is one named volume: a rebuild is safe and a
  reset is a flag a reader has to type on purpose.
- Image publishing makes the repo's packages public alongside the code.

## Alternatives

- **CI only** — fails the natural reading of the requirement.
- **Deploy to a cloud target (Azure/AWS)** — real hosting, but adds accounts,
  costs, and secrets for a case study meant to run local/offline; noted as
  future evolution.
- **A bind mount to a host directory instead of a named volume** — easier to
  inspect with a text editor, but it ties the stack to a host path and hands the
  files the host user's ownership, which a non-root container then cannot write.
  A named volume is portable and Docker manages its ownership.
- **One environment variable per store** — maximal flexibility for a split
  nobody has asked for, and it makes a half-persisted deployment expressible.
  One directory keeps the state one unit.

## References

- Docker Docs, volumes (the writable layer dies with the container; an empty
  volume is populated from the image's directory) —
  https://docs.docker.com/engine/storage/volumes/
- Docker Docs, `docker compose down` (`-v` removes the declared named volumes) —
  https://docs.docker.com/reference/cli/docker/compose/down/
- The Twelve-Factor App, processes (state belongs in a backing service, not the
  process filesystem) — https://12factor.net/processes
- GitHub Docs, publishing Docker images to GHCR with Actions —
  https://docs.github.com/en/actions/publishing-packages/publishing-docker-images
- docker/build-push-action — https://github.com/docker/build-push-action
- ADRs 0001 (ports), 0005 (endpoint as config), 0009 (no committed secrets),
  0002 (the audit log as hardening), 0003 (the database lifecycle), 0010 (the
  corpus fingerprint)
