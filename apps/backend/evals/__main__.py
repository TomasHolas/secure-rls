"""The eval harness entry point: run both suites over every tenant and write the scored report.

    uv run python -m evals --dry-run          # list every graded ask, no endpoint needed
    uv run python -m evals --mocked           # network-free: scripted model, hashed embedder
    uv run python -m evals                    # live: OLLAMA_BASE_URL plus runtime.json's model

The two suites live next door - `correctness` grades answers against pandas ground truth,
`adversarial` grades attacks against the mechanical leak check - and both are run here for every
tenant in turn, because an isolation claim is per tenant. They share one workspace: the committed
CSV is loaded once, the notes are embedded once, and each tenant gets its own compiled graph over
that single database.

The report is written whole, not appended, because it is the current model's scorecard rather
than a log; the timestamp and model id are passed into the renderer by this module, so nothing
deep in the report generator reaches for an ambient clock.

The exit code carries the verdict a pipeline should act on. A leak anywhere, or a turn whose
stream never reached `done`, exits 1. In mocked mode every failed ask exits 1 as well: the
scripted model plays each ask's reference tool path, so a failure there is a bug in the harness
and not a weak model. A live model that simply gets an ask wrong exits 0 and the report says so.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from langchain_core.language_models import BaseChatModel

import rag
from evals import adversarial, correctness, harness, mocked
from evals.harness import Session, Turn
from runtime import runtime

DEFAULT_REPORT = Path(__file__).resolve().parent / "report.md"
MOCKED_REPORT = Path(__file__).resolve().parent / "report-mocked.md"

CORRECTNESS = "correctness"
SECURITY = "security"
SUITES = (CORRECTNESS, SECURITY)

_MOCKED_ENDPOINT = "none - scripted model, network-free"
_MOCKED_EMBEDDER = "a hashed bag of words, not a model"
_LIVE_ENDPOINT = "the configured Ollama endpoint (address deliberately not recorded)"
_MAX_FINDINGS = 25


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the arguments, run the selected suites, write the report and return the verdict."""
    arguments = _parse_args(argv)
    tenants = tuple(arguments.tenant) or harness.TENANTS
    suites = tuple(arguments.suite) or SUITES
    if arguments.dry_run:
        print(_listing(tenants, suites))
        return 0
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    llm, embedder, model = _model(arguments, tenants)
    with harness.workspace(llm, embedder, tenants, model) as session:
        graded = _grade(session, tenants, suites)
        indexed = session.indexed
    report = _render(model, stamp, arguments.mocked, indexed, tenants, suites, *graded)
    out = arguments.out or (MOCKED_REPORT if arguments.mocked else DEFAULT_REPORT)
    out.write_text(f"{report}\n")
    print(f"wrote {out}", file=sys.stderr)
    return _verdict(arguments.mocked, *graded)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """The command line: which model, which tenants, which suites, where the report goes."""
    parser = argparse.ArgumentParser(prog="evals", description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=runtime().agent.model, help="Ollama model id to grade")
    parser.add_argument(
        "--tenant", action="append", default=[], help="grade only this tenant, repeatable"
    )
    parser.add_argument(
        "--suite", action="append", default=[], choices=SUITES, help="run only this suite"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="report file to write; defaults to report.md, or report-mocked.md under --mocked",
    )
    parser.add_argument(
        "--mocked", action="store_true", help="scripted model and hashed embedder, no network"
    )
    parser.add_argument("--dry-run", action="store_true", help="list every graded ask and exit")
    return parser.parse_args(argv)


def _model(
    arguments: argparse.Namespace, tenants: Sequence[str]
) -> tuple[BaseChatModel, rag.EmbedClient, str]:
    """The model and embedder this run grades with: scripted and offline, or live over Ollama."""
    if arguments.mocked:
        scripted = mocked.ScriptedModel(plans=mocked.plans(tenants))
        return scripted, mocked.HashEmbed(), mocked.MODEL_ID
    base_url = harness.require_base_url()
    return (
        harness.live_model(base_url, arguments.model),
        rag.OllamaEmbed(base_url),
        arguments.model,
    )


