"""GUI tests for the three texts of the Check tab.

*What was read* and *Final text* are editable and carry their marks as overlays, so the text a
view stores is the same with and without them.  *With corrections* is read-only and the only view
that carries formatting of its own: the student's words, struck through, in front of the words
that replaced them.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtTest import QTest

from teachers_teammate.application.check_document import CheckDocument, check_document_of
from teachers_teammate.application.check_items import ItemKind
from teachers_teammate.application.formatting import TextFormatter
from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.proofreading.edits import edits
from teachers_teammate.domain.proofreading.review import CorrectionReview, edit_note
from teachers_teammate.domain.teacher_work import TextEdit
from teachers_teammate.gui._formatted_text import document_from_qt
from teachers_teammate.gui.check import _style
from teachers_teammate.gui.check._masking import Masking
from teachers_teammate.gui.check._text_views import (
    MAX_POINT_SIZE,
    MIN_POINT_SIZE,
    TEXT_POINT_SIZE,
    CheckTextView,
    FinalView,
    MarkedView,
    ReadView,
    marks_of,
)
from teachers_teammate.application.document_view import DocumentStateView

RAW = "Der bunte Hund bellt laut."
CORRECTED = "Der braune Hund bellt laut."

#: A stored Markdown text with a heading, a list and a table: the constructs a mark has to land in.
MARKDOWN_RAW = (
    "# Titel\n\n- Der bunte Hund.\n- Die Katze.\n\n| a | b |\n| --- | --- |\n| bunte | x |\n"
)
MARKDOWN_CORRECTED = MARKDOWN_RAW.replace("bunte", "braune")


def _document(
    raw: str = RAW,
    corrected: str = CORRECTED,
    *,
    text_format: TextFormat = "plain",
    review: bool = True,
) -> CheckDocument:
    """Return the Check tab's data for a stored document with a proofreading."""
    raw_plain = TextFormatter.plain_text(raw, text_format)
    corrected_plain = TextFormatter.plain_text(corrected, text_format)
    notes = (
        CorrectionReview(
            edits=tuple(
                edit_note(edit, category="word_choice", explanation="Farbe.")
                for edit in edits(raw_plain, corrected_plain)
            )
        )
        if review
        else None
    )
    return check_document_of(
        DocumentStateView(
            source_id="/tmp/Anna.png",
            name="Anna.png",
            preview_img="",
            raw_text=raw,
            correction_text=corrected,
            evaluation_text="",
            ocr_done=True,
            correction_done=True,
            evaluation_done=False,
            original_img="",
            preprocess_steps=(),
            raw_text_format=text_format,
            correction_review=notes,
            correction_warning="",
            sent={},
            page_images=(),
            original_images=(),
        )
    )


def _view(qtbot, view_class: type[CheckTextView], doc: CheckDocument) -> CheckTextView:
    """Return a shown view of *doc*'s corrected text, with the marks of its review."""
    view = view_class()
    qtbot.addWidget(view)
    view.resize(600, 400)
    view.show()
    qtbot.wait(20)  # let the resize reach the viewport, as it does in the window
    if isinstance(view, MarkedView):
        view.show_document(doc, Masking())
    else:
        view.set_text(doc.corrected_text, doc.text_format)
        view.set_marks(marks_of(doc, Masking()), plain=doc.corrected_plain)
    return view


def _marked_words(view: CheckTextView) -> list[str]:
    """The words the marks of *view* sit on, in text order."""
    return [cursor.selectedText() for _mark, cursor in view._mark_cursors]


def _point_on(view: CheckTextView, cursor: QTextCursor) -> QPoint:
    """Return a point inside the words *cursor* selects."""
    first = QTextCursor(cursor)
    first.setPosition(cursor.selectionStart())
    return view.cursorRect(first).center()


def _select(view: CheckTextView, words: str) -> None:
    """Select the first occurrence of *words* in *view*."""
    cursor = view.document().find(words)
    assert not cursor.isNull()
    view.setTextCursor(cursor)


def _stored(view: CheckTextView) -> str:
    """The text *view* would store, in the notation of its document."""
    doc = document_from_qt(view.document())
    return TextFormatter.markdown(doc) if view._format == "markdown" else TextFormatter.plain(doc)


def _colour_at(view: CheckTextView, word: str) -> str:
    """The colour of the first occurrence of *word* in *view*."""
    document = view.document()
    cursor = QTextCursor(document)
    cursor.setPosition(document.toPlainText().index(word))
    cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor)
    return cursor.charFormat().foreground().color().name()


