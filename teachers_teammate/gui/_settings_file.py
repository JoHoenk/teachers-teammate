"""The settings file and the window's own geometry: read at start, written on request.

Two things a teacher cannot rebuild live here: the pipeline settings in ``ocr.toml`` and the
window geometry in ``QSettings``.  Reading also decides the first start — a window with no
settings file asks its questions instead of opening empty.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QTimer
from PySide6.QtWidgets import QMainWindow, QSplitter

from ..application.composition import AppServices
from ..config import dump_config_file, load_config_file
from ..domain.settings_notice import SettingNotice, SettingNoticeKind
from ..texts import dialogs as dlg
from ..texts import main_window as win
from . import _dialogs
from ._config_panel import ConfigPanel
from ._log_widget import LogWidget
from ._window_geometry import maximize_without_saved_geometry, restore_geometry


@dataclass(frozen=True)
class SettingsFileHooks:
    """What reading the settings asks of the window."""

    #: No settings file yet: ask the first-start questions on top of the window.
    ask_first_start: Callable[[], None]
    #: A settings file was read: show what is cached for the folder it names.
    queue_loaded: Callable[[], None]


class SettingsFile(QObject):
    """Where the settings are stored, and what was read from them."""

    def __init__(
        self,
        window: QMainWindow,
        *,
        services: AppServices,
        panel: ConfigPanel,
        splitter: QSplitter,
        log: LogWidget,
        settings_store: Callable[[], QSettings],
        hooks: SettingsFileHooks,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._services = services
        self._panel = panel
        self._splitter = splitter
        self._log = log
        self._settings_store = settings_store
        self._hooks = hooks
        self._config_path: Path | None = None

    @property
    def config_path(self) -> Path | None:
        """The file the settings were read from and are written to, or ``None``."""
        return self._config_path

    def restore_gui_state(self) -> None:
        """Restore the window geometry and the splitter sizes from the GUI settings."""
        qs = self._settings_store()
        if not restore_geometry(self._window, qs.value("gui/geometry")):
            # The first start: the window is as large as the screen allows.  A saved geometry,
            # normal or maximized, always wins over this.
            maximize_without_saved_geometry(self._window)
        sizes = [360, 1140]
        raw_sizes = qs.value("gui/panel_sizes")
        if isinstance(raw_sizes, list) and len(raw_sizes) == 2:
            try:
                restored = [int(v) for v in raw_sizes]
                if restored[0] >= 200 and restored[1] >= 400:
                    sizes = restored
            except ValueError, TypeError:
                pass
        QTimer.singleShot(0, lambda s=sizes: self._splitter.setSizes(s))

    def load_if_present(self) -> None:
        """Read the settings file, or ask the first-start questions when there is none."""
        config_path = self._services.setup.resolve_config_path(None)
        self.restore_gui_state()

        installer_choices = self._services.setup.first_start_settings(config_path)
        if installer_choices is not None or config_path is None:
            # First start (no ocr.toml yet, or only what the installer wrote): the assistant
            # asks what only the teacher knows and works the models out from the answers.  It
            # opens on top of this window, so the teacher sees the app first.  What the
            # installer chose is loaded first, so the first save keeps it.
            if installer_choices and config_path is not None:
                self._config_path = config_path
                self._panel.load_from_dict(installer_choices)
            QTimer.singleShot(0, self._hooks.ask_first_start)
            return
        self._config_path = config_path
        try:
            loaded = load_config_file(config_path)
            values = loaded.values
            base = config_path.parent.resolve()
            for key in ("input", "output"):
                if values.get(key):
                    p = Path(str(values[key]))
                    if not p.is_absolute():
                        values[key] = str((base / p).resolve())
            self._panel.load_from_dict(values)
            # Signals fired during bulk field restoration can run queue refresh
            # before all dependent settings (e.g. docx_enabled) are applied. Run one
            # final refresh after the full config is loaded so cached state is
            # shown consistently on startup.
            self._hooks.queue_loaded()
            self._log.append_text(win.log_settings_loaded(config_path))
            for notice in loaded.notices:
                self.show_notice(notice)
        except Exception as exc:  # noqa: BLE001  # config file may be missing, malformed, or use an unexpected type; log the warning and continue
            self._log.append_text(win.log_settings_not_loaded(config_path, exc))

    def show_notice(self, setting_notice: SettingNotice) -> None:
        """Say what the app changed about a saved setting instead of changing it silently.

        A replaced text-recognition engine changes what the next run does, so it interrupts
        the teacher; the other notices are in the log.
        """
        notice = dlg.setting_notice(setting_notice)
        self._log.append_text(win.log_notice(notice.body))
        if setting_notice.kind is SettingNoticeKind.ENGINE_REPLACED:
            _dialogs.notify(self._window, notice)

    def save(self, *, notify: bool = True, silent_on_error: bool = False) -> None:
        """Write the settings file and remember the window geometry."""
        qs = self._settings_store()
        qs.setValue("gui/geometry", self._window.saveGeometry())
        qs.setValue("gui/panel_sizes", self._splitter.sizes())

        values = self._panel.to_dict()
        out = (
            self._config_path
            if self._config_path is not None
            else self._services.setup.default_config_path()
        )
        try:
            out.write_text(dump_config_file(values), encoding="utf-8")
            if notify:
                self._log.append_text(win.log_settings_saved(out.resolve()))
        except OSError as exc:
            if not silent_on_error:
                _dialogs.notify(self._window, dlg.save_failed(str(exc)))
