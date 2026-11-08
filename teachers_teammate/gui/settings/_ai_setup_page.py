"""Settings → Where the AI runs: the local AI server, this computer, and the online services.

Everything the AI choices on *AI models* depend on is set up here: on this computer the
server address and the computer profile ("My computer"), at a vendor the API keys.  The
profile and the address are edited here and nowhere else; the AI models and Add-ons pages
show them read-only and follow them through the window's wiring.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import sys
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import DEFAULTS
from .._hardware_profile_selector import HardwareProfileSelector
from .._model_fetch import _GpuDetectThread, _retire_thread
from ._credentials import FILE_FALLBACK_TEXT, FILE_FALLBACK_TITLE, CredentialsGroup
from ._page import (
    FOCUS_API_KEYS,
    FOCUS_LOCAL_MODELS,
    SettingsContext,
    SettingsPage,
    SettingsPageBase,
)

TITLE = "Where the AI runs"
DESCRIPTION = (
    "On this computer (or your school's own server), or at an online service. Set up the local "
    "AI server and say how powerful this computer is, and save the access keys of the online "
    "services. The model suggestions on the other pages follow what you choose here."
)
SERVER_GROUP = "Local AI server (Ollama)"
SERVER_URL_LABEL = "Server address:"
SERVER_URL_TOOLTIP = (
    "The address of your Ollama server. Used for all text recognition,\n"
    "proofreading, and content review steps that use Ollama."
)
DOWNLOAD_MODEL = "Download a model…"
DOWNLOAD_MODEL_TOOLTIP = "Opens Add-ons, where AI models for this computer are downloaded."
PERFORMANCE_GROUP = "Performance"
DETECT_GPU = "Detect GPU"
DETECT_GPU_TOOLTIP = (
    "Check whether a graphics card is available —\n"
    "this can significantly speed up text recognition."
)
INSTALL_GPU_ADDON = "Install GPU Addon…"
DETECTING = "Detecting…"
NO_GPU = "No NVIDIA or AMD GPU detected."


class AiSetupPage(SettingsPageBase):
    """Local AI server address and "Download a model…", computer profile, GPU check, API keys."""

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
        layout.addWidget(self._build_server_group())
        layout.addWidget(
            self._build_performance_group(
                str(settings.get("hardware_profile", DEFAULTS["hardware_profile"]))
            )
        )
        self._credentials = CredentialsGroup(context.app_service)
        layout.addWidget(self._credentials)
        layout.addStretch(1)

    def _build_server_group(self) -> QGroupBox:
        group = QGroupBox(SERVER_GROUP)
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

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

    def apply(self) -> None:
        """Save the API keys (they are not part of the settings file).

        If the keychain refused a key, say where it was kept instead: the note on the group
        promised the keychain.
        """
        in_file = self._credentials.apply()
        if in_file:
            titles = self._credentials.key_titles()
            services = ", ".join(titles.get(env_key, env_key) for env_key in in_file)
            QMessageBox.warning(
                self.window(), FILE_FALLBACK_TITLE, FILE_FALLBACK_TEXT.format(services=services)
            )

    def focus(self, focus: str, preselect: Sequence[str] = ()) -> None:
        _ = preselect
        if focus == FOCUS_API_KEYS:
            self._scroll_to(self._credentials)

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
