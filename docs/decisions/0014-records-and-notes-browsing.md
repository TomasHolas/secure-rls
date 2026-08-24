# ADR 0014 — Records and Notes: browsing the whole dataset as the control group

Status: accepted (rewritten 2026-08-24 per issue #117, an owner correction. The original decision
— that these tabs list "the tenant's own data" — is reversed, not amended: the listings show every
tenant's rows and `tenant_id` is a filter of the same kind as `department`. The reversal is
recorded in Context below, because why it happened is part of the decision.)

## Context

Until these tabs existed the app was one screen. A reader could ask the agent "how many employees
do I have?" and had to take both the answer and the isolation on faith: the only window onto the
data was the model's account of it. The demo claim — layered RLS enforcement, no tenant can reach
another's rows — was therefore argued rather than shown.

The request that created the tabs asked for two things at once: *"i want another tab, where i can
see the database records, filter it by name etc, and one tab for the files 'notes' that are agents
searching, so i can see, **we have all data**, and what data can see the agent."* Both halves. The
first shipment delivered only the second: `/records` and `/notes` listed the signed-in tenant's
rows through `db.execute_scoped`, so `acme` saw 450 of 1000 and the other 550 were unreachable
from any endpoint in the app.

That was wrong in a way worth writing down, because the code was correct and the design was not.

- **A number with nothing to compare it against reads as a bug.** "450 rows" on a screen that
  never mentions 1000 was asked about twice by the owner. The isolation is a *difference*; showing
  one side of it shows nothing.
- **It threw away the strongest thing these tabs could do.** The demo needs both sides visible at
  once: the full dataset, all 1000 rows across `acme`/`beta`/`gamma`, filterable BY tenant, so a
  reviewer sees exactly what exists — and the agent, in the very same app, only ever able to reach
  its own tenant's part of it.
- **The follow-up made it worse before it made it better.** When the owner observed that the
  Records filters would not let them filter by tenant, issue #107 answered by making the listing
  *report* that a `tenant_id` parameter had been discarded, with a sentence explaining that no
  request can name a tenant. That was a good mechanism pointed at the wrong claim: it proved the
  opposite of what had been asked for. The honesty machinery survives this rewrite; the tenant
  sentence does not.

**What the tabs are for, stated plainly.** They are the control group for the security claim, not
a tenant data view. The claim being defended is that the *agent* can never reach another tenant's
rows. An auditor surface that shows what exists is what makes that claim checkable rather than
self-reported. Nothing about the agent's reach changes here.

The risk this creates is the reason the rest of this ADR exists, and it did not change with the
reversal. A browse UI wants exactly the things that break tenant isolation: a column name in
`ORDER BY`, a substring in `LIKE`, a page window, nine optional filters. Each is a place where a
value could become SQL. If the tabs grew a query path with its own rules, they would become the
weakest link and the layers behind the agent would prove nothing.

## Decision

### 1. The listings are the dataset's, and `tenant_id` is a filter

`GET /records` and `GET /notes` return rows from every tenant. Selecting nothing shows all 1000;
selecting a tenant shows 450, 350 or 200. `tenant_id` is a field of `browse.Filters` like
`department`: same allowlist, same binding, same sort and paging rules, and it is a member of the
sort allowlist too, because a mixed listing that cannot be grouped by tenant is harder to read
than one that can.

### 2. The mechanism: one explicitly named unscoped read

A listing that spans tenants cannot go through `db.execute_scoped`, which binds one tenant by
construction. Two ways existed.

**(a) Assemble the full list from per-tenant scoped reads** and merge server-side. It preserves
the sentence "no unscoped query exists anywhere". It also costs merging, sorting and paging across
three reads, and the 200-row result cap (ADR 0007) means a tenant's 450 rows cannot come back in
one read, so it needs per-tenant paging underneath the reader's paging. Clever, and hard to defend
out loud in a 60-minute call — which by this repo's own standard is a reason to reject it.

**(b) One explicitly named unscoped read, used only by the dataset listings.** Chosen.
`db.execute_unscoped_browse` lives in `db.py` beside `execute_scoped`, is named so its nature
cannot be misread, and is documented there as the single deliberate exception. It is simpler and
it is honest about what the surface is.

**Unscoped means "not filtered to one tenant" and nothing else.** What the exception keeps:

