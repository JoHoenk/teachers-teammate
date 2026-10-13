"""GUI tests for formatted OCR text: HTML rendering, Qt round trip and the preview panel."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtTest import QTest

from teachers_teammate.application.formatting import TextFormatter
from teachers_teammate.domain.formatted_text import Block, FormattedDocument, Span, Style
from teachers_teammate.gui._formatted_text import (
    INSERT_COLOUR,
    STRIKE_COLOUR,
    FormattedTextEdit,
    _toggle,
    document_from_qt,
    render_html,
)
from teachers_teammate.gui._preview_panel import PreviewPanel

MARKDOWN = "Dear **diary**, my ~~backpack~~ <sup>passport</sup> and *me*"


def _round_trip(doc: FormattedDocument) -> FormattedDocument:
    qt_doc = QTextDocument()
    qt_doc.setHtml(render_html(doc))
    return document_from_qt(qt_doc)


def _pixmap() -> QPixmap:
    pix = QPixmap(20, 20)
    pix.fill()
    return pix


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


# ── PreviewPanel ──────────────────────────────────────────────────────────


@pytest.mark.gui
def test_panel_shows_markdown_formatted_and_plain_as_source(qtbot) -> None:
    """
    Given  a panel loaded with Markdown OCR text
    When   load() is called with text_format="markdown"
    Then   the formatted view is shown (without markers) and the Source toggle appears;
           plain text keeps the editable source shown and no toggle
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show()

    panel.load(_pixmap(), MARKDOWN, "", "", text_format="markdown")

    assert panel._ocr_stack.currentWidget() is panel._ocr_view
    assert "**" not in panel._ocr_view.toPlainText()
    assert "backpack" in panel._ocr_view.toPlainText()
    assert panel._ocr_text.toPlainText() == MARKDOWN
    assert panel._source_btn.isVisible()

    panel.load(_pixmap(), "**not bold**", "", "")

    assert panel._ocr_stack.currentWidget() is panel._ocr_text
    assert not panel._source_btn.isVisible()


@pytest.mark.gui
def test_panel_source_toggle_switches_to_markdown_source(qtbot) -> None:
    """
    Given  a panel showing Markdown text formatted
    When   the Source toggle is checked and unchecked
    Then   the stored Markdown editor and the formatted view alternate
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.load(_pixmap(), MARKDOWN, "", "", text_format="markdown")

    panel._source_btn.setChecked(True)
    assert panel._ocr_stack.currentWidget() is panel._ocr_text

    panel._source_btn.setChecked(False)
    assert panel._ocr_stack.currentWidget() is panel._ocr_view


@pytest.mark.gui
def test_panel_diff_ignores_markup(qtbot) -> None:
    """
    Given  Markdown OCR text and a correction that only fixes a word
    When   the panel is loaded
    Then   the diff tab compares plain words, so formatting marks never show as changes
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)

    panel.load(
        _pixmap(),
        "I ~~hate~~ lov this",
        "I ~~hate~~ love this",
        "",
        text_format="markdown",
    )

    diff_text = panel._diff_widget.toPlainText()
    assert "~~" not in diff_text
    assert "hate" in diff_text


@pytest.mark.gui
def test_panel_editing_formatted_view_emits_markdown(qtbot) -> None:
    """
    Given  a panel showing Markdown OCR text formatted
    When   the user types into the formatted view
    Then   ocr_text_edited is emitted with the Markdown including the typed text
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.load(_pixmap(), "Hello **world**", "", "", text_format="markdown")

    with qtbot.waitSignal(panel.ocr_text_edited) as blocker:
        cursor = panel._ocr_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertText("Oh, ")

    assert blocker.args == ["Oh, Hello **world**"]
    assert panel._ocr_text.toPlainText() == "Oh, Hello **world**"


@pytest.mark.gui
def test_panel_formatting_shortcut_marks_selection_crossed_out(qtbot) -> None:
    """
    Given  a formatted view with a selected word
    When   the "crossed out" toggle is applied
    Then   the stored Markdown wraps the word in ~~ ~~
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.load(_pixmap(), "my backpack here **x**", "", "", text_format="markdown")
    view = panel._ocr_view
    cursor = view.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(11, QTextCursor.MoveMode.KeepAnchor)
    view.setTextCursor(cursor)

    _toggle(view, "strike")
    panel.flush_pending_edit()

    assert "~~backpack~~" in panel._ocr_text.toPlainText()


