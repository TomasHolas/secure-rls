# ADR 0005 — Ollama as a configurable remote endpoint; model choice

Status: accepted (amended 2026-08-21: live model picker; demo model locked to
huihui_ai/qwen3-abliterated:30b-a3b after a measured shootout; gate validates
rather than picks)

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
- **Demo default (owner decision, 2026-08-21, superseding the earlier q8_0
  pick)**: `runtime.json` `agent.model` = **`huihui_ai/qwen3-abliterated:30b-a3b`**.
  Measured shootout on the host over the tailnet (identical `query_db`
  tool-call test):

  | Model | Size | tok/s | Tool call |
  |---|---|---|---|
  | huihui_ai/qwen3-abliterated:30b-a3b | 18 GB | 120.9 | clean, valid SQL |
  | orcarouter/Qwen3.8-27B-Uncensored:q4_K_M | 17 GB | 28.2 | clean, valid SQL |
  | orcarouter/Qwen3.8-27B-Uncensored:q8_0 | 29 GB | 18.1 | clean |
  | Qwen3.8-27B-Uncensored:f16 | 54 GB | 9.1 | clean |

  The winner's speed is architectural (MoE, ~3B active parameters). Backup:
  `orcarouter/Qwen3.8-27B-Uncensored:q4_K_M`. `f16` stays as the M5
  eval-harness model, where quality is scored and speed is irrelevant. All
  four remain installed on the host. Considered and excluded for cause:
  gpt-oss 20b/120b despite top raw speed — open ChatOllama tool-calling bugs
  (langchain-ai/langchain#32428, ollama/ollama#11704) in exactly this
  project's stack; qwen3-next:80b-a3b — documented llama.cpp MoE inefficiency
  (ggml-org/llama.cpp#19480) with no verified Apple Silicon benchmark.
  The M2 gate (issue #20) runs the ~20-prompt suite against 30b-a3b (primary)
  and q4_K_M (backup) as validation and records results here — it can veto
  the pick only on demonstrated tool-calling failures.
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
