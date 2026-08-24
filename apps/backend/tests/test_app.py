"""Suite for the REST edge (issues #23, #72, ADR 0012).

Network-free by construction: the app factory takes the turn runner, the model lister, the
capability checker, the titler, the registry, the data-store loader, the note indexer, the note
search and the checkpointer cleanup as arguments, plus the `db_path` they all read, so no test
reaches Ollama, the committed dataset or the real state files. The two loader tests are the
exception by necessity - they call the production loader itself, on a tmp database with a
two-row CSV standing in for the committed one. `FakeRunner`
records the keyword arguments it was called with - which is how the tenant-in-body tests prove the
agent was built for the token's tenant and not the body's - and replays a fixed ADR 0012 event
sequence so the SSE framing assertions are exact. `BreakingRunner` is its opposite number: it
yields part of a turn and then raises, which is what the stream-termination contract exists for.

The model list the endpoint reports includes the embedding model this app itself uses for RAG,
because that is the live situation the filter exists for; `FakeCapabilities` answers `/api/show`
from a canned map and counts the lookups, which is how the cache is asserted.

The sliding-session tests (ADR 0009 as amended) sign their own tokens with a chosen expiry
second rather than logging in, because what is under test is the remaining lifetime: inside the
refresh window a response carries `X-Refreshed-Token`, outside it does not, and an expired
token is still a 401 that refreshes nothing. `ExpiringRunner` makes the expiry land while the
SSE stream is open, so "an in-flight turn survives it" is asserted rather than argued - it
advances a `FrozenClock` injected over the one PyJWT verifies `exp` against, so the boundary is
crossed on demand instead of being raced against the wall clock.

The registry here is the real `ConversationRegistry` on a tmp_path file: thread scoping is the
security property under test, so faking it would test nothing. The transcript seam is a fake
holding canned exchanges per thread and recording every thread it was asked for - what belongs
here is that the endpoint serves the transcript in order and never reads one for a thread the
caller does not own; reconstructing it from a real checkpoint is `test_agent.py`'s job.

The stored turn history (issues #70, #90) is asserted end to end here rather than argued: a turn
that reasoned, called a tool and got a chart back is streamed, and the conversation is then fetched
to see that whole turn served beside the transcript, under the turn the transcript counts - and a
turn that was retried and then refused replays with the layer that fired and the prompt-guardrail
position that produced it. The stored events are also compared against what went on the wire, which
is the property the recording position buys. The registry is real, so what the endpoint cannot leak
is real too - a foreign fetch is the same 404 that reads neither transcript nor history. What one
turn's history may hold belongs to `test_turns.py` and how long a thread keeps it to
`test_conversations.py`.

The browse endpoints (issue #88, ADR 0014) get a real database: a five-row inline dataset loaded
into tmp_path, two tenants, with beta's rows planted to answer acme's filters. Faking `browse.py`
here would test the wiring and nothing else - what belongs at this layer is that the tenant the
rows are fetched for is the token's and never the query string's, so the isolation the tabs
demonstrate is asserted through the HTTP surface the browser actually uses. The notes search is
the one browse seam that IS faked, because it is the only one that would need to embed: `FakeNotes`
records the query, tenant and k it was handed, which is how "the tab calls the agent's retrieval
path, for the token's tenant, with the configured k" is asserted without an endpoint.

`FakeTitler` is the titling seam (`PATCH /conversations/{id}`, ADR 0012 as amended): it answers
with a canned title or raises like a dead endpoint, which is how "a titling failure leaves the
thread with the title it had" is asserted without a model. What belongs here rather than in
`test_titles.py` is the endpoint's own contract - the identity check happens before the
transcript is read and before the model is called, and the response carries the stored row.
"""

import csv
import json
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from jwt import api_jwt
from langchain_core.messages import HumanMessage

import app
import db
from agent import STATUS_FAILED, Message
from app import (
    REFRESHED_TOKEN_HEADER,
    ModelEndpointError,
    bounded_model,
    cached_capabilities,
    create_app,
    thinking_checker,
)
from auth import SECRET_ENV_VAR, AuthError
from browse import DEFAULT_SORT
from conversations import ConversationRegistry
from db import VERDICT_APPROVED, init_db
from rag import RetrievalUnavailable
from runtime import runtime

TEST_SECRET = "a1" * 32
SHORT_SECRET = "too-short"

ALICE = ("alice@acme", "demo-acme")
BOB = ("bob@beta", "demo-beta")
ACME = "acme"
BETA = "beta"

CHAT_MODELS = ["fake-model:1b", "other-model:3b"]
EMBED_MODEL = f"{runtime().agent.embed_model}:latest"
SERVED_MODELS = [*CHAT_MODELS, EMBED_MODEL]
CAPABILITIES: dict[str, list[str] | None] = {
    CHAT_MODELS[0]: ["completion"],
    CHAT_MODELS[1]: ["completion", "tools"],
    EMBED_MODEL: ["embedding"],
}
CHOSEN_MODEL = CHAT_MODELS[1]
UNKNOWN_MODEL = "nonexistent-model:9b"
SERVED_DEFAULT = min(CHAT_MODELS)

ANSWER = "acme has 6 employees"
QUESTION = "how many employees?"
TRANSCRIPT = (
    Message(role="user", content=QUESTION),
    Message(role="assistant", content=ANSWER),
    Message(role="user", content="and how many of them in sales?"),
    Message(role="assistant", content="one of them is in sales"),
)
THOUGHT = "counting the rows this tenant can see"
USAGE = {"input_tokens": 250, "output_tokens": 28, "duration_s": 1.75}
EVENTS = (
    {"type": "node_start", "node": "reason"},
    {"type": "reasoning", "text": THOUGHT},
    {"type": "token", "text": ANSWER},
    {"type": "done", "status": "ok", "answer": ANSWER, **USAGE},
)
STARTED = ({"type": "node_start", "node": "reason"}, {"type": "token", "text": "acme has"})
CHART_SPEC = {
    "kind": "bar",
    "title": "Headcount by department",
    "x_label": "department",
    "y_label": "headcount",
    "data": [{"x": "Engineering", "y": 12}],
}
PLOTTED = (
    {"type": "node_start", "node": "reason"},
    {"type": "reasoning", "text": THOUGHT},
    {"type": "node_start", "node": "validate"},
    {
        "type": "tool_call",
        "id": "c1",
        "tool": "plot",
        "args": {"kind": "bar", "column": "department"},
    },
    {"type": "node_start", "node": "execute_tool"},
    {
        "type": "tool_result",
        "id": "c1",
        "tool": "plot",
        "content": "chart displayed to the user",
        "withheld": 0,
        "data": {"chart_spec": CHART_SPEC},
    },
    {"type": "token", "text": ANSWER},
    {"type": "done", "status": "ok", "answer": ANSWER, **USAGE},
)
FOREIGN_SQL = "SELECT * FROM employees WHERE tenant_id = 'beta'"
RETRY_REASON = "the statement did not parse"
REFUSAL_LAYER = "scoped execution"
REFUSAL_REASON = "the query reaches outside the tenant's rows"
REFUSED = (
    {"type": "tool_call", "id": "c1", "tool": "query_db", "args": {"sql": FOREIGN_SQL}},
    {
        "type": "retry",
        "id": "c1",
        "tool": "query_db",
        "layer": "query validation",
        "kind": "malformed_sql",
        "attempt": 1,
        "max_attempts": 3,
        "reason": RETRY_REASON,
    },
    {"type": "tool_call", "id": "c2", "tool": "query_db", "args": {"sql": FOREIGN_SQL}},
    {
        "type": "security_event",
        "id": "c2",
        "tool": "query_db",
        "layer": REFUSAL_LAYER,
        "kind": "policy_violation",
        "reason": REFUSAL_REASON,
    },
    {"type": "token", "text": "I cannot answer that."},
    {
        "type": "done",
        "status": "blocked",
        "answer": "I cannot answer that.",
        "prompt_guardrails": False,
        **USAGE,
    },
)
# What a failing run must not put on the wire, whatever the exception carrying it says.
SECRET_HOST = "http://ollama.internal:11434"
LOADED = "employee database loaded"
INDEXED = "note index built"
_DATA_UNREADABLE = "employees.db is unreadable"
CSV_HEADER = (
    "user_id",
    "tenant_id",
    "name",
    "department",
    "salary",
    "performance_score",
    "hire_date",
    "notes",
)
CSV_ROWS = (
    (1, "acme", "Ada", "Engineering", 100, 4.1, "2020-01-01", "solid quarter"),
    (2, "beta", "Bo", "Engineering", 1000, 4.4, "2021-07-07", "beta secret"),
)
GENERATED_TITLE = "Headcount by department"
GREETING = "Hello, how are you"
RENAMED = "Q3 comp review"

