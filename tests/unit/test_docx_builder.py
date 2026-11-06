"""Unit tests for teachers_teammate.docx_builder."""
# pylint: disable=W0404,W0621,W0613  # reimported — monkeypatch/patch blocks locally reimport the patched symbol / redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
import pytest

from teachers_teammate.domain.correction_review import (
    ChangeNote,
    CorrectionReview,
    SentenceNote,
    UnclearNote,
)
from teachers_teammate.domain.formatted_text import Span
from teachers_teammate.infrastructure.docx_builder import DocxDocumentCreator, _NoteSource

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


def _plain_lines(text: str) -> list[tuple[Span, ...]]:
    return [(Span(line),) if line else () for line in text.split("\n")]


def _no_notes() -> _NoteSource:
    """Return a note source that explains nothing (no review available)."""
    return _NoteSource(None, {})


def test_fill_cell_with_diffs_equal_lines_no_comments(tmp_path: Path) -> None:
    """
    Given  identical raw and corrected multi-line text
    When   _fill_cell_with_styled_diffs() is called
    Then   the pending comments list remains empty
    """
    creator = DocxDocumentCreator(fmt="comments")
    cell = Document().add_table(rows=1, cols=1).rows[0].cells[0]
    lines = _plain_lines("line one\nline two\nline three")
    pending: list = []

    creator._fill_cell_with_styled_diffs(cell, lines, lines, pending, _no_notes())

    assert pending == []


def test_fill_cell_with_diffs_replace_adds_comments(tmp_path: Path) -> None:
    """
    Given  raw and corrected text with a replaced line
    When   _fill_cell_with_styled_diffs() is called
    Then   at least one comment is appended to the pending list
    """
    creator = DocxDocumentCreator(fmt="comments")
    cell = Document().add_table(rows=1, cols=1).rows[0].cells[0]
    pending: list = []

    creator._fill_cell_with_styled_diffs(
        cell,
        _plain_lines("old line\nend"),
        _plain_lines("new line\nend"),
        pending,
        _no_notes(),
    )

    assert len(pending) > 0


def test_styled_word_diff_handles_insert_opcode(tmp_path: Path) -> None:
    """
    Given  a raw line with fewer words than the corrected line (insert opcode)
    When   _add_styled_line_with_word_diff() is called
    Then   a comment is added for the inserted words
    """
    creator = DocxDocumentCreator(fmt="comments")
    para = Document().add_paragraph()
    comments: list = []

    creator._add_styled_line_with_word_diff(
        para, (Span("hello"),), (Span("hello world here"),), comments, _no_notes()
    )

    assert len(comments) > 0


def test_styled_word_diff_handles_delete_opcode(tmp_path: Path) -> None:
    """
    Given  a raw line with more words than the corrected line (delete opcode)
    When   _add_styled_line_with_word_diff() is called
    Then   a comment with '→ (delete)' text is added
    """
    creator = DocxDocumentCreator(fmt="comments")
    para = Document().add_paragraph()
    comments: list = []

    creator._add_styled_line_with_word_diff(
        para, (Span("hello extra words"),), (Span("hello"),), comments, _no_notes()
    )

    assert any("delete" in c[1].lower() for c in comments)


def test_styled_word_diff_both_empty_is_noop(tmp_path: Path) -> None:
    """
    Given  both the raw line and the corrected line are empty
    When   _add_styled_line_with_word_diff() is called
    Then   no exception is raised and no comments are added
    """
    creator = DocxDocumentCreator(fmt="comments")
    para = Document().add_paragraph()
    comments: list = []

    creator._add_styled_line_with_word_diff(para, (), (), comments, _no_notes())

    assert comments == []


def test_comments_layout_keeps_spaces_between_words_of_different_styles(tmp_path: Path) -> None:
    """
    Given  Markdown raw text whose neighbouring words have different styles and a correction
    When   the comments layout is created
    Then   the OCR cell reads "Hello world again Haus Hause" with a space between every word
    """
    out = tmp_path / "styled.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text="Hello **world** again ~~Haus~~ Hause",
        corrected_text="Hello world again Haus Hause!",
        out_path=out,
        title="t",
        text_format="markdown",
    )

    cell_text = Document(str(out)).tables[0].rows[1].cells[1].text
    assert cell_text == "Hello world again Haus Hause"


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


