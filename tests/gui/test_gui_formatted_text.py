"""GUI tests for formatted OCR text: HTML rendering, the Qt round trip and the formatting marks."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtTest import QTest

from teachers_teammate.application.formatting import TextFormatter
from teachers_teammate.domain.formatted_text import Block, FormattedDocument, Span, Style
from teachers_teammate.gui._formatted_text import (
    INSERT_COLOUR,
    STRIKE_COLOUR,
    STUDENT_STRIKE_COLOUR,
    FormattedTextEdit,
    _toggle,
    document_from_qt,
    render_html,
)

MARKDOWN = "Dear **diary**, my ~~backpack~~ <sup>passport</sup> and *me*"


def _round_trip(doc: FormattedDocument) -> FormattedDocument:
    qt_doc = QTextDocument()
    qt_doc.setHtml(render_html(doc))
    return document_from_qt(qt_doc)


# ── render_html ───────────────────────────────────────────────────────────


@pytest.mark.gui
def test_render_html_highlights_struck_and_inserted_text(qtbot) -> None:
    """
    Given  a paragraph with a crossed-out word and a word inserted above the line
    When   render_html is called
    Then   the struck word is red/struck-through and the insertion is raised and blue
    """
    doc = TextFormatter.document("my ~~backpack~~ <sup>passport</sup>", "markdown")

    html_text = render_html(doc)

    assert f"color:{STRIKE_COLOUR}" in html_text
    assert "<s>backpack</s>" in html_text
    assert f"color:{INSERT_COLOUR}" in html_text
    assert "<sup>passport</sup>" in html_text


@pytest.mark.gui
def test_render_html_escapes_text_and_keeps_line_breaks(qtbot) -> None:
    """
    Given  a paragraph containing "<b>" characters and a line break
    When   render_html is called
    Then   the characters are escaped and the break becomes <br>
    """
    doc = FormattedDocument((Block("paragraph", (Span("a <b> & c\nnext"),)),))

    html_text = render_html(doc)

    assert "a &lt;b&gt; &amp; c<br>next" in html_text


@pytest.mark.gui
def test_render_html_colours_crossed_out_words_as_asked(qtbot) -> None:
    """
    Given  a paragraph with a crossed-out word
    When   render_html is called with and without a strike colour
    Then   the default is the model's red and the given one is used instead
    """
    doc = TextFormatter.document("my ~~backpack~~ bag", "markdown")

    assert f"color:{STRIKE_COLOUR}" in render_html(doc)
    assert f"color:{STUDENT_STRIKE_COLOUR}" in render_html(doc, strike_colour=STUDENT_STRIKE_COLOUR)
    assert STRIKE_COLOUR not in render_html(doc, strike_colour=STUDENT_STRIKE_COLOUR)


@pytest.mark.gui
def test_render_html_nests_lists(qtbot) -> None:
    """
    Given  a bullet item with a nested ordered item
    When   rendered and loaded into a Qt document
    Then   the document has two list levels
    """
    doc = FormattedDocument(
        (
            Block("list_item", (Span("one"),)),
            Block("list_item", (Span("sub"),), level=1, ordered=True),
            Block("list_item", (Span("two"),)),
        )
    )

    result = _round_trip(doc)

    assert [(b.text, b.level, b.ordered) for b in result.blocks] == [
        ("one", 0, False),
        ("sub", 1, True),
        ("two", 0, False),
    ]


# ── Qt round trip (editing) ───────────────────────────────────────────────


@pytest.mark.gui
def test_qt_round_trip_preserves_styles(qtbot) -> None:
    """
    Given  a document with bold, italic, struck, inserted, underlined and subscript runs
    When   it is rendered to HTML, loaded by Qt and read back
    Then   the styles are recovered
    """
    doc = TextFormatter.document(
        "**b** *i* ~~s~~ <sup>p</sup> <u>u</u> <sub>q</sub> plain", "markdown"
    )

    result = _round_trip(doc)

    styles = {span.text.strip(): span.style for span in result.blocks[0].spans if span.text.strip()}
    assert styles["b"] == Style.BOLD
    assert styles["i"] == Style.ITALIC
    assert styles["s"] == Style.STRIKE
    assert styles["p"] == Style.SUPERSCRIPT
    assert styles["u"] == Style.UNDERLINE
    assert styles["q"] == Style.SUBSCRIPT
    assert styles["plain"] == Style.NONE


@pytest.mark.gui
def test_qt_round_trip_keeps_markdown_identical(qtbot) -> None:
    """
    Given  Markdown with a heading, paragraphs, a list, a table and a figure
    When   it goes parse -> HTML -> Qt document -> model -> Markdown
    Then   the resulting Markdown equals the original
    """
    markdown = (
        "## Title\n\nDear **diary**, my ~~backpack~~ <sup>passport</sup>.\nSecond line\n\n"
        "- one\n- two\n\n| A | B |\n| --- | --- |\n| 1 | **2** |\n\n![flower icon]()"
    )

    doc = _round_trip(TextFormatter.document(markdown, "markdown"))

    assert TextFormatter.markdown(doc) == markdown


@pytest.mark.gui
def test_qt_round_trip_preserves_blanks_and_underscores(qtbot) -> None:
    """
    Given  a paragraph with double spaces and a fill-in-the-blank run
    When   it round-trips through a Qt document
    Then   the text is unchanged
    """
    markdown = "In total: _____ / 62 BE  (two  spaces)\n\n**x**"

    doc = _round_trip(TextFormatter.document(markdown, "markdown"))

    assert TextFormatter.markdown(doc) == markdown


@pytest.mark.gui
def test_qt_round_trip_keeps_ordered_list_numbers(qtbot) -> None:
    """
    Given  Markdown with a list that starts at 3 and one resumed after a paragraph
    When   it is shown in Qt and read back
    Then   the numbers are unchanged, so editing does not renumber the answers
    """
    markdown = "3. Antwort **drei**\n4. Antwort vier\n\nZwischentext\n\n7. sieben"
    doc = TextFormatter.document(markdown, "markdown")

    back = _round_trip(doc)

    assert TextFormatter.markdown(back) == markdown


@pytest.mark.gui
def test_toggle_helpers_are_symmetric(qtbot) -> None:
    """
    Given  a formatted view
    When   bold is toggled twice at the cursor
    Then   the current format ends up non-bold again
    """
    view = FormattedTextEdit()
    qtbot.addWidget(view)

    _toggle(view, "bold")
    assert view.currentCharFormat().fontWeight() >= 700
    _toggle(view, "bold")
    assert view.currentCharFormat().fontWeight() < 700
    assert isinstance(view.currentCharFormat(), QTextCharFormat)


@pytest.mark.gui
@pytest.mark.parametrize("attribute", ["strike", "insert"])
def test_toggling_strike_or_insert_twice_restores_the_default_colour(qtbot, attribute) -> None:
    """
    Given  a formatted view
    When   "crossed out" (or "inserted") is toggled on and off again
    Then   the style is off and the highlight colour is gone, so the text no longer looks marked
    """
    view = FormattedTextEdit()
    qtbot.addWidget(view)

    _toggle(view, attribute)
    marked = view.currentCharFormat().foreground().color().name()
    _toggle(view, attribute)

    assert marked in (STRIKE_COLOUR, INSERT_COLOUR)
    assert not view.currentCharFormat().fontStrikeOut()
    assert view.currentCharFormat().foreground().style() == Qt.BrushStyle.NoBrush
    assert view.currentCharFormat().verticalAlignment() == (
        QTextCharFormat.VerticalAlignment.AlignNormal
    )


def _type_after(view: FormattedTextEdit, text: str) -> FormattedDocument:
    """Put the cursor at the end of the document, press Enter, type *text*, read it back."""
    view.moveCursor(QTextCursor.MoveOperation.End)
    QTest.keyClick(view, Qt.Key.Key_Return)
    QTest.keyClicks(view, text)
    return document_from_qt(view.document())


@pytest.mark.gui
def test_enter_after_a_heading_starts_a_plain_paragraph(qtbot) -> None:
    """
    Given  a formatted view whose last block is a heading
    When   the cursor is at its end, Enter is pressed and text is typed
    Then   the typed line is a paragraph, not one more (bold) heading
    """
    view = FormattedTextEdit()
    qtbot.addWidget(view)
    doc = FormattedDocument((Block("heading", (Span("Aufgabe 1"),), level=2),))
    view.setHtml(render_html(doc))

    result = _type_after(view, "Max hat gerechnet")

    assert [block.kind for block in result.blocks] == ["heading", "paragraph"]
    assert result.blocks[1].text == "Max hat gerechnet"
    assert TextFormatter.markdown(result) == "## Aufgabe 1\n\nMax hat gerechnet"


@pytest.mark.gui
def test_enter_after_a_figure_placeholder_starts_an_unstyled_paragraph(qtbot) -> None:
    """
    Given  a formatted view whose last block is the muted italic [image] placeholder
    When   the cursor is at its end, Enter is pressed and text is typed
    Then   the typed line is plain text, not italic, and the figure stays a figure
    """
    view = FormattedTextEdit()
    qtbot.addWidget(view)
    doc = FormattedDocument((Block("figure", (Span("Skizze"),)),))
    view.setHtml(render_html(doc))

    result = _type_after(view, "Antwort")

    assert [block.kind for block in result.blocks] == ["figure", "paragraph"]
    assert result.blocks[1].spans == (Span("Antwort"),)


@pytest.mark.gui
def test_chip_placeholders_keeps_the_placeholder_text(qtbot) -> None:
    """
    Given  rendered HTML that contains placeholders and markup
    When   chip_placeholders() wraps them
    Then   each placeholder stays readable inside its chip and markup is untouched
    """
    from teachers_teammate.gui._formatted_text import chip_placeholders  # noqa: PLC0415

    marked = chip_placeholders("<p>[PERSON_1] met [PERSON_2]</p>", ["[PERSON_1]", "[PERSON_2]"])

    assert marked.count("<span") == 2
    assert ">[PERSON_1]</span>" in marked
    assert ">[PERSON_2]</span>" in marked
    assert marked.startswith("<p>") and marked.endswith("</p>")


@pytest.mark.gui
def test_chip_placeholders_sets_a_text_colour_for_the_light_background(qtbot) -> None:
    """
    Given  a placeholder chip with a light background
    When   chip_placeholders() wraps it
    Then   the chip also sets a dark text colour, so a dark theme's light text cannot wash out
    """
    from teachers_teammate.gui._formatted_text import (  # noqa: PLC0415
        PLACEHOLDER_BACKGROUND,
        PLACEHOLDER_TEXT,
        chip_placeholders,
    )

    marked = chip_placeholders("<p>[PERSON_1]</p>", ["[PERSON_1]"])

    assert f"background-color:{PLACEHOLDER_BACKGROUND}" in marked
    assert f"color:{PLACEHOLDER_TEXT}" in marked
