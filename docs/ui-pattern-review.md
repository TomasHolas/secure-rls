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
| **Mark the edit in place** (their diff surfaces) | New `SqlRewrite` brick + `lib/sqldiff.ts`: both statements are on screen, and the tenant scoping is highlighted inside the executed one | The executed statement *is* the generated one plus the scoping, and the highlight says where. The marking is what we took; the "show the executed statement *only*" default this review shipped with it was reversed in #121 — see the verdict below. |
| **Thinking display: live verb → past-tense summary carrying the cost** | A thinking step is open and shimmering while its round's thinking arrives, then folds itself away leaving `Thought for 2.8s` on the row | The old step was closed for the whole turn, so during a live demo the model's reasoning was invisible while it happened and silent about how long it took |
| **Auto state + user override** | `TraceStep`'s `open` is now the state a step is *in* until the reader clicks, not merely the one it mounted in (`choice ?? open`) | The mechanism the fold-when-settled behaviour needs, and it deleted state rather than adding it |
| **Counts always singularized** | `formatCount` in `lib/format.ts`, used by the trace chips and the Records/Notes totals and pagers; `1 row`, not `1 rows` | One formatter, and "1 rows" is visible at demo distance the moment a filter matches a single row |
| **Honour reduced motion** | The shimmer and the loader grid stop under `prefers-reduced-motion` | The reference does this properly; we had no such block at all |
| **Their loader: a pixel grid with a wavefront** | New `Loader` brick (issue #123): a 3x3 grid whose chevron wavefront sweeps, the shimmering label above folded into it, and an elapsed time subtracted from a start timestamp. It replaced the spinning `progress_activity` glyph at every call site — the answer card's pre-token placeholder, the thinking step, the login button, the Records and Notes loading states | A spinner says "something is happening" and nothing else, in five places that had each sized it differently. A wavefront with a running clock says *how long*, which on a demo is the question the room is actually asking while the model thinks — and one brick means the answer looks the same everywhere. **Where it goes** was wrong when this row first shipped and was corrected on owner review — see the placement verdict below. |

### Adopted — the sidebar (issue #114)

The pass above judged the reference's 20 components against the chat, Records and Notes screens
and never looked at the conversation rail, which appears in neither table. The owner's ruling
closed that gap: **port the patterns into our own CSS and tokens** — no Tailwind, no
`@central-icons-react` (it ships under "SEE LICENSE IN LICENSE.md" rather than a standard OSS
licence and this repo goes public), no new dependency of any kind. Our colours come from
`styles/tokens.css` by construction (ADR 0006); what the reference supplies is behaviour and
layout.

| Pattern | What we did | Why |
|---|---|---|
| **Collapse to an icon rail, icons staying put** | The aside animates its own width and clips (`overflow: clip`); `.sidebar-inner` stays laid out at the expanded width in both states, so nothing is re-laid out. Two widths, the icon inset, the durations and the easing are custom properties in `app.css` | Copy at the *labels'* position leaving is the effect; an icon *moving* is a different, worse effect. Re-laying out each row to an icon-only variant is what makes icons jump, and it is two layouts to keep honest instead of one |
| **The clipped column is not a scroll container** | `overflow: clip` rather than `hidden` | Found the hard way: with `hidden` the aside is still scrollable, so focusing a control the collapse had clipped made the browser scroll the whole column sideways to reveal it — icons included — and the rail rendered as an empty strip. Pinned by a test |
| **Inline search growing right-to-left** | New `InlineSearch` brick, filtering the titles already loaded, client-side, with a "No chats found." state; Escape closes, clears and returns focus to the icon | A rail search that hit the server would be a second, weaker retrieval path beside the agent's own; filtering titles on screen is what a reader actually wants from a long rail, and it cannot lie about the data |
| **One gliding hover highlight** | New `GlideList` brick: a single highlight element positioned from the hovered row's offsets, with the per-row `:hover` background kept underneath as the floor and switched off only while the glide is running | One moving highlight says "these rows are one group"; a background per row says "this one". The floor is what makes the JS optional rather than load-bearing |
| **Truncation with `title`** | Every thread row carries its full title as `title` | Generated thread labels are the one string in the rail that reliably outgrows 272px, and the truncation was silent |
| **Honour reduced motion** | The collapse, the search's growth and the glide are added to the existing `prefers-reduced-motion` block in `app.css` — one block, extended | Adopted in the pass above and it applies to every moving part we add, not only the ones that came with it |

**What we left, and why.** Its **workspace switcher** — there is one tenant per session and it is
not a client's choice (ADR 0002 layer 1); a picker there would imply the UI selects a tenant and
the server merely declines, which is the same lie the Records tab's caption exists to avoid. Its **"Invite
users 3/10" row** and its **Upgrade footer** — SaaS furniture for a product with seats and a
plan; this app has three hardcoded tenant users (ADR 0009) and nothing to upgrade. Its **icon
package** — non-OSS licence, and our `Icon` brick is a self-hosted Material Symbols subset, so
the rail ships without one new glyph. Its **Tailwind classes** — the whole point of the ruling:
the tokens are the source of colour and type, and a utility framework would fork that.

**Identity and sign-out stay in the persistent header.** The rail is mounted on the **Chat** tab
only (`App.tsx` passes it as the shell's `sidebar` when that tab is open), so duplicating session
status there added furniture without adding access to the action on Records or Notes.

### Adopted — the prompt bar (issue #159)

The pass above judged the reference's components against the transcript and never touched the
control the reader actually uses: our composer was a caps **QUESTION** label over a two-row
textarea with a resize handle, a hint line and a word-labelled **Ask** button, with the model
picker parked in the page header. The owner's ruling is the sidebar ruling again: **port the
reference's PromptBar patterns into our own CSS and tokens** — no Tailwind, no new dependency,
nothing from its `glimm` shader.

| Pattern | What we did | Why |
|---|---|---|
| **One bar, not a labelled block** | `Composer` is a single rounded `.composer-bar` holding the textarea, the picker and send; the caps label and the `Ask` block are gone | The label named what the only text box on the screen was for, and the button spelled out what its arrow says. Both were height taken from the transcript above them |
| **The textarea grows with the draft, then scrolls** | `scrollHeight` measured on every draft (`height: auto` then the measured height), capped by `--composer-cap` on the bar - five lines at our own type metrics - with `overflow-y: auto` past it | A fixed two-row box is wrong twice: too tall for the one-line question that is most questions, too short for a multi-clause one, and its resize handle made the reader fix it by hand. The ceiling is the stylesheet's, so the number lives beside the metrics it is derived from and no test or component duplicates it |
| **The model picker moves into the bar** | Same `ModelPicker`, same live `GET /models` contract, restyled as the quiet text-and-chevron trigger: `.select`'s metrics with the chrome stripped, `appearance: none`, our own chevron `Icon` | The model is a property of the question being composed, not of the page. In the header it read as configuration; in the bar it reads as part of the send, which is what it is |
| **Send as an arrow in a filled square** | `.composer-send` on the existing `.btn-icon` hit area: the inverse surface while there is something to send, the muted register (disabled) when the draft is empty or a turn is streaming, `aria-label="Send"`, a slight active scale | A control that cannot act must not look like it can, and the word "Ask" beside an arrow was the arrow explained. The square reuses the app's one icon-button geometry rather than inventing a second |
| **The focus ring belongs to the bar** | `.composer-bar:focus-within` takes the accent border; the textarea inside carries none | Three controls in one shell that light up separately read as three controls. One ring says the bar is the field |

**The hint line is gone, and did not move into the placeholder whole.** The key that sends is in
the placeholder (`... - Enter sends`), where it is on screen exactly while the box is empty; the
full contract, Shift+Enter included, is on send's tooltip. A placeholder spelling out both keys
wraps at a 900px viewport, which would cost the bar the one line at rest that is the point of
the rebuild - measured on screenshots at 1680, 1280 and 900, not assumed.

**The guardrail pill stays in the page header.** It was the alternative to the picker's old slot,
and the screenshots settled it: the pill is a statement about the run, read once before a question
is asked, while the bar is a control. Putting a security claim inside the control that sends a
question would have it compete with the picker for the same row and read as if it were a setting
of the send.

**What we left, and why.** Its **attach/files**, **@ data sources**, **/ commands** and
**dictation** controls - this product has no upload, no source picker, no command palette and no
speech input, so each would be a control that does nothing: decoration that lies about what the
app can do, which on a security demo is the one thing that must not be on screen. Its **Connect
rows** (connect Drive, Slack, ...) - the same objection plus an integrations story we do not have.
Its **`glimm` rainbow sweep on model change** - a new dependency and a celebration animation, on a
screen whose subject is a refusal; declined on both counts. Its **demo autoplay loop**, which
types a canned prompt on a timer - reference-site furniture, and here it would put words the
reader did not write into the box that sends them.

### Adopted - the pipeline canvas (owner request, no issue)

The reviews above judged the reference against the transcript, the rail and the prompt bar. The
one screen none of them looked at is the one a demo opens on: an **empty** chat, which was the
`EmptyState` brick - a `chat_bubble` glyph and the sentence telling the reader what to ask, in the
middle of the tallest region in the app. The owner's ruling is the standing one: port the
reference's **Flowchart** pattern into our own CSS and tokens - no Tailwind, no dependency, none
of its hex colours and none of its ice-cream content.

**What fills that region now is the query pipeline**: six cards on a dotted canvas - the model's
statement, layers 2, 2.5, 3 and 4, and the rows that survive them - each carrying one line lifted
from the README's security table, joined by measured connectors, with a selected card lighting the
connectors on both of its sides. The hint sentence stays, above the canvas, because it is the
instruction; what the canvas replaced is the empty space and the lone icon. A thread with any turn
in it renders exactly as before.

**This is not the pattern this review rejected, and the line matters.** Issue #91 rejected *agent
task tracking* - numbered step rings and a `2/N` progress count over a **live** run - because our
agent decides its next tool from what the last one returned, so any plan drawn before the turn
ends is fiction. That objection is about a claim, not about a shape: a diagram of steps is only a
lie when the steps it draws are being chosen as you watch. This canvas draws the **enforcement
path**, which is fixed in `security.py` and `db.py`, identical for every question, and derived
from the docs rather than from a turn. Nothing on it is fed by a run; it is gone the moment one
starts. The rejection stands unchanged for the trace, which still shows what actually happened,
in the order it happened, with no step it has not yet seen.

| Pattern | What we did | Why |
|---|---|---|
| **A dotted editor canvas** | `.pipeline-canvas` takes the reference's radial-gradient dot grid, its colour a border token and its spacing a custom property | The dots say "this is a surface you can move things on" before anything is dragged, which is the whole invitation. A flat panel with cards on it does not |
| **Measured bezier connectors** | Every card reports its own box through a `ResizeObserver`; an edge is one cubic bezier between two of those boxes, its control points a clamped fraction of the span it crosses | The alternative is positioning a curve from numbers a designer typed, which is wrong the first time a card wraps to a third line or the rail collapses. Measured means the copy can change and the picture stays true - the same argument as the rail's collapse being one layout rather than two |
| **0-1 x positions scaled from the container** | A card's x is a fraction in the brick and `clamp(...)` in the stylesheet, so a narrow canvas keeps every card whole inside itself | The gentle left-right stagger is what makes the connectors bow instead of stacking into one straight line - and at 900px it has to fold to the middle rather than clip. Verified on screenshots at 1680, 1280 and 900, where the page never scrolls sideways |
| **Selection lights the connected edges** | Clicking a card presses it (`aria-pressed`) and its two edges take the accent stroke; clicking again releases it | It answers "what is this step next to" without a second panel, and it is the one interaction on the canvas that carries information rather than delight |
| **Drag, clamped, pointer-captured, and not a click** | Ported whole: the pointer slop that separates a drag from a click, the swallowed click at the end of a drag, the clamp against the canvas box | A card that can be moved makes a static picture feel like a surface, and all three mechanics are what stop that costing the selection. **One correction to the reference**: it captures the pointer on the node *around* the card, and a capture retargets the compatibility mouse events too - so on a live click every click landed on the node and the button inside it was never pressed. Ours captures on the card. Found on the first real click, in a browser; jsdom does not model it, so the test pins the element rather than the outcome |
| **Kind pill above the card, hue on the card** | The pill is our `Pill` brick (`layer 2`, `layer 2.5`, ...); the reference's per-step hue moved onto the card's glyph, from `--chart-*` plus `--caution-500` for the model's untrusted statement and `--positive-500` for the rows | CLAUDE.md forbids hand-rolling a pill, and the brick has tones rather than hues. Putting the hue on the glyph keeps the reference's colour-coded read without a second pill implementation, and every colour is one our token set already ships |

**What we left, and why.** Its **condition chips with real dropdowns** (`If flavor is Rocky Road`)
- those are controls that edit a workflow, and this pipeline is not configurable by anyone,
least of all from a browser: a picker there would imply the layers are settings. Its **row/branch
model** (a node with two outputs) - our enforcement path has no branch; every layer either passes
the statement on or refuses it, and the refusal is in the trace, not a second lane on a diagram.
Its **hover-raised shadow on the condition card only** - one card behaving differently from its
neighbour for no reason a reader can name. Its **`glimm`/gradient trim** and its **drag handle
glyph** - the handle is a six-dot decoration promising a grab that the whole card already accepts.

**Two costs, stated.** The canvas is ~763px tall at every width, and the chat log at a 950px
viewport is ~453px, so the reader scrolls it to see the last two cards - the hint and the first
cards are what is above the fold, which is the right order, but it is a scroll where there used to
be none. And the empty transcript no longer pins itself to the bottom: the follow-the-stream
effect now runs only once a turn exists, because a log with nothing in it is read from the top and
without that change the canvas opened scrolled past its own first card.

## Rejected

| Pattern | Why not |
|---|---|
| **Their Diff Table component** | It is not a text diff: it is a row-level diff over a records table with per-row accept/reject toggles and an `Apply 3 changes` footer — an *approval* surface. Our rewrite is not a proposal; it already ran, server-side, and there is nothing for a reader to accept. Adopting the component would invent an interaction that lies about who decides. |
| **Their unified text diff** (the Tool Chips popover) | Line-granular with a `+`/`−` gutter and no intra-line marking. sqlglot re-renders the scoped statement onto **one line**, so a line diff reports the whole statement as changed and says nothing. We needed the marking they do not do. |
| **Agent task tracking** (numbered step rings, `2/N` progress) | Presumes a plan known in advance. Our agent decides its next tool from what the last one returned; a step count would be fiction until the turn ends. Our per-step pending → settled/retried/refused state already carries what those rings carry. |
| **Streaming text: blur tail, blinking caret, inline citation chips** | Answers stream as markdown through the `Markdown` brick; a blur-and-mask tail fights a renderer that is re-parsing on every token. Citation chips need web sources we do not have — retrieved notes already render as note cards in the trace. |
| **Follow-up question pills** | We would have to generate follow-ups: another model call per turn, latency and tokens spent on decoration, and a new thing that can hallucinate. |
| **Context cards for retrieved chunks** | `NoteList` already is this card — the employee, the row's facts, the body, and the retrieval distance. Theirs deliberately shows **no** score; ours shows a real one. Nothing to take. |
| **Confidence meter** | Their meter is a 4-point ordinal signal with a word attached (`High confidence`), no number. Our retrieval number is an unbounded L2 distance from `sqlite-vec`, not a confidence: rendering it as a filled meter would invent a scale and a ceiling. On a security demo, a fabricated confidence is precisely the thing that must not be on screen. |
| **Records table furniture** (resizable and sticky columns, hover row-number → checkbox, per-column AI tool config, calculation footer) | A CRM workspace's chrome. Our Records tab exists to make the isolation claim checkable — same rows, same scoped executor, a different total per tenant. Selection and per-cell enrichment have nothing to select or enrich. |
| **Filter chip strip with per-bucket counts** | **Partially adopted later, per the owner (issue #139) — the strip yes, the counts no.** The original verdict was right about the row as a whole: eight filtered dimensions (name, department, two salary bounds, two score bounds, two dates) cannot be a chip strip. It was wrong to let that settle the *one* dimension the pattern fits — `tenant_id`, three values plus `All` — which shipped as a native `<select>` whose OS-drawn popup paints its selection in the system accent, a colour no stylesheet of ours can reach. That one is a `forms/ChipRow` now. The **counts stay rejected**, and now on the owner's own instruction rather than ours: `acme (450)` on a filter control was the half they objected to. The department filter keeps its select *and* its counts (the counts are the point of that picker), which leaves the OS highlight on that one popup — a separate call, left open. |
| **Rows collapsing in place when a filter changes** | Our filtering is server-side and paged — the rows that do not match were never sent, so there is nothing on the client to collapse. |
| **Insight cards: delta as the headline number** | A delta needs a baseline. We have one snapshot of one tenant's table and no prior period to compare it against; computing a percentage against nothing is fabrication. |
| **Scrub-ready live charts** | Their chart is a paused time-series with a scrub cursor. A `ChartSpec` is a snapshot over a categorical axis — there is no time axis to scrub along. |
| **Code block: filename header, per-line streaming** | `CodeBlock` already has the label + copy header, and our SQL arrives whole in one tool event rather than streaming line by line. |
| **Approval workflows** | The agent proposes no destructive action. The one irreversible action in the app (deleting a conversation) already goes through `ConfirmDialog`. |
| **Staggered entrance animations as the progress signal** | Our steps genuinely arrive seconds apart; animating their entrance adds motion that says nothing the arrival did not already say. |
| **The loader's "Surfer" variant** (a Subway Surfers meme video beside the grid) | This repo is defended line by line in a professional interview. An autoplaying meme video is the single most expensive thing that could be on screen when someone asks "what is that?", and it would ship a video file into a security case study for a joke. Declined outright rather than hidden behind a flag — if it is ever wanted it is the owner's call, in the open (issue #123). |
| **The loader's other two variants** (Dots and Orbit beside the wavefront) | Three interchangeable decorations is speculative flexibility, which CLAUDE.md forbids: nothing in the app distinguishes three kinds of waiting. One grid ships. The one state that genuinely differs — a tool running rather than the model thinking — already reads differently, as a `running` pill on the call's own step, so a second animation would restate it. |
| **The loader's 12px footprint** (4px cells, 1.5px gaps) | Fidelity to the reference is not the goal here, legibility is: at 12px the loader is invisible from the back of a room, and it is exactly what a viewer stares at while the model thinks. Ours is 22px inline and 41px where it stands alone in an empty panel — the latter matching the empty-state icon it took the slot from. |
| **Their `useElapsed` counter** | It adds its own interval to a running total, so it under-reports by however late each tick ran — over a turn that can run to our 120s deadline that is a visible lie about the number the reader is watching. Ours subtracts a start timestamp from the clock on every read, and goes through `lib/format.ts` like every other number on screen rather than its own `toFixed(1)`. |

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

Four consequences:

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
4. **An alias belongs to the `AS` that introduced it**, which no cost function
   sees, because the rewrite spells its alias like the table the model wrote:
   the cheapest alignment explains the alias with that word and ends the
   highlight on a dangling `AS`. One pass after the alignment hands the alias
   back to the insertion. Verified live: on real model output the marked region
   is exactly `(SELECT * FROM employees WHERE employees.tenant_id = ?) AS
   employees`, once per scoped reference.

### What this review got wrong: the marking replaced the pair (reversed, #121)

The three findings above are unchanged; the packaging around them was wrong, and
this is the honest record of it.

**What this review decided.** The two cards made the reader diff two
130-character statements by eye, across a gap, and at demo distance on a shared
screen that comparison does not happen at all — the audience sees "some SQL, and
some more SQL". So `SqlRewrite` showed the executed statement *alone* with the
scoping marked inside it, and kept the pair one click behind a `show both`
toggle, on the argument that the pair was still the better read for lifting
either statement out whole.

**What live use showed.** The two readings are not alternatives. The pair is the
*before and after of the security boundary* — this is what the model asked for,
this is what the database was given — and the highlight is *where inside the
statement the boundary was applied*. A demo needs both sentences, and the old
default made the reader pick one and buy the other with a click nobody performs
mid-demo. Worse, the click was the only path to the generated statement at all,
so the screen never once showed what the model had actually written.

**What we do now.** Both cards, always, with the scoping highlighted inside the
executed one and no toggle anywhere. The diagnosis about the bare pair still
stands, and the highlight is the answer to it: it is what stops the pair being
two undifferentiated blocks of SQL. Two consequences of dropping the toggle:

- The executed card now renders the executed statement and nothing else, so
  `lib/sqldiff.ts` stopped emitting the struck-through `del` segments that the
  single-statement mode needed in order not to hide a replaced stretch. It does
  not hide it — the generated card is on screen beside it, verbatim, which is
  strictly more information than a struck-through fragment was.
- Two code cards do not fit side by side at every width, so the pair stacks
  below **700px of its own width** — 45 monospace characters per column at
  `--text-xs` plus each card's padding and border, twice, plus the gap. The
  threshold is a **container** query, not a media query, because collapsing the
  conversation rail hands the pair ~200px at an unchanged viewport width: at a
  960px viewport the pair stacks with the rail open (540px available) and sits
  side by side with it collapsed (751px). Stacked, the executed card is second,
  so "what ran" is the card touching the result table.

The highlight does not let colour carry the meaning alone (WCAG 1.4.1): it is a
tint *plus* a heavier weight *plus* a solid rule under every wrapped fragment,
with a legend naming it. Verified by screenshot with `filter: grayscale(1)` over
the whole page — with hue removed entirely the marked region is still the
obvious one.

## The loader placement verdict

The brick is unchanged; where it was hung was wrong, and this is the honest
record of it.

**What issue #123 shipped.** The grid went wherever the app said work was in
flight, which in the chat flow meant twice inside the trace panel: one beside
the **TRACE** header for as long as the turn streamed, and one as the live
thinking step's title. The answer card's own placeholder — the `thinking` line
that stands where the answer will be until its first token lands — was left as
it had always been: dead text, no shimmer, no grid.

**What live use showed.** That is exactly backwards. The placeholder is where a
reader is already looking, and for the seconds before the first token it is the
only thing on screen that could say the turn is alive — so it was the one place
that needed the grid and the one place that did not have it. Meanwhile the trace
ran two wavefronts at once for a single fact, and a panel with two animations
competing reads as decoration rather than as signal.

**What we do now.** The grid appears exactly once in a streaming turn: on the
answer card's placeholder, as `<Loader label="thinking" />` — grid plus
shimmering label, no clock, because how long the answer has been coming is
already on the thinking row. Inside the trace the grid is gone from both places.
The thinking step keeps its shimmering label and its counting clock, which is
what says *live* there, and the **TRACE** header animates nothing at all.

The brick took one prop for it rather than a second implementation:
`grid={false}` renders the label and the clock without the 3x3, so the trace and
the placeholder are still the same owner and there is no hand-rolled shimmer
anywhere. Reduced motion is unaffected — the placeholder is the same brick, so
`prefers-reduced-motion` freezes its grid dim and stops its label sweeping, as it
did in the trace.

## Attribution

No code was copied. The patterns adopted above are credited to
[beautifului.dev](https://www.beautifului.dev) (MIT, © Shane Levine / Turbo) in
the catalogue entry of every brick that carries one, and in ADR 0012.