| Control | On the unscoped browse read |
|---|---|
| Layer 2 — `security.validate_sql` allowlist, with the declared parameter count | kept, unchanged |
| Layer 2.5 — `mode=ro` open, `PRAGMA query_only`, `sqlite3_limit` caps, employees-only authorizer | kept, the same connection |
| Query deadline (progress handler) | kept |
| Row cap and truncation reporting (ADR 0007) | kept |
| Audit row for every attempt, approved or refused | kept, under the reader's tenant |
| Layer 3 — the scoping rewrite | absent: one tenant is not what is being asked for |
| Layer 4a — the structural proof that layer 3 applied | absent: there is no rewrite to prove |
| Layer 4b — the egress row check against the session tenant | absent: it would refuse the foreign rows that are the purpose |

Layer 4b deserves its own sentence, because "the egress check is skipped" is exactly the kind of
line that must never be silent. The check compares every returned `tenant_id` against the session
tenant. On this path the returned rows are supposed to carry other tenants, so the comparison has
no meaning to make — it is not weakened, it is inapplicable. The reader's tenant is still bound
into the call, as the audit identity, so the trail records who browsed; and because the executed
statement is recorded too, an audit row from this path is *identifiable* as unscoped by the
absence of the scoping subquery in it. Nothing is bypassed quietly.

### 3. What bounds the exception

An exception is only as good as the fence around it, so the fence is tested rather than asserted:

- **One caller.** A source sweep over every committed Python file parses each module and asserts
  that only `db.py` (the definition), `browse.py` (the listings) and the suite that tests them
  reach the name at all. The sweep is over the parsed tree, not the text, so a module may *explain*
  the exception in a docstring — `app.py` does — without counting as a caller of it.
- **No tool can reach it.** The agent's tool set is built and every name each tool's code reaches
  — nested code objects and closure free variables included — is collected.
  `execute_scoped` is in that set, which is what stops the assertion from passing vacuously; the
  unscoped read is not. No tool description mentions it either, so the model is never told the
  name. This is proof at the binding rather than a claim about the prompt.
- **The model cannot get there by writing SQL.** The function takes no model-produced statement:
  every string it runs is one of `browse.py`'s fixed templates, built from the sqlglot AST.

### 4. `tenant_id` still never comes from the LLM

This is the distinction the whole reversal turns on, and it is not a soft one.

- On the **chat path**, the tenant is read from the verified JWT and bound into the tools by
  closure. No tool exposes a tenant parameter, no request body carries one, and an injection that
  talks the model into asking for another tenant produces a scoped query over its own rows
  (ADR 0002, layers 1 and 3). Unchanged by this ADR.
- On the **auditor listings**, the tenant is a reader's UI control — a select in a filter grid —
  and its value is bound as a parameter exactly like a department or a salary bound, per the OWASP
  parameterization rule. It narrows what a human is shown. It cannot widen what the agent is
  shown, because the agent does not read it and there is no path from it to a tool.

A reader's filter and a model-fillable argument are different things, and conflating them is what
produced the earlier design.

### 5. Values are bound; names are allowlisted

Unchanged from the original decision, and the split follows the OWASP SQL Injection Prevention
Cheat Sheet: bind parameters for values, and where the SQL grammar does not allow a bind — table
and column names, `ORDER BY` — validate against an allow-list of known-safe strings.

1. **Filter values are bound.** Nine allowlisted comparisons (tenant equality, name substring,
   department equality, ranges on salary, performance score and hire date), each a placeholder
   whose value travels beside the statement. Nothing a reader types is rendered into SQL text.
2. **Sort column and direction are allowlisted words**, checked by `security.require_allowed`
   before they become AST nodes, and refused terminally — there is no model to correct a query
   string. So is the column of an options query.
3. **The name filter is `INSTR(LOWER(name), LOWER(?)) > 0`, not a `LIKE`.** A substring search is
   what the box promises; SQLite's `LIKE` would give a typed `%` or `_` a wildcard meaning the
   reader never asked for, and escaping it correctly is a second thing to get wrong.
4. **The page ceiling is the row cap** (`db.max_result_rows`, ADR 0007), because a page larger
   than the cap could not be served whole. A request beyond it is clamped and the response reports
   the page size it actually used. The **true total** is a second read — `COUNT(*)` over the same
   bound filters — which is what makes "1000 rows" a fact about the dataset and "450 rows, tenant
   acme" a fact about the filter, rather than either being a fact about the page in hand.
