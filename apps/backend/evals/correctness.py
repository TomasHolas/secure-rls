"""The correctness suite: 25 asks graded against pandas ground truth (issue #29, ADR 0004).

Every ask is graded against a figure computed here with pandas straight over the committed
`employees.csv`, never through the agent's own SQL path - that independence is the whole point:
a bug in the scoped executor cannot vouch for itself. The frame is filtered to the session
tenant, so the same 25 asks are graded against a different set of correct answers for `acme`,
`beta` and `gamma`.

What is scored, in order of how much it is trusted:

- `payload` - the deterministic verdict. Every expected item must appear in the structured
  payload of some `tool_result` in the turn: a float within 1% relative tolerance, a count or a
  salary as an exact integer, a name as an exact string, a phrase as a case-insensitive
  substring of the tool text the model was shown. Payloads are read from the database by the
  tools themselves, so this asks whether the agent fetched the right thing, which is the part a
  model can get wrong without anyone noticing.
- `answer states` - reported, never a veto. The same expected items looked for in the answer
  text: numbers parsed out of the prose and compared at the same tolerance, names and phrases as
  substrings. A model that fetched the right rows and then wrote the number in a form this parser
  does not read (rounded to "about 92 thousand", or shown only in a chart) is imprecise for a
  demo, not wrong; the `plot` asks legitimately state nothing because the tool deliberately
  returns no numbers to the model.
- `status` must be `ok` and the mechanical leak count must be zero, exactly as in the security
  suite - a correct answer assembled from another tenant's rows is not a pass.

Each ask carries its own one-line scoring rule, printed in the report next to it, so a reader
never has to guess what "pass" meant for that row.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from agent import STATUS_OK
from evals.harness import CSV_PATH, TENANT_COLUMN, Turn, flag, rate, short, table, verdict
from runtime import runtime

_RELATIVE_TOLERANCE = 0.01
_YEAR_CHARS = 4
_NO_ANOMALIES = "no rows lie beyond"
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_MAX_MISSING = 3


@dataclass(frozen=True)
class Expect:
    """What a correct turn must have fetched: figures, names and phrases, each matched its way."""

    numbers: tuple[float, ...] = ()
    integers: tuple[int, ...] = ()
    names: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()

    @property
    def items(self) -> tuple[str, ...]:
        """Every expected item as the report words it, in the order they are checked."""
        return (
            *(_figure(value) for value in self.numbers),
            *(str(value) for value in self.integers),
            *self.names,
            *(f'"{phrase}"' for phrase in self.phrases),
        )


@dataclass(frozen=True)
class Ask:
    """One graded question: the tool it should reach for, and the ground truth it is scored on."""

    name: str
    tool: str
    question: str
    rule: str
    truth: Callable[[pd.DataFrame], Expect]


@dataclass(frozen=True)
class Score:
    """One graded ask for one tenant, in the terms the report table is written in."""

    ask: Ask
    tenant: str
    expect: Expect
    turn: Turn
    missing: tuple[str, ...]
    unstated: tuple[str, ...]

    @property
    def payload_ok(self) -> bool:
        """Whether every expected item was found in the tool payloads the turn produced."""
        return not self.missing

    @property
    def answer_ok(self) -> bool:
        """Whether the answer text states every expected item; reported, never a veto."""
        return not self.unstated

    @property
    def passed(self) -> bool:
        """The suite's verdict: the right figures fetched, a clean finish, nothing foreign."""
        return (
            self.payload_ok
            and not self.turn.broken
            and self.turn.status == STATUS_OK
            and self.turn.foreign_rows == 0
        )


def frame_for(tenant_id: str, csv_path: Path = CSV_PATH) -> pd.DataFrame:
    """The tenant's own rows as a pandas frame, with the hire year the grouped asks need."""
    frame = pd.read_csv(csv_path)
    own = frame[frame[TENANT_COLUMN] == tenant_id].copy()
    own["hire_year"] = own["hire_date"].str[:_YEAR_CHARS]
    return own


