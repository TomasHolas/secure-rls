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
    assert rt.rag.top_k == 5
    assert rt.rag.embed_batch_size == 64
    assert rt.auth.token_ttl_minutes == 120
    assert rt.auth.refresh_within_minutes == 30


def test_refresh_window_fits_inside_the_token_lifetime():
    rt = runtime()
    assert 0 < rt.auth.refresh_within_minutes < rt.auth.token_ttl_minutes


def test_tenant_split_sums_to_one():
    rt = runtime()
    assert set(rt.dataset.tenant_split) == {"acme", "beta", "gamma"}
    assert abs(sum(rt.dataset.tenant_split.values()) - 1.0) < 1e-9


def test_runtime_is_cached_singleton():
    assert runtime() is runtime()
