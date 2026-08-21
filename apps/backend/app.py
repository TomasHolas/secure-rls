"""The REST edge (ADR 0012): thin handlers over the auth, registry and agent bricks.

Every handler is one call into a module that already owns the logic. Nothing here validates
SQL, scopes a tenant or talks to a model; it wires, authorizes and serializes.

Identity (ADR 0002 layer 1). `tenant_id` and `sub` come only from `auth.verify_token`. The
request body is never consulted for identity - `ChatRequest` has no tenant field and Pydantic
drops any the client invents, so a body claiming another tenant is silently ineffective.

Endpoints:

- `GET  /health`            open; liveness plus the API version.
- `POST /login`             credentials for a token, or 401.
- `GET  /models`            the endpoint's live chat-capable models plus the configured default.
- `POST /chat`              one turn as an SSE stream of ADR 0012 trace events.
- `GET  /conversations`     the caller's threads, newest first.
- `POST /conversations`     a new thread; the title is the first user message, truncated.
- `GET  /conversations/{id}` the caller's own thread row plus its replayed transcript.
- `DELETE /conversations/{id}` the thread plus its checkpointer rows.

Conversation titles are set when the thread is created (`POST /conversations {"title": ...}`
with the first user message), which is the only write the registry brick exposes; the SPA has
that message in hand before it opens the stream. A thread created without one carries the
configured default title.

`GET /conversations/{id}` returns the registry row plus `messages`, the thread's user questions
and assistant answers replayed from LangGraph's checkpointer by `agent.thread_messages` - the
sidebar needs them to reopen an old thread instead of showing an empty chat while server-side
memory silently continues. The identity check comes first and is unchanged: the registry is
consulted before the checkpointer is opened, so a foreign or missing id is the same 404 and no
transcript is read for a thread the caller may not see.

Model selection (ADR 0005 as amended). The client never learns `OLLAMA_BASE_URL`: `/models`
proxies the endpoint's `/api/tags`, and a client-chosen `model` on `/chat` is honored only if
it is in that live list at request time - an allowlist over untrusted input. Absent, the
`runtime.json` `agent.model` default applies.

The list is filtered to models that can actually hold a conversation: an endpoint also serves
embedding-only models (`nomic-embed-text`, which this app itself uses for RAG), and picking one
breaks the turn. `chat_capable_lister` asks `/api/show` per model id and keeps the ones whose
`capabilities` include `completion`, caching each answer for the process - the tag list is
short and rarely changes, so one lookup per id is enough. An endpoint too old to report
capabilities falls back to excluding the configured `agent.embed_model` by prefix. Filtering
happens in the lister, not the handler, so the `/chat` allowlist is the same list the picker
was offered.

Startup fails fast when `JWT_SECRET` is unset or too weak (ADR 0009): `create_app` calls
`auth.jwt_secret()` before it builds anything, so a misconfigured process refuses to boot
rather than serving unsigned-in-practice tokens. Importing this module is side-effect free -
the production app is built on first access to the module attribute `app`, which is what
`uvicorn app:app` resolves.

Seams. `create_app` takes the turn runner, the model lister, the capability checker, the
registry and the two checkpointer accesses - transcript replay and cleanup - as arguments,
defaulting to the production wiring. Tests pass fakes and never touch Ollama or the filesystem
outside tmp_path.

Paths. All state files are resolved here, once: the employee database (`db.DEFAULT_DB_PATH`,
beside which `db.py` derives its own `audit.db` and `vectors.db`), the registry's `state.db`
and the LangGraph checkpointer's `checkpoints.db`.
"""

import json
import os
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from typing import Annotated, Protocol

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel

from agent import Message, TraceEvent, build_agent, run_turn, thread_messages
from auth import AuthError, Identity, create_token, jwt_secret, verify_password, verify_token
from conversations import ConversationRegistry, NotFound, Thread
from db import DEFAULT_DB_PATH
from rag import OllamaEmbed
from runtime import runtime

API_VERSION = "0.1.0"
FRONTEND_ORIGIN = "http://localhost:3002"
OLLAMA_ENV_VAR = "OLLAMA_BASE_URL"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

DB_PATH = DEFAULT_DB_PATH
STATE_DB_PATH = DB_PATH.with_name("state.db")
CHECKPOINT_DB_PATH = DB_PATH.with_name("checkpoints.db")

_TAGS_PATH = "/api/tags"
_SHOW_PATH = "/api/show"
_COMPLETION_CAPABILITY = "completion"
_INVALID_CREDENTIALS = "invalid credentials"
_INVALID_TOKEN = "invalid or expired token"
_UNKNOWN_MODEL = "unknown model"
_ENDPOINT_UNAVAILABLE = "the model endpoint is unavailable"

_bearer = HTTPBearer(auto_error=False)


class ModelEndpointError(Exception):
    """Raised when the Ollama endpoint cannot be reached; the API answers 502 without detail."""


