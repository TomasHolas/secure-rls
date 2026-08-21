# ADR 0012 — API transport and chat UX: SSE streaming, scoped conversations, transparent refusals

Status: accepted

## Context

The demo must show the agent reasoning live (as in modelbench's Live Run view),
support multi-turn conversations with history, and make the security behavior
visible. Ollama streams tokens natively; LangGraph's `astream_events` exposes
node/tool/token events.

## Decision

### Transport: SSE with typed events

`POST /chat` returns a Server-Sent Events stream. Event types mirror the audit
log: `token`, `node_start`, `tool_call` (generated SQL / tool args),
`tool_result` (executed SQL, rows, truncation info), `security_event`,
`retry` (attempt n of max), `done`. The SPA consumes the stream via `fetch` +
ReadableStream (browser `EventSource` cannot POST). Each event is rendered as
it arrives — the live trace IS the transport, not a replay.

### Conversations: full history sidebar, tenant-scoped

- Conversations persist server-side: a registry table (thread_id, user,
  tenant, title, created) plus LangGraph's SQLite checkpointer for state.
  Title = first user message, truncated (no LLM call).
- Endpoints: `GET /conversations` (list, JWT-scoped), `POST /conversations`
  (new thread), `GET /conversations/{id}` (history replay),
  `DELETE /conversations/{id}`. Every access verifies the thread belongs to
  the authenticated user+tenant — the conversation store is a fifth
  tenant-scoped data path under the same identity layer (ADR 0002 L1).

### Model picker (amended per ADR 0005)

- `GET /models` (JWT-protected) proxies the Ollama endpoint's `/api/tags` and
  returns the live model list — the SPA never sees `OLLAMA_BASE_URL`, and the
  list is never hardcoded.
- `POST /chat` accepts an optional `model` field, honored only if the id is in
  the live list at request time (allowlist over untrusted client input);
  otherwise the request is rejected. Absent, `runtime.json` `agent.model`
  applies.
- The chat UI renders the picker (a brick) with the default preselected;
  switching models mid-conversation is allowed and visible in the trace.
- Logout invalidates nothing server-side (JWT is stateless) but the UI drops
  the token; a re-login lists only that identity's threads.

### Security visibility

- **Transparent refusals**: a `security_event` renders as a distinct blocked
  state naming the layer and reason ("Blocked: table sqlite_master not
  allowlisted — validation layer"). Justification: the viewer is the
  authenticated tenant; the display reveals that defenses exist and which
  fired, never other tenants' data. OWASP's generic-error guidance targets
  leaking internals (stack traces, system details) to untrusted callers; a
  labeled security event in an audit-transparent analyst tool is a deliberate
  product choice, recorded here as a judgment call.
- **Retry visibility**: retryable failures render each attempt with its error
  and counter — the two-tier retry policy (ADR 0011) is demonstrable, not
  claimed.
- **Truncation chip**: the ADR 0007 truncation signal renders as a visible
  notice on the result table.
- Generated vs executed SQL are displayed side by side in the trace — the
  layer-3 rewrite made visible.

## Consequences

- M3 grows the conversation endpoints; M4 grows the sidebar; both covered by
  scope tests (a user must never list or replay another user's threads).
- SSE keeps the backend single-process simple (no websockets state), and the
  event schema doubles as the eval suite's mechanical checking surface.

## Alternatives

- **Single JSON response** — simplest, but the demo loses the live reasoning
  view entirely.
- **WebSockets** — bidirectional capability we do not need; SSE is sufficient
  for server-to-client event flow.
- **No history (New chat only)** — least scope; rejected in favor of the
  fuller product feel and the extra scoped data path it demonstrates.

## References

- Ollama API (streaming chat) — https://docs.ollama.com/api
- LangGraph streaming (`astream_events`) and persistence (checkpointers) —
  https://docs.langchain.com/oss/python/langgraph/overview
- WHATWG HTML: Server-Sent Events — https://html.spec.whatwg.org/multipage/server-sent-events.html
- OWASP REST Security Cheat Sheet (error-handling context for the
  transparency judgment) —
  https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html
