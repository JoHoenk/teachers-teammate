"""Unit tests for teachers_teammate.docx_builder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
import pytest

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    EditNote,
    UnclearNote,
)
from teachers_teammate.domain.proofreading.word_diff import word_diff
from teachers_teammate.domain.task_text import Segment
from teachers_teammate.infrastructure.docx_builder import DocxDocumentCreator

# ── table format ───────────────────────────────────────────────────────────


@pytest.mark.use_case("DOCX_Report_Export")
def test_create_table_format_produces_docx_file(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a DocxDocumentCreator in table format with raw + corrected text
    When   create() is called
    Then   a .docx file exists at out_path
    """
    out = tmp_path / "test.docx"
    creator = DocxDocumentCreator(fmt="table")
    creator.create(
        raw_text="raw ocr output",
        corrected_text="corrected output",
        out_path=out,
        title="Test Page",
        source_image=sample_png,
    )
    assert out.is_file()
    assert out.stat().st_size > 0


def test_create_table_format_without_image(tmp_path: Path) -> None:
    """
    Given  no source_image supplied
    When   create() is called in table format
    Then   a valid .docx file is produced (image cell left blank)
    """
    out = tmp_path / "no_image.docx"
    creator = DocxDocumentCreator(fmt="table")
    creator.create(
        raw_text="text",
        corrected_text=None,
        out_path=out,
        title="No Image",
    )
    assert out.is_file()


def test_create_table_format_contains_title(tmp_path: Path) -> None:
    """
    Given  title='My Title'
    When   create() produces a table-format DOCX
    Then   'My Title' appears in the document's paragraphs
    """
    out = tmp_path / "titled.docx"
    creator = DocxDocumentCreator(fmt="table")
    creator.create(
        raw_text="some text",
        corrected_text="corrected",
        out_path=out,
        title="My Title",
    )
    doc = Document(str(out))
    all_text = " ".join(p.text for p in doc.paragraphs)
    assert "My Title" in all_text


def test_create_table_format_contains_raw_and_corrected_text(tmp_path: Path) -> None:
    """
    Given  raw_text='raw content' and corrected_text='corrected content'
    When   create() produces a table-format DOCX
    Then   both strings appear somewhere in the document
    """
    out = tmp_path / "content.docx"
    creator = DocxDocumentCreator(fmt="table")
    creator.create(
        raw_text="raw content",
        corrected_text="corrected content",
        out_path=out,
        title="Test",
    )
    doc = Document(str(out))
    all_text = " ".join(
        cell.text for table in doc.tables for row in table.rows for cell in row.cells
    )
    assert "raw content" in all_text
    assert "corrected content" in all_text


def test_create_table_with_none_corrected_text(tmp_path: Path) -> None:
    """
    Given  corrected_text=None (correction disabled)
    When   create() is called
    Then   a .docx file is produced without error
    """
    out = tmp_path / "no_corr.docx"
    creator = DocxDocumentCreator(fmt="table")
    creator.create(raw_text="raw", corrected_text=None, out_path=out, title="T")
    assert out.is_file()


# ── comments format ────────────────────────────────────────────────────────


@pytest.mark.use_case("DOCX_Report_Export")
def test_create_comments_format_produces_docx_file(tmp_path: Path) -> None:
    """
    Given  a DocxDocumentCreator in comments format
    When   create() is called with identical raw and corrected text
    Then   a .docx file is produced
    """
    out = tmp_path / "comments.docx"
    creator = DocxDocumentCreator(fmt="comments")
    creator.create(
        raw_text="hello world",
        corrected_text="hello world",
        out_path=out,
        title="Comments Test",
    )
    assert out.is_file()
    assert out.stat().st_size > 0


def test_create_comments_format_with_diff(tmp_path: Path) -> None:
    """
    Given  raw_text and corrected_text that differ
    When   create() is called in comments format
    Then   a .docx is produced without error
    """
    out = tmp_path / "diff.docx"
    creator = DocxDocumentCreator(fmt="comments")
    creator.create(
        raw_text="The qick brown fox",
        corrected_text="The quick brown fox",
        out_path=out,
        title="Diff Test",
    )
    assert out.is_file()


def test_create_comments_format_with_multiline_diff_attaches_comments_xml(
    tmp_path: Path,
) -> None:
    """
    Given  multi-line raw and corrected text with many differences
    When   create() is called in comments format
    Then   a .docx is produced with a comments.xml part attached
    """
    import zipfile  # noqa: PLC0415

    out = tmp_path / "multi_diff.docx"
    creator = DocxDocumentCreator(fmt="comments")
    creator.create(
        raw_text="helo world\nthis iz rong\nextra deleted line",
        corrected_text="hello world\nthis is correct\nnew inserted line",
        out_path=out,
        title="MultiDiff",
    )
    assert out.is_file()
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert any("comment" in n.lower() for n in names)


