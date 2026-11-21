"""Unit tests for the exchange as a corrector: the adapter and the review it returns."""

from __future__ import annotations

import json

import pytest

from teachers_teammate.domain.proofreading.review import align, fits
from teachers_teammate.exceptions import StageError, StageErrorKind
from teachers_teammate.infrastructure.proofreading.chunking import ChunkingCorrector
from teachers_teammate.infrastructure.proofreading.corrector import (
    ExchangeCorrector,
    ProofreadingSettings,
    build_exchange_corrector,
)
from teachers_teammate.infrastructure.proofreading.exchanges import TExchange
from tests.fakes.fake_port import FakePort, answer, transport

SOURCE = "Ich habe ein grosse Hund. Er ist nett. Wir gehen zu Schule."
CORRECTED = "Ich habe einen großen Hund. Er ist nett. Wir gehen zur Schule."


def _notes(*notes: tuple[int, str, str]) -> str:
    return json.dumps(
        {
            "edits": [
                {"id": number, "category": category, "explanation": explanation}
                for number, category, explanation in notes
            ]
        }
    )


def _corrector(*script) -> ExchangeCorrector:
    return ExchangeCorrector(
        TExchange(FakePort(*script)), settings=ProofreadingSettings(level="B1")
    )


def test_the_review_of_an_exchange_is_the_edit_based_review_of_the_exchange() -> None:
    """
    Given  a text with mistakes in two sentences, explained by the exchange
    When   the result is read as the corrector the pipeline runs
    Then   the corrected text is returned and the review is the exchange's own: its offsets are
           in the plain source text, and every explanation sits on the words it is about
    """
    corrector = _corrector(
        answer(CORRECTED),
        answer(
            _notes(
                (1, "grammar", "Der Akkusativ und die Schreibung."),
                (2, "grammar", "zu + der = zur."),
            )
        ),
        answer(json.dumps({"unclear": []})),
    )

    outcome = corrector.correct(SOURCE, "German")

    assert outcome.text == CORRECTED
    assert outcome.failed is False
    assert outcome.review is not None
    assert fits(outcome.review, SOURCE)
    aligned = align(SOURCE, outcome.text, outcome.review)
    assert aligned.orphan_notes == ()
    assert [change.note.explanation for change in aligned.changes if change.note] == [
        "Der Akkusativ und die Schreibung.",
        "Der Akkusativ und die Schreibung.",
        "zu + der = zur.",
    ]


def test_a_failed_exchange_raises_the_failure_of_the_stage() -> None:
    """
    Given  a server that cannot be reached
    When   the corrector proofreads a text
    Then   the proofreading stage fails with its reason instead of a half-corrected outcome
    """
    with pytest.raises(StageError) as raised:
        _corrector(transport("no server")).correct(SOURCE, "German")

    assert raised.value.kind == StageErrorKind.PROVIDER
    assert raised.value.stage == "correction"
    assert "no server" in str(raised.value)


def test_an_unusable_answer_is_an_outcome_that_keeps_the_source() -> None:
    """
    Given  a model that answers with nothing at all (the request itself went through)
    When   the corrector proofreads a text
    Then   the outcome is failed, keeps the source and says why: the file is still written, but
           the source is never stored as a finished proofreading
    """
    outcome = _corrector(answer("")).correct(SOURCE, "German")

    assert outcome.failed is True
    assert outcome.text == SOURCE
    assert outcome.review is None
    assert outcome.warning


def test_a_long_text_is_split_by_the_budget_of_the_exchange() -> None:
    """
    Given  an exchange corrector on a port with a small window
    When   it is wrapped in the chunking corrector and asked for a text longer than one request
    Then   the text is split by the exchange's own budget, so each piece is proofread on its own
    """
    from teachers_teammate.domain.context_budget import ContextBudget  # noqa: PLC0415

    port = FakePort(window=ContextBudget(8192))
    corrector = build_exchange_corrector(port)
    limit = corrector.max_text_chars("German")
    assert limit is not None and limit > 0

    chunks = ChunkingCorrector(corrector).split("Ein Satz. " * (limit // 5), "German")

    assert chunks is not None and len(chunks) > 1


def test_corrections_only_sends_step_one_alone() -> None:
    """
    Given  a run set to corrections only
    When   the corrector proofreads a text
    Then   one request is sent, the corrected text comes back without a review, and the text
           budget is the one of step ① alone, so a corrections-only text goes out in fewer pieces
    """
    from teachers_teammate.domain.context_budget import ContextBudget  # noqa: PLC0415

    port = FakePort(answer(CORRECTED), window=ContextBudget(8192))
    corrector = ExchangeCorrector(
        TExchange(port), settings=ProofreadingSettings(corrections_only=True)
    )

    outcome = corrector.correct(SOURCE, "German")

    assert [request.step for request in port.requests] == ["correct"]
    assert outcome.text == CORRECTED
    assert outcome.review is None
    assert outcome.failed is False
    full = build_exchange_corrector(FakePort(window=ContextBudget(8192))).max_text_chars("German")
    limited = corrector.max_text_chars("German")
    assert full is not None
    assert limited is not None
    assert limited >= full


def test_a_failed_explain_batch_marks_the_answer_as_partial() -> None:
    """
    Given  a text with mistakes whose explanations the model never answers
    When   the corrector proofreads it
    Then   the corrected text is returned and the outcome says the answer is partial, so the
           speed sample of this file is dropped
    """
    corrector = _corrector(
        answer(CORRECTED), transport("no server"), answer(json.dumps({"unclear": []}))
    )

    outcome = corrector.correct(SOURCE, "German")

    assert outcome.text == CORRECTED
    assert outcome.failed is False
    assert outcome.fell_back is True
    assert outcome.warning is not None


def test_a_stop_during_the_explanations_reaches_the_caller() -> None:
    """
    Given  a port that "Stop now" cuts off while step ② is asked
    When   the corrector proofreads the text
    Then   RunStoppedError reaches the caller, so the file is stopped and nothing is stored
    """
    from teachers_teammate.exceptions import RunStoppedError  # noqa: PLC0415

    class _StoppingPort(FakePort):
        """A port that refuses step ② as a stopped run does."""

        def ask_structured(self, req):
            """Raise for step ②, answer everything else from the script."""
            if req.step == "explain":
                raise RunStoppedError
            return super().ask_structured(req)

    corrector = ExchangeCorrector(TExchange(_StoppingPort(answer(CORRECTED))))

    with pytest.raises(RunStoppedError):
        corrector.correct(SOURCE, "German")


def test_only_the_failure_warnings_of_steps_two_and_three_mark_an_answer_partial() -> None:
    """Given the warnings an exchange can return,
    When   they are checked for a missing part of the answer,
    Then   only the failure warnings of steps ② and ③ count, not a formatting warning.
    """
    from teachers_teammate.infrastructure.proofreading.corrector import _partial  # noqa: PLC0415

    assert _partial(()) is False
    assert _partial(("Part of the text lost its formatting.",)) is False
    assert _partial(("Unclear wording could not be checked for this text.",)) is True
    assert _partial(("Explanations are missing for part of the text.",)) is True