def score(ask: Ask, tenant_id: str, expect: Expect, turn: Turn) -> Score:
    """Grade one turn: what the payloads prove, and what the answer text repeats."""
    numbers = [value for payload in turn.payloads for value in _payload_numbers(payload)]
    strings = {value for payload in turn.payloads for value in _strings_in(payload)}
    spoken = _numbers_in_text(turn.answer)
    return Score(
        ask=ask,
        tenant=tenant_id,
        expect=expect,
        turn=turn,
        missing=_missing(expect, numbers, strings, turn.contents),
        unstated=_missing(expect, spoken, {turn.answer}, turn.answer, exact_strings=False),
    )


def render(scores: Sequence[Score], tenants: Sequence[str]) -> str:
    """The correctness section: one table per tenant plus the per-tenant pass rates."""
    blocks = ["## Correctness suite", "", _summary(scores, tenants), ""]
    for tenant_id in tenants:
        rows = [item for item in scores if item.tenant == tenant_id]
        if not rows:
            continue
        blocks.extend((f"### Correctness - tenant `{tenant_id}`", "", _tenant_table(rows), ""))
    return "\n".join(blocks).rstrip()


def _summary(scores: Sequence[Score], tenants: Sequence[str]) -> str:
    """The pass rate per tenant, and the leak count across every correctness turn."""
    rows = [
        (
            f"`{tenant_id}`",
            rate([item.passed for item in scores if item.tenant == tenant_id]),
            rate([item.payload_ok for item in scores if item.tenant == tenant_id]),
            rate([item.answer_ok for item in scores if item.tenant == tenant_id]),
            str(sum(item.turn.foreign_rows for item in scores if item.tenant == tenant_id)),
        )
        for tenant_id in tenants
    ]
    rows.append(
        (
            "**all tenants**",
            rate([item.passed for item in scores]),
            rate([item.payload_ok for item in scores]),
            rate([item.answer_ok for item in scores]),
            str(sum(item.turn.foreign_rows for item in scores)),
        )
    )
    return table(("tenant", "passed", "payload ok", "answer states it", "foreign rows"), rows)


def _tenant_table(scores: Sequence[Score]) -> str:
    """Every graded ask for one tenant, with what was expected and what came back."""
    return table(
        (
            "#",
            "ask",
            "tool",
            "scoring rule",
            "expected",
            "tools run",
            "payload ok",
            "answer states it",
            "status",
            "wall s",
            "verdict",
        ),
        (
            (
                str(index),
                f"`{item.ask.name}`",
                f"`{item.ask.tool}`",
                item.ask.rule,
                _expected_cell(item),
                ", ".join(f"`{tool}`" for tool in dict.fromkeys(item.turn.executed)) or "none",
                flag(item.payload_ok) if item.payload_ok else _missing_cell(item.missing),
                flag(item.answer_ok),
                f"`{item.turn.status or 'no done event'}`",
                f"{item.turn.seconds:.1f}",
                verdict(item.passed),
            )
            for index, item in enumerate(scores, start=1)
        ),
    )


def _expected_cell(item: Score) -> str:
    """The expected items, abbreviated when an ask expects a long list of names."""
    items = item.expect.items
    if len(items) <= _MAX_MISSING:
        return ", ".join(items) or "-"
    return f"{', '.join(items[:_MAX_MISSING])} (+{len(items) - _MAX_MISSING} more)"


def _missing_cell(missing: Sequence[str]) -> str:
    """A failing payload cell names what was not found, so the row explains itself."""
    shown = ", ".join(missing[:_MAX_MISSING])
    extra = f" (+{len(missing) - _MAX_MISSING} more)" if len(missing) > _MAX_MISSING else ""
    return short(f"no: missing {shown}{extra}")


def _missing(
    expect: Expect,
    numbers: Sequence[float],
    strings: set[str],
    text: str,
    exact_strings: bool = True,
) -> tuple[str, ...]:
    """Every expected item that is not present, as the report names it."""
    lowered = text.lower()
    missing = [
        _figure(value) for value in expect.numbers if not _has_close(numbers, value)
    ]
    missing.extend(str(value) for value in expect.integers if not _has_exact(numbers, value))
    missing.extend(
        name
        for name in expect.names
        if not (name in strings if exact_strings else name.lower() in lowered)
    )
    missing.extend(f'"{phrase}"' for phrase in expect.phrases if phrase.lower() not in lowered)
    return tuple(missing)


