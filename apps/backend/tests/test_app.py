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

The stored tool payloads (issue #70) are asserted end to end here rather than argued: a turn whose
tool returned a chart spec is streamed, and the conversation is then fetched back to see that spec
served beside the transcript, under the turn the transcript counts. The registry is real, so what
the endpoint cannot leak is real too - a foreign fetch is the same 404 that reads nothing. The
bounds on what is kept belong to `test_conversations.py`, which owns the store.

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
from db import init_db
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
    {"type": "node_start", "node": "execute_tool"},
    {
        "type": "tool_result",
        "id": "c1",
        "tool": "plot",
        "content": "chart displayed to the user",
        "data": {"chart_spec": CHART_SPEC},
    },
    {"type": "token", "text": ANSWER},
    {"type": "done", "status": "ok", "answer": ANSWER, **USAGE},
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
BETA_SECRET = "beta secret"
NOTE_HITS = [
    {"user_id": 1, "name": "Ada Lovelace", "note": "shipped the compiler", "distance": 0.21}
]

PROTECTED_ROUTES = [
    ("GET", "/models"),
    ("GET", "/records"),
    ("GET", "/records/departments"),
    ("GET", "/notes"),
    ("GET", "/notes/search?q=compiler"),
    ("GET", "/notes/flagged"),
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


def _new_thread(client: TestClient, headers: dict[str, str], title: str = "first message") -> str:
    """Create a conversation and return its thread id."""
    response = client.post("/conversations", json={"title": title}, headers=headers)
    assert response.status_code == 201
    return response.json()["thread_id"]


def _sse_events(body: str) -> list[dict]:
    """Parse an SSE body: records split on the blank line, each one a `data:` JSON payload."""
    records = [record for record in body.split("\n\n") if record.strip()]
    return [json.loads(record.removeprefix("data: ")) for record in records]


def test_health_is_open_and_reports_version(wiring):
    response = wiring.client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"]


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


def test_models_returns_the_chat_capable_list_and_the_configured_default(wiring):
    response = wiring.client.get("/models", headers=_headers(wiring.client, ALICE))
    assert response.status_code == 200
    assert response.json() == {"models": CHAT_MODELS, "default": runtime().agent.model}
    assert EMBED_MODEL not in response.json()["models"]


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


def test_records_serves_only_the_rows_of_the_tokens_tenant(wiring):
    """The isolation the Records tab demonstrates, asserted through the HTTP surface (ADR 0014)."""
    acme = wiring.client.get("/records", headers=_headers(wiring.client, ALICE)).json()
    beta = wiring.client.get("/records", headers=_headers(wiring.client, BOB)).json()

    assert acme["total"] == ACME_ROWS
    assert beta["total"] == BETA_ROWS
    assert {row[acme["columns"].index("tenant_id")] for row in acme["rows"]} == {ACME}
    assert {row[beta["columns"].index("tenant_id")] for row in beta["rows"]} == {BETA}


@pytest.mark.parametrize(
    "query",
    [
        {"tenant_id": BETA},
        {"tenant": BETA},
        {"tenant_id": BETA, "name": "grace"},
        {"db_path": "/etc/passwd"},
    ],
)
def test_a_tenant_the_client_invents_is_ignored(wiring, query):
    """`Filters` is the allowlist: a parameter that is not one of its fields is not read at all."""
    response = wiring.client.get(
        "/records", params=query, headers=_headers(wiring.client, ALICE)
    )

    assert response.status_code == 200
    assert BETA not in response.text
    assert BETA_SECRET not in response.text


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

    filtered = wiring.client.get("/records", params={"name": "ada"}, headers=headers).json()
    paged = wiring.client.get(
        "/records", params={"page": 2, "page_size": 1, "sort": "salary"}, headers=headers
    ).json()

    assert filtered["total"] == 1
    assert paged["total"] == ACME_ROWS
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


def test_the_departments_offered_are_the_callers_own(wiring):
    acme = wiring.client.get(
        "/records/departments", headers=_headers(wiring.client, ALICE)
    ).json()

    assert acme == [
        {"department": "Engineering", "employees": 2},
        {"department": "Sales", "employees": 1},
    ]


def test_the_notes_corpus_is_the_callers_own(wiring):
    acme = wiring.client.get("/notes", headers=_headers(wiring.client, ALICE))
    beta = wiring.client.get("/notes", headers=_headers(wiring.client, BOB))

    assert acme.json()["total"] == ACME_ROWS
    assert BETA_SECRET not in acme.text
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
    assert response.json()["hits"] == NOTE_HITS
    assert response.json()["k"] == runtime().rag.top_k


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


def test_the_flagged_notes_are_the_callers_own_manifest_rows(wiring):
    """The committed manifest, filtered to the token's tenant so the tab can mark the payloads."""
    response = wiring.client.get("/notes/flagged", headers=_headers(wiring.client, ALICE))

    assert response.status_code == 200
    assert set(response.json()) == {"user_ids", "kinds"}


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
    assert events[-1]["model"] == runtime().agent.model
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


def test_chat_defaults_to_the_configured_model(wiring):
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)
    wiring.client.post("/chat", json={"thread_id": thread_id, "message": "hi"}, headers=headers)
    assert wiring.runner.last["model"] == runtime().agent.model


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
    assert response.json()["tool_results"] == []


def test_a_turns_tool_results_are_stored_and_replayed_beside_the_transcript(tmp_path):
    """The chart a turn drew is served back with the conversation, so a reload re-renders it."""
    transcripts = FakeTranscripts()
    client = _client(tmp_path, chat_runner=FakeRunner(events=PLOTTED), transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)
    transcripts.stored[thread_id] = list(TRANSCRIPT[:2])

    client.post("/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers)

    replayed = client.get(f"/conversations/{thread_id}", headers=headers).json()
    assert replayed["messages"] == [asdict(message) for message in TRANSCRIPT[:2]]
    assert replayed["tool_results"] == [
        {"turn": 1, "tool": "plot", "data": {"chart_spec": CHART_SPEC}}
    ]


def test_stored_tool_results_are_keyed_by_the_turn_that_asked_for_them(tmp_path):
    """One turn is one question, so the question count is what the evidence is filed under."""
    transcripts = FakeTranscripts()
    client = _client(tmp_path, chat_runner=FakeRunner(events=PLOTTED), transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)

    transcripts.stored[thread_id] = list(TRANSCRIPT[:2])
    client.post("/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers)
    transcripts.stored[thread_id] = list(TRANSCRIPT)
    client.post("/chat", json={"thread_id": thread_id, "message": "and in sales?"}, headers=headers)

    replayed = client.get(f"/conversations/{thread_id}", headers=headers).json()["tool_results"]
    assert [result["turn"] for result in replayed] == [1, 2]


def test_a_turn_that_called_no_tool_stores_nothing_and_reads_no_transcript(wiring):
    """The canned turn answers without a tool: nothing to keep, so nothing is looked up either."""
    headers = _headers(wiring.client, ALICE)
    thread_id = _new_thread(wiring.client, headers)

    wiring.client.post("/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers)

    assert wiring.transcripts.asked == []
    replayed = wiring.client.get(f"/conversations/{thread_id}", headers=headers).json()
    assert replayed["tool_results"] == []


def test_a_broken_turn_stores_the_tool_results_it_did_produce(tmp_path):
    """A turn that died after its tool ran still replays that tool's evidence (issue #66, #70)."""
    transcripts = FakeTranscripts()
    client = _client(tmp_path, chat_runner=BreakingRunner(PLOTTED[:2]), transcript=transcripts)
    headers = _headers(client, ALICE)
    thread_id = _new_thread(client, headers, title=QUESTION)
    transcripts.stored[thread_id] = [Message(role="user", content=QUESTION)]

    response = client.post(
        "/chat", json={"thread_id": thread_id, "message": QUESTION}, headers=headers
    )

    assert _sse_events(response.text)[-1]["status"] == STATUS_FAILED
    replayed = client.get(f"/conversations/{thread_id}", headers=headers).json()["tool_results"]
    assert [(result["turn"], result["tool"]) for result in replayed] == [(1, "plot")]


def test_tool_results_of_a_foreign_thread_are_never_served(tmp_path):
    """The 404 is the whole answer: no transcript, no payload, nothing about the thread at all."""
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
    assert transcripts.asked == []
    kept = client.get(f"/conversations/{thread_id}", headers=alice_headers).json()
    assert len(kept["tool_results"]) == 1



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
