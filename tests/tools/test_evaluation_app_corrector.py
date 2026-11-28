"""Tests of the correctors the tools build: the factory, the pieces, the guard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from typing import cast

import pytest

from teachers_teammate.exceptions import StageError, StageErrorKind
from teachers_teammate.infrastructure.proofreading.chunking import ChunkingCorrector
from teachers_teammate.infrastructure.ollama_client import OllamaClient
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.stage_builder import PipelineComponentFactory, StageBuilder
from teachers_teammate.interfaces import Corrector
from tests.conftest import make_config
from tools.evaluation.core.modes import ALL_STEPS, Exchange, Step
from tools.evaluation.run.app_corrector import (
    NoExplanationCorrector,
    app_corrector_factory,
    innermost,
    pieces_of,
    steps_that_run,
)
from tools.evaluation.run.one_request.correction import NativeOllamaCorrector


def _chain(corrector: Corrector) -> list[type[Corrector]]:
    """Return the types of a corrector and of everything it wraps, outermost first."""
    found = [type(corrector)]
    while isinstance(corrector, ChunkingCorrector):
        corrector = corrector.inner
        found.append(type(corrector))
    return found


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_one_request_factory_builds_the_chunking_wrapper_around_the_one_request_corrector() -> (
    None
):
    """
    Given  a client and the one-request text exchange
    When   the factory builds a corrector
    Then   it is the one-request corrector in the chunking wrapper, asking for the text and at
           the window it was given (the app's proofreading window by default)
    """
    client = OllamaClient("http://x")

    small = app_corrector_factory(
        client, timeout=5.0, exchange=Exchange.ONE_REQUEST_TEXT, steps=(Step.CORRECT,), num_ctx=4096
    )("gemma4:e4b", 0.2)
    large = app_corrector_factory(
        client, timeout=5.0, exchange=Exchange.ONE_REQUEST_TEXT, steps=(Step.CORRECT,), num_ctx=8192
    )("gemma4:e4b", 0.2)
    default = app_corrector_factory(
        client, timeout=5.0, exchange=Exchange.ONE_REQUEST_TEXT, steps=(Step.CORRECT,)
    )("gemma4:e4b", 0.2)

    assert isinstance(small, ChunkingCorrector)
    assert isinstance(small.inner, NativeOllamaCorrector)
    assert small.inner.output_mode == "text"
    assert small.inner.max_text_chars("German") < large.inner.max_text_chars("German")
    assert default.inner.max_text_chars("German") == large.inner.max_text_chars("German")


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_exchange_factory_builds_the_same_chain_as_the_stage_builder(tmp_path: Path) -> None:
    """
    Given  an Ollama proofreading config
    When   the stage builder and the tools' exchange factory each build a corrector
    Then   the chains of types agree, so a change of how the app wraps its corrector (a new
           decorator, another order) is found here instead of silently measuring something else
    """
    from teachers_teammate.infrastructure.proofreading.corrector import (  # noqa: PLC0415
        ExchangeCorrector,
    )

    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="gemma4:e4b",
        anonymization_enabled=False,
    )
    client = OllamaClient("http://capture.invalid")

    built, _ = StageBuilder(
        config=config, factory=PipelineComponentFactory(), model_store=ModelStore(tmp_path / "m")
    ).build_correction_and_anonymizer(ollama_client=client)
    ours = app_corrector_factory(
        client, timeout=5.0, exchange=Exchange.TEXT_FIRST, steps=ALL_STEPS
    )("gemma4:e4b", 0.2)

    assert built is not None
    assert _chain(ours) == _chain(built) == [ChunkingCorrector, ExchangeCorrector]


def test_the_factory_refuses_an_exchange_it_has_no_corrector_for() -> None:
    """
    Given  the POC's sentence-walk exchange, which the tools do not build
    When   the factory is asked for a corrector in it
    Then   it is refused, naming the exchange and the ones it does build
    """
    with pytest.raises(
        ValueError, match=r"no corrector for the exchange 'sentence-walk'.*text-first"
    ):
        app_corrector_factory(
            OllamaClient("http://x"),
            timeout=5.0,
            exchange=Exchange.SENTENCE_WALK,
            steps=ALL_STEPS,
        )


def test_the_steps_that_run_drop_the_unclear_check_a_profile_switches_off() -> None:
    """
    Given  a profile with the unclear check on and one with it off
    When   the steps of the exchange are narrowed
    Then   the first keeps all three, the second drops step ③, and the order is kept
    """
    from teachers_teammate.domain.request_profile import RequestProfile  # noqa: PLC0415

    assert steps_that_run(ALL_STEPS, RequestProfile()) == ALL_STEPS
    assert steps_that_run(ALL_STEPS, RequestProfile(unclear_check=False)) == (
        Step.CORRECT,
        Step.EXPLAIN,
    )
    assert steps_that_run((Step.CORRECT,), RequestProfile(unclear_check=False)) == (Step.CORRECT,)


def test_innermost_looks_through_the_wrapper_and_nothing_else() -> None:
    """
    Given  a wrapped corrector, a bare one, and a mock that answers every attribute
    When   the innermost corrector is asked for
    Then   the wrapper is looked through; the others are returned as they are, without looping
    """
    inner = NativeOllamaCorrector("m", OllamaClient("http://x"))
    mock = MagicMock()

    assert innermost(ChunkingCorrector(inner)) is inner
    assert innermost(inner) is inner
    assert innermost(mock) is mock


def _budgeted(max_chars) -> Corrector:
    """Return the app's chunking wrapper around a stand-in with the request budget *max_chars*."""
    inner = MagicMock()
    inner.max_text_chars.return_value = max_chars
    return ChunkingCorrector(inner)


def test_a_text_within_the_budget_is_one_piece_even_when_it_is_blank() -> None:
    """
    Given  a budget larger than the text, and a text of only blanks
    When   the pieces are counted
    Then   it is one piece, as the wrapper sends it whole, and never 0
    """
    corrector = _budgeted(100)

    assert pieces_of(corrector, "Short text.", "German") == 1
    assert pieces_of(corrector, "   \n  ", "German") == 1


def test_a_text_over_the_budget_counts_its_non_blank_pieces() -> None:
    """
    Given  a budget of 20 characters and a text of three paragraphs
    When   the pieces are counted
    Then   the figure is the number of non-blank pieces of the app's split, more than one
    """
    text = "First paragraph here.\n\nSecond paragraph here.\n\nThird paragraph here."

    assert pieces_of(_budgeted(25), text, "German") >= 3


@pytest.mark.parametrize("budget", [None, 0, -5])
def test_a_corrector_without_a_budget_sends_every_text_whole(budget) -> None:
    """
    Given  a corrector that reports no budget, or an unusable one
    When   the pieces of a long text are counted
    Then   it is one piece, as the wrapper sends it
    """
    assert pieces_of(_budgeted(budget), "x" * 500, "German") == 1


def test_a_corrector_that_does_not_chunk_sends_every_text_whole() -> None:
    """
    Given  a corrector that is not the chunking wrapper
    When   the pieces of a long text are counted
    Then   it is one piece
    """
    whole = MagicMock()
    whole.pieces.return_value = None

    assert pieces_of(whole, "x" * 500, "German") == 1


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_exchange_factory_builds_the_exchange_corrector_in_the_chunking_wrapper() -> None:
    """
    Given  a client and a request profile
    When   the exchange factory builds a corrector
    Then   it is the app's exchange corrector in the chunking wrapper, carrying the profile it
           was given, and its budget follows the window like the app's other correctors
    """
    from teachers_teammate.domain.request_profile import RequestProfile  # noqa: PLC0415
    from teachers_teammate.infrastructure.proofreading.corrector import (  # noqa: PLC0415
        ExchangeCorrector,
    )

    client = OllamaClient("http://x")

    small = app_corrector_factory(
        client, timeout=5.0, exchange=Exchange.TEXT_FIRST, steps=ALL_STEPS, num_ctx=4096
    )("gemma4:e4b", 0.2)
    large = app_corrector_factory(
        client,
        timeout=5.0,
        exchange=Exchange.TEXT_FIRST,
        steps=ALL_STEPS,
        num_ctx=8192,
        profile=RequestProfile(unclear_check=False),
    )("gemma4:e4b", 0.2)

    assert isinstance(small, ChunkingCorrector)
    assert isinstance(small.inner, ExchangeCorrector)
    assert small.inner.max_text_chars("German") < large.inner.max_text_chars("German")


def _as_client(stand_in: _OneStepClient) -> OllamaClient:
    """Return the stand-in as the client the factory is typed against: it only calls ``chat``."""
    return cast(OllamaClient, stand_in)


class _OneStepClient:
    """A client that answers step one and fails the test when any other step asks."""

    last_stats = None

    def __init__(self, corrected: str = "Ich habe einen Hund.", *, fail: bool = False) -> None:
        self.corrected = corrected
        self.fail = fail
        self.systems: list[str] = []

    def chat(self, model, prompt, *, system="", **_):
        self.systems.append(system)
        if self.fail:
            raise ConnectionError("no server")
        return self.corrected


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_correct_only_corrector_sends_step_one_alone_and_returns_the_text_without_a_review() -> (
    None
):
    """
    Given  the exchange built to send step one only
    When   a text is proofread
    Then   exactly one request goes out, with step one's prompt, and the outcome is the corrected
           text with no review and no warning about the explanations that were never asked for
    """
    from teachers_teammate.domain.correction_prompts import CORRECT_SYSTEM  # noqa: PLC0415

    client = _OneStepClient()
    corrector = app_corrector_factory(
        _as_client(client), timeout=5.0, exchange=Exchange.TEXT_FIRST, steps=(Step.CORRECT,)
    )("gemma4:e4b", 0.2)

    outcome = corrector.correct("Ich habe ein Hund.", "German")

    assert client.systems == [CORRECT_SYSTEM]
    assert outcome.text == "Ich habe einen Hund."
    assert outcome.review is None
    assert outcome.warning is None
    assert not outcome.failed


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_correct_only_file_whose_step_one_failed_keeps_its_failure() -> None:
    """
    Given  the exchange built to send step one only, and a server that does not answer
    When   a text is proofread
    Then   the proofreading fails with its reason, as in the full exchange
    """
    corrector = app_corrector_factory(
        _as_client(_OneStepClient(fail=True)),
        timeout=5.0,
        exchange=Exchange.TEXT_FIRST,
        steps=(Step.CORRECT,),
    )("gemma4:e4b", 0.2)

    with pytest.raises(StageError) as raised:
        corrector.correct("Ich habe ein Hund.", "German")

    assert raised.value.kind == StageErrorKind.PROVIDER
    assert raised.value.stage == "correction"
    assert "no server" in str(raised.value)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_correct_only_corrector_splits_a_long_text_exactly_as_the_exchange_does() -> None:
    """
    Given  the full exchange and the step-① one, built on the same window
    When   they are asked how much text one request carries
    Then   the budgets are equal, so both send the same pieces and the runs pair text for text
    """
    client = OllamaClient("http://x")

    full = app_corrector_factory(
        client, timeout=5.0, exchange=Exchange.TEXT_FIRST, steps=ALL_STEPS, num_ctx=8192
    )("m", 0.2)
    only = app_corrector_factory(
        client, timeout=5.0, exchange=Exchange.TEXT_FIRST, steps=(Step.CORRECT,), num_ctx=8192
    )("m", 0.2)

    assert isinstance(full, ChunkingCorrector) and isinstance(only, ChunkingCorrector)
    assert only.inner.max_text_chars("German") == full.inner.max_text_chars("German")


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_run_without_step_two_wraps_the_exchange_in_the_no_explanation_corrector() -> None:
    """
    Given  the exchange asked for steps ① and ③, so step ② never runs
    When   the factory builds the corrector
    Then   the exchange is wrapped in the corrector that drops what the skipped step leaves
           behind, so the answer is what step ① corrected and not a warning about step ②
    """
    corrector = app_corrector_factory(
        OllamaClient("http://x"),
        timeout=5.0,
        exchange=Exchange.TEXT_FIRST,
        steps=(Step.CORRECT, Step.UNCLEAR),
    )("m", 0.2)

    assert isinstance(corrector, ChunkingCorrector)
    assert isinstance(corrector.inner, NoExplanationCorrector)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_correct_only_file_that_lost_text_keeps_the_count_of_what_was_put_back() -> None:
    """
    Given  the exchange built to send step one only, and a model that answers only the second of
           two paragraphs, twice
    When   a text is proofread
    Then   the app puts the first paragraph back and the outcome still counts it as left out, so
           the run row shows the drop and the gate's lost-text check can see it
    """
    first = " ".join(f"Am Montag {n} gehe ich mit meinem Hund in den Park." for n in range(12))
    second = " ".join(f"Am Freitag {n} spiele ich mit meinem Bruder Fußball." for n in range(12))
    corrector = app_corrector_factory(
        _as_client(_OneStepClient(second)),
        timeout=5.0,
        exchange=Exchange.TEXT_FIRST,
        steps=(Step.CORRECT,),
    )("gemma4:e4b", 0.2)

    outcome = corrector.correct(f"{first}\n\n{second}", "German")

    assert not outcome.failed
    assert first in outcome.text
    assert outcome.left_out == 1
    assert outcome.fell_back