def _grade(
    session: Session, tenants: Sequence[str], suites: Sequence[str]
) -> tuple[list[correctness.Score], list[adversarial.Score]]:
    """Run every selected suite for every tenant, in the order the report prints them."""
    scored: list[correctness.Score] = []
    attacked: list[adversarial.Score] = []
    for tenant_id in tenants:
        truth = harness.truth_for(tenant_id)
        graph = session.graphs[tenant_id]
        if CORRECTNESS in suites:
            frame = correctness.frame_for(tenant_id)
            for ask in correctness.ASKS:
                _announce(CORRECTNESS, tenant_id, ask.name)
                expect = ask.truth(frame)
                turn = harness.collect(graph, ask.question, f"{tenant_id}-{ask.name}", truth)
                _announce_turn(turn)
                scored.append(correctness.score(ask, tenant_id, expect, turn))
        if SECURITY in suites:
            target = adversarial.target_for(tenant_id)
            planted = harness.poisoned_ids(tenant_id)
            for attack in adversarial.ATTACKS:
                _announce(SECURITY, tenant_id, attack.name)
                questions = adversarial.questions_for(attack, target)
                thread = f"{tenant_id}-{attack.name}"
                turns = [
                    _announce_turn(harness.collect(graph, question, thread, truth))
                    for question in questions
                ]
                attacked.append(
                    adversarial.score(attack, target, questions, turns, truth, planted)
                )
    return scored, attacked


def _announce(suite: str, tenant_id: str, name: str) -> None:
    """Say what is running, so a half-hour live run is watchable while it happens."""
    print(f"{suite}/{tenant_id}: {name}", file=sys.stderr, flush=True)


def _announce_turn(turn: Turn) -> Turn:
    """Say what one turn cost and how it ended, so a stalled run is visible from its log."""
    tools = ",".join(dict.fromkeys(turn.executed)) or "-"
    print(
        f"  {turn.seconds:.1f}s status={turn.status or 'none'} tools={tools} "
        f"chunks={turn.chunks} foreign={turn.foreign_rows}",
        file=sys.stderr,
        flush=True,
    )
    return turn


def _verdict(
    is_mocked: bool, scored: Sequence[correctness.Score], attacked: Sequence[adversarial.Score]
) -> int:
    """1 on a leak or a broken stream - and, in mocked mode, on any failed ask; 0 otherwise."""
    leaks = _leaks(scored, attacked)
    broken = sum(1 for item in _turns(scored, attacked) if item.broken)
    failures = sum(not item.passed for item in scored) + sum(
        not item.passed for item in attacked
    )
    if leaks or broken or (is_mocked and failures):
        return 1
    return 0


def _leaks(
    scored: Sequence[correctness.Score], attacked: Sequence[adversarial.Score]
) -> int:
    """Every foreign row, anomaly or note seen anywhere, plus every foreign name spoken."""
    return (
        sum(item.turn.foreign_rows for item in scored)
        + sum(item.foreign_rows for item in attacked)
        + sum(len(item.leaked_names) for item in attacked)
    )


def _turns(
    scored: Sequence[correctness.Score], attacked: Sequence[adversarial.Score]
) -> list[Turn]:
    """Every turn either suite ran, for the counts the headline reports."""
    return [item.turn for item in scored] + [turn for item in attacked for turn in item.turns]


def _render(
    model: str,
    stamp: str,
    is_mocked: bool,
    indexed: int,
    tenants: Sequence[str],
    suites: Sequence[str],
    scored: Sequence[correctness.Score],
    attacked: Sequence[adversarial.Score],
) -> str:
    """The whole report: the headline, both suite sections, the findings, how to reproduce."""
    blocks = [_headline(model, stamp, is_mocked, indexed, tenants, suites, scored, attacked)]
    if CORRECTNESS in suites:
        blocks.append(correctness.render(scored, tenants))
    if SECURITY in suites:
        blocks.append(adversarial.render(attacked, tenants))
    blocks.append(_findings(scored, attacked))
    blocks.append(_reproduce(is_mocked))
    return "\n\n".join(blocks)


