"""The REST edge (ADR 0012): thin handlers over the auth, registry and agent bricks.

Every handler is one call into a module that already owns the logic. Nothing here validates
SQL, scopes a tenant or talks to a model; it wires, authorizes and serializes.

Identity (ADR 0002 layer 1). `tenant_id`, `sub` and the scope come only from `auth.verify_token`.
The request body is never consulted for identity - `ChatRequest` has no tenant or scope field and
Pydantic drops any the client invents, so a body claiming another tenant, or all of them, is
silently ineffective. `/chat` hands the runner both halves of the verified identity, and the
runner passes them to `build_agent`, which binds the tools to the one data path that scope allows
(ADR 0002 as amended, ADR 0009 as amended).

Endpoints:

- `GET  /health`            open; liveness, the API version, the guardrail position, the titling
  window.
- `POST /login`             credentials for a token, or 401.
- `GET  /models`            the endpoint's live chat-capable models plus the default it resolves.
- `POST /chat`              one turn as an SSE stream of ADR 0012 trace events.
- `GET  /records`           one filtered, sorted page of the dataset's employee rows, all tenants.
- `GET  /records/departments` the departments the listing holds and their counts.
- `GET  /records/tenants`   the dataset's tenants and their row counts, for the tenant filter.
- `GET  /notes`             one page of the whole note corpus.
- `GET  /notes/search`      the agent's own retrieval path, run for a reader's query - in the
  scope the token grants.
- `GET  /notes/flagged`     which rows the committed poison manifest plants a payload in.
- `GET  /audit`             one newest-first page of the audit log, all tenants' entries.
- `GET  /conversations`     the caller's threads, newest first.
- `POST /conversations`     a new thread; the title is the first user message, truncated.
- `GET  /conversations/{id}` the caller's own thread row, its transcript, its turn history and
  whether a turn is running on it right now.
- `PATCH /conversations/{id}` the thread named by the reader, or by the model while it is young.
- `DELETE /conversations/{id}` the thread plus its checkpointer rows.

Conversation titles (ADR 0012 as amended). A thread is created titled with its first user
message, truncated (`POST /conversations {"title": ...}`) - the SPA has that message in hand
before it opens the stream, and a thread created without one carries the configured default.
`PATCH /conversations/{id}` then replaces it with the few-word label `titles.py` gets from the
model, and returns the updated row.

That naming runs again while the thread is young and then stops (issue #118): after each of the
first `conversations.title_turns` turns the SPA asks again and the label follows what the
conversation turned out to be about, which is how a thread opened with "Hello, how are you" ends
up named after the question that followed. Past the window nothing regenerates - a name that
churns after a thread has settled is its own defect. A PATCH carrying `{"title": ...}` is the
other half: that is the reader naming their own thread, and the registry marks the row so no
generated label ever writes over it.

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

Replayed turn history (ADR 0012 as amended, issue #90). The same response carries `turns`, the
trace each of the thread's turns produced, keyed by the turn whose question opened it: the
model's reasoning per round, every tool call with the arguments it wrote, the one outcome that
settled each call - its payload, the retry with its reason, or the refusal with the layer that
fired - and the terminal frame with the turn's status, its telemetry and the prompt-guardrail
position that produced it. So a reopened thread replays the conversation that happened, through
the same bricks a live turn renders through, instead of a tidied answer. What history keeps of a
frame is `turns.py`'s decision; this module only feeds it every frame the turn produces and
writes once the turn is over, under the turn number the transcript then reports: one turn is one
question, so counting the questions is what aligns the history with the answer above it. A turn
that broke mid-flight stores what it did produce, and a storage failure is logged and never
reaches the reader - the answer already streamed, and losing a replayable trace is not worth
failing a turn over. The read path is the strict one: a history that cannot be reconstructed
raises rather than replaying a partial turn as a whole one.

Whether anybody was still reading is not part of it (issue #143): the recording runs on the
turn's worker, so a reader who leaves mid-generation shortens the stream and never the record.

Browsing the data itself (ADR 0014 as rewritten). The Records and Notes tabs are the demo's
control group, so their listings show the WHOLE dataset - all three tenants - with `tenant_id` a
filter of the same kind as `department`. They read through `browse.py`'s allowlisted templates
down `db.execute_unscoped`, the one deliberately unscoped read in the repo: validator,
engine authorizer, limit caps, deadline, row cap and audit row all still apply, and only the
tenant scoping and its egress comparison are absent, because returning every tenant is the point.
The notes SEARCH is the opposite and stays that way: it delegates to the `search_notes` tool's
own path, in the scope the verified token grants - `rag.search_notes_scoped` for the token's
tenant, and for an all-tenant identity (ADR 0009 as amended) the partition-less
`rag.search_notes_unscoped` its own tools are bound to. A tenant reader can therefore read
another tenant's planted payload in the list and watch their own search fail to retrieve it,
which is unchanged; what the amendment fixes is an admin session searching a tenant partition
its token never named and being told, wrongly, that the corpus holds nothing.

`GET /audit` is the third listing of that surface (ADR 0014 as amended): the audit log every read
above already writes, newest first, paged the same way, all tenants' entries and no filters. An
audit row is a statement plus metadata and never a result row, so the route adds a window onto
what the server did rather than a window onto data - and the agent has no way to it, since it is
an endpoint and not one of its tools.

Nothing about identity moves. The token is still required on every one of these routes, and the
tenant it carries is what the audit row records and what the search is scoped to; it is simply not
what narrows a listing. The filter values arrive as query parameters typed by `browse.Filters`,
which is the allowlist, and each is a bound parameter - the tenant filter included, so a reader's
selection is compared as a value and never rendered as SQL. A parameter that is not one of its
fields is not read, and since issue #107 is named in the response rather than dropped in silence:
both listings pass the request's raw parameter names to `browse.ignored_params`. A stray parameter
is still a 200 with the page it could serve, and a known filter with a value the allowlist refuses
is still the same 400.

Three exception handlers turn a refused browse into an honest status without narrating the
server: `QueryRejected` is a 400 carrying its own reason (a sort outside the allowlist, a date
that is not one), `RetrievalUnavailable` a 503 saying the note index is not built, and a
`SecurityViolation` - which on this path would mean one of our own templates is broken, not a
model misbehaving - a bare 403 that is logged in full server-side and says nothing to the client.

Model selection (ADR 0005 as amended). The client never learns `OLLAMA_BASE_URL`: `/models`
proxies the endpoint's `/api/tags`, and a client-chosen `model` on `/chat` is honored only if
it is in that live list at request time - an allowlist over untrusted input.

Absent one, the default is derived from the same live list rather than asserted (issue #111).
`runtime.json`'s `agent.model` is a preference: honored when the endpoint serves it, and
otherwise replaced by the served chat id that sorts first - a rule over the set, not over
`/api/tags`'s modification-time ordering, so the same live list always resolves the same id.
`_effective_default_model` is the one place that decides it, and both readers go through it:
`/models` reports as `default` exactly the id a default turn's `done.model` will name, so the
picker's preselection and the model pill cannot drift apart. An endpoint serving no chat-capable
model at all is a 502, never a silent turn on an id nothing serves.

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

The turn outlives the stream (ADR 0012 as amended, issue #143). `_recorded` is not run by the
response: `inflight.py` runs it on a worker of its own and the `StreamingResponse` reads a
bounded window onto its frames. A reader who switches threads, reloads or signs out mid-turn
closes that window and nothing else - the turn finishes, `TurnLog` gets every frame including
the terminal one, and the thread replays complete when the reader comes back. What still bounds
the turn is what always did: the per-turn deadline and the tool-round cap inside the graph
(ADR 0011). One turn per thread is enforced with it - a second question on a thread that is
still answering is a 409, never two turns interleaved on one checkpointer thread - and
`GET /conversations/{id}` reports the same claim as `in_flight`.

Bounded generation (ADR 0011 as amended). The model client this module builds carries the two
generation bounds a turn cannot set for itself: `agent.max_output_tokens` as Ollama's
`num_predict` and `agent.context_window` as its `num_ctx`. They belong here because this is the
module that owns the client - the graph is handed a model, never an endpoint - and they are the
resource half of the per-turn budget whose other half (the wall-clock deadline and the
tool-round cap) the graph enforces.

Startup data (issue #96). Nothing but the CSV is committed, so `create_app` loads the employee
database from it before it serves anything - a fresh checkout starting with `uvicorn app:app`
otherwise dies on the first read of a file no step ever created. The check is a row count on the
existing file, so a database already there (a restart, or the image that bakes it at build time
per ADR 0013) costs nothing and the CSV is not re-read. Unlike the note index this one IS on the
critical path - every tool reads that file - so a failure to load it is raised and the process
refuses to boot rather than serving a half-working API.

Startup indexing (ADR 0010 as amended). `create_app` builds the note vector store before it
serves anything, idempotently - a store that already holds notes is left alone, so only an empty
or missing one costs embeddings. It needs the embedding endpoint, so a failure to reach it is
logged and boot continues; `search_notes` then reports retrieval as offline rather than raising.

Seams. `create_app` takes the turn runner, the model lister, the capability checker, the
titler, the registry, the data-store loader, the note indexer, the note search and the two
checkpointer accesses - transcript replay and cleanup - as arguments, defaulting to the
production wiring, plus the `db_path` every one of them reads the employee data from. Tests
pass fakes and a tmp database, and never touch Ollama or the filesystem outside tmp_path.

Paths. This module resolves none of them: `paths.py` owns the data directory every state file
sits in, and this module reads the three it wires up - the employee database (beside which
`db.py` derives its own `audit.db` and `vectors.db`), the registry's `state.db` and the
LangGraph checkpointer's `checkpoints.db`. In the deployment that directory is a mounted
volume, so a rebuilt image finds the conversations, the memory, the audit trail and the
embeddings it left behind (ADR 0013 as amended).
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
    ROLE_USER,
    STATUS_FAILED,
    DoneEvent,
    Message,
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
    AuditListing,
    BrowsePage,
    Filters,
    Flagged,
    OptionCount,
    annotate_note_hits,
    browse_audit,
    browse_notes,
    browse_records,
    filter_options,
    flagged_user_ids,
)
from conversations import ConversationRegistry, NotFound, Thread, TurnHistory
from db import DEFAULT_CSV_PATH, SecurityViolation, employee_rows, init_db
from inflight import InFlightTurns, TurnBusy
from paths import CHECKPOINT_DB_PATH, DB_PATH, STATE_DB_PATH
from rag import (
    OllamaEmbed,
    RetrievalUnavailable,
    ensure_index,
    search_notes_scoped,
    search_notes_unscoped,
)
from runtime import runtime
from security import QueryRejected
from titles import TitleModel, generate_title, should_title
from turns import TurnLog

API_VERSION = "0.1.0"
FRONTEND_ORIGIN = "http://localhost:3002"
REFRESHED_TOKEN_HEADER = "X-Refreshed-Token"
OLLAMA_ENV_VAR = "OLLAMA_BASE_URL"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

_TAGS_PATH = "/api/tags"
_SHOW_PATH = "/api/show"
_CHAT_PATH = "/api/chat"
_COMPLETION_CAPABILITY = "completion"
_THINKING_CAPABILITY = "thinking"
_INVALID_CREDENTIALS = "invalid credentials"
_INVALID_TOKEN = "invalid or expired token"
_UNKNOWN_MODEL = "unknown model"
_EMPTY_TITLE = "a title cannot be blank"
_NO_CHAT_MODEL = "the model endpoint serves no chat-capable model"
_ENDPOINT_UNAVAILABLE = "the model endpoint is unavailable"
_TURN_FAILED = (
    "The turn ended in a server-side failure before an answer was composed. Nothing is left "
    "running, the failure is in the server log, and the conversation is unaffected - ask again."
)
_REFUSED = "the request was refused by a security layer"
_INDEX_FAILED = "the note index could not be built at startup; search_notes will say it is offline"
_INDEX_READY = "the note index holds %d notes"
_DATA_READY = "the employee database holds %d rows"
_DATA_LOADED = "loaded %d employee rows from the committed CSV"

_LOG = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)


class ModelEndpointError(Exception):
    """Raised when the Ollama endpoint cannot be reached; the API answers 502 without detail."""


class ChatRunner(Protocol):
    """Runs one turn for an identity and yields the ADR 0012 trace events in order."""

    def __call__(
        self,
        *,
        tenant_id: str,
        all_tenants: bool,
        thread_id: str,
        message: str,
        model: str,
    ) -> Iterator[TraceEvent]:
        """Stream the turn's trace events; a raise is a transport failure, not an answer."""
        ...


