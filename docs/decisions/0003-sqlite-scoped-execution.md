# ADR 0003 — SQLite with emulated RLS via a scoped executor

Status: accepted (amended after issues #96 and #125)

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
3. executes on a connection opened read-only — `mode=ro` on the file URI, which
   is the load-bearing control, plus `PRAGMA query_only` as a second belt,
4. verifies structurally that the scoping applied (layer 4a) and checks the rows
   that come back against the session tenant (layer 4b).

The two read-only controls are deliberately asymmetric, and this ADR originally
claimed the wrong one carried the property. `PRAGMA query_only` is reversible by
SQL ([sqlite.org/pragma.html](https://www.sqlite.org/pragma.html)), so it is only
meaningful as a second belt because layer 2 rejects PRAGMA statements outright;
`mode=ro` at file open is what actually makes the connection read-only. ADR 0002's
[Hardening](0002-defense-in-depth-rls.md#hardening-amended-per-sourced-review)
section is authoritative on this and supersedes the earlier wording here.

AST rewrite was chosen over per-tenant SQL views (`employees_acme`, ...) because
the rewrite keeps one schema the LLM prompts against, adds no per-tenant DDL,
and makes the enforcement a pure, unit-testable function of (SQL, tenant).

## Database lifecycle: loaded at startup (added after issue #96)

Only `employees.csv` is committed; `employees.db` is derived. Nothing owned the
question "who turns one into the other", so a fresh checkout following the dev
instructions died on the first read of a file no step ever created — while
compose worked, because the image bakes the database during its build (ADR 0013).

The decision is the same shape ADR 0010 settled for the note index: **`create_app`
loads the database from the committed CSV before the API serves anything, through
an injected seam, and skips the work when the file already holds rows**
(`db.employee_rows`, the counterpart to `vector_store_rows`). A restart therefore
costs one row count, and the image's baked database is left exactly as built — the
two mechanisms cannot fight, because the runtime one only acts on an empty file.

It differs from the index in one way that matters: **a failed load is fatal.**
Every tool reads that file, so a process that cannot build it has nothing to
serve, and the failure is raised out of `create_app` rather than logged and
skipped. ADR 0010's graceful degradation applies to a dependency that is *not* on
the critical path; the inverse case is fail-fast, and booting an API whose every
answer would be an error is strictly worse than refusing to boot.

Keeping the bake in the image as well is the
[twelve-factor build/release/run](https://12factor.net/build-release-run)
separation: deriving fixed data at build time keeps container start instant.

Since issue #125 that baked file lands in the container's mounted data directory
rather than its writable layer, because `db.py` derives `audit.db` and
`vectors.db` beside whichever database it is handed and those two must survive a
restart ([ADR 0013](0013-deployment-cicd.md) as amended). Nothing about the
lifecycle changes — the row count still decides, and a first boot on a machine
with no volume yet still loads the CSV — but one consequence is worth stating:
because a populated database is left alone, a regenerated dataset reaches a
running deployment only after the volume is reset, not after an image rebuild.

Where to put the *dev-mode* build — a documented manual step, a separate script,
or the process itself — is a modeling judgment with no authoritative source; the
process itself was chosen because a step a reviewer has to read about is a step a
reviewer can miss, which is exactly how this was found.

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
