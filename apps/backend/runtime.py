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
    """Scoped-executor knobs (ADRs 0002, 0007)."""

    max_result_rows: int
    query_timeout_ms: int


@dataclass(frozen=True)
class AgentConfig:
    """Agent and model knobs (ADRs 0005, 0011)."""

    model: str
    embed_model: str
    max_tool_retries: int


@dataclass(frozen=True)
class AuthConfig:
    """Auth knobs (ADR 0009)."""

    token_ttl_minutes: int


@dataclass(frozen=True)
class Runtime:
    """The full typed runtime configuration."""

    dataset: DatasetConfig
    db: DbConfig
    agent: AgentConfig
    auth: AuthConfig


@lru_cache(maxsize=1)
def runtime() -> Runtime:
    """Load and cache the typed runtime configuration."""
    raw = json.loads(_RUNTIME_PATH.read_text())
    return Runtime(
        dataset=DatasetConfig(**raw["dataset"]),
        db=DbConfig(**raw["db"]),
        agent=AgentConfig(**raw["agent"]),
        auth=AuthConfig(**raw["auth"]),
    )