def test_create_comments_format_with_image(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  fmt='comments', differing raw and corrected text, and a source image
    When   create() is called
    Then   a .docx is written with the image embedding code path exercised
    """
    out = tmp_path / "img_comments.docx"
    creator = DocxDocumentCreator(fmt="comments")
    creator.create(
        raw_text="hello wrold",
        corrected_text="hello world",
        out_path=out,
        title="Doc",
        source_image=sample_png,
    )
    assert out.is_file()


def test_insert_image_writes_unavailable_text_on_failure(tmp_path: Path) -> None:
    """
    Given  an image path that does not exist (triggers add_picture exception)
    When   _insert_image() is called directly
    Then   no exception propagates and the run text is set to '(image unavailable)'
    """
    from docx import Document  # noqa: PLC0415
    from docx.shared import Inches  # noqa: PLC0415

    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    DocxDocumentCreator._insert_image(cell, "/nonexistent/path.png", Inches(2))
    run_texts = [r.text for r in cell.paragraphs[0].runs]
    assert any("unavailable" in t for t in run_texts)


# ── Internal helpers ───────────────────────────────────────────────────────


def test_table_layout_shows_a_placeholder_for_figures_without_alt_text(tmp_path: Path) -> None:
    """
    Given  Markdown raw text with an image that has no alt text
    When   the table layout is created
    Then   the OCR cell shows "[image]" like the preview and the comments layout do
    """
    out = tmp_path / "figure.docx"

    DocxDocumentCreator(fmt="table").create(
        raw_text="before\n\n![]()\n\n**after**",
        corrected_text=None,
        out_path=out,
        title="t",
        text_format="markdown",
    )

    cell_text = Document(str(out)).tables[0].rows[1].cells[1].text
    assert cell_text.split("\n") == ["before", "[image]", "after"]


def test_attach_comments_part_adds_relationship_to_document(tmp_path: Path) -> None:
    """
    Given  a Document and a list of (comment_id, text) pairs
    When   _attach_comments_part() is called
    Then   the document part has a relationship pointing to comments XML
    """
    from docx import Document  # noqa: PLC0415

    doc = Document()
    DocxDocumentCreator._attach_comments_part(doc, [(0, "→ correction"), (1, "→ (delete)")])
    rels = doc.part.rels
    assert any("comment" in str(v.reltype).lower() for v in rels.values())


def test_create_comments_format_deleted_lines(tmp_path: Path) -> None:
    """
    Given  raw text has extra lines not in corrected (delete opcode at line level)
    When   create() in comments format
    Then   a .docx is produced without error
    """
    out = tmp_path / "deleted.docx"
    creator = DocxDocumentCreator(fmt="comments")
    creator.create(
        raw_text="keep this\ndelete this\nkeep also",
        corrected_text="keep this\nkeep also",
        out_path=out,
        title="Deleted",
    )
    assert out.is_file()


def test_create_comments_format_inserted_lines(tmp_path: Path) -> None:
    """
    Given  corrected text has extra lines not in raw (insert opcode at line level)
    When   create() in comments format
    Then   a .docx is produced without error
    """
    out = tmp_path / "inserted.docx"
    creator = DocxDocumentCreator(fmt="comments")
    creator.create(
        raw_text="first line\nlast line",
        corrected_text="first line\nnew middle line\nlast line",
        out_path=out,
        title="Inserted",
    )
    assert out.is_file()


# ── private-API smoke tests (B5 pin guard) ─────────────────────────────────
# These tests verify that the python-docx private attributes used in docx_builder
# (_tc for cell shading, _r for comment range anchors) still work after package
# updates.  If a python-docx upgrade renames or removes these attributes, the
# tests fail here — before any user is affected.


def test_table_format_round_trip_is_parseable_with_shaded_header(tmp_path: Path) -> None:
    """Given raw and corrected text, when table format is written, then the .docx is
    readable by python-docx and contains a table with at least two header cells."""
    out = tmp_path / "smoke_table.docx"
    DocxDocumentCreator(fmt="table").create("raw text", "corrected text", out, "title")

    doc = Document(str(out))
    assert len(doc.tables) >= 1
    assert len(doc.tables[0].rows[0].cells) >= 2


def test_comments_format_round_trip_is_parseable(tmp_path: Path) -> None:
    """Given raw and corrected text, when comments format is written, then the .docx is
    readable by python-docx and contains paragraphs."""
    out = tmp_path / "smoke_comments.docx"
    DocxDocumentCreator(fmt="comments").create("raw text", "corrected text", out, "title")

    assert Document(str(out)).paragraphs


# ── Markdown formatting ────────────────────────────────────────────────────


def _runs(doc: Any, text: str) -> list:
    """All runs in the document (body + table cells) whose text equals *text*."""
    found = []
    paragraphs = list(doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
    for para in paragraphs:
        found.extend(run for run in para.runs if run.text == text)
    return found


def test_table_format_keeps_markdown_styles_as_word_runs(tmp_path: Path) -> None:
    """
    Given  Markdown OCR text with bold, a struck-out word and a superscript insertion
    When   create(text_format="markdown") writes the table layout
    Then   the runs carry bold / strike / superscript and no Markdown markers are left
    """
    out = tmp_path / "md.docx"

    DocxDocumentCreator(fmt="table").create(
        raw_text="**Dear diary** my ~~backpack~~ <sup>passport</sup> and *me*",
        corrected_text=None,
        out_path=out,
        title="T",
        text_format="markdown",
    )

    doc = Document(str(out))
    assert _runs(doc, "Dear diary")[0].bold
    assert _runs(doc, "backpack")[0].font.strike
    assert _runs(doc, "passport")[0].font.superscript
    assert _runs(doc, "me")[0].italic
    all_text = " ".join(
        p.text for t in doc.tables for r in t.rows for c in r.cells for p in c.paragraphs
    )
    assert "**" not in all_text
    assert "~~" not in all_text
    assert "<sup>" not in all_text


def test_table_format_writes_task_text_grey_and_italic(tmp_path: Path) -> None:
    """
    Given  a proofread text which is a printed task line above the student's own sentence
    When   create() writes the table layout with that line marked as task text
    Then   the task line is grey and italic, and the student's sentence stands as it is
    """
    out = tmp_path / "task.docx"
    task_line = "Aufgabe 1: Lies den Text!"
    corrected = f"{task_line}\nAnna schreibt einen Brief."
    split = len(task_line)

    DocxDocumentCreator(fmt="table").create(
        raw_text=corrected,
        corrected_text=corrected,
        out_path=out,
        title="T",
        task=(Segment(0, split, "task"), Segment(split, len(corrected), "student")),
    )

    doc = Document(str(out))
    task_run = _runs(doc, task_line)[0]
    assert task_run.italic
    assert str(task_run.font.color.rgb) == "666666"
    student_run = _runs(doc, "Anna schreibt einen Brief.")[0]
    assert not student_run.italic


def test_table_format_keeps_markdown_and_greys_its_task_text(tmp_path: Path) -> None:
    """
    Given  a proofread Markdown text: a printed heading above the student's sentence with a bold
           word, the heading marked as task text
    When   create() writes the table layout
    Then   the heading is grey and italic, and the student's bold word is still bold
    """
    out = tmp_path / "task.docx"
    corrected = "# Aufgabe 1 Lies den Text\n\nAnna schreibt **einen** Brief."
    plain = "Aufgabe 1 Lies den Text\n\nAnna schreibt einen Brief."
    split = len("Aufgabe 1 Lies den Text")

    DocxDocumentCreator(fmt="table").create(
        raw_text=corrected,
        corrected_text=corrected,
        out_path=out,
        title="T",
        text_format="markdown",
        task=(Segment(0, split, "task"), Segment(split, len(plain), "student")),
    )

    doc = Document(str(out))
    heading = [run for run in _runs(doc, "Aufgabe 1 Lies den Text") if run.italic]
    assert heading
    assert str(heading[0].font.color.rgb) == "666666"
    bold = _runs(doc, "einen")
    assert any(run.bold and not run.italic for run in bold)


def test_table_format_plain_text_is_written_verbatim(tmp_path: Path) -> None:
    """
    Given  plain text containing Markdown-looking characters
    When   create() is called with the default text_format
    Then   the characters are written verbatim, without any formatting
    """
    out = tmp_path / "plain.docx"

    DocxDocumentCreator(fmt="table").create(
        raw_text="**not bold**", corrected_text="~~not struck~~", out_path=out, title="T"
    )

    doc = Document(str(out))
    assert _runs(doc, "**not bold**")
    assert _runs(doc, "~~not struck~~")
    assert not _runs(doc, "**not bold**")[0].bold


def test_table_format_renders_markdown_lists_headings_and_tables(tmp_path: Path) -> None:
    """
    Given  Markdown with a heading, a bullet list and a table
    When   create(text_format="markdown") writes the table layout
    Then   the heading is bold, list items are indented paragraphs and the table is nested
    """
    out = tmp_path / "blocks.docx"

    DocxDocumentCreator(fmt="table").create(
        raw_text="# Title\n\n- one\n- two\n\n| A | B |\n| --- | --- |\n| 1 | 2 |",
        corrected_text=None,
        out_path=out,
        title="T",
        text_format="markdown",
    )

    doc = Document(str(out))
    assert _runs(doc, "Title")[0].bold
    cell = doc.tables[0].rows[1].cells[1]
    items = [p for p in cell.paragraphs if p.text.startswith("1. ") or "one" in p.text]
    assert items
    assert items[0].paragraph_format.left_indent is not None
    nested = cell.tables
    assert len(nested) == 1
    assert [[c.text for c in row.cells] for row in nested[0].rows] == [["A", "B"], ["1", "2"]]


def test_comments_format_writes_markdown_as_plain_text(tmp_path: Path) -> None:
    """
    Given  Markdown OCR text with a struck word and a correction that fixes a typo
    When   create(text_format="markdown") writes the comments layout
    Then   the cell holds the plain text without markers and the typo is commented
    """
    out = tmp_path / "md_comments.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text="I ~~hate~~ lov this",
        corrected_text="I ~~hate~~ love this",
        out_path=out,
        title="T",
        text_format="markdown",
    )

    doc = Document(str(out))
    cell_text = doc.tables[0].rows[1].cells[1].text
    assert cell_text == "I hate lov this"
    assert "commentReference" in doc.element.body.xml
    assert '→ "love"' in _comments_xml(out)


def _comments_xml(path: Path) -> str:
    import zipfile  # noqa: PLC0415

    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if "comments" in n.lower())
        return archive.read(name).decode("utf-8")


# ── Proofreading review: explanations and the unclear table ───────────────

_RAW = "Yesterday she have gone to the cinema. It was very nice."
_CORRECTED = "Yesterday she has gone to the cinema. It was very nice."


def _review_of(raw: str, corrected: str, *notes: tuple[str, str, str, str]) -> CorrectionReview:
    """Return a review whose edits are the hunks of *raw* → *corrected*.

    Each note is ``(original, corrected, category, explanation)`` and explains the hunk that
    replaces exactly those words; a hunk without a note carries no explanation.  The model's
    edits are the hunks of the same word diff, so this is what a real review looks like.
    """
    edits = []
    for index, hunk in enumerate(word_diff(raw, corrected), start=1):
        note = next(
            (entry for entry in notes if entry[0] == hunk.original and entry[1] == hunk.corrected),
            None,
        )
        edits.append(
            EditNote(
                id=index,
                parts=(EditPart(hunk.raw_start, hunk.raw_end, hunk.original, hunk.corrected),),
                sentences=(),
                category=note[2] if note is not None else "other",
                explanation=note[3] if note is not None else "",
            )
        )
    return CorrectionReview(edits=tuple(edits))


def _unclear(
    raw: str, expression: str, why: str, recommendation: str, *, suggestion: bool = False
) -> UnclearNote:
    """Return an unclear note anchored on the first occurrence of *expression* in *raw*."""
    start = raw.find(expression)
    return UnclearNote(start, start + len(expression), expression, why, recommendation, suggestion)


_REVIEW = CorrectionReview(
    edits=_review_of(
        _RAW, _CORRECTED, ("have", "has", "grammar", '"she" is 3rd person singular.')
    ).edits,
    unclear=(
        _unclear(
            _RAW,
            "It was very nice",
            '"It" can mean the film or the visit.',
            "The film was very nice",
            suggestion=True,
        ),
        _unclear(
            _RAW,
            "to the cinema",
            "Which cinema is meant is not stated.",
            "to the local cinema",
        ),
    ),
)


def _all_table_text(path: Path) -> str:
    """Return the text of every cell of every table of the stored document."""
    doc = Document(str(path))
    return " | ".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells)


def _headings(path: Path) -> list[str]:
    """Return the text of every heading-styled paragraph."""
    return [para.text for para in Document(str(path)).paragraphs if para.style.name.startswith("H")]


def test_comments_layout_puts_the_explanation_in_the_change_comment(tmp_path: Path) -> None:
    """
    Given  a review whose change "have" → "has" has a grammar explanation
    When   the comments layout is written
    Then   the comment above that correction names the category and the explanation
    """
    out = tmp_path / "review_comments.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=_RAW,
        corrected_text=_CORRECTED,
        out_path=out,
        title="T",
        review=_REVIEW,
    )

    comments_xml = _comments_xml(out)
    assert '\u2192 "has"' in comments_xml
    assert 'Grammar: "she" is 3rd person singular.' in comments_xml


def test_comments_layout_comments_unclear_expressions(tmp_path: Path) -> None:
    """
    Given  a review with one unclear expression that can be recommended and one that is
           only a suggestion
    When   the comments layout is written
    Then   each gets its own comment, the first naming why and recommending, the second
           labelled as a suggestion
    """
    out = tmp_path / "unclear_comments.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=_RAW,
        corrected_text=_CORRECTED,
        out_path=out,
        title="T",
        review=_REVIEW,
    )

    comments_xml = _comments_xml(out)
    assert "Suggestion (meaning not certain): The film was very nice" in comments_xml
    assert (
        "Unclear: Which cinema is meant is not stated. \u2014 Recommended: to the local cinema"
        in comments_xml
    )


def test_comments_layout_comments_unclear_expressions_without_a_corrected_text(
    tmp_path: Path,
) -> None:
    """
    Given  a review with an unclear expression and no corrected text
    When   the comments layout is written
    Then   the expression still gets its comment
    """
    out = tmp_path / "unclear_without_correction.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=_RAW,
        corrected_text=None,
        out_path=out,
        title="T",
        review=CorrectionReview(unclear=_REVIEW.unclear),
    )

    assert "Which cinema is meant is not stated." in _comments_xml(out)


def test_comments_layout_comments_the_line_of_an_unclear_expression_without_a_span(
    tmp_path: Path,
) -> None:
    """
    Given  an unclear expression whose span was not found in the text
    When   the comments layout is written
    Then   the comment is anchored on the line that holds its words
    """
    out = tmp_path / "unclear_without_span.docx"
    note = UnclearNote(0, 0, "very nice", "Vague.", "Say what was nice.")

    DocxDocumentCreator(fmt="comments").create(
        raw_text=_RAW,
        corrected_text=_CORRECTED,
        out_path=out,
        title="T",
        review=CorrectionReview(unclear=(note,)),
    )

    assert "Unclear: Vague." in _comments_xml(out)


def test_unclear_table_is_appended_with_three_columns(tmp_path: Path) -> None:
    """
    Given  a review with two unclear expressions
    When   either layout is written
    Then   the *Unclear or ambiguous expressions* heading comes with a three-column table
           holding the three column titles and one row per expression
    """
    for fmt in ("table", "comments"):
        out = tmp_path / f"unclear_{fmt}.docx"

        DocxDocumentCreator(fmt=fmt).create(
            raw_text=_RAW,
            corrected_text=_CORRECTED,
            out_path=out,
            title="T",
            review=_REVIEW,
        )

        assert "Unclear or ambiguous expressions" in _headings(out)
        doc = Document(str(out))
        unclear = next(
            table for table in doc.tables if len(table.columns) == 3 and len(table.rows) == 3
        )
        assert [cell.text for cell in unclear.rows[0].cells] == [
            "Original expression",
            "Why it is unclear",
            "Recommended version",
        ]
        assert unclear.rows[1].cells[0].text == "It was very nice"
        assert "Suggestion (meaning not certain)" in unclear.rows[1].cells[2].text
        assert unclear.rows[2].cells[2].text == "Recommended: to the local cinema"


def test_table_layout_lists_the_explanations_per_sentence(tmp_path: Path) -> None:
    """
    Given  a review with one changed sentence
    When   the table layout is written
    Then   an *Explanations* section shows the corrected sentence in italics followed by
           one bullet per change
    """
    out = tmp_path / "explanations.docx"

    DocxDocumentCreator(fmt="table").create(
        raw_text=_RAW,
        corrected_text=_CORRECTED,
        out_path=out,
        title="T",
        review=_REVIEW,
    )

    assert "Explanations" in _headings(out)
    doc = Document(str(out))
    corrected = next(p for p in doc.paragraphs if p.text.startswith("1. "))
    assert corrected.runs[1].text == "Yesterday she has gone to the cinema."
    assert corrected.runs[1].italic
    bullets = [p.text for p in doc.paragraphs if p.style.name == "List Bullet"]
    assert any("Grammar: " in bullet and "3rd person singular" in bullet for bullet in bullets)


def test_explanations_list_leaves_out_spelling_and_punctuation(tmp_path: Path) -> None:
    """
    Given  a review whose text has a grammar change and a spelling change
    When   the table layout is written
    Then   the *Explanations* section lists the grammar change only
    """
    raw, corrected = "She have a dogs.", "She has a dog."
    review = _review_of(
        raw,
        corrected,
        ("have", "has", "grammar", "Third person."),
        ("dogs", "dog", "spelling", "Singular."),
    )
    out = tmp_path / "minor.docx"

    DocxDocumentCreator(fmt="table").create(
        raw_text=raw, corrected_text=corrected, out_path=out, title="T", review=review
    )

    bullets = [p.text for p in Document(str(out)).paragraphs if p.style.name == "List Bullet"]
    assert any("Third person." in bullet for bullet in bullets)
    assert not any("Singular." in bullet for bullet in bullets)


def test_review_sections_use_the_explanation_language(tmp_path: Path) -> None:
    """
    Given  a creator whose explanation language is German
    When   the review sections are written
    Then   the headings, column titles and category names are German
    """
    out = tmp_path / "german.docx"

    DocxDocumentCreator(fmt="table", labels_language="de").create(
        raw_text=_RAW,
        corrected_text=_CORRECTED,
        out_path=out,
        title="T",
        review=_REVIEW,
    )

    headings = _headings(out)
    assert "Erläuterungen" in headings
    assert "Unklare oder mehrdeutige Formulierungen" in headings
    table_text = _all_table_text(out)
    assert "Ursprüngliche Formulierung" in table_text
    assert "Empfohlene Version" in table_text
    bullets = [p.text for p in Document(str(out)).paragraphs if p.style.name == "List Bullet"]
    assert any("Grammatik: " in bullet for bullet in bullets)


def test_without_review_the_document_keeps_todays_output(tmp_path: Path) -> None:
    """
    Given  no review (a file proofread in *Corrections only* mode)
    When   either layout is written
    Then   no review section appears, and the comments carry only the plain change text
    """
    table_out = tmp_path / "no_review_table.docx"
    comments_out = tmp_path / "no_review_comments.docx"

    DocxDocumentCreator(fmt="table").create(
        raw_text=_RAW, corrected_text=_CORRECTED, out_path=table_out, title="T"
    )
    DocxDocumentCreator(fmt="comments").create(
        raw_text=_RAW, corrected_text=_CORRECTED, out_path=comments_out, title="T"
    )

    assert _headings(table_out) == ["T"]
    assert len(Document(str(table_out)).tables) == 1
    comments_xml = _comments_xml(comments_out)
    assert "Grammar:" not in comments_xml
    assert "Unclear:" not in comments_xml
    assert '\u2192 "has"' in comments_xml


# ── Review: matching explanations to the diff, anchoring unclear comments ──


def _comment_texts(path: Path) -> dict[int, str]:
    """Return ``{comment id: text}`` of the stored comments part."""
    from lxml import etree  # noqa: PLC0415

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    root = etree.fromstring(_comments_xml(path).encode("utf-8"))
    return {
        int(comment.get(f"{{{ns['w']}}}id")): "".join(comment.itertext())
        for comment in root.findall("w:comment", ns)
    }


def _text_under_comment(path: Path, comment_id: int) -> str:
    """Return the document text between a comment's range start and range end."""
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    inside = False
    parts: list[str] = []
    for element in Document(str(path)).element.body.iter():
        if element.tag == f"{w}commentRangeStart" and element.get(f"{w}id") == str(comment_id):
            inside = True
        elif element.tag == f"{w}commentRangeEnd" and element.get(f"{w}id") == str(comment_id):
            break
        elif inside and element.tag == f"{w}t":
            parts.append(element.text or "")
    return "".join(parts)


def test_comments_layout_explains_multi_word_and_punctuation_edits(tmp_path: Path) -> None:
    """
    Given  notes for a two-word edit, an inserted comma and an added full stop
    When   the comments layout is written
    Then   every one of those comments carries its explanation
    """
    raw = "Yesterday she have gone with his friend. We met Tom and left"
    corrected = "Yesterday she has gone with her friends. We met Tom, and left."
    review = _review_of(
        raw,
        corrected,
        ("have", "has", "grammar", "EXPL-has"),
        ("his", "her", "word_choice", "EXPL-his"),
        ("friend", "friends", "word_choice", "EXPL-friend"),
        ("", ",", "punctuation", "EXPL-comma"),
        ("", ".", "punctuation", "EXPL-period"),
    )
    out = tmp_path / "multi.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=raw, corrected_text=corrected, out_path=out, title="T", review=review
    )

    comments = _comments_xml(out)
    for expected in ("EXPL-has", "EXPL-his", "EXPL-friend", "EXPL-comma", "EXPL-period"):
        assert expected in comments


