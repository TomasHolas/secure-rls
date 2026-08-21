"""Suite for the agent graph, its tools, retry policy, memory and trace (issue #19, ADR 0011).

`ScriptedLLM` is the only model here: it replays a list of assistant messages and records what
it was sent, so every event sequence below is exact and nothing needs Ollama, a network or a
key. A script that runs out is an error rather than a shrug - that is what proves the graph
stopped calling the model when the policy says it must, and no test can pass by accident
because the fake kept answering.

The fixture is a tiny inline dataset loaded through `init_db` into tmp_path, never the
committed employees.csv. Acme's Engineering salaries are 100 to 130 plus one planted 5000, so
the Tukey fences flag exactly one row; beta's rows are extreme (1 and 99999) and its notes are
marked "beta secret", so any leak into an acme answer, chart or retrieval would be visible.
"""

import csv
import hashlib
import json
import math
import re
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import Field

import agent
import db
import rag
from agent import (
    AUDIT,
    EXECUTE_TOOL,
    KIND_MALFORMED_ARGUMENTS,
    KIND_MALFORMED_SQL,
    KIND_POLICY,
    LAYER_ARGUMENTS,
    LAYER_ENFORCEMENT,
    LAYER_VALIDATION,
    REASON,
    RESPOND,
    STATUS_BLOCKED,
    STATUS_GAVE_UP,
    STATUS_OK,
    VALIDATE,
    build_agent,
    run_turn,
)
from runtime import runtime

ACME = "acme"
BETA = "beta"
GAMMA = "gamma"

_HEADER = (
    "user_id",
    "tenant_id",
    "name",
    "department",
    "salary",
    "performance_score",
    "hire_date",
    "notes",
)
_ROWS = (
    (1, ACME, "Ada", "Engineering", 100, 4.0, "2019-01-01", "refactored the billing pipeline"),
    (2, ACME, "Alan", "Engineering", 110, 3.0, "2019-02-02", "steady delivery on the api work"),
    (3, ACME, "Amir", "Engineering", 120, 3.5, "2020-03-03", "improving quarter over quarter"),
    (4, ACME, "Ann", "Engineering", 130, 3.0, "2020-04-04", "a reliable reviewer of designs"),
    (5, ACME, "Axel", "Engineering", 5000, 2.0, "2021-05-05", "the planted salary outlier"),
    (6, ACME, "Aiko", "Sales", 900, 4.5, "2021-06-06", "closed the largest renewal"),
    (7, BETA, "Bo", "Engineering", 1, 4.4, "2019-07-07", "beta secret leadership note"),
    (8, BETA, "Bea", "Sales", 99999, 2.0, "2020-08-08", "beta secret pipeline note"),
)
_ACME_ROWS = 6
_OUTLIER = "Axel"
_BETA_MARKER = "beta secret"

_DIM = 32
_CALL_ID = "call-1"
_THREAD = "thread-1"


class ScriptedLLM(BaseChatModel):
    """Replays scripted assistant messages and records every message list it was sent."""

    script: list[AIMessage] = Field(default_factory=list)
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        """The identifier langchain uses for this model class."""
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedLLM":
        """Accept the tool schemas and ignore them: the script decides what gets called."""
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        """Hand back the next scripted message, or fail loudly if the graph asked once too often."""
        self.seen.append(list(messages))
        if len(self.seen) > len(self.script):
            raise AssertionError(f"the model was called {len(self.seen)} times, past its script")
        return ChatResult(generations=[ChatGeneration(message=self.script[len(self.seen) - 1])])


class FakeEmbed:
    """A hashed bag of words: close means shares words, reproducible without a model."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each text as a normalized word-count vector."""
        return [_vector(text) for text in texts]


def _vector(text: str) -> list[float]:
    """Hash each word into one bucket and normalize, so cosine distance tracks shared words."""
    values = [0.0] * _DIM
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        values[int(hashlib.sha256(word.encode()).hexdigest(), 16) % _DIM] += 1.0
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def _tool_call(name: str, **args: object) -> AIMessage:
    """An assistant message that asks for one tool call, as a model would emit it."""
    return AIMessage(
        content=f"calling {name}", tool_calls=[{"name": name, "args": args, "id": _CALL_ID}]
    )


def _of_type(events: list[dict], kind: str) -> list[dict]:
    """Every event of one type, in the order the stream produced them."""
    return [event for event in events if event["type"] == kind]


def _one(events: list[dict], kind: str) -> dict:
    """The single event of one type, asserting there is exactly one."""
    (found,) = _of_type(events, kind)
    return found