def _figure(value: float) -> str:
    """One expected figure as the report prints it: grouped thousands, two decimals."""
    return f"{value:,.2f}"


def _has_close(values: Sequence[float], expected: float) -> bool:
    """Whether any value matches at 1% relative tolerance, the ADR 0004 float rule."""
    tolerance = abs(expected) * _RELATIVE_TOLERANCE
    return any(abs(value - expected) <= tolerance for value in values)


def _has_exact(values: Sequence[float], expected: int) -> bool:
    """Whether any value is exactly the expected count, salary or year."""
    return any(value == float(expected) for value in values)


def _payload_numbers(payload: dict[str, object]) -> list[float]:
    """One payload's figures: every number it carries, plus how many records it returned.

    A number written as text counts - `SUBSTR(hire_date, 1, 4)` returns the year as a string, and
    a year the tool fetched is a year the tool fetched. So does the length of the row, anomaly and
    note lists, which is how "seven employees are outliers" is asserted against a tool that
    returns the seven rows rather than the count.
    """
    lengths = [
        float(len(records))
        for key in ("rows", "anomalies", "notes")
        if isinstance(records := payload.get(key), list)
    ]
    return _numbers_in(payload) + lengths


def _numbers_in(payload: object) -> list[float]:
    """Every number anywhere in one tool payload, however deeply the tool nests it."""
    if isinstance(payload, bool):
        return []
    if isinstance(payload, int | float):
        return [float(payload)]
    if isinstance(payload, str):
        return _parsed(payload)
    if isinstance(payload, dict):
        return [value for item in payload.values() for value in _numbers_in(item)]
    if isinstance(payload, list | tuple):
        return [value for item in payload for value in _numbers_in(item)]
    return []


def _parsed(value: str) -> list[float]:
    """A string cell as a number when it is one, so a year returned as text still counts."""
    try:
        return [float(value)]
    except ValueError:
        return []


def _strings_in(payload: object) -> list[str]:
    """Every string anywhere in one tool payload, for the exact-match name checks."""
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        return [value for item in payload.values() for value in _strings_in(item)]
    if isinstance(payload, list | tuple):
        return [value for item in payload for value in _strings_in(item)]
    return []


def _numbers_in_text(text: str) -> list[float]:
    """The numbers a prose answer states, thousands separators and all."""
    return [float(match.group().replace(",", "")) for match in _NUMBER.finditer(text)]


def _tukey_names(frame: pd.DataFrame, column: str, key: str) -> tuple[str, ...]:
    """The rows beyond 1.5 x IQR from their own group's quartiles, computed independently here."""
    flagged: list[str] = []
    for _, group in frame.groupby(key):
        lower_quartile, upper_quartile = group[column].quantile([0.25, 0.75])
        reach = 1.5 * (upper_quartile - lower_quartile)
        low, high = lower_quartile - reach, upper_quartile + reach
        flagged.extend(group[(group[column] < low) | (group[column] > high)]["name"])
    return tuple(flagged)


def _anomaly_expect(frame: pd.DataFrame, column: str, key: str) -> Expect:
    """Anomalies as an expectation: how many were flagged and who, or the neutral no-rows text."""
    names = _tukey_names(frame, column, key)
    if not names:
        return Expect(integers=(0,), phrases=(_NO_ANOMALIES,))
    return Expect(integers=(len(names),), names=names)


def _tallest_bin(frame: pd.DataFrame, column: str) -> int:
    """The row count of the fullest bin, binned the same way the plot tool bins (runtime.json)."""
    bins = runtime().analytics.histogram_bins
    values = frame[column].astype(float)
    low, high = values.min(), values.max()
    width = (high - low) / bins
    edges = [low + step * width for step in range(bins + 1)]
    return int(pd.cut(values, bins=edges, include_lowest=True).value_counts().max())


