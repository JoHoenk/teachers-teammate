"""The Settings window: opening it on a page and taking over what it changed.

The window keeps one ``open_settings`` that hands over here, so a deep link from a dialog
(``CheckController``, the readiness line, the menu bar) still reaches the same place.  What the
window has to do afterwards — save, check again, refresh the queue — arrives as
:class:`SettingsHooks`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QWidget

from ..application.composition import AppServices
from ..config import OcrConfig
from ._check_controller import CheckController
from ._config_panel import ConfigPanel
from ._privacy_settings import FlowAcknowledgements
from ._privacy_view_controller import PrivacyViewController
from ._pull_manager import PullManager
from .settings import SettingsContext, SettingsPage, SettingsResult, SettingsWindow


@dataclass(frozen=True)
class SettingsHooks:
    """What the Settings window asks of the window when it closes."""

    #: Settings → Restore defaults was confirmed: reset everything, save, check again.
    restored_all: Callable[[], None]
    #: Write the settings file (the pages changed what the panel now holds).
    save: Callable[[], None]
    #: Ask again what a run needs, and show what is cached for the new folder.
    applied: Callable[[], None]
    #: An add-on was installed from a page.
    addon_installed: Callable[[str], None]
    #: A round of installs may have changed what is installed.
    packages_changed: Callable[[], None]
    #: Show the image-preparation preview over the Settings window that asked for it.
    preprocess_preview: Callable[[OcrConfig, QWidget], None]


class SettingsLauncher(QObject):
    """Opens the one Settings window and applies what it returned."""

    def __init__(
        self,
        window: QWidget,
        *,
        services: AppServices,
        panel: ConfigPanel,
        check: CheckController,
        privacy: PrivacyViewController,
        pull_manager: PullManager,
        acknowledgements: Callable[[], FlowAcknowledgements],
        hooks: SettingsHooks,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._services = services
        self._panel = panel
        self._check = check
        self._privacy = privacy
        self._pull_manager = pull_manager
        self._acknowledgements = acknowledgements
        self._hooks = hooks
        self._open_window: SettingsWindow | None = None

    @property
    def visible_window(self) -> SettingsWindow | None:
        """The Settings window while it is open, so a dialog can point at it."""
        window = self._open_window
        return window if window is not None and window.isVisible() else None

    def context(self) -> SettingsContext:
        """Everything the Settings window is built from."""
        language, anonymizer_config = self._panel.anonymizer_context()
        return SettingsContext(
            settings=self._panel.get_settings_dict(),
            app_service=self._services,
            language=language,
            anonymizer_config=anonymizer_config,
            acknowledgements=self._acknowledgements(),
            display_anonymized=self._privacy.display_anonymized,
            privacy_available=self._privacy.privacy_available,
            pull_manager=self._pull_manager,
        )

    def open(
        self,
        page: SettingsPage,
        *,
        focus: str = "",
        preselect: Sequence[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        """Open the Settings window on *page* (focused on *focus*) and apply it on OK.

        If the window is already open (a deep link from a dialog opened on top of it), it
        only switches to *page*.
        """
        if self.visible_window is not None:
            self.visible_window.show_page(page, focus, preselect)
            return
        window = SettingsWindow(
            self.context(),
            parent or self._window,
            page=page,
            focus=focus,
            preselect=preselect,
        )
        window.preprocess_preview_requested.connect(
            lambda ocr_config: self._hooks.preprocess_preview(ocr_config, window)
        )
        window.packages_changed.connect(self._hooks.packages_changed)
        window.addon_installed.connect(self._hooks.addon_installed)
        # What the Privacy page showed: its checkbox was usable only if hiding was possible
        # when the window opened (later changes to the panel must not decide that).
        hiding_was_possible = self._privacy.privacy_available
        self._open_window = window
        try:
            code = window.exec()
            result = window.values()
        finally:
            self._open_window = None
        if result.restore_all:
            self._hooks.restored_all()
        elif code == QDialog.DialogCode.Accepted:
            self.apply(result, hiding_was_possible=hiding_was_possible)
        window.deleteLater()

    def apply(self, result: SettingsResult, *, hiding_was_possible: bool) -> None:
        """Take over what the Settings window changed, then save once."""
        self._panel.update_settings(result.settings)
        if result.turn_on_anonymization:
            # The AI models page said it would: an online service was chosen while names would
            # be sent as written.  The checkbox's own handler guards the stage and refreshes
            # the data-flow box; it comes before the save so the choice is kept.
            self._panel.set_anonymization_enabled(True)
        if result.anonymizer_config is not None:
            before = self._panel.build_anonymizer_config()
            self._panel.update_anonymizer_config(result.anonymizer_config)
            if self._panel.build_anonymizer_config() != before:
                # New models or patterns produce different placeholders for the shown text.
                self._check.page.forget_anonymization()
        if result.display_anonymized is not None and hiding_was_possible:
            # Without the add-on the checkbox is disabled and shows "off"; applying that
            # would overwrite the saved preference.
            self._privacy.apply_display_anonymized(result.display_anonymized)
        self._hooks.save()
        self._hooks.applied()
