"""Suite for the REST edge (issue #23, ADR 0012).

Network-free by construction: the app factory takes the turn runner, the model lister, the
registry and the checkpointer cleanup as arguments, so no test reaches Ollama, the employee
database or the real state files. `FakeRunner` records the keyword arguments it was called
with - which is how the tenant-in-body tests prove the agent was built for the token's tenant
and not the body's - and replays a fixed ADR 0012 event sequence so the SSE framing assertions
are exact.

The registry here is the real `ConversationRegistry` on a tmp_path file: thread scoping is the
security property under test, so faking it would test nothing.
"""

import json
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from app import ModelEndpointError, create_app
from auth import SECRET_ENV_VAR, AuthError
from conversations import ConversationRegistry
from runtime import runtime

TEST_SECRET = "a1" * 32
SHORT_SECRET = "too-short"

ALICE = ("alice@acme", "demo-acme")
BOB = ("bob@beta", "demo-beta")
ACME = "acme"

MODELS = ["fake-model:1b", "other-model:3b"]
CHOSEN_MODEL = MODELS[1]
UNKNOWN_MODEL = "nonexistent-model:9b"

ANSWER = "acme has 6 employees"
EVENTS = (
    {"type": "node_start", "node": "reason"},
    {"type": "token", "text": ANSWER},
    {"type": "done", "status": "ok", "answer": ANSWER},
)

PROTECTED_ROUTES = [
    ("GET", "/models"),
    ("POST", "/chat"),
    ("GET", "/conversations"),
    ("POST", "/conversations"),
    ("GET", "/conversations/whatever"),
    ("DELETE", "/conversations/whatever"),
]


@dataclass
class FakeRunner:
    """Records every turn it is asked to run and replays the canned trace events."""

    calls: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, *, tenant_id, thread_id, message, model):
        """Record the turn, then yield the fixed event sequence with the model echoed back."""
        self.calls.append(
            {"tenant_id": tenant_id, "thread_id": thread_id, "message": message, "model": model}
        )
        for event in EVENTS:
            yield {**event, "model": model} if event["type"] == "done" else dict(event)

    @property
    def last(self) -> dict[str, str]:
        """The most recent turn's arguments."""
        return self.calls[-1]


@dataclass
class Wiring:
    """A wired app plus the fakes the tests inspect."""

    client: TestClient
    runner: FakeRunner
    deleted: list[str]


@pytest.fixture(autouse=True)
def _signing_secret(monkeypatch):
    """Every test runs with a usable signing secret unless it deliberately removes it."""
    monkeypatch.setenv(SECRET_ENV_VAR, TEST_SECRET)


@pytest.fixture
def wiring(tmp_path) -> Wiring:
    """The app with a fake runner, a fake model list, a tmp registry and a recording cleanup."""
    runner = FakeRunner()
    deleted: list[str] = []
    app = create_app(
        chat_runner=runner,
        model_lister=lambda: list(MODELS),
        registry=ConversationRegistry(tmp_path / "state.db"),
        cleanup=deleted.append,
    )
    return Wiring(client=TestClient(app), runner=runner, deleted=deleted)


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


def test_models_returns_the_live_list_and_the_configured_default(wiring):
    response = wiring.client.get("/models", headers=_headers(wiring.client, ALICE))
    assert response.status_code == 200
    assert response.json() == {"models": MODELS, "default": runtime().agent.model}


def test_models_answers_502_generically_when_the_endpoint_is_down(tmp_path):
    def unreachable() -> list[str]:
        raise ModelEndpointError("connect timeout to http://host.example:11434")

    client = TestClient(
        create_app(
            chat_runner=FakeRunner(),
            model_lister=unreachable,
            registry=ConversationRegistry(tmp_path / "state.db"),
            cleanup=lambda thread_id: None,
        )
    )
    response = client.get("/models", headers=_headers(client, ALICE))
    assert response.status_code == 502
    assert "host.example" not in response.text


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
    bob_headers = _headers(wiring.client, BOB)
    foreign = wiring.client.get(f"/conversations/{alice_thread}", headers=bob_headers)
    missing = wiring.client.get("/conversations/no-such-thread", headers=bob_headers)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


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


def test_cors_allows_only_the_frontend_origin(wiring):
    allowed = wiring.client.get("/health", headers={"Origin": "http://localhost:3002"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3002"
    other = wiring.client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