def _headline(
    model: str,
    stamp: str,
    is_mocked: bool,
    indexed: int,
    tenants: Sequence[str],
    suites: Sequence[str],
    scored: Sequence[correctness.Score],
    attacked: Sequence[adversarial.Score],
) -> str:
    """What was run against what, and the two numbers a reader came for."""
    turns = _turns(scored, attacked)
    seconds = sum(turn.seconds for turn in turns)
    lines = [
        "# Evaluation harness report",
        "",
        f"Model `{model}`, run {stamp}. Endpoint: "
        f"{_MOCKED_ENDPOINT if is_mocked else _LIVE_ENDPOINT}. "
        f"Suites: {', '.join(suites)}. Tenants: {', '.join(f'`{name}`' for name in tenants)}. "
        f"Dataset: the committed `employees.csv`, {indexed} notes indexed with "
        f"{_MOCKED_EMBEDDER if is_mocked else f'`{runtime().agent.embed_model}`'}.",
        "",
        f"- Correctness: **{harness.rate([item.passed for item in scored])}** asks passed",
        f"- Security: **{harness.rate([item.passed for item in attacked])}** attacks held",
        f"- **Leaks: {_leaks(scored, attacked)}** - foreign rows, anomalies or notes in any tool "
        f"result, plus foreign employee names in any answer, over {len(turns)} turns",
        f"- Turns that never reached `done`: {sum(1 for turn in turns if turn.broken)}",
        f"- Turns a per-turn bound cut short: {sum(1 for turn in turns if turn.cut)} "
        f"({_bounds()})",
        f"- Wall time: {seconds / 60:.1f} min over {len(turns)} turns "
        f"({seconds / max(len(turns), 1):.1f} s per turn)",
        f"- Output tokens per turn: {_tokens(turns)}",
    ]
    return "\n".join(lines)


def _tokens(turns: Sequence[Turn]) -> str:
    """The generation the turns actually spent, or a note that the client reported none."""
    counts = [turn.output_tokens for turn in turns]
    if not any(counts):
        return "not reported by this model client"
    return f"median {median(counts):.0f}, max {max(counts)}"


def _bounds() -> str:
    """The per-turn bounds this run was subject to, read from runtime.json (ADR 0011 amended)."""
    agent = runtime().agent
    return (
        f"{agent.max_output_tokens} output tokens, {agent.context_window} context, "
        f"{agent.turn_deadline_s:g} s, {agent.max_tool_iterations} tool rounds"
    )


def _findings(
    scored: Sequence[correctness.Score], attacked: Sequence[adversarial.Score]
) -> str:
    """Every retry, refusal and stream failure the run recorded, collapsed by how often it fired."""
    counted = Counter(
        finding for turn in _turns(scored, attacked) for finding in turn.findings
    )
    if not counted:
        return "## Findings\n\nNo retries, refusals or stream failures were recorded."
    rows = [
        (str(count), finding) for finding, count in counted.most_common(_MAX_FINDINGS)
    ]
    extra = (
        f"\n\n{len(counted) - _MAX_FINDINGS} further distinct findings are not listed."
        if len(counted) > _MAX_FINDINGS
        else ""
    )
    return (
        "## Findings\n\nEvery retry, refusal and stream failure, identical ones collapsed. A "
        "refusal here is the security layers doing their job, not a failure.\n\n"
        + harness.table(("times", "finding"), rows)
        + extra
    )


def _reproduce(is_mocked: bool) -> str:
    """The exact command that produced this report."""
    command = "uv run python -m evals --mocked" if is_mocked else "uv run python -m evals"
    return (
        "## Reproducing this\n\n```bash\ncd apps/backend\n"
        f"{command}\n```\n\nThe live mode needs `OLLAMA_BASE_URL` pointing at an Ollama endpoint "
        "that serves the model above; the mocked mode needs nothing."
    )


def _listing(tenants: Sequence[str], suites: Sequence[str]) -> str:
    """Every graded ask, so `--dry-run` documents the suites without touching an endpoint."""
    blocks = [
        f"Tenants: {', '.join(tenants)}. Suites: {', '.join(suites)}.",
    ]
    if CORRECTNESS in suites:
        blocks.append(
            harness.table(
                ("#", "ask", "tool", "scoring rule", "question"),
                (
                    (str(index), f"`{ask.name}`", f"`{ask.tool}`", ask.rule, ask.question)
                    for index, ask in enumerate(correctness.ASKS, start=1)
                ),
            )
        )
    if SECURITY in suites:
        blocks.append(
            harness.table(
                ("#", "attack", "vector", "turns", "first turn"),
                (
                    (
                        str(index),
                        f"`{attack.name}`",
                        attack.vector,
                        str(len(attack.turns)),
                        attack.turns[0],
                    )
                    for index, attack in enumerate(adversarial.ATTACKS, start=1)
                ),
            )
        )
    return "\n\n".join(blocks)


if __name__ == "__main__":
    raise SystemExit(main())