BROWSE_HEADER = (
    "user_id",
    "tenant_id",
    "name",
    "department",
    "salary",
    "performance_score",
    "hire_date",
    "notes",
)
BROWSE_ROWS = (
    (1, ACME, "Ada Lovelace", "Engineering", 100, 4.5, "2019-01-01", "shipped the compiler"),
    (2, ACME, "Alan Turing", "Engineering", 200, 3.5, "2020-02-02", "strong on theory"),
    (3, ACME, "Grace Hopper", "Sales", 300, 2.5, "2021-03-03", "owns the pipeline"),
    (4, BETA, "Adalovelace Beta", "Engineering", 999999, 5.0, "2019-01-01", "beta secret"),
    (5, BETA, "Grace Beta", "Sales", 1, 1.0, "2024-06-06", "beta secret"),
)
ACME_ROWS = 3
BETA_ROWS = 2
ALL_ROWS = ACME_ROWS + BETA_ROWS
BETA_SECRET = "beta secret"
NOTE_HITS = [
    {"user_id": 1, "name": "Ada Lovelace", "note": "shipped the compiler", "distance": 0.21}
]

PROTECTED_ROUTES = [
    ("GET", "/models"),
    ("GET", "/records"),
    ("GET", "/records/departments"),
    ("GET", "/records/tenants"),
    ("GET", "/notes"),
    ("GET", "/notes/search?q=compiler"),
    ("GET", "/notes/flagged"),
    ("GET", "/audit"),
    ("POST", "/chat"),
    ("GET", "/conversations"),
    ("POST", "/conversations"),
    ("GET", "/conversations/whatever"),
    ("PATCH", "/conversations/whatever"),
    ("DELETE", "/conversations/whatever"),
]


@dataclass
class FakeRunner:
    """Records every turn it is asked to run and replays a canned trace-event sequence."""

    calls: list[dict[str, str]] = field(default_factory=list)
    events: tuple[dict, ...] = EVENTS

    def __call__(self, *, tenant_id, thread_id, message, model):
        """Record the turn, then yield the fixed event sequence with the model echoed back."""
        self.calls.append(
            {"tenant_id": tenant_id, "thread_id": thread_id, "message": message, "model": model}
        )
        for event in self.events:
            yield {**event, "model": model} if event["type"] == "done" else dict(event)

    @property
    def last(self) -> dict[str, str]:
        """The most recent turn's arguments."""
        return self.calls[-1]


FROZEN_NOW = datetime(2026, 1, 1, tzinfo=UTC)
TOKEN_LIFETIME_SECONDS = 3600


@dataclass
class FrozenClock:
    """The clock a token's `exp` is verified against, standing in for `api_jwt`'s wall clock."""

    at: datetime

    def now(self, tz=None) -> datetime:
        """The frozen instant, in the requested zone - PyJWT reads it as `datetime.now(tz=...)`."""
        return self.at if tz is None else self.at.astimezone(tz)

    def advance(self, seconds: float) -> None:
        """Move the frozen instant forward, lapsing any token minted before it."""
        self.at += timedelta(seconds=seconds)

    def __instancecheck__(self, obj) -> bool:
        """`api_jwt.encode` uses the same name as a type, so the stand-in answers for `datetime`."""
        return isinstance(obj, datetime)


@dataclass
class ExpiringRunner:
    """Replays the canned events, lapsing the caller's token between the first two.

    The sliding-session property under test is that a turn already streaming is not killed by
    the clock passing `exp`: verification happens once, at request start. Advancing the injected
    clock inside the generator lands the expiry mid-stream without racing the real one.
    """

    clock: FrozenClock

    def __call__(self, *, tenant_id, thread_id, message, model):
        """Yield the first event, lapse the token well past its expiry, then finish the turn."""
        for index, event in enumerate(EVENTS):
            if index == 1:
                self.clock.advance(2 * TOKEN_LIFETIME_SECONDS)
            yield dict(event)


@dataclass
class FakeTranscripts:
    """The transcript seam: canned exchanges per thread, recording every thread it was asked for."""

    stored: dict[str, list[Message]] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)

    def __call__(self, thread_id: str) -> list[Message]:
        """Record the lookup and replay what was stored for that thread, or nothing."""
        self.asked.append(thread_id)
        return self.stored.get(thread_id, [])


@dataclass
class FakeTitler:
    """The titling seam: answers with the canned title, or fails the way a dead endpoint does."""

    answer: str = GENERATED_TITLE
    error: Exception | None = None
    prompts: list[str] = field(default_factory=list)

    def __call__(self, prompt: str) -> str:
        """Record the prompt, then answer as scripted or raise as scripted."""
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.answer


@dataclass
class FakeCapabilities:
    """The `/api/show` seam: canned capabilities per model id, counting every lookup."""

    declared: dict[str, list[str] | None] = field(
        default_factory=lambda: dict(CAPABILITIES)
    )
    asked: list[str] = field(default_factory=list)

    def __call__(self, model_id: str) -> list[str] | None:
        """Record the lookup and answer what the endpoint would declare for that model."""
        self.asked.append(model_id)
        return self.declared.get(model_id)


@dataclass
class FakeNotes:
    """The notes-search seam: records what it was asked, answers canned hits or reports offline."""

    hits: list[dict] = field(default_factory=lambda: [dict(hit) for hit in NOTE_HITS])
    offline: bool = False
    calls: list[dict] = field(default_factory=list)

    def __call__(self, *, query: str, tenant_id: str, k: int) -> list[dict]:
        """Record the retrieval, then answer as the agent's own path would for that tenant."""
        self.calls.append({"query": query, "tenant_id": tenant_id, "k": k})
        if self.offline:
            raise RetrievalUnavailable("the note index has not been built on this server")
        return self.hits

    @property
    def last(self) -> dict:
        """The most recent retrieval's arguments."""
        return self.calls[-1]


@dataclass
class BreakingRunner:
    """Yields a prefix of a turn and then raises, the way a broken run reaches the SSE layer."""

    prefix: tuple[dict, ...]

    def __call__(self, *, tenant_id, thread_id, message, model):
        """Replay the prefix, then fail the way an unreachable endpoint would."""
        for event in self.prefix:
            yield dict(event)
        raise RuntimeError(f"connect timeout to {SECRET_HOST}")


@dataclass
class Wiring:
    """A wired app plus the fakes the tests inspect."""

    client: TestClient
    runner: FakeRunner
    transcripts: FakeTranscripts
    capabilities: FakeCapabilities
    titler: FakeTitler
    notes: FakeNotes
    deleted: list[str]
    loaded: list[str]
    indexed: list[str]


@pytest.fixture
def bootstrap_db(tmp_path, monkeypatch):
    """A tmp database path for the loader, with a two-row CSV standing in for the committed one.

    The database is an argument the loader takes; the CSV is the committed dataset, so pointing
    the loader away from it is the one thing these tests patch.
    """
    csv_path = tmp_path / "employees.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        writer.writerows(CSV_ROWS)
    monkeypatch.setattr(app, "DEFAULT_CSV_PATH", csv_path)
    return tmp_path / "employees.db"


@pytest.fixture(autouse=True)
def _signing_secret(monkeypatch):
    """Every test runs with a usable signing secret unless it deliberately removes it."""
    monkeypatch.setenv(SECRET_ENV_VAR, TEST_SECRET)


@pytest.fixture
def browse_db(tmp_path):
    """A two-tenant database from the inline rows; the committed employees.csv is never read."""
    csv_path = tmp_path / "employees.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(BROWSE_HEADER)
        writer.writerows(BROWSE_ROWS)
    path = tmp_path / "data.db"
    init_db(csv_path, path)
    return path


@pytest.fixture
def wiring(tmp_path, browse_db) -> Wiring:
    """The app with a fake runner and model list, a tmp registry, and recording replay/cleanup."""
    runner = FakeRunner()
    transcripts = FakeTranscripts()
    capabilities = FakeCapabilities()
    titler = FakeTitler()
    notes = FakeNotes()
    deleted: list[str] = []
    loaded: list[str] = []
    indexed: list[str] = []
    app = create_app(
        chat_runner=runner,
        model_lister=lambda: list(SERVED_MODELS),
        capability_checker=capabilities,
        titler=titler,
        registry=ConversationRegistry(tmp_path / "state.db"),
        transcript=transcripts,
        cleanup=deleted.append,
        data_store=lambda: loaded.append(LOADED),
        note_index=lambda: indexed.append(INDEXED),
        note_search=notes,
        db_path=browse_db,
    )
    return Wiring(
        client=TestClient(app),
        runner=runner,
        transcripts=transcripts,
        capabilities=capabilities,
        titler=titler,
        notes=notes,
        deleted=deleted,
        loaded=loaded,
        indexed=indexed,
    )


