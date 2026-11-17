"""The one place the GUI shows a message box or a file picker.

Every window asks this module instead of touching ``QMessageBox`` or ``QFileDialog``: the
sentences come from ``teachers_teammate/texts``, the widgets are built here.  A test
therefore patches this module rather than each window, and no window can invent a title of
its own.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QFileDialog, QMessageBox, QWidget

from ..texts._types import (
    Answer,
    Choice,
    ChoiceRole,
    FilePrompt,
    Notice,
    NoticeKind,
    Question,
    QuestionKind,
    StandardButton,
)


def _notice_icon(kind: NoticeKind) -> QMessageBox.Icon:
    """Return the icon a notice of *kind* is shown with."""
    return {
        NoticeKind.INFORMATION: QMessageBox.Icon.Information,
        NoticeKind.WARNING: QMessageBox.Icon.Warning,
    }[kind]


def _question_icon(kind: QuestionKind) -> QMessageBox.Icon:
    """Return the icon a question of *kind* is shown with."""
    return {
        QuestionKind.QUESTION: QMessageBox.Icon.Question,
        QuestionKind.WARNING: QMessageBox.Icon.Warning,
    }[kind]


def _button_role(role: ChoiceRole) -> QMessageBox.ButtonRole:
    """Return the Qt role of a choice, so the platform places the button."""
    return {
        ChoiceRole.ACCEPT: QMessageBox.ButtonRole.AcceptRole,
        ChoiceRole.REJECT: QMessageBox.ButtonRole.RejectRole,
        ChoiceRole.DESTRUCTIVE: QMessageBox.ButtonRole.DestructiveRole,
    }[role]


def _standard_button(button: StandardButton) -> QMessageBox.StandardButton:
    """Return the platform button a choice names, whose text Qt translates."""
    return {
        StandardButton.YES: QMessageBox.StandardButton.Yes,
        StandardButton.NO: QMessageBox.StandardButton.No,
        StandardButton.CANCEL: QMessageBox.StandardButton.Cancel,
        StandardButton.OK: QMessageBox.StandardButton.Ok,
    }[button]


def notify(parent: QWidget | None, notice: Notice) -> None:
    """Show *notice* and wait for the teacher to close it."""
    box = QMessageBox(parent)
    box.setIcon(_notice_icon(notice.kind))
    box.setWindowTitle(notice.title)
    box.setText(notice.body)
    box.exec()


def choose(parent: QWidget | None, question: Question) -> Answer:
    """Show *question*; return the button the teacher clicked and the tick box state."""
    box = QMessageBox(parent)
    box.setIcon(_question_icon(question.kind))
    box.setWindowTitle(question.title)
    box.setText(question.body)
    if question.informative:
        box.setInformativeText(question.informative)
    checkbox = None
    if question.checkbox:
        checkbox = QCheckBox(question.checkbox)
        box.setCheckBox(checkbox)
    buttons: dict[Choice, object] = {}
    default = None
    for choice in question.choices:
        if choice.standard is not None:
            button = box.addButton(_standard_button(choice.standard))
        else:
            button = box.addButton(choice.label, _button_role(choice.role))
        button.setEnabled(choice.enabled)
        buttons[choice] = button
        if choice.default:
            default = button
    if default is not None:
        box.setDefaultButton(default)
    box.exec()
    clicked = box.clickedButton()
    chosen = next((choice for choice, button in buttons.items() if button is clicked), None)
    return Answer(chosen, bool(checkbox is not None and checkbox.isChecked()))


def ask(parent: QWidget | None, question: Question) -> bool:
    """Show *question*; return whether the teacher went ahead rather than cancelling."""
    return choose(parent, question).accepted


def pick_folder(parent: QWidget | None, prompt: FilePrompt) -> Path | None:
    """Ask for a folder; return it, or ``None`` when the teacher cancelled."""
    chosen = QFileDialog.getExistingDirectory(parent, prompt.title, prompt.start)
    return Path(chosen) if chosen else None


def pick_file(parent: QWidget | None, prompt: FilePrompt) -> Path | None:
    """Ask for one file; return it, or ``None`` when the teacher cancelled."""
    chosen, _ = QFileDialog.getOpenFileName(parent, prompt.title, prompt.start, prompt.name_filter)
    return Path(chosen) if chosen else None


def pick_files(parent: QWidget | None, prompt: FilePrompt) -> list[Path]:
    """Ask for several files; return them, or an empty list when the teacher cancelled."""
    chosen, _ = QFileDialog.getOpenFileNames(parent, prompt.title, prompt.start, prompt.name_filter)
    return [Path(path) for path in chosen]


def save_file(parent: QWidget | None, prompt: FilePrompt) -> Path | None:
    """Ask where to save one file; return the path, or ``None`` when the teacher cancelled.

    ``prompt.start`` is the path the dialog proposes.
    """
    chosen, _ = QFileDialog.getSaveFileName(parent, prompt.title, prompt.start, prompt.name_filter)
    return Path(chosen) if chosen else None
