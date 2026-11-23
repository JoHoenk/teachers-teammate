"""The comments layout of the Word file: the extracted text, marked where it changed.

The layout shows the student's text as it was extracted, one paragraph per line, and puts every
correction and every unclear expression on the words it is about as a Word comment.  Nothing is
diffed here: the hunks and the spans come from
:func:`~teachers_teammate.domain.proofreading.review.align`, so the Word file and the window can
never disagree about where a change begins and ends.

A :class:`Mark` is one place of the text that carries a comment.  :func:`marks_for` turns an
aligned review into marks; :func:`write_marked_text` writes the text and anchors them.

Pure apart from the python-docx objects it writes into: no I/O, no provider imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re
from typing import Any

from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from ..domain.plain_text import xml_safe_text
from ..domain.proofreading.review import AlignedReview, EditNote, UnclearNote
from ..domain.proofreading.word_diff import Hunk

BODY_PT = 10
"""Size of the body text of every layout of the Word file (the comments layout's text too)."""

_WORD_RE = re.compile(r"\w+")

_ARROW = "\u2192"
_MIDDLE_DOT = "\u00b7"
_EM_DASH = "\u2014"


@dataclass(frozen=True)
class Mark:
    """One place of the extracted text that carries a Word comment."""

    start: int
    """Offset in the plain text; equal to :attr:`end` for an insertion marker."""
    end: int
    comment: str
    """The text of the comment."""
    highlight: bool
    """``True`` for a change (yellow), ``False`` for an unclear expression (comment only)."""
    marker: str = ""
    """The words written at :attr:`start` for an insertion (``"[+words]"``)."""


def marks_for(
    aligned: AlignedReview, labels: dict[str, str], raw_plain: str | None = None
) -> list[Mark]:
    """Return the marks of *aligned*: one per change, one per unclear expression.

    A change is marked on its raw span and commented with what it does to the words; an
    insertion has no span, so it is written as a marker at the place it belongs.  An edit of
    several hunks is explained once, in the comment of its first hunk, because the teacher reads
    it as one change.  An unclear expression is commented on its own words and not highlighted:
    nothing is wrong there, the meaning is just not clear.  An expression whose span was not
    found is, when *raw_plain* is given, commented on the whole line that holds its words (found
    word for word, punctuation ignored); with none, or when no line holds them, it has no mark.
    """
    marks: list[Mark] = []
    explained: set[int] = set()
    for change in aligned.changes:
        comment = _change_text(change.hunk)
        if change.explains_text and change.edit_id is not None and change.edit_id not in explained:
            explained.add(change.edit_id)
            comment = _with_explanation(comment, change.note, labels)
        if change.hunk.kind == "insert":
            marks.append(
                Mark(
                    start=change.hunk.raw_start,
                    end=change.hunk.raw_start,
                    comment=comment,
                    highlight=True,
                    marker=f"[+{_words(change.hunk.corrected)}]",
                )
            )
        else:
            marks.append(
                Mark(
                    start=change.hunk.raw_start,
                    end=change.hunk.raw_end,
                    comment=comment,
                    highlight=True,
                )
            )
    for unclear in aligned.unclear:
        if unclear.note.left_out:
            continue  # the teacher's note in Check, not something for the student's copy
        start, end = unclear.start, unclear.end
        if start is None or end is None:
            line = _line_with_words(raw_plain, unclear.note.expression)
            if line is None:
                continue
            start, end = line
        marks.append(
            Mark(
                start=start,
                end=end,
                comment=unclear_comment(unclear.note, labels),
                highlight=False,
            )
        )
    return marks


def _line_with_words(raw_plain: str | None, expression: str) -> tuple[int, int] | None:
    """Return the span of the first line of *raw_plain* that holds *expression*, or ``None``.

    The words are compared whole and in order, with punctuation and case ignored on both sides,
    so an expression that ends a sentence is found as well as one in the middle of it.
    """
    needle = _WORD_RE.findall(expression.casefold())
    if raw_plain is None or not needle:
        return None
    for start, end, line in _lines(raw_plain):
        haystack = _WORD_RE.findall(line.casefold())
        if any(
            haystack[at : at + len(needle)] == needle
            for at in range(len(haystack) - len(needle) + 1)
        ):
            return start, end
    return None


def write_marked_text(
    cell: Any, raw_plain: str, marks: Sequence[Mark], comments: list[tuple[int, str]]
) -> None:
    """Write *raw_plain* into *cell*, one paragraph per line, with *marks* as comments.

    A line is cut at every mark boundary inside it, so each piece is one run and a change is
    highlighted exactly over its words.  A mark's comment range opens before its first run and
    closes after its last run on its first line; a hunk that runs across a line break is only
    highlighted on the lines after that, because one comment cannot span two paragraphs.
    """
    lines = _lines(raw_plain)
    ordered = sorted(
        enumerate(marks), key=lambda pair: (pair[1].start, pair[1].start - pair[1].end)
    )
    first_line = {index: _first_line(lines, mark) for index, mark in ordered}
    for number, (start, end, text) in enumerate(lines):
        para = cell.paragraphs[0] if number == 0 else cell.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        pieces, markers = _write_line(para, text, start, end, ordered)
        for index, mark in ordered:
            if first_line[index] != number:
                continue
            covered = _covered(mark, index, pieces, markers)
            if not covered and mark.start < mark.end and mark.start == end:
                # The span begins on this line's break and covers none of the line's words: the
                # comment sits on the line's last run (an empty run on an empty line).
                covered = [pieces[-1][2] if pieces else _add_run(para, "")]
            if covered:
                anchor_comment(covered, comments, mark.comment)


def anchor_comment(runs: Sequence[Any], comments: list[tuple[int, str]], text: str) -> None:
    """Wrap *runs* in a comment range and register *text* as the comment's body."""
    if not runs:
        return
    comment_id = len(comments)
    comments.append((comment_id, text))
    range_start = OxmlElement("w:commentRangeStart")
    range_start.set(qn("w:id"), str(comment_id))
    runs[0]._r.addprevious(range_start)  # noqa: SLF001  # python-docx has no public API for OOXML comment range anchors; _r exposes the underlying lxml element
    range_end = OxmlElement("w:commentRangeEnd")
    range_end.set(qn("w:id"), str(comment_id))
    runs[-1]._r.addnext(range_end)  # noqa: SLF001  # python-docx has no public API for OOXML comment range anchors; _r exposes the underlying lxml element
    ref_r = OxmlElement("w:r")
    ref_rpr = OxmlElement("w:rPr")
    ref_style = OxmlElement("w:rStyle")
    ref_style.set(qn("w:val"), "CommentReference")
    ref_rpr.append(ref_style)
    ref_r.append(ref_rpr)
    ref_ref = OxmlElement("w:commentReference")
    ref_ref.set(qn("w:id"), str(comment_id))
    ref_r.append(ref_ref)
    range_end.addnext(ref_r)


def unclear_comment(note: UnclearNote, labels: dict[str, str]) -> str:
    """Return the comment text of one unclear expression."""
    recommendation = (
        f"{labels['suggestion_prefix']}: {note.recommendation}"
        if note.is_suggestion
        else f"{labels['recommended_prefix']}: {note.recommendation}"
    )
    return f"{labels['unclear_prefix']}: {note.why} {_EM_DASH} {recommendation}"


def _change_text(hunk: Hunk) -> str:
    """Return what *hunk* does to the words, as the comment says it."""
    if hunk.kind == "delete":
        return f"{_ARROW} (delete)"
    if hunk.kind == "insert":
        return f'{_ARROW} (insert "{_words(hunk.corrected)}")'
    return f'{_ARROW} "{_words(hunk.corrected)}"'


def _with_explanation(base: str, note: EditNote | None, labels: dict[str, str]) -> str:
    """Return *base* plus the note's category and explanation (``base`` when there is none)."""
    if note is None or not note.explanation:
        return base
    category = labels.get(note.category, note.category.replace("_", " "))
    return f"{base} {_MIDDLE_DOT} {category}: {note.explanation}"


def _words(text: str) -> str:
    """Return *text* with its whitespace collapsed, as a comment quotes it."""
    return " ".join(text.split())


def _lines(text: str) -> list[tuple[int, int, str]]:
    """Return ``(start, end, line)`` per line of *text*, the newline belonging to neither."""
    lines: list[tuple[int, int, str]] = []
    position = 0
    for line in text.split("\n"):
        lines.append((position, position + len(line), line))
        position += len(line) + 1
    return lines


def _first_line(lines: Sequence[tuple[int, int, str]], mark: Mark) -> int:
    """Return the index of the line *mark*'s comment is anchored on, ``-1`` when there is none.

    A span belongs to the line its first character is on.  An insertion belongs to the line that
    holds its position, and a position at a line's end belongs to that line.  A span that begins
    on a line break (a deleted line break, ``"foo\nbar"`` → ``"foo bar"``) has no first character
    on a line: it belongs to the line the break ends.
    """
    for number, (start, end, _text) in enumerate(lines):
        if mark.start == mark.end:
            if start <= mark.start <= end:
                return number
        elif start <= mark.start < end:
            return number
    if mark.start < mark.end:
        for number, (_start, end, _text) in enumerate(lines):
            if end == mark.start:
                return number
    return -1


def _write_line(
    para: Any,
    text: str,
    start: int,
    end: int,
    marks: Sequence[tuple[int, Mark]],
) -> tuple[list[tuple[int, int, Any]], dict[int, Any]]:
    """Write one line of *text* into *para*; return its runs and the insertion markers.

    The runs are ``(start, end, run)`` with offsets in the whole text, so a mark can find the
    ones it covers.
    """
    cuts = {0, len(text)}
    for _index, mark in marks:
        clipped = _clip(mark, start, end)
        if clipped is not None:
            cuts.update(clipped)
    pieces: list[tuple[int, int, Any]] = []
    markers: dict[int, Any] = {}
    position = 0
    for boundary in sorted(cuts):
        if boundary > position:
            run = _add_run(para, text[position:boundary])
            if _highlighted(marks, start + position, start + boundary):
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            pieces.append((start + position, start + boundary, run))
            position = boundary
        for index, mark in marks:
            if mark.start == mark.end and mark.start - start == boundary:
                run = _add_run(para, mark.marker)
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW
                markers[index] = run
    return pieces, markers


def _clip(mark: Mark, start: int, end: int) -> tuple[int, int] | None:
    """Return where *mark* falls inside the line ``start``..``end``, or ``None``.

    The offsets are relative to the line, which is what the cuts of the line are counted in.
    """
    if mark.start == mark.end:
        if start <= mark.start <= end:
            return mark.start - start, mark.start - start
        return None
    low, high = max(mark.start, start), min(mark.end, end)
    if low >= high:
        return None
    return low - start, high - start


def _highlighted(marks: Sequence[tuple[int, Mark]], low: int, high: int) -> bool:
    """Whether a change mark covers the whole of ``low``..``high``."""
    return any(
        mark.highlight and mark.start < mark.end and mark.start <= low and high <= mark.end
        for _index, mark in marks
    )


def _covered(
    mark: Mark, index: int, pieces: Sequence[tuple[int, int, Any]], markers: dict[int, Any]
) -> list[Any]:
    """Return the runs of one line that *mark* covers, in order."""
    if mark.start == mark.end:
        run = markers.get(index)
        return [run] if run is not None else []
    return [run for low, high, run in pieces if low >= mark.start and high <= mark.end]


def _add_run(para: Any, text: str) -> Any:
    """Add *text* to *para* as one run of the layout's size."""
    run = para.add_run(xml_safe_text(text))
    run.font.size = Pt(BODY_PT)
    return run
