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

    `model` is the preferred chat model, not a guarantee: the endpoint decides what it serves, so
    the API honors this id when the live chat-capable list carries it and falls back to a served
    one when it does not (ADR 0005 as amended). `embed_model` has no fallback - the retrieval
    path needs that exact model.

    Four of them bound one turn (ADR 0011 as amended, OWASP LLM10). `max_tool_retries` bounds
    the retries of a single call and `max_tool_iterations` the number of tool rounds the turn may
    take at all; `turn_deadline_s` is its wall-clock budget; `max_output_tokens` and
    `context_window` are the model client's own generation bounds (`num_predict` / `num_ctx`).

    Three more bound what a turn SENDS, so a long thread trims its oldest turns instead of being
    refused by the endpoint for overflowing `context_window` (ADR 0011 as amended, issue #131).
    The budget one model call may occupy is `context_window - max_output_tokens -
    history_headroom_tokens`, since the window has to hold the answer as well as the prompt;
    `history_chars_per_token` is the divisor of the deterministic character-count estimate that
    budget is measured with - an estimate with a margin, never a tokenizer's count; and
    `min_history_turns` is the floor no trimming goes below, so the newest turns survive even
    when one of them is huge.

    `prompt_guardrails` is the one knob that changes prompt text and nothing else (ADR 0011 as
    amended): on, the rendered system prompt carries the rules that ask the model to police
    data-borne instructions and states the tenant scope; off, those blocks are omitted so the
    RLS layers are demonstrated refusing an attack the model actually attempted (ADR 0002).
    It defaults to on because the rules improve answer quality; they were never a boundary, so
    turning them off cannot change what any layer enforces.
    """

    model: str
    embed_model: str
    max_tool_retries: int
    max_tool_iterations: int
    max_output_tokens: int
    context_window: int
    history_headroom_tokens: int
    history_chars_per_token: float
    min_history_turns: int
    turn_deadline_s: float
    thinking: bool
    duration_decimals: int
    prompt_guardrails: bool


@dataclass(frozen=True)
class BrowseConfig:
    """Records and Notes browsing knobs (ADR 0014).

    `page_size` is the default page a listing serves; its ceiling is not a knob but the
    executor's `db.max_result_rows` row cap (ADR 0007), since a larger page could not be
    served whole. `max_filter_chars` bounds the text a filter box may send, and
    `max_search_hits` the hits one notes search may ask the retrieval path for.
    """

    page_size: int
    max_filter_chars: int
    max_search_hits: int


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
    prose rather than a label, so it is rejected in favor of the fallback.
    `title_message_chars` caps how much of one message the titling prompt carries, so a long
    answer cannot push the instruction out of the titling model's context.
    `title_turns` is the window: a thread is named again after each of its first that many turns
    and never after (issue #118) - a thread that opened with a greeting is named from the real
    question that follows it, and a settled thread keeps the name it has. `title_timeout_s`
    bounds the titling call - it runs outside the turn, but not forever.

    Four ceilings bound the turn history a thread accumulates for replay (ADR 0012 as amended),
    since "persist the whole turn" is otherwise unbounded growth in a store served in one
    response: `max_turn_events` is how many events of one turn are kept at all (the terminal
    frame always is, so a capped turn still reports how it ended), `max_turn_payloads` how many
    of that turn's tool results keep their data payload - set to the tool-round cap
    `agent.max_tool_iterations`, so a turn that spent its whole round budget still replays every
    round - `max_reasoning_chars` how much of one model round's thinking is kept, and
    `max_history_turns` how many of a thread's newest turns keep their history. The row window
    inside a payload is not a knob of its own: it is the executor's `db.max_result_rows` cap
    (ADR 0007).
    """

    title_max_chars: int
    generated_title_max_words: int
    generated_title_max_chars: int
    generated_title_reject_chars: int
    title_message_chars: int
    title_turns: int
    title_timeout_s: float
    max_turn_events: int
    max_turn_payloads: int
    max_reasoning_chars: int
    max_history_turns: int


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
    browse: BrowseConfig
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
        browse=BrowseConfig(**raw["browse"]),
        rag=RagConfig(**raw["rag"]),
        auth=AuthConfig(**raw["auth"]),
        conversations=ConversationsConfig(**raw["conversations"]),
        api=ApiConfig(**raw["api"]),
    )
