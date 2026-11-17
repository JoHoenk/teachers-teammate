"""GUI tests for the plain-text map of a Qt document.

The map is the one bridge between a review's offsets (plain text) and what a teacher reads and
clicks (a :class:`QTextDocument`).  Its invariant is tested on every construct the Markdown
notation can produce: the document must reproduce the plain text exactly, or the view shows no
marks at all.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QTextCursor, QTextDocument

from teachers_teammate.application.formatting import TextFormatter
from teachers_teammate.domain.formatted_text import Block, FormattedDocument, Span, Style
from teachers_teammate.gui._formatted_text import render_html
from teachers_teammate.gui.check._plain_map import PlainMap

#: One stored Markdown text per construct the walker has to reproduce.
MARKDOWN_CASES: dict[str, str] = {
    "paragraphs": "One.\n\nTwo.\n",
    "heading": "# Title\n\nBody.\n",
    "bullets": "- a\n- b\n",
    "nested_bullets": "- a\n  - b\n    - c\n",
    "ordered": "1. one\n2. two\n",
    "ordered_start": "3. three\n4. four\n",
    "table": "| x | y |\n| --- | --- |\n| 1 | 2 |\n",
    "figure": "![alt text]()\n",
    "line_break": "Line one\nline two\n",
    "styles": "Some **bold**, *italic*, <u>under</u>, <sup>up</sup>, <sub>down</sub>, ~~gone~~.\n",
    "blank_block": "One.\n\n   \n\nTwo.\n",
    "leading_whitespace": "  Indented start.\n",
    "page_labels": "Page 1:\n\nFirst.\n\nPage 2:\n\nSecond.\n",
    "everything": (
        "# Hi\n\nSome *bold* text.\n\n- a\n- b\n  - nested\n\n3. three\n4. four\n\n"
        "| x | y |\n| --- | --- |\n| 1 | 2 |\n\n![alt text]()\n\nLine one\nline two\n\n"
        "Page 2:\n\nLast para.\n"
    ),
    "empty": "",
    "only_whitespace": "   \n",
}


def _map_of(markdown: str) -> tuple[PlainMap, str]:
    """Return the map of *markdown* rendered into a Qt document, and its plain text."""
    document = QTextDocument()
    document.setHtml(render_html(TextFormatter.document(markdown, "markdown")))
    return PlainMap(document), TextFormatter.plain_text(markdown, "markdown")


# ── The invariant ─────────────────────────────────────────────────────────


@pytest.mark.gui
@pytest.mark.parametrize("markdown", MARKDOWN_CASES.values(), ids=MARKDOWN_CASES.keys())
def test_map_reproduces_the_plain_text_of_every_construct(qtbot, markdown: str) -> None:
    """
    Given  a stored Markdown text of one construct (or of all of them)
    When   it is rendered into a Qt document and mapped
    Then   the map's text is exactly the plain text the review's offsets are in
    """
    plain_map, plain = _map_of(markdown)

    assert plain_map.matches(plain)
    assert plain_map.text == plain


@pytest.mark.gui
def test_map_reproduces_the_plain_text_of_a_document_built_from_the_model(qtbot) -> None:
    """
    Given  a document with every style, a heading, lists, a table and a figure
    When   it is rendered to Markdown, parsed back and mapped
    Then   the map reproduces the plain text of the stored Markdown
    """
    doc = FormattedDocument(
        (
            Block("heading", (Span("Title"),), level=1),
            Block(
                "paragraph",
                (
                    Span("plain "),
                    Span("bold", Style.BOLD),
                    Span(" "),
                    Span("struck", Style.STRIKE),
                    Span(" "),
                    Span("up", Style.SUPERSCRIPT),
                ),
            ),
            Block("list_item", (Span("one"),)),
            Block("list_item", (Span("sub"),), level=1, ordered=True),
            Block("figure", (Span("flower icon"),)),
            Block(
                "table",
                rows=(((Span("A"),), (Span("B", Style.BOLD),)), ((Span("1"),), (Span("2"),))),
            ),
        )
    )
    markdown = TextFormatter.markdown(doc)

    plain_map, plain = _map_of(markdown)

    assert plain_map.matches(plain)


# ── Positions ─────────────────────────────────────────────────────────────


@pytest.mark.gui
@pytest.mark.parametrize("markdown", MARKDOWN_CASES.values(), ids=MARKDOWN_CASES.keys())
def test_every_position_of_the_document_maps_to_a_stable_offset(qtbot, markdown: str) -> None:
    """
    Given  a mapped document
    When   every Qt position is turned into a plain offset and back
    Then   the offset is stable and both directions never move backwards
    """
    plain_map, plain = _map_of(markdown)
    document = QTextDocument()
    document.setHtml(render_html(TextFormatter.document(markdown, "markdown")))

    previous_offset = 0
    previous_position = 0
    for position in range(len(document.toPlainText()) + 1):
        offset = plain_map.offset(position)
        assert offset >= previous_offset
        assert plain_map.offset(plain_map.position(offset)) == offset
        assert plain_map.position(offset) >= previous_position
        previous_offset, previous_position = offset, plain_map.position(offset)
    assert previous_offset <= len(plain)


@pytest.mark.gui
@pytest.mark.parametrize("markdown", MARKDOWN_CASES.values(), ids=MARKDOWN_CASES.keys())
def test_the_start_of_every_run_round_trips_exactly(qtbot, markdown: str) -> None:
    """
    Given  a mapped document
    When   the plain offset of a run's first character is turned into a position and back
    Then   it comes back unchanged, so a mark starts on the right character
    """
    plain_map, plain = _map_of(markdown)

    for offset in range(len(plain)):
        position = plain_map.position(offset)
        if plain_map.offset(position) == offset:
            assert plain_map.position(offset) == position


@pytest.mark.gui
def test_an_offset_inside_a_list_marker_snaps_forward(qtbot) -> None:
    """
    Given  a bullet list, whose markers are in the plain text but not in the document
    When   an offset inside the marker is turned into a position
    Then   it lands on the first character of the item's words
    """
    plain_map, plain = _map_of("- a\n- b\n")
    assert plain == "- a\n- b"

    assert plain_map.position(0) == plain_map.position(2)  # "- " and "a" start together
    assert plain_map.offset(plain_map.position(0)) == 2


@pytest.mark.gui
def test_an_offset_inside_a_table_separator_snaps_forward(qtbot) -> None:
    """
    Given  a table, whose `` | `` and row breaks are in the plain text but not in the document
    When   an offset inside a separator is turned into a position
    Then   it lands on the first character of the next cell
    """
    plain_map, plain = _map_of("| x | y |\n| --- | --- |\n| 1 | 2 |\n")
    assert plain == "x | y\n1 | 2"

    separator = plain.index("|")
    assert plain_map.position(separator) == plain_map.position(separator + 2)


@pytest.mark.gui
def test_an_offset_between_two_blocks_snaps_forward(qtbot) -> None:
    """
    Given  two paragraphs, separated by a blank line the document does not have
    When   an offset inside that blank line is turned into a position
    Then   it lands on the first character of the second paragraph
    """
    plain_map, plain = _map_of("One.\n\nTwo.\n")
    assert plain == "One.\n\nTwo."

    assert plain_map.position(4) == plain_map.position(6)  # the blank line and "T" agree
    assert plain_map.offset(plain_map.position(4)) == 6


@pytest.mark.gui
def test_a_position_in_a_block_separator_snaps_forward(qtbot) -> None:
    """
    Given  two paragraphs, whose separator is one position in the document and two in the text
    When   the position of that separator is turned into an offset
    Then   it lands on the first character of the second paragraph
    """
    plain_map, plain = _map_of("One.\n\nTwo.\n")
    assert plain == "One.\n\nTwo."

    assert plain_map.offset(4) == 6


@pytest.mark.gui
def test_an_insertion_behind_a_paragraph_stays_in_that_paragraph(qtbot) -> None:
    """
    Given  two paragraphs, and an offset right behind the first one
    When   the place to insert at that offset is asked for
    Then   it is the end of the first paragraph, not the start of the second
    """
    plain_map, plain = _map_of("One.\n\nTwo.\n")
    assert plain == "One.\n\nTwo."

    assert plain_map.insertion(4) == plain_map.position(3) + 1  # just behind the "."
    assert plain_map.insertion(4) < plain_map.position(6)
    assert plain_map.insertion(6) == plain_map.position(6)  # in front of "Two."
    assert plain_map.insertion(len(plain)) == plain_map.position(len(plain))


@pytest.mark.gui
def test_an_empty_span_at_a_paragraph_edge_is_one_place(qtbot) -> None:
    """
    Given  two paragraphs, and an empty span right behind the first one
    When   the span is turned into positions
    Then   both ends are the same place, so no paragraph break lies between them
    """
    plain_map, _plain = _map_of("One.\n\nTwo.\n")

    first, last = plain_map.span(4, 4)

    assert first == last


# ── Cursors ───────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_a_cursor_covers_the_words_at_a_plain_span(qtbot) -> None:
    """
    Given  a mapped document
    When   a cursor is made over a plain span
    Then   it selects exactly those words in the document
    """
    plain_map, plain = _map_of("Dear diary, my backpack.\n")
    start = plain.index("backpack")
    document = QTextDocument()
    document.setHtml(render_html(TextFormatter.document("Dear diary, my backpack.\n", "markdown")))

    cursor = plain_map.cursor(document, start, start + len("backpack"))

    assert cursor.selectedText() == "backpack"
    assert cursor.hasSelection()


@pytest.mark.gui
def test_a_cursor_over_a_span_in_a_table_cell_selects_the_cell_words(qtbot) -> None:
    """
    Given  a table
    When   a cursor is made over a span inside one cell
    Then   it selects those words and nothing of the separators
    """
    markdown = "| x | y |\n| --- | --- |\n| 1 | 2 |\n"
    plain_map, plain = _map_of(markdown)
    start = plain.index("2")
    document = QTextDocument()
    document.setHtml(render_html(TextFormatter.document(markdown, "markdown")))

    cursor = plain_map.cursor(document, start, start + 1)

    assert cursor.selectedText() == "2"


# ── Degrading ─────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_a_document_the_map_cannot_reproduce_does_not_match(qtbot) -> None:
    """
    Given  a document whose text is not the plain text of the stored one
    When   it is mapped
    Then   the map says so instead of pretending the offsets are right
    """
    document = QTextDocument()
    document.setPlainText("Something else entirely.")

    plain_map = PlainMap(document)

    assert not plain_map.matches("Dear diary")
    assert plain_map.text == "Something else entirely."


@pytest.mark.gui
def test_an_empty_document_maps_to_an_empty_text(qtbot) -> None:
    """
    Given  a document with no text
    When   it is mapped
    Then   the map is empty and every position is the start of the document
    """
    plain_map = PlainMap(QTextDocument())

    assert plain_map.text == ""
    assert plain_map.matches("")
    assert plain_map.position(0) == 0
    assert plain_map.offset(0) == 0


@pytest.mark.gui
def test_a_cursor_at_the_end_of_the_text_keeps_the_last_position(qtbot) -> None:
    """
    Given  a mapped document
    When   an offset past the end of the plain text is turned into a position
    Then   it lands at the end of the last run, not outside the document
    """
    plain_map, plain = _map_of("One.\n")
    document = QTextDocument()
    document.setHtml(render_html(TextFormatter.document("One.\n", "markdown")))

    position = plain_map.position(len(plain) + 5)

    assert position == len("One.")
    assert QTextCursor(document).position() == 0


# ── The text the map is expected to stand for ─────────────────────────────

#: Stored plain texts whose layout the document does not show.
PLAIN_CASES: dict[str, str] = {
    "trailing_line_break": "Der Hund bellt.\n",
    "indented_block": "  Der Hund bellt.\n",
    "extra_blank_line": "Der Hund.\n\n\nDie Katze.\n",
    "trailing_spaces": "Der Hund.  \n\nDie Katze.\n",
    "leading_line_breaks": "\n\nDer Hund bellt.\n",
    "tab": "Der\tHund.\n",
}


def _located_map_of(text: str, text_format: str) -> tuple[PlainMap, str]:
    """Return the map of *text*, built against the plain text it is expected to stand for."""
    document = QTextDocument()
    document.setHtml(render_html(TextFormatter.document(text, text_format)))
    plain = TextFormatter.plain_text(text, text_format)
    return PlainMap(document, plain), plain


@pytest.mark.gui
@pytest.mark.parametrize("text", PLAIN_CASES.values(), ids=PLAIN_CASES.keys())
def test_a_plain_text_is_mapped_although_the_document_drops_its_layout(qtbot, text: str) -> None:
    """
    Given  a stored plain text with a line break, an indent or a blank line the document drops
    When   the map is built against that text
    Then   it matches, so the marks of a plain document are not lost to its layout
    """
    plain_map, plain = _located_map_of(text, "plain")

    assert plain_map.matches(plain)


@pytest.mark.gui
@pytest.mark.parametrize("markdown", MARKDOWN_CASES.values(), ids=MARKDOWN_CASES.keys())
def test_the_located_map_places_the_same_runs_as_the_exact_one(qtbot, markdown: str) -> None:
    """
    Given  a stored Markdown text
    When   its map is built with and without the plain text it stands for
    Then   both place every run at the same position
    """
    exact, plain = _map_of(markdown)
    located, _ = _located_map_of(markdown, "markdown")
    document = QTextDocument()
    document.setHtml(render_html(TextFormatter.document(markdown, "markdown")))

    assert located.matches(plain)
    for offset in range(len(plain) + 1):
        assert located.position(offset) == exact.position(offset)
    for position in range(len(document.toPlainText()) + 1):
        assert located.offset(position) == exact.offset(position)


@pytest.mark.gui
def test_a_document_that_is_not_the_text_is_not_matched(qtbot) -> None:
    """
    Given  a document built from one text and the plain text of another
    When   the map is built against that other text
    Then   it does not match, so the view shows no marks instead of wrong ones
    """
    document = QTextDocument()
    document.setHtml(render_html(TextFormatter.document("Der Hund bellt.", "plain")))

    plain_map = PlainMap(document, "Die Katze schläft.")

    assert not plain_map.matches("Die Katze schläft.")


@pytest.mark.gui
def test_the_end_of_a_plain_text_maps_to_the_end_of_the_document(qtbot) -> None:
    """
    Given  a stored plain text that ends with a line break the document does not show
    When   the offset after the last word is turned into a position
    Then   it lands at the end of the document, so a mark there is not lost
    """
    plain_map, plain = _located_map_of("Der Hund bellt.\n", "plain")
    document = QTextDocument()
    document.setHtml(render_html(TextFormatter.document("Der Hund bellt.\n", "plain")))

    assert plain_map.position(len(plain)) == len(document.toPlainText())