def test_comments_layout_marks_an_insertion_with_its_words(tmp_path: Path) -> None:
    """
    Given  a correction that only inserts a comma
    When   the comments layout is written
    Then   the comma is written as a marker at its place and carries the comment
    """
    raw = "We met Tom and left"
    corrected = "We met Tom, and left"
    review = _review_of(raw, corrected, ("", ",", "punctuation", "EXPL-comma"))
    out = tmp_path / "insertion.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=raw, corrected_text=corrected, out_path=out, title="T", review=review
    )

    cell_text = Document(str(out)).tables[0].rows[1].cells[1].text
    assert cell_text == "We met Tom[+,] and left"
    comments = _comment_texts(out)
    comma_id = next(i for i, text in comments.items() if "EXPL-comma" in text)
    assert _text_under_comment(out, comma_id) == "[+,]"


def test_comments_layout_highlights_a_change_across_a_line_break(tmp_path: Path) -> None:
    """
    Given  a change whose words run from one line into the next
    When   the comments layout is written
    Then   both lines are highlighted and the comment sits on the first one only
    """
    raw = "a b\nc d"
    corrected = "a x d"
    review = _review_of(raw, corrected, ("b\nc", "x", "grammar", "EXPL-across"))
    out = tmp_path / "across.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=raw, corrected_text=corrected, out_path=out, title="T", review=review
    )

    doc = Document(str(out))
    cell = doc.tables[0].rows[1].cells[1]
    assert [para.text for para in cell.paragraphs] == ["a b", "c d"]
    highlighted = [
        run.text
        for para in cell.paragraphs
        for run in para.runs
        if run.font.highlight_color is not None
    ]
    assert highlighted == ["b", "c"]
    comments = _comment_texts(out)
    assert len(comments) == 1
    assert _text_under_comment(out, next(iter(comments))) == "b"


