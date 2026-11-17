"""GUI tests for the Check tab's page: header, Copy menu and Esc."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QComboBox, QLineEdit, QTextEdit

from teachers_teammate.application.check_document import CheckDocument, check_document_of
from teachers_teammate.gui import _user_text as txt
from teachers_teammate.gui._export_menu import ExportKind
from teachers_teammate.gui.check import CheckPage, CopyKind, CopyState
from teachers_teammate.gui.check._page import _escape_is_for_the_focus
from teachers_teammate.infrastructure.state_repository import DocumentStateView


def _document(name: str, source_id: str) -> CheckDocument:
    """Return the Check tab's data for a stored document that has no text yet."""
    return check_document_of(
        DocumentStateView(
            source_id=source_id,
            name=name,
            preview_img="",
            raw_text="",
            correction_text="",
            evaluation_text="",
            ocr_done=False,
            correction_done=False,
            evaluation_done=False,
            cache_status_label="",
            loaded_at="",
            original_img="",
            preprocess_steps=(),
            raw_text_format="plain",
            correction_review=None,
            correction_warning="",
            sent={},
            page_images=(),
            original_images=(),
        )
    )


def _press_escape(page: CheckPage) -> None:
    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    page.keyPressEvent(event)


@pytest.mark.gui
def test_check_page_starts_empty(qtbot) -> None:
    """
    Given  a fresh Check page
    When   it is shown for the first time
    Then   the header names no document and both arrows are off
    """
    page = CheckPage()
    qtbot.addWidget(page)

    assert page._name_label.text() == ""
    assert page._position_label.isHidden() is True
    assert page._previous_btn.isEnabled() is False
    assert page._next_btn.isEnabled() is False


@pytest.mark.gui
def test_check_page_shows_name_position_and_arrows(qtbot) -> None:
    """
    Given  a Check page
    When   a document in the middle of the queue is shown
    Then   the header names it, says where it sits and offers both arrows
    """
    page = CheckPage()
    qtbot.addWidget(page)

    page.show_document(_document("Anna_Mueller.pdf", "/tmp/Anna_Mueller.pdf"), 3, 12)

    assert page._name_label.text() == "Anna_Mueller.pdf"
    assert page._name_label.toolTip() == "/tmp/Anna_Mueller.pdf"
    assert page._position_label.text() == "3 of 12"
    assert page._position_label.isHidden() is False
    assert page._previous_btn.isEnabled() is True
    assert page._next_btn.isEnabled() is True


@pytest.mark.gui
@pytest.mark.parametrize(
    ("position", "total", "previous", "next_enabled"),
    [(1, 12, False, True), (12, 12, True, False), (1, 1, False, False)],
)
def test_check_page_arrows_stop_at_the_ends(
    qtbot, position: int, total: int, previous: bool, next_enabled: bool
) -> None:
    """
    Given  a Check page
    When   the first, the last or the only document is shown
    Then   the arrows do not wrap around
    """
    page = CheckPage()
    qtbot.addWidget(page)

    page.show_document(_document("a.pdf", "/tmp/a.pdf"), position, total)

    assert page._previous_btn.isEnabled() is previous
    assert page._next_btn.isEnabled() is next_enabled


@pytest.mark.gui
def test_check_page_elides_a_long_name_and_keeps_the_path_in_the_tooltip(qtbot) -> None:
    """
    Given  a Check page
    When   a document with a very long name is shown
    Then   the header shortens the name in the middle and the tooltip keeps the whole path
    """
    page = CheckPage()
    qtbot.addWidget(page)
    name = "a_very_long_document_name_that_does_not_fit_into_the_header_at_all.pdf"

    page.show_document(_document(name, f"/tmp/{name}"), 1, 2)

    assert page._name_label.text() != name
    assert page._name_label.text().endswith(".pdf")
    assert page._name_label.toolTip() == f"/tmp/{name}"


@pytest.mark.gui
def test_check_page_clear_empties_the_header(qtbot) -> None:
    """
    Given  a Check page showing a document
    When   clear is called
    Then   the header is empty again and the Copy menu offers nothing
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.show_document(_document("a.pdf", "/tmp/a.pdf"), 2, 3)
    page.set_copy_state(CopyState(has_raw=True, has_corrected=True, can_hide_names=True))

    page.clear()

    assert page._name_label.text() == ""
    assert page._name_label.toolTip() == ""
    assert page._position_label.isHidden() is True
    assert page._previous_btn.isEnabled() is False
    assert page._next_btn.isEnabled() is False
    assert not any(action.isEnabled() for action in page._copy_actions.values())


@pytest.mark.gui
def test_check_page_arrows_ask_for_the_neighbour(qtbot) -> None:
    """
    Given  a Check page showing a document in the middle
    When   the two arrows are clicked
    Then   the page asks for the previous and the next document
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.show_document(_document("b.pdf", "/tmp/b.pdf"), 2, 3)
    asked: list[str] = []
    page.previous_requested.connect(lambda: asked.append("previous"))
    page.next_requested.connect(lambda: asked.append("next"))

    page._previous_btn.click()
    page._next_btn.click()

    assert asked == ["previous", "next"]


@pytest.mark.gui
def test_check_page_copy_menu_offers_every_entry(qtbot) -> None:
    """
    Given  a Check page
    When   its Copy menu is read
    Then   it lists the four copy entries and the two save entries in order
    """
    page = CheckPage()
    qtbot.addWidget(page)

    labels = [
        action.text() for action in page._copy_btn.menu().actions() if not action.isSeparator()
    ]

    assert labels == [
        "Copy what was read",
        "Copy what was read, names hidden",
        "Copy corrected text",
        "Copy corrected text, names hidden",
        "Save what was read as text file\u2026",
        "Save it with names hidden\u2026",
    ]