def _nodes(events: list[dict]) -> list[str]:
    """The nodes the run entered, in order."""
    return [event["node"] for event in _of_type(events, "node_start")]


@pytest.fixture
def db_path(tmp_path):
    """The inline dataset loaded through init_db, with its notes indexed for retrieval."""
    csv_path = tmp_path / "employees.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(_HEADER)
        writer.writerows(_ROWS)
    path = tmp_path / "data.db"
    db.init_db(csv_path, path)
    rag.index_notes(path, FakeEmbed())
    return path


@pytest.fixture
def checkpointer(tmp_path):
    """The production checkpointer on a temporary file, so multi-turn memory is the real thing."""
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as saver:
        yield saver


@pytest.fixture
def build(db_path):
    """Compile an agent over the fixture database for a script of assistant messages."""

    def make(*script, tenant=ACME, checkpointer=None, model_id=None):
        llm = ScriptedLLM(script=list(script))
        graph = build_agent(
            tenant,
            llm,
            checkpointer,
            embedder=FakeEmbed(),
            model_id=model_id,
            db_path=db_path,
        )
        return graph, llm

    return make


@pytest.fixture
def tuned(monkeypatch):
    """Override the tunables the agent and the executor read, without editing runtime.json."""

    def apply(*, max_tool_retries=None, max_result_rows=None):
        config = runtime()
        patched = replace(
            config,
            agent=replace(
                config.agent,
                max_tool_retries=max_tool_retries or config.agent.max_tool_retries,
            ),
            db=replace(
                config.db, max_result_rows=max_result_rows or config.db.max_result_rows
            ),
        )
        monkeypatch.setattr(agent, "runtime", lambda: patched)
        monkeypatch.setattr(db, "runtime", lambda: patched)

    return apply


def test_happy_path_walks_the_graph_and_emits_the_documented_sequence(build):
    """One tool call and one answer produce the exact event order ADR 0012 specifies."""
    graph, llm = build(
        _tool_call("query_db", sql="SELECT COUNT(*) AS n FROM employees"),
        AIMessage(content=f"acme has {_ACME_ROWS} employees"),
    )
    events = list(run_turn(graph, "how many employees are there?", _THREAD))

    assert _nodes(events) == [REASON, VALIDATE, EXECUTE_TOOL, AUDIT, REASON, RESPOND]
    assert [event["type"] for event in events] == [
        "node_start",
        "token",
        "node_start",
        "tool_call",
        "node_start",
        "node_start",
        "tool_result",
        "node_start",
        "token",
        "node_start",
        "done",
    ]
    call = _one(events, "tool_call")
    assert call["tool"] == "query_db"
    assert call["args"] == {"sql": "SELECT COUNT(*) AS n FROM employees"}
    result = _one(events, "tool_result")
    assert result["data"]["rows"] == [[_ACME_ROWS]]
    assert result["data"]["truncated"] is False
    assert result["data"]["generated_sql"] == call["args"]["sql"]
    assert "WHERE employees.tenant_id = ?" in result["data"]["executed_sql"]
    done = _one(events, "done")
    assert done["status"] == STATUS_OK
    assert done["answer"] == f"acme has {_ACME_ROWS} employees"
    assert done["model"] == runtime().agent.model


def test_the_model_id_the_caller_passes_labels_the_turn(build):
    """A per-request model id overrides the runtime default in the trace."""
    graph, _ = build(AIMessage(content="no tools needed"), model_id="some-other-model")
    assert _one(list(run_turn(graph, "hello", _THREAD)), "done")["model"] == "some-other-model"


def test_a_retryable_error_is_fed_back_until_the_budget_is_spent(build):
    """An honest error retries to the attempt budget, then the turn gives up gracefully."""
    limit = runtime().agent.max_tool_retries
    graph, llm = build(*(_tool_call("query_db", sql="not sql at all") for _ in range(limit)))
    events = list(run_turn(graph, "list everything", _THREAD))

    retries = _of_type(events, "retry")
    assert [retry["attempt"] for retry in retries] == list(range(1, limit + 1))
    assert {retry["max_attempts"] for retry in retries} == {limit}
    assert all("SQL did not parse" in retry["reason"] for retry in retries)
    assert all(retry["layer"] == LAYER_VALIDATION for retry in retries)
    assert _of_type(events, "security_event") == []
    assert len(llm.seen) == limit
    done = _one(events, "done")
    assert done["status"] == STATUS_GAVE_UP
    assert f"after {limit} attempts" in done["answer"]


