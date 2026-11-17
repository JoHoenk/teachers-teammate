"""Tests of the baseline's review as the edit-based review, and of the rows it produces."""

from __future__ import annotations

import json

from tools.model_eval.one_request.sentence_review import (
    ChangeNote,
    CorrectionReview as SentenceReview,
    SentenceNote,
    UnclearNote as SentenceUnclearNote,
    align as align_sentences,
)
from teachers_teammate.domain.proofreading.review import align
from teachers_teammate.infrastructure.proofreading.corrector import (
    ExchangeCorrector,
    ProofreadingSettings,
)
from teachers_teammate.infrastructure.proofreading.exchanges import TExchange
from tests.fakes.fake_port import FakePort, answer
from tools.model_eval.answer_mode import AnswerMode
from tools.model_eval.comparison import WritingModelComparison
from tools.model_eval.one_request.edit_review import edit_review
from tools.model_eval.results import TestRecord

SOURCE = "Ich habe ein grosse Hund. Er ist nett. Wir gehen zu Schule."
CORRECTED = "Ich habe einen großen Hund. Er ist nett. Wir gehen zur Schule."

# One hunk per edit, so the sentence alignment and the edit alignment can be compared directly.
SIMPLE_SOURCE = "Ich habe ein Hund. Wir gehen zu Schule."
SIMPLE_CORRECTED = "Ich habe einen Hund. Wir gehen zur Schule."


def _sentence_review() -> SentenceReview:
    """Return the baseline's answer for SOURCE → CORRECTED."""
    return SentenceReview(
        sentences=(
            SentenceNote(
                original="Ich habe ein grosse Hund.",
                corrected="Ich habe einen großen Hund.",
                changes=(ChangeNote("grosse", "großen", "grammar", "Der Akkusativ."),),
            ),
            SentenceNote(
                original="Wir gehen zu Schule.",
                corrected="Wir gehen zur Schule.",
                changes=(ChangeNote("zu", "zur", "grammar", "zu + der = zur."),),
            ),
        )
    )


def test_every_hunk_keeps_the_note_the_sentence_alignment_gave_it() -> None:
    """
    Given  a sentence answer for a text with two mistakes, one hunk each
    When   it is read as the edit-based review and aligned again
    Then   every hunk carries the same category and explanation as before
    """
    review = _sentence_review()
    before = align_sentences(SIMPLE_SOURCE, SIMPLE_CORRECTED, review)
    after = align(
        SIMPLE_SOURCE, SIMPLE_CORRECTED, edit_review(SIMPLE_SOURCE, SIMPLE_CORRECTED, review)
    )

    assert len(after.changes) == len(before.changes) == 2
    assert [change.hunk for change in after.changes] == [change.hunk for change in before.changes]
    assert [change.category for change in after.changes] == [
        change.category for change in before.changes
    ]
    assert [change.explanation for change in after.changes] == [
        change.explanation for change in before.changes
    ]
    assert after.orphan_notes == ()


def test_an_edit_of_two_hunks_carries_its_note_on_both() -> None:
    """
    Given  a sentence answer whose one note explains two hunks of the word diff
    When   it is read as the edit-based review and aligned again
    Then   both hunks carry the note, because the teacher reads them as one change
    """
    review = _sentence_review()
    before = align_sentences(SOURCE, CORRECTED, review)
    after = align(SOURCE, CORRECTED, edit_review(SOURCE, CORRECTED, review))

    assert [change.explanation for change in before.changes] == [
        "",
        "Der Akkusativ.",
        "zu + der = zur.",
    ]
    assert [change.explanation for change in after.changes] == [
        "Der Akkusativ.",
        "Der Akkusativ.",
        "zu + der = zur.",
    ]


def test_the_edits_are_numbered_in_text_order() -> None:
    """
    Given  a sentence answer with two changed sentences
    When   it is read as the edit-based review
    Then   the edits are numbered 1..k and their parts are the hunks of the word diff
    """
    review = edit_review(SOURCE, CORRECTED, _sentence_review())

    assert [note.id for note in review.edits] == [1, 2]
    assert [note.original for note in review.edits] == ["ein grosse", "zu"]
    assert [note.corrected for note in review.edits] == ["einen großen", "zur"]


