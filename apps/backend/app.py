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
- `GET  /records`           one filtered, sorted page of the caller's own employee rows.
- `GET  /records/departments` the caller's departments and headcounts, for the filter's options.
- `GET  /notes`             one page of the caller's note corpus.
- `GET  /notes/search`      the agent's own retrieval path, run for a reader's query.
- `GET  /notes/flagged`     which of the caller's rows the committed poison manifest plants.
- `GET  /conversations`     the caller's threads, newest first.
- `POST /conversations`     a new thread; the title is the first user message, truncated.
- `GET  /conversations/{id}` the caller's own thread row plus its replayed transcript.
- `PATCH /conversations/{id}` the thread retitled from its first exchange by the model.
- `DELETE /conversations/{id}` the thread plus its checkpointer rows.

Conversation titles (ADR 0012 as amended). A thread is created titled with its first user
message, truncated (`POST /conversations {"title": ...}`) - the SPA has that message in hand
before it opens the stream, and a thread created without one carries the configured default.
`PATCH /conversations/{id}` then replaces it with the few-word label `titles.py` gets from the
model, and returns the updated row.

That PATCH is deliberately its own request rather than a step inside the `/chat` stream: the
titling call is an LLM call, and one that hangs, times out or dies must not be able to delay a
token or break the turn's termination contract. The SPA fires it once the `done` frame has
landed, so the answer is already on screen and the only thing at stake is the label. The
handler's order is the security-relevant part: the registry is asked first, so a foreign or
missing id is the same 404 as everywhere else and neither the transcript nor the model is
touched for a thread the caller may not see.

`GET /conversations/{id}` returns the registry row plus `messages`, the thread's user questions
and assistant answers replayed from LangGraph's checkpointer by `agent.thread_messages` - the
sidebar needs them to reopen an old thread instead of showing an empty chat while server-side
memory silently continues. The identity check comes first and is unchanged: the registry is
consulted before the checkpointer is opened, so a foreign or missing id is the same 404 and no
transcript is read for a thread the caller may not see.

Replayed tool evidence (ADR 0012 as amended). The same response carries `tool_results`, the
server-produced payload of every `tool_result` the thread's turns produced, keyed by the turn
whose question opened them - so a reopened thread renders its charts, its generated-versus-
executed SQL pair and its tables through the same bricks a live turn does, instead of prose
where a plot used to be. The evidence is collected off the `/chat` stream as it passes (`_recorded`)
and written once the turn is over, under the turn number the transcript then reports: one turn
is one question, so counting the questions is what aligns the evidence with the answer above it.
A turn that broke mid-flight stores the payloads it did produce, and a storage failure is logged
and never reaches the reader - the answer already streamed, and losing a replayable chart is not
worth failing a turn over. What stays session-only is the thinking: the reasoning, the retries
and the node steps are the transport of the turn that produced them and are not stored anywhere.

Browsing the data itself (ADR 0014). The Records and Notes tabs read through `browse.py`, whose
two allowlisted templates go down the same `db.execute_scoped` path the agent's tools do, and
the notes search delegates to `rag.search_notes_scoped`, the retrieval path the `search_notes`
tool uses. So these handlers stay what every other one here is - one call into the module that
owns the logic - and the tenant reaches them the only way it ever does, from the verified token.
The filter values arrive as query parameters typed by `browse.Filters`, which is the allowlist:
a parameter the client invents is not in it and is dropped, exactly as a body field would be.

Three exception handlers turn a refused browse into an honest status without narrating the
server: `QueryRejected` is a 400 carrying its own reason (a sort outside the allowlist, a date
that is not one), `RetrievalUnavailable` a 503 saying the note index is not built, and a
`SecurityViolation` - which on this path would mean one of our own templates is broken, not a
model misbehaving - a bare 403 that is logged in full server-side and says nothing to the client.

Model selection (ADR 0005 as amended). The client never learns `OLLAMA_BASE_URL`: `/models`
proxies the endpoint's `/api/tags`, and a client-chosen `model` on `/chat` is honored only if
it is in that live list at request time - an allowlist over untrusted input. Absent, the
`runtime.json` `agent.model` default applies.

