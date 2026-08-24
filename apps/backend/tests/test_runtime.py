"""Scaffold tests: the typed runtime loader (issue #13)."""

from runtime import runtime


def test_runtime_loads_typed_values():
    rt = runtime()
    assert isinstance(rt.dataset.seed, int)
    assert rt.dataset.rows == 1000
    assert rt.db.max_result_rows == 200
    assert rt.db.query_timeout_ms == 2000
    assert rt.analytics.histogram_bins == 10
    assert rt.analytics.max_scan_rows == 5000
    assert rt.agent.embed_model == "nomic-embed-text"
    assert rt.agent.max_tool_retries == 3
    assert isinstance(rt.agent.prompt_guardrails, bool)
    assert rt.rag.top_k == 5
    assert rt.rag.embed_batch_size == 64
    assert rt.auth.token_ttl_minutes == 120
    assert rt.auth.refresh_within_minutes == 30


def test_the_shipped_default_keeps_the_prompt_guardrails_on():
    """The shipped default is a contract: on. Off is a demo position, never what we release.

    This is the one knob a demo is expected to flip, so it gets its own named test rather than
    riding along in the type check above - a suite that goes red for a flipped knob must say
    exactly that, not raise a bare AssertionError somewhere in a value sweep (issue #102 review).
    """
    assert runtime().agent.prompt_guardrails is True, (
        "runtime.json ships agent.prompt_guardrails = true. This failure means the working tree "
        "is in the demo position, not the shipped one: the prompt's self-policing rules are off. "
        "That is a valid way to run the demo and an invalid way to release - set it back to true "
        "before merging, and grade the off position with `python -m evals --no-guardrails` "
        "instead of by editing the default."
    )


def test_the_history_send_budget_leaves_room_for_the_answer():
    """What one model call may send must fit the window beside the generation it is allowed.

    This is the arithmetic of issue #131: a budget that did not subtract `max_output_tokens` and a
    headroom for the estimate's own error would let a prompt fill the window on its own, which is
    the request the endpoint refused. The floor is at least one turn, or a trim could drop the
    question the turn exists to answer.
    """
    agent = runtime().agent
    assert agent.history_headroom_tokens > 0
    assert agent.history_chars_per_token > 0
    assert agent.min_history_turns >= 1
    assert agent.context_window - agent.max_output_tokens - agent.history_headroom_tokens > 0


def test_refresh_window_fits_inside_the_token_lifetime():
    rt = runtime()
    assert 0 < rt.auth.refresh_within_minutes < rt.auth.token_ttl_minutes


def test_tenant_split_sums_to_one():
    rt = runtime()
    assert set(rt.dataset.tenant_split) == {"acme", "beta", "gamma"}
    assert abs(sum(rt.dataset.tenant_split.values()) - 1.0) < 1e-9


def test_runtime_is_cached_singleton():
    assert runtime() is runtime()