def test_an_unclear_quote_is_anchored_where_it_was_found() -> None:
    """
    Given  an unclear expression the baseline quoted from the student's text
    When   it is read as the edit-based review
    Then   its span covers exactly those words
    """
    review = SentenceReview(
        unclear=(
            SentenceUnclearNote(
                expression="Er ist nett", why="Wer ist gemeint?", recommendation="Den Namen nennen."
            ),
        )
    )

    converted = edit_review(SOURCE, CORRECTED, review)

    start = SOURCE.index("Er ist nett")
    assert (converted.unclear[0].raw_start, converted.unclear[0].raw_end) == (
        start,
        start + len("Er ist nett"),
    )
    assert converted.unclear[0].why == "Wer ist gemeint?"


def test_an_unclear_quote_that_is_not_in_the_text_keeps_equal_ends() -> None:
    """
    Given  an unclear expression the baseline quoted but the text does not contain
    When   it is read as the edit-based review
    Then   its span is empty, which is how a review says it has no place in the text
    """
    review = SentenceReview(
        unclear=(SentenceUnclearNote(expression="nicht da", why="Warum?", recommendation="Etwas."),)
    )

    converted = edit_review(SOURCE, CORRECTED, review)

    assert (converted.unclear[0].raw_start, converted.unclear[0].raw_end) == (0, 0)


def test_a_markdown_text_is_read_in_its_plain_form() -> None:
    """
    Given  a Markdown text whose plain form drops the notation
    When   it is read as the edit-based review
    Then   the offsets are those of the plain text, which is what a stored review is anchored on
    """
    source = "I **has** a dog."
    corrected = "I **have** a dog."
    review = SentenceReview(
        sentences=(
            SentenceNote(
                original="I **has** a dog.",
                corrected="I **have** a dog.",
                changes=(ChangeNote("has", "have", "grammar", "Third person singular."),),
            ),
        )
    )

    converted = edit_review(source, corrected, review, "markdown")

    assert converted.edits[0].parts[0].raw_start == 2
    assert converted.edits[0].parts[0].original == "has"


# ── the rows of the exchange path ─────────────────────────────────────────


def _notes(*notes: tuple[int, str, str]) -> str:
    """Return the step-two answer of the exchange for *notes*."""
    return json.dumps(
        {
            "edits": [
                {"id": number, "category": category, "explanation": explanation}
                for number, category, explanation in notes
            ]
        }
    )


def _exchange(*script) -> ExchangeCorrector:
    """Return the app's exchange corrector over a scripted port."""
    return ExchangeCorrector(
        TExchange(FakePort(*script)), settings=ProofreadingSettings(level="B1")
    )


def test_the_rows_of_the_exchange_path_stay_as_they_are() -> None:
    """
    Given  the invented corpus and a recorded exchange answer
    When   the comparison runs the exchange path over it
    Then   the row's review fields are exactly what the tools stored before the readers moved
    """
    record = TestRecord(
        id="en-1",
        language="en",
        kind="errors",
        origin="invented",
        source="I has a dog.",
        reference="I have a dog.",
    )
    comparison = WritingModelComparison(
        lambda model, temperature: _exchange(
            answer("I have a dog."),
            answer(_notes((1, "grammar", "Third person singular."))),
            answer(json.dumps({"unclear": []})),
        ),
        clock=lambda: 0.0,
        mode=AnswerMode.EXCHANGE,
    )

    row = next(comparison.run([record], model="m", temperature=0.0))

    assert row.output == "I have a dog."
    assert row.has_review is True
    assert row.notes == (
        {
            "sentence": "I have a dog.",
            "original": "has",
            "corrected": "have",
            "category": "grammar",
            "explanation": "Third person singular.",
        },
    )
    assert row.changes == (
        {
            "edit": [2, 3, "have"],
            "category": "grammar",
            "explanation": "Third person singular.",
        },
    )
    assert (row.unclear, row.explained, row.needed, row.orphans) == (0, 1, 1, 0)
    assert (row.true_positives, row.false_positives, row.false_negatives) == (1, 0, 0)
    assert row.pieces == 1