The list is filtered to models that can actually hold a conversation: an endpoint also serves
embedding-only models (`nomic-embed-text`, which this app itself uses for RAG), and picking one
breaks the turn. `chat_capable_lister` keeps the ids whose declared `capabilities` include
`completion`; an endpoint too old to report capabilities falls back to excluding the configured
`agent.embed_model` by prefix. Filtering happens in the lister, not the handler, so the `/chat`
allowlist is the same list the picker was offered.

The same declaration decides whether a turn asks the model to think (ADR 0012 as amended).
Ollama refuses a `think` request outright for a model that does not declare `thinking`, so
`thinking_checker` enables the reasoning channel per model rather than per process: configured
on in `agent.thinking` and declared by the model, or no reasoning for that turn. One
`cached_capabilities` wrapper serves both readers, so `/api/show` is asked once per id per
process however many turns and model lists follow.

Sliding session (ADR 0009 as amended). Every authenticated request re-issues the caller's
token when it is close to expiring and returns the new one on the `X-Refreshed-Token`
response header (exposed to the SPA through CORS), so an active user is never signed out
mid-demo. The header is set once per request by the `_identity` dependency, from the token
the request arrived with; `/chat` copies it onto the streaming response because a directly
returned `Response` does not inherit the dependency's headers. Verification happens once, at
request start - the SSE generator never re-checks, so a turn already in flight completes even
if the clock passes `exp` while it streams.

Startup fails fast when `JWT_SECRET` is unset or too weak (ADR 0009): `create_app` calls
`auth.jwt_secret()` before it builds anything, so a misconfigured process refuses to boot
rather than serving unsigned-in-practice tokens. Importing this module is side-effect free -
the production app is built on first access to the module attribute `app`, which is what
`uvicorn app:app` resolves.

Stream termination (ADR 0012 as amended). `POST /chat` always ends in one `done` frame. The
agent composes `ok`, `blocked`, `gave_up` and `cut_short`; a run that breaks before it can - an
unreachable model endpoint - is closed here with `status: "failed"` and the reason in `answer`,
instead of a body that simply stops and leaves the reader waiting. The reason is generic on
purpose: the exception is logged server-side, never streamed.

Bounded generation (ADR 0011 as amended). The model client this module builds carries the two
generation bounds a turn cannot set for itself: `agent.max_output_tokens` as Ollama's
`num_predict` and `agent.context_window` as its `num_ctx`. They belong here because this is the
module that owns the client - the graph is handed a model, never an endpoint - and they are the
resource half of the per-turn budget whose other half (the wall-clock deadline and the
tool-round cap) the graph enforces.

Startup indexing (ADR 0010 as amended). `create_app` builds the note vector store before it
serves anything, idempotently - a store that already holds notes is left alone, so only an empty
or missing one costs embeddings. It needs the embedding endpoint, so a failure to reach it is
logged and boot continues; `search_notes` then reports retrieval as offline rather than raising.

Seams. `create_app` takes the turn runner, the model lister, the capability checker, the
titler, the registry, the note indexer, the note search and the two checkpointer accesses -
transcript replay and cleanup - as arguments, defaulting to the production wiring, plus the
`db_path` every one of them reads the employee data from. Tests pass fakes and a tmp database,
and never touch Ollama or the filesystem outside tmp_path.

