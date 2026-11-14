"""Unit tests for the exchange chain (``infrastructure/proofreading/exchanges/fallback.py``)."""

from __future__ import annotations

from teachers_teammate.domain.model_catalog import RequestProfile
from teachers_teammate.domain.proofreading.result import ExchangeKind, FailureKind, StepReport
from teachers_teammate.infrastructure.proofreading.exchanges.base import (
    ExchangeCost,
    ExchangeResult,
    ProofreadContext,
)
from teachers_teammate.infrastructure.proofreading.exchanges.fallback import FallbackExchange


class _Stub:
    """An exchange that returns one prepared result and counts its calls."""

    kind: ExchangeKind = "T"

    def __init__(self, result: ExchangeResult) -> None:
        self._result = result
        self.calls = 0

    def proofread(self, source: str, ctx: ProofreadContext) -> ExchangeResult:
        """Return the prepared result."""
        self.calls += 1
        return self._result

    def max_text_chars(self, ctx: ProofreadContext) -> int | None:
        """Return no limit."""
        return None

    def cost_model(self) -> ExchangeCost:
        """Return an empty cost."""
        return ExchangeCost(requests_per_page={}, answer_tokens_per_100_words={})


def _ctx() -> ProofreadContext:
    """Return a context for German."""
    return ProofreadContext(language="German", profile=RequestProfile())


def _failed(warning: str, failure: FailureKind = "invalid") -> ExchangeResult:
    """Return the result of an exchange that kept the source text."""
    return ExchangeResult(
        text="Quelle",
        failed=True,
        warnings=(warning,),
        reports=(StepReport(step="correct", failure=failure),),
    )


def test_the_fallback_runs_when_the_primary_could_not_use_the_answer() -> None:
    """
    Given  a primary whose answer was unusable and a fallback that corrects the text
    When   the chain proofreads
    Then   the fallback's result is returned, without the primary's "text kept" warning,
           and both exchanges' reports are kept
    """
    primary = _Stub(_failed("Proofreading failed; keeping original text."))
    fallback = _Stub(ExchangeResult(text="Korrigiert", reports=(StepReport(step="correct"),)))

    result = FallbackExchange(primary, fallback).proofread("Quelle", _ctx())

    assert result.text == "Korrigiert"
    assert result.warnings == ()
    assert len(result.reports) == 2


def test_the_fallback_does_not_ask_a_server_that_could_not_be_reached() -> None:
    """
    Given  a primary whose request never reached the model
    When   the chain proofreads
    Then   the primary's failure is the result and the fallback is not asked
    """
    primary = _Stub(_failed("Connection refused; keeping original text.", failure="transport"))
    fallback = _Stub(ExchangeResult(text="Korrigiert"))

    result = FallbackExchange(primary, fallback).proofread("Quelle", _ctx())

    assert result.failed
    assert fallback.calls == 0
    assert result.warnings == ("Connection refused; keeping original text.",)


def test_when_both_fail_each_reason_is_shown_once() -> None:
    """
    Given  a primary and a fallback that both failed, with one warning in common
    When   the chain proofreads
    Then   the warnings are the primary's followed by the fallback's new ones
    """
    primary = _Stub(_failed("Proofreading failed; keeping original text."))
    fallback = _Stub(_failed("Proofreading failed; keeping original text."))

    result = FallbackExchange(primary, fallback).proofread("Quelle", _ctx())

    assert result.failed
    assert result.warnings == ("Proofreading failed; keeping original text.",)
