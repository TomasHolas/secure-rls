# ADR 0005 — Ollama as a configurable remote endpoint; model choice

Status: accepted (model pick provisional until the M2 empirical gate)

## Context

The assignment requires local/offline LLMs via Ollama. The development machine
does not run Ollama; a second, stronger laptop on the same tailnet does, and can
power a larger model. The demo call must also survive that machine being
unreachable. LangGraph tool calling requires a model with reliable tool support.

## Decision

- **The Ollama endpoint is config, never code**: `OLLAMA_BASE_URL` in `.env`
  (default `http://localhost:11434` in `.env.example`). The Tailscale address
  is never committed — the repo goes public.
- **Model is a tunable** in `runtime.json` (`agent.model`), so switching costs
  a config edit, not a code change.
- **Provisional pick: `qwen3` in the largest size the tailnet machine runs
  comfortably** (target 14b-32b), fallback `llama3.1:8b` runnable on the dev
  laptop for offline demo insurance. Both have solid tool-calling support in
  Ollama/LangChain.
- **M2 empirical gate**: before the pick is final, run the tool-calling smoke
  suite (does the model reliably call `query_db` with well-formed SQL across
  ~20 prompts?) against the candidates and record results in this ADR.

## Consequences

- The system is honest about being a client of a model endpoint — the same
  code serves localhost and the tailnet machine.
- Demo insurance: if Tailscale or the second laptop fails mid-call, switch
  `OLLAMA_BASE_URL` to localhost with the fallback model pulled in advance.
- Security does not depend on the model at all (ADR 0002); the model choice
  affects only answer quality and tool-call reliability.

## Alternatives

- **Hardcoding a local Ollama** — rejected: weaker model, and a hidden
  assumption the deployment section would contradict.
- **Hosted API models** — rejected: assignment requires local/offline.