Paths. All state files are resolved here, once: the employee database (`db.DEFAULT_DB_PATH`,
beside which `db.py` derives its own `audit.db` and `vectors.db`), the registry's `state.db`
and the LangGraph checkpointer's `checkpoints.db`.
"""

import json
import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Annotated, Protocol

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel

from agent import (
    EVENT_DONE,
    EVENT_TOOL_RESULT,
    ROLE_USER,
    STATUS_FAILED,
    DoneEvent,
    Message,
    ToolResultEvent,
    TraceEvent,
    build_agent,
    run_turn,
    thread_messages,
)
from auth import (
    AuthError,
    Identity,
    create_token,
    jwt_secret,
    refreshed_token,
    verify_password,
    verify_token,
)
from browse import (
    DEFAULT_DIRECTION,
    DEFAULT_SORT,
    BrowsePage,
    Filters,
    Flagged,
    browse_notes,
    browse_records,
    departments,
    flagged_user_ids,
)
from conversations import ConversationRegistry, NotFound, Thread, ToolResult
from db import DEFAULT_DB_PATH, SecurityViolation
from rag import OllamaEmbed, RetrievalUnavailable, ensure_index, search_notes_scoped
from runtime import runtime
from security import QueryRejected
from titles import TitleModel, generate_title

API_VERSION = "0.1.0"
FRONTEND_ORIGIN = "http://localhost:3002"
REFRESHED_TOKEN_HEADER = "X-Refreshed-Token"
OLLAMA_ENV_VAR = "OLLAMA_BASE_URL"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

DB_PATH = DEFAULT_DB_PATH
STATE_DB_PATH = DB_PATH.with_name("state.db")
CHECKPOINT_DB_PATH = DB_PATH.with_name("checkpoints.db")

_TAGS_PATH = "/api/tags"
_SHOW_PATH = "/api/show"
_CHAT_PATH = "/api/chat"
_COMPLETION_CAPABILITY = "completion"
_THINKING_CAPABILITY = "thinking"
_INVALID_CREDENTIALS = "invalid credentials"
_INVALID_TOKEN = "invalid or expired token"
_UNKNOWN_MODEL = "unknown model"
_ENDPOINT_UNAVAILABLE = "the model endpoint is unavailable"
_TURN_FAILED = (
    "The turn ended in a server-side failure before an answer was composed. Nothing is left "
    "running, the failure is in the server log, and the conversation is unaffected - ask again."
)
_REFUSED = "the request was refused by a security layer"
_INDEX_FAILED = "the note index could not be built at startup; search_notes will say it is offline"
_INDEX_READY = "the note index holds %d notes"

_LOG = logging.getLogger(__name__)
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


class NoteSearch(Protocol):
    """Runs one notes retrieval for a tenant: the agent's own path, called by the Notes tab."""

    def __call__(self, *, query: str, tenant_id: str, k: int) -> list[dict[str, object]]:
        """The scoped hits for query, or raise RetrievalUnavailable when no index exists."""
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
class NoteHits:
    """What `GET /notes/search` serves: the query, the hits asked for, and the scored matches."""

    query: str
    k: int
    hits: list[dict[str, object]]


@dataclass(frozen=True)
class Conversation:
    """One thread as `GET /conversations/{id}` serves it: the row, the transcript, the evidence."""

    thread_id: str
    title: str
    created: str
    messages: list[Message]
    tool_results: list[ToolResult]


def bounded_model(base_url: str, model: str, *, reasoning: bool) -> ChatOllama:
    """The turn's model client with its generation bounded (ADR 0011 as amended, OWASP LLM10).

    `num_predict` caps what one call may generate and `num_ctx` sizes the context it generates
    into; langchain-ollama forwards both as Ollama request `options` (verified against 1.1.0).
    Unset, each falls back to the endpoint's own default - which is how one hostile prompt was
    able to generate for forty minutes, and why these are `runtime.json` knobs rather than
    something the endpoint decides for us.
    """
    return ChatOllama(
        base_url=base_url,
        model=model,
        reasoning=reasoning,
        num_predict=runtime().agent.max_output_tokens,
        num_ctx=runtime().agent.context_window,
    )


