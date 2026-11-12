"""Settings → This computer: what the choices on *AI models* need from this computer.

Not a choice of its own (the place a step runs is decided by the service picked on AI
models), so the sidebar shows it indented under AI models.  Most important first: how powerful
this computer is ("My computer", which the model suggestions follow) and its graphics card,
then the address of the local AI server.  The profile and the address are edited here and
nowhere else; the AI models and Add-ons pages show them read-only and follow them through the
window's wiring.  The keys of the online services are on *Online services*.
"""

from __future__ import annotations

from collections.abc import Mapping
import sys
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import DEFAULTS
from .. import _user_text as txt
from .._hardware_profile_selector import HardwareProfileSelector
from .._model_fetch import _GpuDetectThread, _retire_thread
from ._page import (
    FOCUS_LOCAL_MODELS,
    SettingsContext,
    SettingsPage,
    SettingsPageBase,
)

TITLE = txt.PAGE_AI_SETUP
DESCRIPTION = (
    f"What {txt.PAGE_AI_MODELS} needs from this computer: how powerful it is (the model "
    "suggestions follow it) and where its local AI server is. Set up once; usually nothing "
    "to change."
)
SERVER_GROUP = "Local AI server (Ollama)"
SERVER_NOTE = (
    "Usually runs on this computer. If your school runs Ollama on its own server, enter that "
    "server's address."
)
SERVER_URL_LABEL = "Server address:"
SERVER_URL_TOOLTIP = (
    "The address of your Ollama server. Used for all text recognition,\n"
    "proofreading, and content review steps that use Ollama."
)
DOWNLOAD_MODEL = "Download a model…"
DOWNLOAD_MODEL_TOOLTIP = "Opens Add-ons, where AI models for this computer are downloaded."
PERFORMANCE_GROUP = "How powerful this computer is"
DETECT_GPU = "Detect GPU"
DETECT_GPU_TOOLTIP = (
    "Check whether a graphics card is available —\n"
    "this can significantly speed up text recognition."
)
INSTALL_GPU_ADDON = "Install GPU Addon…"
DETECTING = "Detecting…"
NO_GPU = "No NVIDIA or AMD GPU detected."