def _canonical(text: str, text_format: TextFormat) -> str:
    """Return *text* in the notation a view stores, without the layout it does not show."""
    doc = TextFormatter.document(text, text_format)
    return TextFormatter.markdown(doc) if text_format == "markdown" else TextFormatter.plain(doc)


# ── The marks ─────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_marks_land_on_the_words_of_a_plain_text(qtbot) -> None:
    """
    Given  a proofreading of a plain text
    When   the final text is shown with the marks of its review
    Then   the mark sits on the words the model put there
    """
    view = _view(qtbot, FinalView, _document())

    assert _marked_words(view) == ["braune"]


@pytest.mark.gui
def test_marks_land_on_the_words_of_a_markdown_text(qtbot) -> None:
    """
    Given  a proofreading of a Markdown text with a heading, a list and a table
    When   the final text is shown with the marks of its review
    Then   every mark sits on its own words, in the list item and in the table cell
    """
    view = _view(
        qtbot, FinalView, _document(MARKDOWN_RAW, MARKDOWN_CORRECTED, text_format="markdown")
    )

    assert _marked_words(view) == ["braune", "braune"]


@pytest.mark.gui
def test_a_mark_of_every_kind_lands_on_its_words(qtbot) -> None:
    """
    Given  a document whose review knows one change, one unclear passage and one taken-out note
    When   the final text is shown with the marks of its review
    Then   each mark sits on the words of its item, and the taken-out one on the words it left
    """
    doc = _document()
    assert doc.aligned is not None
    view = _view(qtbot, FinalView, doc)

    kinds = [mark.kind for mark in marks_of(doc, Masking())]

    assert kinds == [ItemKind.CORRECTION]
    assert _marked_words(view) == ["braune"]


@pytest.mark.gui
def test_a_mark_is_drawn_in_the_look_of_its_item(qtbot) -> None:
    """
    Given  a document with one explained correction
    When   the final text is shown with the marks of its review
    Then   the mark carries the item's look, and is drawn in that look's background
    """
    doc = _document()
    view = _view(qtbot, FinalView, doc)

    mark, _cursor = view._mark_cursors[0]

    assert mark.look == _style.look(doc.items[0])
    assert view.extraSelections()[0].format.background().color() == QColor(mark.look.background)


@pytest.mark.gui
def test_the_chosen_item_is_scrolled_to_without_selecting_its_words(qtbot) -> None:
    """
    Given  a Markdown text with two corrections, the second one far down the text
    When   the second item is revealed
    Then   the caret stands at its first word and nothing is selected, so the theme's selection
           blue is not drawn over the mark
    """
    doc = _document(MARKDOWN_RAW, MARKDOWN_CORRECTED, text_format="markdown")
    view = _view(qtbot, FinalView, doc)
    second = doc.items[1]

    view.reveal(second.key)

    assert view.textCursor().selectedText() == ""
    assert view.textCursor().position() == view._mark_cursors[1][1].selectionStart()


@pytest.mark.gui
def test_a_click_on_a_mark_tells_the_page_which_item_it_is(qtbot) -> None:
    """
    Given  a shown final text with a mark
    When   the teacher clicks the marked words
    Then   the view says which item was clicked
    """
    view = _view(qtbot, FinalView, _document())
    cursor = view._mark_cursors[0][1]
    middle = QTextCursor(view.document())
    middle.setPosition((cursor.selectionStart() + cursor.selectionEnd()) // 2)

    with qtbot.waitSignal(view.item_clicked, timeout=1000) as blocker:
        QTest.mouseClick(
            view.viewport(), Qt.MouseButton.LeftButton, pos=view.cursorRect(middle).center()
        )

    assert blocker.args == ["edit:1"]


@pytest.mark.gui
def test_a_click_beside_a_mark_tells_nothing(qtbot) -> None:
    """
    Given  a shown final text with a mark
    When   the teacher clicks words that are not marked
    Then   no item is reported
    """
    view = _view(qtbot, FinalView, _document())
    clicked: list[str] = []
    view.item_clicked.connect(clicked.append)

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.cursorRect().center())

    assert clicked == []