def _client(
    tmp_path,
    *,
    model_lister=None,
    capability_checker=None,
    chat_runner=None,
    data_store=None,
    note_index=None,
    titler=None,
    transcript=None,
    note_search=None,
    db_path=None,
) -> TestClient:
    """A client wired like the fixture but with the seams a single test wants to vary."""
    return TestClient(
        create_app(
            chat_runner=chat_runner or FakeRunner(),
            model_lister=model_lister or (lambda: list(SERVED_MODELS)),
            capability_checker=capability_checker or FakeCapabilities(),
            titler=titler or FakeTitler(),
            registry=ConversationRegistry(tmp_path / "state.db"),
            transcript=transcript or FakeTranscripts(),
            cleanup=lambda thread_id: None,
            data_store=data_store or (lambda: None),
            note_index=note_index or (lambda: None),
            note_search=note_search or FakeNotes(),
            **({} if db_path is None else {"db_path": db_path}),
        )
    )


def _token(client: TestClient, credentials: tuple[str, str]) -> str:
    """Log in and return the bearer token."""
    username, password = credentials
    response = client.post("/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["token"]


def _headers(client: TestClient, credentials: tuple[str, str]) -> dict[str, str]:
    """An Authorization header for the given demo identity."""
    return {"Authorization": f"Bearer {_token(client, credentials)}"}


def _greeted_transcript(turns: int) -> list[Message]:
    """A thread that opened with a greeting and then asked the real question, `turns` turns long."""
    transcript = [
        Message(role="user", content=GREETING),
        Message(role="assistant", content="I am well. Ask me about your HR data."),
    ]
    for turn in range(1, turns):
        transcript.append(Message(role="user", content=f"{QUESTION} ({turn})"))
        transcript.append(Message(role="assistant", content=ANSWER))
    return transcript


def _new_thread(client: TestClient, headers: dict[str, str], title: str = "first message") -> str:
    """Create a conversation and return its thread id."""
    response = client.post("/conversations", json={"title": title}, headers=headers)
    assert response.status_code == 201
    return response.json()["thread_id"]


def _pin_model(monkeypatch, model_id: str) -> None:
    """Pin `runtime.json`'s `agent.model` for one test, the way a deployment's config would."""
    config = runtime()
    monkeypatch.setattr(
        app, "runtime", lambda: replace(config, agent=replace(config.agent, model=model_id))
    )


def _sse_events(body: str) -> list[dict]:
    """Parse an SSE body: records split on the blank line, each one a `data:` JSON payload."""
    records = [record for record in body.split("\n\n") if record.strip()]
    return [json.loads(record.removeprefix("data: ")) for record in records]


def test_health_is_open_and_reports_version(wiring):
    response = wiring.client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"]


def test_health_reports_the_prompt_guardrail_position_as_a_boolean(wiring):
    """The SPA states the mode before the first turn, so no demo can hide a prompt swap (#102)."""
    body = wiring.client.get("/health").json()

    assert body["prompt_guardrails"] is runtime().agent.prompt_guardrails
    assert isinstance(body["prompt_guardrails"], bool)


def test_health_reports_the_titling_window(wiring):
    """The SPA stops asking for a generated title past the window it reads here (#118)."""
    body = wiring.client.get("/health").json()

    assert body["title_turns"] == runtime().conversations.title_turns


def test_login_issues_a_token(wiring):
    assert _token(wiring.client, ALICE)


@pytest.mark.parametrize(
    "credentials",
    [(ALICE[0], "wrong-password"), ("nobody@acme", ALICE[1])],
    ids=["wrong password", "unknown user"],
)
def test_login_rejects_bad_credentials(wiring, credentials):
    username, password = credentials
    response = wiring.client.post("/login", json={"username": username, "password": password})
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path"), PROTECTED_ROUTES)
def test_every_non_login_route_requires_a_token(wiring, method, path):
    response = wiring.client.request(method, path, json={})
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path"), PROTECTED_ROUTES)
def test_every_non_login_route_rejects_a_forged_token(wiring, method, path):
    response = wiring.client.request(
        method, path, json={}, headers={"Authorization": "Bearer not.a.token"}
    )
    assert response.status_code == 401


def test_models_returns_the_chat_capable_list_and_the_default_it_resolves(wiring):
    """The fake endpoint does not serve the committed `agent.model`, so the fallback is reported."""
    response = wiring.client.get("/models", headers=_headers(wiring.client, ALICE))
    assert response.status_code == 200
    assert response.json() == {"models": CHAT_MODELS, "default": SERVED_DEFAULT}
    assert EMBED_MODEL not in response.json()["models"]


def test_models_reports_the_configured_default_when_the_endpoint_serves_it(wiring, monkeypatch):
    """The preference wins whenever it is live: the fallback is the exception, not the rule."""
    _pin_model(monkeypatch, CHOSEN_MODEL)

    response = wiring.client.get("/models", headers=_headers(wiring.client, ALICE))

    assert response.json() == {"models": CHAT_MODELS, "default": CHOSEN_MODEL}


def test_models_reports_a_served_default_when_the_configured_one_is_absent(wiring, monkeypatch):
    """A pinned id the endpoint dropped must not be reported as the default (issue #111)."""
    _pin_model(monkeypatch, UNKNOWN_MODEL)

    body = wiring.client.get("/models", headers=_headers(wiring.client, ALICE)).json()

    assert body["default"] == SERVED_DEFAULT
    assert body["default"] in body["models"]


def test_the_resolved_default_does_not_depend_on_the_order_the_endpoint_listed(tmp_path):
    """`/api/tags` orders by modification time, so the rule reads the set, not the order."""
    forward = _client(tmp_path, model_lister=lambda: list(SERVED_MODELS))
    reversed_order = _client(tmp_path, model_lister=lambda: list(reversed(SERVED_MODELS)))

    first = forward.get("/models", headers=_headers(forward, ALICE)).json()
    second = reversed_order.get("/models", headers=_headers(reversed_order, ALICE)).json()

    assert first["default"] == second["default"] == SERVED_DEFAULT
    assert sorted(first["models"]) == sorted(second["models"])


def test_models_answers_502_when_the_endpoint_serves_no_chat_capable_model(tmp_path):
    """No chat model is an upstream failure, not a default: nothing is invented to run on."""
    client = _client(tmp_path, model_lister=lambda: [EMBED_MODEL])

    response = client.get("/models", headers=_headers(client, ALICE))

    assert response.status_code == 502
    assert "chat-capable" in response.json()["detail"]


def test_models_falls_back_to_the_embed_model_prefix_without_declared_capabilities(tmp_path):
    silent = FakeCapabilities(declared={})
    client = _client(tmp_path, capability_checker=silent)

    response = client.get("/models", headers=_headers(client, ALICE))

    assert response.status_code == 200
    assert response.json()["models"] == CHAT_MODELS
    assert silent.asked == SERVED_MODELS


def test_models_asks_the_endpoint_about_each_model_once(wiring):
    headers = _headers(wiring.client, ALICE)

    wiring.client.get("/models", headers=headers)
    wiring.client.get("/models", headers=headers)

    assert wiring.capabilities.asked == SERVED_MODELS


def test_only_a_model_that_declares_thinking_is_asked_to_think():
    """Ollama refuses `think` for a model without the capability, so the declaration decides."""
    capabilities = FakeCapabilities(
        declared={CHAT_MODELS[0]: ["completion", "thinking"], CHAT_MODELS[1]: ["completion"]}
    )
    thinks = thinking_checker(capabilities)

    assert thinks(CHAT_MODELS[0]) is True
    assert thinks(CHAT_MODELS[1]) is False


def test_the_turn_model_carries_the_configured_generation_bounds():
    """The client the turn runs on is bounded, and by the runtime knobs rather than by a constant.

    `num_predict` and `num_ctx` are the parameter names langchain-ollama 1.1.0 forwards as Ollama
    request options; left unset each falls back to whatever the endpoint decides, which is the
    unbounded generation of issue #83 (OWASP LLM10).
    """
    bounds = runtime().agent
    client = bounded_model("http://model.example:11434", CHAT_MODELS[0], reasoning=False)

    assert client.num_predict == bounds.max_output_tokens
    assert client.num_ctx == bounds.context_window
    assert client.model == CHAT_MODELS[0]


def test_the_generation_bounds_reach_the_endpoint_as_request_options():
    """The two bounds are not just set on the client: they ride in the options Ollama reads."""
    bounds = runtime().agent
    client = bounded_model("http://model.example:11434", CHAT_MODELS[0], reasoning=False)

    options = client._chat_params([HumanMessage(content=QUESTION)])["options"]

    assert options["num_predict"] == bounds.max_output_tokens
    assert options["num_ctx"] == bounds.context_window


def test_no_model_is_asked_to_think_when_the_configuration_turns_it_off(monkeypatch):
    """The reasoning channel is a runtime knob, not a hardcoded behavior of the runner."""
    config = runtime()
    monkeypatch.setattr(
        app, "runtime", lambda: replace(config, agent=replace(config.agent, thinking=False))
    )
    thinks = thinking_checker(FakeCapabilities(declared={CHAT_MODELS[0]: ["thinking"]}))

    assert thinks(CHAT_MODELS[0]) is False


def test_the_endpoint_is_asked_about_a_model_once_for_every_reader():
    """One cache serves the model list and the thinking decision; `/api/show` is asked once."""
    capabilities = FakeCapabilities()
    cached = cached_capabilities(capabilities)

    assert cached(CHAT_MODELS[0]) == CAPABILITIES[CHAT_MODELS[0]]
    assert thinking_checker(cached)(CHAT_MODELS[0]) is False
    assert capabilities.asked == [CHAT_MODELS[0]]


def test_models_answers_502_generically_when_the_endpoint_is_down(tmp_path):
    def unreachable() -> list[str]:
        raise ModelEndpointError("connect timeout to http://host.example:11434")

    client = _client(tmp_path, model_lister=unreachable)
    response = client.get("/models", headers=_headers(client, ALICE))
    assert response.status_code == 502
    assert "host.example" not in response.text


def test_records_serves_the_whole_dataset_whichever_token_asks(wiring):
    """The control group over HTTP: the listing is the dataset, so both tokens see all of it.

    This replaced "records serves only the rows of the token's tenant" (issue #117). The property
    that survived - the AGENT sees one tenant - is asserted on the chat path, not here.
    """
    acme = wiring.client.get("/records", headers=_headers(wiring.client, ALICE)).json()
    beta = wiring.client.get("/records", headers=_headers(wiring.client, BOB)).json()

    assert acme["total"] == beta["total"] == ALL_ROWS
    assert acme["rows"] == beta["rows"]
    assert {row[acme["columns"].index("tenant_id")] for row in acme["rows"]} == {ACME, BETA}


def test_the_tenant_filter_narrows_the_listing_over_http(wiring):
    """`tenant_id` is a query parameter of the same kind as `department`, and it is honored."""
    body = wiring.client.get(
        "/records", params={"tenant_id": BETA}, headers=_headers(wiring.client, ALICE)
    ).json()

    assert body["total"] == BETA_ROWS
    assert {row[body["columns"].index("tenant_id")] for row in body["rows"]} == {BETA}
    assert body["ignored"] == []


@pytest.mark.parametrize("query", [{"tenant": BETA}, {"db_path": "/etc/passwd"}])
def test_a_parameter_that_is_not_a_filter_is_still_not_read(wiring, query):
    """`Filters` is the allowlist: a name that is not one of its fields changes nothing at all.

    The `tenant_id=beta` case that used to live in this parametrization moved out: it is a filter
    now. `tenant` - a name the listing does not have - keeps the property asserted.
    """
    response = wiring.client.get(
        "/records", params=query, headers=_headers(wiring.client, ALICE)
    )

    assert response.status_code == 200
    assert response.json()["total"] == ALL_ROWS
    assert [param["name"] for param in response.json()["ignored"]] == list(query)


def test_an_unknown_parameter_is_reported_rather_than_swallowed(wiring):
    """Not every stray parameter is an attack; every one of them is still reported."""
    body = wiring.client.get(
        "/records", params={"db_path": "/etc/passwd"}, headers=_headers(wiring.client, ALICE)
    ).json()

    assert body["total"] == ALL_ROWS
    assert [param["name"] for param in body["ignored"]] == ["db_path"]
    assert "not a parameter this listing reads" in body["ignored"][0]["reason"]


def test_an_accepted_filter_is_not_reported_as_ignored(wiring):
    """The report is about what was discarded; a filter that worked is visible in the rows."""
    body = wiring.client.get(
        "/records",
        params={"tenant_id": ACME, "name": "ada", "sort": "salary", "page_size": 2},
        headers=_headers(wiring.client, ALICE),
    ).json()

    assert body["ignored"] == []


def test_the_notes_listing_reports_an_unread_parameter_too(wiring):
    """Both listings take the same filters, so both owe the same report (ADR 0014 as amended)."""
    body = wiring.client.get(
        "/notes", params={"tenant": BETA}, headers=_headers(wiring.client, BOB)
    ).json()

    assert body["total"] == ALL_ROWS
    assert [param["name"] for param in body["ignored"]] == ["tenant"]
    assert "not a parameter this listing reads" in body["ignored"][0]["reason"]


@pytest.mark.parametrize(
    "hostile",
    ["' OR 1=1 --", "x' UNION SELECT * FROM employees WHERE tenant_id='beta' --", "%", "?"],
)
def test_a_hostile_filter_over_http_leaks_neither_a_row_nor_an_error(wiring, hostile):
    """No foreign row, no stack, no statement: an ordinary empty page (ADR 0002 as amended)."""
    response = wiring.client.get(
        "/records", params={"name": hostile}, headers=_headers(wiring.client, ALICE)
    )

    assert response.status_code == 200
    assert response.json()["rows"] == []
    assert response.json()["total"] == 0
    assert BETA_SECRET not in response.text


def test_records_filters_and_pages_with_the_true_total(wiring):
    headers = _headers(wiring.client, ALICE)

    filtered = wiring.client.get(
        "/records", params={"tenant_id": ACME, "name": "ada"}, headers=headers
    ).json()
    paged = wiring.client.get(
        "/records", params={"page": 2, "page_size": 1, "sort": "salary"}, headers=headers
    ).json()

    assert filtered["total"] == 1
    assert paged["total"] == ALL_ROWS
    assert paged["page"] == 2
    assert len(paged["rows"]) == 1


def test_a_page_larger_than_the_row_cap_is_clamped_and_says_so(wiring):
    """ADR 0007 again: the page ceiling is the executor's cap, and the response reports it."""
    response = wiring.client.get(
        "/records", params={"page_size": 10**9}, headers=_headers(wiring.client, ALICE)
    )

    assert response.json()["page_size"] == runtime().db.max_result_rows


@pytest.mark.parametrize(
    "query,expected",
    [
        ({"sort": "notes"}, "sort must be one of"),
        ({"direction": "sideways"}, "direction must be one of"),
        ({"hired_from": "yesterday"}, "ISO date"),
        ({"name": "a" * 500}, "characters"),
        ({"sort": "notes", "tenant_id": BETA}, "sort must be one of"),
    ],
)
def test_a_refused_browse_is_a_400_naming_the_allowlist_that_refused_it(wiring, query, expected):
    response = wiring.client.get(
        "/records", params=query, headers=_headers(wiring.client, ALICE)
    )

    assert response.status_code == 400
    assert expected in response.json()["detail"]


def test_records_defaults_to_the_primary_key_sort(wiring):
    response = wiring.client.get("/records", headers=_headers(wiring.client, ALICE))
    assert response.json()["sort"] == DEFAULT_SORT


def test_the_departments_offered_count_the_listing_and_narrow_with_the_tenant(wiring):
    """The option counts follow the tenant filter, so no number on the picker is orphaned."""
    headers = _headers(wiring.client, ALICE)

    everyone = wiring.client.get("/records/departments", headers=headers).json()
    acme_only = wiring.client.get(
        "/records/departments", params={"tenant_id": ACME}, headers=headers
    ).json()

    assert everyone == [
        {"value": "Engineering", "employees": 3},
        {"value": "Sales", "employees": 2},
    ]
    assert acme_only == [
        {"value": "Engineering", "employees": 2},
        {"value": "Sales", "employees": 1},
    ]


def test_the_tenants_offered_are_the_datasets_own_with_their_row_counts(wiring):
    """The tenant picker states the control group: this is what the dataset holds (ADR 0014)."""
    body = wiring.client.get("/records/tenants", headers=_headers(wiring.client, ALICE)).json()

    assert body == [
        {"value": ACME, "employees": ACME_ROWS},
        {"value": BETA, "employees": BETA_ROWS},
    ]


def test_the_notes_corpus_is_the_whole_corpus_whichever_token_asks(wiring):
    """A reader must be able to READ another tenant's planted note; the search still cannot.

    Replaces "the notes corpus is the caller's own" (issue #117). The asymmetry it creates is
    asserted right below, on `/notes/search`, which stays scoped to the token's tenant.
    """
    acme = wiring.client.get("/notes", headers=_headers(wiring.client, ALICE))
    beta = wiring.client.get("/notes", headers=_headers(wiring.client, BOB))

    assert acme.json()["total"] == beta.json()["total"] == ALL_ROWS
    assert BETA_SECRET in acme.text
    assert BETA_SECRET in beta.text


def test_the_notes_search_runs_the_agents_retrieval_path_for_the_tokens_tenant(wiring):
    """The same call the `search_notes` tool makes, with the configured k (ADRs 0010, 0014)."""
    response = wiring.client.get(
        "/notes/search", params={"q": "compiler"}, headers=_headers(wiring.client, ALICE)
    )

    assert response.status_code == 200
    assert wiring.notes.last == {
        "query": "compiler",
        "tenant_id": ACME,
        "k": runtime().rag.top_k,
    }
    assert response.json()["hits"] == [
        {**NOTE_HITS[0], "tenant_id": ACME, "department": "Engineering", "performance_score": 4.5}
    ]
    assert response.json()["k"] == runtime().rag.top_k


def test_a_notes_search_hit_carries_the_department_and_score_of_its_own_row(wiring):
    """What a reader verifies a hit against: the retrieval's text, plus the row's own fields."""
    response = wiring.client.get(
        "/notes/search", params={"q": "compiler"}, headers=_headers(wiring.client, ALICE)
    )

    (hit,) = response.json()["hits"]
    assert hit["tenant_id"] == ACME
    assert hit["department"] == "Engineering"
    assert hit["performance_score"] == 4.5
    assert hit["distance"] == NOTE_HITS[0]["distance"]


def test_a_hit_naming_a_foreign_row_is_annotated_with_nothing(tmp_path, browse_db):
    """The annotation reads through the scoped executor, so it cannot describe another tenant."""
    foreign = [{"user_id": 4, "name": "Adalovelace Beta", "note": "beta secret", "distance": 0.1}]
    client = _client(tmp_path, note_search=FakeNotes(hits=foreign), db_path=browse_db)

    response = client.get(
        "/notes/search", params={"q": "secret"}, headers=_headers(client, ALICE)
    )

    (hit,) = response.json()["hits"]
    assert "tenant_id" not in hit
    assert "department" not in hit
    assert "performance_score" not in hit


def test_the_notes_search_takes_its_tenant_from_the_token_and_nowhere_else(wiring):
    wiring.client.get(
        "/notes/search",
        params={"q": "secret", "tenant_id": BETA},
        headers=_headers(wiring.client, ALICE),
    )

    assert wiring.notes.last["tenant_id"] == ACME


def test_the_notes_search_holds_k_inside_the_configured_ceiling(wiring):
    headers = _headers(wiring.client, ALICE)

    wiring.client.get("/notes/search", params={"q": "x", "k": 10**6}, headers=headers)
    assert wiring.notes.last["k"] == runtime().browse.max_search_hits

    wiring.client.get("/notes/search", params={"q": "x", "k": 0}, headers=headers)
    assert wiring.notes.last["k"] == 1


def test_a_notes_search_without_an_index_reports_retrieval_offline(tmp_path, browse_db):
    """An operator condition, not a model error and not a leak: 503 and a plain statement."""
    notes = FakeNotes(offline=True)
    client = _client(tmp_path, note_search=notes, db_path=browse_db)

    response = client.get(
        "/notes/search", params={"q": "compiler"}, headers=_headers(client, ALICE)
    )

    assert response.status_code == 503
    assert "index" in response.json()["detail"]


def test_the_flagged_notes_are_the_manifests_rows_across_every_tenant(wiring):
    """The committed manifest, unfiltered now that the corpus listing shows every tenant's notes.

    Filtering it to the caller was right while the corpus was the caller's; it would now hide
    exactly the foreign planted payload the demo points at (issue #117). A token is still
    required, which `PROTECTED_ROUTES` asserts.
    """
    response = wiring.client.get("/notes/flagged", headers=_headers(wiring.client, ALICE))

    assert response.status_code == 200
    assert set(response.json()) == {"user_ids", "kinds"}


def test_the_audit_log_is_served_newest_first_with_every_tenants_entries(wiring):
    """The Audit tab over HTTP: the trail of what ran, whichever token asks for it (ADR 0014).

    Two tokens each browse, so the log holds rows under both tenants, and the listing has to show
    both - a trail narrowed to the caller could not show that the other tenant's query was scoped
    to the other tenant, which is the whole reason to serve it.
    """
    wiring.client.get("/records", headers=_headers(wiring.client, ALICE))
    wiring.client.get("/notes", headers=_headers(wiring.client, BOB))

    body = wiring.client.get("/audit", headers=_headers(wiring.client, ALICE)).json()

    assert body["total"] == len(body["entries"]) == 4
    assert {entry["tenant"] for entry in body["entries"]} == {ACME, BETA}
    assert [entry["id"] for entry in body["entries"]] == sorted(
        (entry["id"] for entry in body["entries"]), reverse=True
    )
    assert body["entries"][0]["verdict"] == VERDICT_APPROVED


def test_an_audit_entry_carries_the_statements_and_no_result_row(wiring):
    """What the endpoint exposes is SQL and metadata; the rows a statement returned are not in it.

    `beta secret` is a value of the dataset, and the audit store never holds one - which is why
    serving this trail unfiltered adds no reachable tenant data (ADR 0002 as amended).
    """
    wiring.client.get("/records", params={"tenant_id": BETA}, headers=_headers(wiring.client, BOB))

    response = wiring.client.get("/audit", headers=_headers(wiring.client, BOB))

    entry = response.json()["entries"][0]
    assert set(entry) == {
        "id",
        "ts",
        "tenant",
        "generated_sql",
        "verdict",
        "executed_sql",
        "rowcount",
        "error_kind",
    }
    assert BETA_SECRET not in response.text


def test_the_audit_log_pages_from_its_head_with_the_true_total(wiring):
    """Paged like every other listing: the same clamped page size, and a total of the log."""
    for _ in range(3):
        wiring.client.get("/records", headers=_headers(wiring.client, ALICE))
    headers = _headers(wiring.client, ALICE)

    first = wiring.client.get("/audit", params={"page_size": 2}, headers=headers).json()
    second = wiring.client.get("/audit", params={"page": 2, "page_size": 2}, headers=headers).json()
    clamped = wiring.client.get("/audit", params={"page_size": 10**9}, headers=headers).json()

    assert first["total"] == second["total"] == 6
    assert [entry["id"] for entry in first["entries"]] == [6, 5]
    assert [entry["id"] for entry in second["entries"]] == [4, 3]
    assert clamped["page_size"] == runtime().db.max_result_rows


def test_a_browse_the_allowlist_refused_leaves_no_row_in_the_trail(wiring):
    """A sort outside the allowlist is refused before any statement is built, so nothing ran.

    The trail records what reached the executor. A 400 from `browse.py`'s allowlist never got
    there, which is why the log shows the one call that did and not the one that was turned away.
    """
    refused = wiring.client.get(
        "/records", params={"sort": "notes"}, headers=_headers(wiring.client, ALICE)
    )
    wiring.client.get("/records", headers=_headers(wiring.client, ALICE))

    body = wiring.client.get("/audit", headers=_headers(wiring.client, ALICE)).json()

    assert refused.status_code == 400
    assert body["total"] == 2
    assert [entry["verdict"] for entry in body["entries"]] == [VERDICT_APPROVED] * 2


def test_chat_streams_the_trace_events_as_sse(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)
    response = wiring.client.post(
        "/chat", json={"thread_id": thread_id, "message": "how many employees?"}, headers=headers
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    assert [event["type"] for event in events] == [event["type"] for event in EVENTS]
    assert events[-1]["answer"] == ANSWER


def test_chat_closes_a_broken_stream_with_a_terminal_failed_frame(tmp_path):
    """A run that dies mid-flight still ends in one `done` frame, so no turn is left streaming."""
    client = _client(tmp_path, chat_runner=BreakingRunner(STARTED))
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers)

    response = client.post(
        "/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert [event["type"] for event in events] == ["node_start", "token", "done"]
    assert events[-1]["status"] == STATUS_FAILED
    assert events[-1]["answer"]
    assert events[-1]["model"] == SERVED_DEFAULT
    assert SECRET_HOST not in response.text


def test_a_terminal_failed_frame_reports_the_seconds_it_ran_and_no_tokens(tmp_path):
    """A run that never reached an answer has no usage to report, but it does have a duration."""
    client = _client(tmp_path, chat_runner=BreakingRunner(STARTED))
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers)

    response = client.post(
        "/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers
    )

    done = _sse_events(response.text)[-1]
    assert (done["input_tokens"], done["output_tokens"]) == (0, 0)
    assert done["duration_s"] >= 0
    assert done["prompt_guardrails"] is runtime().agent.prompt_guardrails


def test_chat_streams_the_reasoning_and_the_turn_cost_verbatim(wiring):
    """The edge serializes what the agent said: the thinking as its own frame, usage on `done`."""
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)

    response = wiring.client.post(
        "/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers
    )

    events = _sse_events(response.text)
    assert [event for event in events if event["type"] == "reasoning"] == [
        {"type": "reasoning", "text": THOUGHT}
    ]
    assert {key: events[-1][key] for key in USAGE} == USAGE
    assert THOUGHT not in events[-1]["answer"]


