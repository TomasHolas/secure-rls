# docs — index

Design documentation for secure-rls. `CLAUDE.md` at the repo root is the
resumption guide; this folder holds the architecture and the accepted decisions.
When design and code disagree, the code plus `CLAUDE.md` win, but update the
docs in the same change.

## Requirements

- [requirements.md](requirements.md) — the assignment, distilled: hard
  requirements, MVP features, deliverables, demo-call agenda.

## Architecture

- [architecture.md](architecture.md) — system overview, the four RLS defense
  layers, request flow, data model, agent tool set, assignment compliance map.

## Decisions (ADRs)

- [0001 — React + FastAPI over Streamlit/Dash](decisions/0001-react-fastapi-split.md)
- [0002 — Defense-in-depth RLS: four independent layers](decisions/0002-defense-in-depth-rls.md)
- [0003 — SQLite with emulated RLS via a scoped executor](decisions/0003-sqlite-scoped-execution.md)
- [0004 — Testing and evaluation strategy: CI never needs a model](decisions/0004-testing-and-eval-strategy.md)
- [0005 — Ollama as a configurable remote endpoint; model choice](decisions/0005-ollama-endpoint-and-model.md)
- [0006 — Reuse of the knowledgebase design system](decisions/0006-kb-design-system.md)
- [0007 — Result-size handling: cap, truncation signal, aggregation push-down](decisions/0007-result-size-handling.md)
- [0008 — Dataset generation: seeded, calibrated to sourced distributions](decisions/0008-dataset-generation.md)
- [0009 — Auth implementation: PBKDF2 hashing, pinned-algorithm JWT](decisions/0009-auth-implementation.md)
- [0010 — Tenant-filtered RAG over notes (sqlite-vec partition keys)](decisions/0010-tenant-filtered-rag.md)
- [0011 — Agent design: explicit graph, retry policy, memory, tool contracts](decisions/0011-agent-design.md)
- [0012 — API transport and chat UX: SSE, scoped conversations, transparent refusals](decisions/0012-api-and-chat-ux.md)
- [0013 — Deployment: CI+CD to GHCR, compose as the deployment unit](decisions/0013-deployment-cicd.md)