def test_comments_layout_writes_one_paragraph_per_line(tmp_path: Path) -> None:
    """
    Given  a text of three lines, one of them empty
    When   the comments layout is written without a corrected text
    Then   the cell holds one paragraph per line and no comment at all
    """
    out = tmp_path / "lines.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text="line one\n\nline three", corrected_text=None, out_path=out, title="T"
    )

    doc = Document(str(out))
    cell = doc.tables[0].rows[1].cells[1]
    assert [para.text for para in cell.paragraphs] == ["line one", "", "line three"]
    assert "commentReference" not in doc.element.body.xml


def test_comments_layout_comments_nothing_on_identical_texts(tmp_path: Path) -> None:
    """
    Given  a corrected text that is the raw text word for word, and a review of an edit that
           is not in it
    When   the comments layout is written
    Then   no comment is attached
    """
    review = _review_of(_RAW, _CORRECTED, ("have", "has", "grammar", "EXPL-unused"))
    out = tmp_path / "identical.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=_RAW, corrected_text=_RAW, out_path=out, title="T", review=review
    )

    assert "commentReference" not in Document(str(out)).element.body.xml


def test_unclear_comment_covers_only_the_expression(tmp_path: Path) -> None:
    """
    Given  an unclear expression in the middle of a line
    When   the comments layout is written
    Then   the unclear comment's range wraps exactly those words, not the whole line
    """
    out = tmp_path / "anchored.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=_RAW, corrected_text=_CORRECTED, out_path=out, title="T", review=_REVIEW
    )

    comments = _comment_texts(out)
    unclear_id = next(i for i, text in comments.items() if "Which cinema" in text)
    assert _text_under_comment(out, unclear_id) == "to the cinema"
    nice_id = next(i for i, text in comments.items() if "The film was very nice" in text)
    assert _text_under_comment(out, nice_id) == "It was very nice"


