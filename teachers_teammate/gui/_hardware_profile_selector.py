"""Shared "My computer" hardware-profile selector widget.

The widget presents the ``hardware_profile`` setting ("My computer") together
with a plain-language status line that explains *why* a profile was chosen.
All discovery runs in a background task (:class:`~._background.LatestTask`), so
opening a dialog never blocks the UI, and closing it while detection is still
running is harmless.  The wording lives in :mod:`._model_wording`.
"""

from __future__ import annotations

from typing import Protocol, cast

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..config import DEFAULTS
from ..domain.model_catalog import (
    PROFILE_SETTINGS,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
)
from ._background import LatestTask
from ._model_wording import (
    CHECKING_COMPUTER,
    NO_SUGGESTION_FOR_COMPUTER,
    PROFILE_TEXT,
    READ_ONLY_PROFILE_TOOLTIP,
    auto_profile_text,
    detection_status_text,
    profile_text,
    read_only_profile_text,
)

_STATUS_STYLE = "color: #888; font-size: 11px;"


class _ProfileService(Protocol):
    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection: ...


class HardwareProfileSelector(QWidget):
    """ "My computer" picker with a plain-language detection status line.

    Editable variant: a combo with the four ``PROFILE_SETTINGS`` items plus a
    status label and a "Detect again" link.  ``read_only=True`` shows only
    "Suggestions are for: {profile text}" with a tooltip pointing at the two
    places where the value can be changed.
    """

    setting_changed = Signal(str)
    resolved_changed = Signal(object)  # HardwareDetection or None

    def __init__(
        self,
        *,
        app_service: _ProfileService,
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
        self._detection: HardwareDetection | None = None
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
                return HardwareDetection(
                    profile=cast(HardwareProfile, setting), reason=DetectionReason.MANUAL
                )
            return service.detect_hardware(url, refresh=refresh)

        self._task.submit(resolve, self._on_resolved)

    def _on_resolved(self, result: object, error: BaseException | None) -> None:
        if error is not None or not isinstance(result, HardwareDetection):
            # Detection must never get in the way; the app falls back to 8 GB hints.
            self._status_lbl.setText(NO_SUGGESTION_FOR_COMPUTER)
            self.resolved_changed.emit(None)
            return
        self._detection = result
        if self._read_only or self._combo is None:
            self._status_lbl.setText(read_only_profile_text(result.profile))
        else:
            self._status_lbl.setText(detection_status_text(result))
            auto_idx = self._combo.findData("auto")
            if auto_idx >= 0:
                text = (
                    auto_profile_text(result.profile)
                    if self.setting() == "auto"
                    else PROFILE_TEXT["auto"]
                )
                self._combo.setItemText(auto_idx, text)
        self.resolved_changed.emit(result)