class ChatRunner(Protocol):
    """Runs one turn for a tenant and yields the ADR 0012 trace events in order."""

    def __call__(
        self, *, tenant_id: str, thread_id: str, message: str, model: str
    ) -> Iterator[TraceEvent]:
        """Stream the turn's trace events; a raise is a transport failure, not an answer."""
        ...


class ModelLister(Protocol):
    """Returns the model ids the endpoint currently serves."""

    def __call__(self) -> list[str]:
        """List the live model ids, raising ModelEndpointError when the endpoint is unusable."""
        ...


class CapabilityChecker(Protocol):
    """Reports what one model id can do, or None when the endpoint does not say."""

    def __call__(self, model_id: str) -> list[str] | None:
        """The model's capabilities, or None if the endpoint reports none for it."""
        ...


class LoginRequest(BaseModel):
    """Credentials for `POST /login`."""

    username: str
    password: str


class ChatRequest(BaseModel):
    """One turn: the thread, the message and an optional model id. No tenant field, ever."""

    thread_id: str
    message: str
    model: str | None = None


class ConversationRequest(BaseModel):
    """A new thread, titled with the first user message when the client has one."""

    title: str | None = None


@dataclass(frozen=True)
class Conversation:
    """One thread as `GET /conversations/{id}` serves it: the registry row plus the transcript."""

    thread_id: str
    title: str
    created: str
    messages: list[Message]


def ollama_chat_runner(base_url: str) -> ChatRunner:
    """The production runner: ChatOllama plus the tenant's graph over the SQLite checkpointer."""

    def run(
        *, tenant_id: str, thread_id: str, message: str, model: str
    ) -> Iterator[TraceEvent]:
        """Build the graph for this turn and stream it; the checkpointer closes with the stream."""
        with SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as checkpointer:
            graph = build_agent(
                tenant_id,
                ChatOllama(base_url=base_url, model=model),
                checkpointer,
                embedder=OllamaEmbed(base_url),
                model_id=model,
                db_path=DB_PATH,
            )
            yield from run_turn(graph, message, thread_id)

    return run


def ollama_model_lister(base_url: str) -> ModelLister:
    """The production model lister: the endpoint's `/api/tags`, on a short timeout."""

    def list_models() -> list[str]:
        """Return the live model ids, or raise ModelEndpointError with nothing client-visible."""
        try:
            with httpx.Client(
                base_url=base_url, timeout=runtime().api.models_timeout_s
            ) as client:
                response = client.get(_TAGS_PATH)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ModelEndpointError(str(exc)) from exc
        return [entry["model"] for entry in payload.get("models", [])]

    return list_models


def ollama_capability_checker(base_url: str) -> CapabilityChecker:
    """The production capability checker: the endpoint's `/api/show`, on the same short timeout."""

    def capabilities(model_id: str) -> list[str] | None:
        """Return the model's declared capabilities, or None when the endpoint reports none."""
        try:
            with httpx.Client(
                base_url=base_url, timeout=runtime().api.models_timeout_s
            ) as client:
                response = client.post(_SHOW_PATH, json={"model": model_id})
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ModelEndpointError(str(exc)) from exc
        declared = payload.get("capabilities")
        return list(declared) if isinstance(declared, list) else None

    return capabilities


def chat_capable_lister(
    list_models: ModelLister, capabilities: CapabilityChecker
) -> ModelLister:
    """Wrap a lister so only chat-capable ids survive; each id's capabilities are cached here."""
    cached: dict[str, list[str] | None] = {}

    def chat_capable(model_id: str) -> bool:
        """Keep a model that declares `completion`; without a declaration, exclude the embedder."""
        if model_id not in cached:
            cached[model_id] = capabilities(model_id)
        declared = cached[model_id]
        if declared is None:
            return not model_id.startswith(runtime().agent.embed_model)
        return _COMPLETION_CAPABILITY in declared

    def list_chat_models() -> list[str]:
        """The live list minus everything that cannot answer a turn."""
        return [model_id for model_id in list_models() if chat_capable(model_id)]

    return list_chat_models


def read_transcript(thread_id: str) -> list[Message]:
    """Replay a thread's exchanges from the LangGraph checkpointer file; the agent owns the how."""
    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as checkpointer:
        return thread_messages(checkpointer, thread_id)


def delete_checkpoints(thread_id: str) -> None:
    """Drop a deleted thread's LangGraph checkpointer rows; the registry row is already gone."""
    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as checkpointer:
        checkpointer.delete_thread(thread_id)