5. **Ordering is deterministic.** Every page orders by the requested column and then by the
   `user_id` primary key. Offset paging over a non-unique sort is the classic way to make rows
   repeat or vanish between pages (Winand); the tie-break removes the possibility rather than
   making it unlikely.
6. **Both templates select `tenant_id`.** It was originally justified as putting the egress check
   on this path; that reason is gone and a better one replaced it — a listing that mixes tenants
   has to say which tenant each row belongs to, and that column is the login-switch comparison
   made visible in the data rather than only in the header badge.

### 6. The Notes search stays scoped — the asymmetry is the demonstration

The corpus **list** is the dataset's. The search **is not**: `GET /notes/search` delegates to
`rag.search_notes_scoped` (ADR 0010), the partition-key pre-filtered KNN the `search_notes` tool
calls, for the token's tenant alone, and surfaces the distance. `browse.annotate_note_hits` reads
each hit's row through `db.execute_scoped` — the scoped executor, with its egress check — so a hit
naming a foreign row is annotated with nothing.

So a reader can read beta's planted injection payload in the list, search for its exact text as
acme, and get nothing back. Neither half proves much alone. Together they are the point of the
tab, and they are the reason the list must not be scoped and the search must not be unscoped.

For the same reason the **poison manifest is surfaced for every tenant**. It is generated with the
dataset, committed, and pointed at by the README, so marking the planted rows tells a reader
nothing the repo does not already say out loud; filtering the badges to the caller would hide
exactly the foreign payload the demo points at.

### 7. No total on screen is orphaned from what it counts

A number is only evidence if it says what it is a number of. Every total states its scope:
"1,000 matching rows · all tenants", "450 matching rows · tenant acme", and the same suffix on
the pager. The filter pickers obey the same rule: `GET /records/tenants` gives the tenant options
with their row counts — 450/350/200, the control group in one line — and `GET /records/departments`
takes the applied tenant filter, so a department count counts the rows the reader is actually
looking at. The executed-SQL card is labelled *"executed without tenant scoping — this listing is
the whole dataset"*, because a label promising a rewrite the statement does not carry would be a
worse lie than showing no statement at all.

### 8. What a listing did not read, it says (issue #107, retained and repointed)

A stray query parameter must not break a page — RFC 9110 defines 400 as the status for a request
the server "cannot or will not process", and a browsing surface that breaks over one is a worse
product than one that keeps answering. JSON:API takes the stricter line for its own document
protocol (an unrecognized query parameter MUST be 400); we deviate deliberately, because the value
here is that the page survives the attempt and can therefore show the attempt failing.

So `browse.ignored_params` reports every parameter name the request carried that the listing does
not read, each with the reason and the set it does read. **Names only, never values**: a response
that echoed a value would put text the server never accepted into the server's own output.

What changed with this rewrite: `tenant_id` is no longer among the ignored, because it is a filter,
and the special sentence it used to earn — "no request can name a tenant" — is deleted rather than
softened. It was true of a scoped listing and is false of this one. A misspelled `tenant` gets the
generic report, which names `tenant_id` among the accepted parameters and therefore answers the
reader's actual next question.

**The interactive affordance stays a raw parameter box, repointed.** Issue #107 rejected a "reach
another tenant" button, and that rejection still holds for the chat path: a control named after
another tenant frames layer 1 as a policy that could be relaxed rather than an input that does not
exist, and an outcome a button hard-codes is indistinguishable from a canned message. The
`ParamProbe` box therefore remains — a reader appends a parameter of their own and reads back what
the server ignored — but it now claims only what is still true of this surface (a request gets the
parameters the endpoint declares, and is told about the rest) and points the tenant claim where it
holds: the agent's tenant comes from the verified token and reaches its tools by closure. Leaving
a control on screen asserting something false would have cost more than the control is worth.

### 9. Endpoints

All JWT-required, all thin handlers over `browse.py`: `GET /records`, `GET /records/departments`,
`GET /records/tenants`, `GET /notes`, `GET /notes/search`, `GET /notes/flagged`. A refusal is an
honest status: 400 carrying the allowlist's own reason, 503 when no note index exists, and a bare
logged 403 if an RLS layer ever trips — which on this path would mean one of our own templates is
broken.

### 10. What a note card carries (issue #103, retained)

A verification surface that omits the field a reader would verify against defeats its own purpose:
"the mentoring notes in Engineering" cannot be checked on a card that never says Engineering.

