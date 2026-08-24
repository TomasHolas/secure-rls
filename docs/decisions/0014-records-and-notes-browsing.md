# ADR 0014 — Records and Notes tabs: browsing the tenant's own data without a second data path

Status: accepted

## Context

Until now the app was one screen. A reader could ask the agent "how many
employees do I have?" and had to take both the answer and the isolation on
faith: the only window onto the data was the model's account of it. The demo
claim — layered RLS enforcement, no tenant can reach another's rows — was
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
would become the weakest link and the layers behind the agent would prove
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

## Amendment (after issue #103): a note card carries what it is verified against

The Notes tab shipped showing a name, a `#user_id` and the note text. `GET /notes` already
served the department; the frontend mapped three columns and dropped it before render. A
verification surface that omits the field a reader would verify against defeats its own purpose
— "the mentoring notes in Engineering" cannot be checked on a card that never says Engineering.

- **The card shows the fields a retrieval claim is checked against**: the employee, their
  `department`, their `performance_score`, the `tenant_id` of the row, and the `distance` when
  retrieval produced one. The score earns its place from ADR 0008: the notes are composed from
  clause pools disjoint across score bands, so tone against number is a coherence a reader can
  falsify at a glance. The tenant earns its place from point 5 above — the row carrying its own
  tenant is the demo's point in the data — and it is what makes the login-switch comparison
  visible on the card itself rather than only in the header badge.
- **`salary` and `hire_date` are deliberately absent.** Neither helps decide whether a text hit
  is the right one, and a verification surface that shows everything verifies nothing in
  particular. The omission is stated in `browse.py` beside the column tuple, so the next reader
  does not have to guess whether it was a decision.
- **The corpus listing gains the column; the search hits gain a scoped lookup.** The vec0 store
  holds what was embedded plus the identity of its row (ADR 0010) — deliberately, since its
  fingerprint covers every field it serves, and storing a department there would make a
  department change re-embed the corpus. So `browse.annotate_note_hits` reads the tenant,
  department and score of the hit rows from `employees` through a third fixed template
  (`user_id IN (?, …)`, the ids bound) down the same `db.execute_scoped` path. The retrieval is
  untouched: the hits, their order and their distances remain literally what the `search_notes`
  tool returned, and the annotation cannot describe a row the caller's tenant cannot see —
  a foreign id simply matches nothing.
- **One card shape everywhere.** The `NoteList` brick renders every one of these fields when its
  caller has it, so the chat trace, the corpus listing and the search hits stay the same card.

## Amendment (after issue #107): a listing says what it did not read, and the reader gets to try

There is deliberately no tenant filter, and there will not be one: a caller holds exactly one
tenant, and a control offering to pick one would advertise a capability the architecture does not
have. That part was right. What was wrong was the silence around it. As `alice@acme`,
`GET /records?tenant_id=beta` returned acme's 450 rows and said nothing at all about the
parameter it discarded, and `?tenant=beta` behaved the same. A reader cannot tell "the parameter
was refused" from "the other tenant happens to hold the same rows" - which is precisely the
ambiguity a skeptical reviewer presses on, and the one this whole surface exists to remove.
Meanwhile a *known* filter with a bad value already answers honestly: `sort=notes` is a 400
naming the allowlist that refused it. An unknown parameter deserved the same honesty.

- **Still a 200, with the caller's own rows.** RFC 9110 defines 400 as the status for a request
  the server "cannot or will not process" - a stray query parameter does not prevent serving the
  page, and a browsing surface that breaks over one is a worse product than one that keeps
  answering. JSON:API takes the stricter line for its own document protocol (an unrecognized
  query parameter MUST be 400); we deviate deliberately, because the value here is that the page
  survives the attempt and can therefore *show* the attempt failing.
- **The response reports what it did not read.** `browse.ignored_params` turns the request's raw
  parameter names into an `ignored` list on `BrowsePage`, one entry per name the listing does not
  read, each with the reason. The rows, the total, the sort and the executed statement are
  untouched - the report is about the request, not the data.
- **Names only, never values.** The report never echoes what a parameter carried. A response that
  repeated `beta` back would put a tenant name the server never accepted into the server's own
  output, and the property that no foreign tenant string appears in a response is one the suite
  asserts. The name came from the reader's own request; the value is not repeated for them.
