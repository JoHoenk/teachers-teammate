"""Unit tests for the LangChain helpers of teachers_teammate.infrastructure._llm_invoke."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda
import pytest

from teachers_teammate.infrastructure._llm_invoke import (
    _rejected_request,
    answer_was_cut_off,
    invoke_langchain_structured,
)


class _HttpxStyleError(Exception):
    """An error that, like ``httpx.HTTPStatusError``, keeps its status only on ``response``."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Error response {status}")
        self.response = SimpleNamespace(status_code=status)


class _SdkStyleError(Exception):
    """An error that, like the OpenAI SDK's, carries ``status_code`` itself."""

    def __init__(self, status: int) -> None:
        super().__init__(f"status {status}")
        self.status_code = status


class _FailingModel:
    """A chat model whose bound structured chain raises *error* when it is asked."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def with_structured_output(self, schema: object, **_kwargs: object) -> RunnableLambda:
        def ask(_prompt: object) -> object:
            raise self._error

        return RunnableLambda(ask)


@pytest.mark.parametrize("error_type", [_HttpxStyleError, _SdkStyleError])
@pytest.mark.parametrize(
    ("status", "rejected"),
    [(400, True), (422, True), (401, False), (404, False), (429, False), (500, False)],
)
def test_rejected_request_reads_the_status_wherever_the_sdk_keeps_it(
    error_type: type[Exception], status: int, rejected: bool
) -> None:
    """
    Given  an error whose HTTP status sits on the error itself or only on its response
    When   _rejected_request() judges it
    Then   only 400 and 422 count as the provider rejecting the request
    """
    assert _rejected_request(error_type(status)) is rejected


def test_rejected_request_ignores_an_error_without_a_status() -> None:
    """
    Given  a network error and an error whose response has no integer status
    When   _rejected_request() judges them
    Then   neither is a rejected request
    """
    assert _rejected_request(ConnectionError("down")) is False
    error = Exception("odd")
    error.response = SimpleNamespace(status_code="422")
    assert _rejected_request(error) is False


@pytest.mark.parametrize(
    ("status", "failure"),
    [(422, "unsupported"), (400, "unsupported"), (401, "transport"), (500, "transport")],
)
def test_a_rejected_structured_request_is_unsupported_not_a_transport_failure(
    status: int, failure: str
) -> None:
    """
    Given  a model that takes a schema when it is bound but answers it with an httpx-style
           HTTP error (Mistral raises its status only on ``response``)
    When   invoke_langchain_structured() sends the request
    Then   a 400 or 422 is reported as "unsupported", so the caller falls back to a plain
           answer, and any other status stays a transport failure with the app's warning
    """
    result = invoke_langchain_structured(
        _FailingModel(_HttpxStyleError(status)),  # ty: ignore[invalid-argument-type]
        system="s",
        human="{text}",
        variables={"text": "t"},
        schema=dict,
    )

    assert result.failure == failure
    assert result.parsed is None
    if failure == "transport":
        assert result.error.startswith("Correction failed (")


# ── A cut-off answer ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("metadata", "cut_off"),
    [
        ({"finish_reason": "length"}, True),
        ({"stop_reason": "max_tokens"}, True),
        ({"finish_reason": "MAX_TOKENS"}, True),
        ({"finish_reason": "stop"}, False),
        ({"stop_reason": "end_turn"}, False),
        ({"model_name": "gpt-4o"}, False),
        ({}, False),
    ],
)
def test_a_cut_off_answer_is_read_from_the_providers_finish_reason(
    metadata: dict[str, str], cut_off: bool
) -> None:
    """
    Given  a message whose provider metadata names how its answer ended
    When   answer_was_cut_off() reads it
    Then   it is cut off exactly for the three names of a stopped-at-the-limit answer, and a
           provider that says nothing counts as complete
    """
    assert answer_was_cut_off(SimpleNamespace(response_metadata=metadata)) is cut_off


def test_an_answer_without_provider_metadata_counts_as_complete() -> None:
    """
    Given  a message that carries no response metadata at all
    When   answer_was_cut_off() reads it
    Then   the answer counts as complete, because there is nothing that says otherwise
    """
    assert answer_was_cut_off(SimpleNamespace(content="half")) is False