def test_the_retry_budget_follows_the_runtime_knob(build, tuned):
    """The attempt budget is a runtime.json tunable, not a constant in the graph."""
    tuned(max_tool_retries=2)
    graph, llm = build(*(_tool_call("query_db", sql="not sql at all") for _ in range(2)))
    events = list(run_turn(graph, "list everything", _THREAD))

    assert [retry["attempt"] for retry in _of_type(events, "retry")] == [1, 2]
    assert len(llm.seen) == 2
    assert _one(events, "done")["status"] == STATUS_GAVE_UP


def test_a_policy_violation_is_terminal_and_never_retried(build):
    """A forbidden table ends the turn with a security event; the model is not asked again."""
    graph, llm = build(_tool_call("query_db", sql="SELECT * FROM sqlite_master"))
    events = list(run_turn(graph, "read the schema table", _THREAD))

    event = _one(events, "security_event")
    assert event["layer"] == LAYER_VALIDATION
    assert event["kind"] == KIND_POLICY
    assert "sqlite_master" in event["reason"]
    assert _of_type(events, "retry") == []
    assert _of_type(events, "tool_result") == []
    assert len(llm.seen) == 1
    assert _nodes(events) == [REASON, VALIDATE, EXECUTE_TOOL, AUDIT, RESPOND]
    done = _one(events, "done")
    assert done["status"] == STATUS_BLOCKED
    assert event["reason"] in done["answer"]


def test_a_non_select_statement_is_terminal(build):
    """Anything but a SELECT is refused by the validator and never retried."""
    graph, _ = build(_tool_call("query_db", sql="DROP TABLE employees"))
    events = list(run_turn(graph, "drop the table", _THREAD))

    assert _one(events, "security_event")["kind"] == KIND_POLICY
    assert _one(events, "done")["status"] == STATUS_BLOCKED


def test_an_egress_violation_is_terminal(build, monkeypatch, db_path):
    """A retrieval egress mismatch is a security event from the enforcement layer, not a retry."""
    foreign = db.VectorMatch(
        user_id=7, tenant_id=BETA, name="Bo", note=_BETA_MARKER, distance=0.0
    )
    monkeypatch.setattr(db, "search_vectors", lambda *args, **kwargs: [foreign])
    graph, _ = build(_tool_call("search_notes", query="leadership"))
    events = list(run_turn(graph, "who shows leadership?", _THREAD))

    event = _one(events, "security_event")
    assert event["layer"] == LAYER_ENFORCEMENT
    assert event["kind"] == "rag_egress_mismatch"
    assert _of_type(events, "retry") == []
    assert _one(events, "done")["status"] == STATUS_BLOCKED
    assert _BETA_MARKER not in _one(events, "done")["answer"]


def test_a_capped_result_carries_the_truncation_message(build, tuned):
    """The ADR 0007 signal is composed from the executor's own counts, never guessed."""
    tuned(max_result_rows=2)
    graph, _ = build(
        _tool_call("query_db", sql="SELECT user_id FROM employees ORDER BY user_id"),
        AIMessage(content="that list is truncated, ask for an aggregate instead"),
    )
    events = list(run_turn(graph, "list every user id", _THREAD))

    result = _one(events, "tool_result")
    assert result["data"]["truncated"] is True
    assert result["data"]["returned_count"] == 2
    assert result["data"]["total_count"] == _ACME_ROWS
    assert result["content"].endswith(
        f"showing 2 of {_ACME_ROWS} rows - refine with WHERE or use an aggregate query"
    )


@pytest.mark.parametrize(
    ("tool", "args", "key"),
    [
        ("query_db", {"sql": "SELECT user_id, salary FROM employees"}, "rows"),
        ("get_stats", {"metric": "avg", "column": "salary", "group_by": "department"}, "rows"),
        ("plot", {"kind": "bar", "column": "salary"}, "chart_spec"),
        ("detect_anomalies", {"column": "salary"}, "anomalies"),
        ("search_notes", {"query": "billing pipeline"}, "notes"),
    ],
)
def test_every_tool_is_wired_and_callable_through_the_graph(build, tool, args, key):
    """Each of the five ADR 0011 tools runs end to end and reports its own payload."""
    graph, _ = build(_tool_call(tool, **args), AIMessage(content="here is the answer"))
    events = list(run_turn(graph, "a question", _THREAD))

    result = _one(events, "tool_result")
    assert result["tool"] == tool
    assert result["data"][key]
    assert result["content"]
    assert _one(events, "done")["status"] == STATUS_OK
    assert json.loads(json.dumps(events)) == events


