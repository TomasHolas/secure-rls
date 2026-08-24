# ADR 0014 — Records, Notes and Audit: browsing the whole dataset as the control group

Status: accepted (rewritten 2026-08-24 per issue #117, an owner correction. The original decision
— that these tabs list "the tenant's own data" — is reversed, not amended: the listings show every
tenant's rows and `tenant_id` is a filter of the same kind as `department`. The reversal is
recorded in Context below, because why it happened is part of the decision. Amended the same day
per issue #139, an owner review of how these two tabs read: the note card, the presentation of the
executed statement, the tenant filter, and the deletion of the reader-facing parameter probe. What
the endpoints do is untouched by that amendment — including the ignored-parameter report, which is
still in every listing response. Amended the same day once more, on the owner's question "do we
have a logs tab? we were saving the logs right?": the audit log of ADR 0002 was persisted from the
first RLS commit and nothing served it, so it gains a fourth tab and a listing of its own — see
section 12. Nothing above changes. Amended 2026-08-25 for the all-tenant identity of ADR 0009: the
unscoped read gains a second caller and loses the `_browse` in its name, and the fence of section 3
is restated - a tool reaches it only under a verified all-tenant scope. The listings themselves are
untouched.)

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
`db.execute_unscoped` lives in `db.py` beside `execute_scoped`, is named so its nature cannot be
misread, and is documented there as the single deliberate exception. It is simpler and it is
honest about what the surface is. (It was `execute_unscoped_browse` until 2026-08-25, when the
all-tenant identity of ADR 0009 became its second caller and the name stopped being true; see
section 3.)

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

An exception is only as good as the fence around it, so the fence is tested rather than asserted.
It was rewritten on 2026-08-25 when the all-tenant identity of ADR 0009 became the read's second
caller: what changed is the fence's *meaning*, not its strength. It used to say "no tool can reach
the unscoped read"; it now says "a tool reaches it only when the verified identity carries
all-tenant scope, and never through any input the model or the client controls".

- **Declared callers only.** A source sweep over every committed Python file parses each module
  and asserts that only `db.py` (the definition), `browse.py` (the listings), `analytics.py` and
  `agent.py` (the all-scope tool binding) and the suite that tests them reach the name at all. The
  sweep is over the parsed tree, not the text, so a module may *explain* the exception in a
  docstring — `app.py` does — without counting as a caller of it.
- **A tool reaches it only by grant, and the grant is bound before the model speaks.** The tool
  set is built for each identity and the data path each tool closed over is read back off the
  objects. A tenant identity's set holds `execute_scoped` and the partition-filtered retrieval and
  nothing else — the unscoped read appears nowhere in the names its code reaches, and no tool
  description mentions it, so the model is never even told the name. An all-scope identity's set
  holds the unscoped read, bound at build time from `auth.Identity.all_tenants`. This is proof at
  the binding rather than a claim about the prompt.
- **No runtime input moves a set from one to the other.** Both sets present the model the same
  tools with the same argument names, and none of those names is a tenant or a scope; an argument
  that invents one is refused by the schema rather than ignored. What each set reads is what its
  binding decided, on the identical statement.
- **The model cannot get there by writing SQL.** The function takes no model-produced statement
  from the listings: every string those run is one of `browse.py`'s fixed templates, built from
  the sqlglot AST. It does run an all-scope identity's generated SQL — that is what that grant is
  — through layer 2, layer 2.5, the caps, the deadline and the audit row, exactly as
  `execute_scoped` runs a tenant's.

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
looking at.