@pytest.mark.gui
def test_panel_source_edit_refreshes_formatted_view(qtbot) -> None:
    """
    Given  a panel showing Markdown text
    When   the stored source is edited
    Then   the formatted view re-renders and the edit signal fires
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.load(_pixmap(), "plain **x**", "", "", text_format="markdown")

    with qtbot.waitSignal(panel.ocr_text_edited):
        panel._ocr_text.setPlainText("plain ~~x~~")

    iterator = panel._ocr_view.document().begin().begin()
    formats = []
    while not iterator.atEnd():
        formats.append(iterator.fragment().charFormat().fontStrikeOut())
        iterator += 1
    assert True in formats


@pytest.mark.gui
def test_toggle_helpers_are_symmetric(qtbot) -> None:
    """
    Given  a formatted view
    When   bold is toggled twice at the cursor
    Then   the current format ends up non-bold again
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    view = panel._ocr_view

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
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    view = panel._ocr_view

    _toggle(view, attribute)
    marked = view.currentCharFormat().foreground().color().name()
    _toggle(view, attribute)

    assert marked in (STRIKE_COLOUR, INSERT_COLOUR)
    assert not view.currentCharFormat().fontStrikeOut()
    assert view.currentCharFormat().foreground().style() == Qt.BrushStyle.NoBrush
    assert view.currentCharFormat().verticalAlignment() == (
        QTextCharFormat.VerticalAlignment.AlignNormal
    )


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
def test_typing_in_one_view_does_not_rerender_the_other_or_hidden_views(qtbot, monkeypatch) -> None:
    """
    Given  a panel with Markdown OCR and correction text, the correction view in Source mode
    When   the user types into the formatted OCR view
    Then   no formatted view is re-rendered (setHtml is not called again)
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.load(
        _pixmap(),
        "Hello **world**",
        "Hello **world**!",
        "",
        text_format="markdown",
    )
    calls: list[str] = []
    for name in ("_ocr_view", "_correction_view"):
        view = getattr(panel, name)
        original = view.setHtml
        monkeypatch.setattr(
            view, "setHtml", lambda html, _o=original, _n=name: (calls.append(_n), _o(html))
        )

    with qtbot.waitSignal(panel.ocr_text_edited):
        cursor = panel._ocr_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertText("Oh, ")

    assert calls == []
    assert panel._ocr_text.toPlainText() == "Oh, Hello **world**"


@pytest.mark.gui
def test_hidden_formatted_view_is_rendered_when_the_source_toggle_is_switched_off(qtbot) -> None:
    """
    Given  a panel in Source mode whose Markdown text changed meanwhile
    When   the Source toggle is switched off
    Then   the formatted view shows the current text
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.load(_pixmap(), "first **x**", "", "", text_format="markdown")
    panel._source_btn.setChecked(True)
    panel._ocr_text.setPlainText("second ~~y~~")

    panel._source_btn.setChecked(False)

    assert panel._ocr_view.toPlainText() == "second y"


@pytest.mark.gui
def test_typing_in_a_formatted_view_is_committed_once_after_a_pause(qtbot) -> None:
    """
    Given  a panel showing Markdown text formatted
    When   the user types several characters in a row
    Then   the source text and the edit signal are not touched per keystroke, and the edit is
           committed once typing pauses
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.load(_pixmap(), "Hello **world**", "", "", text_format="markdown")
    emitted: list[str] = []
    panel.ocr_text_edited.connect(emitted.append)

    cursor = panel._ocr_view.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    for char in "Oh, ":
        cursor.insertText(char)

    assert emitted == []
    assert panel._ocr_text.toPlainText() == "Hello **world**"
    qtbot.waitUntil(lambda: emitted == ["Oh, Hello **world**"])
    assert panel._ocr_text.toPlainText() == "Oh, Hello **world**"


@pytest.mark.gui
def test_flush_pending_edit_commits_typed_text_immediately(qtbot) -> None:
    """
    Given  typed text in a formatted view that the typing pause has not committed yet
    When   flush_pending_edit() is called (as on focus loss or before a reload)
    Then   the source holds the text and the edit signal fired exactly once
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.load(_pixmap(), "Hello **world**", "", "", text_format="markdown")
    emitted: list[str] = []
    panel.ocr_text_edited.connect(emitted.append)
    cursor = panel._ocr_view.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    cursor.insertText("Oh, ")

    panel.flush_pending_edit()
    panel.flush_pending_edit()  # nothing pending any more

    assert emitted == ["Oh, Hello **world**"]


@pytest.mark.gui
def test_switching_to_source_keeps_text_typed_in_the_formatted_view(qtbot) -> None:
    """
    Given  uncommitted typing in the formatted view
    When   the Source toggle is switched on
    Then   the source view shows the typed text, which is not lost to a stale re-render
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.load(_pixmap(), "Hello **world**", "", "", text_format="markdown")
    cursor = panel._ocr_view.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    cursor.insertText("Oh, ")

    panel._source_btn.setChecked(True)

    assert panel._ocr_text.toPlainText() == "Oh, Hello **world**"


@pytest.mark.gui
def test_reload_commits_pending_typing_first(qtbot) -> None:
    """
    Given  uncommitted typing in the formatted view
    When   load() replaces the panel content
    Then   the typed text was committed (edit signal) before the content was replaced
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.load(_pixmap(), "Hello **world**", "", "", text_format="markdown")
    emitted: list[str] = []
    panel.ocr_text_edited.connect(emitted.append)
    cursor = panel._ocr_view.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    cursor.insertText("Oh, ")

    panel.load(_pixmap(), "Other **text**", "", "", text_format="markdown")

    assert emitted == ["Oh, Hello **world**"]
    assert panel._ocr_text.toPlainText() == "Other **text**"


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
