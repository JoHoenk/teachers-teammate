"""GUI tests for ``gui/_dialogs.py``: which button a question reports, and what a picker gets."""
# pylint: disable=W0212,W0613  # protected-access — the tests reach into the box / unused-argument — pytest fixtures

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtWidgets import QAbstractButton, QCheckBox, QFileDialog, QMessageBox

from teachers_teammate.gui import _dialogs
from teachers_teammate.texts import dialogs as dlg
from teachers_teammate.texts._types import (
    Choice,
    FilePrompt,
    Notice,
    NoticeKind,
    Question,
    StandardButton,
)


def _click(monkeypatch, pick: Callable[[QMessageBox], QAbstractButton | None]) -> list[QMessageBox]:
    """Make every box ``exec`` click the button *pick* chooses (``None`` closes it unclicked)."""
    boxes: list[QMessageBox] = []

    def fake_exec(box: QMessageBox) -> int:
        boxes.append(box)
        button = pick(box)
        if button is not None:
            button.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return boxes


def _labelled(text: str) -> Callable[[QMessageBox], QAbstractButton | None]:
    """Return a picker for the button whose text is *text*."""
    return lambda box: next(b for b in box.buttons() if b.text() == text)


def _standard(button: QMessageBox.StandardButton) -> Callable[[QMessageBox], QAbstractButton]:
    """Return a picker for a platform button."""
    return lambda box: box.button(button)


@pytest.mark.gui
def test_a_destructive_button_goes_ahead_and_the_rejecting_one_calls_off(
    qtbot, monkeypatch
) -> None:
    """
    Given  the question asked when the window is closed during a run
    When   the teacher clicks "Stop and close", and in a second run "Keep running"
    Then   the first answer is accepted and the second is not
    """
    question = dlg.close_during_run()

    _click(monkeypatch, _labelled("Stop and close"))
    stop = _dialogs.choose(None, question)
    _click(monkeypatch, _labelled("Keep running"))
    keep = _dialogs.choose(None, question)

    assert (stop.accepted, keep.accepted) == (True, False)
    assert (stop.choice, keep.choice) == (question.choices[0], question.choices[1])


@pytest.mark.gui
def test_a_yes_no_question_reports_the_button_clicked(qtbot, monkeypatch) -> None:
    """
    Given  a yes/no question
    When   the teacher clicks Yes, and in a second run No
    Then   ``ask`` returns True, then False
    """
    question = dlg.clear_all_runs("essay.png")

    _click(monkeypatch, _standard(QMessageBox.StandardButton.Yes))
    yes = _dialogs.ask(None, question)
    _click(monkeypatch, _standard(QMessageBox.StandardButton.No))
    no = _dialogs.ask(None, question)

    assert (yes, no) == (True, False)


@pytest.mark.gui
def test_a_question_closed_with_the_window_button_is_not_confirmed(qtbot, monkeypatch) -> None:
    """
    Given  a question whose box closes without any button clicked
    When   it is asked
    Then   no choice is reported and ``ask`` returns False
    """
    _click(monkeypatch, lambda _box: None)

    answer = _dialogs.choose(None, dlg.clear_all_runs("essay.png"))

    assert answer.choice is None
    assert not answer.accepted


@pytest.mark.gui
def test_a_hand_built_no_button_does_not_confirm(qtbot, monkeypatch) -> None:
    """
    Given  a question whose No button was built without a role
    When   the teacher clicks it
    Then   ``ask`` returns False
    """
    question = Question(
        "Delete?",
        "Really?",
        (Choice(standard=StandardButton.YES), Choice(standard=StandardButton.NO)),
    )
    _click(monkeypatch, _standard(QMessageBox.StandardButton.No))

    assert _dialogs.ask(None, question) is False


@pytest.mark.gui
def test_the_choice_the_teacher_clicked_is_found_by_id_among_several(qtbot, monkeypatch) -> None:
    """
    Given  the restore-defaults question with a page, an everything and a cancel button
    When   the teacher clicks "All settings"
    Then   the answer names it by id and is accepted
    """
    _click(monkeypatch, _labelled("All settings"))

    answer = _dialogs.choose(None, dlg.restore_settings(applies_immediately=False))

    assert answer.choice is not None
    assert (answer.choice.id, answer.accepted) == (dlg.RESTORE_EVERYTHING, True)