def ollama_chat_runner(
    base_url: str, thinking: Callable[[str], bool], db_path: Path
) -> ChatRunner:
    """The production runner: ChatOllama plus the tenant's graph over the SQLite checkpointer."""

    def run(
        *, tenant_id: str, thread_id: str, message: str, model: str
    ) -> Iterator[TraceEvent]:
        """Build the graph for this turn and stream it; the checkpointer closes with the stream."""
        with SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as checkpointer:
            graph = build_agent(
                tenant_id,
                bounded_model(base_url, model, reasoning=thinking(model)),
                checkpointer,
                embedder=OllamaEmbed(base_url),
                model_id=model,
                db_path=db_path,
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


def cached_capabilities(capabilities: CapabilityChecker) -> CapabilityChecker:
    """Wrap a checker so each id is asked once per process; the tag list is short and stable."""
    cached: dict[str, list[str] | None] = {}

    def read(model_id: str) -> list[str] | None:
        """The model's capabilities, from the cache after the first lookup."""
        if model_id not in cached:
            cached[model_id] = capabilities(model_id)
        return cached[model_id]

    return read


def ollama_titler(base_url: str) -> TitleModel:
    """The production titler: one non-streaming `/api/chat` completion on the titling timeout.

    The endpoint is called directly rather than through the graph's `ChatOllama` because this is
    not a turn: no tools, no tenant, no checkpoint, one prompt and one line back. What it does
    need is a hard deadline, which is why it sits next to the other two raw-httpx callers here.
    An unreachable or slow endpoint raises, and `titles.generate_title` falls back.
    """

    def ask(prompt: str) -> str:
        """Ask the configured model for a title and return whatever it answered, verbatim."""
        with httpx.Client(
            base_url=base_url, timeout=runtime().conversations.title_timeout_s
        ) as client:
            response = client.post(
                _CHAT_PATH,
                json={
                    "model": runtime().agent.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
            )
            response.raise_for_status()
        return response.json().get("message", {}).get("content", "")

    return ask


def chat_capable_lister(
    list_models: ModelLister, capabilities: CapabilityChecker
) -> ModelLister:
    """Wrap a lister so only chat-capable ids survive, per what the endpoint declares."""

    def chat_capable(model_id: str) -> bool:
        """Keep a model that declares `completion`; without a declaration, exclude the embedder."""
        declared = capabilities(model_id)
        if declared is None:
            return not model_id.startswith(runtime().agent.embed_model)
        return _COMPLETION_CAPABILITY in declared

    def list_chat_models() -> list[str]:
        """The live list minus everything that cannot answer a turn."""
        return [model_id for model_id in list_models() if chat_capable(model_id)]

    return list_chat_models


def thinking_checker(capabilities: CapabilityChecker) -> Callable[[str], bool]:
    """Whether to ask a model to think: only if it is configured and the model declares it.

    Ollama refuses `think` outright for a model without the `thinking` capability, so asking
    every model to reason would break every turn on an endpoint serving one that cannot.
    """

    def thinks(model_id: str) -> bool:
        """True when the reasoning channel is both wanted and supported for this model."""
        if not runtime().agent.thinking:
            return False
        return _THINKING_CAPABILITY in (capabilities(model_id) or ())

    return thinks


def build_note_index(base_url: str, db_path: Path) -> None:
    """The production indexer: embed the notes unless the store already holds them (ADR 0010)."""
    _LOG.info(_INDEX_READY, ensure_index(db_path, OllamaEmbed(base_url)))


def ollama_note_search(base_url: str, db_path: Path) -> NoteSearch:
    """The production notes search: `rag.search_notes_scoped`, the agent's retrieval path itself."""

    def search(*, query: str, tenant_id: str, k: int) -> list[dict[str, object]]:
        """Return the tenant's nearest notes for query, scored, exactly as the tool sees them."""
        return search_notes_scoped(db_path, OllamaEmbed(base_url), query, tenant_id, k)

    return search


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
    titler: TitleModel | None = None,
    registry: ConversationRegistry | None = None,
    transcript: Callable[[str], list[Message]] | None = None,
    cleanup: Callable[[str], None] | None = None,
    note_index: Callable[[], None] | None = None,
    note_search: NoteSearch | None = None,
    db_path: Path = DB_PATH,
) -> FastAPI:
    """Build the API, refusing to start without a usable signing secret (ADR 0009).

    The note index is built here, before anything is served (ADR 0010 as amended), and its
    failure is not fatal: an unreachable embedding endpoint must not stop the API from booting,
    so it is logged and `search_notes` reports retrieval as offline for as long as it is.
    """
    jwt_secret()
    base_url = os.environ.get(OLLAMA_ENV_VAR, DEFAULT_OLLAMA_BASE_URL)
    index_notes = note_index or (lambda: build_note_index(base_url, db_path))
    capabilities = cached_capabilities(
        capability_checker or ollama_capability_checker(base_url)
    )
    run_chat = chat_runner or ollama_chat_runner(
        base_url, thinking_checker(capabilities), db_path
    )
    list_models = chat_capable_lister(model_lister or ollama_model_lister(base_url), capabilities)
    ask_title = titler or ollama_titler(base_url)
    threads = registry or ConversationRegistry(STATE_DB_PATH)
    replay = transcript or read_transcript
    drop_checkpoints = cleanup or delete_checkpoints
    search_notes = note_search or ollama_note_search(base_url, db_path)
    try:
        index_notes()
    except Exception:
        _LOG.warning(_INDEX_FAILED, exc_info=True)

    app = FastAPI(title="secure-rls API", version=API_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_ORIGIN],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=[REFRESHED_TOKEN_HEADER],
    )
    app.add_exception_handler(NotFound, _not_found)
    app.add_exception_handler(ModelEndpointError, _bad_gateway)
    app.add_exception_handler(QueryRejected, _bad_request)
    app.add_exception_handler(RetrievalUnavailable, _unavailable)
    app.add_exception_handler(SecurityViolation, _forbidden)

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
        body: ChatRequest,
        identity: Annotated[Identity, Depends(_identity)],
        response: Response,
    ) -> StreamingResponse:
        """Stream one turn as SSE; the thread must belong to the token's identity.

        The turn's tool payloads are kept for replay on the way past (ADR 0012 as amended). The
        recording sits between the runner and the SSE framing, so the frames on the wire are
        exactly what the agent yielded and a storage failure cannot change a single one of them.
        """
        threads.get_thread(identity, body.thread_id)
        model = _resolve_model(body.model, list_models)
        events = _recorded(
            run_chat(
                tenant_id=identity.tenant_id,
                thread_id=body.thread_id,
                message=body.message,
                model=model,
            ),
            lambda results: _keep_results(threads, replay, identity, body.thread_id, results),
        )
        stream = StreamingResponse(_sse(events, model), media_type="text/event-stream")
        refreshed = response.headers.get(REFRESHED_TOKEN_HEADER)
        if refreshed is not None:
            stream.headers[REFRESHED_TOKEN_HEADER] = refreshed
        return stream

    @app.get("/records")
    def records(
        identity: Annotated[Identity, Depends(_identity)],
        filters: Annotated[Filters, Depends()],
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIRECTION,
        page: int = 1,
        page_size: int | None = None,
    ) -> BrowsePage:
        """One page of the caller's own employee rows: the Records tab (ADR 0014).

        The tenant is the token's, so there is nothing to authorize here beyond having a token:
        the same query for two identities reads two disjoint sets of rows because the executor
        binds a different tenant into it, not because this handler chose differently.
        """
        return browse_records(
            identity.tenant_id,
            filters=filters,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
            db_path=db_path,
        )

    @app.get("/records/departments")
    def record_departments(
        identity: Annotated[Identity, Depends(_identity)],
    ) -> list[dict[str, object]]:
        """The caller's departments and headcounts, so the filter offers only values it has."""
        return departments(identity.tenant_id, db_path=db_path)

    @app.get("/notes")
    def notes(
        identity: Annotated[Identity, Depends(_identity)],
        filters: Annotated[Filters, Depends()],
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIRECTION,
        page: int = 1,
        page_size: int | None = None,
    ) -> BrowsePage:
        """One page of the caller's note corpus - the text the agent retrieves over (ADR 0014)."""
        return browse_notes(
            identity.tenant_id,
            filters=filters,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
            db_path=db_path,
        )

    @app.get("/notes/search")
    def note_search_results(
        identity: Annotated[Identity, Depends(_identity)],
        q: str,
        k: int | None = None,
    ) -> NoteHits:
        """The agent's own retrieval path, run for a reader's query and scored (ADR 0010).

        Not a second search: `rag.search_notes_scoped` is what the `search_notes` tool calls, so
        the hits and their distances are what the model would have been handed for that query.
        """
        wanted = _hit_count(k)
        return NoteHits(
            query=q,
            k=wanted,
            hits=search_notes(query=q, tenant_id=identity.tenant_id, k=wanted),
        )

    @app.get("/notes/flagged")
    def flagged_notes(identity: Annotated[Identity, Depends(_identity)]) -> Flagged:
        """Which of the caller's rows the committed poison manifest plants a payload in."""
        return flagged_user_ids(identity.tenant_id)

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
        """The caller's own thread, its transcript and its tool evidence; a foreign id is a 404.

        The registry answers first, so an id the caller does not own never reaches the
        checkpointer and never reaches the payload store. `messages` replays what was said - the
        questions asked and the answers given - and `tool_results` the server-produced payload of
        each turn's tool calls, which is what lets a reopened thread re-render its charts, SQL
        pair and tables (ADR 0012 as amended). The thinking around them stays session-only: the
        model's reasoning, the retries and the graph steps are the SSE transport of the turn that
        produced them, watched once and never re-served. A thread never chatted in replays as two
        empty lists.
        """
        thread = threads.get_thread(identity, thread_id)
        return Conversation(
            **asdict(thread),
            messages=replay(thread_id),
            tool_results=threads.thread_tool_results(identity, thread_id),
        )

    @app.patch("/conversations/{thread_id}")
    def retitle_conversation(
        thread_id: str, identity: Annotated[Identity, Depends(_identity)]
    ) -> Thread:
        """Retitle the caller's own thread from its first exchange, and return the updated row.

        The order is the contract. The registry answers first, so a foreign or missing id is the
        same 404 as everywhere else and no transcript is read and no model called for a thread
        the caller may not see. Then the titler gets the exchange and returns a title in every
        case (ADR 0012 as amended): the model's when it gives a usable one, the first question
        when the call fails or answers with junk, the current title when there is nothing to
        name yet. So this endpoint has no failure mode of its own - it either renames the thread
        to something better or leaves it as good as it was.
        """
        thread = threads.get_thread(identity, thread_id)
        title = generate_title(replay(thread_id), ask_title, current=thread.title)
        return threads.rename_thread(identity, thread_id, title)

    @app.delete("/conversations/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_conversation(
        thread_id: str, identity: Annotated[Identity, Depends(_identity)]
    ) -> Response:
        """Delete the caller's own thread and its checkpointer state."""
        threads.delete_thread(identity, thread_id, cleanup=drop_checkpoints)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


