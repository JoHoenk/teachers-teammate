"""Edits of a plain text, and the separators they need.

The whitespace is the whole difficulty.  A hunk of the word diff is trimmed of the layout around
it, so putting the student's words back where the model removed them would glue them to their
neighbours (``"Der bunte Hund"`` → ``"DerHund"``).  One function, :func:`fit_gap`, decides the
separators of every edit: a replacement keeps the ones it has, an insertion gets a space only
where it would otherwise touch a word, and a removal takes one adjacent space with it.  Words are
never joined across a block boundary.  The card actions of the Check tab
(:mod:`~teachers_teammate.domain.teacher_work`) and a reading fix carried into the corrected text
(:mod:`~teachers_teammate.domain.proofreading.carry`) both place their words here.

Pure: no I/O, no Qt, no Markdown.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re

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


def fit_gap(
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
