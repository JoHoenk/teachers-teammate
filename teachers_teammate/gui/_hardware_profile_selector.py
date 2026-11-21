"""Shared "My computer" hardware-profile selector widget.

The widget presents the ``hardware_profile`` setting ("My computer") together
with a plain-language status line that explains *why* a profile was chosen.
All discovery runs in a background task (:class:`~._background.LatestTask`), so
opening a dialog never blocks the UI, and closing it while detection is still
running is harmless.  The wording lives in :mod:`._model_wording`.

The status line also names the memory that is free right now.  That figure is
not part of the detection — it changes all the time and no suggestion depends on
it — so it is read again whenever the line is drawn: when the page is shown, on
"Detect again", and every :data:`_FREE_MEMORY_INTERVAL_MS` while the page is
visible.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..application.composition import AppServices
from ..config import DEFAULTS
from ..domain.model_catalog import PROFILE_SETTINGS, HardwareDetection
from ._background import LatestTask
from ._model_wording import (
    CHECK_COMPUTER_FAILED,
    CHECKING_COMPUTER,
    NO_SUGGESTION_FOR_COMPUTER,
    READ_ONLY_PROFILE_TOOLTIP,
    detection_status_text,
    profile_text,
    read_only_profile_text,
)

_logger = logging.getLogger(__name__)

_STATUS_STYLE = "color: #888; font-size: 11px;"
#: How often the free memory is read again while the page is visible.
_FREE_MEMORY_INTERVAL_MS = 10_000


class HardwareProfileSelector(QWidget):
    """ "My computer" picker with a plain-language detection status line.

    Editable variant: a combo with the four ``PROFILE_SETTINGS`` items plus a
    status label and a "Detect again" link.  ``read_only=True`` shows only
    "Models are suggested for: {profile text}" with a tooltip pointing at the two
    places where the value can be changed.
    """

    setting_changed = Signal(str)
    resolved_changed = Signal(object)  # HardwareDetection or None

    def __init__(
        self,
        *,
        app_service: AppServices,
        setting: str = DEFAULTS["hardware_profile"],
        ollama_url: str = DEFAULTS["ollama_url"],
        read_only: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_service = app_service
        self._ollama_url = ollama_url
        self._read_only = read_only
        self._task = LatestTask(self)
        self._memory_task = LatestTask(self)
        self._detection: HardwareDetection | None = None
        self._available_memory_gb: float | None = None
        self._setting: str = setting if setting in PROFILE_SETTINGS else "auto"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._combo: QComboBox | None = None
        if read_only:
            self._status_lbl = QLabel(CHECKING_COMPUTER)
            self._status_lbl.setWordWrap(True)
            self._status_lbl.setStyleSheet(_STATUS_STYLE)
            self._status_lbl.setToolTip(READ_ONLY_PROFILE_TOOLTIP)
            layout.addWidget(self._status_lbl)
        else:
            combo_row = QWidget()
            combo_layout = QHBoxLayout(combo_row)
            combo_layout.setContentsMargins(0, 0, 0, 0)
            combo_layout.addWidget(QLabel("My computer:"))
            self._combo = QComboBox()
            for value in PROFILE_SETTINGS:
                self._combo.addItem(profile_text(value), userData=value)
            idx = self._combo.findData(self._setting)
            self._combo.setCurrentIndex(max(idx, 0))
            self._combo.currentIndexChanged.connect(self._on_combo_changed)
            combo_layout.addWidget(self._combo, stretch=1)
            layout.addWidget(combo_row)

            status_row = QWidget()
            status_layout = QHBoxLayout(status_row)
            status_layout.setContentsMargins(0, 0, 0, 0)
            self._status_lbl = QLabel(CHECKING_COMPUTER)
            self._status_lbl.setWordWrap(True)
            self._status_lbl.setStyleSheet(_STATUS_STYLE)
            status_layout.addWidget(self._status_lbl, stretch=1)
            self._detect_again_lbl = QLabel('<a href="detect">Detect again</a>')
            self._detect_again_lbl.linkActivated.connect(self._on_detect_again)
            status_layout.addWidget(self._detect_again_lbl)
            layout.addWidget(status_row)

        # Only the editable line names the free memory, so only it needs the timer.
        self._memory_timer = QTimer(self)
        self._memory_timer.setInterval(_FREE_MEMORY_INTERVAL_MS)
        self._memory_timer.timeout.connect(self._read_available_memory)

        self._start_resolve(refresh=False)

    # ── public API ─────────────────────────────────────────────────────────

    def set_setting(self, setting: str) -> None:
        """Set the current setting without emitting :attr:`setting_changed`."""
        self._setting = setting if setting in PROFILE_SETTINGS else "auto"
        if self._combo is not None:
            idx = self._combo.findData(self._setting)
            if idx >= 0:
                self._combo.blockSignals(True)
                self._combo.setCurrentIndex(idx)
                self._combo.blockSignals(False)
        self._detection = None
        self._start_resolve(refresh=False)

    def setting(self) -> str:
        """Return the current setting value ("auto" / "cpu" / "8gb" / "16gb")."""
        if self._combo is not None:
            data = self._combo.currentData()
            return str(data) if data else "auto"
        return self._setting

    def resolved(self) -> HardwareDetection | None:
        """Return the latest resolved detection, or ``None`` while unresolved."""
        return self._detection

    def refresh_status(self) -> None:
        """Resolve again from the (possibly re-probed) hardware; never probes itself."""
        self._start_resolve(refresh=False)

    def set_ollama_url(self, url: str) -> None:
        """Update the Ollama server URL and re-resolve the profile for it."""
        new_url = url or DEFAULTS["ollama_url"]
        if new_url == self._ollama_url:
            return
        self._ollama_url = new_url
        if self.setting() == "auto":
            # A concrete choice does not depend on the server.
            self._start_resolve(refresh=False)

    # ── the free memory ────────────────────────────────────────────────────

    def showEvent(self, event) -> None:  # Qt's name
        super().showEvent(event)
        if self._read_only:
            return
        self._read_available_memory()
        self._memory_timer.start()

    def hideEvent(self, event) -> None:  # Qt's name
        super().hideEvent(event)
        self._memory_timer.stop()

    def _read_available_memory(self) -> None:
        """Ask for the memory free right now and redraw the line with it.

        Only worth asking when the line names the computer's memory at all: a remote server's
        free memory is not this computer's, and a computer whose memory is unknown has no
        figure to add to.
        """
        detection = self._detection
        if detection is None or detection.system_memory_gb is None or not self.isVisible():
            return
        self._memory_task.submit(
            self._app_service.recommendations.available_memory_gb, self._on_memory_read
        )

    def _on_memory_read(self, result: object, error: BaseException | None) -> None:
        if error is not None:
            _logger.debug("Reading the free memory failed", exc_info=error)
            return
        self._available_memory_gb = result if isinstance(result, float) else None
        self._draw_status()

    # ── internal ───────────────────────────────────────────────────────────

    def _on_combo_changed(self, index: int) -> None:
        data = self._combo.itemData(index) if self._combo is not None else None
        if data is None:
            return
        self._setting = str(data)
        self.setting_changed.emit(self._setting)
        self._detection = None
        self._start_resolve(refresh=False)

    def _on_detect_again(self, _link: str = "") -> None:
        self._start_resolve(refresh=True)

    def _start_resolve(self, *, refresh: bool) -> None:
        setting = self.setting()
        url = self._ollama_url
        service = self._app_service
        self._status_lbl.setText(CHECKING_COMPUTER)

        def resolve() -> HardwareDetection:
            if setting != "auto":
                # Through the service: a chosen profile still carries this computer's memory.
                return service.recommendations.resolve_hardware_profile(setting, url)
            return service.recommendations.detect_hardware(url, refresh=refresh)

        self._task.submit(resolve, self._on_resolved)

    def _on_resolved(self, result: object, error: BaseException | None) -> None:
        if error is not None or not isinstance(result, HardwareDetection):
            # Detection must never get in the way; the app falls back to 8 GB hints.  A
            # failure is said and logged, so it is not mistaken for "nothing to suggest".
            if error is not None:
                _logger.warning("Checking this computer failed", exc_info=error)
                self._status_lbl.setText(CHECK_COMPUTER_FAILED)
            else:
                self._status_lbl.setText(NO_SUGGESTION_FOR_COMPUTER)
            self.resolved_changed.emit(None)
            return
        self._detection = result
        self._available_memory_gb = None
        self._draw_status()
        self._read_available_memory()
        self.resolved_changed.emit(result)

    def _draw_status(self) -> None:
        """Draw the status line from the detection and the free memory read last."""
        detection = self._detection
        if detection is None:
            return
        if self._read_only:
            self._status_lbl.setText(read_only_profile_text(detection.profile))
            return
        self._status_lbl.setText(
            detection_status_text(detection, available_memory_gb=self._available_memory_gb)
        )
