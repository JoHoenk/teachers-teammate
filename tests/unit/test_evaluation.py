"""Unit tests for the content review over a model port."""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage

from teachers_teammate.domain.context_budget import EVALUATION_ANSWER_TOKENS
from teachers_teammate.domain.evaluation import EvaluationOutcome
from teachers_teammate.domain.review_prompts import review_system
from teachers_teammate.infrastructure.evaluation import PortEvaluator
from teachers_teammate.infrastructure.ollama_utils import ChatStats, OllamaClient
from teachers_teammate.infrastructure.proofreading.port import (
    LangChainPort,
    ModelPort,
    OllamaPort,
    StepRequest,
)
from teachers_teammate.texts.review import TOO_LONG_FOR_REVIEW_WARNING
from tests.fakes.fake_port import FakePort, answer

TEXT = "Der Hund bellt."
LANGUAGE = "German"
EXPECTED_USER = f"Language: {LANGUAGE}\n\nCorrected text:\n\n{TEXT}"

LOCAL_URL = "http://localhost:11434"


class _RecordingChain:
    """A chain that records the messages and the variables it is built and invoked with."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.variables: dict[str, str] = {}

    def __or__(self, _other: object) -> _RecordingChain:
        return self

    def invoke(self, variables: dict[str, str]) -> AIMessage:
        self.variables = variables
        return AIMessage(content="ok")


class _FailingChain(_RecordingChain):
    """A chain whose provider call fails."""

    def invoke(self, _variables: dict[str, str]) -> AIMessage:
        raise RuntimeError("connection refused")


def _evaluator(port: ModelPort, prompt: str = "Grade it.") -> PortEvaluator:
    """Return the review of a run that asks *port* with *prompt*."""
    return PortEvaluator(port, prompt, temperature=0.3)


def _recording_chain(mocker) -> _RecordingChain:
    """Return a chain that stands in for LangChain's prompt template and records it."""
    chain = _RecordingChain()

    def from_messages(messages: list[tuple[str, str]]) -> _RecordingChain:
        chain.messages.extend(messages)
        return chain

    mocker.patch("langchain_core.prompts.ChatPromptTemplate.from_messages", from_messages)
    return chain


def _cut_off_port() -> LangChainPort:
    """Return a port whose provider cut its answer off at the output limit."""
    message = AIMessage(content="half a rev", response_metadata={"finish_reason": "length"})
    return LangChainPort(FakeMessagesListChatModel(responses=[message]))


@pytest.mark.unit
def test_the_review_asks_one_request_about_the_text_with_a_short_answer_allowance() -> None:
    """
    Given  a port that answers
    When   the review runs
    Then   it sent one request: the language and the text, at the review's temperature, with
           the fixed allowance of a review answer
    """
    port = FakePort(answer("ok"))

    outcome = _evaluator(port).evaluate(TEXT, LANGUAGE)

    assert outcome == EvaluationOutcome(text="ok")
    assert len(port.requests) == 1
    request = port.requests[0]
    assert request.step == "review"
    assert request.user == EXPECTED_USER
    assert request.temperature == 0.3
    assert request.answer_tokens == EVALUATION_ANSWER_TOKENS


@pytest.mark.unit
def test_the_review_carries_the_notation_hint_a_markdown_text_needs(mocker) -> None:
    """
    Given  a Markdown text and a plain one
    When   the review runs
    Then   the Markdown request says that crossed-out words are not part of the submission
    """
    port = FakePort(answer("ok"), answer("ok"))

    _evaluator(port).evaluate("~~a~~ b", LANGUAGE, text_format="markdown")
    _evaluator(port).evaluate("a b", LANGUAGE)

    markdown, plain = (request.system for request in port.requests)
    assert markdown == review_system("Grade it.", "markdown")
    assert "not part of the submission" in markdown
    assert plain == "Grade it."


