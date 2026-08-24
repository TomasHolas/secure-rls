# Evaluation harness report - prompt guardrails off

This position has not been run against a live model yet.

The run that fills this file is `uv run python -m evals --no-guardrails`, from `apps/backend`.
It overwrites this file whole. Why the off position is the artifact worth having is argued in
[ADR 0002](../../../docs/decisions/0002-defense-in-depth-rls.md), under "Demonstrating the
claim"; the on-position scorecard is [`report.md`](report.md).
