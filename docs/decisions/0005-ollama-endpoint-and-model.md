# ADR 0005 — Ollama as a configurable remote endpoint; model choice

Status: accepted (amended 2026-08-21: demo model q8_0; live model picker; gate
validates rather than picks)

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
  comfortably** (target 14b-32b), with solid tool-calling support in
  Ollama/LangChain. **No local fallback model**: the dev laptop is too weak to
  run one (owner decision, 2026-08-21) — the tailnet host is the only model
  endpoint. Candidate set and provisioning status are tracked in issue #11.
- **M2 empirical gate**: before the pick is final, run the tool-calling smoke
  suite (does the model reliably call `query_db` with well-formed SQL across
  ~20 prompts?) against the candidates and record results in this ADR.
- **Demo default (owner decision, 2026-08-21)**: the host serves two builds of
  the chosen model — `Qwen3.8-27B-Uncensored:f16` (full precision, ~9.1 tok/s)
  and `orcarouter/Qwen3.8-27B-Uncensored:q8_0` (~2x faster). The demo default
  is **q8_0** for pacing; `runtime.json` `agent.model` carries it. The M2 gate
  (issue #20) validates tool-call quality on both and records the table here —
  it can veto q8_0 only on demonstrated tool-calling failures.
- **The model is user-selectable at runtime**: the UI offers a model picker
  populated live from the endpoint's `/api/tags` — never a hardcoded list —
  proxied through the backend (`GET /models`, ADR 0012 as amended) so the
  client never learns `OLLAMA_BASE_URL`. A client-chosen model id is accepted
  only if present in that live list (allowlist over untrusted input).
  `runtime.json` `agent.model` is the default when the client sends none.
  Model choice has zero effect on RLS — every layer is model-agnostic
  (ADR 0002), which the demo states explicitly.

## Consequences

- The system is honest about being a client of a model endpoint — the same
  code serves localhost and the tailnet machine.
- Demo insurance without a fallback model: both laptops are physically present
  at the call (Tailscale also works over the same LAN), the host runs a
  KeepAlive-supervised Ollama service, and a pre-call health check
  (`/api/version` + one live tool call) is part of the demo runbook. If the
  host still dies mid-call, the deterministic security tests and the committed
  eval report carry the security story without a live model.
- Security does not depend on the model at all (ADR 0002); the model choice
  affects only answer quality and tool-call reliability.

## Alternatives

- **Hardcoding a local Ollama** — rejected: weaker model, and a hidden
  assumption the deployment section would contradict.
- **Hosted API models** — rejected: assignment requires local/offline.