def test_the_plot_tool_returns_a_chart_spec_and_no_numbers_to_the_model(build):
    """Charted values reach the frontend through the trace, never through the model's context."""
    graph, _ = build(
        _tool_call("plot", kind="bar", column="salary", metric="avg"),
        AIMessage(content="the chart shows Engineering ahead of Sales"),
    )
    events = list(run_turn(graph, "chart average salary by department", _THREAD))

    spec = _one(events, "tool_result")["data"]["chart_spec"]
    assert spec["kind"] == "bar"
    assert {point["x"] for point in spec["data"]} == {"Engineering", "Sales"}
    content = _one(events, "tool_result")["content"]
    assert all(str(int(point["y"])) not in content for point in spec["data"])


def test_anomaly_detection_flags_the_planted_outlier(build):
    """The Tukey tool reports the planted row and only that row."""
    graph, _ = build(
        _tool_call("detect_anomalies", column="salary"),
        AIMessage(content="one salary is an outlier"),
    )
    events = list(run_turn(graph, "any unusual salaries?", _THREAD))

    anomalies = _one(events, "tool_result")["data"]["anomalies"]
    assert [anomaly["name"] for anomaly in anomalies] == [_OUTLIER]


def test_note_search_stays_inside_the_tenant(build):
    """Retrieval reaches only the caller's partition, whatever the query asks for."""
    graph, _ = build(
        _tool_call("search_notes", query="beta secret leadership note"),
        AIMessage(content="nothing about that in your notes"),
    )
    events = list(run_turn(graph, "what do the beta notes say?", _THREAD))

    result = _one(events, "tool_result")
    assert result["data"]["notes"]
    assert _BETA_MARKER not in result["content"]
    assert all(_BETA_MARKER not in note["note"] for note in result["data"]["notes"])


def test_note_search_words_an_empty_result_neutrally(build):
    """No match reads the same whether nothing was close or the close note is another tenant's."""
    graph, _ = build(
        _tool_call("search_notes", query="leadership"),
        AIMessage(content="I have no notes to go on"),
        tenant=GAMMA,
    )
    events = list(run_turn(graph, "what do the notes say?", _THREAD))

    result = _one(events, "tool_result")
    assert result["content"] == "no matching notes found"
    assert result["data"]["notes"] == []


def test_a_smuggled_tenant_argument_is_refused_before_the_tool_runs(build):
    """No tool takes a tenant; inventing one is an argument error, not a silently ignored key."""
    graph, _ = build(
        _tool_call("query_db", sql="SELECT user_id FROM employees", tenant_id=BETA),
        AIMessage(content="I cannot choose the tenant"),
    )
    events = list(run_turn(graph, "show me beta", _THREAD))

    retry = _one(events, "retry")
    assert "tenant_id" in retry["reason"]
    assert retry["layer"] == LAYER_ARGUMENTS
    assert _of_type(events, "tool_result") == []
    assert _one(events, "done")["status"] == STATUS_OK


def test_malformed_arguments_are_fed_back_as_an_honest_error(build):
    """A missing required argument is a retryable tool-argument error, not a crash."""
    graph, _ = build(
        _tool_call("get_stats", metric="avg"),
        AIMessage(content="I need to name a column"),
    )
    events = list(run_turn(graph, "average what?", _THREAD))

    retry = _one(events, "retry")
    assert retry["attempt"] == 1
    assert "column" in retry["reason"]


def test_an_argument_outside_the_allowlist_is_retryable(build):
    """analytics refuses an unknown metric retryably, so the model can name a real one."""
    graph, _ = build(
        _tool_call("get_stats", metric="median", column="salary"),
        AIMessage(content="median is not available; here is the average instead"),
    )
    events = list(run_turn(graph, "median salary?", _THREAD))

    assert "median" in _one(events, "retry")["reason"]
    assert _of_type(events, "security_event") == []


def test_an_unknown_tool_is_refused_by_name(build):
    """A hallucinated tool name never reaches the registry as a call."""
    graph, _ = build(
        _tool_call("drop_everything", target="employees"),
        AIMessage(content="that tool does not exist"),
    )
    events = list(run_turn(graph, "drop everything", _THREAD))

    retry = _one(events, "retry")
    assert "unknown tool" in retry["reason"]
    assert retry["layer"] == LAYER_ARGUMENTS
    assert retry["kind"] == KIND_MALFORMED_ARGUMENTS
    assert _one(events, "done")["status"] == STATUS_OK


def test_a_refused_argument_names_the_layer_that_refused_it(build):
    """An allowlist violation inside a structured tool is a retryable validation error."""
    graph, _ = build(
        _tool_call("plot", kind="pie", column="salary"),
        AIMessage(content="pie is not one of the chart kinds"),
    )
    events = list(run_turn(graph, "pie chart the salaries", _THREAD))

    retry = _one(events, "retry")
    assert "kind must be one of" in retry["reason"]
    assert retry["layer"] == LAYER_VALIDATION
    assert retry["kind"] == KIND_MALFORMED_SQL


