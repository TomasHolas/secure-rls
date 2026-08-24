"""Titling tests (issues #72 and #118, ADR 0012 as amended): the label and every way it fails.

Model-free by construction: the titler seam is a callable, so a test scripts what the model
"answers" - a good title, thinking markup, control characters, a paragraph, an exception - and
asserts what the thread ends up called. What every case shares is that `generate_title` returns
a usable title and never raises: a titling failure must not be able to fail anything.

Two properties are the point of the re-titling window (issue #118): the prompt carries the
exchanges the thread has had, so a thread that opened with a greeting is named from the question
that followed; and a label that cannot be produced leaves the standing name alone, because
re-titling repeatedly must never be able to cost a reader a name they already had.
"""

import pytest

from agent import Message
from runtime import runtime
from titles import generate_title, should_title

QUESTION = "what is the average salary per department?"
ANSWER = "Engineering leads at 91000."
CURRENT = "what is the average salary per depa..."
UNNAMED = runtime().api.default_title
EXCHANGE = [Message(role="user", content=QUESTION), Message(role="assistant", content=ANSWER)]
GREETING = "Hello, how are you"
GREETED = "I am well. Ask me about your HR data."
TWO_TURNS = [
    Message(role="user", content=GREETING),
    Message(role="assistant", content=GREETED),
    *EXCHANGE,
]
TITLE = "Average salary by department"


class ScriptedModel:
    """Answers every titling prompt with the canned text, recording the prompts it was given."""

    def __init__(self, answer: str) -> None:
        """Script the answer this model returns."""
        self.answer = answer
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        """Record the prompt and answer as scripted."""
        self.prompts.append(prompt)
        return self.answer


def _refusing(error: Exception):
    """A titler that fails the way an unreachable or timed-out endpoint does."""

    def ask(prompt: str) -> str:
        raise error

    return ask


def _turns(count: int) -> list[Message]:
    """A transcript of that many answered turns, each nameable on its own."""
    return [
        message
        for turn in range(count)
        for message in (
            Message(role="user", content=f"question {turn}"),
            Message(role="assistant", content=f"answer {turn}"),
        )
    ]


def test_the_models_title_is_used_when_it_gives_one():
    assert generate_title(EXCHANGE, ScriptedModel(TITLE), current=CURRENT) == TITLE


def test_the_prompt_carries_the_exchange_and_the_configured_word_cap():
    model = ScriptedModel(TITLE)

    generate_title(EXCHANGE, model, current=CURRENT)

    assert QUESTION in model.prompts[0]
    assert ANSWER in model.prompts[0]
    assert str(runtime().conversations.generated_title_max_words) in model.prompts[0]


def test_the_prompt_carries_the_answer_and_not_the_words_before_the_tool_calls():
    model = ScriptedModel(TITLE)
    with_tools = [
        Message(role="user", content=QUESTION),
        Message(role="assistant", content="Let me query the database."),
        Message(role="assistant", content=ANSWER),
    ]

    generate_title(with_tools, model, current=CURRENT)

    assert ANSWER in model.prompts[0]
    assert "Let me query the database." not in model.prompts[0]


def test_an_unanswered_turn_is_still_titled_from_its_question():
    model = ScriptedModel(TITLE)
    question_only = [Message(role="user", content=QUESTION)]

    assert generate_title(question_only, model, current=CURRENT) == TITLE
    assert QUESTION in model.prompts[0]


@pytest.mark.parametrize(
    "answered",
    [
        f'"{TITLE}"',
        f"Title: {TITLE}",
        f"**{TITLE}.**",
        f"<think>The user asks about salaries, so a good label is this.</think>\n{TITLE}",
        f"  {TITLE}\n",
    ],
    ids=["quoted", "labelled", "markdown", "thinking out loud", "padded"],
)
def test_the_wrapping_a_model_adds_is_stripped(answered):
    assert generate_title(EXCHANGE, ScriptedModel(answered), current=CURRENT) == TITLE


