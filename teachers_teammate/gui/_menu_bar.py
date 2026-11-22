"""The main window's menu bar: File, Settings, Tools and Help, and what each entry does.

The window builds the bar once and hands over the handlers it needs as :class:`MenuHooks`;
nothing here reaches into the window.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMenuBar

from ..texts import menus
from ..texts import settings as st
from . import _handles
from ._config_panel import ConfigPanel
from ._export_controller import ExportController
from ._export_menu import build_export_menu
from ._help_dialog import AboutDialog, HelpDialog, ThirdPartyLicensesDialog
from .settings import PAGE_TITLES, SettingsPage, last_page
from .settings._window import SUB_PAGES


@dataclass(frozen=True)
class MenuHooks:
    """What the menu bar asks of the window."""

    quit_requested: Callable[[], None]
    open_settings: Callable[[SettingsPage], None]
    open_benchmark: Callable[[], None]
    open_monitor: Callable[[], None]
    open_setup_assistant: Callable[[], None]


class MenuBar(QObject):
    """The window's menus; it holds no state of its own beyond the entries it built."""

    def __init__(
        self,
        window: QMainWindow,
        *,
        panel: ConfigPanel,
        export: ExportController,
        hooks: MenuHooks,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._panel = panel
        self._export = export
        self._hooks = hooks
        #: One entry per main page of the Settings window, by page.
        self.settings_actions: dict[SettingsPage, QAction] = {}
        #: "All settings…", which opens the window where the teacher left it.
        self.all_settings_action: QAction
        #: The Tools entries; the window's tests click them to open what they show.
        self.benchmark_action: QAction
        self.monitor_action: QAction
        self.setup_action: QAction
        bar = window.menuBar()
        assert bar is not None
        self._build_file_menu(bar)
        self._build_settings_menu(bar)
        self._build_tools_menu(bar)
        self._build_help_menu(bar)

    def _build_file_menu(self, bar: QMenuBar) -> None:
        """File: open documents, export results, quit."""
        file_menu = bar.addMenu(menus.FILE)
        assert file_menu is not None
        open_folder_act = QAction(menus.OPEN_FOLDER, self)
        open_folder_act.triggered.connect(self._panel.select_folder)
        file_menu.addAction(open_folder_act)
        open_files_act = QAction(menus.OPEN_FILES, self)
        open_files_act.triggered.connect(self._panel.select_files)
        file_menu.addAction(open_files_act)
        export_menu, kinds = build_export_menu(file_menu)
        export_menu.triggered.connect(lambda action: self._export.on_toolbar_action(kinds[action]))
        file_menu.addMenu(export_menu)
        # Enabled together with the Export button: there is nothing to export before a run.
        self._export.add_actions(list(kinds))
        file_menu.addSeparator()
        quit_act = QAction(menus.QUIT, self)
        quit_act.setShortcut(QKeySequence(QKeySequence.StandardKey.Quit))
        quit_act.setMenuRole(QAction.MenuRole.QuitRole)
        quit_act.triggered.connect(self._hooks.quit_requested)
        file_menu.addAction(quit_act)

    def _build_settings_menu(self, bar: QMenuBar) -> None:
        """Settings: one entry per main page of the Settings window, and "All settings…"."""
        settings_menu = bar.addMenu(menus.SETTINGS)
        assert settings_menu is not None
        tooltips = {
            SettingsPage.GENERAL: st.PAGE_TIP_GENERAL,
            SettingsPage.AI_MODELS: st.PAGE_TIP_AI_MODELS,
            SettingsPage.AI_SETUP: st.PAGE_TIP_AI_SETUP,
            SettingsPage.ONLINE_SERVICES: st.PAGE_TIP_ONLINE_SERVICES,
            SettingsPage.SCAN_QUALITY: st.PAGE_TIP_SCAN_QUALITY,
            SettingsPage.PRIVACY: st.PAGE_TIP_PRIVACY,
            SettingsPage.ADDONS: st.PAGE_TIP_ADDONS,
        }
        for page in SettingsPage:
            if page in SUB_PAGES:
                continue  # reached from AI models and in the window's sidebar, not from here
            action = QAction(menus.entry(PAGE_TITLES[page]), self)
            action.setToolTip(tooltips[page])
            action.triggered.connect(lambda _checked=False, p=page: self._hooks.open_settings(p))
            settings_menu.addAction(action)
            self.settings_actions[page] = action
        settings_menu.addSeparator()
        all_act = QAction(menus.ALL_SETTINGS, self)
        all_act.setObjectName(_handles.ALL_SETTINGS)
        all_act.setShortcut(QKeySequence("Ctrl+,"))
        all_act.setMenuRole(QAction.MenuRole.PreferencesRole)
        all_act.triggered.connect(lambda: self._hooks.open_settings(last_page()))
        settings_menu.addAction(all_act)
        self.all_settings_action = all_act

    def _build_tools_menu(self, bar: QMenuBar) -> None:
        """Tools: things that are not part of a batch (Benchmark, System Monitor, Setup)."""
        tools_menu = bar.addMenu(menus.TOOLS)
        assert tools_menu is not None
        benchmark_act = QAction(menus.BENCHMARK, self)
        benchmark_act.setObjectName(_handles.BENCHMARK)
        benchmark_act.setToolTip(menus.BENCHMARK_TIP)
        benchmark_act.triggered.connect(self._hooks.open_benchmark)
        tools_menu.addAction(benchmark_act)
        monitor_act = QAction(menus.SYSTEM_MONITOR, self)
        monitor_act.setObjectName(_handles.SYSTEM_MONITOR)
        monitor_act.setToolTip(menus.SYSTEM_MONITOR_TIP)
        monitor_act.triggered.connect(self._hooks.open_monitor)
        tools_menu.addAction(monitor_act)
        self.benchmark_action = benchmark_act
        self.monitor_action = monitor_act
        tools_menu.addSeparator()
        self.setup_action = QAction(menus.SETUP_ASSISTANT, self)
        self.setup_action.setObjectName(_handles.SETUP_ASSISTANT)
        self.setup_action.setToolTip(menus.SETUP_ASSISTANT_TIP)
        self.setup_action.triggered.connect(
            lambda _checked=False: self._hooks.open_setup_assistant()
        )
        tools_menu.addAction(self.setup_action)

    def _build_help_menu(self, bar: QMenuBar) -> None:
        """Help: the guide, the licences of what ships with the app, and the version."""
        help_menu = bar.addMenu(menus.HELP)
        assert help_menu is not None
        guide_act = QAction(menus.USER_GUIDE, self)
        guide_act.setObjectName(_handles.USER_GUIDE)
        guide_act.triggered.connect(lambda: HelpDialog(self._window).exec())
        help_menu.addAction(guide_act)
        help_menu.addSeparator()
        licenses_act = QAction(menus.LICENSES, self)
        licenses_act.setObjectName(_handles.LICENSES)
        licenses_act.triggered.connect(lambda: ThirdPartyLicensesDialog(self._window).exec())
        help_menu.addAction(licenses_act)
        help_menu.addSeparator()
        about_act = QAction(menus.ABOUT, self)
        about_act.setObjectName(_handles.ABOUT)
        about_act.triggered.connect(lambda: AboutDialog(self._window).exec())
        help_menu.addAction(about_act)
