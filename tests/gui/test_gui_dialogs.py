"""GUI tests for ``gui/_dialogs.py``: which button a question reports, and what a picker gets."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtWidgets import QAbstractButton, QCheckBox, QFileDialog, QMessageBox

from teachers_teammate.gui import _dialogs
from teachers_teammate.texts import dialogs as dlg
from teachers_teammate.texts._types import (
    Answer,
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
    question = dlg.search_network()

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

    answer = _dialogs.choose(None, dlg.search_network())

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

    assert _dialogs.choose(None, dlg.search_network()).checked is False


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


def _nothing_shown(monkeypatch) -> None:
    """Fail the test if any box or picker is really shown."""

    def shown(*_args, **_kwargs):
        raise AssertionError("a dialog was shown while an answerer was open")

    monkeypatch.setattr(QMessageBox, "exec", shown)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", shown)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", shown)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", shown)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", shown)


def _answering_everything(asked: _dialogs.Asked) -> object:
    """Answer each call kind the way the walkthrough does: a choice, a path, nothing."""
    return {
        "notify": None,
        "choose": Answer(dlg.search_network().choices[0], checked=True),
        "pick_folder": Path("/chosen"),
        "pick_file": Path("/chosen/a.png"),
        "pick_files": [Path("/chosen/a.png"), Path("/chosen/b.png")],
        "save_file": Path("/chosen/out.docx"),
    }[asked.call]


@pytest.mark.gui
def test_an_answerer_answers_every_box_and_picker_instead_of_showing_it(qtbot, monkeypatch) -> None:
    """
    Given  an answerer that answers every kind of call
    When   a notice, a question, the two pickers and the save prompt are asked
    Then   nothing is shown, each answer comes back, and the record names every call once
    """
    _nothing_shown(monkeypatch)
    prompt = FilePrompt("Title")

    with _dialogs.answering(_answering_everything) as asked:
        _dialogs.notify(None, Notice("Save failed", "Disk full", NoticeKind.WARNING))
        answer = _dialogs.choose(None, dlg.search_network())
        accepted = _dialogs.ask(None, dlg.search_network())
        folder = _dialogs.pick_folder(None, prompt)
        one = _dialogs.pick_file(None, prompt)
        several = _dialogs.pick_files(None, prompt)
        saved = _dialogs.save_file(None, prompt)

    assert (answer.checked, accepted) == (True, True)
    assert (folder, one, several, saved) == (
        Path("/chosen"),
        Path("/chosen/a.png"),
        [Path("/chosen/a.png"), Path("/chosen/b.png")],
        Path("/chosen/out.docx"),
    )
    # ``ask`` goes through ``choose``: the teacher answered one question, so it is recorded once.
    assert [call.call for call in asked] == [
        "notify",
        "choose",
        "choose",
        "pick_folder",
        "pick_file",
        "pick_files",
        "save_file",
    ]


@pytest.mark.gui
def test_the_innermost_answerer_answers_and_the_outer_one_still_counts(qtbot, monkeypatch) -> None:
    """
    Given  an answerer that says No, and an inner one that says Yes
    When   a question is asked inside the inner block and again after it
    Then   the inner answer wins while it is open, and the outer one takes over afterwards
    """
    yes = Answer(dlg.search_network().choices[0])
    no = Answer(dlg.search_network().choices[1])

    with _dialogs.answering(lambda _asked: no) as outer:
        with _dialogs.answering(lambda _asked: yes) as inner:
            inside = _dialogs.choose(None, dlg.search_network())
        after = _dialogs.choose(None, dlg.search_network())

    assert (inside.accepted, after.accepted) == (True, False)
    assert len(inner) == 1
    assert len(outer) == 1  # the outer one records only what it answered itself


@pytest.mark.gui
def test_the_answerer_is_put_back_when_the_block_raises(qtbot, monkeypatch) -> None:
    """
    Given  an answerer that is open while the code it wraps fails
    When   the exception leaves the block
    Then   the next box is shown again, by the real dialog
    """
    shown = _click(monkeypatch, lambda _box: None)

    with pytest.raises(RuntimeError):
        with _dialogs.answering(_answering_everything):
            raise RuntimeError("the walk stopped here")

    _dialogs.notify(None, Notice("Save failed", "Disk full", NoticeKind.WARNING))

    assert len(shown) == 1


@pytest.mark.gui
def test_an_answerer_that_declines_and_cancels_says_so(qtbot, monkeypatch) -> None:
    """
    Given  an answerer that answers nothing and picks nothing, as a declined box does
    When   a question and the pickers are asked
    Then   the question comes back declined and unchecked, and the pickers come back empty
    """
    _nothing_shown(monkeypatch)
    prompt = FilePrompt("Title")

    with _dialogs.answering(lambda _asked: None):
        answer = _dialogs.choose(None, dlg.search_network())
        folder = _dialogs.pick_folder(None, prompt)
        several = _dialogs.pick_files(None, prompt)

    assert (answer.choice, answer.checked, answer.accepted) == (None, False, False)
    assert folder is None
    assert several == []


@pytest.mark.gui
def test_the_same_answerer_can_be_open_twice(qtbot, monkeypatch) -> None:
    """
    Given  one answerer opened inside itself, both records still empty and so equal
    When   the inner block ends and a question is asked
    Then   the outer record gets it, the stack is clean after both blocks, and the real box is
           back
    """
    yes = Answer(dlg.search_network().choices[0])

    def answerer(_asked: _dialogs.Asked) -> Answer:
        return yes

    with _dialogs.answering(answerer) as outer:
        with _dialogs.answering(answerer):
            pass
        _dialogs.choose(None, dlg.search_network())

    assert len(outer) == 1
    assert _dialogs._ANSWERING == []


@pytest.mark.gui
@pytest.mark.parametrize(
    "wrong", [True, "Yes", Choice(label="Elsewhere"), Answer(Choice(label="Elsewhere"))]
)
def test_an_answerer_must_answer_a_question_with_an_answer(qtbot, wrong: object) -> None:
    """
    Given  an answerer that returns something a box could not give: a bool, a string, a button
           that is not the question's
    When   the question is asked
    Then   the script is told, instead of the answer counting as the teacher declining
    """
    with _dialogs.answering(lambda _asked: wrong):
        with pytest.raises((TypeError, ValueError)):
            _dialogs.choose(None, dlg.search_network())