@pytest.mark.gui
def test_check_page_copy_state_without_a_proofreading(qtbot) -> None:
    """
    Given  a document that was only read, and names can be hidden
    When   the copy state is pushed
    Then   the corrected entries are off and the rest is on
    """
    page = CheckPage()
    qtbot.addWidget(page)

    page.set_copy_state(CopyState(has_raw=True, can_hide_names=True))

    assert page._copy_actions[CopyKind.RAW].isEnabled() is True
    assert page._copy_actions[CopyKind.RAW_ANONYMIZED].isEnabled() is True
    assert page._copy_actions[CopyKind.CORRECTED].isEnabled() is False
    assert page._copy_actions[CopyKind.CORRECTED_ANONYMIZED].isEnabled() is False
    assert page._save_actions[ExportKind.TEXT].isEnabled() is True
    assert page._save_actions[ExportKind.TEXT_ANONYMIZED].isEnabled() is True


@pytest.mark.gui
def test_check_page_copy_state_without_the_privacy_addon(qtbot) -> None:
    """
    Given  a document with both texts, but names cannot be hidden
    When   the copy state is pushed
    Then   the "names hidden" entries are off and say where names could be hidden
    """
    page = CheckPage()
    qtbot.addWidget(page)

    page.set_copy_state(CopyState(has_raw=True, has_corrected=True))

    assert page._copy_actions[CopyKind.RAW].isEnabled() is True
    assert page._copy_actions[CopyKind.CORRECTED].isEnabled() is True
    assert page._copy_actions[CopyKind.RAW_ANONYMIZED].isEnabled() is False
    assert page._copy_actions[CopyKind.CORRECTED_ANONYMIZED].isEnabled() is False
    assert page._save_actions[ExportKind.TEXT_ANONYMIZED].isEnabled() is False
    assert (
        page._copy_actions[CopyKind.RAW_ANONYMIZED].toolTip()
        == txt.HIDE_PERSONAL_INFO_UNAVAILABLE_TIP
    )
    assert page._copy_actions[CopyKind.RAW].toolTip() != txt.HIDE_PERSONAL_INFO_UNAVAILABLE_TIP


@pytest.mark.gui
def test_check_page_copy_menu_asks_for_the_chosen_kind(qtbot) -> None:
    """
    Given  a Check page with both texts available
    When   one copy entry and one save entry are triggered
    Then   the page asks for exactly those two
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.set_copy_state(CopyState(has_raw=True, has_corrected=True, can_hide_names=True))
    asked: list[object] = []
    page.copy_requested.connect(asked.append)
    page.save_requested.connect(asked.append)

    page._copy_actions[CopyKind.CORRECTED_ANONYMIZED].trigger()
    page._save_actions[ExportKind.TEXT_ANONYMIZED].trigger()

    assert asked == [CopyKind.CORRECTED_ANONYMIZED, ExportKind.TEXT_ANONYMIZED]


@pytest.mark.gui
def test_check_page_confirms_a_copy_and_hides_the_note_again(qtbot) -> None:
    """
    Given  a Check page
    When   a copy is confirmed
    Then   the note shows next to the button and goes away on its own
    """
    page = CheckPage()
    qtbot.addWidget(page)

    page.show_copied()

    assert page._copied_label.isHidden() is False
    assert page._copied_label.text() == "Copied"
    assert page._copied_timer.isActive() is True


@pytest.mark.gui
def test_check_page_escape_asks_to_go_back(qtbot) -> None:
    """
    Given  a Check page whose focus is nowhere in particular
    When   Esc is pressed
    Then   the page asks to go back to Documents
    """
    page = CheckPage()
    qtbot.addWidget(page)
    asked: list[str] = []
    page.back_requested.connect(lambda: asked.append("back"))

    _press_escape(page)

    assert asked == ["back"]


@pytest.mark.gui
@pytest.mark.parametrize(
    ("kind", "expected"),
    [(None, False), ("line", True), ("text", True), ("combo", False)],
)
def test_escape_belongs_to_the_focus_only_where_the_teacher_types(
    qtbot, kind: str | None, expected: bool
) -> None:
    """
    Given  a widget of one kind
    When   the page asks whether Esc is the widget's own key
    Then   only a field the teacher can type in claims it
    """
    widget = (
        None if kind is None else {"line": QLineEdit, "text": QTextEdit, "combo": QComboBox}[kind]()
    )
    if widget is not None:
        qtbot.addWidget(widget)

    assert _escape_is_for_the_focus(widget) is expected


@pytest.mark.gui
def test_escape_belongs_to_a_read_only_text(qtbot) -> None:
    """
    Given  a text view the teacher cannot type in
    When   the page asks whether Esc is the widget's own key
    Then   it is not, so Esc still leaves the tab
    """
    view = QTextEdit()
    qtbot.addWidget(view)
    view.setReadOnly(True)

    assert _escape_is_for_the_focus(view) is False


@pytest.mark.gui
def test_escape_belongs_to_an_editable_combo(qtbot) -> None:
    """
    Given  a combo box the teacher can type into
    When   the page asks whether Esc is the widget's own key
    Then   it is, because Esc drops what was typed
    """
    combo = QComboBox()
    qtbot.addWidget(combo)
    combo.setEditable(True)

    assert _escape_is_for_the_focus(combo) is True