def test_a_stream_that_breaks_after_its_done_frame_is_not_closed_twice(tmp_path):
    """The turn already said how it ended; a failure on the way out must not contradict it."""
    client = _client(tmp_path, chat_runner=BreakingRunner(EVENTS))
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers)

    response = client.post(
        "/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers
    )

    events = _sse_events(response.text)
    assert [event["type"] for event in events] == [event["type"] for event in EVENTS]
    assert events[-1]["status"] == "ok"


def test_the_app_loads_the_employee_database_before_it_serves_anything(wiring):
    """A fresh checkout has only the CSV, so loading it is a startup step (issue #96)."""
    assert wiring.loaded == [LOADED]


def test_an_employee_database_that_cannot_be_loaded_stops_the_app_from_booting(tmp_path):
    """The data file is on the critical path: a half-working API is worse than no API."""

    def broken() -> None:
        raise RuntimeError(_DATA_UNREADABLE)

    with pytest.raises(RuntimeError, match=_DATA_UNREADABLE):
        _client(tmp_path, data_store=broken)


def test_the_data_store_loader_builds_a_missing_database_from_the_committed_csv(bootstrap_db):
    app.build_data_store(bootstrap_db)

    assert db.employee_rows(bootstrap_db) == len(CSV_ROWS)


