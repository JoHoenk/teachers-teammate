"""A reading fix that leaves the proofreading standing.

The teacher fixes one misread word in *What was read*, after the banner said what that costs.  The
proofreading was made from the text that word stands in, and every offset of its review points
into that text.  A fix that touches no word the proofreading changed is carried over: the same
word changes are made in the corrected text, the review moves with them, and the proofreading and
the decisions about it survive.  Any other fix gives ``None``, and the caller drops the proofreading
as it did before — a half-carried review is worse than none (AGENTS.md: a review that fails
``fits`` is dropped).

Three parts, one rule.  :func:`carry_reading_fix` decides on the plain texts: the offsets of
:mod:`~teachers_teammate.domain.proofreading.review` are plain-text offsets, so the review it
returns points into the new transcription.  :func:`retarget_words` makes the same word changes in a
text the model never wrote — the teacher's own corrected text — which is placed by its words,
because a reading offset says nothing about it.  :class:`CarriedFix` carries the word changes, so
that second text can take them.

Offsets are moved by :class:`~teachers_teammate.domain.proofreading.word_diff.OffsetMap`, which
knows the layout whitespace the word diff counts as equal.  The result is then checked against the
model's own changes: the corrected text and the new transcription must differ by exactly the
words they differed by before, or nothing is carried.

Pure: no I/O, no Qt, no provider imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum
import re

from .edits import EditPart
from .review import CorrectionReview, EditNote, UnclearNote, fits
from .text_edits import TextEdit, apply_edits, fit_gap
from .word_diff import Hunk, OffsetMap, word_diff


class ReadingFix(Enum):
    """What a reading fix did to the proofreading of the document it was made in."""

    NONE = "none"
    """There was no proofreading to keep or to lose."""
    CARRIED = "carried"
    """The proofreading stands, moved over the fix."""
    TOUCHED_CORRECTION = "touched_correction"
    """The proofreading is gone: the fix was in or next to a correction."""
    NOT_CARRIED = "not_carried"
    """The proofreading is gone for another reason: the fix could not be carried over."""


@dataclass(frozen=True)
class WordFix:
    """One change of the reading: ``old[start:end]`` reads *words* now."""

    start: int
    """Offset of the first character the fix changes in the old transcription."""
    end: int
    """Offset after the last character it changes; ``start`` for inserted words."""
    original: str
    """The words the reading had; empty for words that were only inserted."""
    words: str
    """The words it has now; empty for words that were only removed."""


@dataclass(frozen=True)
class CarriedFix:
    """A proofreading that a reading fix left standing."""

    corrected: str
    """The corrected text with the same word changes made."""
    review: CorrectionReview
    """The review, with its offsets moved into the new transcription."""
    fixes: tuple[WordFix, ...] = ()
    """The word changes themselves, for a second corrected text (the teacher's own)."""


def carry_reading_fix(
    old: str, new: str, corrected: str, review: CorrectionReview
) -> CarriedFix | None:
    """Return the proofreading the fix from *old* to *new* leaves standing, or ``None``.

    Both texts are plain, and *old* is the transcription the proofreading was made from.
    *corrected* is the proofread text, *review* its review: the two are carried together or not
    at all, because a corrected text without its explanations is not what the teacher worked on.

    The fix is safe when every word change of it

    * touches no word the model changed — every hunk from *old* to *corrected*, explained by the
      review or not — and no expression the review asks about: the teacher changed words such an
      explanation is about, and an insertion at the edge of one is inside it; and
    * therefore lies in a stretch the two texts share, where the corrected text takes the fix
      there and nowhere else.

    A change of layout alone has no word change and is carried as it is.  The result is checked
    before it is given: the corrected text and *new* must differ by exactly the changes *corrected*
    and *old* differed by, each where the review says it is.  Anything else returns ``None``.  The
    sentence numbers of the notes are dropped: they were counted on the old text, whose sentence
    boundaries a fix can move.
    """
    if old == new:
        return CarriedFix(corrected=corrected, review=review)
    hunks = word_diff(old, new)
    model = word_diff(old, corrected)
    if not all(_clear_of(hunk, model, review) for hunk in hunks):
        return None
    fixes = tuple(
        WordFix(hunk.raw_start, hunk.raw_end, hunk.original, hunk.corrected) for hunk in hunks
    )
    through = OffsetMap(old, corrected)
    edits: list[TextEdit] = []
    for fix in fixes:
        start = through.forward(fix.start)
        end = start if fix.start == fix.end else through.forward(fix.end, end=True)
        edits.append(fit_gap(corrected, start, end, fix.words))
    result = apply_edits(corrected, tuple(edits))
    landed = word_diff(new, result)
    if _changes(landed) != _changes(model):
        return None
    moved = _moved(review, old, new, model, landed)
    if moved is None or not fits(moved, new):
        return None
    return CarriedFix(corrected=result, review=moved, fixes=fixes)


def touches_a_correction(old: str, new: str, corrected: str, review: CorrectionReview) -> bool:
    """Whether the fix from *old* to *new* is in or next to a change of *corrected* or a question.

    The reason a fix is not carried that the teacher can act on: it is the words they changed.
    """
    model = word_diff(old, corrected)
    return not all(_clear_of(hunk, model, review) for hunk in word_diff(old, new))


def retarget_words(text: str, fixes: Sequence[WordFix]) -> str | None:
    """Return *text* with *fixes* made by their words, or ``None`` when one of them does not fit.

    The teacher's own corrected text was written over the model's, so an offset of the reading
    says nothing about where its words are in it.  A fix is placed by the words the reading had,
    and only when they stand in the text exactly once as whole words: a word the text holds twice,
    or only inside a longer word, is not a place to guess.  Words that were only inserted have
    none of their own to find, so such a fix cannot be placed here and the caller drops the
    proofreading.
    """
    result = text
    for fix in fixes:
        if not fix.original:
            return None
        found = list(re.finditer(_whole(fix.original), result))
        if len(found) != 1:
            return None
        edit = fit_gap(result, found[0].start(), found[0].end(), fix.words)
        result = apply_edits(result, (edit,))
    return result


def _whole(words: str) -> str:
    """Return the pattern for *words* standing on their own, not inside a longer word."""
    before = r"(?<!\w)" if re.match(r"\w", words[0]) else ""
    after = r"(?!\w)" if re.match(r"\w", words[-1]) else ""
    return before + re.escape(words) + after


def _changes(hunks: Sequence[Hunk]) -> list[tuple[str, str]]:
    """Return what each hunk changes, in order: the two texts' words without their places."""
    return [(hunk.original, hunk.corrected) for hunk in hunks]


def _clear_of(hunk: Hunk, model: Sequence[Hunk], review: CorrectionReview) -> bool:
    """Whether *hunk* touches no word the model changed and no expression the review asks about."""
    spans = [(change.raw_start, change.raw_end) for change in model]
    spans += [(note.raw_start, note.raw_end) for note in review.unclear]
    return all(hunk.raw_end < low or hunk.raw_start > high for low, high in spans)


def _moved(
    review: CorrectionReview,
    old: str,
    new: str,
    model: Sequence[Hunk],
    landed: Sequence[Hunk],
) -> CorrectionReview | None:
    """Return *review* with every offset carried into *new*, or ``None`` when one cannot be.

    A part that is one of the model's changes takes the place that change has in *landed*, the
    diff of *new* against the carried text; any other offset goes through the layout of the two
    readings.  An unclear note must quote the same words in both.
    """
    places = {
        (change.raw_start, change.raw_end, change.corrected): index
        for index, change in enumerate(model)
    }
    through = OffsetMap(old, new)
    notes: list[EditNote] = []
    for note in review.edits:
        parts: list[EditPart] = []
        for part in note.parts:
            index = places.get((part.raw_start, part.raw_end, part.corrected))
            if index is not None:
                start, end = landed[index].raw_start, landed[index].raw_end
            else:
                start = through.forward(part.raw_start)
                end = through.forward(part.raw_end, end=True)
            parts.append(replace(part, raw_start=start, raw_end=end))
        notes.append(replace(note, parts=tuple(parts), sentences=()))
    unclear: list[UnclearNote] = []
    for question in review.unclear:
        start = through.forward(question.raw_start)
        end = (
            start
            if question.raw_start == question.raw_end
            else through.forward(question.raw_end, end=True)
        )
        if old[question.raw_start : question.raw_end] != new[start:end]:
            return None
        unclear.append(replace(question, raw_start=start, raw_end=end))
    return CorrectionReview(edits=tuple(notes), unclear=tuple(unclear))