def test_unclear_comment_covers_the_stored_span_not_a_longer_word(tmp_path: Path) -> None:
    """
    Given  the unclear expression "he", whose letters also occur inside "Then"
    When   the comments layout is written
    Then   the comment's range wraps the pronoun "he", not the letters inside "Then"
    """
    text = "Then the boy said he left."
    start = text.index(" he ") + 1
    review = CorrectionReview(
        unclear=(UnclearNote(start, start + 2, "he", "Which person?", "the boy"),)
    )
    out = tmp_path / "whole_word.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=text, corrected_text=text, out_path=out, title="T", review=review
    )

    comments = _comment_texts(out)
    unclear_id = next(i for i, comment in comments.items() if "Which person?" in comment)
    assert _text_under_comment(out, unclear_id) == "he"


def test_suggestion_comment_keeps_the_reason(tmp_path: Path) -> None:
    """
    Given  an unclear expression whose meaning cannot be determined (a suggestion)
    When   the comments layout is written
    Then   its comment names why it is unclear as well as the labelled suggestion
    """
    out = tmp_path / "suggestion.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=_RAW, corrected_text=_CORRECTED, out_path=out, title="T", review=_REVIEW
    )

    assert any(
        '"It" can mean the film or the visit.' in text
        and "Suggestion (meaning not certain): The film was very nice" in text
        for text in _comment_texts(out).values()
    )


