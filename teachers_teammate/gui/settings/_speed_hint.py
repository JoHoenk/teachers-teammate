"""The line on *AI models* that says how long a class set takes, read-only.

The speed test itself is on *This computer* (:mod:`._speed_panel`): the speed of a computer is a
fact about the computer.  The page where the models are chosen only needs the answer, so it shows
the class-set figure from the stored measurements and a button that opens the test.  It never
contacts the server and never starts a test.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ...application.speed_estimates import CLASS_SET_FILES, SpeedChoices, SpeedSummary
from ...domain.model_catalog import HardwareDetection
from .. import _user_text as txt
from .._hardware_profile_selector import HardwareProfileSelector

_HINT_STYLE = "color: #888; font-size: 11px;"


class _SpeedService(Protocol):
    """What the hint asks the application service."""

    def speed_choices(self, settings: Mapping[str, object]) -> SpeedChoices: ...

    def speed_summary(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cached_only: bool = True,
    ) -> SpeedSummary: ...


class SpeedHint(QWidget):
    """One line: how long a class set takes with the models chosen here, and where to measure."""

    #: The button was clicked: show the page with the speed test.
    open_requested = Signal()

    def __init__(
        self,
        *,
        app_service: _SpeedService,
        settings_provider: Callable[[], Mapping[str, object]],
        profile_selector: HardwareProfileSelector,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = app_service
        self._settings_provider = settings_provider
        self._selector = profile_selector
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("")
        self._label.setWordWrap(True)
        self._label.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self._label, stretch=1)
        self._button = QPushButton(txt.SPEED_HINT_MEASURE)
        self._button.setFlat(True)
        self._button.setToolTip(txt.SPEED_HINT_TOOLTIP)
        self._button.clicked.connect(self.open_requested.emit)
        layout.addWidget(self._button)

    def refresh(self) -> None:
        """Redraw from the stored measurements; hidden while no step uses Ollama.

        Call it once the hint sits in its page: showing a widget that has no parent yet would
        open it as a window of its own.
        """
        choices = self._service.speed_choices(self._settings_provider())
        self.setVisible(bool(choices.models()))
        if not choices.models():
            return
        summary = self._service.speed_summary(choices, hardware=self._selector.resolved())
        measured = summary.class_set is not None
        self._label.setText(
            txt.speed_class_set(CLASS_SET_FILES, summary.class_set.low_s, summary.class_set.high_s)
            if summary.class_set is not None
            else txt.SPEED_NEVER_MEASURED
        )
        self._button.setText(txt.SPEED_HINT_DETAILS if measured else txt.SPEED_HINT_MEASURE)