def _identity(
    response: Response,
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
        identity = verify_token(credentials.credentials)
        refreshed = refreshed_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            _INVALID_TOKEN,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if refreshed is not None:
        response.headers[REFRESHED_TOKEN_HEADER] = refreshed
    return identity


def _hit_count(requested: int | None) -> int:
    """How many hits one notes search may ask for: the retrieval default, capped by the browse one.

    The default is `rag.top_k` deliberately - unchanged, that is exactly the hit list the agent's
    `search_notes` tool receives, which is the whole point of showing it (ADR 0014).
    """
    config = runtime()
    if requested is None:
        return config.rag.top_k
    return min(max(requested, 1), config.browse.max_search_hits)


def _resolve_model(requested: str | None, list_models: ModelLister) -> str:
    """Honor a client model id only if the endpoint serves it for chat now; else the configured."""
    if requested is None:
        return runtime().agent.model
    if requested not in list_models():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _UNKNOWN_MODEL)
    return requested


def _recorded(
    events: Iterator[TraceEvent], keep: Callable[[list[ToolResultEvent]], None]
) -> Iterator[TraceEvent]:
    """Pass the turn through untouched, keeping its tool results for the store when it ends.

    The write happens once, on the way out, so it costs the stream nothing while tokens are
    flowing and so a turn that broke mid-flight still stores the payloads it did produce - the
    `finally` runs whether the stream ended or raised. A turn that called no tool writes nothing
    and is not even looked up. A storage failure is logged and swallowed here: by then the answer
    has streamed, and a lost replayable chart is not worth turning a good turn into a failed one.
    """
    results: list[ToolResultEvent] = []
    try:
        for event in events:
            if event["type"] == EVENT_TOOL_RESULT:
                results.append(event)
            yield event
    finally:
        try:
            if results:
                keep(results)
        except Exception:
            _LOG.exception("the turn's tool results were not stored")


