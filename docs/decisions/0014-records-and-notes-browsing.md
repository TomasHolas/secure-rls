# ADR 0014 — Records and Notes tabs: browsing the tenant's own data without a second data path

Status: accepted

## Context

Until now the app was one screen. A reader could ask the agent "how many
employees do I have?" and had to take both the answer and the isolation on
faith: the only window onto the data was the model's account of it. The demo
claim — four independent RLS layers, no tenant can reach another's rows — was
therefore argued rather than shown.

Two tabs beside the chat change that. **Records** lists the signed-in tenant's
employee rows with filters, sorting and paging; **Notes** lists the note corpus
the agent retrieves over and runs the agent's own retrieval for a typed query.
Sign in as `acme` and the total is 450; sign in as `beta` and the same request
answers 350, with entirely different rows and notes. The Notes tab additionally
lets the demo point at a planted injection payload in the corpus *before* asking
the agent something that retrieves it, which turns the second-order prompt
injection story (OWASP LLM01) from an assertion into a before-and-after.

The risk this creates is the whole reason the ADR exists. A browse UI wants
exactly the things that break tenant isolation: a column name in `ORDER BY`, a
substring in `LIKE`, a page window, eight optional filters. Each is a place
where a value could become SQL. If the tabs grew their own query path, they
would become the weakest link and the four layers behind the agent would prove
nothing.

## Decision

**One data path, no exceptions.** A new brick, `apps/backend/browse.py`, owns
two fixed query templates built from the sqlglot AST and executed by
`db.execute_scoped` — the same validator (layer 2), scoping rewrite (layer 3),
structural check (layer 4a), read-only connection and authorizer (layer 2.5),
egress row check (layer 4b), row cap and audit log every agent tool goes through
(ADRs 0002, 0003, 0007). `browse.py` opens no connection, so the grep-guard that
only `db.py` and `conversations.py` call `sqlite3.connect` still holds.

The split between what may be *bound* and what must be *allowlisted* follows the
OWASP SQL Injection Prevention Cheat Sheet, which states the rule plainly: bind
parameters for values, and where the SQL grammar does not allow a bind — table
and column names, `ORDER BY` — validate against an allow-list of known-safe
strings.

1. **Filter values are bound.** Eight allowlisted comparisons (name substring,
   department equality, ranges on salary, performance score and hire date), each
   a placeholder whose value travels beside the statement. Nothing a reader
   types is ever rendered into SQL text.
2. **Sort column and direction are allowlisted words**, checked by
   `security.require_allowed` before they become AST nodes, and refused
   terminally — there is no model to correct a query string.
3. **The name filter is `INSTR(LOWER(name), LOWER(?)) > 0`, not a `LIKE`.**
   A substring search is what the box promises; SQLite's `LIKE` would give a
   typed `%` or `_` wildcard meaning the reader never asked for, and escaping it
   correctly is a second thing to get wrong. `instr` compares text, full stop.
4. **The page ceiling is the executor's row cap** (`db.max_result_rows`, ADR
   0007), because a page larger than the cap could not be served whole. A
   request beyond it is clamped and the response reports the page size it
   actually used, so the clamp is stated rather than silent. The **true total**
   is a second scoped query — `COUNT(*)` over the same bound filters — which is
   what makes "450 rows" a fact about the tenant's data rather than about the
   page in hand.
5. **Both templates select `tenant_id`.** It costs nothing, it puts the egress
   check on this path too, and every row a reader sees then carries the tenant
   it came from, which is the demo's point in the data itself.
6. **The Notes search is not a second search.** It delegates to
   `rag.search_notes_scoped` (ADR 0010) — the partition-key pre-filtered KNN the
   `search_notes` tool calls — with `rag.top_k` as the default hit count, and
   surfaces the distance. What the reader sees is literally what the model would
   have been handed.
7. **Ordering is deterministic.** Every page orders by the requested column and
   then by the `user_id` primary key. Offset paging over a non-unique sort is
   the classic way to make rows repeat or vanish between pages (Winand); the
   tie-break removes the possibility rather than making it unlikely.
8. **Identity is unchanged.** Every endpoint requires a JWT and takes the tenant
   from it. `browse.Filters` is the query-parameter allowlist, so a
   `?tenant_id=beta` a client invents is not a field of it and is not read at
   all — the same property `ChatRequest` has for the body (ADR 0012).