class ModelLister(Protocol):
    """Returns the model ids the endpoint currently serves."""

    def __call__(self) -> list[str]:
        """List the live model ids, raising ModelEndpointError when the endpoint is unusable."""
        ...


class NoteSearch(Protocol):
    """Runs one notes retrieval for an identity: the agent's own path, called by the Notes tab."""

    def __call__(
        self, *, query: str, tenant_id: str, all_tenants: bool, k: int
    ) -> list[dict[str, object]]:
        """The hits for query in the caller's scope, or raise RetrievalUnavailable with no index."""
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


class RetitleRequest(BaseModel):
    """A PATCH body: the name a reader typed, or nothing - which asks the model for one."""

    title: str | None = None


@dataclass(frozen=True)
class NoteHits:
    """What `GET /notes/search` serves: the query, the hits asked for, and the scored matches."""

    query: str
    k: int
    hits: list[dict[str, object]]


@dataclass(frozen=True)
class Conversation:
    """One thread as `GET /conversations/{id}` serves it: the row, the transcript, the history.

    `in_flight` is whether a turn is running on it at this moment, so a thread reopened mid-turn
    can say it is still answering rather than replaying a turn whose history is not written yet.
    """

    thread_id: str
    title: str
    created: str
    messages: list[Message]
    turns: list[TurnHistory]
    in_flight: bool


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
    """The production runner: ChatOllama plus the identity's graph over the SQLite checkpointer."""

    def run(
        *,
        tenant_id: str,
        all_tenants: bool,
        thread_id: str,
        message: str,
        model: str,
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
                all_tenants=all_tenants,
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

    Thinking is switched off for this call (issue #118). Ollama turns it on by default for a model
    that supports it, and measured on the configured model a titling call then spent 245-918
    generation tokens reasoning about a six-word label: 7-17 seconds against a timeout, and every
    read timeout observed in the owner's real threads. A label needs no reasoning, so the request
    says so, and `think: false` is rejected by no model - the endpoint refuses only thinking asked
    of a model that cannot (ollama/ollama `server/routes.go`).
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
                    "think": False,
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


def build_data_store(db_path: Path) -> None:
    """The production loader: load the committed CSV unless the database holds rows (issue #96)."""
    held = employee_rows(db_path)
    if held:
        _LOG.info(_DATA_READY, held)
        return
    init_db(DEFAULT_CSV_PATH, db_path)
    _LOG.info(_DATA_LOADED, employee_rows(db_path))


def build_note_index(base_url: str, db_path: Path) -> None:
    """The production indexer: embed the notes unless the store already holds them (ADR 0010)."""
    _LOG.info(_INDEX_READY, ensure_index(db_path, OllamaEmbed(base_url)))


def ollama_note_search(base_url: str, db_path: Path) -> NoteSearch:
    """The production notes search: the agent's own retrieval path, in the caller's scope.

    Which of the two retrievals runs is the same decision `agent._build_tools` makes, made here
    per call because this seam is wired once for the process rather than once per identity. The
    scope still arrives only from the verified token - the handler reads it off `Identity` and
    nothing else can supply it.
    """

    def search(
        *, query: str, tenant_id: str, all_tenants: bool, k: int
    ) -> list[dict[str, object]]:
        """Return the nearest notes in the caller's scope, scored, exactly as the tool sees them."""
        embedder = OllamaEmbed(base_url)
        if all_tenants:
            return search_notes_unscoped(db_path, embedder, query, k)
        return search_notes_scoped(db_path, embedder, query, tenant_id, k)

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
    data_store: Callable[[], None] | None = None,
    note_index: Callable[[], None] | None = None,
    note_search: NoteSearch | None = None,
    db_path: Path = DB_PATH,
) -> FastAPI:
    """Build the API, refusing to start without a usable signing secret (ADR 0009).

    The employee database is loaded here first (issue #96), and a failure to load it IS fatal:
    every tool reads that file, so a process that cannot open it has nothing to serve.

    The note index is built next (ADR 0010 as amended), and its failure is not fatal: an
    unreachable embedding endpoint must not stop the API from booting, so it is logged and
    `search_notes` reports retrieval as offline for as long as it is.

    `data_store` loads that database and `note_search` answers the Notes tab's retrieval; like
    every other seam they default to a production builder reading the one `db_path` this factory
    was given, so a test points the whole factory at a tmp database by passing that one argument.
    """
    jwt_secret()
    base_url = os.environ.get(OLLAMA_ENV_VAR, DEFAULT_OLLAMA_BASE_URL)
    load_data = data_store or (lambda: build_data_store(db_path))
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
    running = InFlightTurns()
    load_data()
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
    def health() -> dict[str, str | bool | int]:
        """Liveness, the prompt-guardrail position and the titling window the SPA plays along with.

        Both knobs are server-side settings the SPA has to reflect rather than decide: the
        guardrail position so it can state the mode before a turn, and `title_turns` so it stops
        asking for a generated title once the thread is past the window (issue #118). The window
        is enforced here either way - this only spares the SPA a request whose answer is known.
        """
        return {
            "status": "ok",
            "version": API_VERSION,
            "prompt_guardrails": runtime().agent.prompt_guardrails,
            "title_turns": runtime().conversations.title_turns,
        }

    @app.post("/login")
    def login(body: LoginRequest) -> dict[str, str]:
        """Exchange demo credentials for a tenant-claim token, or 401 without saying which half."""
        identity = verify_password(body.username, body.password)
        if identity is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, _INVALID_CREDENTIALS)
        return {"token": create_token(identity)}

    @app.get("/models", dependencies=[Depends(_identity)])
    def models() -> dict[str, object]:
        """The endpoint's live chat-capable models plus the default a turn would run on.

        Both fields come from one reading of the live list (ADR 0005 as amended), so `default` is
        the id `/chat` resolves for a request that names none - never a configured id the endpoint
        does not serve.
        """
        served = list_models()
        return {"models": served, "default": _effective_default_model(served)}

    @app.post("/chat")
    def chat(
        body: ChatRequest,
        identity: Annotated[Identity, Depends(_identity)],
        response: Response,
    ) -> StreamingResponse:
        """Stream one turn as SSE; the thread must belong to the token's identity.

        The turn does not run on this response (ADR 0012 as amended, issue #143). It is started on
        its own worker and this stream is a window onto it, so a reader who switches threads,
        reloads or signs out mid-generation interrupts nothing: the turn finishes, its history is
        written and the audit trail is complete, whether or not anybody is still reading.

        The turn's history is kept for replay on the way past: the log is offered every frame the
        turn produces, in the worker, and is written once the turn is over - so a storage failure
        cannot change a single frame of it and a departed reader cannot shorten it.

        One turn per thread at a time. A second question on a thread whose turn is still running is
        refused with 409 rather than interleaved onto the same checkpointer thread.
        """
        threads.get_thread(identity, body.thread_id)
        model = _resolve_model(body.model, list_models)
        events = run_chat(
            tenant_id=identity.tenant_id,
            all_tenants=identity.all_tenants,
            thread_id=body.thread_id,
            message=body.message,
            model=model,
        )
        history = TurnLog(
            lambda kept, cut: _keep_turn(threads, replay, identity, body.thread_id, kept, cut)
        )
        try:
            window = running.start(body.thread_id, _recorded(events, model, history))
        except TurnBusy as busy:
            raise HTTPException(status.HTTP_409_CONFLICT, str(busy)) from busy
        stream = StreamingResponse(_sse(window), media_type="text/event-stream")
        refreshed = response.headers.get(REFRESHED_TOKEN_HEADER)
        if refreshed is not None:
            stream.headers[REFRESHED_TOKEN_HEADER] = refreshed
        return stream

    @app.get("/records")
    def records(
        request: Request,
        identity: Annotated[Identity, Depends(_identity)],
        filters: Annotated[Filters, Depends()],
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIRECTION,
        page: int = 1,
        page_size: int | None = None,
    ) -> BrowsePage:
        """One page of the DATASET's employee rows, every tenant: the Records tab (ADR 0014).

        This listing is the control group, not a tenant view: a reader sees all 1000 rows and can
        narrow to one tenant with `tenant_id`, a filter of the same kind as `department`, so the
        agent's own 450 can be compared against what exists. The token is still required and its
        tenant is still what the audit row records; it is not what narrows the page.

        The raw parameter names travel with the request so `browse.py` can report the ones it
        does not read (issue #107) - the names only, never their values. A parameter this
        handler has no field for is still not read; what the page adds is saying so, rather than
        leaving a reader to guess whether it was refused or honored.
        """
        return browse_records(
            reader_tenant=identity.tenant_id,
            filters=filters,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
            requested=request.query_params.keys(),
            db_path=db_path,
        )

    @app.get("/records/departments")
    def record_departments(
        identity: Annotated[Identity, Depends(_identity)],
        tenant_id: str | None = None,
    ) -> list[OptionCount]:
        """The departments the listing holds and their counts, so the filter offers real values.

        It takes the tenant filter the listing took, so the count beside an option is a count of
        the rows the reader is actually looking at rather than of a set they did not ask for.
        """
        return filter_options(
            "department", reader_tenant=identity.tenant_id, tenant_id=tenant_id, db_path=db_path
        )

    @app.get("/records/tenants")
    def record_tenants(identity: Annotated[Identity, Depends(_identity)]) -> list[OptionCount]:
        """The dataset's tenants and their row counts: the tenant filter's options (ADR 0014).

        Never narrowed by the tenant filter itself - the picker's whole job is to state that the
        dataset holds 450, 350 and 200 rows, which is the control group in one line.
        """
        return filter_options("tenant_id", reader_tenant=identity.tenant_id, db_path=db_path)

    @app.get("/notes")
    def notes(
        request: Request,
        identity: Annotated[Identity, Depends(_identity)],
        filters: Annotated[Filters, Depends()],
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIRECTION,
        page: int = 1,
        page_size: int | None = None,
    ) -> BrowsePage:
        """One page of the whole note corpus - the text the agent retrieves over (ADR 0014).

        Same listing contract as `/records`, filters and ignored-parameter report included, and
        unscoped for the same reason: a reader has to be able to see another tenant's planted
        payload here before finding that `/notes/search` cannot retrieve it for them.
        """
        return browse_notes(
            reader_tenant=identity.tenant_id,
            filters=filters,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
            requested=request.query_params.keys(),
            db_path=db_path,
        )

    @app.get("/notes/search")
    def note_search_results(
        identity: Annotated[Identity, Depends(_identity)],
        q: str,
        k: int | None = None,
    ) -> NoteHits:
        """The agent's own retrieval path, run for a reader's query and scored (ADR 0010).

        Not a second search: this is the very call the `search_notes` tool makes for this
        identity, so the hits and their distances are what the model would have been handed for
        that query. Each hit is then annotated with its row's department and score, read back
        through the same scope, so a reader can check a retrieval claim against the data
        (ADR 0014).

        Both halves follow the token and nothing else. For a tenant identity that is the scoped
        retrieval and the scoped lookup, unchanged: the payload a reader can read in the corpus
        listing beside it is one their own search will never return, which is the asymmetry the
        tab exists to show. For an all-tenant identity it is the partition-less retrieval its own
        agent uses and the same unscoped read the listings take - the tab shows what that session
        can actually reach rather than searching a tenant its token never named.
        """
        wanted = _hit_count(k)
        return NoteHits(
            query=q,
            k=wanted,
            hits=annotate_note_hits(
                identity.tenant_id,
                search_notes(
                    query=q,
                    tenant_id=identity.tenant_id,
                    all_tenants=identity.all_tenants,
                    k=wanted,
                ),
                all_tenants=identity.all_tenants,
                db_path=db_path,
            ),
        )

    @app.get("/notes/flagged", dependencies=[Depends(_identity)])
    def flagged_notes() -> Flagged:
        """Which rows the committed poison manifest plants a payload in, across every tenant.

        Repo metadata the README already points at, and now every tenant's, because the corpus
        listing shows every tenant's notes. A token is still required to read it.
        """
        return flagged_user_ids()

    @app.get("/audit", dependencies=[Depends(_identity)])
    def audit(page: int = 1, page_size: int | None = None) -> AuditListing:
        """One newest-first page of the audit log: what the data path ran (ADRs 0002, 0014).

        Every call through the executor persists a row - the generated SQL, the verdict, the
        statement that executed, the row count, the error kind - and until now nothing served it.
        This is that trail, all tenants' entries, for the same reason Records lists all 1000 rows:
        the tabs are the auditor surface, and a trail filtered to the caller could not show that
        another tenant's query was scoped to that tenant.

        Serving it exposes nothing new. An audit row holds statements and metadata and never a
        result row, so there is no tenant data here that Records does not show outright; a token
        is required exactly as on every other listing; and the agent cannot reach this - it is an
        endpoint, not a tool, and no tool of its set names it (ADR 0002, layer 1).

        A token is what the route needs, not an identity: the log records who ran what, so
        narrowing it by the caller would delete the comparison the page exists for.
        """
        return browse_audit(page=page, page_size=page_size, db_path=db_path)

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
        """The caller's own thread, its transcript and its turn history; a foreign id is a 404.

        The registry answers first, so an id the caller does not own never reaches the
        checkpointer and never reaches the history store. `messages` replays what was said - the
        questions asked and the answers given - and `turns` the trace each turn produced: the
        reasoning per model round, every tool call with the arguments the model wrote, the outcome
        that settled it, and the terminal frame with the turn's status and telemetry (ADR 0012 as
        amended). A thread never chatted in replays as two empty lists.

        `in_flight` says whether a turn is running on this thread right now (issue #143). A
        backgrounded turn stores its history only when it ends, so without it a thread reopened
        mid-turn would replay its newest question as a turn whose trace was never kept - a running
        turn reading as a lost one. It is read off the same claim that refuses a second turn, so
        the flag and the 409 can never disagree.
        """
        thread = threads.get_thread(identity, thread_id)
        return Conversation(
            **asdict(thread),
            messages=replay(thread_id),
            turns=threads.thread_turns(identity, thread_id),
            in_flight=running.running(thread_id),
        )

    @app.patch("/conversations/{thread_id}")
    def retitle_conversation(
        thread_id: str,
        identity: Annotated[Identity, Depends(_identity)],
        body: RetitleRequest | None = None,
    ) -> Thread:
        """Name the caller's own thread - as they typed it, or as the model reads it.

        The order is the contract. The registry answers first, so a foreign or missing id is the
        same 404 as everywhere else and no transcript is read and no model called for a thread
        the caller may not see.

        A body carrying a title is the reader naming their thread, and it is stored as theirs for
        good: nothing generated writes over it afterwards (issue #118). A body with no title asks
        for a generated one, which happens only while the thread is young - `titles.should_title`
        is that window, and past it the thread keeps the name it settled on rather than churning.
        Inside the window the titler gets the exchanges so far and returns a title in every case
        (ADR 0012 as amended): the model's when it gives a usable one, the standing title when the
        call fails or answers with junk, the first question when the thread is still unnamed. So
        this endpoint has no failure mode of its own - it either names the thread better or leaves
        it as good as it was.
        """
        thread = threads.get_thread(identity, thread_id)
        if body is not None and body.title is not None:
            if not body.title.strip():
                raise HTTPException(status.HTTP_400_BAD_REQUEST, _EMPTY_TITLE)
            return threads.rename_thread(identity, thread_id, body.title)
        messages = replay(thread_id)
        if not should_title(messages):
            return thread
        title = generate_title(messages, ask_title, current=thread.title)
        return threads.retitle_thread(identity, thread_id, title)

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


