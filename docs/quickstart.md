# Quickstart

How to configure and run secure-rls - compose as the deployment, dev mode for
work on the code. The one-command summary: put two variables in `.env` and
`docker compose up --build`; the app is at http://localhost:3002 and the demo
credentials are in the [README](../README.md#tenant-credentials).

## Configure

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
([ADR 0005](decisions/0005-ollama-endpoint-and-model.md)), and it must serve
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

## Run - compose (the primary path)

```bash
docker compose up --build        # backend :8002, frontend :3002
```

This is the deployment unit
([ADR 0013](decisions/0013-deployment-cicd.md)); CI publishes both images to
GHCR on every push to `main`, so compose also runs the published
`ghcr.io/tomasholas/secure-rls-{backend,frontend}:latest` without building (the
frontend image bakes its API URL, so a different backend means rebuilding with
`VITE_API_URL` set). Backend state — conversations and their turns, the LangGraph
memory, the audit trail, the embeddings, the loaded tenant data — lives on the
named volume `backend-state`, so a rebuild keeps it; `docker compose down -v` is
the only reset, after which the next boot reloads the CSV and re-embeds the notes.

## Run - dev mode

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

## Tests and evaluation commands

```bash
cd apps/backend && uv run pytest -q     # the layers and the API edge, no Ollama
cd apps/frontend && npm test            # the bricks, the session, the trace fold
cd apps/backend && uv run python -m evals --mocked         # the harness, no endpoint
cd apps/backend && uv run python -m evals --no-guardrails  # live, self-policing off
```

Methodology, graded failures, test totals, the model gate and CI:
[development-process.md](development-process.md).
