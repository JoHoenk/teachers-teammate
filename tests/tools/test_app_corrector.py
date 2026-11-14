"""Tests of the app's own corrector as the tools build it: the factory, the pieces, the guard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from teachers_teammate.infrastructure.correction import ChunkingCorrector, NativeOllamaCorrector
from teachers_teammate.infrastructure.ollama_utils import OllamaClient
from teachers_teammate.infrastructure.stage_builder import PipelineComponentFactory, StageBuilder
from teachers_teammate.interfaces import Corrector
from tests.conftest import make_config
from tools.model_eval.answer_mode import AnswerMode
from tools.model_eval.app_corrector import (
    app_corrector_factory,
    exchange_corrector_factory,
    innermost,
    pieces_of,
)


def _chain(corrector: Corrector) -> list[type[Corrector]]:
    """Return the types of a corrector and of everything it wraps, outermost first."""
    found = [type(corrector)]
    while isinstance(corrector, ChunkingCorrector):
        corrector = corrector.inner
        found.append(type(corrector))
    return found


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_app_corrector_factory_builds_the_chunking_wrapper_around_the_apps_corrector() -> None:
    """
    Given  a client and a mode
    When   the factory builds a corrector
    Then   it is the app's own corrector in the chunking wrapper, asking in that mode and at the
           window it was given (the app's proofreading window by default)
    """
    client = OllamaClient("http://x")

    small = app_corrector_factory(client, timeout=5.0, mode=AnswerMode.TEXT, num_ctx=4096)(
        "gemma4:e4b", 0.2
    )
    large = app_corrector_factory(client, timeout=5.0, mode=AnswerMode.TEXT, num_ctx=8192)(
        "gemma4:e4b", 0.2
    )
    default = app_corrector_factory(client, timeout=5.0, mode=AnswerMode.REVIEW)("gemma4:e4b", 0.2)

    assert isinstance(small, ChunkingCorrector)
    assert isinstance(small.inner, NativeOllamaCorrector)
    assert small.inner.output_mode == "text"
    assert small.inner.max_text_chars("German") < large.inner.max_text_chars("German")
    assert default.inner.max_text_chars("German") == large.inner.max_text_chars("German")


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_factory_builds_the_same_chain_as_the_stage_builder(tmp_path: Path) -> None:
    """
    Given  an Ollama proofreading config
    When   the stage builder and the tools' factory each build a corrector
    Then   the chains of types agree, so a change of how the app wraps its corrector (a new
           decorator, another order) is found here instead of silently measuring something else
    """
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="gemma4:e4b",
        anonymization_enabled=False,
    )
    client = OllamaClient("http://capture.invalid")

    built, _ = StageBuilder(
        config=config, factory=PipelineComponentFactory()
    ).build_correction_and_anonymizer(ollama_client=client)
    ours = app_corrector_factory(client, timeout=5.0, mode=AnswerMode.REVIEW)("gemma4:e4b", 0.2)

    assert built is not None
    assert _chain(ours) == _chain(built) == [ChunkingCorrector, NativeOllamaCorrector]


def test_the_factory_refuses_a_mode_the_corrector_cannot_be_asked_in() -> None:
    """
    Given  the two-step mode, which asks the corrector in text mode first
    When   the factory is asked for a corrector in it
    Then   it is refused, naming the mode and the work item that adds it
    """
    with pytest.raises(
        ValueError, match="two-step asks the corrector in text mode, which nothing runs yet"
    ):
        app_corrector_factory(OllamaClient("http://x"), timeout=5.0, mode=AnswerMode.TWO_STEP)


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
    assert pieces_of(MagicMock(), "x" * 500, "German") == 1


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_exchange_factory_builds_the_exchange_corrector_in_the_chunking_wrapper() -> None:
    """
    Given  a client and a request profile
    When   the exchange factory builds a corrector
    Then   it is the app's exchange corrector in the chunking wrapper, carrying the profile it
           was given, and its budget follows the window like the app's other correctors
    """
    from teachers_teammate.domain.model_catalog import RequestProfile  # noqa: PLC0415
    from teachers_teammate.infrastructure.proofreading.corrector import (  # noqa: PLC0415
        ExchangeCorrector,
    )

    client = OllamaClient("http://x")

    small = exchange_corrector_factory(client, timeout=5.0, num_ctx=4096)("gemma4:e4b", 0.2)
    large = exchange_corrector_factory(
        client, timeout=5.0, num_ctx=8192, profile=RequestProfile(unclear_check=False)
    )("gemma4:e4b", 0.2)

    assert isinstance(small, ChunkingCorrector)
    assert isinstance(small.inner, ExchangeCorrector)
    assert small.inner.max_text_chars("German") < large.inner.max_text_chars("German")