def test_the_data_store_loader_leaves_a_populated_database_alone(bootstrap_db, monkeypatch):
    """The idempotent half: a restart pays a row count, never the CSV again (issue #96)."""
    app.build_data_store(bootstrap_db)
    loads: list[tuple[object, object]] = []
    monkeypatch.setattr(app, "init_db", lambda csv_path, db_path: loads.append((csv_path, db_path)))

    app.build_data_store(bootstrap_db)

    assert loads == []
    assert db.employee_rows(bootstrap_db) == len(CSV_ROWS)


def test_the_app_builds_the_note_index_before_it_serves_anything(wiring):
    """Indexing is a startup step, not a first-request surprise (ADR 0010 as amended)."""
    assert wiring.indexed == [INDEXED]


def test_a_note_index_that_cannot_be_built_does_not_stop_the_app_from_booting(tmp_path):
    """An unreachable embedding endpoint costs retrieval, never the API."""

    def unreachable() -> None:
        raise RuntimeError(f"connect timeout to {SECRET_HOST}")

    client = _client(tmp_path, note_index=unreachable)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers)

    assert client.get("/health").status_code == 200
    chat = client.post(
        "/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers
    )
    assert chat.status_code == 200


def test_chat_defaults_to_the_configured_model_when_the_endpoint_serves_it(wiring, monkeypatch):
    """The unchanged case: a live preference is what a default turn runs on and reports."""
    _pin_model(monkeypatch, CHOSEN_MODEL)
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)

    response = wiring.client.post(
        "/chat", json={"thread_id": thread_id, "message": "hi"}, headers=headers
    )

    assert wiring.runner.last["model"] == CHOSEN_MODEL
    assert _sse_events(response.text)[-1]["model"] == CHOSEN_MODEL


