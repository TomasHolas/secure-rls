# ADR 0013 — Deployment: CI+CD to GHCR, compose as the deployment unit

Status: accepted

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

## Consequences

- "Deployment" is demonstrably real: pull two public images, `compose up`,
  login. The README documents it as the primary run path.
- The compose file is also the local dev convenience, keeping one description
  of the stack.
- Image publishing makes the repo's packages public alongside the code.

## Alternatives

- **CI only** — fails the natural reading of the requirement.
- **Deploy to a cloud target (Azure/AWS)** — real hosting, but adds accounts,
  costs, and secrets for a case study meant to run local/offline; noted as
  future evolution.

## References

- GitHub Docs, publishing Docker images to GHCR with Actions —
  https://docs.github.com/en/actions/publishing-packages/publishing-docker-images
- docker/build-push-action — https://github.com/docker/build-push-action
- ADRs 0001 (ports), 0005 (endpoint as config), 0009 (no committed secrets)