def test_a_change_inside_an_unclear_expression_gives_two_overlapping_ranges(
    tmp_path: Path,
) -> None:
    """
    Given  a change whose words lie inside an unclear expression
    When   the comments layout is written
    Then   both comments are there, their ranges overlap, and the file still opens
    """
    raw = "It was very nice."
    corrected = "The film was very nice."
    review = CorrectionReview(
        edits=_review_of(raw, corrected, ("It", "The film", "word_choice", "EXPL-film")).edits,
        unclear=(_unclear(raw, "It was very nice", "Which visit?", "The film was very nice"),),
    )
    out = tmp_path / "overlap.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=raw, corrected_text=corrected, out_path=out, title="T", review=review
    )

    comments = _comment_texts(out)
    assert len(comments) == 2
    change_id = next(i for i, text in comments.items() if "EXPL-film" in text)
    unclear_id = next(i for i, text in comments.items() if "Which visit?" in text)
    assert _text_under_comment(out, change_id) == "It"
    assert _text_under_comment(out, unclear_id) == "It was very nice"
    assert Document(str(out)).tables[0].rows[1].cells[1].text == "It was very nice."


def test_a_two_hunk_edit_is_explained_once(tmp_path: Path) -> None:
    """
    Given  one edit of two hunks with a single explanation
    When   the comments layout is written
    Then   the first hunk carries the explanation and the second only the change text
    """
    raw, corrected = "She have a dogs.", "She has a dog."
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(4, 8, "have", "has"), EditPart(11, 15, "dogs", "dog")),
                sentences=(),
                category="grammar",
                explanation="EXPL-once",
            ),
        )
    )
    out = tmp_path / "two_hunks.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=raw, corrected_text=corrected, out_path=out, title="T", review=review
    )

    comments_xml = _comments_xml(out)
    assert comments_xml.count("EXPL-once") == 1
    assert '\u2192 "has"' in comments_xml
    assert '\u2192 "dog"' in comments_xml