def test_chat_falls_back_to_a_served_model_when_the_configured_one_is_absent(wiring, monkeypatch):
    """A pinned id the endpoint dropped must not refuse a turn other models can answer (#111)."""
    _pin_model(monkeypatch, UNKNOWN_MODEL)
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)

    response = wiring.client.post(
        "/chat", json={"thread_id": thread_id, "message": "hi"}, headers=headers
    )

    assert response.status_code == 200
    assert wiring.runner.last["model"] == SERVED_DEFAULT
    assert _sse_events(response.text)[-1]["model"] == SERVED_DEFAULT


def test_chat_answers_502_when_the_endpoint_serves_no_chat_capable_model(tmp_path):
    """The turn fails loudly with the reason, and no model is run on an invented id."""
    runner = FakeRunner()
    client = _client(tmp_path, chat_runner=runner, model_lister=lambda: [EMBED_MODEL])
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers)

    response = client.post(
        "/chat", json={"thread_id": thread_id, "message": "hi"}, headers=headers
    )

    assert response.status_code == 502
    assert "chat-capable" in response.json()["detail"]
    assert runner.calls == []


@pytest.mark.parametrize(
    "pinned", [CHOSEN_MODEL, UNKNOWN_MODEL], ids=["configured is served", "configured is absent"]
)
def test_the_model_models_reports_as_default_is_the_one_a_default_turn_runs_on(
    wiring, monkeypatch, pinned
):
    """The two reporting paths cannot drift: one resolver answers the picker and the turn alike."""
    _pin_model(monkeypatch, pinned)
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)

    listed = wiring.client.get("/models", headers=headers).json()
    response = wiring.client.post(
        "/chat", json={"thread_id": thread_id, "message": "hi"}, headers=headers
    )

    assert listed["default"] in listed["models"]
    assert wiring.runner.last["model"] == listed["default"]
    assert _sse_events(response.text)[-1]["model"] == listed["default"]


def test_chat_honors_a_model_from_the_live_list(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)
    response = wiring.client.post(
        "/chat",
        json={"thread_id": thread_id, "message": "hi", "model": CHOSEN_MODEL},
        headers=headers,
    )
    assert response.status_code == 200
    assert wiring.runner.last["model"] == CHOSEN_MODEL


def test_chat_rejects_a_model_the_endpoint_does_not_serve(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)
    response = wiring.client.post(
        "/chat",
        json={"thread_id": thread_id, "message": "hi", "model": UNKNOWN_MODEL},
        headers=headers,
    )
    assert response.status_code == 400
    assert wiring.runner.calls == []


def test_chat_rejects_an_embedding_model_the_endpoint_does_serve(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)
    response = wiring.client.post(
        "/chat",
        json={"thread_id": thread_id, "message": "hi", "model": EMBED_MODEL},
        headers=headers,
    )
    assert response.status_code == 400
    assert wiring.runner.calls == []