@pytest.mark.gui
@pytest.mark.parametrize(
    ("raw", "corrected", "text_format"),
    [
        (RAW, CORRECTED, "plain"),
        (MARKDOWN_RAW, MARKDOWN_CORRECTED, "markdown"),
    ],
    ids=["plain", "markdown"],
)
def test_a_mark_never_changes_the_text_a_view_stores(
    qtbot, raw: str, corrected: str, text_format: TextFormat
) -> None:
    """
    Given  a shown final text
    When   the marks of its review are drawn
    Then   the text the view would store is the same as without them
    """
    doc = _document(raw, corrected, text_format=text_format)
    view = FinalView()
    qtbot.addWidget(view)
    view.set_text(doc.corrected_text, doc.text_format)
    without = _stored(view)

    view.set_marks(marks_of(doc, Masking()), plain=doc.corrected_plain)

    assert _stored(view) == without


# ── The student's words and the model's ───────────────────────────────────


@pytest.mark.gui
def test_the_students_crossed_out_words_are_grey(qtbot) -> None:
    """
    Given  a transcription in which the student crossed out a word
    When   it is shown in the view the teacher reads
    Then   the crossed-out word is grey, not the red of the model's own changes
    """
    view = ReadView()
    qtbot.addWidget(view)
    view.set_text("Der ~~bunte~~ braune Hund.", "markdown")

    assert _colour_at(view, "bunte") == _style.STUDENT_STRIKE


@pytest.mark.gui
def test_the_model_took_out_words_are_red_in_the_marked_view(qtbot) -> None:
    """
    Given  a proofreading that replaced a word
    When   the text with corrections is shown
    Then   the student's word stands struck through in front of the new one, in red
    """
    view = _view(qtbot, MarkedView, _document())

    assert view.toPlainText() == "Der bunte braune Hund bellt laut."
    assert _colour_at(view, "bunte") == _style.MODEL_STRIKE


@pytest.mark.gui
def test_a_deleted_word_stands_struck_where_it_was(qtbot) -> None:
    """
    Given  a proofreading that took a word out
    When   the text with corrections is shown
    Then   the student's word stands struck through where the corrected text has a gap
    """
    view = _view(qtbot, MarkedView, _document(RAW, "Der Hund bellt laut."))

    assert view.toPlainText() == "Der bunte Hund bellt laut."


@pytest.mark.gui
def test_an_inserted_word_shows_no_struck_words(qtbot) -> None:
    """
    Given  a proofreading that inserted a word the student never wrote
    When   the text with corrections is shown
    Then   nothing is struck through: there are no words of the student to show
    """
    view = _view(qtbot, MarkedView, _document("Der Hund bellt laut.", CORRECTED))

    assert view.toPlainText() == CORRECTED
    assert _colour_at(view, "braune") != _style.MODEL_STRIKE


# ── Names hidden ──────────────────────────────────────────────────────────


def _masking() -> Masking:
    """Return a masking that hides one name behind its placeholder."""
    masking = Masking()
    masking.set_available(True)
    masking.set_enabled(True)
    masking.set_result({"[PERSON_1]": "Anna"})
    return masking


@pytest.mark.gui
def test_marks_sit_on_the_same_words_when_the_names_are_hidden(qtbot) -> None:
    """
    Given  a document that mentions a name, and hiding turned on
    When   the final text is shown with the marks of its review
    Then   the name is a placeholder and the mark still sits on the corrected words
    """
    doc = _document("Anna sagt, der bunte Hund bellt.", "Anna sagt, der braune Hund bellt.")
    masking = _masking()
    view = FinalView()
    qtbot.addWidget(view)
    view.set_text(masking.mask(doc.corrected_text), doc.text_format)
    view.set_marks(marks_of(doc, masking), plain=masking.mask(doc.corrected_plain))

    assert "[PERSON_1]" in view.toPlainText()
    assert _marked_words(view) == ["braune"]


@pytest.mark.gui
def test_the_marked_view_hides_the_names_too(qtbot) -> None:
    """
    Given  a document that mentions a name, and hiding turned on
    When   the text with corrections is shown
    Then   the name is a placeholder there as well, and the student's word is still struck
    """
    doc = _document("Anna sagt, der bunte Hund bellt.", "Anna sagt, der braune Hund bellt.")
    view = MarkedView()
    qtbot.addWidget(view)

    view.show_document(doc, _masking())

    assert view.toPlainText() == "[PERSON_1] sagt, der bunte braune Hund bellt."


