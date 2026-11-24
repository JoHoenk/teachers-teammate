"""Unit tests for the merge that every reader of a stored document goes through."""

from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path

import pytest

from teachers_teammate.application import merged_view as merged_view_module
from teachers_teammate.application.document_view import to_view
from teachers_teammate.application.merged_view import merged_view
from teachers_teammate.domain.proofreading.edits import edits
from teachers_teammate.domain.proofreading.review import CorrectionReview, edit_note
from teachers_teammate.domain.document_state import DocumentState

RAW = "Der bunte Hund bellt laut."
CORRECTED = "Der braune Hund bellt laut."
SOURCE = Path("/tmp/Anna.png")


def _review() -> CorrectionReview:
    """Return the review of :data:`RAW` against :data:`CORRECTED`."""
    return CorrectionReview(
        edits=(edit_note(edits(RAW, CORRECTED)[0], category="word_choice", explanation="Farbe."),)
    )


def _state(review: CorrectionReview | None) -> DocumentState:
    """Return a stored document whose proofreading carries *review*."""
    return DocumentState(
        schema_version=2,
        source_path=str(SOURCE),
        source_hash="abc123",
        raw_text=RAW,
        correction_text=CORRECTED,
        correction_review=None if review is None else review.to_dict(),
        ocr_done=True,
        correction_done=True,
    )


def _wrong() -> CorrectionReview:
    """Return the review with every quoted word replaced by one the text does not hold."""
    review = _review()
    return replace(
        review,
        edits=tuple(
            replace(note, parts=tuple(replace(part, original="grüne") for part in note.parts))
            for note in review.edits
        ),
    )


@pytest.fixture(autouse=True)
def _fresh_log_memory() -> None:
    """Let every test see the first warning of its document again."""
    merged_view_module._warn_dropped.cache_clear()


def _merge(state: DocumentState):
    """Return the view of *state* as every reader gets it."""
    return merged_view(to_view(SOURCE, state), state, None)


def test_a_review_that_fits_the_transcription_is_kept() -> None:
    """
    Given  a stored review that quotes the words it points at
    When   the document is merged
    Then   the view carries the review
    """
    assert _merge(_state(_review())).correction_review == _review()


def test_a_review_that_does_not_fit_is_dropped_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Given  a stored review whose words are not at the offsets it points at
    When   the document is read three times, as the queue and the Check tab do
    Then   the review is dropped every time and the log names the document once
    """
    state = _state(_wrong())

    with caplog.at_level(logging.WARNING):
        views = [_merge(state) for _ in range(3)]

    assert [view.correction_review for view in views] == [None, None, None]
    assert caplog.text.count("Dropped the review of Anna.png") == 1


def test_a_document_without_a_review_says_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """
    Given  a stored document that was corrected without a review
    When   it is merged
    Then   it has no review and nothing is logged
    """
    with caplog.at_level(logging.WARNING):
        view = _merge(_state(None))

    assert view.correction_review is None
    assert caplog.text == ""