class AiSetupPage(SettingsPageBase):
    """Computer profile and GPU check, local AI server address."""

    page = SettingsPage.AI_SETUP
    title = TITLE
    description = DESCRIPTION

    #: The server address was changed (on editingFinished, never while typing).
    ollama_url_changed = Signal(str)
    #: The computer profile was changed.
    hardware_setting_changed = Signal(str)
    #: A GPU add-on was installed from this page.
    addon_installed = Signal(str)

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._app_service = context.app_service
        self._gpu_detect_thread: _GpuDetectThread | None = None
        self._detected_gpus: list = []
        settings = context.settings
        # Known before any widget is built: "My computer" must detect for the
        # configured server, not for the default one.
        self._url = str(settings.get("ollama_url") or DEFAULTS["ollama_url"])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(
            self._build_performance_group(
                str(settings.get("hardware_profile", DEFAULTS["hardware_profile"]))
            )
        )
        layout.addWidget(self._build_server_group())
        layout.addStretch(1)

    def _build_server_group(self) -> QGroupBox:
        group = QGroupBox(SERVER_GROUP)
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        note = QLabel(SERVER_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow(note)

        self._ollama_url = QLineEdit(self._url)
        self._ollama_url.setToolTip(SERVER_URL_TOOLTIP)
        # editingFinished, not textChanged: a half-typed URL must not trigger detection.
        self._ollama_url.editingFinished.connect(self._on_url_edited)
        form.addRow(SERVER_URL_LABEL, self._ollama_url)

        self._download_model_btn = QPushButton(DOWNLOAD_MODEL)
        self._download_model_btn.setToolTip(DOWNLOAD_MODEL_TOOLTIP)
        self._download_model_btn.clicked.connect(
            lambda: self._navigate(SettingsPage.ADDONS, FOCUS_LOCAL_MODELS)
        )
        row = QHBoxLayout()
        row.addWidget(self._download_model_btn)
        row.addStretch(1)
        form.addRow("", row)
        return group

    def _build_performance_group(self, hardware_profile: str) -> QGroupBox:
        group = QGroupBox(PERFORMANCE_GROUP)
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._hardware_selector = HardwareProfileSelector(
            app_service=self._app_service,
            setting=hardware_profile,
            ollama_url=self._url,
        )
        self._hardware_selector.setting_changed.connect(self.hardware_setting_changed.emit)
        form.addRow(self._hardware_selector)

        self._gpu_detect_btn = QPushButton(DETECT_GPU)
        self._gpu_detect_btn.setFixedWidth(120)
        self._gpu_detect_btn.setToolTip(DETECT_GPU_TOOLTIP)
        self._gpu_detect_btn.clicked.connect(self._on_detect_gpu)
        self._gpu_status_lbl = QLabel("—")
        self._gpu_status_lbl.setWordWrap(True)
        gpu_detect_row = QWidget()
        gdr = QHBoxLayout(gpu_detect_row)
        gdr.setContentsMargins(0, 0, 0, 0)
        gdr.addWidget(self._gpu_detect_btn)
        gdr.addWidget(self._gpu_status_lbl, stretch=1)
        form.addRow("GPU:", gpu_detect_row)

        self._gpu_install_btn = QPushButton(INSTALL_GPU_ADDON)
        self._gpu_install_btn.setVisible(False)
        self._gpu_install_btn.clicked.connect(self._on_install_gpu_addon)
        form.addRow("", self._gpu_install_btn)
        return group

    # ── settings ──────────────────────────────────────────────────────────────

    def load(self, values: Mapping[str, Any]) -> None:
        if "ollama_url" in values:
            self._url = str(values["ollama_url"] or DEFAULTS["ollama_url"])
            self._ollama_url.setText(self._url)
            self._hardware_selector.set_ollama_url(self._url)
        if "hardware_profile" in values:
            self._hardware_selector.set_setting(str(values["hardware_profile"]))

    def values(self) -> dict[str, Any]:
        return {
            "ollama_url": self._ollama_url.text().strip() or DEFAULTS["ollama_url"],
            "hardware_profile": self._hardware_selector.setting(),
        }

    def owned_keys(self) -> frozenset[str]:
        return frozenset(("ollama_url", "hardware_profile"))

    def restore_defaults(self) -> None:
        """Show the defaults and let the other pages follow them, as after an edit here."""
        before = self.values()
        super().restore_defaults()
        after = self.values()
        if after["ollama_url"] != before["ollama_url"]:
            self.ollama_url_changed.emit(after["ollama_url"])
        if after["hardware_profile"] != before["hardware_profile"]:
            self.hardware_setting_changed.emit(after["hardware_profile"])

    def shutdown(self) -> None:
        """Detach the GPU-detection thread before the page is destroyed."""
        _retire_thread(self._gpu_detect_thread)

    # ── server ────────────────────────────────────────────────────────────────

    def _on_url_edited(self) -> None:
        url = self._ollama_url.text().strip() or DEFAULTS["ollama_url"]
        if url == self._url:
            return
        self._url = url
        self._hardware_selector.set_ollama_url(url)
        self.ollama_url_changed.emit(url)

    # ── hardware ──────────────────────────────────────────────────────────────

    def _on_detect_gpu(self) -> None:
        self._gpu_detect_btn.setEnabled(False)
        self._gpu_status_lbl.setText(DETECTING)
        self._gpu_install_btn.setVisible(False)
        self._gpu_detect_thread = _GpuDetectThread(self._app_service)
        self._gpu_detect_thread.detect_done.connect(self._on_gpu_detected)
        self._gpu_detect_thread.finished.connect(lambda: self._gpu_detect_btn.setEnabled(True))
        self._gpu_detect_thread.start()

    def _on_gpu_detected(self, gpus: list) -> None:
        self._detected_gpus = gpus
        # The probe was just repeated: let "My computer" describe the new result.
        self._hardware_selector.refresh_status()
        if not gpus:
            self._gpu_status_lbl.setText(NO_GPU)
            self._gpu_install_btn.setVisible(False)
            return
        names = ", ".join(f"{g.name} ({g.vendor.upper()})" for g in gpus)
        self._gpu_status_lbl.setText(names)
        self._gpu_install_btn.setVisible(getattr(sys, "frozen", False))

    def _on_install_gpu_addon(self) -> None:
        from .._addon_installer_dialog import GpuAddonDialog  # noqa: PLC0415

        dlg = GpuAddonDialog(self._detected_gpus, self.window())
        dlg.installed.connect(self.addon_installed)
        dlg.exec()
