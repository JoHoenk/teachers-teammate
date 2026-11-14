"""Unit tests for the exchange as a corrector: the adapter and the sentence-based review."""

from __future__ import annotations

import json

from teachers_teammate.domain.correction_review import align
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote, UnclearNote
from teachers_teammate.domain.proofreading.legacy_review import sentence_review
from teachers_teammate.infrastructure.correction import ChunkingCorrector
from teachers_teammate.infrastructure.proofreading.corrector import (
    ExchangeCorrector,
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
    return ExchangeCorrector(TExchange(FakePort(*script)), level="B1")


def test_the_review_of_an_exchange_is_a_sentence_review_that_aligns_with_the_text() -> None:
    """
    Given  a text with mistakes in two sentences, explained by the exchange
    When   the result is read as the corrector the pipeline runs
    Then   the corrected text is returned and every explanation is matched to its own change by
           the alignment the GUI and the evaluation tools use
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
    assert [s.original for s in outcome.review.sentences] == [
        "Ich habe ein grosse Hund.",
        "Wir gehen zu Schule.",
    ]
    assert outcome.review.sentences[0].corrected == "Ich habe einen großen Hund."
    aligned = align(SOURCE, outcome.text, outcome.review)
    assert aligned.orphan_notes == ()
    assert [change.note.explanation for change in aligned.changes if change.note] == [
        "Der Akkusativ und die Schreibung.",
        "Der Akkusativ und die Schreibung.",
        "zu + der = zur.",
    ]


def test_a_failed_exchange_keeps_the_source_and_says_why() -> None:
    """
    Given  a server that cannot be reached
    When   the corrector proofreads a text
    Then   the outcome is a failure that keeps the source text and carries the warning
    """
    outcome = _corrector(transport("no server")).correct(SOURCE, "German")

    assert outcome.failed is True
    assert outcome.text == SOURCE
    assert outcome.review is None
    assert "no server" in (outcome.warning or "")


def test_unclear_notes_keep_their_wording() -> None:
    """
    Given  an edit-based review with one unclear expression
    When   it is read as a sentence review
    Then   the expression, the reason and the suggestion are kept, and unchanged sentences are
           not listed
    """
    review = CorrectionReview(
        edits=(),
        unclear=(
            UnclearNote(
                raw_start=0,
                raw_end=3,
                expression="Er",
                why="Wer ist gemeint?",
                recommendation="Den Namen nennen.",
                is_suggestion=True,
            ),
        ),
    )

    converted = sentence_review("Er kam.", review)

    assert converted.sentences == ()
    assert (converted.unclear[0].expression, converted.unclear[0].why) == ("Er", "Wer ist gemeint?")
    assert converted.unclear[0].is_suggestion is True


def test_an_edit_without_a_named_sentence_goes_into_the_sentence_that_holds_it() -> None:
    """
    Given  an edit note that names no sentence
    When   the review is read as a sentence review
    Then   the edit lands in the sentence its first hunk lies in
    """
    from teachers_teammate.domain.proofreading.edits import EditPart  # noqa: PLC0415

    raw = "Eins. Zwei fehlerr."
    note = EditNote(
        id=1,
        parts=(EditPart(raw_start=12, raw_end=18, original="fehlerr", corrected="Fehler"),),
        sentences=(),
        category="spelling",
        explanation="Tippfehler.",
    )

    converted = sentence_review(raw, CorrectionReview(edits=(note,)))

    assert [s.original for s in converted.sentences] == ["Zwei fehlerr."]


def test_a_long_text_is_split_by_the_budget_of_the_exchange() -> None:
    """
    Given  an exchange corrector on a port with a small window
    When   it is wrapped in the chunking corrector and asked for a text longer than one request
    Then   the text is split by the exchange's own budget, so each piece is proofread on its own
    """
    from teachers_teammate.domain.context_budget import ContextBudget  # noqa: PLC0415

    port = FakePort(window=ContextBudget(8192))
    corrector = build_exchange_corrector(None, "m", timeout=5.0, num_ctx=8192, port=port)  # ty: ignore[invalid-argument-type]
    limit = corrector.max_text_chars("German")
    assert limit is not None and limit > 0

    chunks = ChunkingCorrector(corrector).split("Ein Satz. " * (limit // 5), "German")

    assert chunks is not None and len(chunks) > 1
