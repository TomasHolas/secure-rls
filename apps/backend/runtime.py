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
    """Agent and model knobs (ADRs 0005, 0011, 0012).

    Four of them bound one turn (ADR 0011 as amended, OWASP LLM10). `max_tool_retries` bounds
    the retries of a single call and `max_tool_iterations` the number of tool rounds the turn may
    take at all; `turn_deadline_s` is its wall-clock budget; `max_output_tokens` and
    `context_window` are the model client's own generation bounds (`num_predict` / `num_ctx`).
    """

    model: str
    embed_model: str
    max_tool_retries: int
    max_tool_iterations: int
    max_output_tokens: int
    context_window: int
    turn_deadline_s: float
    thinking: bool
    duration_decimals: int


@dataclass(frozen=True)
class RagConfig:
    """Retrieval and embedding knobs (ADR 0010)."""

    top_k: int
    embed_batch_size: int
    embed_timeout_s: float


@dataclass(frozen=True)
class AuthConfig:
    """Auth knobs (ADR 0009): the token lifetime and the sliding-refresh window inside it."""

    token_ttl_minutes: int
    refresh_within_minutes: int


@dataclass(frozen=True)
class ConversationsConfig:
    """Conversation registry and titling knobs (ADR 0012 as amended).

    `title_max_chars` caps every stored title. The generated ones are held tighter: the word
    count is what the titling prompt asks the model for (guidance), `generated_title_max_chars`
    is what the sanitizer enforces, and output longer than `generated_title_reject_chars` is
    prose rather than a label, so it is rejected in favor of the first-message fallback.
    `title_timeout_s` bounds the titling call - it runs outside the turn, but not forever.

    The two stored-result ceilings bound the tool evidence a thread accumulates for replay (ADR
    0012 as amended): `max_stored_results_per_turn` payloads of one turn are kept - set to the
    tool-round cap `agent.max_tool_iterations`, so a turn that spent its whole round budget still
    replays every round - and only the newest `max_stored_result_turns` turns of a thread keep
    theirs. The row window inside a payload is not a knob of its own: it is the executor's
    `db.max_result_rows` cap (ADR 0007).
    """

    title_max_chars: int
    generated_title_max_words: int
    generated_title_max_chars: int
    generated_title_reject_chars: int
    title_timeout_s: float
    max_stored_results_per_turn: int
    max_stored_result_turns: int


@dataclass(frozen=True)
class ApiConfig:
    """REST edge knobs (ADR 0012): the model-list proxy timeout and the untitled-thread title."""

    models_timeout_s: float
    default_title: str


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
    api: ApiConfig


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
        api=ApiConfig(**raw["api"]),
    )
