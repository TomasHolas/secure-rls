"""Fixtures shared across the suites, starting with the prompt-guardrail switch (issue #102).

`guardrails` is parametrized over both positions of `agent.prompt_guardrails` and is deliberately
not autouse: a module opts in with a one-line autouse fixture of its own, and the enforcement
suites (`test_security.py`, `test_db.py`) do exactly that, so every refusal, every rewrite and
every egress check in them is proved twice - once with the prompt's self-policing rules rendered
and once without. ADR 0002's claim is that no prompt line is a boundary; a layer that started
reading the knob would show up here as a behavioural difference between the two runs.

The position is flipped globally rather than per module: `runtime()` is an `lru_cache` over one
JSON file, so pointing the loader at a rewritten copy and clearing the cache is seen by every
module that imported it, with no module-by-module patching to forget. The cache is cleared again
on the way out so the next test reads the committed file.
"""

import json
from pathlib import Path

import pytest

import runtime as runtime_module

RUNTIME_JSON = Path(runtime_module.__file__).resolve().parent / "runtime.json"


@pytest.fixture(params=[True, False], ids=["guardrails-on", "guardrails-off"])
def guardrails(request, monkeypatch, tmp_path):
    """Put `agent.prompt_guardrails` in one position for the test, and yield the position."""
    enabled = request.param
    raw = json.loads(RUNTIME_JSON.read_text())
    raw["agent"]["prompt_guardrails"] = enabled
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(runtime_module, "_RUNTIME_PATH", path)
    runtime_module.runtime.cache_clear()
    yield enabled
    runtime_module.runtime.cache_clear()