- **The card shows the fields a retrieval claim is checked against**: the employee, their
  `department`, their `performance_score`, the `tenant_id` of the row, and the `distance` when
  retrieval produced one. The score earns its place from ADR 0008 — the notes are composed from
  clause pools disjoint across score bands, so tone against number is a coherence a reader can
  falsify at a glance. The tenant earns its place from point 5.6 above.
- **`salary` and `hire_date` are deliberately absent.** Neither helps decide whether a text hit is
  the right one, and a verification surface that shows everything verifies nothing in particular.
  The omission is stated in `browse.py` beside the column tuple.
- **One card shape everywhere.** The `NoteList` brick renders every one of these fields when its
  caller has it, so the chat trace, the corpus listing and the search hits stay the same card.

### 11. Frontend

The tabs are a shell brick (`components/layout/Tabs.tsx`) in the header, not a view's furniture;
the conversation rail belongs to the chat and is passed to the shell only while the chat is open. A
tab the reader has opened stays **mounted and hidden** rather than unmounted, so switching away and
back cannot cost them a streamed transcript, a typed filter or a search they ran; a tab never
opened is not mounted. The views compose existing bricks (`DataTable`, `Pill`, `CodeBlock`,
`EmptyState`, `NoteList`, `ParamProbe`, the form kit) and the catalogue in
`src/components/README.md` carries them. Sorting and paging are server-side: a header click is a
request, never an in-browser reorder of one page — which is the only honest thing a table holding
page 3 of 40 can do.

The filter block is laid out per issue #115 and the tenant control that shipped disabled there is
now live. The grid is six cells — three single filters (tenant, name, department) and three
`FieldPair`s — because a bound pair that is one cell cannot be split across rows by the wrap, and
six is a full grid at three, two and one column, so no cell is ever stranded beside dead space. The
actions close the form on their own full-width row in the `[Reset] [Apply]` order a terminal action
reads in. One `--control-height` custom property, declared once in `app.css` and matched by element
rather than by class inside a `control-row`, puts every input, select and button on one baseline;
`styles/controls.test.ts` asserts it against the real stylesheet in jsdom. The date filters are ISO
text rather than native date inputs: the native control brings a calendar popover and keyboard
handling for free, and also a placeholder rendered in the viewer's locale (`dd.mm.yyyy` here,
`mm/dd/yyyy` on a US machine) plus the OS calendar glyph — a demo whose first frame differs per
laptop and disagrees with the ISO dates in the cells below it, in the executed statement, and in
the server's own refusal. Nothing is validated client-side: a bad date reaches the server and comes
back as its own 400, which a blocking HTML `pattern` would have swallowed.

The Notes tab carries one filter of its own, a tenant select applying on change (a select is one
deliberate action; a text box would fire a request per keystroke). Without it, reaching another
tenant's planted note means paging 40 pages, and the demonstration the tab exists for would depend
on patience.

## Consequences

- The isolation claim becomes checkable in the sharpest available form: a reviewer reads the whole
  dataset on one tab and watches the agent answer 450 on another, in one session, without trusting
  either surface to describe the other.
- One unscoped read now exists in a repo whose hard rule was that none did. That is a real cost,
  paid deliberately, and it is bounded by a name that cannot be misread, a single caller proven by
  a source sweep, a tool-closure test, and every other control still in place. CLAUDE.md's hard
  rule names it beside the existing `conversations.py` exception rather than being left to
  contradict the code.
- The Notes tab now has an asymmetry a reader must be told about, or it looks like an
  inconsistency. The retrieval note on screen says it in one sentence: the search answers for your
  tenant only, while the corpus below is the whole dataset.
- Two reads per page (the window and the count) instead of one, plus one per options picker. At
  this scale that is trivial, and the alternative is a total that describes the page rather than
  the data.
- The audit log records what readers browsed as well as what the model asked. That is a feature:
  the trail is of data access, whoever caused it — and an unscoped browse row is distinguishable in
  it by the statement it recorded.
- A demo that shows a planted payload before the agent reads it, in another tenant's rows, needs
  no narration to explain what second-order injection means.

## Alternatives

- **Keeping the listings tenant-scoped** — what shipped first, and what this ADR reverses. It
  answers only half the request, makes 450 look like a defect, and discards the comparison the
  tabs exist to make.
