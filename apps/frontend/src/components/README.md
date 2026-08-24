# UI bricks — the frontend design system

Reusable, self-contained building blocks. The rule: **a view imports a brick and
fills it with data — it never re-implements or re-styles one.** Every button, icon,
card, etc. in this SPA composes a brick from here; a genuinely new reusable element
means creating the brick first (CLAUDE.md, "everything is a lego brick").

The bricks and the visual system are ported from the **knowledgebase** repo
(`knowledgebase/apps/frontend`), which stays the tracking source of truth — see
[ADR 0006](../../../../docs/decisions/0006-kb-design-system.md). `styles/tokens.css`
is a verbatim copy (KB's copy wins on divergence); `styles/app.css` carries over only
the rules the bricks below use. Only the bricks this app needs were ported, not the
whole KB library.

## Where things live

```
components/
  Button.tsx       the one button brick — variants primary/ghost
  Icon.tsx         <Icon name="..." /> — Google Material Symbols only
  Pill.tsx         the status chip — tones neutral/accent/ok/warn/danger
  Loader.tsx       the one loading signal — pixel grid (droppable), label, elapsed
  CodeBlock.tsx    labelled monospace block with a copy control (SQL lives here)
  SqlRewrite.tsx   the generated/executed pair, scoping highlighted in the executed card
  Markdown.tsx     render a markdown string as sanitized GFM HTML
  DataTable.tsx    backend rows as a compact, visually capped table (optional server-side sort)
  NoteList.tsx     employee-written notes as quoted note cards
  ParamProbe.tsx   the reader's own query parameter, and what the server ignored of it
  TenantPill.tsx   the identity chip (tenant + user) in the header slot
  InlineSearch.tsx a search box that grows right-to-left out of its own icon
  GlideList.tsx    a row group with one highlight that glides to the row under the pointer
  Modal.tsx        the one dialog brick — portal, backdrop, Escape/backdrop/× dismissal
  ConfirmDialog.tsx  the confirm step in front of a delete, on Modal + Button
  forms/           FormCard, TextField, SelectField, FieldPair (+ index barrel)
  layout/          AppLayout, Header, Tabs, Sidebar, Page, PageHeader, Section,
                   EmptyState (+ index barrel)
  charts/          Chart — renders a backend ChartSpec (+ index barrel)
  chat/            ChatMessage, Composer, ModelPicker, TracePanel, TraceStep,
                   ToolResultView (+ index barrel)
```

Views live beside them in `src/views/` (`LoginView`, `SessionBadge`, `ChatView`,
`ConversationsSidebar`, `RecordsView`, `NotesView`); the non-visual bricks they compose
are `src/auth.ts` (the
session), `src/lib/api.ts` (the one HTTP client), `src/lib/sse.ts` (the SSE stream to
typed trace events), `src/lib/trace.ts` (those events folded into one turn's state),
`src/lib/conversations.ts` (which thread is open, and the thread list around it),
`src/lib/format.ts` (the one formatter every reader-facing number, duration and count goes
through) and `src/lib/sqldiff.ts` (the token alignment `SqlRewrite` paints).

CSS for every brick lives in `styles/app.css`; colors, spacing, radii, motion and
fonts come from `styles/tokens.css`.

**A row of controls carries `control-row`.** Any element that puts an input, a select and a
button on one line — the Records filter grid, the Notes search, `ParamProbe` — adds that class
beside its own layout class, and every control inside takes one height from `--control-height`
and loses the field's bottom margin, so the row is one height and one baseline (issue #115). The
property is declared once, at the top of `app.css` rather than in `tokens.css`, because that file
is KB's verbatim copy (ADR 0006) and a metric KB does not define would be lost on the next sync.
`styles/controls.test.ts` asserts the contract against the real stylesheet in jsdom, so a control
that escapes the rule or a second declaration of the height fails the suite rather than a review.

## Bricks

### Button

```tsx
<Button variant="primary" onClick={send} disabled={busy}>
  <Icon name="send" size={16} /> Ask
</Button>
```

`variant` is `primary` (accent fill) or `ghost` (default, bordered). `type="submit"`
for forms. Never hand-write `<button className="btn ...">`.

### Icon — Material Symbols only

Every icon comes from **Google Material Symbols**
(https://fonts.google.com/icons) — no other icon library, no hand-rolled SVGs.
`Icon.tsx` maps our stable `name` keys to Material ligatures and renders them via a
**self-hosted subset font** (`public/fonts/material-symbols-subset.v3.woff2`,
`@font-face` in `tokens.css`). The map mirrors exactly the glyphs in that subset, so
to add an icon: add a mapping in `MATERIAL_SYMBOLS`, then regenerate the subset woff2
— fetch
`https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined&icon_names=<comma,list>`
(the `icon_names` list must cover **every** value in `MATERIAL_SYMBOLS`), download the
linked woff2 and save it under a **bumped version suffix**
(`...subset.v4.woff2`, ...), updating the `@font-face` url in `tokens.css` to match.
The version bump is the cache-bust: `public/` assets are not fingerprinted by the
bundler, so reusing the filename makes browsers serve the stale font and new glyphs
render as literal text. A name whose ligature is not in the subset renders as literal
text, so keep the map and the subset in sync.
### Pill — the status chip

```tsx
<Pill tone="warn">showing 200 of 543 rows</Pill>
<Pill tone="danger">blocked</Pill>
<Pill tone="neutral" icon="cpu">{turn.model}</Pill>
```

Every short status label in the app: a turn's verdict, the ADR 0007 truncation notice,
a retry counter, the model that answered, the prompt-guardrail position of the session
and of each finished turn (`ChatView`'s `GuardrailPill`, `danger` when the guardrails
are off). `tone` is `neutral | accent | ok | warn | danger`, mapped onto the
`--positive-500` / `--caution-500` / `--critical-500` state tokens. `TenantPill` stays
separate because it is the identity chip, not a status.

### Loader — the one "work is in flight" signal

```tsx
<Loader />                                             {/* a pending button */}
<Loader label="thinking" />                            {/* the answer card before its first token */}
<Loader scale="page" label="Loading notes…" />         {/* a panel with nothing in it yet */}
<Loader label="Thinking" since={x} grid={false} />     {/* how long, where a grid already shows */}
```

A 3x3 pixel grid whose chevron wavefront sweeps across it — the middle row leads, the
corners trail — with an optional shimmering label and an optional live elapsed time. It
replaced a spinning `progress_activity` glyph at every place the app shows work in
flight (issue #123), so there is one loading idiom rather than one per view: the answer
card before its first token arrives, the trace's thinking step, the login button's
pending state, and the Records and Notes tabs before their first page arrives.

It composes rather than insisting. The grid alone is the whole loader where the text
beside it already says what is happening; `label` turns it into a panel's loading state;
`since` (a start timestamp) adds the elapsed time where how long this is taking is the
reader's real question — the model thinking. `scale` is `inline` (default, a 22px grid in
a row of text or a button) or `page` (a 41px grid, centred in the empty panel it fills).
`grid={false}` leaves the shimmering label as the whole signal, for a place that would
otherwise put a second wavefront on screen beside a first: the chat flow carries the grid
once, on the answer card's placeholder, and the trace's thinking row shimmers without one
(the owner's placement ruling on #123, `docs/ui-pattern-review.md`).

Every metric — cell, gap, the wavefront's step and cycle, the two opacities — is a custom
property in `app.css`'s own `:root`, so nothing is a number in JSX and the two scales
differ by two declarations. The cycle is deliberately shorter than the time a front takes
to cross the grid, which is what keeps a second front in flight behind the first. The
elapsed time is subtracted from the start timestamp on every read and formatted by
`lib/format.ts`, never accumulated in the interval — a counter that adds its own ticks
under-reports a long turn. Under `prefers-reduced-motion` the grid freezes at its dim
opacity and the label stops sweeping, while the elapsed time keeps ticking: that is
information, not decoration.

The wrapper is the `role="status"` region, the grid is `aria-hidden`, and the label is
real text under a clipped gradient — selectable, and readable by a screen reader. The
elapsed time is hidden from the region too: it changes ten times a second, and a live
region that noisy is noise. Pattern credited to
[beautifului.dev](https://www.beautifului.dev), ported onto our CSS and tokens — no
Tailwind, no dependency, and DOM rather than a glyph, since `Icon` is a fixed subset.

### CodeBlock

```tsx
<CodeBlock label="generated by the model" code={data.generated_sql} />
<CodeBlock label="executed after tenant scoping" code={data.executed_sql} tone="accent" />
```

A labelled monospace block with a copy control. All SQL in the app goes through it, so
every statement is rendered by the same code in the same register. The copy control hides
itself where `navigator.clipboard` is unavailable (insecure origin, jsdom) instead of
offering a button that cannot work.

One optional slot: `children` is a **marked-up rendering of the same `code`** — pass
it and the body renders that instead of the plain string, while `code` stays what the
copy control writes, so a reader never lifts markup out of the demo. A caller that
passes children owes it that they render the same statement.

### SqlRewrite

```tsx
<SqlRewrite generated={data.generated_sql} executed={data.executed_sql} />
```

**The demo's money shot.** Two `CodeBlock`s in a `.sql-pair` grid — what the model wrote
and what actually ran — **both always visible, with no toggle** (ADR 0012 as amended after
issue #121). Inside the executed card, what the tenant-scoping layer added is highlighted:
the model wrote everything unmarked, and
`(SELECT * FROM employees WHERE employees.tenant_id = ?) AS employees` is the layer-3
rewrite with its bound parameter. A legend states what the highlight means, and the
highlight itself carries a heavier weight and a solid rule as well as the tint — colour is
never the only signal. Either card's `copy` writes plain SQL, never the markup.

`lib/sqldiff.ts` owns the alignment and this brick only paints it. That module diffs
**tokens**, case-insensitively (sqlglot re-renders the statement onto one flat line with
uppercased keywords, so a line diff reports everything as changed), minimises the number
of edit **runs** rather than the number of edited tokens — the injected subquery repeats
`employees`, `FROM` and `WHERE`, so the alignment with the most matched tokens strands the
model's own words inside the insertion and renders the rewrite as confetti — and then
hands the alias of an inserted `AS` to that insertion, since the alignment would otherwise
account for it with the table reference the model wrote and end the highlight on a
dangling keyword. Its segments cover only the executed statement, so they concatenate back
to it exactly. Past its token cap `diffSql` returns null and the pair renders unmarked.

The pair stacks below **700px of its own width** — a `@container` query, because
collapsing the conversation rail changes the available width by ~200px at an unchanged
viewport. Stacked, the executed card is second, so "what ran" is the card touching the
result table.

Pattern from [beautifului.dev](https://www.beautifului.dev) (MIT): its diff surfaces mark
an edit in place rather than beside it. **Reimplemented, not copied** — its own diffs are
line-granular with a `+`/`−` gutter, which cannot show a change that lives inside one line.

### Markdown

```tsx
<Markdown>{turn.answer}</Markdown>
```

The KB brick, ported: `react-markdown` with `remark-gfm` (tables, lists, strikethrough)
and `rehype-sanitize`, links forced to `target="_blank" rel="noopener noreferrer"`. The
sanitize plugin is the point — the string is model-written, so raw HTML in it is stripped
rather than rendered. Styled by `.markdown-body` in `app.css` (KB's rules verbatim), which
also resets an outer `white-space: pre-wrap`, so wrap it in a `markdown-body` element.
Assistant answers are its only caller today.

### DataTable

```tsx
<DataTable columns={data.columns} rows={data.rows} />
```

Backend rows as a compact table inside a horizontal scroll wrapper. `maxRows` (default
8) is a **visual** cap only, with a footer stating how many of the returned rows are
hidden; the server-side cap of ADR 0007 is reported separately by the truncation `Pill`.
`null` renders as `-`, numbers right-align in mono and print through `lib/format.ts`, the
same formatter the chart axes use.

Sorting is **optional and server-side**. Pass `sortable` (the columns the server will sort
by), the `sort`/`direction` it is currently sorting by, and `onSort`, and those headers
become buttons that request a sort, carrying `aria-sort` for a screen reader:

```tsx
<DataTable columns={page.columns} rows={page.rows} maxRows={page.page_size}
  sortable={SORTABLE} sort={page.sort} direction={page.direction} onSort={sortBy} />
```

The table never reorders rows itself — it is holding one page, and the order of the rest is
the server's to decide. Without those props it is the plain table the chat trace shows.

### NoteList

```tsx
<NoteList notes={data.notes} />                          // the chat trace: retrieved notes
<NoteList notes={hits.hits} flagged={kinds} empty="…" /> // the Notes tab: search hits
```

Employee-written notes as quoted data, never as instructions — one card per note with its
name, the `department` and `performance_score` of the row it belongs to (the note's tone is
composed coherent with that score, ADR 0008, so the pair is a check a reader can make at a
glance), `#user_id`, the `tenant_id` the row came from and, when retrieval produced one, the
`distance` it scored. Every field but the name and the text is optional — the card shows what
its caller has. `flagged` is a `{user_id: payload_kind}` map (from `GET /notes/flagged`, the
committed `poisoned_manifest.json`) and marks a planted injection payload with a warn `Pill`,
which is what lets the demo point at a payload before the agent reads it (ADR 0014). The chat
trace and the Notes tab share this brick, so a note reads identically wherever it is served.

### ParamProbe

```tsx
<ParamProbe id="records-probe" ignored={page.ignored} onSend={setProbe} disabled={loading} />
```

The Records and Notes tabs' one control that is not a filter (issue #107). A box appends a raw
`name=value` of the reader's own choosing to the next listing request, and the notice below it
names every parameter the response reports as unread, with the server's own reason — verbatim,
so the report a reviewer reads on screen is the server's and not a paraphrase of it. `onSend`
hands the parent the text as typed; the parent sends it beside its filters and feeds `ignored`
back. The notice renders nothing when nothing was ignored.

It no longer claims that no request can name a tenant, because on these listings one can:
`tenant_id` is a real filter there, since the tabs show the whole dataset (ADR 0014 as rewritten
by issue #117). The claim it makes instead is the one that survived — a request gets exactly the
parameters the endpoint declares and is told about the rest — and the explainer points the tenant
claim at where it is still true: the agent's tenant comes from the verified token and reaches its
tools by closure, so no tool argument can name one. It is still not a named control of any kind: a
parameter box implies nothing, because a query parameter is what the request already is.

### Brand mark

There is no logo component: `Header` renders `public/anteater.png` through an `<img>`,
exactly like the KB header does. `public/favicon.png` is the tab icon.

### TenantPill

```tsx
<TenantPill tenant={session.tenantId} username={session.username} />
```

The identity chip in the header slot: tenant id (mono) plus the signed-in user. Both
values come from `auth.ts`, which reads them out of the JWT payload **for display
only** — the server derives the real tenant from the verified token.

### InlineSearch

```tsx
<InlineSearch id="rail-search" label="Search conversations" placeholder="Search chats"
  value={query} onChange={setQuery} hidden={collapsed} />
```

A search box that **grows right-to-left out of its own icon control** and focuses itself; Escape
closes it, clears the query and hands focus back to the icon (issue #114). The wrapper sits at the
end of its row, so the box growing inside it pushes its own left edge leftwards over whatever
label was there. The query is the **caller's** state - the caller filters what it has already
loaded, and this brick owns nothing but the disclosure. `hidden` is for a container that clips it
out of view: the box closes, clears, and both controls leave the Tab order and the accessibility
tree. While closed the box is `aria-hidden`, which is why it and the icon can share one name.

### GlideList

```tsx
<GlideList hidden={collapsed}>{rows /* <li> each */}</GlideList>
```

A row group with **one** highlight that glides to the row the pointer or the keyboard is on,
instead of every row lighting a background of its own (issue #114). The travel is what says the
rows are one group. The highlight is a first `<li>` behind the others, positioned from the hovered
row's own offsets; where that measurement has not happened - no layout yet, no scripting - the
stylesheet's plain `:hover` per row is the floor, and the `gliding` class is what switches it off
in favour of the travelling one. The active row keeps its own background either way.

### Modal

The one dialog brick: a dimmed backdrop over the page, a centered panel with a title and a
close control, and every expected dismissal — Escape, backdrop click, the ×. Body scroll is
locked while open and the panel renders through a portal, so no view has to host it.

```tsx
<Modal open={open} onClose={close} title="Delete conversation?" width={420}>{body}</Modal>
```

### ConfirmDialog

```tsx
<ConfirmDialog
  open={pendingDelete !== null}
  title="Delete conversation?"
  message={<>This permanently removes <strong>{pendingDelete?.title}</strong>…</>}
  onCancel={() => setPendingDelete(null)}
  onConfirm={remove}
/>
```

The confirm step in front of an irreversible action, composed from `Modal` + `Button`.
**Every delete in the app goes through it** — no view fires a destructive call straight from
a click. `confirmLabel` defaults to "Delete" and renders in the `.btn-danger` register.

### forms/FormCard

The card a standalone form sits in: title, optional subtitle, the fields as children
(ending in a `btn-block` Button), and the error slot. `onSubmit` is wired to a real
form submit, so Enter works from any field. The wrapper centres the card in the main
region, vertically as well as horizontally.

```tsx
<FormCard title="Sign in" subtitle="..." error={error} onSubmit={submit}>
  <TextField id="login-username" label="Username" value={username} onChange={setUsername} />
  <Button variant="primary" type="submit" className="btn-block" disabled={pending}>Sign in</Button>
</FormCard>
```

### forms/SelectField

```tsx
<SelectField id="records-department" label="Department" value={draft.department}
  options={departments.map((d) => ({ value: d.value, label: `${d.value} (${d.employees})` }))}
  onChange={setDepartment} placeholder="any department" />
```

`TextField`'s counterpart for a value that comes from a fixed set: the labelled native
`<select>` on the `.select` metrics `chat/ModelPicker` uses, inside the `.field` + label
pattern `TextField` owns. It exists because a filter must not let a reader type a value the
data does not hold — the options are whatever the server listed (`GET /records/departments`,
`GET /records/tenants`, both `FilterOption[]`), and `placeholder` renders the empty "no filter"
option. Both pickers carry the count beside the value, and both counts follow the listing they
describe, so nothing on screen is a number attached to a set nobody asked for.

### forms/TextField

The labelled text input (`type="text" | "password" | "number"`). KB writes this label+input pair
inline in its views; here it is a brick, so no view re-styles an input.

```tsx
<TextField id="login-password" label="Password" type="password" value={password}
  onChange={setPassword} autoComplete="current-password" disabled={pending} />
```

There is deliberately **no `date`**: a native date input renders its placeholder in the viewer's
locale (`dd.mm.yyyy` on this machine, `mm/dd/yyyy` on the next) with the OS calendar glyph, while
the table cells, the executed SQL and the server's own refusal all speak ISO. A date filter is a
text field carrying an ISO placeholder instead, so what a reader types is what they read back
(issue #115). Nothing is validated in the browser: the server owns the format and answers a bad
one with a sentence naming it, and a blocking `pattern` would swallow that refusal.

### forms/FieldPair

```tsx
<FieldPair>
  <TextField id="records-salary-min" label="Salary from" type="number" ... />
  <TextField id="records-salary-max" label="Salary to" type="number" ... />
</FieldPair>
```

The two bounds of one filter as a **single grid cell**, so a wrap can never leave `from` on one
row and `to` on the next: the filter grid lays out the pair, not the fields (issue #115). Purely
presentational — each field keeps its own label and id, so nothing changes for a screen reader.

### layout/AppLayout

The app shell: the `Header` over a body row of the optional left rail plus the `main`
region. `tenantBadge` is the header slot the identity badge fills once logged in — it is
passed straight through to `Header`, and stays empty on the login screen. `sidebar` is the
rail slot; empty on the login screen too, so the shell has one shape.

The shell is **exactly the viewport tall**, unlike KB's document-height one: header and rail
are flex furniture measured by layout (so nothing has to guess a header height), and
scrolling happens inside the region that owns it. `main` is a column that scrolls when a
view is taller than the shell (the login card) and hands its full height to a view that
claims it with `flex: 1` instead (the chat page, whose `.chat-log` is its own scroller).

```tsx
<AppLayout
  tenantBadge={<SessionBadge session={session} />}
  sidebar={<ConversationsSidebar store={conversations} />}
>
  {page}
</AppLayout>
```

### layout/Tabs

```tsx
<Tabs tabs={[{ id: "chat", label: "Chat", icon: "message-circle" }]} active={tab} onSelect={select} />
```

The shell's top-level sections (ADR 0014), rendered in the `Header` through `AppLayout`'s
`tabs` slot rather than by any view — the sections are siblings and none owns the others. A
`<button role="tab">` each, `aria-selected` carrying the state for a screen reader and
`.active` for an eye. The shell keeps a visited section mounted and hidden (`.tab-panel`), so
switching never costs a reader the state of the one they left.

### layout/Sidebar

```tsx
<Sidebar title="Conversations" search={<InlineSearch … />}
  actions={<Button className="side-add">New chat</Button>}>
  <GlideList>{rows}</GlideList>
</Sidebar>
```

The shell's left rail: a head row carrying the collapse control, the caps `title` and the `search`
slot, then `actions`, then the list — a full-height column whose body has its own scroll so a long
list never scrolls the page beside it, and so its right border reads as the page's divider rather
than stopping under the last row. The rows are `.rail-item` (+ `.active`): a `.rail-item-open`
label over its meta line, with an optional trailing control.

**The collapse is the brick's mechanism** (issue #114, from
[beautifului.dev](https://www.beautifului.dev)): the aside animates its own width and clips, while
the column inside it stays laid out at the expanded width whatever the state. Nothing is re-laid
out, so an icon cannot slide sideways when the copy beside it leaves — which is the whole point,
and why the collapsed state is not a second, narrower layout. Two consequences a caller has to
know about:

- The clip is `overflow: clip`, not `hidden`. A hidden box is still a scroll container, so
  focusing a control the collapse had clipped made the browser scroll the whole column sideways
  to reveal it; a clip container cannot be scrolled at all.
- Copy that would otherwise be cut off mid-word carries `rail-copy` and fades out instead, and a
  slot whose controls end up clipped takes them out of the Tab order and the accessibility tree
  itself — `useSidebarCollapsed()` is how it reads that state, so nothing is threaded through a
  view. The brick still owns the state, the way KB's `Collapsible` owns its open flag.

Everything a designer would touch — the two widths, the shared icon inset, how far the search
grows, the durations and the easing — is a custom property in the rail's block in `app.css`.

### layout/Header

Brand mark + app name + the trailing `tenantBadge` slot. One header for the whole
app; views never render their own.

### layout/Page

The `.page` container every view sits in (the shared padding). Pass
`className="section-stack"` to stack `Section` cards with the standard gap.

### layout/PageHeader

The standard page head: `eyebrow`, `title`, `subtitle`, plus optional `actions`.

### layout/Section

The titled content card every block sits in (small uppercase title above a rounded
card). Label/control rows inside it use `.settings-row` (`.settings-label` +
`.settings-name` + `.settings-help` on the left, `.settings-control` on the right).
`.mono-inline` is the inline mono chip for values like a URL.

```tsx
<Section title="Backend">
  <div className="settings-row">
    <div className="settings-label">
      <div className="settings-name">API base URL</div>
      <div className="settings-help">Set VITE_API_URL to point at another backend.</div>
    </div>
    <div className="settings-control"><span className="mono-inline">{API_BASE_URL}</span></div>
  </div>
</Section>
```

### layout/EmptyState

Centered empty/placeholder state with a leading icon.

```tsx
<EmptyState icon="message-circle">No conversations yet.</EmptyState>
```

### charts/Chart

The one chart brick. It takes the backend's **ChartSpec verbatim** — the contract
documented in `apps/backend/analytics.py`'s module docstring, which the `plot` tool
returns through the chat trace — and dispatches on `kind`:

```tsx
<Chart spec={toolResult.chart_spec} />          // height defaults to 260
```

```ts
{
  kind: "bar" | "line" | "grouped_bar" | "histogram" | "scatter" | "box",
  title, x_label, y_label,
  series_label?,                 // grouped_bar only: the legend's caption
  data: [{ y, x?, series?, x_value?, x_low?, x_high?, low?, q1?, q3?, high? }]
}
```

Every point carries `y`; which further keys it carries is fixed per kind by the backend, and
each kind reads only its own:

| kind | mark | the point's other keys |
|---|---|---|
| `bar`, `histogram` | bars — bins sit nearly flush, categories breathe and cap at KB's 38px column | `x` / `x_low`+`x_high` |
| `line` | polyline with a filled area underneath and a dot per point | `x` |
| `grouped_bar` | one bar per series inside each category's band, coloured from `--chart-1..10` with a legend under the plot | `x`, `series` |
| `scatter` | one dot per row on a **linear** x axis (the only kind that is not a band scale) | `x` (the row's name), `x_value` |
| `box` | quartile box, median rule and whiskers at the extremes inside the group's Tukey fences | `x`, `q1`, `q3`, `low`, `high` |

Band kinds label the x axis with their point labels (thinned to at most 8 so they never
collide); a histogram labels the **boundary** with the bin's lower edge and keeps the whole
range for the hover. `scatter` and `box` scale to their own spread; the bar and area kinds
keep zero in the domain so the baseline stays on the plot. Every mark carries a `<title>`
tooltip with its exact values. An empty `data` array renders the `EmptyState` brick under
the title instead of an axis-less plot.

**Numbers are never formatted here** — axis ticks, bin edges and hover values all go through
`lib/format.ts` (grouped thousands, at most two decimals), the same formatter `DataTable`
uses, so a salary reads identically as a row and as a bar.

The brick **reserves `height`** whether it plots or reports no data, and measures its own
width before the first paint rather than after it: a chart lands in the middle of a stream,
so a chart-shaped hole that then resizes would shift the transcript under the reader.

Charts are **hand-rolled SVG, no chart library** — matching the KB, which has none
either. Fixtures for every kind live in `charts/Chart.test.tsx` (`npm test`).

### chat/ChatMessage

```tsx
<ChatMessage role="user" text={turn.question} />
<ChatMessage
  role="assistant"
  text={turn.answer}
  lead={<TracePanel items={turn.items} streaming={live} />}
  footer={<Pill tone="ok">…</Pill>}
>
  {turn.error ? <p className="form-error">{turn.error}</p> : null}
</ChatMessage>
```

One turn's bubble: an icon + caps role header over the text, `user` a compact tinted
bubble and `assistant` the full-width card that holds three slots in reading order —
`lead` above the text, `children` under it, `footer` last. The trace goes in the **lead**
(ADR 0012 as amended): the steps that produced an answer are read before the answer, not
found underneath it.
The **assistant** body goes through the `Markdown` brick (answers arrive in markdown, so
`**bold**` must not show literally); the **user** body stays plain text with
`white-space: pre-wrap` — it is what the person typed, never interpreted as markup. Those
two are the whole rule: `pre-wrap` belongs to the typed question only, so the assistant's
`.msg-text.markdown-body` drops it and the rendered blocks own their own whitespace.
Structured output the model might describe (SQL, tables, charts) still has its own brick
in the trace, where it is the real thing rather than model-written markup.

### chat/Composer

```tsx
<Composer onSend={send} disabled={streaming} />
```

The question box: Enter sends, Shift+Enter starts a line, and the whole control is
disabled while a turn streams so a second turn cannot race the first on one thread.

### chat/ModelPicker

```tsx
<ModelPicker models={models} value={model} onChange={setModel} disabled={streaming} />
```

The options are whatever `GET /models` listed — never a hardcoded list (ADR 0005 as
amended) — with the `default` from the same response preselected; switching
mid-conversation is allowed. An empty list means the endpoint was unreachable: the
picker says so and the turn falls back to the server-side default.

### chat/TracePanel

```tsx
<TracePanel items={turn.items} streaming={live} />
<TracePanel items={turn.items} streaming={false} open />
```

The trace of one turn, folded by `lib/trace.ts`: the model's thinking, each tool call with its
arguments, and the **one** outcome that closes it — a result, a `retry` with its attempt
counter and fed-back reason, or a `security_event` as a red blocked state naming the layer,
kind and reason (ADR 0012). Collapsible; `open` is the state it starts in and defaults to
`streaming`. **The graph's own node transitions are not rows** (ADR 0012 as amended after
issue #87): they stay in the stream and the audit trail, and here they only say which model
round a thought belongs to. One thinking step per model round, chipped with `round n` from
the second round on; a round that streamed no thinking is no row at all. A thinking step is
**open and shimmering while its round's thinking is still arriving** and folds itself away
once it settles, leaving `Thought for 2.8s` where the label was (ADR 0012 as amended after
issue #91 — the span is this client's own clock, since the stream carries no timestamps).
A call that has not settled says `running` on the card itself. An outcome whose call was never
announced still renders, so nothing the backend said is dropped.
The same panel renders a **replayed** turn (ADR 0012 as amended): `lib/trace.ts`'s
`replayTurns` folds `GET /conversations/{id}`'s stored tool results into the same items, so a
reopened thread shows its SQL rewrite, tables and charts through these bricks rather than a
second renderer. Such a turn starts `open` — the evidence is why it is there — and carries no
reasoning, retry or step timing, because none of those are stored.

### chat/TraceStep

```tsx
<TraceStep icon="database" title="query_db" meta={<Pill tone="ok">3 rows</Pill>} tone="blocked">
<TraceStep icon="sparkles" title="Thinking" tone="muted" open={thinking}>
```

One entry on the trace rail: icon, title, right-aligned chips, body. `tone` is `default |
muted | warn | blocked`, carrying the state in a second channel next to the icon. A step
with a body is its **own disclosure** — the head becomes a button with `aria-expanded` and
the panel's chevron — so no reader is stuck with every SQL statement, table and chart open
at once. A step with nothing to show stays a plain row with no control.

`open` is the state the step is **in** until the reader clicks, not merely the one it
mounted in: a caller can hold a step open while it works and let it fold itself away once it
settles, and the reader's click wins from then on whatever the caller does after
(`choice ?? open` — the auto-state-plus-override idiom from
[beautifului.dev](https://www.beautifului.dev)'s thinking traces).

### chat/ToolResultView

```tsx
<ToolResultView content={outcome.content} data={outcome.data} />
```

A `tool_result` payload, each key through the brick that owns it: `generated_sql` +
`executed_sql` through `SqlRewrite`, `columns`/`rows` through `DataTable`,
`chart_spec` through `Chart` verbatim, `notes` as note cards (employee-written text,
quoted as data), `anomalies` as a derived table. A payload with no structured keys falls
back to the text the model itself read, so no result ever renders as nothing. A replayed
result passes `content=""`: the model-facing rendering of the same rows is not stored, and
every stored payload has structured keys, so the fallback is a live-turn path only.

## Deviations from the KB originals

- `Button` drops KB's `to` (react-router `<Link>`) variant — this app has no router
  yet, so the link branch would be dead code. Add it back with the router.
- CSS renames for clarity outside KB's view names: `.api-inline` -> `.mono-inline`,
  `.settings` (the section stack) -> `.section-stack`, `.capture-card` ->
  `.form-card`, `.capture-error` -> `.form-error`.
- `FormCard` / `TextField` have no KB counterpart component: KB writes the
  `.capture-card` + `.field` + `.input` markup inline in each view. The CSS is ported
  verbatim apart from `.form-card-wrap`; the components are new so this app has one
  owner per form shape.
- `.form-card-wrap` is the one rule that diverges: KB pads its card down from the top of a
  document that grows, which in this viewport-height shell left the login card in the upper
  third of the page. Here the wrapper's auto margins centre it in `.main` and collapse to
  zero when the card is taller than the region, so a short viewport scrolls rather than
  clipping (issue #116).
- `TenantPill` is new (KB has no tenants); its visual is KB's `.category-pill` shape
  with the accent color instead of the per-category one.
- `Markdown` drops KB's `[[wiki-link]]` chips, `[n]` citation chips and the `inline`
  mode: this app has no records to link and no cited sources, so all three branches
  would be dead code. The library set and pins are KB's (`react-markdown`,
  `remark-gfm`, `rehype-sanitize`); the `.markdown-body` CSS is verbatim.
- KB bricks not ported because nothing composes them yet (atoms, `Badge`, ...). Port from
  KB when a view needs one — never hand-roll an equivalent.
- `Modal` is KB's verbatim, with one bug fixed: KB's `.modal-backdrop` mixes an undefined
  `--bg-base`, which voids the whole declaration and leaves it with no dim layer at all.
  Here it is `--bg-page`, the token that exists.
- `ConfirmDialog` drops KB's `busy` prop: the delete here closes the dialog and lets the row
  disappear when the call resolves, so there is no pending state to label.
- `layout/Sidebar` is new as a brick. KB has no shell sidebar — each of its views hand-rolls
  the same `.wiki-sidebar` rail markup — and none of them collapse. The visual is that rail
  (`.wiki-sec-item` renamed `.rail-item`, since nothing here is a wiki, and its trailing
  count chip replaced by the row's delete control); lifting it into `AppLayout` is what keeps
  the thread list alive across the chat instead of belonging to one page.
- `InlineSearch` and `GlideList` are new, and so is the rail's collapse mechanism (issue #114):
  **KB has no disclosure-search or hover-tracking anywhere**, so their control registers
  (`.rail-search`, `.rail-glide`) are ours on KB's tokens. The rail's own empty and loading states
  stay `.sidebar-note` paragraphs rather than the `EmptyState` brick, which is a centred card
  sized for a page region, not a 272px column.
- **KB has no chat UI at all** (no bubbles, no streaming text, no composer beyond its
  one-shot Ask box), so the `chat/` bricks are new. Their visuals still come from KB:
  the bubble is its `.ask-answer` card plus the icon + caps role header it puts above an
  answer, the composer is its `.bm-composer` (textarea over an action row), the trace
  disclosure is its `Collapsible` (chevron + caps label + count chip), and the step rail
  is its `.md-item` log row with the date gutter turned into an icon rail.
- `DataTable` is KB's `.usage-table` inside its `.table-scroll` wrapper, without KB's
  `RecordsTable` pagination: a trace step shows a capped peek at the rows, not a browser.
  Its optional sortable headers are new; KB's tables do not sort, and here the sort is a
  server request rather than an in-browser reorder (ADR 0014).
- `layout/Tabs` and `forms/SelectField` are new as bricks: KB is a multi-page router app
  with no tab strip and no select anywhere. `NoteList` is new too, lifted out of
  `chat/ToolResultView` when the Notes tab needed the same cards - the trace and the tab now
  compose one brick instead of two copies drifting apart.
- `Pill` replaces KB's hardcoded hexes with the state tokens, and **adds the amber
  `.pill-warn` KB lacks** — KB's only alert tone is red, and the truncation notice needs
  its own. The same split applies to `.notice-warn`, where KB's `Notice` collapses
  `warn` into the red `error` styling.
- `CodeBlock` is KB's `.api-code` body under a header row KB has no counterpart for:
  **KB has no copy-to-clipboard control anywhere**, so the copy button is new, in a new
  `.btn-xs` register (KB's smallest button is still card-sized).
- `ModelPicker` uses a native `<select>`: **KB has no select, dropdown or listbox
  anywhere**, so it borrows `.cfg-input`'s metrics and its border-only focus instead.
- `SqlRewrite`'s pair is KB's two-column `.usage-grid` at `1fr 1fr`; KB's own diff
  (`.prop-diff`) is inline before/after chips, which cannot hold two SQL statements, and it
  has no in-place marking of a changed run anywhere, so `.sql-add` is new on our accent
  token. **KB has no container query anywhere** either — its own grids collapse on the
  viewport — so `.sql-pair`'s `@container` breakpoint is ours.
- `charts/Chart` is one brick where KB has four (`AreaTrend`, `BarTimeline`,
  `RankedBars`, `MonthDrill`), because a single backend contract feeds it: it keeps
  AreaTrend's measured-width SVG scaffolding and `.chart-grid`/`.chart-axis` chrome
  and BarTimeline's bar register, and drops KB's click-to-drill — a ChartSpec is
  one chart, not an entry point into another one. KB's per-series legend and its
  `--chart-1..10` categorical palette **are** used, by the one kind that has real
  series (`grouped_bar`); every other kind is a single series in the accent register.
  The box, whisker and scatter marks have no KB counterpart and are new.
- Toolchain: Vite 7 + `@vitejs/plugin-react` 5 rather than KB's Vite 5 line, which
  still carries dev-server advisories (`npm audit` reports one high, one moderate on
  Vite 5). The design-system port itself is version-independent.

## Adding a new brick

Reach for an existing brick first. If a genuinely new, reusable shape is needed:
check the KB library for it, port that (CSS into `app.css`), document it above, and
put every sibling view on it — never leave a one-off copy in a view.