def test_chat_takes_the_tenant_from_the_token_and_ignores_the_body(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)
    response = wiring.client.post(
        "/chat",
        json={
            "thread_id": thread_id,
            "message": "hi",
            "tenant_id": "beta",
            "tenant": "beta",
            "sub": "bob@beta",
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert wiring.runner.last["tenant_id"] == ACME


def test_chat_on_a_foreign_thread_is_indistinguishable_from_a_missing_one(wiring):
    alice_thread = _new_thread(wiring.client, _headers(wiring.client, ALICE))
    bob_headers = _headers(wiring.client, BOB)
    foreign = wiring.client.post(
        "/chat", json={"thread_id": alice_thread, "message": "hi"}, headers=bob_headers
    )
    missing = wiring.client.post(
        "/chat", json={"thread_id": "no-such-thread", "message": "hi"}, headers=bob_headers
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert wiring.runner.calls == []


def test_get_conversation_on_a_foreign_thread_is_a_404(wiring):
    alice_thread = _new_thread(wiring.client, _headers(wiring.client, ALICE))
    wiring.transcripts.stored[alice_thread] = list(TRANSCRIPT)
    bob_headers = _headers(wiring.client, BOB)
    foreign = wiring.client.get(f"/conversations/{alice_thread}", headers=bob_headers)
    missing = wiring.client.get("/conversations/no-such-thread", headers=bob_headers)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert wiring.transcripts.asked == []


def test_get_conversation_replays_the_exchanges_in_order(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers, title=QUESTION)
    wiring.transcripts.stored[thread_id] = list(TRANSCRIPT)

    response = wiring.client.get(f"/conversations/{thread_id}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == thread_id
    assert body["title"] == QUESTION
    assert body["created"]
    assert body["messages"] == [asdict(message) for message in TRANSCRIPT]
    assert wiring.transcripts.asked == [thread_id]


def test_get_conversation_replays_a_thread_never_chatted_in_as_empty(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)
    response = wiring.client.get(f"/conversations/{thread_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["messages"] == []
    assert response.json()["turns"] == []


def test_a_turns_history_is_stored_and_replayed_beside_the_transcript(tmp_path):
    """The whole turn is served back with the conversation: the call, its evidence, its frame."""
    transcripts = FakeTranscripts()
    client = _client(tmp_path, chat_runner=FakeRunner(events=PLOTTED), transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)
    transcripts.stored[thread_id] = list(TRANSCRIPT[:2])

    client.post("/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers)

    replayed = client.get(f"/conversations/{thread_id}", headers=headers).json()
    assert replayed["messages"] == [asdict(message) for message in TRANSCRIPT[:2]]
    assert replayed["turns"] == [
        {
            "turn": 1,
            "cut": 0,
            "events": [
                {"type": "node_start", "node": "reason"},
                {"type": "reasoning", "text": THOUGHT, "truncated": False},
                {
                    "type": "tool_call",
                    "id": "c1",
                    "tool": "plot",
                    "args": {"kind": "bar", "column": "department"},
                },
                {
                    "type": "tool_result",
                    "id": "c1",
                    "tool": "plot",
                    "content": "",
                    "withheld": 0,
                    "data": {"chart_spec": CHART_SPEC},
                },
                {
                    "type": "done",
                    "status": "ok",
                    "answer": ANSWER,
                    **USAGE,
                    "model": SERVED_DEFAULT,
                },
            ],
        }
    ]


def test_a_replayed_turn_carries_the_layer_that_refused_and_the_retry_that_preceded_it(tmp_path):
    """The interesting part of an RLS demo is what was retried and refused, so it is replayed."""
    transcripts = FakeTranscripts()
    client = _client(tmp_path, chat_runner=FakeRunner(events=REFUSED), transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)
    transcripts.stored[thread_id] = list(TRANSCRIPT[:2])

    client.post("/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers)

    events = client.get(f"/conversations/{thread_id}", headers=headers).json()["turns"][0]["events"]
    assert [event["type"] for event in events] == [
        "tool_call",
        "retry",
        "tool_call",
        "security_event",
        "done",
    ]
    assert events[1]["attempt"] == 1
    assert events[1]["reason"] == RETRY_REASON
    assert events[3]["layer"] == REFUSAL_LAYER
    assert events[3]["reason"] == REFUSAL_REASON
    assert events[0]["args"] == {"sql": FOREIGN_SQL}


def test_a_replayed_turn_carries_the_prompt_guardrail_position_that_produced_it(tmp_path):
    """The per-turn record of which prompt answered is the point of the field (#102, #110)."""
    transcripts = FakeTranscripts()
    client = _client(tmp_path, chat_runner=FakeRunner(events=REFUSED), transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)

    client.post("/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers)

    events = client.get(f"/conversations/{thread_id}", headers=headers).json()["turns"][0]["events"]
    assert events[-1]["prompt_guardrails"] is False


def test_stored_turn_history_is_keyed_by_the_turn_that_asked_for_it(tmp_path):
    """One turn is one question, so the question count is what the history is filed under."""
    transcripts = FakeTranscripts()
    client = _client(tmp_path, chat_runner=FakeRunner(events=PLOTTED), transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)

    transcripts.stored[thread_id] = list(TRANSCRIPT[:2])
    client.post("/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers)
    transcripts.stored[thread_id] = list(TRANSCRIPT)
    client.post("/chat", json={"thread_id": thread_id, "message": "and in sales?"}, headers=headers)

    replayed = client.get(f"/conversations/{thread_id}", headers=headers).json()["turns"]
    assert [turn["turn"] for turn in replayed] == [1, 2]


def test_a_turn_that_called_no_tool_still_replays_its_reasoning_and_its_frame(wiring):
    """A turn with no tool call is still a turn: what it thought and how it ended are history."""
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)

    wiring.client.post("/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers)

    assert wiring.transcripts.asked == [thread_id]
    replayed = wiring.client.get(f"/conversations/{thread_id}", headers=headers).json()["turns"]
    assert [event["type"] for event in replayed[0]["events"]] == [
        "node_start",
        "reasoning",
        "done",
    ]


def test_a_broken_turn_stores_the_history_it_did_produce_and_its_failed_frame(tmp_path):
    """A turn that died after its tool ran replays that tool and the frame the API closed with."""
    transcripts = FakeTranscripts()
    client = _client(tmp_path, chat_runner=BreakingRunner(PLOTTED[:6]), transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)
    transcripts.stored[thread_id] = [Message(role="user", content=QUESTION)]

    response = client.post(
        "/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers
    )

    assert _sse_events(response.text)[-1]["status"] == STATUS_FAILED
    events = client.get(f"/conversations/{thread_id}", headers=headers).json()["turns"][0]["events"]
    assert [event["type"] for event in events] == [
        "node_start",
        "reasoning",
        "tool_call",
        "tool_result",
        "done",
    ]
    assert events[-1]["status"] == STATUS_FAILED
    assert SECRET_HOST not in json.dumps(events)


def test_the_stored_history_is_what_went_on_the_wire(tmp_path):
    """History is written from the framing itself, so a replay cannot claim an unsent frame."""
    transcripts = FakeTranscripts()
    client = _client(tmp_path, chat_runner=FakeRunner(events=REFUSED), transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)

    response = client.post(
        "/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers
    )

    streamed = [event for event in _sse_events(response.text) if event["type"] != "token"]
    events = client.get(f"/conversations/{thread_id}", headers=headers).json()["turns"][0]["events"]
    assert [event["type"] for event in events] == [event["type"] for event in streamed]


def test_turn_history_of_a_foreign_thread_is_never_served(tmp_path):
    """The 404 is the whole answer: no transcript, no history, nothing about the thread at all."""
    transcripts = FakeTranscripts()
    client = _client(tmp_path, chat_runner=FakeRunner(events=PLOTTED), transcript=transcripts)
    alice_headers = _headers(client, ALICE)
    thread_id = _new_thread(client, alice_headers, title=QUESTION)
    client.post("/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=alice_headers)
    transcripts.asked.clear()

    bob_headers = _headers(client, BOB)
    foreign = client.get(f"/conversations/{thread_id}", headers=bob_headers)
    missing = client.get("/conversations/no-such-thread", headers=bob_headers)

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert CHART_SPEC["title"] not in foreign.text
    assert THOUGHT not in foreign.text
    assert transcripts.asked == []
    kept = client.get(f"/conversations/{thread_id}", headers=alice_headers).json()
    assert len(kept["turns"]) == 1


def test_patch_conversation_retitles_the_thread_from_its_first_exchange(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers, title=QUESTION)
    wiring.transcripts.stored[thread_id] = list(TRANSCRIPT)

    response = wiring.client.patch(f"/conversations/{thread_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["title"] == GENERATED_TITLE
    assert response.json()["thread_id"] == thread_id
    assert QUESTION in wiring.titler.prompts[0]
    listed = wiring.client.get("/conversations", headers=headers).json()
    assert listed[0]["title"] == GENERATED_TITLE


def test_patch_conversation_on_a_foreign_thread_is_a_404_that_titles_nothing(wiring):
    alice_headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, alice_headers, title=QUESTION)
    wiring.transcripts.stored[thread_id] = list(TRANSCRIPT)
    bob_headers = _headers(wiring.client, BOB)

    foreign = wiring.client.patch(f"/conversations/{thread_id}", headers=bob_headers)
    missing = wiring.client.patch("/conversations/no-such-thread", headers=bob_headers)

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert wiring.titler.prompts == []
    assert wiring.transcripts.asked == []
    kept = wiring.client.get(f"/conversations/{thread_id}", headers=alice_headers)
    assert kept.json()["title"] == QUESTION


def test_patch_conversation_keeps_the_first_message_title_when_titling_fails(tmp_path):
    transcripts = FakeTranscripts()
    titler = FakeTitler(error=RuntimeError(f"connect timeout to {SECRET_HOST}"))
    client = _client(tmp_path, titler=titler, transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)
    transcripts.stored[thread_id] = list(TRANSCRIPT)

    response = client.patch(f"/conversations/{thread_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["title"] == QUESTION
    assert SECRET_HOST not in response.text


def test_patch_conversation_keeps_the_title_of_a_thread_never_chatted_in(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers, title=QUESTION)

    response = wiring.client.patch(f"/conversations/{thread_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["title"] == QUESTION
    assert wiring.titler.prompts == []


def test_patch_conversation_retitles_after_each_turn_while_the_thread_is_young(tmp_path):
    """A thread that opened with a greeting is renamed from the question that followed (#118)."""
    window = runtime().conversations.title_turns
    transcripts = FakeTranscripts()
    titler = FakeTitler()
    client = _client(tmp_path, titler=titler, transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=GREETING)

    for turn in range(1, window + 1):
        transcripts.stored[thread_id] = _greeted_transcript(turn)
        titler.answer = f"{GENERATED_TITLE} {turn}"

        response = client.patch(f"/conversations/{thread_id}", headers=headers)

        assert response.status_code == 200
        assert response.json()["title"] == f"{GENERATED_TITLE} {turn}"
    assert GREETING in titler.prompts[-1]
    assert QUESTION in titler.prompts[-1]


def test_patch_conversation_stops_titling_a_thread_past_the_window(tmp_path):
    window = runtime().conversations.title_turns
    transcripts = FakeTranscripts()
    titler = FakeTitler(answer="Settled label")
    client = _client(tmp_path, titler=titler, transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=GREETING)
    transcripts.stored[thread_id] = _greeted_transcript(window)
    settled = client.patch(f"/conversations/{thread_id}", headers=headers).json()["title"]

    transcripts.stored[thread_id] = _greeted_transcript(window + 1)
    titler.answer = "A later subject"
    response = client.patch(f"/conversations/{thread_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["title"] == settled == "Settled label"
    assert len(titler.prompts) == 1


def test_patch_conversation_with_a_title_is_the_readers_own_rename(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers, title=QUESTION)

    response = wiring.client.patch(
        f"/conversations/{thread_id}", json={"title": " Q3 comp\nreview "}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Q3 comp review"
    assert wiring.titler.prompts == []


def test_a_readers_rename_is_never_overwritten_by_a_later_generated_title(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers, title=QUESTION)
    wiring.client.patch(
        f"/conversations/{thread_id}", json={"title": RENAMED}, headers=headers
    )
    wiring.transcripts.stored[thread_id] = list(TRANSCRIPT)

    response = wiring.client.patch(f"/conversations/{thread_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["title"] == RENAMED
    listed = wiring.client.get("/conversations", headers=headers).json()
    assert listed[0]["title"] == RENAMED


def test_patch_conversation_refuses_a_blank_title_and_changes_nothing(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers, title=QUESTION)

    response = wiring.client.patch(
        f"/conversations/{thread_id}", json={"title": "   "}, headers=headers
    )

    assert response.status_code == 400
    assert wiring.client.get(f"/conversations/{thread_id}", headers=headers).json()["title"] == (
        QUESTION
    )


def test_patch_conversation_with_a_title_on_a_foreign_thread_renames_nothing(wiring):
    alice_headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, alice_headers, title=QUESTION)
    bob_headers = _headers(wiring.client, BOB)

    foreign = wiring.client.patch(
        f"/conversations/{thread_id}", json={"title": RENAMED}, headers=bob_headers
    )
    missing = wiring.client.patch(
        "/conversations/no-such-thread", json={"title": RENAMED}, headers=bob_headers
    )

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    kept = wiring.client.get(f"/conversations/{thread_id}", headers=alice_headers)
    assert kept.json()["title"] == QUESTION


def test_a_generated_title_is_stored_stripped_of_control_characters(tmp_path):
    transcripts = FakeTranscripts()
    client = _client(
        tmp_path, titler=FakeTitler(answer="Head\x00count\nby office"), transcript=transcripts
    )
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)
    transcripts.stored[thread_id] = list(TRANSCRIPT)

    response = client.patch(f"/conversations/{thread_id}", headers=headers)

    assert response.json()["title"] == "Head count by office"


def test_conversation_lists_are_scoped_to_the_identity(wiring):
    _new_thread(wiring.client, _headers(wiring.client, ALICE), title="acme thread")
    listed = wiring.client.get("/conversations", headers=_headers(wiring.client, BOB))
    assert listed.status_code == 200
    assert listed.json() == []


def test_an_untitled_conversation_gets_the_configured_default_title(wiring):
    headers = _headers(wiring.client, ALICE)
    response = wiring.client.post("/conversations", json={}, headers=headers)
    assert response.json()["title"] == runtime().api.default_title


def test_login_chat_list_delete_happy_path(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers, title="how many employees?")

    chat = wiring.client.post(
        "/chat", json={"thread_id": thread_id, "message": "how many employees?"}, headers=headers
    )
    assert chat.status_code == 200

    listed = wiring.client.get("/conversations", headers=headers).json()
    assert [thread["thread_id"] for thread in listed] == [thread_id]
    assert listed[0]["title"] == "how many employees?"

    fetched = wiring.client.get(f"/conversations/{thread_id}", headers=headers)
    assert fetched.json()["thread_id"] == thread_id

    deleted = wiring.client.delete(f"/conversations/{thread_id}", headers=headers)
    assert deleted.status_code == 204
    assert wiring.deleted == [thread_id]
    assert wiring.client.get("/conversations", headers=headers).json() == []


def test_deleting_a_foreign_thread_leaves_it_and_its_checkpoints_alone(wiring):
    alice_headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, alice_headers)
    response = wiring.client.delete(
        f"/conversations/{thread_id}", headers=_headers(wiring.client, BOB)
    )
    assert response.status_code == 404
    assert wiring.deleted == []
    still_there = wiring.client.get(f"/conversations/{thread_id}", headers=alice_headers)
    assert still_there.status_code == 200


@pytest.mark.parametrize("secret", [None, SHORT_SECRET], ids=["unset", "too short"])
def test_the_app_refuses_to_build_without_a_usable_signing_secret(monkeypatch, secret):
    if secret is None:
        monkeypatch.delenv(SECRET_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(SECRET_ENV_VAR, secret)
    with pytest.raises(AuthError):
        create_app()


def _token_expiring_at(expires_at: int, issued_at: int) -> str:
    """A validly signed token for ALICE with exact issue and expiry seconds (ADR 0009)."""
    return jwt.encode(
        {"sub": ALICE[0], "tenant_id": ACME, "iat": issued_at, "exp": expires_at},
        TEST_SECRET,
        algorithm="HS256",
    )


def _expiring_headers(seconds: float) -> dict[str, str]:
    """An Authorization header whose token has the given remaining lifetime."""
    issued_at = int(time.time())
    return {"Authorization": f"Bearer {_token_expiring_at(issued_at + int(seconds), issued_at)}"}


def _inside_the_window() -> float:
    """A remaining lifetime comfortably inside the configured refresh window, in seconds."""
    return runtime().auth.refresh_within_minutes * 60 / 2


def test_a_request_inside_the_refresh_window_is_answered_with_a_fresh_token(wiring):
    response = wiring.client.get("/conversations", headers=_expiring_headers(_inside_the_window()))

    assert response.status_code == 200
    claims = jwt.decode(response.headers[REFRESHED_TOKEN_HEADER], TEST_SECRET, algorithms=["HS256"])
    assert (claims["sub"], claims["tenant_id"]) == (ALICE[0], ACME)
    assert claims["exp"] - claims["iat"] == runtime().auth.token_ttl_minutes * 60


def test_the_refreshed_token_is_accepted_and_needs_no_further_refresh(wiring):
    refreshed = wiring.client.get(
        "/conversations", headers=_expiring_headers(_inside_the_window())
    ).headers[REFRESHED_TOKEN_HEADER]

    again = wiring.client.get("/conversations", headers={"Authorization": f"Bearer {refreshed}"})

    assert again.status_code == 200
    assert REFRESHED_TOKEN_HEADER not in again.headers


def test_a_request_outside_the_refresh_window_carries_no_refreshed_token(wiring):
    response = wiring.client.get("/conversations", headers=_headers(wiring.client, ALICE))
    assert response.status_code == 200
    assert REFRESHED_TOKEN_HEADER not in response.headers


def test_an_expired_token_is_still_rejected_and_refreshes_nothing(wiring):
    response = wiring.client.get("/conversations", headers=_expiring_headers(-60))
    assert response.status_code == 401
    assert REFRESHED_TOKEN_HEADER not in response.headers


def test_chat_carries_the_refreshed_token_on_the_streaming_response(wiring):
    thread_id = _new_thread(wiring.client, _headers(wiring.client, ALICE))

    response = wiring.client.post(
        "/chat",
        json={"thread_id": thread_id, "message": QUESTION},
        headers=_expiring_headers(_inside_the_window()),
    )

    assert response.status_code == 200
    assert response.headers[REFRESHED_TOKEN_HEADER]
    assert [event["type"] for event in _sse_events(response.text)] == [
        event["type"] for event in EVENTS
    ]


def test_a_turn_in_flight_is_not_killed_by_the_token_expiring(tmp_path, monkeypatch):
    clock = FrozenClock(at=FROZEN_NOW)
    monkeypatch.setattr(api_jwt, "datetime", clock)
    issued_at = int(FROZEN_NOW.timestamp())
    client = TestClient(
        create_app(
            chat_runner=ExpiringRunner(clock=clock),
            model_lister=lambda: list(SERVED_MODELS),
            capability_checker=FakeCapabilities(),
            registry=ConversationRegistry(tmp_path / "state.db"),
            cleanup=lambda thread_id: None,
            data_store=lambda: None,
            note_index=lambda: None,
        )
    )
    token = _token_expiring_at(issued_at + TOKEN_LIFETIME_SECONDS, issued_at)
    headers = {"Authorization": f"Bearer {token}"}
    thread_id = _new_thread(client, headers)

    response = client.post(
        "/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert [event["type"] for event in events] == [event["type"] for event in EVENTS]
    assert events[-1]["status"] == "ok"
    assert client.get("/conversations", headers=headers).status_code == 401


def test_cors_exposes_the_refreshed_token_header_to_the_spa(wiring):
    response = wiring.client.get("/health", headers={"Origin": "http://localhost:3002"})
    assert REFRESHED_TOKEN_HEADER in response.headers["access-control-expose-headers"]


def test_cors_allows_only_the_frontend_origin(wiring):
    allowed = wiring.client.get("/health", headers={"Origin": "http://localhost:3002"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3002"
    other = wiring.client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
