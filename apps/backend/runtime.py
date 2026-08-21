"""Typed view over runtime.json - the single home of every tunable value."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_RUNTIME_PATH = Path(__file__).parent / "runtime.json"


@dataclass(frozen=True)
class DatasetConfig:
    """Dataset generator knobs (ADR 0008)."""

    seed: int
    rows: int
    tenant_split: dict[str, float]
    poisoned_fraction: float


@dataclass(frozen=True)
class DbConfig:
    """Scoped-executor knobs, including the engine caps for untrusted SQL (ADRs 0002, 0007)."""

    max_result_rows: int
    query_timeout_ms: int
    max_sql_length: int
    max_expr_depth: int
    max_compound_select: int
    max_vdbe_ops: int
    max_like_pattern_length: int


@dataclass(frozen=True)
class AnalyticsConfig:
    """Structured-analytics knobs: histogram binning and the in-Python scan budget (ADR 0011)."""

    histogram_bins: int
    max_histogram_bins: int
    max_scan_rows: int


@dataclass(frozen=True)
class AgentConfig:
    """Agent and model knobs (ADRs 0005, 0011)."""

    model: str
    embed_model: str
    max_tool_retries: int


@dataclass(frozen=True)
class RagConfig:
    """Retrieval and embedding knobs (ADR 0010)."""

    top_k: int
    embed_batch_size: int
    embed_timeout_s: float


@dataclass(frozen=True)
class AuthConfig:
    """Auth knobs (ADR 0009)."""

    token_ttl_minutes: int


@dataclass(frozen=True)
class ConversationsConfig:
    """Conversation registry knobs (ADR 0012)."""

    title_max_chars: int


@dataclass(frozen=True)
class Runtime:
    """The full typed runtime configuration."""

    dataset: DatasetConfig
    db: DbConfig
    analytics: AnalyticsConfig
    agent: AgentConfig
    rag: RagConfig
    auth: AuthConfig
    conversations: ConversationsConfig


@lru_cache(maxsize=1)
def runtime() -> Runtime:
    """Load and cache the typed runtime configuration."""
    raw = json.loads(_RUNTIME_PATH.read_text())
    return Runtime(
        dataset=DatasetConfig(**raw["dataset"]),
        db=DbConfig(**raw["db"]),
        analytics=AnalyticsConfig(**raw["analytics"]),
        agent=AgentConfig(**raw["agent"]),
        rag=RagConfig(**raw["rag"]),
        auth=AuthConfig(**raw["auth"]),
        conversations=ConversationsConfig(**raw["conversations"]),
    )
