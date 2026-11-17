"""The one-request baseline's answer, read as the edit-based review at its edge.

The baseline answers with sentences and the changes inside them; the app stores edits with
character offsets.  This module is the one place where the two meet, so the evaluation tools can
compare a baseline run with an exchange run without a second review model: everything outside
:mod:`.correction` and :mod:`.structured_check` reads the edit-based review only.

The conversion is deliberately mechanical.  The sentence answer is aligned against the plain
texts with the sentence-based ``align``, which gives every hunk of the word diff the note that
explains it; the hunks are then grouped into edits exactly as the exchange groups them, and an
edit takes the category and explanation of its first hunk that has a note.  The unclear quotes are
searched in the student's text, because that is what the baseline quotes; a quote that cannot be
found keeps equal ends, which is how a review says "no span".
"""

from __future__ import annotations

from collections.abc import Sequence

from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.proofreading.edits import Edit, EditPart, edits_from_hunks
from teachers_teammate.domain.proofreading.quotes import find_spans
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    EditNote,
    UnclearNote,
)
from teachers_teammate.domain.proofreading.sentences import sentence_spans
from teachers_teammate.infrastructure.ocr_formats import plain_text_of
from tools.model_eval.one_request.sentence_review import ChangeNote
from tools.model_eval.one_request.sentence_review import CorrectionReview as SentenceReview
from tools.model_eval.one_request.sentence_review import align as align_sentences


def edit_review(
    source: str,
    corrected: str,
    review: SentenceReview,
    text_format: TextFormat = "plain",
) -> CorrectionReview:
    """Return *review* of *source* → *corrected* as the edit-based review the app stores.

    Args:
        source: The text before proofreading, in its stored notation.
        corrected: The text after proofreading, in the same notation.
        review: The baseline's sentence answer.
        text_format: The notation of both texts; the offsets are made in their plain text.

    Returns:
        The review with one note per edit, numbered in text order, and the unclear expressions
        anchored where they were found.
    """
    raw_plain = plain_text_of(source, text_format)
    corrected_plain = plain_text_of(corrected, text_format)
    aligned = align_sentences(raw_plain, corrected_plain, review)
    notes = {
        (change.hunk.raw_start, change.hunk.raw_end): change.note for change in aligned.changes
    }
    edits = edits_from_hunks(
        tuple(change.hunk for change in aligned.changes), raw_plain, sentence_spans(raw_plain)
    )
    return CorrectionReview(
        edits=tuple(_note(edit, notes) for edit in edits),
        unclear=_unclear(review, raw_plain),
    )


def _note(edit: Edit, notes: dict[tuple[int, int], ChangeNote | None]) -> EditNote:
    """Return *edit* as a note, taking category and explanation from its first explained hunk."""
    explaining = _explaining_note(edit.parts, notes)
    return EditNote(
        id=edit.id,
        parts=edit.parts,
        sentences=edit.sentences,
        category=explaining.category if explaining is not None else "other",
        explanation=explaining.explanation if explaining is not None else "",
    )


def _explaining_note(
    parts: Sequence[EditPart], notes: dict[tuple[int, int], ChangeNote | None]
) -> ChangeNote | None:
    """Return the note of the first part of an edit that has one, or ``None``."""
    for part in parts:
        note = notes.get((part.raw_start, part.raw_end))
        if note is not None:
            return note
    return None


def _unclear(review: SentenceReview, raw_plain: str) -> tuple[UnclearNote, ...]:
    """Return the baseline's unclear expressions, anchored in *raw_plain* where they were found."""
    spans = find_spans(raw_plain, [note.expression for note in review.unclear])
    return tuple(
        UnclearNote(
            raw_start=span[0] if span is not None else 0,
            raw_end=span[1] if span is not None else 0,
            expression=note.expression,
            why=note.why,
            recommendation=note.recommendation,
            is_suggestion=note.is_suggestion,
        )
        for note, span in zip(review.unclear, spans, strict=True)
    )