def _keep_results(
    threads: ConversationRegistry,
    transcript: Callable[[str], list[Message]],
    identity: Identity,
    thread_id: str,
    results: list[ToolResultEvent],
) -> None:
    """Store one turn's tool payloads under the turn its question opened (ADR 0012 as amended).

    The turn number is the count of questions the thread now holds: `/chat` appends exactly one
    of them per turn, so counting them in the transcript gives the same ordinal the SPA arrives
    at when it groups the replayed exchanges into turns. It is read after the turn, when the
    checkpoint that made it the newest question is already written.
    """
    turn = sum(1 for message in transcript(thread_id) if message.role == ROLE_USER)
    threads.record_tool_results(
        identity,
        thread_id,
        [
            ToolResult(turn=turn, tool=result["tool"], data=dict(result["data"]))
            for result in results
        ],
    )


def _sse(events: Iterator[TraceEvent], model: str) -> Iterator[str]:
    """Frame each trace event as one SSE `data:` record, and never end the stream on silence.

    A run that breaks before `done` - an unreachable model endpoint, a recursion limit, anything
    the agent did not turn into a retry - would otherwise close the body mid-flight and leave the
    reader with a turn stuck at "streaming". It closes here instead with the terminal `done` frame
    ADR 0012 defines, status `failed`, so the client always learns how the turn ended. The reason
    is deliberately generic; the exception is logged, where its detail belongs.

    The terminal frame carries the telemetry the turn managed to produce: the seconds it ran
    before it broke, and no token counts, because a run that never reached `respond` never got
    a usage report to pass on.
    """
    closed = False
    started = perf_counter()
    try:
        for event in events:
            closed = event["type"] == EVENT_DONE
            yield _frame(event)
    except Exception:
        _LOG.exception("the chat stream failed")
        if not closed:
            yield _frame(
                DoneEvent(
                    type=EVENT_DONE,
                    status=STATUS_FAILED,
                    answer=_TURN_FAILED,
                    model=model,
                    input_tokens=0,
                    output_tokens=0,
                    duration_s=round(
                        perf_counter() - started, runtime().agent.duration_decimals
                    ),
                )
            )