def test_control_characters_never_survive_into_the_title():
    answered = "Average\x00salary\x1b by‮ department​"

    titled = generate_title(EXCHANGE, ScriptedModel(answered), current=CURRENT)

    assert titled == "Average salary by department"


def test_an_overlong_but_title_shaped_answer_is_cut_to_the_generated_cap():
    caps = runtime().conversations
    wordy = "Average salary " * (caps.generated_title_reject_chars // len("Average salary ") - 1)

    titled = generate_title(EXCHANGE, ScriptedModel(wordy), current=CURRENT)

    assert caps.generated_title_max_chars < len(wordy) <= caps.generated_title_reject_chars
    assert titled == wordy.strip()[: caps.generated_title_max_chars]


@pytest.mark.parametrize(
    "answered",
    [
        "",
        "   \n\t  ",
        "\x00\x1b​",
        '"""',
        "<think>I should answer with a title, but I will forget to.</think>",
        "Sure. " + "This conversation is about salaries per department. " * 10,
    ],
    ids=["empty", "whitespace", "control only", "punctuation only", "thinking only", "prose"],
)
def test_junk_output_leaves_the_title_the_thread_already_has(answered):
    assert generate_title(EXCHANGE, ScriptedModel(answered), current=CURRENT) == CURRENT
    assert generate_title(EXCHANGE, ScriptedModel(answered), current=UNNAMED) == QUESTION


@pytest.mark.parametrize(
    "error",
    [TimeoutError("read timeout"), RuntimeError("connect timeout to http://ollama.internal")],
    ids=["timeout", "unreachable endpoint"],
)
def test_a_failing_model_leaves_the_title_the_thread_already_has(error):
    assert generate_title(EXCHANGE, _refusing(error), current=CURRENT) == CURRENT
    assert generate_title(EXCHANGE, _refusing(error), current=UNNAMED) == QUESTION


@pytest.mark.parametrize(
    "error",
    [TimeoutError("read timeout"), RuntimeError("connect timeout to http://ollama.internal")],
    ids=["timeout", "unreachable endpoint"],
)
def test_a_failed_retitle_keeps_the_label_an_earlier_turn_produced(error):
    assert generate_title(TWO_TURNS, _refusing(error), current=TITLE) == TITLE


def test_the_prompt_carries_every_exchange_the_thread_has_had():
    model = ScriptedModel(TITLE)

    titled = generate_title(TWO_TURNS, model, current="Greetings")

    assert titled == TITLE
    for said in (GREETING, GREETED, QUESTION, ANSWER):
        assert said in model.prompts[0]


def test_the_prompt_carries_at_most_the_windows_exchanges():
    window = runtime().conversations.title_turns
    model = ScriptedModel(TITLE)

    generate_title(_turns(window + 2), model, current=CURRENT)

    assert f"question {window - 1}" in model.prompts[0]
    assert f"question {window}" not in model.prompts[0]


def test_a_long_message_is_clipped_before_it_reaches_the_prompt():
    cap = runtime().conversations.title_message_chars
    answer = "salary " * cap
    model = ScriptedModel(TITLE)

    generate_title(
        [Message(role="user", content=QUESTION), Message(role="assistant", content=answer)],
        model,
        current=CURRENT,
    )

    assert answer[:cap] in model.prompts[0]
    assert answer[: cap + 1] not in model.prompts[0]


def test_a_young_thread_is_titled_again_after_every_turn():
    window = runtime().conversations.title_turns

    assert all(should_title(_turns(turn)) for turn in range(window + 1))


def test_a_thread_past_the_window_is_never_titled_again():
    past = runtime().conversations.title_turns + 1

    assert should_title(_turns(past)) is False
    assert should_title(_turns(past * 3)) is False


def test_an_empty_transcript_keeps_the_title_the_thread_already_has():
    model = ScriptedModel(TITLE)

    assert generate_title([], model, current=CURRENT) == CURRENT
    assert model.prompts == []
