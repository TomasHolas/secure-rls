# ADR 0003 — SQLite with emulated RLS via a scoped executor

Status: accepted

## Context

The assignment prescribes loading the CSV into SQLite (or pandas). SQLite has
no native row-level security — unlike PostgreSQL, where `CREATE POLICY` enforces
RLS in the engine. RLS must therefore be emulated in the application layer, and
that emulation must be impossible to route around.

## Decision

One scoped executor in `db.py` — the only module in the repo allowed to open a
SQLite connection (enforced by convention and by a test that greps for
`sqlite3.connect` outside `db.py`). The executor:

1. accepts only SQL that passed the `security.py` validator (layer 2),
2. rewrites every `employees` reference in the AST to
   `(SELECT * FROM employees WHERE tenant_id = ?)` using sqlglot, binding the
   tenant as a parameter — never string interpolation,
3. executes on a read-only connection (`PRAGMA query_only`),
4. applies the egress row check (layer 4).

AST rewrite was chosen over per-tenant SQL views (`employees_acme`, ...) because
the rewrite keeps one schema the LLM prompts against, adds no per-tenant DDL,
and makes the enforcement a pure, unit-testable function of (SQL, tenant).

## Consequences

- The executor is a single lego brick: agent tools, stats, anomaly detection,
  and the eval harness all call it; there is no second path to the data.
- sqlglot becomes a core dependency, used by both layer 2 and layer 3.
- The demo can show the rewritten SQL side by side with the generated SQL —
  strong live evidence of enforcement.

## Alternatives

- **PostgreSQL with native `CREATE POLICY` RLS** — the production answer and the
  planned "future evolution" talking point; rejected here for setup weight
  (the assignment wants SQLite and easy local runs).
- **Per-tenant views + read-only role** — workable, but per-tenant DDL and a
  mapping table instead of one pure function.
- **Pandas-only filtering** — no SQL story at all; weaker demonstration of
  securing generated SQL, which is the interesting risk.