def test_comments_format_diffs_markdown_text_and_keeps_styles(tmp_path: Path) -> None:
    """
    Given  Markdown OCR text with a struck word and a correction that fixes a typo
    When   create(text_format="markdown") writes the comments layout
    Then   the typo is commented, the struck word keeps its formatting, no markers remain
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
    assert _runs(doc, "hate ")[0].font.strike or _runs(doc, "hate")[0].font.strike
    body = doc.element.body.xml
    assert "commentReference" in body
    all_text = " ".join(
        p.text for t in doc.tables for r in t.rows for c in r.cells for p in c.paragraphs
    )
    assert "lov" in all_text
    assert "~~" not in all_text


def test_comments_format_comments_a_correction_that_drops_a_strikethrough(tmp_path: Path) -> None:
    """
    Given  Markdown OCR text with a struck word and a correction with the same words
           but without the strikethrough
    When   create(text_format="markdown") writes the comments layout
    Then   the changed formatting is commented although the words are identical
    """
    out = tmp_path / "md_style_change.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text="I ~~hate~~ love this",
        corrected_text="I hate love this",
        out_path=out,
        title="T",
        text_format="markdown",
    )

    comments_xml = _comments_xml(out)
    assert "formatting changed" in comments_xml


def test_comments_format_does_not_comment_unchanged_formatting(tmp_path: Path) -> None:
    """
    Given  Markdown OCR text and a correction that keeps every word and its formatting
    When   create(text_format="markdown") writes the comments layout
    Then   no comment is attached
    """
    out = tmp_path / "md_same.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text="I ~~hate~~ **love** this",
        corrected_text="I ~~hate~~ **love** this",
        out_path=out,
        title="T",
        text_format="markdown",
    )

    assert "commentReference" not in Document(str(out)).element.body.xml


def _comments_xml(path: Path) -> str:
    import zipfile  # noqa: PLC0415

    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if "comments" in n.lower())
        return archive.read(name).decode("utf-8")


# ── Proofreading review: explanations and the unclear table ───────────────

_RAW = "Yesterday she have gone to the cinema. It was very nice."
_CORRECTED = "Yesterday she has gone to the cinema. It was very nice."

_REVIEW = CorrectionReview(
    sentences=(
        SentenceNote(
            original="Yesterday she have gone to the cinema.",
            corrected="Yesterday she has gone to the cinema.",
            changes=(ChangeNote("have", "has", "grammar", '"she" is 3rd person singular.'),),
        ),
    ),
    unclear=(
        UnclearNote(
            expression="It was very nice",
            why='"It" can mean the film or the visit.',
            recommendation="The film was very nice",
            is_suggestion=True,
        ),
        UnclearNote(
            expression="to the cinema",
            why="Which cinema is meant is not stated.",
            recommendation="to the local cinema",
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
    Given  a review whose sentence has a grammar change and a spelling change
    When   the table layout is written
    Then   the *Explanations* section lists the grammar change only
    """
    from teachers_teammate.domain.correction_review import (  # noqa: PLC0415
        ChangeNote,
        CorrectionReview,
        SentenceNote,
    )

    raw, corrected = "She have a dogs.", "She has a dog."
    review = CorrectionReview(
        sentences=(
            SentenceNote(
                raw,
                corrected,
                (
                    ChangeNote("have", "has", "grammar", "Third person."),
                    ChangeNote("dogs", "dog", "spelling", "Singular."),
                ),
            ),
        )
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
    Given  notes for a two-word edit, an inserted comma and an added full stop, edits which
           the comments layout's whitespace word diff cuts differently than the token diff
    When   the comments layout is written
    Then   every one of those comments carries its explanation
    """
    raw = "Yesterday she have gone with his friend. We met Tom and left"
    corrected = "Yesterday she has gone with her friends. We met Tom, and left."
    review = CorrectionReview(
        sentences=(
            SentenceNote(
                "Yesterday she have gone with his friend.",
                "Yesterday she has gone with her friends.",
                (
                    ChangeNote("have", "has", "grammar", "EXPL-has"),
                    ChangeNote("his friend", "her friends", "word_choice", "EXPL-friends"),
                ),
            ),
            SentenceNote(
                "We met Tom and left",
                "We met Tom, and left.",
                (
                    ChangeNote("Tom", "Tom,", "punctuation", "EXPL-comma"),
                    ChangeNote("left", "left.", "punctuation", "EXPL-period"),
                ),
            ),
        )
    )
    out = tmp_path / "multi.docx"

    DocxDocumentCreator(fmt="comments").create(
        raw_text=raw, corrected_text=corrected, out_path=out, title="T", review=review
    )

    comments = _comments_xml(out)
    for expected in ("EXPL-has", "EXPL-friends", "EXPL-comma", "EXPL-period"):
        assert expected in comments


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


def test_unclear_comment_never_starts_inside_a_longer_word(tmp_path: Path) -> None:
    """
    Given  the unclear expression "he", which also occurs inside "Then"
    When   the comments layout is written
    Then   the comment's range wraps the pronoun "he", not the letters inside "Then"
    """
    text = "Then the boy said he left."
    review = CorrectionReview(
        unclear=(UnclearNote(expression="he", why="Which person?", recommendation="the boy"),)
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


def test_table_layout_lists_only_explanations_of_changes_in_the_text(tmp_path: Path) -> None:
    """
    Given  a review with a sentence that cannot be placed, a note for an edit that is not in
           the corrected text, and a corrected text the teacher has since edited
    When   the table layout is written
    Then   the explanations list only the change that is in the text, quoting the sentence as
           it now reads
    """
    review = CorrectionReview(
        sentences=(
            SentenceNote(
                "She have gone.",
                "She has gone.",
                (
                    ChangeNote("have", "has", "grammar", "EXPL-real"),
                    ChangeNote("gone", "went", "grammar", "EXPL-phantom"),
                ),
            ),
            SentenceNote(
                "Missing one.", "Missing two.", (ChangeNote("one", "two", "other", "EXPL-lost"),)
            ),
        ),
        unplaced=(1,),
    )
    out = tmp_path / "only_real.docx"

    DocxDocumentCreator(fmt="table").create(
        raw_text="She have gone.",
        corrected_text="She has gone!",
        out_path=out,
        title="T",
        review=review,
    )

    doc = Document(str(out))
    bullets = " ".join(p.text for p in doc.paragraphs if p.style.name == "List Bullet")
    assert "EXPL-real" in bullets
    assert "EXPL-phantom" not in bullets
    assert "EXPL-lost" not in bullets
    quoted = next(p for p in doc.paragraphs if p.text.startswith("1. "))
    assert quoted.runs[1].text == "She has gone!"