def test_a_change_the_teacher_made_afterwards_is_not_explained(tmp_path: Path) -> None:
    """
    Given  a note on an edit the teacher has since changed in the corrected text
    When   either layout is written
    Then   the change is marked but its explanation is left out, because it explains words
           that are no longer there
    """
    raw = "She have gone home."
    review = _review_of(raw, "She has gone home.", ("have", "has", "grammar", "EXPL-changed"))
    table_out = tmp_path / "changed_table.docx"
    comments_out = tmp_path / "changed_comments.docx"

    DocxDocumentCreator(fmt="table").create(
        raw_text=raw,
        corrected_text="She had gone home.",
        out_path=table_out,
        title="T",
        review=review,
    )
    DocxDocumentCreator(fmt="comments").create(
        raw_text=raw,
        corrected_text="She had gone home.",
        out_path=comments_out,
        title="T",
        review=review,
    )

    bullets = [p.text for p in Document(str(table_out)).paragraphs if p.style.name == "List Bullet"]
    assert not any("EXPL-changed" in bullet for bullet in bullets)
    comments_xml = _comments_xml(comments_out)
    assert "EXPL-changed" not in comments_xml
    assert '\u2192 "had"' in comments_xml


def test_table_layout_lists_only_explanations_of_changes_in_the_text(tmp_path: Path) -> None:
    """
    Given  a review with a note for an edit that is not in the corrected text any more
    When   the table layout is written
    Then   the explanations list only the change that is in the text
    """
    raw = "She have gone."
    corrected = "She has gone!"
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(4, 8, "have", "has"),),
                sentences=(),
                category="grammar",
                explanation="EXPL-real",
            ),
            EditNote(
                id=2,
                parts=(EditPart(9, 13, "gone", "went"),),
                sentences=(),
                category="grammar",
                explanation="EXPL-phantom",
            ),
        )
    )
    out = tmp_path / "only_real.docx"

    DocxDocumentCreator(fmt="table").create(
        raw_text=raw, corrected_text=corrected, out_path=out, title="T", review=review
    )

    doc = Document(str(out))
    bullets = " ".join(p.text for p in doc.paragraphs if p.style.name == "List Bullet")
    assert "EXPL-real" in bullets
    assert "EXPL-phantom" not in bullets
    quoted = next(p for p in doc.paragraphs if p.text.startswith("1. "))
    assert quoted.runs[1].text == "She has gone!"