@pytest.mark.gui
def test_the_page_button_is_disabled_when_the_page_applies_immediately(qtbot, monkeypatch) -> None:
    """
    Given  the restore-defaults question for a page that has nothing to restore
    When   the box is shown
    Then   "This page" is disabled and the page's note is the informative text
    """
    boxes = _click(monkeypatch, _labelled("All settings"))

    _dialogs.choose(None, dlg.restore_settings(applies_immediately=True))

    page = next(b for b in boxes[0].buttons() if b.text() == "This page")
    assert not page.isEnabled()
    assert "Add-ons has no settings" in boxes[0].informativeText()


@pytest.mark.gui
def test_the_tick_box_state_is_reported(qtbot, monkeypatch) -> None:
    """
    Given  the question before documents leave the computer, with its tick box
    When   the teacher ticks it and continues
    Then   the answer is accepted and reports the box as checked
    """

    def tick_and_continue(box: QMessageBox) -> QAbstractButton:
        checkbox = box.checkBox()
        assert isinstance(checkbox, QCheckBox)
        checkbox.setChecked(True)
        return next(b for b in box.buttons() if b.text() == "Continue")

    _click(monkeypatch, tick_and_continue)

    answer = _dialogs.choose(None, dlg.documents_leave_computer("Level", [("Step", "online")], []))

    assert (answer.accepted, answer.checked) == (True, True)


@pytest.mark.gui
def test_a_question_without_a_tick_box_reports_unchecked(qtbot, monkeypatch) -> None:
    """
    Given  a question that has no tick box
    When   the teacher answers it
    Then   the answer reports ``checked`` as False
    """
    _click(monkeypatch, _standard(QMessageBox.StandardButton.Yes))

    assert _dialogs.choose(None, dlg.clear_all_runs("x")).checked is False


@pytest.mark.gui
def test_the_default_button_is_the_one_the_question_marks(qtbot, monkeypatch) -> None:
    """
    Given  the close-during-run question, whose default is "Keep running"
    When   the box is shown
    Then   Enter would press "Keep running"
    """
    boxes = _click(monkeypatch, _labelled("Keep running"))

    _dialogs.choose(None, dlg.close_during_run())

    assert boxes[0].defaultButton().text() == "Keep running"


@pytest.mark.gui
def test_a_notice_is_shown_with_its_title_text_and_icon(qtbot, monkeypatch) -> None:
    """
    Given  a warning notice
    When   it is shown
    Then   the box carries its title, body and the warning icon
    """
    boxes = _click(monkeypatch, lambda _box: None)

    _dialogs.notify(None, Notice("Save failed", "Disk full", NoticeKind.WARNING))

    box = boxes[0]
    assert (box.windowTitle(), box.text(), box.icon()) == (
        "Save failed",
        "Disk full",
        QMessageBox.Icon.Warning,
    )


@pytest.mark.gui
def test_the_pickers_open_at_the_start_of_their_prompt(qtbot, monkeypatch) -> None:
    """
    Given  a folder, a file, a files and a save prompt that each carry a start
    When   the teacher picks, and the system dialog answers with a path
    Then   each dialog was opened with the prompt's title, start and filter, and returns the path
    """
    calls: dict[str, tuple] = {}

    def folder(*args):
        calls["folder"] = args
        return "/chosen"

    def one(*args):
        calls["file"] = args
        return "/chosen/a.png", ""

    def several(*args):
        calls["files"] = args
        return ["/chosen/a.png", "/chosen/b.png"], ""

    def save(*args):
        calls["save"] = args
        return "/chosen/out.docx", ""

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", folder)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", one)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", several)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", save)
    prompt = FilePrompt("Title", name_filter="Filter", start="/start")

    assert _dialogs.pick_folder(None, prompt) == Path("/chosen")
    assert _dialogs.pick_file(None, prompt) == Path("/chosen/a.png")
    assert _dialogs.pick_files(None, prompt) == [Path("/chosen/a.png"), Path("/chosen/b.png")]
    assert _dialogs.save_file(None, prompt) == Path("/chosen/out.docx")
    assert calls["folder"] == (None, "Title", "/start")
    assert calls["file"] == calls["files"] == calls["save"] == (None, "Title", "/start", "Filter")


@pytest.mark.gui
def test_a_cancelled_picker_returns_nothing(qtbot, monkeypatch) -> None:
    """
    Given  system dialogs the teacher cancels
    When   each picker is asked
    Then   they return None or an empty list
    """
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a: "")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a: ("", ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_a: ([], ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a: ("", ""))
    prompt = FilePrompt("Title")

    assert _dialogs.pick_folder(None, prompt) is None
    assert _dialogs.pick_file(None, prompt) is None
    assert _dialogs.pick_files(None, prompt) == []
    assert _dialogs.save_file(None, prompt) is None
