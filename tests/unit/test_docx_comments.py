"""Unit tests for the comments layout of the Word file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
import pytest

from teachers_teammate.domain.proofreading.edits import edits_from_hunks
from teachers_teammate.domain.proofreading.review import (
    AlignedReview,
    AlignedUnclear,
    CorrectionReview,
    EditNote,
    UnclearNote,
    align,
)
from teachers_teammate.domain.proofreading.sentences import sentence_spans
from teachers_teammate.domain.proofreading.word_diff import word_diff
from teachers_teammate.infrastructure.docx_comments import (
    Mark,
    marks_for,
    unclear_comment,
    write_marked_text,
)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_LABELS = {
    "grammar": "Grammar",
    "unclear_prefix": "Unclear",
    "recommended_prefix": "Recommended",
    "suggestion_prefix": "Suggestion",
}


def _review(
    raw: str,
    corrected: str,
    *,
    category: str = "grammar",
    explanation: str = "Because it is.",
    unclear: tuple[UnclearNote, ...] = (),
) -> CorrectionReview:
    """Return a review of *raw* against *corrected* with one note per edit."""
    edits = edits_from_hunks(word_diff(raw, corrected), raw, sentence_spans(raw))
    return CorrectionReview(
        edits=tuple(
            EditNote(
                id=edit.id,
                parts=edit.parts,
                sentences=edit.sentences,
                category=category,
                explanation=explanation,
            )
            for edit in edits
        ),
        unclear=unclear,
    )


def _cell() -> Any:
    """Return an empty table cell of a fresh document."""
    return Document().add_table(rows=1, cols=1).rows[0].cells[0]


def _commented_text(cell: Any, comment_id: int) -> str:
    """Return the text the comment range *comment_id* covers."""
    parts: list[str] = []
    inside = False
    for (
        element
    ) in cell._tc.iter():  # python-docx exposes no public API for reading OOXML comment ranges
        if element.tag == f"{_W}commentRangeStart" and element.get(f"{_W}id") == str(comment_id):
            inside = True
        elif element.tag == f"{_W}commentRangeEnd" and element.get(f"{_W}id") == str(comment_id):
            inside = False
        elif inside and element.tag == f"{_W}t":
            parts.append(element.text or "")
    return "".join(parts)


def _text_before_comment(cell: Any, comment_id: int) -> str:
    """Return the text of the cell that comes before the comment range *comment_id*."""
    parts: list[str] = []
    for (
        element
    ) in cell._tc.iter():  # python-docx exposes no public API for reading OOXML comment ranges
        if element.tag == f"{_W}commentRangeStart" and element.get(f"{_W}id") == str(comment_id):
            return "".join(parts)
        if element.tag == f"{_W}t":
            parts.append(element.text or "")
    return ""


def _highlighted(cell: Any) -> list[str]:
    """Return the text of every highlighted run of the cell."""
    return [
        run.text
        for para in cell.paragraphs
        for run in para.runs
        if run.font.highlight_color == WD_COLOR_INDEX.YELLOW
    ]


# ── marks_for ─────────────────────────────────────────────────────────────


def test_marks_for_comments_a_change_with_its_explanation() -> None:
    """
    Given  a review whose change "have" → "has" carries a grammar explanation
    When   marks_for() is called
    Then   one mark covers exactly those words and its comment names category and explanation
    """
    raw = "Yesterday she have gone."
    marks = marks_for(
        align(raw, "Yesterday she has gone.", _review(raw, "Yesterday she has gone.")), _LABELS
    )

    assert len(marks) == 1
    assert (marks[0].start, marks[0].end) == (14, 18)
    assert marks[0].highlight
    assert marks[0].comment == '\u2192 "has" \u00b7 Grammar: Because it is.'


def test_marks_for_writes_an_insertion_as_a_marker() -> None:
    """
    Given  a review whose only change inserts a word
    When   marks_for() is called
    Then   the mark is zero-width, carries the words as a marker and says it is an insertion
    """
    raw = "I like it."
    marks = marks_for(align(raw, "I really like it.", _review(raw, "I really like it.")), _LABELS)

    assert len(marks) == 1
    assert marks[0].start == marks[0].end
    assert marks[0].marker == "[+really]"
    assert marks[0].comment.startswith('\u2192 (insert "really")')


def test_marks_for_explains_a_two_hunk_edit_once() -> None:
    """
    Given  one edit of two hunks, both with the same note
    When   marks_for() is called
    Then   only the first hunk's comment carries the explanation
    """
    raw = "Yesterday she have gone to the cinema."
    corrected = "Yesterday she has went to the cinema."
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(
                    _part(raw, "have", "has"),
                    _part(raw, "gone", "went"),
                ),
                sentences=(0,),
                category="grammar",
                explanation="Because it is.",
            ),
        )
    )
    marks = marks_for(align(raw, corrected, review), _LABELS)

    assert len(marks) == 2
    assert marks[0].comment == '\u2192 "has" \u00b7 Grammar: Because it is.'
    assert marks[1].comment == '\u2192 "went"'


def test_marks_for_leaves_out_the_explanation_of_a_changed_hunk() -> None:
    """
    Given  a note whose edit the teacher changed afterwards
    When   marks_for() is called
    Then   the comment says what the words became and explains nothing
    """
    raw = "Yesterday she have gone."
    corrected = "Yesterday she got gone."
    marks = marks_for(align(raw, corrected, _review(raw, "Yesterday she has gone.")), _LABELS)

    assert len(marks) == 1
    assert marks[0].comment == '\u2192 "got"'


def test_marks_for_comments_an_unclear_expression_without_highlighting_it() -> None:
    """
    Given  a review with an unclear expression that was found in the text
    When   marks_for() is called
    Then   its mark covers its words, is not highlighted and quotes why and what to write
    """
    raw = "It was very nice."
    note = UnclearNote(
        raw_start=7,
        raw_end=16,
        expression="very nice",
        why='"It" can mean the film or the visit.',
        recommendation="The film was very nice",
    )
    marks = marks_for(align(raw, raw, CorrectionReview(unclear=(note,))), _LABELS)

    assert len(marks) == 1
    assert (marks[0].start, marks[0].end) == (7, 16)
    assert not marks[0].highlight
    assert marks[0].comment == unclear_comment(note, _LABELS)


def test_marks_for_skips_an_unclear_expression_without_a_span() -> None:
    """
    Given  an unclear expression the producer could not find in the text
    When   marks_for() is called
    Then   no mark is made for it (it stays in the table of the Word file)
    """
    raw = "It was very nice."
    note = UnclearNote(
        raw_start=0, raw_end=0, expression="not there", why="Why.", recommendation="What."
    )

    assert marks_for(align(raw, raw, CorrectionReview(unclear=(note,))), _LABELS) == []


def test_marks_for_makes_nothing_of_identical_texts() -> None:
    """
    Given  a text that was not corrected
    When   marks_for() is called
    Then   there is no mark at all
    """
    raw = "Nothing to change here."

    assert marks_for(align(raw, raw, CorrectionReview()), _LABELS) == []


# ── write_marked_text ─────────────────────────────────────────────────────


def test_write_marked_text_highlights_a_hunk_across_a_line_break() -> None:
    """
    Given  a mark that runs from one line into the next
    When   write_marked_text() writes the text
    Then   both lines are highlighted and the comment sits on the first line only
    """
    cell = _cell()
    comments: list[tuple[int, str]] = []

    write_marked_text(
        cell, "one two\nthree four", [Mark(start=4, end=13, comment="c", highlight=True)], comments
    )

    assert [para.text for para in cell.paragraphs] == ["one two", "three four"]
    assert _highlighted(cell) == ["two", "three"]
    assert _commented_text(cell, 0) == "two"
    assert comments == [(0, "c")]


def test_write_marked_text_comments_an_unclear_expression_over_its_words() -> None:
    """
    Given  an unclear expression that ends a sentence
    When   write_marked_text() writes the text
    Then   the comment covers exactly its words and nothing is highlighted
    """
    cell = _cell()
    comments: list[tuple[int, str]] = []

    write_marked_text(
        cell, "It was very nice.", [Mark(start=7, end=16, comment="c", highlight=False)], comments
    )

    assert _commented_text(cell, 0) == "very nice"
    assert _highlighted(cell) == []


def test_write_marked_text_comments_the_occurrence_the_span_names() -> None:
    """
    Given  an unclear expression whose words also appear earlier in the text
    When   write_marked_text() writes the text
    Then   the comment sits on the stored span, not on the first match
    """
    cell = _cell()
    comments: list[tuple[int, str]] = []

    write_marked_text(
        cell,
        "The nice thing is nice.",
        [Mark(start=18, end=22, comment="c", highlight=False)],
        comments,
    )

    assert _text_before_comment(cell, 0) == "The nice thing is "
    assert _commented_text(cell, 0) == "nice"


def test_write_marked_text_writes_an_insertion_marker_at_its_place() -> None:
    """
    Given  an insertion mark in the middle of a line
    When   write_marked_text() writes the text
    Then   the marker is written there, highlighted, and the comment covers it
    """
    cell = _cell()
    comments: list[tuple[int, str]] = []

    write_marked_text(
        cell,
        "I like it.",
        [Mark(start=2, end=2, comment="c", highlight=True, marker="[+really]")],
        comments,
    )

    assert cell.text == "I [+really]like it."
    assert _highlighted(cell) == ["[+really]"]
    assert _commented_text(cell, 0) == "[+really]"


def test_write_marked_text_writes_an_insertion_at_the_end_of_a_line() -> None:
    """
    Given  an insertion mark exactly at a line's end
    When   write_marked_text() writes the text
    Then   the marker belongs to that line and not to the next one
    """
    cell = _cell()
    comments: list[tuple[int, str]] = []

    write_marked_text(
        cell,
        "one\ntwo",
        [Mark(start=3, end=3, comment="c", highlight=True, marker="[+x]")],
        comments,
    )

    assert [para.text for para in cell.paragraphs] == ["one[+x]", "two"]
    assert _commented_text(cell, 0) == "[+x]"


def test_write_marked_text_accepts_a_hunk_inside_an_unclear_expression() -> None:
    """
    Given  a change mark inside an unclear mark
    When   write_marked_text() writes the text
    Then   both ranges are written and the document opens again in python-docx
    """
    cell = _cell()
    comments: list[tuple[int, str]] = []

    write_marked_text(
        cell,
        "It was very nice.",
        [
            Mark(start=7, end=16, comment="unclear", highlight=False),
            Mark(start=7, end=11, comment="change", highlight=True),
        ],
        comments,
    )

    assert _commented_text(cell, 0) == "very nice"
    assert _commented_text(cell, 1) == "very"
    assert _highlighted(cell) == ["very"]
    assert comments == [(0, "unclear"), (1, "change")]


def test_write_marked_text_round_trips_through_a_stored_file(tmp_path: Path) -> None:
    """
    Given  a text with a change and an unclear expression
    When   the document is stored and read again
    Then   python-docx opens it and the text is unchanged
    """
    doc = Document()
    cell = doc.add_table(rows=1, cols=1).rows[0].cells[0]
    comments: list[tuple[int, str]] = []
    write_marked_text(
        cell,
        "It was very nice.",
        [
            Mark(start=7, end=16, comment="unclear", highlight=False),
            Mark(start=7, end=11, comment="change", highlight=True),
        ],
        comments,
    )
    out = tmp_path / "marked.docx"
    doc.save(str(out))

    reopened = Document(str(out))

    assert reopened.tables[0].rows[0].cells[0].text == "It was very nice."


def test_write_marked_text_without_marks_writes_the_plain_text() -> None:
    """
    Given  no marks at all
    When   write_marked_text() writes the text
    Then   every line is one paragraph and no comment is anchored
    """
    cell = _cell()
    comments: list[tuple[int, str]] = []

    write_marked_text(cell, "first line\n\nthird line", [], comments)

    assert [para.text for para in cell.paragraphs] == ["first line", "", "third line"]
    assert comments == []
    assert (
        "commentReference" not in cell._tc.xml
    )  # python-docx exposes no public API for reading OOXML comment ranges


def test_write_marked_text_writes_notation_as_it_is() -> None:
    """
    Given  Markdown notation in the text
    When   write_marked_text() writes it
    Then   it is written as plain characters, without bold or strikethrough runs
    """
    cell = _cell()

    write_marked_text(cell, "I ~~hate~~ **love** this", [], [])

    assert cell.text == "I ~~hate~~ **love** this"
    assert not any(run.bold or run.font.strike for para in cell.paragraphs for run in para.runs)


def test_write_marked_text_keeps_the_text_of_every_line() -> None:
    """
    Given  a text with a change on the second line
    When   write_marked_text() writes it
    Then   the lines read as before and only the changed words are highlighted
    """
    cell = _cell()
    comments: list[tuple[int, str]] = []

    write_marked_text(
        cell,
        "first line\nsecond line",
        [Mark(start=18, end=22, comment="c", highlight=True)],
        comments,
    )

    assert [para.text for para in cell.paragraphs] == ["first line", "second line"]
    assert _highlighted(cell) == ["line"]
    assert _commented_text(cell, 0) == "line"


def _part(raw: str, original: str, corrected: str) -> Any:
    """Return the edit part of *original* in *raw*, replaced by *corrected*."""
    from teachers_teammate.domain.proofreading.edits import EditPart  # noqa: PLC0415

    start = raw.index(original)
    return EditPart(
        raw_start=start, raw_end=start + len(original), original=original, corrected=corrected
    )


@pytest.mark.parametrize("text", ["", "one line", "one\ntwo\n", "\n\n"])
def test_write_marked_text_handles_short_texts(text: str) -> None:
    """
    Given  an empty, single-line or blank-line text
    When   write_marked_text() writes it without marks
    Then   the paragraphs read back as the lines of the text
    """
    cell = _cell()

    write_marked_text(cell, text, [], [])

    assert [para.text for para in cell.paragraphs] == text.split("\n")


def test_aligned_review_of_no_review_has_no_marks() -> None:
    """
    Given  a corrected text but no review at all
    When   marks_for() is called on its alignment
    Then   every hunk is a mark without an explanation
    """
    raw = "Yesterday she have gone."
    marks = marks_for(align(raw, "Yesterday she has gone.", CorrectionReview()), _LABELS)

    assert len(marks) == 1
    assert marks[0].comment == '\u2192 "has"'


def test_marks_for_keeps_the_text_order_of_changes_and_unclear_spans() -> None:
    """
    Given  a change before an unclear expression
    When   marks_for() is called
    Then   the change comes first, so the comments read in text order
    """
    raw = "She have a nice plan."
    note = UnclearNote(
        raw_start=11, raw_end=20, expression="nice plan", why="Why.", recommendation="What."
    )
    review = _review(raw, "She has a nice plan.", unclear=(note,))
    marks = marks_for(align(raw, "She has a nice plan.", review), _LABELS)

    assert [(mark.start, mark.highlight) for mark in marks] == [(4, True), (11, False)]


def test_marks_for_returns_an_aligned_review_unchanged() -> None:
    """
    Given  an aligned review with no changes and no unclear spans
    When   marks_for() is called
    Then   it returns an empty list
    """
    assert marks_for(AlignedReview(), _LABELS) == []


def test_marks_for_ignores_an_unclear_span_that_was_not_anchored() -> None:
    """
    Given  an aligned unclear expression without a span
    When   marks_for() is called
    Then   no mark is made for it
    """
    note = UnclearNote(raw_start=0, raw_end=0, expression="x", why="Why.", recommendation="What.")
    aligned = AlignedReview(unclear=(AlignedUnclear(number=1, note=note),))

    assert marks_for(aligned, _LABELS) == []


def test_write_marked_text_comments_a_deleted_line_break_on_the_line_before_it() -> None:
    """
    Given  a mark over the line break of "foo\nbar" (the break is replaced by a space)
    When   write_marked_text() writes the text
    Then   the comment is not lost: it covers the last words before the break
    """
    cell = _cell()
    comments: list[tuple[int, str]] = []

    write_marked_text(cell, "foo\nbar", [Mark(3, 4, "joined", highlight=True)], comments)

    assert [text for _id, text in comments] == ["joined"]
    assert _commented_text(cell, 0) == "foo"


def test_write_marked_text_comments_a_deleted_empty_line() -> None:
    """
    Given  a mark over the line break after an empty line
    When   write_marked_text() writes the text
    Then   the comment is still written, on the empty line
    """
    cell = _cell()
    comments: list[tuple[int, str]] = []

    write_marked_text(cell, "foo\n\nbar", [Mark(4, 5, "gone", highlight=True)], comments)

    assert [text for _id, text in comments] == ["gone"]


def test_marks_for_comments_the_line_of_an_unclear_expression_without_a_span() -> None:
    """
    Given  an unclear note whose span was not found, and the raw text that holds its words
    When   marks_for() is called with that text
    Then   the note is commented on the whole line that holds the words, without highlight
    """
    raw = "First line.\nIt was very nice, really.\nLast line."
    note = UnclearNote(0, 0, "very nice", "Vague.", "Say what was nice.")
    aligned = align(raw, raw, CorrectionReview(unclear=(note,)))

    marks = marks_for(aligned, _LABELS, raw)

    assert [(mark.start, mark.end, mark.highlight) for mark in marks] == [(12, 37, False)]
    assert marks_for(aligned, _LABELS) == []
