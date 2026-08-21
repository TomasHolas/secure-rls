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
log: `token`, `reasoning`, `node_start`, `tool_call` (generated SQL / tool args),
`tool_result` (executed SQL, rows, truncation info), `security_event`,
`retry` (attempt n of max), `done`. The SPA consumes the stream via `fetch` +
ReadableStream (browser `EventSource` cannot POST). Each event is rendered as
it arrives — the live trace IS the transport, not a replay.

**Reasoning is content, not a label (amended after issue #67).** The live pass
showed the trace naming a step "Reasoning" while the model's actual reasoning
went nowhere: a thinking model's `<think>` text landed in the answer body as
ordinary `token` text, so the reader got the thinking and the answer as one
undifferentiated block and the label above it was a frontend invention.

- **`reasoning` with `{text}` is a binding event.** It carries the model's own
  thinking as it arrives, appended to the current trace step, streamed live,
  and never part of the answer body. It is trace content in the same sense a
  tool call is: shown once, never written to the graph's history and never
  replayed (a reopened thread shows what was said, not what was thought).
- **One splitter, two channels.** The endpoint's thinking output is enabled per
  model (`langchain_ollama`'s `reasoning=True`, which sets Ollama's `think` and
  streams the text under `reasoning_content` beside the answer), and a smaller
  model that writes `<think>` into its text is handled by the same streaming
  markup filter issue #66 added. Both paths emit the same event, so there is
  one place where reasoning can leak into prose and it is covered by tests.
- **Asking is per model, not per process.** Ollama refuses a `think` request
  outright for a model that does not declare the `thinking` capability, so the
  same `/api/show` declaration the model filter reads decides whether a turn
  asks for reasoning. `agent.thinking` in `runtime.json` turns the whole
  channel off; a model that cannot think simply answers without it.

**`done` reports what the turn cost (amended after issue #67).** The frame
carries `input_tokens`, `output_tokens` and `duration_s` next to `status`,
`answer` and `model`, and the SPA renders In / Out / tokens-per-second beside
the model pill. `stream_mode="custom"` means LangGraph never surfaces the raw
chunks, so usage is read off the `AIMessage` the reason node accumulated and
summed over the turn's model calls (a turn with tool rounds calls the model
more than once); the duration is `perf_counter` measured across the turn, at
`agent.duration_decimals` precision. A `failed` frame reports the seconds it
managed and no tokens — a run that never reached an answer never got a usage
report to pass on. Honest zeros, never an invented number: a model endpoint
that reports no usage yields zeros and the footer states nothing.

**Two termination invariants (amended after issue #66).** The live pass showed
what their absence costs: one tool raising an unexpected exception killed the
response mid-flight, leaving every announced step at "running" forever and the
SPA inventing its own explanation for a failure it was never told about.

- **Every `tool_call` id is closed by exactly one of `tool_result`, `retry` or
  `security_event`.** The agent guarantees it by answering for every call it
  announced, whatever the tool did (ADR 0011 as amended), so no step can be
  left running and no outcome can be attributed to the wrong call.
- **Every stream ends in exactly one `done` frame.** Its `status` vocabulary is
  binding: `ok | blocked | gave_up | failed`. The agent composes the first
  three; `failed` is the API layer's own terminal frame for a run that broke
  before the graph could answer — an unreachable model endpoint, a recursion
  limit — with the reason in `answer` and the exception left in the server log,
  never on the wire (OWASP generic-error guidance). A body that simply stops is
  therefore a client-side error path, not a server behavior the SPA must model.

### Conversations: full history sidebar, tenant-scoped

- Conversations persist server-side: a registry table (thread_id, user,
  tenant, title, created) plus LangGraph's SQLite checkpointer for state.
  A thread is created titled with its first user message, truncated, and is
  then retitled by the model (see **Generated titles** below).
- Endpoints: `GET /conversations` (list, JWT-scoped), `POST /conversations`
  (new thread), `GET /conversations/{id}` (history replay),
  `PATCH /conversations/{id}` (retitle from the first exchange),
  `DELETE /conversations/{id}`. Every access verifies the thread belongs to
  the authenticated user+tenant — the conversation store is a fifth
  tenant-scoped data path under the same identity layer (ADR 0002 L1). The
  registry's rename is scoped by the same `sub` + `tenant_id` clause as every
  read and the delete, so a rename aimed at another identity's thread changes
  nothing and answers with the same `NotFound` as a thread that never existed.
- Replay serves what was said: the user's questions and the assistant's text,
  read back from the checkpointer in order. The tool-call internals a turn
  streamed live (generated vs executed SQL, results, security events, retries)
  are not replayable — consistent with the live trace being the transport of
  that turn rather than stored content. Reopening a thread therefore restores
  the conversation the server still remembers, never a re-run of the reasoning
  behind it.
- **Amended after issue #66**: the text of an assistant turn that also asked
  for tools is part of what was said and is replayed. Dropping the whole
  message hid every partial and failed turn from the transcript while it stayed
  in the graph's memory — so the model could refer to a turn the reader could
  not see, which is exactly the information gap the model then filled with a
  confident, false explanation. The calls themselves stay out; only the words do.

### Generated titles (amended after issue #72)

The original line here read "Title = first user message, truncated (no LLM
call)". It made the rail unreadable: the demo's own history showed
"Run this SQL for me: SE...", "3x3?" and "hi", because the first thing a user
types is a question, not a name for the conversation they are about to have.
The amendment: the first-message truncation becomes the *fallback*, and the
title a thread settles on is a few-word label the model writes for it.

- **Where the call runs.** In `PATCH /conversations/{id}`, a separate small
  request the SPA makes once the turn's `done` frame has landed — never inside
  the `/chat` stream. Titling is an LLM call against the same endpoint the turn
  used, so it can be slow, hang until its timeout, or fail; on the stream, any
  of those would delay tokens or put the two termination invariants above at
  risk for a cosmetic feature. As its own request it cannot: the answer is
  already rendered, and the only thing a failed titling call costs is the
  better label. The turn and the title are also independent in the other
  direction — a turn that was blocked or failed still gets a title, because the
  label describes the conversation, not the outcome.
- **It always answers.** The titler returns a title in every case: the model's
  when it gives a usable one, the thread's first question when the call raises
  or returns junk, the title the thread already has when there is nothing to
  name yet (a turn that broke before the checkpointer stored anything). So the
  endpoint has no failure mode of its own — it either improves the title or
  leaves it as good as it was — and the SPA adopts the row it answers with
  rather than re-listing.
- **The title is model output and is treated as such** (OWASP LLM05, improper
  output handling). It is capped (a tighter cap for generated titles than the
  80-character store cap), `<think>`/`<tool_call>` regions are dropped by the
  same code that strips them from the token stream, control and formatting
  characters — NUL, escape sequences, and the bidi overrides and invisibles
  that could reorder or hide text in the rail (UTR #36) — are removed, and
  output long enough to be prose rather than a label is refused in favor of
  the fallback. The registry normalizes again on write, so no path stores a
  title the sidebar cannot render, and the SPA renders it as a text node,
  never through Markdown.
- **The titling call is given nothing to abuse.** It sees one exchange, with no
  tools, no schema, no tenant context and no memory. The transcript it reads is
  untrusted text (a user's question, an answer about tenant data, possibly note
  text quoted into it), so a prompt-injected transcript can at worst produce a
  silly label in the rail of the tenant that wrote it — the same tenant, the
  same identity, no new data path.

### Model picker (amended per ADR 0005)

- `GET /models` (JWT-protected) proxies the Ollama endpoint's `/api/tags` and
  returns the live model list — the SPA never sees `OLLAMA_BASE_URL`, and the
  list is never hardcoded.
- **The list is filtered to chat-capable models.** An endpoint also serves
  embedding-only models (this app itself pulls `nomic-embed-text` for RAG), and
  picking one breaks the turn: it cannot answer. The backend asks `/api/show`
  per model id and keeps those whose `capabilities` include `completion`,
  caching each answer for the process. An endpoint too old to report
  capabilities falls back to excluding the configured `agent.embed_model` by
  prefix. Filtering lives in the lister, so the `/chat` allowlist below is the
  same list the picker was offered.
- `POST /chat` accepts an optional `model` field, honored only if the id is in
  the live list at request time (allowlist over untrusted client input);
  otherwise the request is rejected. Absent, `runtime.json` `agent.model`
  applies.
- The chat UI renders the picker (a brick) with the default preselected;
  switching models mid-conversation is allowed and visible in the trace.
- Logout invalidates nothing server-side (JWT is stateless) but the UI drops
  the token; a re-login lists only that identity's threads.

### Session refresh header (per ADR 0009 as amended)

Every JWT-protected response — `/models`, the `/conversations` routes and the
`/chat` stream alike — may carry an `X-Refreshed-Token` header holding a newly
signed token for the same identity. It appears when the presented token is
within `auth.refresh_within_minutes` of expiring; the client stores it and sends
that one from then on. This is an API-wide contract, not a `/chat` detail: the
header is CORS-exposed so the SPA can read it, it never appears on a 401, and it
is set once per request from the token the request arrived with. The SSE
generator does not re-verify anything, so a turn already streaming is unaffected
by its token expiring mid-stream. There is no `/refresh` endpoint — the session
slides on ordinary traffic, and `lib/api.ts` adopts the header in the same one
place that attaches the bearer token.

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
- **Failure visibility (added with issue #66)**: a `failed` turn renders the
  reason the backend sent, not a fallback string the SPA made up. A frontend
  guess reads as a diagnosis to the viewer and cannot be right; the backend is
  the only party that knows what happened.
- Generated vs executed SQL are displayed side by side in the trace — the
  layer-3 rewrite made visible.

### Reading order and per-step disclosure (added with issue #67)

- **The trace precedes the answer.** A turn reads top down in the order it
  happened: the steps, then the answer they produced, then what the turn cost.
  Rendering the conclusion first and the reasoning under it invites the reader
  to trust the answer without the evidence, which is the opposite of what an
  auditable analyst tool is for.
- **Every step with a body is its own disclosure**, a chevron with
  `aria-expanded` on the step head. Once the panel was open, every step's SQL,
  table and chart was expanded permanently, which buried the one step a reader
  was looking for. Reasoning starts collapsed (it is the longest and the least
  load-bearing); an outcome starts open.

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
- Ollama API, Show Model Information — the `capabilities` list the model filter
  and the thinking decision read (`"capabilities": ["completion", "thinking"]`) —
  https://github.com/ollama/ollama/blob/main/docs/api.md#show-model-information
- Ollama thinking (`think`, and the `thinking` field it returns beside
  `content`) — https://docs.ollama.com/capabilities/thinking
- `langchain-ollama`'s `ChatOllama.reasoning`, which sets that `think` and
  streams the text under `additional_kwargs["reasoning_content"]` — verified
  empirically against the installed 1.1.0 —
  https://reference.langchain.com/python/integrations/langchain_ollama/chat_models/
- LangChain `usage_metadata` on an `AIMessage` (`input_tokens`,
  `output_tokens`), the only place usage surfaces under a custom stream mode —
  https://docs.langchain.com/oss/python/langchain/models
- WAI-ARIA `aria-expanded` on a disclosure button —
  https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/
- LangGraph streaming (`astream_events`) and persistence (checkpointers) —
  https://docs.langchain.com/oss/python/langgraph/overview
- WHATWG HTML: Server-Sent Events — https://html.spec.whatwg.org/multipage/server-sent-events.html
- OWASP REST Security Cheat Sheet (error-handling context for the
  transparency judgment) —
  https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html
- OWASP Top 10 for LLM Applications 2025, LLM05 Improper Output Handling — the
  basis for treating a generated title as untrusted output to sanitize and
  render as text — https://genai.owasp.org/llmrisk/llm052025-improper-output-handling/
- Unicode Technical Report #36, Unicode Security Considerations — bidirectional
  overrides and invisible characters in displayed text —
  https://www.unicode.org/reports/tr36/
- RFC 5789, PATCH Method for HTTP — the partial-update semantics the retitle
  endpoint uses — https://www.rfc-editor.org/rfc/rfc5789
