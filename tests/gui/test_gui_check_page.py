"""GUI tests for the Check tab's page: header, Copy menu and Esc."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QComboBox, QLineEdit, QTextEdit

from teachers_teammate.application.check_document import CheckDocument, check_document_of
from teachers_teammate.domain.teacher_work import WorkStatus
from teachers_teammate.texts import review as txt
from teachers_teammate.gui._export_menu import ExportKind
from teachers_teammate.gui.check import CheckPage, CopyKind, CopyState, RunState
from teachers_teammate.gui.check._page import _escape_is_for_the_focus
from teachers_teammate.application.document_view import DocumentStateView


def _document(
    name: str, source_id: str, *, raw_text: str = "", work: WorkStatus | None = None
) -> CheckDocument:
    """Return the Check tab's data for a stored document, with what the teacher did to it."""
    return check_document_of(
        DocumentStateView(
            source_id=source_id,
            name=name,
            preview_img="",
            raw_text=raw_text,
            correction_text="",
            evaluation_text="",
            ocr_done=False,
            correction_done=False,
            evaluation_done=False,
            original_img="",
            preprocess_steps=(),
            raw_text_format="plain",
            correction_review=None,
            correction_warning="",
            sent={},
            page_images=(),
            original_images=(),
            work=work or WorkStatus(),
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


@pytest.mark.gui
@pytest.mark.parametrize(
    ("work", "shown", "checked"),
    [
        (WorkStatus(), txt.MARK_AS_CHECKED, False),
        (WorkStatus(transcription_checked=True), txt.TEXT_CHECKED_BUTTON, True),
        (WorkStatus(corrections_checked=True), txt.CHECKED_BUTTON, True),
    ],
)
def test_check_page_checked_toggle_says_what_was_checked(
    qtbot, work: WorkStatus, shown: str, checked: bool
) -> None:
    """
    Given  a document whose work says nothing, the text, or the corrections were checked
    When   the Check page shows it
    Then   the header's toggle says so and is pressed accordingly
    """
    page = CheckPage()
    qtbot.addWidget(page)

    page.show_document(_document("a.pdf", "/tmp/a.pdf", raw_text="what was read", work=work), 1, 1)

    assert page._checked_btn.text() == shown
    assert page._checked_btn.isChecked() is checked


@pytest.mark.gui
def test_check_page_checked_toggle_needs_a_text(qtbot) -> None:
    """
    Given  a document that was never read
    When   the Check page shows it
    Then   *Checked* cannot be pressed: there is nothing to check
    """
    page = CheckPage()
    qtbot.addWidget(page)

    page.show_document(_document("a.pdf", "/tmp/a.pdf"), 1, 1)

    assert page._checked_btn.isEnabled() is False


@pytest.mark.gui
def test_check_page_checked_toggle_is_disabled_while_the_run_holds_the_document(qtbot) -> None:
    """
    Given  a document with a text, open in the Check page
    When   the run takes the document and then lets it go
    Then   *Checked* is disabled while the lock holds and enabled again afterwards
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.show_document(_document("a.pdf", "/tmp/a.pdf", raw_text="what was read"), 1, 1)

    page.set_lock(txt.BEING_PROOFREAD)
    locked = page._checked_btn.isEnabled()
    page.set_lock(None)

    assert locked is False
    assert page._checked_btn.isEnabled() is True


@pytest.mark.gui
def test_check_page_checked_toggle_asks_for_the_new_state(qtbot) -> None:
    """
    Given  a document with a text, open in the Check page
    When   the teacher presses *Checked*
    Then   the page asks for the document to be marked as checked
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.show_document(_document("a.pdf", "/tmp/a.pdf", raw_text="what was read"), 1, 1)

    with qtbot.waitSignal(page.checked_toggled, timeout=1000) as sig:
        page._checked_btn.click()

    assert sig.args == [True]


@pytest.mark.gui
def test_check_page_mark_looked_at_says_whether_it_was_new(qtbot) -> None:
    """
    Given  a document open in the Check page
    When   the same item is marked as looked at twice
    Then   only the first time is new, so the window writes to the store once
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.show_document(_document("a.pdf", "/tmp/a.pdf", raw_text="what was read"), 1, 1)

    first = page.mark_looked_at("edit:1")
    second = page.mark_looked_at("edit:1")

    assert first is True
    assert second is False


@pytest.mark.gui
def test_check_page_records_nothing_as_looked_at_while_the_run_holds_the_document(qtbot) -> None:
    """
    Given  a document the run holds (the page is locked)
    When   an item is marked as looked at
    Then   nothing is recorded, so the window writes nothing for a document a run is working on
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.show_document(_document("a.pdf", "/tmp/a.pdf", raw_text="what was read"), 1, 1)
    page.set_lock(txt.BEING_PROOFREAD)

    held = page.mark_looked_at("edit:1")
    page.set_lock(None)
    free = page.mark_looked_at("edit:1")

    assert held is False
    assert free is True


@pytest.mark.gui
def test_check_page_refresh_checked_puts_the_button_back_to_what_the_document_says(qtbot) -> None:
    """
    Given  an unchecked document whose *Checked* button was pressed but not stored
    When   the page is asked to show *Checked* as the document says
    Then   the button is not pressed any more
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.show_document(_document("a.pdf", "/tmp/a.pdf", raw_text="what was read"), 1, 1)
    page._checked_btn.click()
    assert page._checked_btn.isChecked() is True

    page.refresh_checked()

    assert page._checked_btn.isChecked() is False
    assert page._checked_btn.text() == txt.MARK_AS_CHECKED


@pytest.mark.gui
@pytest.mark.parametrize("wish_first", [True, False])
def test_hide_names_shows_unticked_while_names_cannot_be_hidden(qtbot, wish_first) -> None:
    """
    Given  a stored wish to hide names and no add-on to do it
    When   the page is told both, in either order
    Then   the box is greyed out and unticked, and ticked again once the add-on arrives
    """
    page = CheckPage()
    qtbot.addWidget(page)

    if wish_first:
        page.set_display_anonymized(True)
        page.set_display_anonymized_available(False)
    else:
        page.set_display_anonymized_available(False)
        page.set_display_anonymized(True)

    assert not page._hide_names_box.isEnabled()
    assert not page._hide_names_box.isChecked()

    page.set_display_anonymized_available(True)

    assert page._hide_names_box.isEnabled()
    assert page._hide_names_box.isChecked()


@pytest.mark.gui
def test_the_next_step_strip_tells_what_to_do_after_reading(qtbot) -> None:
    """
    Given  a document that was read but not proofread
    When   it is shown
    Then   a strip says to check the text and proofread, with Proofread this document as its
           button, and pressing it asks for the proofreading
    """
    page = CheckPage()
    qtbot.addWidget(page)
    asked: list[str] = []
    page.proofread_requested.connect(lambda: asked.append("proofread"))

    page.show_document(_document("a.png", "/tmp/a.png", raw_text="Hallo"), 1, 1)

    assert not page._next_step.isHidden()
    assert page._next_step.button.text() == txt.PROOFREAD_THIS_DOCUMENT
    page._next_step.button.click()
    assert asked == ["proofread"]


@pytest.mark.gui
def test_the_next_step_strip_goes_while_a_run_holds_the_document(qtbot) -> None:
    """
    Given  a document that was read but not proofread, with the next-step strip shown
    When   a run locks the document, and then lets it go again
    Then   the strip is gone while it is locked (the run strip says what goes on) and back after
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.show_document(_document("a.png", "/tmp/a.png", raw_text="Hallo"), 1, 1)
    assert not page._next_step.isHidden()

    page.set_lock(txt.BEING_PROOFREAD)
    assert page._next_step.isHidden()

    page.set_lock(None)
    assert not page._next_step.isHidden()


@pytest.mark.gui
def test_the_next_step_button_sets_proofreading_up_when_it_cannot_run(qtbot) -> None:
    """
    Given  a next-step strip, and proofreading that is not set up
    When   the button is pressed
    Then   it reads "Set up proofreading…" and asks for the settings, not for a proofreading
    """
    page = CheckPage()
    qtbot.addWidget(page)
    seen: list[str] = []
    page.proofread_requested.connect(lambda: seen.append("proofread"))
    page.proofreading_setup_requested.connect(lambda: seen.append("setup"))
    page.show_document(_document("a.png", "/tmp/a.png", raw_text="Hallo"), 1, 1)

    page.set_proofreading_ready(False)
    assert page._next_step.button.text() == "Set up proofreading…"
    page._next_step.button.click()
    page.set_proofreading_ready(True)
    page._next_step.button.click()

    assert seen == ["setup", "proofread"]


@pytest.mark.gui
def test_the_next_step_strip_offers_to_change_how_pages_are_read(qtbot) -> None:
    """
    Given  the next-step strip of a document
    When   "Read badly? Change how pages are read…" is pressed
    Then   the page asks for the settings of the reading
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.show_document(_document("a.png", "/tmp/a.png", raw_text="Hallo"), 1, 1)

    with qtbot.waitSignal(page.read_settings_requested, timeout=1000):
        page._next_step._read_badly.click()


@pytest.mark.gui
def test_the_run_strip_shows_what_the_run_does_and_stops_it(qtbot) -> None:
    """
    Given  a page whose document is in a run
    When   the window gives the strip a state, and then none
    Then   the strip says what is going on with the time left and has Stop, which asks to stop;
           with no state it is gone
    """
    page = CheckPage()
    qtbot.addWidget(page)
    page.show_document(_document("a.png", "/tmp/a.png", raw_text="Hallo"), 1, 1)

    page.set_run(
        RunState("Proofreading with qwen3…", time_left="about 2 min left", stop_text="Stop")
    )

    assert not page._run_strip.isHidden()
    assert page._run_strip.text == "Proofreading with qwen3… · about 2 min left"
    with qtbot.waitSignal(page.stop_requested, timeout=1000):
        page._run_strip.stop_button.click()

    page.set_run(None)
    assert page._run_strip.isHidden()