# ── What a view offers ────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_formatting_keys_are_offered_only_for_a_markdown_text(qtbot) -> None:
    """
    Given  a plain text and a Markdown text in the same editable view
    When   the view is filled with each
    Then   only the Markdown one offers the formatting keys, which a plain text cannot store
    """
    view = FinalView()
    qtbot.addWidget(view)

    view.set_text(RAW, "plain")
    plain = [action.shortcut().toString() for action in view._formatting]

    view.set_text(MARKDOWN_RAW, "markdown")
    markdown = [action.shortcut().toString() for action in view._formatting]

    assert plain == []
    assert markdown == ["Ctrl+B", "Ctrl+I", "Ctrl+U", "Ctrl+Shift+X", "Ctrl+Shift+="]


@pytest.mark.gui
def test_ctrl_i_changes_nothing_in_a_plain_text(qtbot) -> None:
    """
    Given  an editable view of a plain text, with a word selected
    When   Ctrl+I is pressed
    Then   the text is unchanged, because a plain text stores no character style
    """
    view = FinalView()
    qtbot.addWidget(view)
    view.resize(600, 400)
    view.show()
    qtbot.wait(20)
    view.set_text(RAW, "plain")
    _select(view, "bunte")
    view.setFocus()

    QTest.keyClick(view, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier)

    assert _stored(view) == RAW


@pytest.mark.gui
def test_ctrl_i_is_stored_in_a_markdown_text(qtbot) -> None:
    """
    Given  an editable view of a Markdown text, with a word selected
    When   Ctrl+I is pressed
    Then   the word is italic in the text the view stores
    """
    view = FinalView()
    qtbot.addWidget(view)
    view.resize(600, 400)
    view.show()
    qtbot.wait(20)
    view.set_text(MARKDOWN_RAW, "markdown")
    _select(view, "bunte")
    view.setFocus()

    QTest.keyClick(view, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier)

    assert "*bunte*" in _stored(view)


@pytest.mark.gui
def test_hovering_a_mark_says_what_the_item_is(qtbot) -> None:
    """
    Given  a document with one explained correction
    When   the pointer rests on its mark
    Then   the tip names the words, the category and the first line of the explanation
    """
    doc = _document()
    view = _view(qtbot, FinalView, doc)
    _mark, cursor = view._mark_cursors[0]

    tip = view._tip_at(_point_on(view, cursor))

    assert "bunte \u2192 braune" in tip
    assert "Word choice" in tip
    assert "Farbe." in tip


@pytest.mark.gui
def test_a_point_beside_a_mark_has_no_tip(qtbot) -> None:
    """
    Given  a document with one correction
    When   the pointer rests on a word the review did not touch
    Then   there is no tip of an item, so the view's own tip shows
    """
    doc = _document()
    view = _view(qtbot, FinalView, doc)
    _mark, cursor = view._mark_cursors[0]
    beside = _point_on(view, cursor)
    beside.setX(beside.x() + 400)

    assert view._tip_at(beside) == ""


# ── Typing ────────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_a_read_only_view_takes_no_typing(qtbot) -> None:
    """
    Given  a view that is not editable
    When   the teacher types into it
    Then   the text is unchanged
    """
    view = ReadView()
    qtbot.addWidget(view)
    view.set_text(RAW, "plain")
    view.set_editable(False)

    QTest.keyClicks(view, "x")

    assert view.toPlainText() == RAW


@pytest.mark.gui
@pytest.mark.use_case("Manual_Text_Editing")
def test_an_edit_is_stored_after_the_pause(qtbot) -> None:
    """
    Given  an editable view of a stored text
    When   the teacher types into it and stops
    Then   the view hands the new text over once
    """
    view = ReadView()
    qtbot.addWidget(view)
    view.set_text(RAW, "plain")
    view.moveCursor(QTextCursor.MoveOperation.End)

    with qtbot.waitSignal(view.text_edited, timeout=2000) as blocker:
        QTest.keyClicks(view, "!")

    assert blocker.args == [RAW + "!"]


@pytest.mark.gui
def test_an_untouched_text_stores_nothing(qtbot) -> None:
    """
    Given  an editable view of a stored text
    When   it is asked to store what was typed, and nothing was
    Then   it hands nothing over: a commit that changes nothing would only cost a reload
    """
    view = ReadView()
    qtbot.addWidget(view)
    view.set_text(RAW, "plain")
    stored: list[str] = []
    view.text_edited.connect(stored.append)

    view.flush()

    assert stored == []