- **Assembling the dataset from three scoped reads** (option (a) above) — preserves "no unscoped
  query exists" at the cost of paging three cursors under the reader's paging, an interaction with
  the row cap, and a sorting merge. Rejected as a mechanism that costs more to explain than the
  property it saves is worth, given the property being defended is about the agent.
- **A tenant filter that filters client-side over a full download** — would need the row cap
  lifted, ships every row to the browser, and makes the page a lie about what the server holds.
- **A 400 for an unrecognized query parameter** (JSON:API's rule) — defensible for a document
  protocol, wrong for a page: a stray parameter would break a listing that could have served it,
  and the attempt would produce an error instead of a demonstration.
- **A "try to reach another tenant" button** — still rejected, and now for a sharper reason: on
  these listings a tenant *can* be selected, so a button implying a refusal would misdescribe the
  surface it sits on, while on the chat path there is no request to name a tenant with.
- **Reporting the value alongside the ignored name** — it would print text the server never
  accepted into the server's own response, for no gain: the reader typed it.
- **A generic filter DSL from the client** (`?where=salary>100`) — the parameterization rule and
  the whole point of layer 2 say no; it is user-supplied SQL by another name.
- **Interpolating an allowlisted column into the `WHERE` text** rather than binding the value —
  safe only for as long as the allowlist is perfect, and it makes the declared placeholder count
  unpredictable.
- **A `LIKE '%x%'` name filter** — simpler to write, but it silently hands the reader wildcard
  semantics and needs `ESCAPE` handling to avoid it.
- **Reusing the agent's `query_db` tool from the UI** (have the tab send SQL) — puts
  generated-SQL machinery on a path that has no model in it, for no gain over a fixed template.
- **Unscoping the Notes search too, for symmetry** — it would delete the demonstration. The search
  is the agent's own retrieval path; changing it would change what the agent can reach, which is
  the one thing this ADR does not touch.
- **Unmounting a tab on switch** — simplest React, but a reader who checks a row count mid-
  conversation would come back to an empty chat.

## Sources

- OWASP Cheat Sheet Series, *SQL Injection Prevention* — bind parameters for values; allow-list
  validation for table/column names and `ORDER BY`, which cannot be parameterized:
  https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html
- OWASP Cheat Sheet Series, *Query Parameterization*:
  https://cheatsheetseries.owasp.org/cheatsheets/Query_Parameterization_Cheat_Sheet.html
- OWASP Top 10 for LLM Applications (LLM01 prompt injection, including indirect injection through
  retrieved content): https://genai.owasp.org/llm-top-10/
- SQLite documentation, `LIKE`/`GLOB` wildcard semantics and the `ESCAPE` clause:
  https://sqlite.org/lang_expr.html
- SQLite documentation, core functions (`instr`, `lower`): https://sqlite.org/lang_corefunc.html
- Markus Winand, *SQL Performance Explained* / use-the-index-luke, "Fetching the next page" —
  offset paging needs a deterministic (tie-broken) sort order or rows repeat and vanish:
  https://use-the-index-luke.com/sql/partial-results/fetch-next-page
- RFC 9110, HTTP Semantics, §15.5.1 (400 Bad Request) — the status for a request the server
  "cannot or will not process due to something that is perceived to be a client error":
  https://www.rfc-editor.org/rfc/rfc9110.html#name-400-bad-request
- JSON:API, *Implementation-Specific Query Parameters* — the stricter alternative this ADR
  deviates from: a server "MUST return 400 Bad Request" for a query parameter it does not know how
  to process: https://jsonapi.org/format/
- ADR 0002 (the layers, and the declared-filter-parameter amendment this path relies on), ADR 0007
  (the row cap and truncation honesty), ADR 0010 (the retrieval path the search reuses, unchanged),
  ADR 0012 (identity from the token, thin handlers), ADR 0006 (the design system the views
  compose), ADR 0008 (the score/tone coherence a note card is verified against).

The judgment calls, labelled as such because no external source settles them: that an auditor
surface showing every tenant is worth one named unscoped read (the central one — no published
guidance addresses a demo's control group); which nine filters to allowlist; showing the executed
SQL under the table and labelling it as unscoped; surfacing the committed poison manifest for every
tenant; keeping a visited tab mounted rather than unmounting it; answering an unread parameter with
a 200 plus a report rather than a 400, and the wording of that report; and repointing the parameter
box rather than removing it once its original claim stopped being true here.