def _frame(event: TraceEvent) -> str:
    """One SSE `data:` record; the trace events are JSON-able exactly as the agent yields them."""
    return f"data: {json.dumps(event)}\n\n"


async def _not_found(request: Request, exc: Exception) -> JSONResponse:
    """A foreign thread answers exactly like a missing one (existence non-disclosure)."""
    return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_404_NOT_FOUND)


async def _bad_gateway(request: Request, exc: Exception) -> JSONResponse:
    """An unreachable model endpoint is a generic 502; the address never reaches the client."""
    return JSONResponse(
        {"detail": _ENDPOINT_UNAVAILABLE}, status_code=status.HTTP_502_BAD_GATEWAY
    )


async def _bad_request(request: Request, exc: Exception) -> JSONResponse:
    """A refused browse says which allowlist refused it: the reason is about the request only."""
    return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST)


async def _unavailable(request: Request, exc: Exception) -> JSONResponse:
    """No note index is an operator condition, so retrieval reports itself offline (ADR 0010)."""
    return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


async def _forbidden(request: Request, exc: Exception) -> JSONResponse:
    """An RLS layer tripped: logged in full server-side, and a bare refusal to the client."""
    _LOG.error("a security layer refused a request", exc_info=exc)
    return JSONResponse({"detail": _REFUSED}, status_code=status.HTTP_403_FORBIDDEN)


def __getattr__(name: str) -> FastAPI:
    """Build the production app on first access to `app`, so importing this module is inert."""
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