@pytest.mark.gui
def test_a_plain_text_that_ends_with_a_line_break_is_not_an_edit(qtbot) -> None:
    """
    Given  a stored plain text that ends with a line break the document does not show
    When   it is shown and stored without a keystroke
    Then   nothing is handed over
    """
    view = ReadView()
    qtbot.addWidget(view)
    view.set_text(RAW + "\n", "plain")
    stored: list[str] = []
    view.text_edited.connect(stored.append)

    view.flush()

    assert stored == []


# ── The card actions ──────────────────────────────────────────────────────


@pytest.mark.gui
@pytest.mark.use_case("Correction_Take_Out_And_Put_Back")
def test_a_card_action_is_one_undo_step(qtbot) -> None:
    """
    Given  a final text with a correction
    When   the correction is taken out and the teacher presses Ctrl+Z
    Then   the corrected text is back, in one step
    """
    view = _view(qtbot, FinalView, _document())
    stored: list[str] = []
    view.text_edited.connect(stored.append)

    view.apply_plain_edits([TextEdit(4, 10, "bunte")])
    assert view.toPlainText() == RAW
    assert stored == [RAW]

    view.undo()
    view.flush()

    assert view.toPlainText() == CORRECTED
    assert stored == [RAW, CORRECTED]


@pytest.mark.gui
def test_a_card_action_lands_in_a_markdown_text(qtbot) -> None:
    """
    Given  a Markdown final text with a correction in a table cell
    When   the correction is taken out
    Then   the student's word is back in the cell and nothing else moved
    """
    doc = _document(MARKDOWN_RAW, MARKDOWN_CORRECTED, text_format="markdown")
    view = _view(qtbot, FinalView, doc)

    view.apply_plain_edits(
        [
            TextEdit(item.corrected_spans[0][0], item.corrected_spans[0][1], "bunte")
            for item in doc.items
        ]
    )

    assert _stored(view) == _canonical(MARKDOWN_RAW, "markdown")


@pytest.mark.gui
def test_putting_words_back_at_the_end_of_a_paragraph_keeps_the_paragraphs(qtbot) -> None:
    """
    Given  a Markdown final text whose first paragraph lost its last word
    When   an insertion of that word right behind the paragraph is applied
    Then   the word is back in the first paragraph and the two paragraphs stay two
    """
    raw, corrected = "Eins zwei\n\nDrei\n", "Eins\n\nDrei\n"
    view = _view(qtbot, FinalView, _document(raw, corrected, text_format="markdown"))

    view.apply_plain_edits([TextEdit(4, 4, " zwei")])

    assert _stored(view) == _canonical(raw, "markdown")


@pytest.mark.gui
def test_putting_words_back_at_the_start_of_a_paragraph_keeps_the_paragraphs(qtbot) -> None:
    """
    Given  a Markdown final text whose second paragraph lost its first word
    When   an insertion of that word right in front of the paragraph is applied
    Then   the word is back in the second paragraph and the two paragraphs stay two
    """
    raw, corrected = "Eins\n\nzwei Drei\n", "Eins\n\nDrei\n"
    view = _view(qtbot, FinalView, _document(raw, corrected, text_format="markdown"))

    view.apply_plain_edits([TextEdit(6, 6, "zwei ")])

    assert _stored(view) == _canonical(raw, "markdown")


@pytest.mark.gui
def test_the_struck_words_of_a_deleted_last_word_stay_in_their_paragraph(qtbot) -> None:
    """
    Given  a Markdown text whose first paragraph lost its last word
    When   the With corrections view shows it
    Then   the struck word stands at the end of the first paragraph
    """
    raw, corrected = "Eins zwei\n\nDrei\n", "Eins\n\nDrei\n"
    view = _view(qtbot, MarkedView, _document(raw, corrected, text_format="markdown"))

    first, second = view.document().firstBlock(), view.document().firstBlock().next()

    assert "zwei" in first.text()
    assert "zwei" not in second.text()


# ── The text size ─────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_text_size_stays_within_its_range(qtbot) -> None:
    """
    Given  a view at its default text size
    When   the teacher zooms far in and far out
    Then   the size stops at the ends of its range and every change is reported
    """
    view = ReadView()
    qtbot.addWidget(view)
    sizes: list[int] = []
    view.zoom_changed.connect(sizes.append)

    view.zoom_by(-100)
    view.zoom_by(100)

    assert view.point_size == MAX_POINT_SIZE
    assert sizes == [MIN_POINT_SIZE, MAX_POINT_SIZE]
    assert TEXT_POINT_SIZE > MIN_POINT_SIZE
