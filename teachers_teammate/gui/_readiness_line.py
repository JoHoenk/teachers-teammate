"""The line above Run that says whether a run can start, and what to press if it can't.

It replaces the row of coloured dots.  One place, next to the action it is about:

* a requirement is unmet → the sentence and a button for each way to fix it,
* a problem with the Results step → the same,
* otherwise where the teacher is on the way: documents to choose, rows to select, or ready.

The line never disables Run.  It may be a few hundred milliseconds old, so the check at Run
stays the authority (`MainWindow._check_requirements_then`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ._fix_targets import Fix, FixAction


class Tone(StrEnum):
    """How the line is coloured: what the teacher has to do about it."""

    WARNING = "warning"  # orange: something must be fixed first
    HINT = "hint"  # grey: the next obvious step
    READY = "ready"  # green: Run will start
    BUSY = "busy"  # blue: a download is running


_TONE_COLORS = {
    Tone.WARNING: "#e67e22",
    Tone.HINT: "#888",
    Tone.READY: "#27ae60",
    Tone.BUSY: "#3498db",
}


@dataclass(frozen=True)
class Readiness:
    """What the line shows."""

    text: str
    tone: Tone
    actions: tuple[FixAction, ...] = ()


def _documents(count: int) -> str:
    return f"{count} document{'s' if count != 1 else ''}"


def readiness(
    *,
    blocker: Fix | None,
    results: Fix | None,
    inputs_chosen: bool,
    files: int,
    selected: int,
    checking: bool,
) -> Readiness:
    """Return what the line says; the first matching row wins.

    Settings problems come before the states of the documents: they need a step outside the
    panel, while choosing documents is the obvious next step anyway.

    Args:
        blocker: The fix for the first unmet requirement of the last check, if any.
        results: The fix for a problem with the Results step (no folder, text into the input
            folder), if any.
        inputs_chosen: Whether a folder or files were chosen.
        files: How many documents the queue holds.
        selected: How many of them are selected.
        checking: Whether the first check has not answered yet.
    """
    if blocker is not None:
        return Readiness(f"⚠ {blocker.line}", Tone.WARNING, blocker.actions)
    if results is not None:
        return Readiness(f"⚠ {results.line}", Tone.WARNING, results.actions)
    if not inputs_chosen:
        return Readiness("Select documents to start", Tone.HINT)
    if files == 0:
        return Readiness("No documents found there", Tone.HINT)
    if selected == 0:
        return Readiness("Select the documents to run in the queue", Tone.HINT)
    if checking:
        return Readiness("Checking…", Tone.HINT)
    count = _documents(files) if selected == files else f"{selected} of {_documents(files)}"
    return Readiness(f"✓ Ready · {count}", Tone.READY)


class ReadinessLine(QWidget):
    """One label and up to two buttons; a download takes the label over while it runs."""

    #: A fix button was pressed.
    action_chosen = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setToolTip(
            "Whether a run can start with the current settings. It never blocks Run: "
            "the requirements are checked again when you press Run."
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.label, stretch=1)
        self._buttons: list[QPushButton] = []
        for _ in range(2):
            button = QPushButton()
            button.setVisible(False)
            button.clicked.connect(lambda _checked=False, b=button: self._on_clicked(b))
            row.addWidget(button)
            self._buttons.append(button)
        layout.addLayout(row)
        self._download_bar = QProgressBar()
        self._download_bar.setRange(0, 100)
        self._download_bar.setTextVisible(False)
        self._download_bar.setFixedHeight(8)
        self._download_bar.setVisible(False)
        layout.addWidget(self._download_bar)
        self._actions: dict[QPushButton, FixAction] = {}
        self._state: Readiness | None = None
        self._downloading = False

    @property
    def state(self) -> Readiness | None:
        """The last state handed to :meth:`show_state` (what the line returns to)."""
        return self._state

    @property
    def downloading(self) -> bool:
        """Whether a download currently takes the line over."""
        return self._downloading

    def show_state(self, state: Readiness) -> None:
        """Show *state*; while a download runs it is remembered and shown afterwards."""
        self._state = state
        if not self._downloading:
            self._render(state)

    def show_download(self, model: str) -> None:
        """Take the line over for a download of *model* (a blue label and a progress bar)."""
        self._downloading = True
        self._render(Readiness(f"Downloading {model}", Tone.BUSY))
        self._download_bar.setValue(0)
        self._download_bar.setToolTip("")
        self._download_bar.setVisible(True)

    def set_download_progress(self, percent: int, tooltip: str = "") -> None:
        """Move the bar of the running download."""
        self._download_bar.setValue(percent)
        self._download_bar.setToolTip(tooltip)

    def end_download(self) -> None:
        """Hand the line back; it shows the last state until a new check answers."""
        self._downloading = False
        self._download_bar.setVisible(False)
        if self._state is not None:
            self._render(self._state)

    def _render(self, state: Readiness) -> None:
        self.label.setText(state.text)
        self.label.setStyleSheet(f"color: {_TONE_COLORS[state.tone]}; font-size: 9pt;")
        self._actions = {}
        for button, action in zip(self._buttons, state.actions, strict=False):
            button.setText(action.label)
            button.setToolTip(action.tooltip)
            button.setVisible(True)
            self._actions[button] = action
        for button in self._buttons[len(state.actions) :]:
            button.setVisible(False)

    def _on_clicked(self, button: QPushButton) -> None:
        action = self._actions.get(button)
        if action is not None:
            self.action_chosen.emit(action)
