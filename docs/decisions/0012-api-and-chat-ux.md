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
it arrives — the live trace IS the transport, not a replay. ("Each event" was
too broad for `node_start`, which is transport and audit only — see **What the
trace renders** below, which is binding.)

**Reasoning is content, not a label (amended after issue #67).** The live pass
showed the trace naming a step "Reasoning" while the model's actual reasoning
went nowhere: a thinking model's `<think>` text landed in the answer body as
ordinary `token` text, so the reader got the thinking and the answer as one
undifferentiated block and the label above it was a frontend invention.

- **`reasoning` with `{text}` is a binding event.** It carries the model's own
  thinking as it arrives, appended to the current trace step, streamed live,
  and never part of the answer body. It is trace content in the same sense a
  tool call is: never written to the graph's message history — and, since issue
  #90, kept per model round in the turn history a reopened thread replays (see
  **Full-fidelity turn history** below, which replaces the session-only rule
  this bullet originally carried).
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

**`done` says whether a tool grounded the answer (added with issue #94).** The
frame carries `grounded` beside `status`: whether any tool of that turn returned
a result the answer could rest on. It is a field of the terminal frame rather
than an event of its own because it is a property of the finished turn, and the
turn already has one frame for those. The SPA renders `ok` plus not-grounded as
a warn pill, "answered without querying the data", beside the answer; a turn
that ended some other way already carries a pill saying so. Since issue #90 the
terminal frame is stored with the turn, so a replayed turn reports its
groundedness too; a turn whose frame was not kept still claims nothing about it.
The mechanism behind the flag - one grounding nudge per turn - is ADR 0011's,
and it is answer quality, never a security layer.

**`done` says whether the turn's memory was sent whole (added with issue #131).**
The frame carries `history_trimmed`: whether a thread too long for the context
window had its oldest turns left out of what this turn's model calls were sent
(the mechanism and its knobs are ADR 0011's). It rides the terminal frame for
the same reason `grounded` does — it is a property of the finished turn, not a
moment the reader watches — and it is a boolean, because what a reader needs is
whether this answer had the whole conversation behind it; how many turns were
left out goes to the server log. Two things it does not mean: nothing was
trimmed from storage (the checkpointer keeps every message and replay still
serves the whole thread), and the turn is not `cut_short` — no bound ended it,
the answer is complete, it simply had a shorter memory. The SPA treatment
follows the same honesty pattern as the truncation chip and the not-grounded
pill; a client that does not read the field is unaffected, since every trace
event is JSON parsed by name and never by shape.

**Two termination invariants (amended after issue #66).** The live pass showed
what their absence costs: one tool raising an unexpected exception killed the
response mid-flight, leaving every announced step at "running" forever and the
SPA inventing its own explanation for a failure it was never told about.

- **Every `tool_call` id is closed by exactly one of `tool_result`, `retry` or
  `security_event`.** The agent guarantees it by answering for every call it
  announced, whatever the tool did (ADR 0011 as amended), so no step can be
  left running and no outcome can be attributed to the wrong call.
- **Every stream ends in exactly one `done` frame.** Its `status` vocabulary is
  binding: `ok | blocked | gave_up | cut_short | failed` (`cut_short` added with
  issue #83). The agent composes the first four; `failed` is the API layer's own
  terminal frame for a run that broke before the graph could answer — an
  unreachable model endpoint — with the reason in `answer` and the exception left
  in the server log, never on the wire (OWASP generic-error guidance). A body
  that simply stops is therefore a client-side error path, not a server behavior
  the SPA must model.

**`cut_short`: a turn one of its own bounds stopped (added with issue #83).**
A per-turn wall-clock deadline or the tool-round cap of ADR 0011 ended the turn.
It is deliberately not `failed`: nothing broke, the server is not in an unknown
state, and the turn may well carry a partial answer — a runaway generation is cut
after the words it already streamed, and those tokens stay the answer with the
notice appended (streamed as its own token, so the reader sees it arrive, and
persisted, so a reopened thread still explains why the answer stops
mid-sentence). It is not `gave_up` either: no retry budget was spent and the
model made no error. The SPA renders it as a warn pill, "stopped at its turn
limit", beside the answer it did get.

### Conversations: full history sidebar, tenant-scoped

- Conversations persist server-side: a registry table (thread_id, user,
  tenant, title, created) plus LangGraph's SQLite checkpointer for state.
  A thread is created titled with its first user message, truncated, and is
  then retitled by the model while it is young (see **Generated titles**
  below). The row also carries whether the reader renamed it themselves.
- Endpoints: `GET /conversations` (list, JWT-scoped), `POST /conversations`
  (new thread), `GET /conversations/{id}` (history replay),
  `PATCH /conversations/{id}` (the reader's own rename with a title in the
  body, or a generated one when the body carries none),
  `DELETE /conversations/{id}`. Every access verifies the thread belongs to
  the authenticated user+tenant — the conversation store is a fifth
  tenant-scoped data path under the same identity layer (ADR 0002 L1). The
  registry's rename is scoped by the same `sub` + `tenant_id` clause as every
  read and the delete, so a rename aimed at another identity's thread changes
  nothing and answers with the same `NotFound` as a thread that never existed.
- Replay serves what was said: the user's questions and the assistant's text,
  read back from the checkpointer in order. Reopening a thread restores the
  conversation the server still remembers, never a re-run of the reasoning
  behind it. (This bullet originally put every tool-call internal out of
  replay; issue #70 let the tool evidence back in, and issue #90 the rest of the
  turn — see **Full-fidelity turn history** below, which is binding.)
- **Amended after issue #66**: the text of an assistant turn that also asked
  for tools is part of what was said and is replayed. Dropping the whole
  message hid every partial and failed turn from the transcript while it stayed
  in the graph's memory — so the model could refer to a turn the reader could
  not see, which is exactly the information gap the model then filled with a
  confident, false explanation. The calls themselves stay out; only the words do.

### Full-fidelity turn history (amended after issue #90, replacing the session-only rule)

Issue #70 stored the tool evidence and left the thinking session-only, on the
argument that model output is worth watching once and not worth re-serving as
record. The owner's correction: for **this** product that was the wrong call.
The claim this demo makes is auditability of a row-level-security boundary, and
the interesting part of a past conversation is exactly what the agent tried,
what got rewritten, what got retried and what got refused — not the tidy answer
at the end. A reviewer who reopens a thread and sees only questions, answers and
tables is being shown the part that needs the least trust. So the split of the
previous amendment is replaced: **the whole turn is persisted and replayed.**

- **What is kept, per turn, keyed by thread and turn ordinal.** The ADR 0012
  trace events the turn produced, in the order they happened: the model's
  reasoning **concatenated per model round** (one live turn produced 1175
  `reasoning` frames — the round's text is the unit a reader reads, and the
  frame is not), every `tool_call` with the arguments the model wrote, the one
  outcome that settled each of them (`tool_result` with its server-produced
  payload, `retry` with its reason and attempt number, or `security_event` with
  the layer and kind that fired), and the terminal `done` frame with the turn's
  status, its token and duration telemetry, its groundedness, whether its memory
  had to be trimmed to be sent, and the prompt-guardrail position that produced
  it (ADR 0011 as amended). The
  `reason`-node `node_start` events are kept because they are what groups the
  reasoning into rounds; the other nodes render nothing, so they are transport
  only, as the amendment after issue #87 already said.
- **What is not kept.** `token` frames: the answer is already in the
  checkpointer's transcript and whole in the terminal frame, so a third copy
  would be the largest thing stored and would replay nothing new. The
  model-facing rendering of a tool result (the pipe-separated table the tool
  returned to the model) for the same reason — it is a second copy of the rows,
  for a reader who is no longer there.
- **Where it lives, and why not the audit log.** In `conversations.py`'s
  `state.db`, one row per turn beside the thread rows — the same documented
  exception to "only `db.py` opens a connection" that the registry already is.
  Not in `audit.db`: that store is `db.py`'s record of tenant-**data** queries,
  written by the executor for every query approved or refused, and a turn record
  is mostly not data queries at all (reasoning, retries, a refusal that never
  reached the database, a terminal frame). Making the data-access auditor the
  owner of application history would put two unrelated retention rules, two
  scoping stories and two schemas in one file. A third database was rejected for
  the opposite reason: this is conversation state, and the conversation store
  already exists.
- **Scoped and non-disclosing, unchanged.** Every history row carries the `sub`
  and `tenant_id` that produced it, and both the read and the write are filtered
  by both (ADR 0002 layer 1): a write aimed at another identity's thread stores
  nothing and raises nothing, a read of one returns nothing, and the endpoint
  asks the registry for the thread first, so a foreign id is the same 404 that
  reads neither transcript nor history.
- **Model output is data on the way in and text on the way out.** Stored tool
  arguments are what the model wrote; nothing re-executes them on replay, and the
  SPA renders them as text nodes exactly as it renders a note or a generated
  title (OWASP LLM05). A replayed turn is a rendering of a record, never a
  re-run.
- **Bounded, and the bound is stated rather than hidden.** Per turn: at most
  `conversations.max_turn_events` events are kept, at most
  `conversations.max_turn_payloads` tool results keep their `data` (set to the
  tool-round cap `agent.max_tool_iterations`), the row-shaped lists inside a
  payload are cut to the executor's own `db.max_result_rows` (ADR 0007), and one
  round's thinking is cut to `conversations.max_reasoning_chars`. Per thread:
  only the newest `conversations.max_history_turns` turns keep their history, and
  an older turn replays as text, exactly as it did before any of this was stored.
  The terminal frame is kept whatever else a cap refused, because a turn that
  cannot say how it ended is the one thing a history must not be. Every piece a
  cap refuses is **counted** and served with the turn, and the SPA states it on a
  pill; a truncated round says so on its own step. A capped history that read as
  a whole one would be worse than no history.
- **Written from the framing, so the record is what the reader was sent.** The
  turn log is offered every frame `_sse` puts on the wire — the API's own
  `failed` terminal frame included — and is flushed once the stream is over, so
  it costs the stream one append per frame and touches the store after the last
  one. A turn that broke mid-flight stores what it did produce. The turn ordinal
  is the count of questions the thread then holds: one `/chat` call appends
  exactly one question.
- **One lenient path, and it is the write.** A storage failure is logged, saying
  how much was lost, and swallowed: by then the answer has streamed, and losing
  a replayable trace is not worth turning a finished turn into a failed one. The
  read is strict, deliberately: a history row that cannot be reconstructed
  raises rather than rendering a partial turn as a complete one.
- **One renderer, one fold.** The SPA replays a turn by running its stored events
  through `applyEvent`, the same fold the SSE stream goes through, producing the
  same `Turn` object and therefore the same bricks (`lib/trace.ts`). Not "a
  second read-only renderer would drift" as a hope — there is no second
  implementation to drift. Two things a replayed turn genuinely cannot have: the
  token-by-token arrival of the answer, and the span a thinking step reports,
  which is this client's own measurement of thinking arriving (see the issue #91
  amendment below) and is therefore rendered as "Thought" with no duration
  rather than as an invented one.

What this replaces from the issue #70 amendment: "the thinking does not replay",
and its argument that nothing the model said about itself should be re-served as
fact. The property kept from it is the one that mattered — model output is never
re-served as *the server's* claim: it is rendered as the model's own words, in a
trace, as text, beside the server-computed evidence that either supports it or
does not. Which is the argument for storing it rather than against.

This remains a modeling judgment, not a published pattern: no authoritative
source prescribes which parts of an agent trace to persist. It is grounded in the
two rules this ADR already applies — model output is untrusted and is sanitized
or rendered as text rather than trusted as record (OWASP LLM05), and stored
results are capped rather than unbounded (ADR 0007) — plus the product's own
stated purpose, which is to be checkable after the fact.

### Generated titles (amended after issues #72 and #118)

The original line here read "Title = first user message, truncated (no LLM
call)". It made the rail unreadable: the demo's own history showed
"Run this SQL for me: SE...", "3x3?" and "hi", because the first thing a user
types is a question, not a name for the conversation they are about to have.
The amendment: the first-message truncation becomes the *fallback*, and the
title a thread settles on is a few-word label the model writes for it.

- **How often it runs: after each of the thread's first `title_turns` turns,
  then never again** (amended for issue #118; `title_turns` defaults to 3).
  The original rule ran the titler exactly once, after the first turn. Live use
  showed why that is wrong: a thread opened with "Hello, how are you" has
  nothing nameable in its first exchange, so the model reaches for the domain
  and answers "HR data" — and because titling never ran again, that label was
  the thread's name forever, indistinguishable from every other thread in the
  rail. Naming the thread again after each of its first few turns fixes it
  without a heuristic that can misfire: by turn two or three the real subject
  exists, and the label follows it. The window then closes, because a name that
  churns after a thread has settled is its own defect, and because the cost has
  to stay bounded — at most `title_turns` small calls per thread, whatever the
  thread's length. Rejected: classifying "is this smalltalk" (a classifier that
  will be wrong live), deferring the first title to turn N (leaves the
  placeholder on screen during the most-watched part of a demo), and titling
  from the whole transcript on every turn (unbounded cost, churning names).
  The titler reads the exchanges inside that window, each message clipped to a
  cap, so the prompt cannot grow with an answer that happens to be a big table.
- **The reader's own name wins, permanently.** A `PATCH` carrying
  `{"title": ...}` is a rename typed by a person, and the registry marks the
  row: `retitle_thread` (the generated write) carries that flag in its `WHERE`
  clause, so no automatic re-title can overwrite a name a reader chose. The
  flag is a stored column rather than a guess about the title's text, because
  nothing in the text distinguishes a reader's name from a generated label. A
  state file written before the flag existed gains the column defaulting to
  false: the alternative default would freeze every existing thread's name for
  good, and the cost of this one is at most one re-title of a thread that had
  been renamed before the upgrade.
- **A failed titling call leaves the standing name.** With the titler running
  more than once, the old fallback (the first user message) would have let a
  timeout on turn two destroy the good label turn one produced. So the fallback
  is the title the thread already has; the first question is used only while
  the thread still carries the unnamed placeholder, which is the case #72 was
  about.
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
  when it gives a usable one, the title the thread already has when the call
  raises or returns junk or when there is nothing to name yet (a turn that
  broke before the checkpointer stored anything), and the first question when
  the thread is still unnamed. So the
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
- **Thinking is switched off for the titling call.** Ollama enables it by
  default on a model that supports one, and measured against this project's
  configured model a titling call then spent 245-918 generation tokens
  reasoning its way to a six-word label — 7 to 17 seconds per call, which is
  what produced every timed-out titling call observed in real use (the
  measurement is in issue #118). A label needs no reasoning, so the request
  says `think: false`; the endpoint rejects only thinking asked of a model that
  cannot do it, never thinking declined. Since the same measurement put a
  no-thinking call as high as 15.9s under load, and re-titling makes the call up
  to `title_turns` times per thread, `title_timeout_s` moved from 20s to 30s.
- **The titling call is given nothing to abuse.** It sees the young thread's
  exchanges, with no
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
  otherwise the request is rejected. Absent, the default is resolved from that
  same live list — `runtime.json` `agent.model` when the endpoint serves it, and
  otherwise the served chat id that sorts first (ADR 0005 as amended, issue
  #111). `GET /models`'s `default` is that resolved id, so the field the picker
  preselects is the field the turn's `done.model` reports back; an endpoint
  serving no chat-capable model is a 502 on both routes.
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
- **Bound visibility (added with issue #83)**: a `cut_short` turn says so on a
  pill and in the notice appended to its answer, naming which bound it hit. A
  resource limit the product chose is stated as such, never rendered as a
  failure or as an answer that merely happens to stop.
- **Groundedness visibility (added with issue #94)**: an answered turn that no
  tool result of its own stands behind says so on a warn pill. An analyst tool
  that shows its SQL, its rows and its refusals should not quietly hide the one
  case where there was nothing to show.
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

### What the trace renders: node events are transport, not rows (amended after issue #87)

The original decision above listed `node_start` beside `token` and `tool_call`
as an event type "rendered as it arrives", and the SPA took that literally: one
row per LangGraph node, its name mapped to invented prose. The live pass showed
what that reads like — "Validating the tool call" *above* the calls, "Running
the tool" *below* them, then "Auditing the outcome" and "Composing the answer".
The order was faithful to the graph (the calls are announced in `validate` and
settled in `audit`, with `execute_tool` between them contributing only its own
`node_start`) and meaningless to a reader, who was being shown our internal
mechanics instead of the analysis.

- **`node_start` stays in the SSE contract, and stays out of the UI.** It is
  cheap, it mirrors the audit log, and it is what tells the frontend which model
  round the reasoning and the calls that follow belong to. It produces no row,
  no label and no chrome of its own, and the label map that turned node names
  into user-facing prose is deleted. Transport and audit, not presentation.
- **The rendered trace is reader-meaningful entries only**: reasoning steps with
  the model's streamed thinking, tool cards (name, arguments, generated versus
  executed SQL, the result table or chart, the truncation chip) each carrying
  its **own** pending → settled/retried/refused state, retry entries and
  security events. A reader of a data-analyst trace wants what the model
  thought, what it called, what ran, what came back, and what was retried or
  refused; the graph's shape is an implementation detail of that.
- **Reasoning is grouped per model round.** A turn calls the model once per tool
  round, so consecutive `reasoning` chunks inside one round are one step however
  many frames they arrived in, and the round *after* the tool results is its own
  step — legitimate and worth seeing, and chipped with its round number so which
  is which is stated rather than inferred. The round counter is the count of
  `reason` entries the fold has seen; no new event field was needed, because the
  event that marks a new model call already exists.
- **No empty chrome.** A step with no content does not exist: a round that
  streamed no thinking is not an empty "Reasoning" row, and a turn whose only
  events were node transitions renders no panel at all.
- Everything the previous amendments established is unchanged by this one: the
  trace still precedes the answer, each step is still its own `aria-expanded`
  disclosure with reasoning collapsed by default, the terminal frame still
  reports what the turn cost, and a replayed turn still renders its stored
  evidence through these same bricks (one renderer, not two).

### The rewrite marked in place, and thinking that says how long it took (amended after issue #91)

A review pass over beautifului.dev, a pattern library for AI-native interfaces,
against these screens (the adopt/reject table is
[docs/ui-pattern-review.md](../ui-pattern-review.md)). Two of its patterns
changed decisions taken above; everything else was rejected there with a reason.

- **The generated/executed pair carries the rewrite marked inside the executed
  card** (as amended again after issue #121 — the original text of this bullet
  put the pair one click behind a `show both` toggle and showed the marked
  statement alone; see the amendment at the end of this ADR). The executed
  statement *is* the generated one plus what layer 3 wrapped around every
  `employees` reference, and a bare pair of adjacent columns asked the reader to
  diff two 130-character statements by eye — which at demo distance does not
  happen. `lib/sqldiff.ts` aligns the two token streams and `SqlRewrite` paints
  the result: the tenant predicate and its bound parameter are marked, the
  model's own words are not. Four properties earn their code:
  - The diff is **token-level and case-insensitive**, because sqlglot renders the
    scoped tree onto one flat line with uppercased keywords. A line diff reports
    the whole statement as changed and communicates nothing.
  - It minimises the number of edit **runs**, not the number of edited tokens
    (an affine gap cost — Gotoh 1982). The injected subquery repeats the words
    around it, so the alignment with the most matched tokens strands the model's
    own words inside the insertion and renders the rewrite as confetti.
  - **An alias belongs to the `AS` that introduced it.** The rewrite spells its
    alias like the table the model wrote, so the cheapest alignment accounts for
    the alias with that token and ends the highlight on a dangling `AS`. One
    pass after the alignment hands the alias back to the insertion.
  - Nothing is hidden: a legend states what the highlight means (colour is never
    the only signal, WCAG 1.4.1) and the copy control writes plain SQL rather
    than the markup. A statement too long to align renders as the plain pair.
- **A thinking step is open while its thinking arrives and folds itself away when
  it settles**, leaving `Thought for 2.8s` where the label was. The disclosure
  decision above ("reasoning starts collapsed") was right about the settled state
  and wrong about the live one: a reader watching a turn happen was shown a
  closed row and a spinner while the interesting part streamed behind it. The
  span is measured by **this client**, between the round's first reasoning chunk
  and whatever the turn did next — the stream carries no timestamps, and the
  measurement is honest about being the client's own. The reader's click still
  wins from then on, so `TraceStep`'s `open` became the state a step is in rather
  than the one it mounted in. A replayed round does carry its thinking (issue
  #90) but no span: it never arrived here, so the step reads "Thought" and states
  no duration rather than reporting a measurement nobody took.

### Both SQL cards always, no toggle (amended after issue #121)

The amendment above put the marked statement on screen and the generated/executed
pair one click behind `show both`. Live use said that is one reading too few. The
pair is the **before and after of the security boundary** — what the model asked
for, what the database was given — and the highlight is **where inside the
statement the boundary landed**. A demo needs both sentences at once, and a
toggle nobody clicks mid-demo meant the screen never showed what the model had
actually written. Reversed: **both cards render unconditionally, with the scoping
highlighted inside the executed one, and there is no toggle**. Everything else
about the marking is unchanged; three things follow.

- **The executed card shows the executed statement and nothing else.**
  `lib/sqldiff.ts` no longer emits the struck-through `del` segments the
  single-statement mode needed so as not to hide a replaced stretch: the
  generated card is now beside it, verbatim, which says strictly more. Its
  segments therefore concatenate back to the executed statement exactly, which
  is asserted in `lib/sqldiff.test.ts`.
- **The pair stacks below 700px of its OWN width**, executed card second so
  "what ran" is the one touching the result table. 700px is two legible columns:
  45 monospace characters at `--text-xs` is 325px of code, plus each card's
  padding and border, twice, plus the gap. It is a **container** query, not a
  media query, because collapsing the conversation rail hands the pair ~200px at
  an unchanged viewport — measured, at a 960px viewport the pair stacks with the
  rail open (540px available) and sits side by side with it collapsed (751px), so
  a viewport breakpoint would be wrong in both directions. Container queries are
  Baseline 2023 and older than the `color-mix()` the tokens already require.
- **The highlight does not rely on colour** (WCAG 1.4.1): an accent tint, a
  heavier weight, and a solid rule under every wrapped fragment
  (`box-decoration-break: clone`, because the marked run wraps), plus the legend
  naming it. Verified by screenshot with the whole page under
  `filter: grayscale(1)`.

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
- Ollama, `/api/chat` `think` parameter, and its server-side validation showing
  that only thinking *asked of* a model that cannot think is refused
  (`server/routes.go`) — the basis for declining thinking on the titling call —
  https://docs.ollama.com/api and
  https://github.com/ollama/ollama/blob/main/server/routes.go
- beautifului.dev, a free MIT-licensed pattern library for AI-native interfaces
  (Turbo / Shane Levine) — the source of the mark-the-edit-in-place and
  thinking-summary patterns adopted above; no code taken —
  https://www.beautifului.dev (license: https://www.beautifului.dev/license)
- O. Gotoh, "An improved algorithm for matching biological sequences", Journal of
  Molecular Biology 162(3):705-708, 1982 — the affine gap cost that makes the SQL
  alignment prefer whole runs — https://doi.org/10.1016/0022-2836(82)90398-9
- WHATWG HTML, the `mark` element — the semantics the highlight renders with, so
  the marking is not colour-only in the DOM either —
  https://html.spec.whatwg.org/multipage/text-level-semantics.html#the-mark-element
- CSS Containment Module Level 3, `container-type` and `@container` — the pair's
  breakpoint measured against its own inline size rather than the viewport's —
  https://drafts.csswg.org/css-contain-3/
- CSS Fragmentation, `box-decoration-break: clone` — why every wrapped fragment
  of the marked run keeps its own rule —
  https://drafts.csswg.org/css-break/#break-decoration
- The 700px stacking threshold is an engineering judgment, not a cited number:
  no standard gives a minimum measure for code. It is derived from a 45-character
  floor per column (the low end of the 45-75 character measure that typographic
  practice recommends for continuous text) at the measured 7.22px advance of
  Geist Mono at `--text-xs`.
- WCAG 2.2, Understanding SC 1.4.1 Use of Color — why the highlight carries a
  legend — https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html
- Media Queries Level 5, `prefers-reduced-motion` — the guard on the shimmering
  live label — https://www.w3.org/TR/mediaqueries-5/#prefers-reduced-motion
