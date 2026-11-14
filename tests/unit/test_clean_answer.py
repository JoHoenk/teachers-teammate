"""Unit tests for the repair of a review answer (``infrastructure/correction_schema.py``)."""

from __future__ import annotations

from teachers_teammate.infrastructure.correction_schema import (
    Change,
    CorrectionAnswer,
    SentenceReview,
    clean_answer,
)


def _answer(*sentences: SentenceReview) -> CorrectionAnswer:
    """Return an answer of *sentences* without unclear expressions."""
    return CorrectionAnswer(sentences=list(sentences), unclear=[])


def _change(original: str, corrected: str) -> Change:
    """Return a spelling change."""
    return Change(
        original=original, corrected=corrected, category="spelling", explanation="Spelling."
    )


def test_a_placeholder_correction_keeps_the_sentence_and_drops_its_changes() -> None:
    """
    Given  a sentence whose ``corrected`` is a placeholder that has changes listed
    When   the answer is cleaned
    Then   the student's sentence stays and so do no changes, which explain nothing in the text
    """
    answer = _answer(
        SentenceReview(
            original="Ich habe ein grosse Hund.",
            corrected="corrected sentence",
            changes=[_change("grosse", "große")],
        )
    )

    cleaned, repairs = clean_answer(answer, "plain")

    assert cleaned.sentences[0].corrected == "Ich habe ein grosse Hund."
    assert cleaned.sentences[0].changes == []
    assert repairs.junk == 1


def test_a_real_correction_keeps_its_changes() -> None:
    """
    Given  a sentence whose ``corrected`` is a real correction
    When   the answer is cleaned
    Then   the sentence and its changes are kept
    """
    answer = _answer(
        SentenceReview(
            original="Ich habe ein grosse Hund.",
            corrected="Ich habe einen großen Hund.",
            changes=[_change("ein grosse", "einen großen")],
        )
    )

    cleaned, repairs = clean_answer(answer, "plain")

    assert cleaned.sentences[0].corrected == "Ich habe einen großen Hund."
    assert len(cleaned.sentences[0].changes) == 1
    assert repairs.junk == 0
