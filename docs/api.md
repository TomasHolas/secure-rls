# HTTP API

The REST surface the SPA talks to, and nothing else talks to the data. Handlers
in [`apps/backend/app.py`](../apps/backend/app.py) are thin: one service call
each, no logic. Transport decisions are ADR 0012
([API transport and chat UX](decisions/0012-api-and-chat-ux.md)); auth is
ADR 0009 ([auth implementation](decisions/0009-auth-implementation.md)).

Everything but `/health` and `/login` requires `Authorization: Bearer <jwt>`.
The `tenant_id` claim in that token is the only source of tenant identity — no
route accepts a tenant in a body, a path or a query parameter.

## Routes

| Route | Purpose |
|---|---|
| `GET /health` | Liveness, the API version and the prompt-guardrail position. Open by design; also the container health check |
| `POST /login` | Demo credentials in, JWT with the `tenant_id` claim out. A wrong user and a wrong password return the same 401 |
| `GET /models` | The endpoint's live chat-capable models plus the default a turn resolves from that same list. The SPA never learns `OLLAMA_BASE_URL` |
| `POST /chat` | One turn as an SSE stream of typed trace events. The turn runs on the server independently of the stream, so a reader who leaves does not interrupt it; a second question on a thread that is still answering is a 409 |
| `GET /conversations` | The caller's own threads, newest first |
| `POST /conversations` | Register a thread for the caller |
| `PATCH /conversations/{id}` | Name a thread, in two modes: a title in the body is the reader's own and final, and a body without one asks the model for a label. The titling lifecycle is [ADR 0012](decisions/0012-api-and-chat-ux.md) |
| `GET/DELETE /conversations/{id}` | Replay or delete the caller's own thread. The replay carries `in_flight`, whether a turn is running on it right now. A foreign id and a missing id return the same 404 |
| `GET /records`, `GET /records/departments`, `GET /records/tenants` | The **whole dataset**, paged, filtered and sorted through allowlisted templates, with `tenant_id` a bound filter like `department`; plus the two filter pickers' options and counts — the Records tab, the control group ([ADR 0014](decisions/0014-records-and-notes-browsing.md)) |
| `GET /notes`, `GET /notes/flagged` | The whole note corpus and every planted injection payload in it, so a reader can see a foreign tenant's before the agent ever reads one |
| `GET /notes/search` | The agent's own retrieval path — **scoped to the token's tenant**, unlike the listing beside it. That asymmetry is the demonstration |
| `GET /audit` | The audit log every read above writes ([ADR 0002](decisions/0002-defense-in-depth-rls.md)): newest first, paged, all tenants' entries, no filters — the Audit tab. Statements and metadata only; no result row is in that store, and no tool can reach the route |

## The chat stream

The whole turn is streamed as Server-Sent Events, so the trace the UI renders
**is** the transport ([ADR 0012](decisions/0012-api-and-chat-ux.md)) — and those
same events are what the server keeps, so reopening the thread replays that turn
through the same code rather than a summary of it. The typed frames are
`token`, `node_start`, `tool_call`, `tool_result`, `security_event`, `retry` and
`done`.

Two invariants keep the stream honest:

- every announced `tool_call` is closed by exactly one `tool_result`, `retry` or
  `security_event`;
- every stream ends in exactly one `done` frame with status
  `ok | blocked | gave_up | failed`.

A `tool_result` carries two views of one result and says where they differ: `data`
is every row the server's cap allowed and is what the UI renders, `content` is
what the model was handed, and `withheld` is how many lines the model's copy lost
to the per-reply character cap ([ADR 0007](decisions/0007-result-size-handling.md)
as amended) — 0 when it lost none.

The `done` frame also carries the turn's cost, whether the answer was grounded in
a tool call of its own turn, and the prompt-guardrail position that produced it,
so no trace can be read as the other mode's.

The stream is a window onto the turn, not the turn's lifetime
([ADR 0012](decisions/0012-api-and-chat-ux.md) as amended). The turn runs on a
worker of its own and is recorded there, so a viewer who switches threads,
reloads or signs out mid-generation stops receiving frames and changes nothing
else: the turn finishes, the history keeps its whole trace including the terminal
frame, and reopening the thread replays it complete. What still bounds it is the
per-turn deadline and the tool-round cap inside the graph
([ADR 0011](decisions/0011-agent-design.md)). One turn per thread runs at a time
— a second `POST /chat` for a thread that is still answering is refused with 409
rather than interleaved onto the same conversation memory.

## Sessions

Sessions slide rather than expiring under the user: a token lives 120 minutes,
and any authenticated response may carry `X-Refreshed-Token` when the presented
token is within 30 minutes of expiry. There is no `/refresh` route and no client
timer ([ADR 0009](decisions/0009-auth-implementation.md)).
