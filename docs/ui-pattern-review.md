# UI pattern review — beautifului.dev against our screens

A review-and-adopt pass over [beautifului.dev](https://www.beautifului.dev) (a
free, MIT-licensed pattern library for AI-native interfaces, by Turbo), filed as
issue #91. The reference is 20 components on one page; the source is copy-paste
from the page itself, so what is adopted below is **reimplemented on our tokens
and our data**, never lifted. ADR 0006 stands: the knowledgebase design system
remains the visual source of truth, and what we borrow is interaction and
information design.

The comparison is against the screens as they stand *after* issues #87 (the
reworked trace panel) and #88/#0014 (the Records and Notes tabs) landed, which
is why several patterns the issue expected us to want are already in place.

## Adopted

| Pattern | What we did | Why |
|---|---|---|
| **Mark the edit in place** (their diff surfaces) | New `SqlRewrite` brick + `lib/sqldiff.ts`: the executed statement rendered once, with the tenant scoping marked inside it; the two cards stay one click away | The executed statement *is* the generated one plus the scoping. See the verdict below. |
| **Thinking display: live verb → past-tense summary carrying the cost** | A thinking step is open and shimmering while its round's thinking arrives, then folds itself away leaving `Thought for 2.8s` on the row | The old step was closed for the whole turn, so during a live demo the model's reasoning was invisible while it happened and silent about how long it took |
| **Auto state + user override** | `TraceStep`'s `open` is now the state a step is *in* until the reader clicks, not merely the one it mounted in (`choice ?? open`) | The mechanism the fold-when-settled behaviour needs, and it deleted state rather than adding it |
| **Counts always singularized** | `formatCount` in `lib/format.ts`, used by the trace chips and the Records/Notes totals and pagers; `1 row`, not `1 rows` | One formatter, and "1 rows" is visible at demo distance the moment a filter matches a single row |
| **Honour reduced motion** | The shimmer and the trace spinner stop under `prefers-reduced-motion` | The reference does this properly; we had no such block at all |

## Rejected

| Pattern | Why not |
|---|---|
| **Their Diff Table component** | It is not a text diff: it is a row-level diff over a records table with per-row accept/reject toggles and an `Apply 3 changes` footer — an *approval* surface. Our rewrite is not a proposal; it already ran, server-side, and there is nothing for a reader to accept. Adopting the component would invent an interaction that lies about who decides. |
| **Their unified text diff** (the Tool Chips popover) | Line-granular with a `+`/`−` gutter and no intra-line marking. sqlglot re-renders the scoped statement onto **one line**, so a line diff reports the whole statement as changed and says nothing. We needed the marking they do not do. |
| **Agent task tracking** (numbered step rings, `2/N` progress) | Presumes a plan known in advance. Our agent decides its next tool from what the last one returned; a step count would be fiction until the turn ends. Our per-step pending → settled/retried/refused state already carries what those rings carry. |
| **Streaming text: blur tail, blinking caret, inline citation chips** | Answers stream as markdown through the `Markdown` brick; a blur-and-mask tail fights a renderer that is re-parsing on every token. Citation chips need web sources we do not have — retrieved notes already render as note cards in the trace. |
| **Follow-up question pills** | We would have to generate follow-ups: another model call per turn, latency and tokens spent on decoration, and a new thing that can hallucinate. |
| **Context cards for retrieved chunks** | `NoteList` already is this card — title, id, body, and the retrieval distance. Theirs deliberately shows **no** score; ours shows a real one. Nothing to take. |
| **Confidence meter** | Their meter is a 4-point ordinal signal with a word attached (`High confidence`), no number. Our retrieval number is an unbounded L2 distance from `sqlite-vec`, not a confidence: rendering it as a filled meter would invent a scale and a ceiling. On a security demo, a fabricated confidence is precisely the thing that must not be on screen. |
| **Records table furniture** (resizable and sticky columns, hover row-number → checkbox, per-column AI tool config, calculation footer) | A CRM workspace's chrome. Our Records tab exists to make the isolation claim checkable — same rows, same scoped executor, a different total per tenant. Selection and per-cell enrichment have nothing to select or enrich. |
| **Filter chip strip with per-bucket counts** | Fits one low-cardinality dimension; we filter eight (name, department, two salary bounds, two score bounds, two dates). The "know the bucket size before you click" idea we already have: the department select carries each department's count. |
| **Rows collapsing in place when a filter changes** | Our filtering is server-side and paged — the rows that do not match were never sent, so there is nothing on the client to collapse. |
| **Insight cards: delta as the headline number** | A delta needs a baseline. We have one snapshot of one tenant's table and no prior period to compare it against; computing a percentage against nothing is fabrication. |
| **Scrub-ready live charts** | Their chart is a paused time-series with a scrub cursor. A `ChartSpec` is a snapshot over a categorical axis — there is no time axis to scrub along. |
| **Code block: filename header, per-line streaming** | `CodeBlock` already has the label + copy header, and our SQL arrives whole in one tool event rather than streaming line by line. |
| **Approval workflows** | The agent proposes no destructive action. The one irreversible action in the app (deleting a conversation) already goes through `ConfirmDialog`. |
| **Staggered entrance animations as the progress signal** | Our steps genuinely arrive seconds apart; animating their entrance adds motion that says nothing the arrival did not already say. |

