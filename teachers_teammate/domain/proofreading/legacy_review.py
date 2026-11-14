"""The edit-based review of the exchange, shown as the sentence-based review the app still reads.

The exchange explains *edits* (:class:`~.review.CorrectionReview`); the GUI, the DOCX builder, the
cache and the evaluation tools still read the sentence-based review of
:mod:`~teachers_teammate.domain.correction_review`, which the review path produces.  Until the
readers move, this module is the one bridge: every edit becomes a change note in the sentence it
touches, and the sentence carries its corrected wording, so ``align()`` matches every note to the
same hunk the edit was made from.  When the readers take the edit-based review, this module goes.

Pure: no I/O, no Qt, no provider imports.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..correction_review import ChangeNote, SentenceNote
from ..correction_review import CorrectionReview as SentenceReview
from ..correction_review import UnclearNote as SentenceUnclear
from .review import CorrectionReview, EditNote, UnclearNote
from .sentences import sentence_spans


def sentence_review(raw_plain: str, review: CorrectionReview) -> SentenceReview:
    """Return *review* as a sentence-based review of *raw_plain*, the plain source text.

    An edit goes into the first source sentence it touches (the sentence of its first hunk when
    the review does not name one).  The sentence's corrected wording is the sentence with every
    hunk of its edits applied, so it reads exactly like the corrected text.  Unclear notes keep
    their quote, reason and suggestion; their offsets are not part of the sentence-based review.

    Args:
        raw_plain: The student's text as the exchange diffed it (plain text, marks removed).
        review: The edit-based review of that text.
    """
    spans = sentence_spans(raw_plain)
    by_sentence: dict[int, list[EditNote]] = {}
    for note in review.edits:
        by_sentence.setdefault(_sentence_of(note, spans), []).append(note)
    sentences = tuple(
        _sentence(raw_plain, spans[index], by_sentence[index])
        for index in sorted(by_sentence)
        if 0 <= index < len(spans)
    )
    return SentenceReview(
        sentences=sentences, unclear=tuple(_unclear(note) for note in review.unclear)
    )


def _sentence_of(note: EditNote, spans: Sequence[tuple[int, int]]) -> int:
    """Return the index of the source sentence *note* belongs to, ``-1`` when none holds it."""
    if note.sentences:
        return note.sentences[0]
    for index, (start, end) in enumerate(spans):
        if start <= note.raw_start <= end:
            return index
    return -1


def _sentence(raw_plain: str, span: tuple[int, int], notes: list[EditNote]) -> SentenceNote:
    """Return the sentence at *span* with the change notes of *notes* and its corrected wording."""
    start, end = span
    parts = sorted(
        (part for note in notes for part in note.parts),
        key=lambda part: part.raw_start,
        reverse=True,
    )
    corrected = raw_plain[start:end]
    for part in parts:
        low = max(part.raw_start, start) - start
        high = min(max(part.raw_end, part.raw_start), end) - start
        corrected = corrected[:low] + part.corrected + corrected[high:]
    return SentenceNote(
        original=raw_plain[start:end],
        corrected=corrected.strip(),
        changes=tuple(
            ChangeNote(
                original=note.original,
                corrected=note.corrected,
                category=note.category,
                explanation=note.explanation,
            )
            for note in sorted(notes, key=lambda note: note.raw_start)
        ),
    )


def _unclear(note: UnclearNote) -> SentenceUnclear:
    """Return *note* without its offsets."""
    return SentenceUnclear(
        expression=note.expression,
        why=note.why,
        recommendation=note.recommendation,
        is_suggestion=note.is_suggestion,
    )
