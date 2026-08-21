"""The evaluation harness: live-model runs that never belong to CI (ADR 0004).

Everything here talks to a real Ollama endpoint and is run by hand, which is why no test
imports it: CI proves the deterministic RLS layers with a mocked LLM, and the harness proves
what only a live model can show. Each tool commits its scored report next to its code.

- `model_gate` (issue #20): the M2 tool-calling gate behind the ADR 0005 model pick.
"""
