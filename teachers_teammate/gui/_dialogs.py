"""The one place the GUI shows a message box or a file picker.

Every window asks this module instead of touching ``QMessageBox`` or ``QFileDialog``: the
sentences come from ``teachers_teammate/texts``, the widgets are built here.  A test
therefore patches this module rather than each window, and no window can invent a title of
its own.

:func:`answering` puts an answerer in front of all of them: while it is open, no box and no
picker is shown, and the answerer says what the teacher would have done.  The app walkthrough
runs the real window that way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class Asked:
    """One box or picker the GUI would have shown."""

    call: str
    """``"notify"``, ``"choose"``, ``"pick_folder"``, ``"pick_file"``, ``"pick_files"`` or
    ``"save_file"``."""
    item: Notice | Question | FilePrompt
    """What was asked: the notice, the question or the picker's prompt."""


#: What the walkthrough answers: an :class:`~teachers_teammate.texts._types.Answer` for a
#: question, a path for a picker (a list for ``pick_files``), ``None`` for ``notify``.
Answerer = Callable[[Asked], Any]

#: The answerers that are open, innermost last; the innermost one answers.
_ANSWERING: list[tuple[Answerer, list[Asked]]] = []

#: What an unanswered call gets back: a real answer is ``None`` for some calls.
_UNANSWERED = object()


@contextmanager
def answering(answerer: Answerer) -> Iterator[list[Asked]]:
    """While open, every box and picker calls *answerer* instead of showing; yields the record.

    The record holds one :class:`Asked` per call, in the order they were made.  Answerers
    nest: the innermost one answers and records, and every one of them is put back on the way
    out, also when the block raises.

    Args:
        answerer: Called with each :class:`Asked`; its answer is used as the teacher's.

    Yields:
        The calls that were answered while the block ran.
    """
    asked: list[Asked] = []
    _ANSWERING.append((answerer, asked))
    try:
        yield asked
    finally:
        _ANSWERING.remove((answerer, asked))


def _answer(call: str, item: Notice | Question | FilePrompt) -> Any:
    """Return what the innermost answerer says for *call*, or :data:`_UNANSWERED`."""
    if not _ANSWERING:
        return _UNANSWERED
    answerer, asked = _ANSWERING[-1]
    question = Asked(call, item)
    asked.append(question)
    return answerer(question)


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
    if _answer("notify", notice) is not _UNANSWERED:
        return
    box = QMessageBox(parent)
    box.setIcon(_notice_icon(notice.kind))
    box.setWindowTitle(notice.title)
    box.setText(notice.body)
    box.exec()


def choose(parent: QWidget | None, question: Question) -> Answer:
    """Show *question*; return the button the teacher clicked and the tick box state."""
    answer = _answer("choose", question)
    if answer is not _UNANSWERED:
        return answer if isinstance(answer, Answer) else Answer()
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
    answer = _answer("pick_folder", prompt)
    if answer is not _UNANSWERED:
        return None if answer is None else Path(answer)
    chosen = QFileDialog.getExistingDirectory(parent, prompt.title, prompt.start)
    return Path(chosen) if chosen else None


def pick_file(parent: QWidget | None, prompt: FilePrompt) -> Path | None:
    """Ask for one file; return it, or ``None`` when the teacher cancelled."""
    answer = _answer("pick_file", prompt)
    if answer is not _UNANSWERED:
        return None if answer is None else Path(answer)
    chosen, _ = QFileDialog.getOpenFileName(parent, prompt.title, prompt.start, prompt.name_filter)
    return Path(chosen) if chosen else None


def pick_files(parent: QWidget | None, prompt: FilePrompt) -> list[Path]:
    """Ask for several files; return them, or an empty list when the teacher cancelled."""
    answer = _answer("pick_files", prompt)
    if answer is not _UNANSWERED:
        return [Path(path) for path in (answer or [])]
    chosen, _ = QFileDialog.getOpenFileNames(parent, prompt.title, prompt.start, prompt.name_filter)
    return [Path(path) for path in chosen]


def save_file(parent: QWidget | None, prompt: FilePrompt) -> Path | None:
    """Ask where to save one file; return the path, or ``None`` when the teacher cancelled.

    ``prompt.start`` is the path the dialog proposes.
    """
    answer = _answer("save_file", prompt)
    if answer is not _UNANSWERED:
        return None if answer is None else Path(answer)
    chosen, _ = QFileDialog.getSaveFileName(parent, prompt.title, prompt.start, prompt.name_filter)
    return Path(chosen) if chosen else None