**Endpoints** (all JWT-required, all thin handlers over `browse.py`):
`GET /records`, `GET /records/departments`, `GET /notes`, `GET /notes/search`,
`GET /notes/flagged`. A refusal is an honest status: 400 carrying the allowlist's
own reason, 503 when no note index exists, and a bare logged 403 if an RLS layer
ever trips — which on this path would mean one of our own templates is broken.

**The poison manifest is surfaced deliberately.** `poisoned_manifest.json` is
generated with the dataset and committed, so marking the rows it plants tells a
reader nothing the repo does not already say out loud. It is filtered to the
caller's tenant regardless, and it is repo metadata rather than tenant data.

**Frontend.** The tabs are a shell brick (`components/layout/Tabs.tsx`) in the
header, not a view's furniture; the conversation rail belongs to the chat and is
passed to the shell only while the chat is open. A tab the reader has opened
stays **mounted and hidden** rather than unmounted, so switching away and back
cannot cost them a streamed transcript, a typed filter or a search they ran; a
tab never opened is not mounted, so nothing fetches rows for a tab nobody asked
for. The views compose existing bricks (`DataTable`, `Pill`, `CodeBlock`,
`EmptyState`, the form kit); three bricks are new or extended, and the catalogue
in `src/components/README.md` carries them. Sorting is server-side: a header
click is a request, never an in-browser reorder of one page — which is the only
honest thing a table holding page 3 of 18 can do.

## Consequences

- The isolation claim becomes checkable without trusting the agent, and the row
  counts the tabs show can be compared against the answers the agent gives.
- The tabs cannot become a weaker path than the agent's: they have no path of
  their own. A filter that cannot be expressed as an allowlisted template with
  bound parameters does not ship.
- Two scoped queries per page (the window and the count) instead of one. At this
  scale the count is trivial, and the alternative is a total that describes the
  page rather than the data.
- The audit log now also records what readers browsed, not only what the model
  asked. That is a feature: the trail is of data access, whoever caused it.
- A demo that shows a planted payload before the agent reads it needs no
  narration to explain what second-order injection means.

## Alternatives

- **A generic filter DSL from the client** (`?where=salary>100`) — the
  parameterization rule and the whole point of layer 2 say no; it is
  user-supplied SQL by another name.
- **Interpolating an allowlisted column into the `WHERE` text** rather than
  binding the value — safe only for as long as the allowlist is perfect, and it
  makes the placeholder count layer 4a checks unpredictable.
- **A `LIKE '%x%'` name filter** — simpler to write, but it silently hands the
  reader wildcard semantics and needs `ESCAPE` handling to avoid it.
- **Client-side filtering and sorting over a full download** — would need the
  row cap lifted, ships every row to the browser, and makes the page a lie about
  what the server holds.
- **Reusing the agent's `query_db` tool from the UI** (have the tab send SQL) —
  puts generated-SQL machinery on a path that has no model in it, for no gain
  over a fixed template.
- **Unmounting a tab on switch** — simplest React, but a reader who checks a row
  count mid-conversation would come back to an empty chat.

## Sources

- OWASP Cheat Sheet Series, *SQL Injection Prevention* — bind parameters for
  values; allow-list validation for table/column names and `ORDER BY`, which
  cannot be parameterized:
  https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html
- OWASP Cheat Sheet Series, *Query Parameterization*:
  https://cheatsheetseries.owasp.org/cheatsheets/Query_Parameterization_Cheat_Sheet.html
- OWASP Top 10 for LLM Applications (LLM01 prompt injection, including indirect
  injection through retrieved content): https://genai.owasp.org/llm-top-10/
- SQLite documentation, `LIKE`/`GLOB` wildcard semantics and the `ESCAPE` clause:
  https://sqlite.org/lang_expr.html
- SQLite documentation, core functions (`instr`, `lower`):
  https://sqlite.org/lang_corefunc.html
- Markus Winand, *SQL Performance Explained* / use-the-index-luke, "Fetching the
  next page" — offset paging needs a deterministic (tie-broken) sort order or
  rows repeat and vanish:
  https://use-the-index-luke.com/sql/partial-results/fetch-next-page
- ADR 0002 (the layers), ADR 0007 (the row cap and truncation honesty), ADR 0010
  (the retrieval path this tab reuses), ADR 0012 (identity from the token, thin
  handlers), ADR 0006 (the design system the views compose).

The judgment calls, labelled as such because no external source settles them:
which eight filters to allowlist, showing the executed SQL under the table,
surfacing the committed poison manifest in the UI, and keeping a visited tab
mounted rather than unmounting it.
