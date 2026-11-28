"""Settings → This computer: what the choices on *AI models* need from this computer.

Not a choice of its own (the place a step runs is decided by the service picked on AI
models), so the sidebar shows it indented under AI models.  Most important first: how powerful
this computer is ("My computer", which the model suggestions follow) and how fast it runs the
chosen models, then the address of the local AI server.  The profile and the address are edited
here and nowhere else; the AI models and Add-ons pages show them read-only and follow them
through the window's wiring.  The keys of the online services are on *Online services*.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import QTimer, Signal
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

from ...domain.hardware_profile import HardwareProfileSetting, profile_setting
from ...domain.install_item import InstallItem
from ...domain.ollama_address import normalize_ollama_url
from ...settings_schema import DEFAULTS, normalize_saved_ollama_url
from ...texts import models as mdl
from ...texts import review as txt
from ...texts import servers as srv
from ...texts import settings as st
from .. import _html
from .._hardware_profile_selector import HardwareProfileSelector
from .._ollama_status_line import OllamaStatusLine
from ._page import (
    FOCUS_FIND_SERVER,
    FOCUS_LOCAL_MODELS,
    FOCUS_SERVER,
    FOCUS_SPEED,
    SettingsContext,
    SettingsPage,
    SettingsPageBase,
)
from ._speed_panel import SpeedPanel


class AiSetupPage(SettingsPageBase):
    """Computer profile and speed test, local AI server address."""

    page = SettingsPage.AI_SETUP
    title = st.PAGE_AI_SETUP
    description = st.PAGE_INTRO_AI_SETUP

    #: The server address was changed (on editingFinished, never while typing).
    ollama_url_changed = Signal(str)
    #: The computer profile was changed.
    hardware_setting_changed = Signal(str)
    #: A speed test finished and stored its measurement.
    speed_measured = Signal()

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._app_service = context.app_service
        self._speed_settings_provider: Callable[[], Mapping[str, Any]] | None = None
        settings = context.settings
        # Known before any widget is built: "My computer" must detect for the
        # configured server, not for the default one.
        self._url = normalize_saved_ollama_url(settings.get("ollama_url"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        computer = self._build_performance_group(
            profile_setting(settings.get("hardware_profile", DEFAULTS["hardware_profile"]))
        )
        # The speed test is part of the same box: both are facts about this computer.
        self._performance_form.addRow(self._build_speed_block(computer))
        layout.addWidget(computer)
        layout.addWidget(self._build_server_group())
        layout.addStretch(1)

    def focus(self, focus: str, preselect: Sequence[InstallItem] = ()) -> None:
        _ = preselect
        if focus == FOCUS_SERVER:
            self._scroll_to(self._ollama_url)
            QTimer.singleShot(0, self._ollama_url, self._ollama_url.setFocus)
        elif focus == FOCUS_FIND_SERVER:
            self._scroll_to(self._ollama_url)
            QTimer.singleShot(0, self, self._on_find_server)
        elif focus == FOCUS_SPEED:
            self._scroll_to(self._speed_group)
            self._speed_panel.refresh()

    def _build_server_group(self) -> QGroupBox:
        group = QGroupBox(srv.SERVER_GROUP)
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        note = QLabel(srv.SERVER_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow(note)

        self._ollama_url = QLineEdit(self._url)
        self._ollama_url.setToolTip(srv.SERVER_URL_TOOLTIP)
        # editingFinished, not textChanged: a half-typed URL must not trigger detection.
        self._ollama_url.editingFinished.connect(self._on_url_edited)
        self._find_server_btn = QPushButton(srv.FIND_SERVER_BUTTON)
        self._find_server_btn.setToolTip(srv.FIND_SERVER_TOOLTIP)
        self._find_server_btn.clicked.connect(self._on_find_server)
        url_row = QHBoxLayout()
        url_row.setContentsMargins(0, 0, 0, 0)
        url_row.addWidget(self._ollama_url, stretch=1)
        url_row.addWidget(self._find_server_btn)
        form.addRow(srv.SERVER_URL_LABEL, url_row)

        self._status_line = OllamaStatusLine(app_service=self._app_service, url=self._url)
        self._status_line.use_server_requested.connect(self.set_server_url)
        form.addRow("", self._status_line)

        self._download_model_btn = QPushButton(mdl.DOWNLOAD_MODEL)
        self._download_model_btn.setToolTip(mdl.DOWNLOAD_MODEL_TOOLTIP)
        self._download_model_btn.clicked.connect(
            lambda: self._navigate(SettingsPage.ADDONS, FOCUS_LOCAL_MODELS)
        )
        row = QHBoxLayout()
        row.addWidget(self._download_model_btn)
        row.addStretch(1)
        form.addRow("", row)
        return group

    def _build_speed_block(self, group: QGroupBox) -> QWidget:
        """Build the speed test: it measures the models chosen on *AI models* on this computer.

        It sits in the same box as the power of the computer: both are facts about it.  The
        note and the button share one row, so the note talks about the button beside it.
        """
        self._speed_group = group
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 8, 0, 0)
        heading = QLabel(_html.bold(txt.SPEED_HEADING))
        layout.addWidget(heading)
        self._speed_panel = SpeedPanel(
            app_service=self._app_service,
            settings_provider=self._speed_settings,
            profile_selector=self._hardware_selector,
            ollama_url=self._url,
            parent=group,
        )
        self._speed_panel.measured.connect(self.speed_measured.emit)
        layout.addWidget(self._speed_panel)
        self._speed_panel.refresh()
        return block

    def set_speed_settings_provider(self, provider: Callable[[], Mapping[str, Any]]) -> None:
        """Take the settings the test measures from *provider* (the window hands the models
        chosen on *AI models*, unsaved choices included) instead of the saved ones."""
        self._speed_settings_provider = provider
        self._speed_panel.refresh()

    def _speed_settings(self) -> dict[str, Any]:
        """Return the settings the speed test measures, with this page's own server address."""
        base = (
            self._speed_settings_provider()
            if self._speed_settings_provider is not None
            else self._context.settings
        )
        return {**base, "ollama_url": self._url}

    def refresh_speed(self) -> None:
        """Redraw the speed block: the models chosen on *AI models* or a measurement changed."""
        self._speed_panel.refresh()

    def _build_performance_group(self, hardware_profile: HardwareProfileSetting) -> QGroupBox:
        group = QGroupBox(mdl.PERFORMANCE_GROUP)
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._hardware_selector = HardwareProfileSelector(
            app_service=self._app_service,
            setting=hardware_profile,
            ollama_url=self._url,
        )
        self._hardware_selector.setting_changed.connect(self.hardware_setting_changed.emit)
        form.addRow(self._hardware_selector)

        self._performance_form = form
        return group

    # ── settings ──────────────────────────────────────────────────────────────

    def load(self, values: Mapping[str, Any]) -> None:
        if "ollama_url" in values:
            self._url = normalize_saved_ollama_url(values["ollama_url"])
            self._ollama_url.setText(self._url)
            self._status_line.set_url(self._url)
            self._hardware_selector.set_ollama_url(self._url)
            self._speed_panel.set_ollama_url(self._url)
        if "hardware_profile" in values:
            self._hardware_selector.set_setting(profile_setting(values["hardware_profile"]))

    def values(self) -> dict[str, Any]:
        return {
            "ollama_url": normalize_ollama_url(self._ollama_url.text()),
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
        """Stop the speed test and the server check before the page is destroyed."""
        self._speed_panel.shutdown()
        self._status_line.shutdown()

    # ── server ────────────────────────────────────────────────────────────────

    def _on_url_edited(self) -> None:
        url = normalize_ollama_url(self._ollama_url.text())
        if url == self._url:
            return
        self._url = url
        self._status_line.set_url(url)
        self._hardware_selector.set_ollama_url(url)
        self._speed_panel.set_ollama_url(url)
        self.ollama_url_changed.emit(url)

    def _on_find_server(self) -> None:
        """Open the search dialog; a server picked there goes into the address field."""
        from ._server_search_dialog import ServerSearchDialog  # noqa: PLC0415

        dialog = ServerSearchDialog(self._app_service, self.window())
        dialog.server_chosen.connect(self.set_server_url)
        dialog.manual_entry_requested.connect(self._focus_url_field)
        dialog.exec()

    def _focus_url_field(self) -> None:
        self._ollama_url.setFocus()
        self._ollama_url.selectAll()

    def set_server_url(self, url: str) -> None:
        """Put *url* into the address field and let the other pages follow, as after typing."""
        self._ollama_url.setText(url)
        self._on_url_edited()
