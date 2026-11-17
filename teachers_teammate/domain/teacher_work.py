"""What a teacher does to a corrected text, as edits in plain offsets.

A card action in the Check tab — *Take out*, *Put back*, *Use suggestion* — is not a state the
app keeps: it is a change of the corrected text, and the text is what the app stores.  This
module turns each action into the :class:`TextEdit` values that make it, in character offsets of
the *plain* text (``TextFormatter.plain_text``), so the caller can apply them to the document it
shows and let the normal commit path store the result.

The whitespace is the whole difficulty.  A hunk of the word diff is trimmed of the layout around
it, so putting the student's words back where the model removed them would glue them to their
neighbours (``"Der bunte Hund"`` → ``"DerHund"``).  One function, :func:`_fit_gap`, decides the
separators of every edit: a replacement keeps the ones it has, an insertion gets a space only
where it would otherwise touch a word, and a removal takes one adjacent space with it.  Words are
never joined across a block boundary.

Pure: no I/O, no Qt, no Markdown.  The offsets are those of the text ``align()`` saw.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re

from .proofreading.quotes import map_forward
from .proofreading.review import AlignedChange, AlignedReview, AlignedUnclear, EditNote, UnclearNote
from .proofreading.word_diff import Hunk

_WORD_CHAR = re.compile(r"\w", flags=re.UNICODE)

#: Whitespace a removal may take with it.  A line break is never taken: it separates blocks.
_TAKEABLE = " \t\u00a0"


@dataclass(frozen=True)
class TextEdit:
    """Replace ``corrected_plain[start:end]`` by *text* (``start == end``: an insertion)."""

    start: int
    end: int
    text: str


def apply_edits(text: str, edits: Sequence[TextEdit]) -> str:
    """Return *text* with every edit of *edits* applied, last to first.

    The edits must be disjoint; applying them from the end keeps the offsets of the ones before
    valid.  The tests use this to check an action without a widget.
    """
    result = text
    for edit in sorted(edits, key=lambda item: (item.start, item.end), reverse=True):
        result = result[: edit.start] + edit.text + result[edit.end :]
    return result


def take_out(
    change_group: Sequence[AlignedChange], raw_plain: str, corrected_plain: str
) -> tuple[TextEdit, ...]:
    """Return the edits that undo one item of the review: the student's words come back.

    Every hunk of the item is undone: a replacement gets the student's words back, a deletion
    puts them back where they were, and an insertion is removed.  The edits are in text order
    and disjoint, so the caller applies them last to first.
    """
    hunks = [change.hunk for change in change_group]
    edits: list[TextEdit] = []
    for index, hunk in enumerate(hunks):
        # A removal may take one adjacent space; it must not take the neighbour hunk's.
        low = hunks[index - 1].corrected_end if index else 0
        high = hunks[index + 1].corrected_start if index + 1 < len(hunks) else len(corrected_plain)
        words = raw_plain[hunk.raw_start : hunk.raw_end] if hunk.original else ""
        edits.append(
            _fit_gap(
                corrected_plain,
                hunk.corrected_start,
                hunk.corrected_end,
                words,
                low=low,
                high=high,
            )
        )
    return tuple(edits)


def put_back(
    note: EditNote, aligned: AlignedReview, raw_plain: str, corrected_plain: str
) -> tuple[TextEdit, ...]:
    """Return the edits that put a taken-out edit back into the corrected text.

    The words the edit changed are found by carrying its span of the student's text forward
    through the current diff, and replaced by the model's wording.  ``()`` when a part of the
    edit cannot be put back: its words are no longer where the note says they are, or the
    teacher's own change stands there now — the card then says so and offers no button.
    """
    hunks = [change.hunk for change in aligned.changes]
    teacher = [change.hunk for change in aligned.changes if change.by_teacher]
    edits: list[TextEdit] = []
    for part in note.parts:
        if raw_plain[part.raw_start : part.raw_end] != part.original:
            return ()
        start, end = map_forward((part.raw_start, part.raw_end), hunks)
        if any(_touches(hunk, start, end) for hunk in teacher):
            return ()
        edits.append(_fit_gap(corrected_plain, start, end, part.corrected))
    return tuple(edits)


def use_suggestion(
    item: AlignedUnclear, aligned: AlignedReview, raw_plain: str, corrected_plain: str
) -> tuple[TextEdit, ...]:
    """Return the edits that replace an unclear expression by the model's suggestion.

    The expression's span is carried forward through the current diff, so a suggestion on words
    the model also changed lands on the corrected wording.  ``()`` when the expression was not
    found in the text: there is nothing to replace.
    """
    if not item.anchored or item.start is None or item.end is None:
        return ()
    if not 0 <= item.start <= item.end <= len(raw_plain):
        return ()
    hunks = [change.hunk for change in aligned.changes]
    start, end = map_forward((item.start, item.end), hunks)
    return (_fit_gap(corrected_plain, start, end, item.note.recommendation),)


def suggestion_is_wording(note: UnclearNote) -> bool:
    """Whether the note's recommendation is a wording to use, not a question to ask.

    Step ③ answers with one string: either a clearer wording or the question to put to the
    student.  A question ends with ``?``; anything else is offered as *Use suggestion*.
    """
    text = note.recommendation.strip()
    return bool(text) and not text.endswith("?")


def _fit_gap(
    text: str, start: int, end: int, replacement: str, *, low: int = 0, high: int | None = None
) -> TextEdit:
    """Return the edit that puts *replacement* in place of ``text[start:end]``.

    A replacement keeps the separators it has, because the hunk's span was trimmed of layout and
    so begins and ends where the words do.  An insertion gets one space on the side where it
    would otherwise touch a word, and none next to punctuation or a line start or end.  A removal
    takes one adjacent space with it — the one after, else the one before, never a line break —
    so the words around it do not end up with two spaces between them.  *low* and *high* bound
    the space a removal may take, so two edits of one item never overlap.
    """
    limit = len(text) if high is None else high
    if start < end and replacement:
        return TextEdit(start, end, replacement)
    if start == end and replacement:
        return _insertion(text, start, replacement)
    if start < end:
        return _removal(text, start, end, low=low, high=limit)
    return TextEdit(start, end, replacement)


def _insertion(text: str, at: int, words: str) -> TextEdit:
    """Return the edit that inserts *words* at *at*, with the spaces they need."""
    before = text[at - 1] if at > 0 else ""
    after = text[at] if at < len(text) else ""
    lead = " " if _is_word(before) else ""
    trail = " " if _is_word(after) else ""
    return TextEdit(at, at, lead + words + trail)


def _removal(text: str, start: int, end: int, *, low: int, high: int) -> TextEdit:
    """Return the edit that removes ``text[start:end]`` and one adjacent space."""
    after = text[end] if end < len(text) else ""
    if after in _TAKEABLE and end + 1 <= high:
        return TextEdit(start, end + 1, "")
    before = text[start - 1] if start > 0 else ""
    if before in _TAKEABLE and start - 1 >= low:
        return TextEdit(start - 1, end, "")
    return TextEdit(start, end, "")


def _is_word(char: str) -> bool:
    """Whether *char* is a letter, digit or underscore — what a word is made of."""
    return bool(char) and _WORD_CHAR.match(char) is not None


def _touches(hunk: Hunk, start: int, end: int) -> bool:
    """Whether the corrected span *start*..*end* touches *hunk* (a teacher's own change)."""
    if start == end:
        return hunk.corrected_start <= start <= hunk.corrected_end
    return start < hunk.corrected_end and hunk.corrected_start < end