## The SQL diff verdict

The issue asked for this one to be judged honestly, so: **a diff wins, but not
the diff the issue had in mind, and not their component.**

What decided it was the actual data. `db.execute_scoped` rewrites every
`employees` reference into a tenant-filtered subquery and then renders the whole
tree back through sqlglot, so a real pair looks like this:

```
generated  SELECT name, department, salary FROM employees ORDER BY salary DESC LIMIT 5
executed   SELECT name, department, salary FROM (SELECT * FROM employees WHERE employees.tenant_id = ?) AS employees ORDER BY salary DESC LIMIT 5
```

Three consequences:

1. **A line diff is worthless here.** sqlglot emits one flat line and uppercases
   keywords, so a model that wrote four indented lines produces a diff in which
   every line changed. The whole story is *inside* one line.
2. **The diff must be token-level and case-insensitive**, which cancels the
   re-rendering and leaves only the rewrite. The reference does no intra-line
   diffing anywhere, so this part is ours.
3. **Maximising matched tokens is the wrong objective.** The injected subquery
   repeats the words around it (`employees`, `FROM`, `WHERE`), so a plain LCS
   alignment strands the model's own words inside the insertion and renders the
   rewrite as confetti — two or three highlighted fragments with unmarked words
   between them. `lib/sqldiff.ts` therefore minimises the number of edit *runs*
   (an affine gap cost, Gotoh 1982), which keeps the whole subquery as one block.
   This was verified live: on real model output, the marked region is exactly
   `(SELECT * FROM employees WHERE employees.tenant_id = ?) AS`, once per scoped
   reference, with no deletions.

Against the two cards it replaces: the cards made the reader diff two 130-character
statements by eye, across a gap, and at demo distance on a shared screen that
comparison does not happen at all — the audience sees "some SQL, and some more
SQL". One statement with the tenant predicate and its bound parameter glowing
inside it is a single glance. The cards are kept, one click behind `show both`,
because they are still the better read for lifting either statement out whole,
and they are what a statement too long to align falls back to.

Two things the diff deliberately does not do: it does not hide a difference it
cannot explain (anything the rewrite replaced renders as a struck-through
deletion beside its replacement), and it does not let colour carry the meaning
alone — a legend states what the highlight is, per WCAG 1.4.1.

## Attribution

No code was copied. The patterns adopted above are credited to
[beautifului.dev](https://www.beautifului.dev) (MIT, © Shane Levine / Turbo) in
the catalogue entry of every brick that carries one, and in ADR 0012.