def test_a_stretch_the_model_left_out_is_not_in_the_word_files_unclear_table(
    tmp_path: Path,
) -> None:
    """
    Given  a review whose only unclear note is a stretch the model left out and the app put back
    When   the table layout and the comments layout are written
    Then   neither lists it: no *Unclear* heading, no table row, no comment (it is a note for the
           teacher in Check, not for the student's copy)
    """
    left_out = UnclearNote(
        0,
        len(_RAW),
        _RAW,
        "The AI left this passage out of its answer.",
        "",
        left_out=True,
    )
    review = CorrectionReview(edits=_REVIEW.edits, unclear=(left_out,))

    for layout in ("table", "comments"):
        out = tmp_path / f"left_out_{layout}.docx"
        DocxDocumentCreator(fmt=layout).create(
            raw_text=_RAW, corrected_text=_CORRECTED, out_path=out, title="T", review=review
        )

        assert "Unclear or ambiguous expressions" not in _headings(out)
        assert "left this passage out" not in _all_table_text(out)
        if layout == "comments":
            assert "left this passage out" not in _comments_xml(out)


@pytest.mark.parametrize("fmt", ["table", "comments"])
def test_a_control_character_in_the_text_does_not_stop_the_document(
    tmp_path: Path, fmt: str
) -> None:
    """
    Given  a transcription that carries a control character (a .txt file, a model answer)
    When   create() is called in either layout
    Then   the document is written, and the character is not in its text
    """
    out = tmp_path / f"control-{fmt}.docx"
    creator = DocxDocumentCreator(fmt=fmt)
    creator.create(
        raw_text="Seite eins\x0cSeite zwei",
        corrected_text="Seite eins\x0cSeite zwei",
        out_path=out,
        title="Kontrolle",
    )

    from docx import Document  # noqa: PLC0415

    written = "\n".join(paragraph.text for paragraph in Document(str(out)).paragraphs) + "\n".join(
        cell.text for table in Document(str(out)).tables for row in table.rows for cell in row.cells
    )
    assert "\x0c" not in written
    assert "Seite eins" in written


def test_a_control_character_in_the_title_does_not_stop_the_document(tmp_path: Path) -> None:
    """
    Given  a title (the file's name) that carries a control character
    When   create() is called
    Then   the document is written and its heading holds the title without the character
    """
    out = tmp_path / "title.docx"
    DocxDocumentCreator(fmt="table").create(
        raw_text="Text", corrected_text="Text", out_path=out, title="Auf\x0bgabe"
    )

    from docx import Document  # noqa: PLC0415

    headings = [p.text for p in Document(str(out)).paragraphs if p.style.name.startswith("Heading")]
    assert headings[0] == "Aufgabe"


def test_a_control_character_in_a_comment_does_not_stop_the_document() -> None:
    """
    Given  a comment text from a model answer that carries a control character
    When   the comments part is built
    Then   no error is raised and the comment holds the text without the character
    """
    from docx import Document  # noqa: PLC0415

    doc = Document()
    DocxDocumentCreator._attach_comments_part(doc, [(0, "Er\x1bklärung")])

    part = next(rel.target_part for rel in doc.part.rels.values() if "comments" in rel.reltype)
    assert "Erklärung" in part.blob.decode("utf-8")
