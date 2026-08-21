# ADR 0006 — Reuse of the knowledgebase design system

Status: accepted

## Context

The author maintains a product family sharing one visual identity: the
knowledgebase (KB) defines the design system (dark-only, Geist fonts, brand
green `#34a86a`, the anteater logo, tokenized spacing/radii/motion, lego-brick
components); modelbench already mirrors it as a sibling product. secure-rls
should look and feel like the third sibling, not a one-off.

## Decision

Copy from the KB repo (`../knowledgebase/apps/frontend`) exactly what this app
needs, keeping the KB as the tracking source of truth:

- `src/styles/tokens.css` (verbatim; KB's copy wins on divergence),
- the self-hosted font files (Geist, Geist Mono, the Material Symbols subset),
- the logo assets (`anteater.png`, favicon),
- only the bricks the app uses (Button, Icon, Logo, layout, chart bricks, ...),
  ported as needed — never hand-rolled equivalents.

Port scheme continues the sibling convention: KB 8000/3000, modelbench
8001/3001, **secure-rls 8002/3002**.

New reusable UI elements follow the lego-brick rule: create the brick first in
`src/components/`, then use it everywhere.

## Consequences

- The frontend starts with a mature visual system instead of a blank page —
  most M4 time goes to the chat/trace UX, not styling.
- Divergence risk: copied tokens can drift from KB. Accepted for a case study;
  the header comment in the copied `tokens.css` names the source.
- The demo shows a personal product line, which strengthens the "authentic
  ownership" success criterion.

## Alternatives

- **A fresh design or a UI kit (MUI/shadcn)** — rejected: slower to a polished
  result and generic-looking; the sibling identity is a feature.
- **Sharing via a published package** — correct long-term, overkill for three
  repos maintained by one person; noted as future evolution.