def create_app(
    *,
    chat_runner: ChatRunner | None = None,
    model_lister: ModelLister | None = None,
    capability_checker: CapabilityChecker | None = None,
    registry: ConversationRegistry | None = None,
    transcript: Callable[[str], list[Message]] | None = None,
    cleanup: Callable[[str], None] | None = None,
) -> FastAPI:
    """Build the API, refusing to start without a usable signing secret (ADR 0009)."""
    jwt_secret()
    base_url = os.environ.get(OLLAMA_ENV_VAR, DEFAULT_OLLAMA_BASE_URL)
    run_chat = chat_runner or ollama_chat_runner(base_url)
    list_models = chat_capable_lister(
        model_lister or ollama_model_lister(base_url),
        capability_checker or ollama_capability_checker(base_url),
    )
    threads = registry or ConversationRegistry(STATE_DB_PATH)
    replay = transcript or read_transcript
    drop_checkpoints = cleanup or delete_checkpoints

    app = FastAPI(title="secure-rls API", version=API_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_ORIGIN],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_exception_handler(NotFound, _not_found)
    app.add_exception_handler(ModelEndpointError, _bad_gateway)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness for the demo runbook and compose health checks; open by design."""
        return {"status": "ok", "version": API_VERSION}

    @app.post("/login")
    def login(body: LoginRequest) -> dict[str, str]:
        """Exchange demo credentials for a tenant-claim token, or 401 without saying which half."""
        identity = verify_password(body.username, body.password)
        if identity is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, _INVALID_CREDENTIALS)
        return {"token": create_token(identity)}

    @app.get("/models", dependencies=[Depends(_identity)])
    def models() -> dict[str, object]:
        """The endpoint's live chat-capable models plus the configured default (ADR 0005)."""
        return {"models": list_models(), "default": runtime().agent.model}

    @app.post("/chat")
    def chat(
        body: ChatRequest, identity: Annotated[Identity, Depends(_identity)]
    ) -> StreamingResponse:
        """Stream one turn as SSE; the thread must belong to the token's identity."""
        threads.get_thread(identity, body.thread_id)
        events = run_chat(
            tenant_id=identity.tenant_id,
            thread_id=body.thread_id,
            message=body.message,
            model=_resolve_model(body.model, list_models),
        )
        return StreamingResponse(_sse(events), media_type="text/event-stream")

    @app.get("/conversations")
    def list_conversations(identity: Annotated[Identity, Depends(_identity)]) -> list[Thread]:
        """The caller's own threads, newest first."""
        return threads.list_threads(identity)

    @app.post("/conversations", status_code=status.HTTP_201_CREATED)
    def create_conversation(
        body: ConversationRequest, identity: Annotated[Identity, Depends(_identity)]
    ) -> Thread:
        """Register a thread for the caller, titled with the first user message when given."""
        return threads.create_thread(identity, body.title or runtime().api.default_title)

    @app.get("/conversations/{thread_id}")
    def get_conversation(
        thread_id: str, identity: Annotated[Identity, Depends(_identity)]
    ) -> Conversation:
        """The caller's own thread and its transcript; a foreign or missing id is the same 404.

        The registry answers first, so an id the caller does not own never reaches the
        checkpointer. `messages` replays the exchanges only - the questions asked and the
        answers given. The live trace of a turn (tool calls, generated vs executed SQL,
        security events, retries) is ephemeral by design (ADR 0012): it is the SSE transport of
        the turn that produced it, watched once, never re-served. A thread never chatted in
        replays as an empty list.
        """
        thread = threads.get_thread(identity, thread_id)
        return Conversation(**asdict(thread), messages=replay(thread_id))

    @app.delete("/conversations/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_conversation(
        thread_id: str, identity: Annotated[Identity, Depends(_identity)]
    ) -> Response:
        """Delete the caller's own thread and its checkpointer state."""
        threads.delete_thread(identity, thread_id, cleanup=drop_checkpoints)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


def _identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Identity:
    """The verified caller behind the Bearer token - the only source of tenant and sub."""
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            _INVALID_TOKEN,
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verify_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            _INVALID_TOKEN,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _resolve_model(requested: str | None, list_models: ModelLister) -> str:
    """Honor a client model id only if the endpoint serves it for chat now; else the configured."""
    if requested is None:
        return runtime().agent.model
    if requested not in list_models():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _UNKNOWN_MODEL)
    return requested


def _sse(events: Iterator[TraceEvent]) -> Iterator[str]:
    """Frame each trace event as one SSE `data:` record; the events are JSON-able as they come."""
    for event in events:
        yield f"data: {json.dumps(event)}\n\n"


async def _not_found(request: Request, exc: Exception) -> JSONResponse:
    """A foreign thread answers exactly like a missing one (existence non-disclosure)."""
    return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_404_NOT_FOUND)


async def _bad_gateway(request: Request, exc: Exception) -> JSONResponse:
    """An unreachable model endpoint is a generic 502; the address never reaches the client."""
    return JSONResponse(
        {"detail": _ENDPOINT_UNAVAILABLE}, status_code=status.HTTP_502_BAD_GATEWAY
    )


def __getattr__(name: str) -> FastAPI:
    """Build the production app on first access to `app`, so importing this module is inert."""
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
