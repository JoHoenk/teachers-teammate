"""The strips above the Check tab's text that tell the teacher what to do or what is going on.

Two small widgets, each hidden unless it has something to say:

* :class:`NextStepStrip` — over a transcription that has not been proofread: what to do after
  reading it, with *Proofread this document* as the primary button (or *Set up proofreading…*
  where nothing can proofread yet) and the way to change how the pages are read.
* :class:`RunStrip` — while the open document is part of a run: that something is happening,
  what, how long it takes, and *Stop*.  The window feeds it from the signals of the run it
  already follows; the strip keeps no state of the run.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QToolButton,
    QWidget,
)

from ...texts import check as check_text
from .. import _user_text as txt

#: The accent of the primary button: the same green as *Run* in the Documents tab.
PRIMARY_STYLE = (
    "QPushButton { background: #27ae60; color: white; font-weight: bold; "
    "padding: 4px 12px; border-radius: 4px; } "
    "QPushButton:hover { background: #219a52; } "
    "QPushButton:disabled { background: #95a5a6; }"
)


class NextStepStrip(QWidget):
    """What to do after reading: proofread, or set proofreading up first."""

    proofread_requested = Signal()
    set_up_requested = Signal()
    read_settings_requested = Signal()
    """The teacher wants to change how the pages are read."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ready = True
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)

        self._label = QLabel(check_text.NEXT_STEP)
        self._label.setWordWrap(True)
        layout.addWidget(self._label, 1)

        self._read_badly = QToolButton()
        self._read_badly.setText(check_text.NEXT_STEP_READ_BADLY)
        self._read_badly.setToolTip(check_text.NEXT_STEP_READ_BADLY_TIP)
        self._read_badly.setAutoRaise(True)
        self._read_badly.setStyleSheet("QToolButton { text-decoration: underline; }")
        self._read_badly.clicked.connect(self.read_settings_requested)
        layout.addWidget(self._read_badly)

        self.button = QPushButton()
        self.button.setStyleSheet(PRIMARY_STYLE)
        self.button.clicked.connect(self._on_button)
        layout.addWidget(self.button)

        self.set_ready(True)
        self.setVisible(False)

    def set_ready(self, ready: bool) -> None:
        """Say whether proofreading is set up: the button proofreads, or opens the settings."""
        self._ready = ready
        self.button.setText(txt.PROOFREAD_THIS_DOCUMENT if ready else check_text.NEXT_STEP_SET_UP)
        self.button.setToolTip("" if ready else check_text.NEXT_STEP_SET_UP_TIP)

    @property
    def ready(self) -> bool:
        """Whether the button proofreads (otherwise it sets proofreading up)."""
        return self._ready

    def _on_button(self) -> None:
        (self.proofread_requested if self._ready else self.set_up_requested).emit()


@dataclass(frozen=True)
class RunState:
    """What the run strip says about the run the open document is in."""

    text: str
    """What is being done, or why the document waits."""
    busy: bool = True
    """Whether the document itself is being worked on (the bar moves) or still waits."""
    time_left: str = ""
    """The remaining time as the pace estimate gives it, or ``""`` while nothing is known."""
    time_left_tip: str = ""
    stop_text: str = ""
    stop_enabled: bool = True


class RunStrip(QWidget):
    """An indeterminate bar, what is going on, the time left and *Stop*."""

    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # the run reports files, not steps: the bar has no end
        self._bar.setTextVisible(False)
        self._bar.setFixedWidth(120)
        self._bar.setFixedHeight(10)
        layout.addWidget(self._bar)

        self._label = QLabel()
        layout.addWidget(self._label, 1)

        self._stop = QPushButton()
        self._stop.clicked.connect(self.stop_requested)
        layout.addWidget(self._stop)
        self.setVisible(False)

    def show_state(self, state: RunState | None) -> None:
        """Show *state*, or take the strip away when the open document is not in a run."""
        if state is None:
            self.setVisible(False)
            return
        text = state.text
        if state.time_left:
            text += check_text.RUN_TIME_LEFT_SEPARATOR + state.time_left
        self._label.setText(text)
        self._label.setToolTip(state.time_left_tip)
        self._bar.setVisible(state.busy)
        self._stop.setText(state.stop_text)
        self._stop.setEnabled(state.stop_enabled)
        self._stop.setVisible(bool(state.stop_text))
        self.setVisible(True)

    @property
    def text(self) -> str:
        """What the strip says."""
        return self._label.text()

    @property
    def stop_button(self) -> QPushButton:
        """The strip's *Stop* button."""
        return self._stop