def _effective_default_model(served: list[str]) -> str:
    """The model a default turn runs on, derived from what the endpoint actually serves.

    `runtime.json`'s `agent.model` stays the preference and is honored whenever the live
    chat-capable list carries it. When it does not, the default is the served chat id that sorts
    first by Python's string order - a rule over the set rather than over the endpoint's own
    ordering, which `/api/tags` returns by modification time and therefore reshuffles as models
    are pulled or run. The same live set thus always yields the same id, so the picker's
    preselection and the turn's `done.model` cannot disagree between turns.

    An endpoint serving no chat-capable model at all is an upstream failure, not a default: it
    answers 502 like every other model-endpoint condition rather than inventing an id to run on.
    """
    if not served:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, _NO_CHAT_MODEL)
    configured = runtime().agent.model
    return configured if configured in served else min(served)


def _resolve_model(requested: str | None, list_models: ModelLister) -> str:
    """Honor a client model id only if the endpoint serves it now; else the effective default.

    One live list serves both branches, so the default is validated against exactly the list the
    picker was offered without the turn making a second call to the endpoint.
    """
    served = list_models()
    if requested is None:
        return _effective_default_model(served)
    if requested not in served:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _UNKNOWN_MODEL)
    return requested


def _keep_turn(
    threads: ConversationRegistry,
    transcript: Callable[[str], list[Message]],
    identity: Identity,
    thread_id: str,
    events: list[dict[str, object]],
    cut: int,
) -> None:
    """Store one turn's history under the turn its question opened (ADR 0012 as amended).

    The turn number is the count of questions the thread now holds: `/chat` appends exactly one
    of them per turn, so counting them in the transcript gives the same ordinal the SPA arrives
    at when it groups the replayed exchanges into turns. It is read after the turn, when the
    checkpoint that made it the newest question is already written.
    """
    turn = sum(1 for message in transcript(thread_id) if message.role == ROLE_USER)
    threads.record_turn(identity, thread_id, TurnHistory(turn=turn, events=events, cut=cut))