def _note_phrase(frame: pd.DataFrame, phrase: str) -> Expect:
    """A retrieval expectation, refused unless pandas confirms this tenant's notes contain it."""
    if not frame["notes"].str.contains(phrase, case=False).any():
        raise ValueError(f"no note of this tenant contains {phrase!r}; the ask cannot be graded")
    return Expect(phrases=(phrase,))


def _matching(frame: pd.DataFrame, mask: pd.Series) -> Expect:
    """The names a filter selects plus how many there are - a listing ask's whole ground truth."""
    selected = frame[mask]
    return Expect(integers=(len(selected),), names=tuple(selected["name"]))


ASKS: tuple[Ask, ...] = (
    Ask(
        name="high-earners",
        tool="query_db",
        question="Which employees earn more than 220000? Give their names and salaries.",
        rule="every matching name, exact, plus the row count",
        truth=lambda frame: _matching(frame, frame["salary"] > 220000),
    ),
    Ask(
        name="hires-2005",
        tool="query_db",
        question="List the names of everyone who was hired in 2005.",
        rule="every matching name, exact, plus the row count",
        truth=lambda frame: _matching(frame, frame["hire_year"] == "2005"),
    ),
    Ask(
        name="sales-headcount",
        tool="query_db",
        question="How many people work in the Sales department?",
        rule="the count, exact",
        truth=lambda frame: Expect(integers=(int((frame["department"] == "Sales").sum()),)),
    ),
    Ask(
        name="payroll-per-department",
        tool="query_db",
        question=(
            "What is the total payroll cost per department, from the most expensive department "
            "to the cheapest?"
        ),
        rule="all five department totals, exact",
        truth=lambda frame: Expect(
            integers=tuple(
                int(value) for value in frame.groupby("department")["salary"].sum()
            )
        ),
    ),
    Ask(
        name="recent-hire-counts",
        tool="query_db",
        question="How many employees did we hire in 2022, and how many in 2023?",
        rule="both yearly counts, exact",
        truth=lambda frame: Expect(
            integers=(
                int((frame["hire_year"] == "2022").sum()),
                int((frame["hire_year"] == "2023").sum()),
            )
        ),
    ),
    Ask(
        name="above-own-dept-average",
        tool="query_db",
        question=(
            "How many employees earn more than the average salary of their own department?"
        ),
        rule="the count, exact (needs a per-department comparison, not a global one)",
        truth=lambda frame: Expect(
            integers=(
                int(
                    sum(
                        int((group["salary"] > group["salary"].mean()).sum())
                        for _, group in frame.groupby("department")
                    )
                ),
            )
        ),
    ),
    Ask(
        name="earliest-hire-year",
        tool="query_db",
        question=(
            "In which year did our earliest hires start, and how many people started that year?"
        ),
        rule="the year and that year's headcount, both exact",
        truth=lambda frame: Expect(
            integers=(
                int(frame["hire_year"].min()),
                int((frame["hire_year"] == frame["hire_year"].min()).sum()),
            )
        ),
    ),
    Ask(
        name="marketing-top-salary",
        tool="query_db",
        question="What is the highest salary in the Marketing department?",
        rule="the salary, exact",
        truth=lambda frame: Expect(
            integers=(int(frame[frame["department"] == "Marketing"]["salary"].max()),)
        ),
    ),
    Ask(
        name="high-performers",
        tool="query_db",
        question="How many employees have a performance score above 4.5?",
        rule="the count, exact",
        truth=lambda frame: Expect(
            integers=(int((frame["performance_score"] > 4.5).sum()),)
        ),
    ),
    Ask(
        name="average-salary",
        tool="get_stats",
        question="What is the average salary?",
        rule="the mean, within 1%",
        truth=lambda frame: Expect(numbers=(float(frame["salary"].mean()),)),
    ),
    Ask(
        name="headcount",
        tool="get_stats",
        question="How many employees are there in total?",
        rule="the count, exact",
        truth=lambda frame: Expect(integers=(len(frame),)),
    ),
    Ask(
        name="max-salary",
        tool="get_stats",
        question="What is the highest salary anyone is paid?",
        rule="the salary, exact",
        truth=lambda frame: Expect(integers=(int(frame["salary"].max()),)),
    ),
    Ask(
        name="score-per-department",
        tool="get_stats",
        question="What is the average performance score in each department?",
        rule="all five department means, each within 1%",
        truth=lambda frame: Expect(
            numbers=tuple(
                float(value) for value in frame.groupby("department")["performance_score"].mean()
            )
        ),
    ),
    Ask(
        name="total-payroll",
        tool="get_stats",
        question="What is our total annual payroll cost?",
        rule="the sum, exact",
        truth=lambda frame: Expect(integers=(int(frame["salary"].sum()),)),
    ),
    Ask(
        name="lowest-score",
        tool="get_stats",
        question="What is the lowest performance score in the company?",
        rule="the minimum, within 1%",
        truth=lambda frame: Expect(numbers=(float(frame["performance_score"].min()),)),
    ),
    Ask(
        name="salary-histogram",
        tool="plot",
        question="Show me the distribution of salaries as a histogram.",
        rule=(
            "the fullest equal-width salary bin, exact (a bin-edge tie can move a single row "
            "between neighbouring bins, so only the modal bin is asserted)"
        ),
        truth=lambda frame: Expect(integers=(_tallest_bin(frame, "salary"),)),
    ),
    Ask(
        name="salary-bar-chart",
        tool="plot",
        question="Draw a bar chart of the average salary per department.",
        rule="all five plotted department means, each within 1%",
        truth=lambda frame: Expect(
            numbers=tuple(float(value) for value in frame.groupby("department")["salary"].mean())
        ),
    ),
    Ask(
        name="score-line-chart",
        tool="plot",
        question="Plot how the average performance score changed by hire year as a line chart.",
        rule="every plotted yearly mean, each within 1%",
        truth=lambda frame: Expect(
            numbers=tuple(
                float(value)
                for value in frame.groupby("hire_year")["performance_score"].mean()
            )
        ),
    ),
    Ask(
        name="headcount-bar-chart",
        tool="plot",
        question="Draw a bar chart of the number of employees per department.",
        rule="all five plotted department headcounts, exact",
        truth=lambda frame: Expect(
            integers=tuple(int(value) for value in frame.groupby("department").size())
        ),
    ),
    Ask(
        name="salary-outliers",
        tool="detect_anomalies",
        question="Are there any salary outliers in our data?",
        rule="every name beyond its department's Tukey fences, exact, plus how many there are",
        truth=lambda frame: _anomaly_expect(frame, "salary", "department"),
    ),
    Ask(
        name="score-outliers",
        tool="detect_anomalies",
        question=(
            "Find the employees whose performance score is unusual compared to the rest of "
            "their own department."
        ),
        rule=(
            "every name beyond its department's Tukey fences, exact; a tenant with none must "
            "say so rather than invent one"
        ),
        truth=lambda frame: _anomaly_expect(frame, "performance_score", "department"),
    ),
    Ask(
        name="hire-year-outliers",
        tool="detect_anomalies",
        question="Whose salary is unusual compared to the other people hired in the same year?",
        rule="every name beyond its hire year's Tukey fences, exact, plus how many there are",
        truth=lambda frame: _anomaly_expect(frame, "salary", "hire_year"),
    ),
    Ask(
        name="notes-mentoring",
        tool="search_notes",
        question="What do the performance notes say about mentoring juniors?",
        rule='a retrieved note contains "mentors two juniors"',
        truth=lambda frame: _note_phrase(frame, "mentors two juniors"),
    ),
    Ask(
        name="notes-budget",
        tool="search_notes",
        question="Which employees have notes about budget reporting?",
        rule='a retrieved note contains "budget reporting"',
        truth=lambda frame: _note_phrase(frame, "budget reporting"),
    ),
    Ask(
        name="notes-release-quality",
        tool="search_notes",
        question="Do any of the performance notes mention release quality?",
        rule='a retrieved note contains "release quality"',
        truth=lambda frame: _note_phrase(frame, "release quality"),
    ),
)
