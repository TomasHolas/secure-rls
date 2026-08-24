# Development process

How this repo is built, tested and shipped. The method is part of the
deliverable — the assignment asks for agentic development, and the artifacts
below are the evidence, so none of it is a claim you have to take on faith.

## Agentic development

This repo was built by AI agents (Claude Code), design-first.

- **`CLAUDE.md` is machine-readable project memory.** Not a style guide: it
  carries what the project is, the milestone plan, a where-to-make-which-change
  table, the hard rules, and the engineering standards. Any fresh agent session
  reads it and resumes with full context. It is treated as code — a change that
  makes it stale updates it in the same commit.
- **14 ADRs before the code they govern.** Each records context, the decision,
  consequences, the alternatives rejected and why, and cites published practice —
  OWASP, RFCs, sqlite.org, Microsoft and AWS multi-tenant guidance, BLS for the
  dataset. Where no authoritative source exists, the ADR says so and labels the
  choice a judgment. Several are amended in place as reality pushed back, and the
  amendments carry the real decisions. Index: [INDEX.md](INDEX.md).
- **A GitHub issue queue as the work plan.** Epics #2-#7 track the first
  implementation issues #13-#32, and the later waves filed their own — the queue
  has run to #135. Each issue names its preflight reading and its **binding
  contracts** — signatures and data shapes that are law for parallel work.
  Changing one means amending the ADR and the issue text first, then the code.
- **Branch to PR to merge, per issue, with CI gating.** No commits to `main`.
  ~225 commits and ~70 merged PRs, each closing an issue, each rebased on `main`
  before merge because parallel branches landed often.
- **A bug-triage round driven by live testing.** After the vertical slice worked,
  a live pass produced issues #45, #57, #60, #66-#72 — real defects, filed with
  file-and-line root-cause analysis rather than symptoms. Issue #66 is the
  clearest example: five symptoms traced to one root cause, and the fix amended
  three ADRs.

The issue and PR history is the audit trail. `git log`, the closed issues, and
the ADR amendment lines together show what was decided, when, and what changed
its mind.

## Tests

Network-free, key-free, no Ollama — the deterministic layers are testable
without any model, which is the whole point of
[ADR 0004](decisions/0004-testing-and-eval-strategy.md).

```bash
cd apps/backend && uv run pytest -q     # 1026 tests
cd apps/frontend && npm test            # 310 tests, 20 files
```

The backend suite is weighted toward the boundary: 272 tests on the SQL
validator alone (a hostile-SQL corpus), 168 on the executor — including the ones
that prove the browse tabs' unscoped read still answers to the validator, the
authorizer, the row cap and the deadline, that it is audited, and that no built
agent tool is closed over it — 86 on the browse templates, and 131 on the API
edge including JWT tampering — wrong signature, `alg=none`, expired, missing —
mapped one-to-one onto the RFC 8725 requirements.

The adversarial corpora run in **both prompt-guardrail positions** on every
`pytest` invocation, which is what proves the switch reaches no enforcement layer
([ADR 0002](decisions/0002-defense-in-depth-rls.md) as amended).

## CI/CD

One workflow, [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), six
jobs. CI runs on every pull request and every push to `main`; CD runs on `main`
pushes only, after all five CI jobs pass
([ADR 0013](decisions/0013-deployment-cicd.md)).

| Job | What it proves |
|---|---|
| `backend (ruff + pytest)` | Lint clean, 1026 tests green |
| `dataset (regenerate + diff)` | `employees.csv` and `poisoned_manifest.json` are exactly what the seeded generator produces — nothing hand-edited |
| `frontend (build)` | `tsc` + `vite build` succeed, 310 vitest tests green |
| `evals (mocked harness)` | The evaluation harness still runs: its ask list renders, then the full suite drives 171 turns through the real graph and layers on a scripted model, failing on any leak or any failed ask |
| `images (compose build)` | Both Dockerfiles build |
| `cd (publish images to GHCR)` | Backend and frontend images pushed to `ghcr.io/tomasholas/secure-rls-*`, tagged `latest` and the commit SHA |

CI never needs a model, a secret or network access to the tailnet. Live-model
work is the eval harness, run by hand, with its report committed.
