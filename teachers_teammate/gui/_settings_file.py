"""The settings file and the window's own geometry: read at start, written on request.

Two things a teacher cannot rebuild live here: the pipeline settings in ``ocr.toml`` and the
window geometry in ``QSettings``.  Reading also decides the first start — a window with no
settings file asks its questions instead of opening empty.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QTimer
from PySide6.QtWidgets import QMainWindow, QSplitter

from .. import __version__
from ..application.composition import AppServices, set_log_detail
from ..application.stored_results import RunInProgressError
from ..config import broken_settings_copy, load_config_file, save_settings_file
from ..domain.releases import version_tuple
from ..domain.settings_notice import SettingNotice, SettingNoticeKind
from ..messages import log_failure
from ..settings_schema import DEFAULTS, log_detail_of
from ..texts import dialogs as dlg
from ..texts import main_window as win
from ..texts._types import Notice
from . import _dialogs
from ._config_panel import ConfigPanel
from ._log_widget import LogWidget
from ._window_geometry import maximize_without_saved_geometry, restore_geometry

_logger = logging.getLogger(__name__)

#: The GUI-store key holding the app version that last started with this setup.
APP_VERSION_KEY = "gui/app_version"


@dataclass(frozen=True)
class SettingsFileHooks:
    """What reading the settings asks of the window."""

    #: No settings file yet: ask the first-start questions on top of the window.
    ask_first_start: Callable[[], None]
    #: A settings file was read: show what is cached for the folder it names.
    queue_loaded: Callable[[], None]
    #: The app is another version than the one that last ran with this setup: offer the
    #: assistant again (it changes nothing unless the teacher presses Set up).
    ask_after_update: Callable[[], None]


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
        # A file that could not be read is written over only after a copy of it is kept.
        self._load_failed = False
        self._backup: Path | None = None
        # The teacher hears about the unreadable file once, not on every save.
        self._notice_shown = False

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
                pass  # a stored size that is no number keeps the default panes
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
            self._remember_version()
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
            # Before anything else is written down: how much this session puts into the log.
            self._apply_log_detail()
            # Before the queue is shown: a row must never name a result this just deleted.
            self._expire_stored_results()
            # Signals fired during bulk field restoration can run queue refresh
            # before all dependent settings (e.g. docx_enabled) are applied. Run one
            # final refresh after the full config is loaded so cached state is
            # shown consistently on startup.
            self._hooks.queue_loaded()
            self._log.append_text(win.log_settings_loaded(config_path))
            for notice in loaded.notices:
                self.show_notice(notice)
            if self._remember_version():
                QTimer.singleShot(0, self._hooks.ask_after_update)
        except Exception as exc:  # noqa: BLE001  # a file that does not load is kept, not written over
            log_failure(_logger, "The settings file could not be loaded", exc)
            self._keep_unreadable_file(config_path, exc)

    def _apply_log_detail(self) -> None:
        """Send the saved log detail to the log: the setting decides unless a flag did."""
        stored = self._panel.get_settings_dict().get("log_detail", DEFAULTS["log_detail"])
        set_log_detail(log_detail_of(stored), from_setting=True)

    def _remember_version(self) -> bool:
        """Store the running app version; return whether it is newer than the one stored before."""
        store = self._settings_store()
        stored = store.value(APP_VERSION_KEY)
        if stored == __version__:
            return False
        store.setValue(APP_VERSION_KEY, __version__)
        return stored is None or version_tuple(__version__) > version_tuple(str(stored))

    def _expire_stored_results(self) -> None:
        """Drop the stored results older than ``results_keep_days``; once, at start.

        A run in progress is left alone, and so is everything when the retention is off (``0``).
        A change of the setting takes effect at the next start, which is what the page says.
        """
        days = int(
            self._panel.get_settings_dict().get("results_keep_days", DEFAULTS["results_keep_days"])
        )
        try:
            removed = self._services.stored.expire(days)
        except RunInProgressError:
            self._log.append_text(win.LOG_STORED_RESULTS_KEPT)
            return
        if removed:
            self._log.append_text(win.log_stored_results_expired(removed))

    def _keep_unreadable_file(self, path: Path, exc: BaseException) -> None:
        """Keep a settings file that cannot be read, say so, and hold the save back.

        A teacher's setup is worth more than the start-up that failed: a copy beside the file
        says what it held, and the file itself is replaced by a later save only once that copy
        exists.
        """
        self._load_failed = True
        self._log.append_text(win.log_settings_not_loaded(path, exc))
        backup = self._copy_broken_file(path)
        if backup is not None:
            self._announce_once(lambda: dlg.settings_file_broken(path.name, backup.name))

    def _copy_broken_file(self, path: Path) -> Path | None:
        """Copy a settings file that cannot be read; ``None`` when there is no copy."""
        try:
            backup = broken_settings_copy(path)
        except OSError as exc:
            self._log.append_text(win.log_settings_backup_failed(path, exc))
            return None
        if backup is not None:
            self._backup = backup
            self._log.append_text(win.log_settings_backup(backup))
        return backup

    def _announce_once(self, notice: Callable[[], Notice], *, silent: bool = False) -> None:
        """Show this session's one notice about the unreadable settings file."""
        if silent or self._notice_shown:
            return
        self._notice_shown = True
        _dialogs.notify(self._window, notice())

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
        if not self._may_replace(out, silent=silent_on_error):
            self._log.append_text(win.log_settings_save_refused(out))
            self._announce_once(
                lambda: dlg.settings_file_not_replaced(out.name), silent=silent_on_error
            )
            return
        try:
            save_settings_file(out, values)
            self._load_failed = False
            self._backup = None
            self._apply_log_detail()  # the teacher changed it, so it takes effect now
            if notify:
                self._log.append_text(win.log_settings_saved(out.resolve()))
        except OSError as exc:
            if not silent_on_error:
                _dialogs.notify(self._window, dlg.save_failed(str(exc)))

    def _may_replace(self, out: Path, *, silent: bool = False) -> bool:
        """Return whether the settings may be written to *out*.

        A file that could not be read is written over only while a copy of it is kept.  The copy
        is tried again here: what blocked it at start-up may be gone by now, and a file that is
        gone takes nothing with it.
        """
        if not self._load_failed or not out.exists():
            return True
        if self._backup is not None and self._backup.exists():
            return True
        backup = self._copy_broken_file(out)
        if backup is None:
            return False
        self._announce_once(lambda: dlg.settings_file_broken(out.name, backup.name), silent=silent)
        return True