def test_a_second_turn_on_one_thread_sees_the_first(build, checkpointer):
    """The checkpointer carries the whole first turn - question, tool result and answer."""
    graph, llm = build(
        _tool_call("query_db", sql="SELECT COUNT(*) AS n FROM employees"),
        AIMessage(content=f"acme has {_ACME_ROWS} employees"),
        AIMessage(content="the same six, yes"),
        checkpointer=checkpointer,
    )
    list(run_turn(graph, "how many employees?", _THREAD))
    list(run_turn(graph, "are you sure?", _THREAD))

    second = [message.text for message in llm.seen[2]]
    assert "how many employees?" in second
    assert f"{_ACME_ROWS}" in "".join(second)
    assert f"acme has {_ACME_ROWS} employees" in second
    assert second[-1] == "are you sure?"


def test_a_different_thread_starts_clean(build, checkpointer):
    """Conversation state is keyed by thread_id; a new thread carries nothing over."""
    graph, llm = build(
        AIMessage(content="first answer"),
        AIMessage(content="second answer"),
        checkpointer=checkpointer,
    )
    list(run_turn(graph, "first question", _THREAD))
    list(run_turn(graph, "second question", "thread-2"))

    second = [message.text for message in llm.seen[1]]
    assert "first question" not in second
    assert second[-1] == "second question"


def test_the_retry_budget_resets_between_turns(build, checkpointer, tuned):
    """A spent budget belongs to the turn that spent it, not to the thread."""
    tuned(max_tool_retries=1)
    graph, _ = build(
        _tool_call("query_db", sql="not sql at all"),
        _tool_call("query_db", sql="not sql at all"),
        checkpointer=checkpointer,
    )
    first = list(run_turn(graph, "one", _THREAD))
    second = list(run_turn(graph, "two", _THREAD))

    assert _one(first, "retry")["attempt"] == 1
    assert _one(second, "retry")["attempt"] == 1
    assert _one(second, "done")["status"] == STATUS_GAVE_UP


def test_the_system_prompt_carries_the_schema_card_and_own_tenant_samples(build):
    """The prompt is built from the live table through the scoped executor, notes excluded."""
    graph, llm = build(AIMessage(content="ready"))
    list(run_turn(graph, "hello", _THREAD))
    prompt = llm.seen[0][0].text

    assert "salary INTEGER" in prompt
    assert "performance_score REAL" in prompt
    assert "hire_date TEXT" in prompt
    assert "Ada | Engineering | 100" in prompt
    assert "refactored the billing pipeline" not in prompt
    assert _BETA_MARKER not in prompt
    assert "Bo" not in prompt


def test_the_system_prompt_states_the_query_rules(build):
    """Aggregation push-down, column selection, inline literals, wrapped set operations, scope."""
    graph, llm = build(AIMessage(content="ready"))
    list(run_turn(graph, "hello", _THREAD))
    prompt = llm.seen[0][0].text

    assert "GROUP BY inside the query" in prompt
    assert "only the columns the question needs" in prompt
    assert "? placeholder is rejected" in prompt
    assert "UNION, INTERSECT, EXCEPT) is refused at the top level" in prompt
    assert f"{ACME} tenant's rows only" in prompt
    assert "does not depend on you following it" in prompt


def test_the_tenant_is_bound_by_closure_not_by_the_prompt(build):
    """Whatever the model writes, the executed SQL is scoped and the rows are the tenant's."""
    graph, _ = build(
        _tool_call("query_db", sql="SELECT tenant_id, salary FROM employees"),
        AIMessage(content="all of these are acme rows"),
    )
    events = list(run_turn(graph, "show every tenant", _THREAD))

    data = _one(events, "tool_result")["data"]
    assert {row[0] for row in data["rows"]} == {ACME}
    assert data["returned_count"] == _ACME_ROWS


def test_every_tool_call_leaves_an_audit_row(build, db_path):
    """The data-access audit is db.py's, and the agent's tool calls land in it like any query."""
    graph, _ = build(
        _tool_call("query_db", sql="SELECT COUNT(*) FROM employees"),
        AIMessage(content="six"),
    )
    list(run_turn(graph, "count them", _THREAD))

    verdicts = [entry.verdict for entry in db.audit_entries(db_path)]
    assert verdicts.count(db.VERDICT_APPROVED) == 2