@pytest.mark.unit
def test_a_local_review_and_an_online_review_send_the_same_messages(mocker) -> None:
    """
    Given  a local Ollama port and an online provider's port
    When   the same review runs on both
    Then   both sent the same system message and the same text
    """
    local_chat = mocker.patch.object(OllamaClient, "chat", return_value="ok")
    chain = _recording_chain(mocker)

    _evaluator(OllamaPort(OllamaClient(LOCAL_URL), "m")).evaluate(TEXT, LANGUAGE)
    _evaluator(LangChainPort(FakeListChatModel(responses=["ok"]))).evaluate(TEXT, LANGUAGE)

    assert chain.messages == [
        ("system", review_system("Grade it.", "plain")),
        ("human", "{text}"),
    ]
    assert local_chat.call_args.kwargs["system"] == chain.messages[0][1]
    assert local_chat.call_args.args[1] == EXPECTED_USER
    assert chain.variables == {"text": EXPECTED_USER}


@pytest.mark.unit
def test_an_online_answer_cut_off_at_its_limit_is_not_a_review() -> None:
    """
    Given  a provider whose answer the output limit cut off
    When   the review runs
    Then   the outcome carries the too-long warning instead of half a review
    """
    outcome = _evaluator(_cut_off_port()).evaluate(TEXT, LANGUAGE)

    assert outcome == EvaluationOutcome(warning=TOO_LONG_FOR_REVIEW_WARNING)


@pytest.mark.unit
def test_a_text_beyond_the_window_is_not_reviewed(mocker) -> None:
    """
    Given  a local port with a small window
    When   a text far longer than that window is reviewed
    Then   the answer carries the too-long warning, because a content review cannot be split
           into pieces the way proofreading can
    """
    # The real chat() runs its size check; only the server round trip is stubbed.
    mocker.patch.object(
        OllamaClient, "chat_with_stats", return_value=("ok", ChatStats(done_reason="stop"))
    )
    port = OllamaPort(OllamaClient(LOCAL_URL), "m", num_ctx=8192)

    outcome = _evaluator(port).evaluate("a" * 100_000, LANGUAGE)

    assert outcome.warning == TOO_LONG_FOR_REVIEW_WARNING


@pytest.mark.unit
def test_a_review_that_never_reached_the_model_names_the_review(mocker) -> None:
    """
    Given  a provider whose request fails
    When   the review runs
    Then   the warning says the evaluation failed: the file's proofreading is not blamed
    """
    mocker.patch(
        "langchain_core.prompts.ChatPromptTemplate.from_messages",
        return_value=_FailingChain(),
    )

    outcome = _evaluator(LangChainPort(FakeListChatModel(responses=["ok"]))).evaluate(
        TEXT, LANGUAGE
    )

    assert outcome.text == ""
    assert outcome.warning is not None
    assert outcome.warning.startswith("Evaluation failed (")


@pytest.mark.unit
def test_the_review_is_stored_without_reasoning_or_code_fence() -> None:
    """
    Given  a model that leaks a  thinking block and fences its review
    When   the review runs
    Then   the stored review is only the review text
    """
    port = FakePort(answer(" thinkinghm</think>\n```markdown\n**Good** work\n```"))

    assert _evaluator(port).evaluate(TEXT, LANGUAGE) == EvaluationOutcome(text="**Good** work")


@pytest.mark.unit
def test_a_proofreading_request_of_the_review_step_is_not_worded_as_an_evaluation(mocker) -> None:
    """
    Given  a proofreading request that carries the review step's name (the R exchange)
    When   its provider request fails
    Then   the warning says the correction failed: only a content review words its own failure
    """
    mocker.patch(
        "langchain_core.prompts.ChatPromptTemplate.from_messages",
        return_value=_FailingChain(),
    )

    answer_ = LangChainPort(FakeListChatModel(responses=["ok"])).ask_text(
        StepRequest(step="review", system="s", user="u")
    )

    assert answer_.failure == "transport"
    assert answer_.error.startswith("Correction failed (")