The executed statement is under the table as **a stated fact plus the evidence for it**, amended
per issue #139. The fact is a caption beside the pager — *"this listing is the whole dataset,
unscoped by design (the agent's queries never are)"* — and the statement itself is one click
behind a closed `Disclosure` labelled *"show the SQL this page ran"*, still carrying
*"executed without tenant scoping"* on the block a reader copies out. What it replaced was an
always-open card under an all-caps section head, which led the tab with the least readable thing
on it and shouted the same absent-rewrite label twice. The statement is **never removed**: a claim
about what ran is only checkable against what ran. One click is the cost of not having a block of
monospace be the first thing the tab says; a label promising a rewrite the statement does not
carry would still be the worse lie.

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

**The reader-facing probe box is gone; the report is not** (issue #139). Issue #107 answered the
silent-parameter problem with two things at once: the server naming what it ignored, and a box on
both tabs for a reader to type a parameter into. The server half is the decision and it stands —
every listing response still carries `ignored`, checkable with `curl` or the browser's network
tab, and asserted by the backend suite. The box was the owner's to keep or drop, and on review
they dropped it: a control whose whole subject is an HTTP property explained no data, took a
section of its own above the rows on the two tabs that exist to show data, and was the panel they
pointed at twice. Nothing about the API changed, so nothing that was checkable stopped being
checkable — it is checked one layer down instead of on screen.

A "reach another tenant" button remains rejected, for issue #107's original reason: a control
named after another tenant frames layer 1 as a policy that could be relaxed rather than an input
that does not exist, and an outcome a button hard-codes is indistinguishable from a canned
message.

### 9. Endpoints

All JWT-required, all thin handlers over `browse.py`: `GET /records`, `GET /records/departments`,
`GET /records/tenants`, `GET /notes`, `GET /notes/search`, `GET /notes/flagged`, `GET /audit`
(section 12). A refusal is an
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
  falsify at a glance. The tenant earns its place from point 5.6 above, and per issue #139 it is a
  `Pill` beside the name rather than corner microtext: on a list that spans every tenant it is the
  most load-bearing word on the card.
- **A rank is a fact about a ranking** (issue #139). `#user_id` is shown with the `distance`, on a
  search hit, where together they are the nearest-first order a reader checks. On the corpus
  listing there is no distance and the `#N` was row position presented as data, so it is not
  shown.
- **The prose is capped at a measure, not at the card.** ~80 rendered characters
  (`--note-measure`), the ceiling WCAG 1.4.8 names for a block of text; the card keeps the width
  of the region it sits in. A 1600px line of notes on a demo screen was unreadable at exactly the
  moment a reader was being asked to read it.
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
`EmptyState`, `NoteList`, `Disclosure`, the form kit including `ChipRow`) and the catalogue in
`src/components/README.md` carries them. Sorting and paging are server-side: a header click is a
request, never an in-browser reorder of one page — which is the only honest thing a table holding
page 3 of 40 can do.

The filter block is laid out per issue #115 and the tenant control that shipped disabled there is
now live. It is **a row of chips rather than a select** (issue #139): three tenants and an `All`
all fit on one line, and a native `<select>`'s popup is drawn by the OS, so its selected row
arrives in the system accent — a colour no stylesheet here can reach. The chips carry no counts;
the tenant row counts that used to hang off the options were what the owner objected to, and
`GET /records/tenants` still serves them for whoever else asks. The department filter keeps its
`<select>` and its counts (six options, and the counts are the point of that picker), which means
the department popup keeps the OS highlight — a separate call, left to the owner. The grid is six
cells — three single filters (tenant, name, department) and three `FieldPair`s — because a bound pair that is one cell cannot be split across rows by the wrap, and
six is a full grid at three, two and one column, so no cell is ever stranded beside dead space. **Every filter applies itself** (issue #152): a chip or a select on the change, a typed box once
the reader has stopped typing for `FILTER_DEBOUNCE_MS` — 350ms, held once in
`src/lib/debounce.ts`. The `Apply` button that used to gate all nine is gone, because it made a
pressed chip a promise the table had not kept: the strip showed `acme` while the pill below it
still said "1,000 matching rows · all tenants", and the Notes tab, whose one chip always applied on
the click, disagreed with it. What is sent is unchanged — the same query parameters, only fired at a
different moment. Reset stays and closes the form alone on its own full-width row: clearing nine
boxes at once is one action, and it also cancels a keystroke still waiting, so nothing lands after
it. A refusal the server earns on a half-typed value (`hired_from=2020-0`) is held while the
interval is open and painted only once the value has settled and is genuinely refused, so the
banner never flashes over the table between two keystrokes. One `--control-height` custom property, declared once in `app.css` and matched by element
rather than by class inside a `control-row`, puts every input, select and button on one baseline;
`styles/controls.test.ts` asserts it against the real stylesheet in jsdom. The date filters are ISO
text rather than native date inputs: the native control brings a calendar popover and keyboard
handling for free, and also a placeholder rendered in the viewer's locale (`dd.mm.yyyy` here,
`mm/dd/yyyy` on a US machine) plus the OS calendar glyph — a demo whose first frame differs per
laptop and disagrees with the ISO dates in the cells below it, in the executed statement, and in
the server's own refusal. Nothing is validated client-side: a bad date reaches the server and comes
back as its own 400, which a blocking HTML `pattern` would have swallowed.

The Notes tab carries one filter of its own, the same tenant chip row, applying on the click —
which is now what both tabs do. Its search box keeps its button: a retrieval over the note corpus
is an embedding of the reader's query, not a filter narrowing rows, and a request per pause of a
typed question would spend the model on drafts nobody asked about. Without the filter, reaching another tenant's planted note means paging 40 pages, and the demonstration the tab
exists for would depend on patience.

### 12. The Audit tab: the trail the server was already keeping

The owner asked *"do we have a logs tab? we were saving the logs right?"* — and both halves were
true at once. Every call through `db.py` has written an audit row since the first RLS commit (the
generated SQL, the verdict, the executed statement, the row count, the error kind, ADR 0002), the
row is written in a `finally` so no path can skip it, and the eval leak checks read it. Nothing in
the app served it. So the one claim the surface could not make checkable was the load-bearing one:
that every read was scoped, refused or recorded.

The Audit tab is that trail, and it belongs to this ADR because it is the same decision as Records
and Notes: **the tabs are the auditor surface**, and a trail is what an auditor reads.

- **All tenants' entries, newest first, no filters.** The same reason Records lists 1000 rows: a
  trail narrowed to the caller could not show another tenant's query being scoped to that other
  tenant, which is exactly the comparison the page is for. A log is read from its head, so it has
  a pager and a reload and no filter grid at all. A tenant chip row is the obvious next thing and
  is the owner's to ask for; adding it before it is asked for would make the log a workbench.
- **Serving it exposes nothing new.** An audit row holds *statements and metadata* — never a
  result row. There is no tenant data in that store that Records does not already show outright, a
  token is required exactly as on every other listing, and the endpoint is not a tool, so no model
  can reach it (ADR 0002, layer 1; the tool-closure sweep of section 3 is unchanged and untouched
  by this addition).
- **`db.py` stays the only reader of `audit.db`.** `audit_entries` hands the whole log to the
  evals; `audit_window` hands one newest-first window to this listing, with `LIMIT`/`OFFSET` bound
  and the total counted in the store rather than loading the log to slice it in Python.
  `browse.browse_audit` applies the same paging rules the row listings use — the same default page,
  the same ceiling, the executor's row cap (ADR 0007) — and the handler stays one call.
- **Reading the log writes no audit row.** Reading the trail is not a read of the dataset, and a
  trail that recorded every look at itself would bury the rows it exists to show. So this is the
  one listing without a `reader_tenant`: there is no data access to attribute.
- **On screen it is the store's own shape**, under the store's own column names: `ts`, `tenant`,
  `generated_sql`, `verdict`, `error_kind`, `executed_sql`, `rowcount`. The verdict is a `Pill` —
  neutral for a statement that ran, `danger` for one a layer refused, `warn` for one that broke —
  and each statement is one ellipsised line with the full text on the cell a reader hovers, because
  a log is scanned down its verdict column and two columns of wrapped SQL per row would bury that.
  A refused row carries no executed statement at all, which is the log saying nothing ran rather
  than saying nothing. The timestamps stay UTC ISO as the server wrote them (the conversation rail
  localizes its own, because a thread is something the reader did and a log row is something the
  server recorded).
- **One brick change, additive.** `DataTable` gained an optional `render` map keyed by column name,
  which is what lets a `Pill` and a truncated statement sit in cells without a second table brick
  existing. A column not listed renders exactly as before, so no other caller is touched.

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
- The audit trail is now on screen as well as in the store, which cuts both ways and is worth
  stating: the browse rows a reader generates are in it too, so the log a demo shows is partly the
  demo's own footprints. That is the trail being honest about who read what, and the executed
  statement in each row is what tells the two apart.

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
- Nielsen Norman Group, *Response Times: The 3 Important Limits* — the 1-second ceiling for
  keeping a reader's flow of thought uninterrupted, which the filter debounce sits inside:
  https://www.nngroup.com/articles/response-times-3-important-limits/
- ADR 0002 (the layers, and the declared-filter-parameter amendment this path relies on), ADR 0007
  (the row cap and truncation honesty), ADR 0010 (the retrieval path the search reuses, unchanged),
  ADR 0012 (identity from the token, thin handlers), ADR 0006 (the design system the views
  compose), ADR 0008 (the score/tone coherence a note card is verified against).

The judgment calls, labelled as such because no external source settles them: that an auditor
surface showing every tenant is worth one named unscoped read (the central one — no published
guidance addresses a demo's control group); which nine filters to allowlist; showing the executed
SQL under the table, labelling it as unscoped, and putting it behind one click with the fact stated
in front of it; surfacing the committed poison manifest for every tenant; keeping a visited tab
mounted rather than unmounting it; answering an unread parameter with a 200 plus a report rather
than a 400, and the wording of that report; serving that report from the API only, with no
control on screen for producing one; and the 350ms debounce interval — no source names a number for
a filter box, so it is a judgment bounded by the source that does exist: well inside the 1-second
limit above, and long enough to swallow a burst of typing.
