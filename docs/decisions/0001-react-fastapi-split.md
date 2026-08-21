# ADR 0001 — React + FastAPI over Streamlit/Dash

Status: accepted

## Context

The assignment allows React, Dash, or Streamlit. Streamlit would be the fastest
path (pure Python, chat UI built in) and the smallest surface to defend in the
60-minute call. But "full-stack development capabilities" is an explicit
evaluation area, and the author maintains a product family (knowledgebase,
modelbench) that is React SPA + FastAPI with a shared design system — this
project is the third sibling.

## Decision

React SPA (Vite) + FastAPI backend. The SPA is a pure HTTP client; all logic is
server-side. Ports follow the sibling scheme: backend `:8002`, frontend `:3002`.

A real client/server split also makes the security story honest: the JWT with
the `tenant_id` claim crosses a genuine trust boundary, instead of auth being
simulated inside one Streamlit process.

## Consequences

- More code than Streamlit — mitigated by reusing the KB design bricks (ADR 0006).
- Cleaner auth architecture: login issues a JWT; `/chat` verifies it; the
  tenant claim is the single identity source (ADR 0002 layer 1).
- The frontend build becomes a CI job.

## Alternatives

- **Streamlit** — fastest, but weak full-stack signal and an auth story that is
  a session-state simulation rather than a trust boundary.
- **Dash** — Python like Streamlit but with more boilerplate for chat UIs and
  no advantage over either option for this use case.
