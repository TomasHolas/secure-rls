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
  layout/          AppLayout, Header, Page, PageHeader, Section, EmptyState (+ index barrel)
```

CSS for every brick lives in `styles/app.css`; colors, spacing, radii, motion and
fonts come from `styles/tokens.css`.

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
**self-hosted subset font** (`public/fonts/material-symbols-subset.v2.woff2`,
`@font-face` in `tokens.css`). The map mirrors exactly the glyphs in that subset, so
to add an icon: add a mapping in `MATERIAL_SYMBOLS`, then regenerate the subset woff2
— fetch
`https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined&icon_names=<comma,list>`
(the `icon_names` list must cover **every** value in `MATERIAL_SYMBOLS`), download the
linked woff2 and save it under a **bumped version suffix**
(`...subset.v3.woff2`, ...), updating the `@font-face` url in `tokens.css` to match.
The version bump is the cache-bust: `public/` assets are not fingerprinted by the
bundler, so reusing the filename makes browsers serve the stale font and new glyphs
render as literal text. A name whose ligature is not in the subset renders as literal
text, so keep the map and the subset in sync.


The anteater brand mark as a `currentColor` SVG, sized by `size` (height in px).
`public/anteater.png` is the same mark as a raster (used as the touch icon);
`public/favicon.png` is the tab icon.

### layout/AppLayout

The app shell: the sticky `Header` plus the scrolling `main` region.
`tenantBadge` is the header slot the tenant badge fills once login lands (M4b) —
it is passed straight through to `Header`.

```tsx
<AppLayout tenantBadge={<TenantBadge tenant={tenant} />}>{page}</AppLayout>
```

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

## Deviations from the KB originals

- `Button` drops KB's `to` (react-router `<Link>`) variant — this app has no router
  yet, so the link branch would be dead code. Add it back with the router.
- CSS renames for clarity outside KB's view names: `.api-inline` -> `.mono-inline`,
  `.settings` (the section stack) -> `.section-stack`.
- KB bricks not ported because nothing composes them yet (Modal, ConfirmDialog,
  tables, charts, atoms, ...). Port from KB when a view needs one — never hand-roll
  an equivalent.
- Toolchain: Vite 7 + `@vitejs/plugin-react` 5 rather than KB's Vite 5 line, which
  still carries dev-server advisories (`npm audit` reports one high, one moderate on
  Vite 5). The design-system port itself is version-independent.

## Adding a new brick

Reach for an existing brick first. If a genuinely new, reusable shape is needed:
check the KB library for it, port that (CSS into `app.css`), document it above, and
put every sibling view on it — never leave a one-off copy in a view.