def _recorded(events: Iterator[TraceEvent], model: str, history: TurnLog) -> Iterator[TraceEvent]:
    """The turn as it is recorded and run: every frame kept, and never an end without `done`.

    A run that breaks before `done` - an unreachable model endpoint, a recursion limit, anything
    the agent did not turn into a retry - would otherwise stop mid-flight and leave the turn
    stuck at "streaming". It closes here instead with the terminal `done` frame ADR 0012 defines,
    status `failed`, so the reader and the history both learn how the turn ended. The reason
    is deliberately generic; the exception is logged, where its detail belongs.

    The terminal frame carries the telemetry the turn managed to produce: the seconds it ran
    before it broke, and no token counts, because a run that never reached `respond` never got
    a usage report to pass on. It reports the turn as ungrounded for the same reason: a run that
    never answered has no answer a tool result could stand behind, and it claims no trimmed history
    because only the graph knows what it managed to send. The prompt-guardrail position is read off
    the knob rather than off the broken run, since a run that never built a prompt never chose a
    position of its own (ADR 0011 as amended).

    History is written from here rather than from around the runner (issue #90), so every frame the
    turn produced - this terminal one included - is a frame the turn's stored history holds, and
    the write happens once the turn is over whether it ended or raised. Since issue #143 this runs
    on the turn's own worker rather than on the response (`inflight.py`), so what is recorded is
    what the turn produced and not what a reader stayed to watch.
    """
    closed = False
    started = perf_counter()
    try:
        for event in events:
            closed = event["type"] == EVENT_DONE
            history.add(event)
            yield event
    except Exception:
        _LOG.exception("the chat turn failed")
        if not closed:
            terminal = DoneEvent(
                type=EVENT_DONE,
                status=STATUS_FAILED,
                answer=_TURN_FAILED,
                grounded=False,
                history_trimmed=False,
                model=model,
                prompt_guardrails=runtime().agent.prompt_guardrails,
                input_tokens=0,
                output_tokens=0,
                duration_s=round(perf_counter() - started, runtime().agent.duration_decimals),
            )
            history.add(terminal)
            yield terminal
    finally:
        history.close()


def _sse(events: Iterator[TraceEvent]) -> Iterator[str]:
    """Frame the turn's events as SSE `data:` records; the window they come through is closed here.

    Nothing else happens on the response any more (ADR 0012 as amended, issue #143): the turn runs
    on its own worker, and a reader who leaves closes this generator, which stops the forwarding
    and nothing else.
    """
    for event in events:
        yield _frame(event)


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
