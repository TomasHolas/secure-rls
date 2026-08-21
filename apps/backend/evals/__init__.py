"""The evaluation harness: model runs that produce the committed scorecards (ADR 0004).

Every module here drives the real thing end to end - `build_agent` over the real tools, the
committed `employees.csv` through `db.init_db`, the real scoped executor - because a harness
that graded a mock would prove nothing. The live modes are run by hand against an Ollama
endpoint and commit their reports next to the code; the mocked mode replaces only the model and
the embedder, so CI can prove the harness itself executes without a network (ADR 0013).

- `harness`: the bricks the suites share - the workspace, trace collection, the mechanical leak
  check against CSV ground truth, and the markdown helpers.
- `correctness` (issue #29): 25 asks per tenant graded against pandas ground truth.
- `adversarial` (issue #29): the attacks, graded on zero foreign rows, notes and names.
- `mocked` (issue #29): the scripted model and hashed embedder behind `--mocked`.
- `__main__` (issue #29): `python -m evals` - both suites, every tenant, one `report.md`.
- `model_gate` (issue #20): the M2 tool-calling gate behind the ADR 0005 model pick.
"""
