"""Unit tests for teachers_teammate.evaluation."""

from __future__ import annotations

import pytest

from teachers_teammate.infrastructure.evaluation import LangChainEvaluator


class _PassThroughChain:
    def __or__(self, _other):
        return self

    def invoke(self, payload):
        return f"{payload['language']}::{payload['text']}"


class _FailingChain:
    def __or__(self, _other):
        return self

    def invoke(self, _payload):
        raise RuntimeError("chain failed")


@pytest.mark.unit
def test_langchain_evaluator_returns_chain_output(monkeypatch) -> None:
    """
    Given  a LangChainEvaluator with a chain that returns text
    When   evaluate() is called
    Then   the returned value is the chain output
    """
    monkeypatch.setattr(
        "langchain_core.prompts.ChatPromptTemplate.from_messages",
        lambda _messages: _PassThroughChain(),
    )
    monkeypatch.setattr("langchain_core.output_parsers.StrOutputParser", lambda: object())

    evaluator = LangChainEvaluator(llm=object(), prompt="sys")  # ty: ignore[invalid-argument-type]  # test passes a stub in place of a BaseChatModel
    result = evaluator.evaluate("corrected", "English")

    assert result == ("English::corrected", None)


@pytest.mark.unit
def test_langchain_evaluator_returns_empty_on_exception(monkeypatch) -> None:
    """
    Given  a LangChainEvaluator whose chain invocation raises
    When   evaluate() is called
    Then   an empty string is returned and a warning message is in the result
    """
    monkeypatch.setattr(
        "langchain_core.prompts.ChatPromptTemplate.from_messages",
        lambda _messages: _FailingChain(),
    )
    monkeypatch.setattr("langchain_core.output_parsers.StrOutputParser", lambda: object())

    evaluator = LangChainEvaluator(llm=object(), prompt="sys")  # ty: ignore[invalid-argument-type]  # test passes a stub in place of a BaseChatModel
    result_text, warning = evaluator.evaluate("corrected", "English")

    assert result_text == ""
    assert warning is not None
    assert "Evaluation failed" in warning


@pytest.mark.unit
def test_langchain_evaluator_uses_provided_system_prompt(monkeypatch) -> None:
    """
    Given  a LangChainEvaluator initialised with a custom system prompt
    When   evaluate() is called
    Then   the prompt is passed to ChatPromptTemplate.from_messages as the system message
    """
    captured_messages: list = []

    def fake_from_messages(messages):
        captured_messages.extend(messages)
        return _PassThroughChain()

    monkeypatch.setattr(
        "langchain_core.prompts.ChatPromptTemplate.from_messages",
        fake_from_messages,
    )
    monkeypatch.setattr("langchain_core.output_parsers.StrOutputParser", lambda: object())

    evaluator = LangChainEvaluator(llm=object(), prompt="my custom evaluator prompt")  # ty: ignore[invalid-argument-type]  # test passes a stub in place of a BaseChatModel
    evaluator.evaluate("text", "English")

    assert any("my custom evaluator prompt" in str(m) for m in captured_messages)


# ── NativeOllamaEvaluator ──────────────────────────────────────────────────


def test_native_ollama_evaluator_passes_temperature_in_options(mocker) -> None:
    """
    Given  a NativeOllamaEvaluator constructed with temperature=0.4
    When   evaluate() is called
    Then   OllamaClient.chat receives options={"temperature": 0.4}
    """
    from teachers_teammate.infrastructure.evaluation import NativeOllamaEvaluator  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="eval result")
    client = OllamaClient("http://localhost:11434")
    evaluator = NativeOllamaEvaluator(model="m", client=client, temperature=0.4)

    result, warning = evaluator.evaluate("corrected", "English")

    assert result == "eval result"
    assert warning is None
    call_kwargs = mock_chat.call_args.kwargs
    assert call_kwargs["options"]["temperature"] == 0.4


def test_native_ollama_evaluator_default_temperature(mocker) -> None:
    """
    Given  a NativeOllamaEvaluator constructed without an explicit temperature
    When   evaluate() is called
    Then   OllamaClient.chat receives options={"temperature": 0.7}
    """
    from teachers_teammate.infrastructure.evaluation import NativeOllamaEvaluator  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="ok")
    client = OllamaClient("http://localhost:11434")
    evaluator = NativeOllamaEvaluator(model="m", client=client)

    evaluator.evaluate("corrected", "English")

    call_kwargs = mock_chat.call_args.kwargs
    assert call_kwargs["options"]["temperature"] == 0.7