- **`tenant_id` and `tenant` get their own sentence**, not a generic "unknown parameter": the
  reason they cannot be parameters *is* the security claim (ADR 0002, layer 1 - the tenant is read
  from the verified token and reaches the query by closure, so there is no request that can name
  one), and this is the one place a reader is holding the keyboard when they need to read it. Any
  casing of those two names earns that sentence, since no casing of either is accepted anywhere;
  everything else is matched exactly, which is how the framework itself matches a parameter name.
- **Nothing is added to the allowlist, and no refusal is softened.** The allowlist runs first and
  terminally: a sort outside `SORT_COLUMNS`, a direction that is not one, a date that is not a
  date, an over-long filter are the same 400s they were, even when the same request also carries
  a tenant parameter. This amendment makes a refusal audible; it does not make one negotiable.

**The interactive affordance: a parameter box, not a tenant button.** Issue #107 floated a
deliberate "try to reach another tenant" control. Rejected, in that shape. A control named after
another tenant implies the UI could select one and the server merely declines - it frames layer 1
as a policy that could be relaxed rather than an input that does not exist, and it is the one
thing this surface must not imply. It is also unconvincing: an outcome a button hard-codes is
indistinguishable from a canned message, and a skeptic is right not to believe it.

An always-on notice alone was the other option, and it is not sufficient on its own: nothing the
SPA sends can be unaccepted, so the notice would be a component that never renders - dead code by
this repo's own rule, and a demo that still requires curl to see the point.

So the tab carries one control that is not a filter: a box that appends a query parameter of the
reader's own choosing to the next request, and the notice that reports what came back. It implies
nothing, because a query parameter is what an HTTP request already is; it lets a viewer type the
attack themselves instead of watching a canned one; and it makes the notice a live surface rather
than an unreachable one. It is labelled as a demonstration ("Attack it yourself"), and the box
says in words what it is not: not a filter, and not a tenant picker. Typing `tenant_id=beta`
returns the same 450 acme rows, the same total, and the server's own sentence about why no request
can name a tenant. Typing `sort=notes` still gets the 400. Both tabs carry it, because both
listings take the same filters and owe a reader the same account of them.

## Consequences

- The isolation claim becomes checkable without trusting the agent, and the row
  counts the tabs show can be compared against the answers the agent gives.
- The tabs cannot become a weaker path than the agent's: they have no path of
  their own. A filter that cannot be expressed as an allowlisted template with
  bound parameters does not ship.
- Two scoped queries per page (the window and the count) instead of one. At this
  scale the count is trivial, and the alternative is a total that describes the
  page rather than the data.
- A reader can now attempt the attack themselves and read the refusal, so the
  isolation claim survives the one question the tabs could not answer before:
  was that parameter refused, or did it simply not matter?
- The audit log now also records what readers browsed, not only what the model
  asked. That is a feature: the trail is of data access, whoever caused it.
- A demo that shows a planted payload before the agent reads it needs no
  narration to explain what second-order injection means.

## Alternatives

- **A 400 for an unrecognized query parameter** (JSON:API's rule) - defensible for
  a document protocol, wrong for a page: a stray parameter would break a listing
  that could have served it, and the attempt would produce an error instead of a
  demonstration.
- **A "try to reach another tenant" button** - see the amendment above: it implies
  the UI could pick a tenant, and a canned outcome convinces nobody.
- **Reporting the value alongside the name** - it would print a tenant name the
  server never accepted into the server's own response, for no gain: the reader
  typed it.
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
- RFC 9110, HTTP Semantics, §15.5.1 (400 Bad Request) - the status for a request
  the server "cannot or will not process due to something that is perceived to be
  a client error": https://www.rfc-editor.org/rfc/rfc9110.html#name-400-bad-request
- JSON:API, *Implementation-Specific Query Parameters* - the stricter alternative
  this ADR deviates from: a server "MUST return 400 Bad Request" for a query
  parameter it does not know how to process: https://jsonapi.org/format/
- ADR 0002 (the layers), ADR 0007 (the row cap and truncation honesty), ADR 0010
  (the retrieval path this tab reuses), ADR 0012 (identity from the token, thin
  handlers), ADR 0006 (the design system the views compose).

The judgment calls, labelled as such because no external source settles them:
which eight filters to allowlist, showing the executed SQL under the table,
surfacing the committed poison manifest in the UI, keeping a visited tab
mounted rather than unmounting it, and - per the #107 amendment - answering an
unread parameter with a 200 plus a report rather than a 400, the wording of that
report, and choosing a raw parameter box over a tenant-named button as the
interactive proof.
