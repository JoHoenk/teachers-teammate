"""Main application window and entry point."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplashScreen,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
import qdarkstyle

from .. import __version__ as _APP_VERSION
from ..application.composition import (
    AppServices,
    diagnostics_phase,
    inject_addon_packages_dir,
    start_diagnostics,
)
from ..application.documents import WorkedOn
from ..application.ollama_server import OllamaServerService
from ..application.queue import StageState
from ..application.run import IssueKind, RunPreflight
from ..config import DEFAULTS, Config, dump_config_file, load_config_file
from ..domain.data_location import DataFlow
from ..domain.local_models import PAGE_MODELS_ADDON_ID, PRIVACY_ADDON_ID, READER_ADDON_ID
from ..domain.model_catalog import HardwareDetection
from ..domain.ollama_discovery import ServerKind
from ..domain.settings_notice import SettingNotice, SettingNoticeKind
from ..texts import dialogs as dlg
from ..texts.settings import WHERE_ADDONS, WHERE_GENERAL
from . import _dialogs
from ._app_bootstrap import create_app, finish_process
from ._background import LatestTask, run_in_background, wait_for_background_tasks
from ._chart_widget import ChartWidget
from ._check_controller import CheckController, CheckHooks
from ._config_panel import ConfigPanel
from ._credential_store import CredentialStore
from ._data_flow_summary import advice_text, level_text, stage_tooltips
from ._export_controller import ExportController
from ._export_menu import build_export_menu
from ._help_dialog import AboutDialog, HelpDialog, ThirdPartyLicensesDialog
from ._image_utils import load_icon, load_pixmap
from ._log_widget import LogWidget
from ._model_wording import profile_text
from ._preprocess_preview import PreprocessPreview
from ._privacy_settings import (
    DISPLAY_ANONYMIZED_DEFAULT,
    FlowAcknowledgements,
)
from ._privacy_view_controller import PrivacyViewController
from ._pull_manager import PullManager
from ._readiness_controller import ReadinessController, ReadinessHooks
from ._readiness_line import ReadinessLine
from ._results_table import ResultsTable
from ._run_controller import RunController, RunHooks, WorkerLike
from ._run_session import RunSession, wait_detail_text
from ._stats_widget import SystemStatsWidget
from ._system_monitor import SystemMonitorWindow
from ._update_check import UpdateCheckThread
from ._window_geometry import maximize_without_saved_geometry, restore_geometry
from .settings import (
    PAGE_TITLES,
    SettingsContext,
    SettingsPage,
    SettingsResult,
    SettingsWindow,
    last_page,
)
from .settings._page import FOCUS_DETECTION
from .settings._window import SUB_PAGES

if TYPE_CHECKING:
    from .benchmark._benchmark_window import BenchmarkWindow
    from .setup import SetupResult


def _is_shown(widget: QWidget) -> bool:
    """Return whether *widget* still exists and is visible (a closed window is not)."""
    try:
        return widget.isVisible()
    except RuntimeError:  # its C++ object was deleted
        return False


def _app_icon() -> QIcon | None:
    """Return the best available icon for the current platform."""
    assets = Path(__file__).parent.parent / "assets"
    if sys.platform == "win32":
        candidates = [
            assets / "teachers_teammate.ico",
            assets / "teachers_teammate_icon.png",
            assets / "teachers_teammate.png",
        ]
    elif sys.platform == "darwin":
        candidates = [
            assets / "teachers_teammate.icns",
            assets / "teachers_teammate_icon.png",
            assets / "teachers_teammate.png",
        ]
    else:
        candidates = [
            assets / "teachers_teammate_icon.png",
            assets / "teachers_teammate.png",
            assets / "teachers_teammate.ico",
        ]
    for path in candidates:
        if path.exists():
            icon = load_icon(path)
            if not icon.isNull():
                return icon
    return None


#: The two texts the Check tab lets the teacher type in, as the store names them.

#: Whose computer a found server is on, as the readiness line says it.
_SERVER_PLACES = {
    ServerKind.THIS_COMPUTER: "this computer",
    ServerKind.WINDOWS_HOST: "the Windows computer",
    ServerKind.NETWORK: "another computer",
}

#: The kinds that mean "Ollama isn't answering": the line keeps looking while one of them
#: blocks the run, so an Ollama installed or started meanwhile turns it green by itself.
_OLLAMA_KINDS = frozenset(
    {IssueKind.OLLAMA_UNREACHABLE, IssueKind.OLLAMA_NOT_FOUND, IssueKind.OLLAMA_NOT_RUNNING}
)


class MainWindow(QMainWindow):
    """Teacher's Teammate main application window."""

    #: Something was installed: a package, a spaCy model, the reader's models.  Every installer
    #: emits it — the Settings window, the Add-ons page, the add-on dialog, the Setup
    #: Assistant's round, also when that round finishes after its window closed.
    packages_changed = Signal()

    def __init__(
        self,
        *,
        app_service: AppServices,
        worker_factory: Callable[..., WorkerLike] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Teacher's Teammate")
        self.resize(1500, 860)
        self._app_service = app_service
        self._worker_factory = worker_factory
        _icon = _app_icon()
        if _icon is not None:
            self.setWindowIcon(_icon)
        self._init_runtime_state()
        self._init_run_state()

        self._pull_manager.progress.connect(self._on_pull_progress)
        self._pull_manager.started.connect(self._on_pull_started)
        self._pull_manager.finished.connect(self._on_pull_finished)
        self._pull_manager.error.connect(self._on_pull_error)
        self.packages_changed.connect(self._on_packages_changed)

        self.setAcceptDrops(True)
        self._build_ui()
        self._setup_tray()

        self._load_credentials_from_settings()
        self._load_toml_if_present()

    def _init_menu_state(self) -> None:
        """Declare what the menu bar fills in and the tools open later."""
        self._settings_actions: dict[SettingsPage, QAction] = {}
        self._all_settings_action: QAction
        self._benchmark_action: QAction
        self._monitor_action: QAction
        self._setup_action: QAction
        #: The Benchmark window once opened (parentless, see ``_open_benchmark``).
        self._benchmark_window: BenchmarkWindow | None = None
        #: Whether the Setup Assistant is open, so a second click cannot open a second one.
        self._setup_open = False

    def _init_run_state(self) -> None:
        """Declare what the window needs for a run that is going: the clock, the notes, closing."""
        #: Set once the teacher agreed to stop the run and close (File → Quit asks itself).
        self._close_confirmed = False
        #: Under the progress bar; built in ``_build_left_panel``.
        self._awake_note: QLabel
        self._battery_note: QLabel

    def _init_runtime_state(self) -> None:
        """Initialise per-window runtime state and declare widgets built later."""
        self._config_path: Path | None = None
        self._pull_manager = PullManager(self, service=self._app_service)
        #: What the run in progress is doing: the file, the step, the pace and the Stop stage.
        self._run_session = RunSession()
        self._first_run_task = LatestTask(self)
        self._preprocess_preview: PreprocessPreview
        #: The Settings window while it is open (dialogs started from it use it as parent).
        self._settings_window: SettingsWindow | None = None
        self._init_menu_state()

        # Declared here; assigned in _build_widgets and _init_controllers.
        self._config_panel: ConfigPanel
        self._readiness_line: ReadinessLine
        self._export_controller: ExportController
        self._run_controller: RunController
        self._readiness_controller: ReadinessController
        self._check_controller: CheckController
        self._privacy_controller: PrivacyViewController
        # True while the requirement check of a Run / stage re-run is in flight.
        self._preflight_running = False
        # The same for the expected cache keys of the queue: they depend on the card.
        self._cache_state_task = LatestTask(self)
        self._update_thread: UpdateCheckThread | None = None
        self._declare_widgets()

    def _declare_widgets(self) -> None:
        """Declare the widgets ``_build_ui`` assigns, so the type checker sees them."""
        self._update_banner: QFrame
        self._monitor: SystemMonitorWindow
        self._stats: SystemStatsWidget
        self._run_btn: QPushButton
        self._stop_btn: QPushButton
        self._export_btn: QPushButton
        self._progress: QProgressBar
        self._tabs: QTabWidget
        self._log: LogWidget
        self._results_table: ResultsTable
        self._chart: ChartWidget
        self._main_splitter: QSplitter

    def _setup_tray(self) -> None:
        """Create and show the system-tray icon, falling back to a themed icon."""
        _style = self.style()
        assert _style is not None
        _icon_for_tray = _app_icon()
        _tray_icon = (
            _icon_for_tray
            if _icon_for_tray is not None
            else QIcon.fromTheme(
                "applications-science",
                _style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
            )
        )
        self._tray = QSystemTrayIcon(_tray_icon, self)
        self._tray.show()

    def _load_credentials_from_settings(self) -> None:
        """Inject stored provider API keys into os.environ (env var takes priority)."""
        env_keys = {
            str(self._app_service.providers.get_provider_info(provider).get("env_key", ""))
            for provider in self._app_service.providers.list_providers()
        }
        CredentialStore().load_into_environment(sorted(key for key in env_keys if key))

    def _build_ui(self) -> None:
        """Build the widgets, hand them to the controllers, then lay them out."""
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        self._update_banner = self._build_update_banner()
        root.addWidget(self._update_banner)

        self._build_widgets()
        self._init_controllers()
        root.addWidget(self._build_tabs(), 1)
        self._build_menu_bar()
        self._connect_pages()

        # No parent: the thread outlives the window if it is closed during the request.
        # The switch is read once, at start: turning it off takes effect at the next start.
        if self._config_panel.get_settings_dict().get(
            "check_for_updates", DEFAULTS["check_for_updates"]
        ):
            self._update_thread = UpdateCheckThread()
            self._update_thread.update_available.connect(self._show_update_banner)
            QTimer.singleShot(0, self._update_thread.start)

    def _build_widgets(self) -> None:
        """Build what the window shows and the controllers work on."""
        self._config_panel = ConfigPanel()
        self._config_panel.set_service(self._app_service)
        self._results_table = ResultsTable()
        self._log = LogWidget()
        # The tabs are filled once the controllers exist: the Check page is one of them.
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        _tab_bar_font = self._tabs.tabBar().font()
        _tab_bar_font.setPointSize(14)
        self._tabs.tabBar().setFont(_tab_bar_font)
        # Built now and kept hidden, so the chart collects the data of every run.  Parentless on
        # purpose (see SystemMonitorWindow): closeEvent closes it with this window.
        self._monitor = SystemMonitorWindow(self._app_service.recommendations.read_gpu_usage)
        self._chart = self._monitor.chart
        self._stats = self._monitor.stats

    def _run_hooks(self) -> RunHooks:
        """What the run asks of this window. Looked up when a run happens, not now."""
        return RunHooks(
            apply_lock=self._check_controller.apply_lock,
            update_check_tab=self._check_controller.update_tab,
            refresh_check_run=self._check_controller.refresh_run,
            sync_row=self._sync_row,
            # The two a test puts its own in the place of: looked up when the run gets there.
            load_check=lambda source_id: self._check_controller.load(source_id),  # noqa: PLW0108
            current_source_id=lambda: self._check_controller.current,
            queued_files=self._queued_files,
            save_settings=lambda **kwargs: self._save_settings(**kwargs),  # noqa: PLW0108
            show_documents=lambda: self._tabs.setCurrentIndex(0),
            enable_export=lambda: self._export_controller.set_enabled(True),
            notify=self._show_run_message,
            worker_factory=lambda: self._worker_factory,
            preflight_running=lambda: self._preflight_running,
        )

    def _readiness_hooks(self) -> ReadinessHooks:
        """What the readiness check asks of this window. Looked up when it asks, not now."""
        return ReadinessHooks(
            open_settings=lambda page, focus, preselect: self.open_settings(
                SettingsPage(page), focus=str(focus), preselect=list(preselect)
            ),
            save_settings=lambda **kwargs: self._save_settings(**kwargs),  # noqa: PLW0108
            queue_refresh=self._refresh_queue_from_inputs,
            provider_name=self._provider_display_name,
            log_line=self._log.append_text,
        )

    def _check_hooks(self) -> CheckHooks:
        """What the Check tab asks of this window. Looked up when it asks, not now."""
        return CheckHooks(
            sync_row=self._sync_row,
            run_busy=self._run_in_progress,
            warn_busy=lambda: self._run_controller.warn_busy(),  # noqa: PLW0108
            run_stage=lambda ids, stage: self._on_stage_run_requested(ids, stage),  # noqa: PLW0108
            open_settings=lambda page, focus: self.open_settings(page, focus=focus),
            privacy_available=lambda: self._privacy_controller.privacy_available,
            export=lambda ids, kind: self._export_controller.on_requested(ids, kind),  # noqa: PLW0108
        )

    def _init_controllers(self) -> None:
        """Build the controllers, each given the widgets it works on."""
        self._check_controller = CheckController(
            self,
            services=self._app_service,
            panel=self._config_panel,
            table=self._results_table,
            session=self._run_session,
            tabs=self._tabs,
            settings_store=self._settings_store,
            hooks=self._check_hooks(),
        )
        self._privacy_controller = PrivacyViewController(
            self,
            services=self._app_service,
            panel=self._config_panel,
            table=self._results_table,
            check=self._check_controller,
            settings_store=self._settings_store,
        )
        self._readiness_controller = ReadinessController(
            self,
            services=self._app_service,
            panel=self._config_panel,
            table=self._results_table,
            page=self._check_controller.page,
            hooks=self._readiness_hooks(),
        )
        self._readiness_line = self._readiness_controller.line
        self._run_controller = RunController(
            self,
            services=self._app_service,
            table=self._results_table,
            session=self._run_session,
            log=self._log,
            chart=self._chart,
            hooks=self._run_hooks(),
        )
        # The run's widgets are the controller's; the window lays them out and reads them.
        self._run_btn = self._run_controller.run_button
        self._stop_btn = self._run_controller.stop_button
        self._progress = self._run_controller.progress_bar
        self._awake_note = self._run_controller.awake_note
        self._battery_note = self._run_controller.battery_note
        self._run_btn.clicked.connect(self._on_run)
        self._stop_btn.clicked.connect(self._run_controller.on_stop_clicked)
        self._check_controller.page.stop_requested.connect(self._run_controller.on_stop_clicked)
        self._export_controller = ExportController(
            self,
            services=self._app_service,
            panel=self._config_panel,
            table=self._results_table,
        )
        self._export_controller.log_line.connect(self._log.append_text)
        self._preprocess_preview = PreprocessPreview(
            self, services=self._app_service, panel=self._config_panel, table=self._results_table
        )

    def _connect_pages(self) -> None:
        """Wire the panels and the pages to the window."""
        self._results_table.check_requested.connect(self._check_controller.open_document)
        self._results_table.stage_run_requested.connect(self._on_stage_run_requested)
        self._results_table.what_is_sent_requested.connect(
            self._privacy_controller.on_what_is_sent_requested
        )
        self._config_panel.data_flow_changed.connect(self._on_data_flow_changed)
        self._results_table.itemSelectionChanged.connect(self._readiness_controller.update)
        # The box rendered once while the panels were built, before these connections.
        self._on_data_flow_changed(self._config_panel.data_flow())
        self._results_table.export_requested.connect(self._export_controller.on_requested)
        self._config_panel.paths_changed.connect(self._refresh_queue_from_inputs)
        self._config_panel.anonymizer_configure_requested.connect(
            lambda _language, _config: self.open_settings(
                SettingsPage.PRIVACY, focus=FOCUS_DETECTION
            )
        )
        self._config_panel.results_changed.connect(self._on_results_changed)
        self._config_panel.instructions_changed.connect(self._on_instructions_changed)
        self._readiness_line.action_chosen.connect(self._readiness_controller.run_fix)
        self._privacy_controller.refresh_availability()
        # Kick off the first readiness check once the window is laid out.
        # Pass self as the receiver so the callback is cancelled if the window is destroyed.
        QTimer.singleShot(500, self, self._readiness_controller.start_check)

    def _build_tabs(self) -> QTabWidget:
        """The two tabs of the window: the work, and one document to check.

        The Check tab holds the preview panel full width, so the queue keeps its own tab.
        """
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.addWidget(self._build_left_panel())
        self._main_splitter.addWidget(self._build_right_panel())
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._tabs.addTab(self._main_splitter, "Documents")

        self._tabs.addTab(self._check_controller.page, "Check")
        self._tabs.setTabEnabled(1, False)  # nothing to check before a document has a result
        self._tabs.currentChanged.connect(self._check_controller.on_tab_changed)
        return self._tabs

    def _build_menu_bar(self) -> None:
        bar = self.menuBar()
        assert bar is not None
        self._build_file_menu(bar)
        self._build_settings_menu(bar)
        self._build_tools_menu(bar)

        help_menu = bar.addMenu("Help")
        assert help_menu is not None
        guide_act = QAction("User Guide\u2026", self)
        guide_act.triggered.connect(self._open_help_dialog)
        help_menu.addAction(guide_act)
        help_menu.addSeparator()
        licenses_act = QAction("Third-Party Licenses\u2026", self)
        licenses_act.triggered.connect(self._open_licenses_dialog)
        help_menu.addAction(licenses_act)
        help_menu.addSeparator()
        about_act = QAction("About Teacher\u2019s Teammate\u2026", self)
        about_act.triggered.connect(self._open_about_dialog)
        help_menu.addAction(about_act)

    def _build_file_menu(self, bar: Any) -> None:
        """File: open documents, export results, quit."""
        file_menu = bar.addMenu("File")
        assert file_menu is not None
        open_folder_act = QAction("Open Folder\u2026", self)
        # The panel does not exist yet when the menu bar is built, hence the lambdas.
        open_folder_act.triggered.connect(
            lambda: self._config_panel.select_folder()  # noqa: PLW0108
        )
        file_menu.addAction(open_folder_act)
        open_files_act = QAction("Open Files\u2026", self)
        open_files_act.triggered.connect(
            lambda: self._config_panel.select_files()  # noqa: PLW0108
        )
        file_menu.addAction(open_files_act)
        export_menu, kinds = build_export_menu(file_menu)
        export_menu.triggered.connect(
            lambda action: self._export_controller.on_toolbar_action(kinds[action])
        )
        file_menu.addMenu(export_menu)
        # Enabled together with the Export button: there is nothing to export before a run.
        self._export_controller.add_actions(list(kinds))
        file_menu.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.setShortcut(QKeySequence(QKeySequence.StandardKey.Quit))
        quit_act.setMenuRole(QAction.MenuRole.QuitRole)
        quit_act.triggered.connect(self._on_quit_requested)
        file_menu.addAction(quit_act)

    def _build_settings_menu(self, bar: Any) -> None:
        """Settings: one entry per main page of the Settings window, and "All settings…"."""
        settings_menu = bar.addMenu("Settings")
        assert settings_menu is not None
        tooltips = {
            SettingsPage.GENERAL: (
                "How the app behaves while it works, for example keeping the computer awake "
                "during a run."
            ),
            SettingsPage.AI_MODELS: "Which AI reads the handwriting, proofreads and reviews.",
            SettingsPage.AI_SETUP: (
                "What the AI models need from this computer: how powerful it is and where "
                "the local AI server is."
            ),
            SettingsPage.ONLINE_SERVICES: (
                "The access keys (API keys) of the online AI services you can use."
            ),
            SettingsPage.SCAN_QUALITY: "How scans are cleaned up before the text is read.",
            SettingsPage.PRIVACY: (
                "What may leave this computer, and how names and other personal information "
                "are found and replaced."
            ),
            SettingsPage.ADDONS: "Download AI models and install optional parts.",
        }
        for page in SettingsPage:
            if page in SUB_PAGES:
                continue  # reached from AI models and in the window's sidebar, not from here
            action = QAction(f"{PAGE_TITLES[page]}\u2026", self)
            action.setToolTip(tooltips[page])
            action.triggered.connect(lambda _checked=False, p=page: self.open_settings(p))
            settings_menu.addAction(action)
            self._settings_actions[page] = action
        settings_menu.addSeparator()
        all_act = QAction("All settings\u2026", self)
        all_act.setShortcut(QKeySequence("Ctrl+,"))
        all_act.setMenuRole(QAction.MenuRole.PreferencesRole)
        all_act.triggered.connect(lambda: self.open_settings(last_page()))
        settings_menu.addAction(all_act)
        self._all_settings_action = all_act

    def _build_tools_menu(self, bar: Any) -> None:
        """Tools: things that are not part of a batch (Benchmark, System Monitor, Setup)."""
        tools_menu = bar.addMenu("Tools")
        assert tools_menu is not None
        benchmark_act = QAction("Benchmark\u2026", self)
        benchmark_act.setToolTip(
            "Try a document with different text-recognition settings and compare the results."
        )
        benchmark_act.triggered.connect(self._open_benchmark)
        tools_menu.addAction(benchmark_act)
        monitor_act = QAction("System Monitor", self)
        monitor_act.setToolTip("How busy this computer is, and how long each document took.")
        monitor_act.triggered.connect(self._open_system_monitor)
        tools_menu.addAction(monitor_act)
        tools_menu.addSeparator()
        setup_act = QAction("Setup Assistant\u2026", self)
        setup_act.setToolTip("Answer a few questions to set up the AI for this computer.")
        setup_act.triggered.connect(lambda _checked=False: self._open_setup_assistant())
        tools_menu.addAction(setup_act)
        self._benchmark_action = benchmark_act
        self._monitor_action = monitor_act
        self._setup_action = setup_act

    def _open_system_monitor(self) -> None:
        self._monitor.show_and_raise()

    def _open_benchmark(self) -> None:
        """Show the Benchmark window; a second click brings the open one to the front.

        The window is parentless on purpose: it is a whole app that the teacher arranges next
        to this one, and an owned window would stay on top of it on Windows.  It reads the
        saved settings (server, profile) when it is created, and again each time it is shown
        after it was closed.
        """
        if self._benchmark_window is None:
            from .benchmark.composition import (  # noqa: PLC0415  # loaded on demand
                build_benchmark_window,
            )

            self._benchmark_window = build_benchmark_window(app_services=self._app_service)
        elif not self._benchmark_window.isVisible():
            self._benchmark_window.reload_settings()  # closed since: the settings may differ now
        window = self._benchmark_window
        window.show()
        window.raise_()
        window.activateWindow()

    def _open_help_dialog(self) -> None:
        HelpDialog(self).exec()

    def _open_licenses_dialog(self) -> None:
        ThirdPartyLicensesDialog(self).exec()

    def _open_about_dialog(self) -> None:
        AboutDialog(self).exec()

    def _build_left_panel(self) -> QWidget:
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._config_panel.open_settings_requested.connect(
            lambda page, focus, preselect: self.open_settings(
                SettingsPage(page), focus=str(focus), preselect=list(preselect)
            )
        )
        layout.addWidget(self._config_panel)

        # Whether a run can start, and what to press if it can't -- right above Run.
        layout.addWidget(self._readiness_line)

        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        self._export_btn = self._export_controller.create_button()
        btn_layout.addWidget(self._run_btn)
        btn_layout.addWidget(self._stop_btn)
        btn_layout.addWidget(self._export_btn)
        layout.addWidget(btn_row)

        layout.addWidget(self._progress)
        layout.addWidget(self._awake_note)
        layout.addWidget(self._battery_note)

        left.setMinimumWidth(340)
        left.setMaximumWidth(420)
        return left

    def _build_right_panel(self) -> QWidget:
        results_splitter = QSplitter(Qt.Orientation.Vertical)
        results_splitter.addWidget(self._results_table)
        results_splitter.addWidget(self._log)
        results_splitter.setStretchFactor(0, 3)
        results_splitter.setStretchFactor(1, 1)
        return results_splitter

    # ── Settings persistence ──────────────────────────────────────────────

    #: How long a drag of a pane waits before the layout is written to the settings.
    _QSETTINGS_ORG = "TeachersTeammate"
    _QSETTINGS_APP = "TeachersTeammate"

    def _restore_gui_state(self) -> None:
        """Restore the window geometry and the splitter sizes from QSettings."""
        qs = QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP)
        if not restore_geometry(self, qs.value("gui/geometry")):
            # The first start: the window is as large as the screen allows.  A saved geometry,
            # normal or maximized, always wins over this.
            maximize_without_saved_geometry(self)
        sizes = [360, 1140]
        raw_sizes = qs.value("gui/panel_sizes")
        if isinstance(raw_sizes, list) and len(raw_sizes) == 2:
            try:
                restored = [int(v) for v in raw_sizes]
                if restored[0] >= 200 and restored[1] >= 400:
                    sizes = restored
            except ValueError, TypeError:
                pass
        QTimer.singleShot(0, lambda s=sizes: self._main_splitter.setSizes(s))

    def _load_toml_if_present(self) -> None:
        config_path = self._app_service.setup.resolve_config_path(None)
        self._restore_gui_state()

        installer_choices = self._app_service.setup.first_start_settings(config_path)
        if installer_choices is not None or config_path is None:
            # First start (no ocr.toml yet, or only what the installer wrote): the assistant
            # asks what only the teacher knows and works the models out from the answers.  It
            # opens on top of this window, so the teacher sees the app first.  What the
            # installer chose is loaded first, so the first save keeps it.
            if installer_choices and config_path is not None:
                self._config_path = config_path
                self._config_panel.load_from_dict(installer_choices)
            QTimer.singleShot(0, self._open_setup_assistant_first_start)
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
            self._config_panel.load_from_dict(values)
            # Signals fired during bulk field restoration can run queue refresh
            # before all dependent settings (e.g. docx_enabled) are applied. Run one
            # final refresh after the full config is loaded so cached state is
            # shown consistently on startup.
            self._refresh_queue_from_inputs()
            self._log.append_text(f"Loaded settings from {config_path}\n")
            for notice in loaded.notices:
                self._show_setting_notice(notice)
        except Exception as exc:  # noqa: BLE001  # config file may be missing, malformed, or use an unexpected type; log the warning and continue
            self._log.append_text(f"WARNING: Could not load {config_path}: {exc}\n")

    def _show_setting_notice(self, setting_notice: SettingNotice) -> None:
        """Say what the app changed about a saved setting instead of changing it silently.

        A replaced text-recognition engine changes what the next run does, so it interrupts
        the teacher; the other notices are in the log.
        """
        notice = dlg.setting_notice(setting_notice)
        self._log.append_text(f"NOTICE: {notice.body}\n")
        if setting_notice.kind is SettingNoticeKind.ENGINE_REPLACED:
            _dialogs.notify(self, notice)

    def _open_setup_assistant_first_start(self) -> None:
        """Open the assistant on a first start, on top of the window that just appeared."""
        self._open_setup_assistant(first_start=True)

    def _open_setup_assistant(self, *, first_start: bool = False) -> None:
        """Ask the questions a first start needs, then apply the setup plan the teacher agreed to.

        On a first start the assistant starts on the system language, because there is no
        saved one yet; from the Tools menu it starts on the settings as they are.  Cancel on a
        first start leaves today's setup in place (the best models for this computer and the
        system language); from the Tools menu it changes nothing.
        """
        if self._setup_open:
            return
        from .setup import SetupAssistant, SetupContext  # noqa: PLC0415  # loaded on demand

        language = ""
        if first_start:
            language = (
                self._app_service.setup.system_language() or self._config_panel.lookup_language()
            )
        context = SetupContext(
            settings=self._config_panel.get_settings_dict(),
            app_service=self._app_service,
            pull_manager=self._pull_manager,
            language=language,
            privacy_available=self._privacy_controller.privacy_available,
            first_start=first_start,
            display_anonymized=self._privacy_controller.display_anonymized,
        )
        assistant = SetupAssistant(context, self)
        assistant.committed.connect(self._apply_setup_result)
        # The round outlives the assistant: a download that finishes after the window closed
        # must still tell this window that packages changed.
        install_round = assistant.install_round()
        install_round.finished.connect(self._on_install_round_finished)
        self._setup_open = True
        try:
            assistant.exec()
        finally:
            self._setup_open = False
        # The round was handed to this window so it can finish after the assistant: it goes
        # when it is over, or at once when nothing is running.
        if install_round.running():
            install_round.finished.connect(install_round.deleteLater)
        else:
            install_round.deleteLater()
        wants_documents = assistant.documents_wanted()
        # Set up decides, not how the window closed: the X on the done page is a reject too.
        was_set_up = assistant.is_committed()
        assistant.deleteLater()
        if first_start and not was_set_up:
            self._apply_first_start_defaults()
        if wants_documents:
            self._config_panel.select_folder()

    def _apply_first_start_defaults(self) -> None:
        """Leave a first start with a working setup when the assistant was cancelled.

        The system language goes into the panel, because no settings file names one yet; then
        today's first start runs: the best models for this computer, once detection finishes.
        """
        language = self._app_service.setup.system_language()
        if language:
            self._config_panel.load_from_dict({"language": language})
            self._save_settings(notify=False)
        self._apply_first_start_model_settings()

    def _apply_setup_result(self, result: SetupResult) -> None:
        """Take over what the assistant set up, then save once.

        The main-window keys go through ``load_from_dict``, which skips the stage guard: the
        models are still downloading, so a guard would untick Proofread and open a dialog.
        """
        self._config_panel.update_settings(result.settings)
        self._config_panel.load_from_dict(result.panel)
        if result.display_anonymized is not None:
            # The name detection may still be on its way: the wish is stored as it is, and the
            # view follows it as soon as hiding is possible (refresh_availability).
            self._privacy_controller.store_display_anonymized(result.display_anonymized)
        self._save_settings(notify=False)
        self._readiness_controller.start_check()
        self._refresh_queue_from_inputs()

    def _apply_first_start_model_settings(self) -> None:
        """Start first-start hardware detection in the background (AC12/AC13)."""
        service = self._app_service
        ollama_url = self._current_ollama_url()
        # Detection must never get in the way of starting the app: any failure
        # simply leaves the static defaults in place.
        self._first_run_task.submit(
            lambda: service.recommendations.detect_hardware(ollama_url),
            self._on_first_start_detection,
        )

    def _on_first_start_detection(self, detection: object, error: BaseException | None) -> None:
        """Replace untouched static defaults with the BEST models for the profile."""
        if error is not None or not isinstance(detection, HardwareDetection):
            return
        settings = self._config_panel.get_settings_dict()
        best = self._app_service.recommendations.first_run_model_settings(
            detection.profile,
            system_memory_gb=detection.system_memory_gb,
            card_gb=detection.card_gb,
        )
        apply: dict[str, str] = {}
        if (
            settings.get("ocr_engine") == "ollama"
            and settings.get("ocr_model") == DEFAULTS["ocr_model"]
        ):
            apply["ocr_model"] = best["ocr_model"]
        if (
            settings.get("correction_provider") == "ollama"
            and settings.get("correction_model") == DEFAULTS["correction_model"]
        ):
            apply["correction_model"] = best["correction_model"]
        if (
            settings.get("evaluate_provider") == "ollama"
            and settings.get("evaluate_model") == DEFAULTS["evaluate_model"]
        ):
            apply["evaluate_model"] = best["evaluate_model"]
        if not apply:
            return
        self._config_panel.update_settings(apply)
        ocr_model = apply.get("ocr_model", str(settings.get("ocr_model", "")))
        general_model = apply.get("correction_model") or apply.get(
            "evaluate_model", best["correction_model"]
        )
        self._log.append_text(
            f"First start: chose reading model {ocr_model} and writing model "
            f"{general_model} for your computer ({profile_text(detection.profile)}). "
            f"You can download them under {WHERE_ADDONS}.\n"
        )
        self._save_settings(notify=False)
        self._readiness_controller.start_check()

    def _save_settings(self, *, notify: bool = True, silent_on_error: bool = False) -> None:
        # Persist window geometry and splitter sizes to QSettings (UI state, not pipeline config)
        qs = QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP)
        qs.setValue("gui/geometry", self.saveGeometry())
        qs.setValue("gui/panel_sizes", self._main_splitter.sizes())

        values = self._config_panel.to_dict()
        out = (
            self._config_path
            if self._config_path is not None
            else self._app_service.setup.default_config_path()
        )
        try:
            out.write_text(dump_config_file(values), encoding="utf-8")
            if notify:
                self._log.append_text(f"Settings saved to {out.resolve()}\n")
        except OSError as exc:
            if not silent_on_error:
                _dialogs.notify(self, dlg.save_failed(str(exc)))

    def _confirm_close_during_run(self) -> bool:
        """Ask whether to stop the run and close; the default answer keeps the run going."""
        return _dialogs.ask(self, dlg.close_during_run())

    def _on_quit_requested(self) -> None:
        """File → Quit: ask first when a run is going, then close like the window's X."""
        if self._run_controller.active and not self._confirm_close_during_run():
            return
        self._close_confirmed = True
        self.close()

    def closeEvent(self, event: Any) -> None:
        if (
            self._run_controller.active
            and not self._close_confirmed
            and event.spontaneous()
            and not self._confirm_close_during_run()
        ):
            event.ignore()
            return
        try:
            self._save_settings(notify=False, silent_on_error=True)
        except Exception:  # noqa: BLE001  # never block window close on a settings-save error
            pass
        self._check_controller.flush_layout()
        # Only close() runs the Benchmark window's own closeEvent, which stops its worker;
        # deleting it with a parent would not.  Closing the last window ends the app.
        if self._benchmark_window is not None:
            self._benchmark_window.close()
        # The System Monitor is parentless too: closing the last visible window ends the app.
        self._monitor.close()
        self._first_run_task.cancel()
        self._readiness_controller.shutdown()
        # Let the window disappear before waiting: running background tasks are
        # given a moment to end, and the user should not see a frozen window.
        self.hide()
        self._privacy_controller.shutdown()
        self._run_controller.stop_for_close()
        wait_for_background_tasks()
        event.accept()

    # ── Run / Stop ────────────────────────────────────────────────────────

    def _show_run_message(self, text: str, warning: bool) -> None:
        """Say how the run ended in the notification area, with the icon that fits it."""
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if warning
            else QSystemTrayIcon.MessageIcon.Information
        )
        self._tray.showMessage("Teacher's Teammate", text, icon, 4000)

    def _get_file_names(self, config: Config) -> list[str]:
        """Return queued file names via application-layer file discovery."""
        return [file.name for file in self._app_service.run.collect_input_files(config)]

    def _scan_input_files(self) -> list[Path]:
        """Scan the input folder through the run service while the config is incomplete."""
        selected = self._config_panel.get_selected_files()
        if selected is not None:
            return selected
        input_text = self._config_panel.get_input_dir()
        if not input_text:
            return []
        input_dir = Path(input_text)
        if not input_dir.is_dir():
            return []
        return self._app_service.run.collect_input_files_from_dir(input_dir, recursive=True)

    def _refresh_queue_from_inputs(self) -> None:
        """Refresh queue from input selection without showing modal validation errors."""
        files = self._scan_input_files()

        try:
            config = self._config_panel.to_config()
        except ValueError:
            # A setting that is not valid yet (no output folder) must not hide stored results:
            # the lookup config names the same state folder and needs no valid folder.
            config = self._config_panel.lookup_config()
        else:
            self._ensure_hardware_known_for_cache_state(config)
        rows = self._app_service.documents.queue_rows(config, files)
        self._results_table.set_queue(rows)
        if any(row.ocr is StageState.DONE for row in rows):
            self._export_controller.set_enabled(True)
        # The readiness line says what is in the queue and what to do next; the bar only
        # reports progress, so it stays "Idle" until something runs.
        if not self._run_in_progress():
            self._run_controller.show_idle()
        self._check_controller.sync_with_queue()
        self._check_controller.update_tab()
        self._readiness_controller.update()
        # Other documents may need another engine (text files need none, recognised ones
        # no more recognition): ask again what a run over them requires.
        self._readiness_controller.schedule_check()

    def _queued_files(self, source_ids: list[str] | None = None) -> list[Path]:
        """Return the queued files of *source_ids* (``None``: the whole queue)."""
        return self._results_table.files(source_ids)

    def _recognised_files(self) -> set[Path]:
        """Return the queued files whose recognised text is stored.

        A run does not read them again, so a missing text recognition engine must not stop a
        run over only these.
        """
        return self._results_table.recognised_files()

    def _recognition_needed(self, source_ids: list[str] | None = None) -> bool:
        """Return whether a run over *source_ids* (default: the whole queue) reads pages."""
        return self._app_service.run.needs_text_recognition(
            self._queued_files(source_ids), recognised=self._recognised_files()
        )

    def _run_in_progress(self) -> bool:
        """Return whether a requirement check or an OCR worker is already active."""
        return self._run_controller.busy

    def _on_run(self) -> None:
        if self._run_in_progress():
            return
        self._run_session.from_check = False
        try:
            config = self._config_panel.to_config()
        except ValueError as exc:
            _dialogs.notify(self, dlg.configuration_error(str(exc)))
            return
        selected = self._results_table.selected_source_ids()
        if not selected and self._results_table.queued_source_ids():
            return _dialogs.notify(self, dlg.no_files_selected())
        self._check_requirements_then(
            config,
            lambda preflight: self._confirm_teacher_work_then(
                config, preflight.worked_on, lambda: self._continue_run(config)
            ),
            recognition=self._recognition_needed(selected or None),
            files=self._queued_files(selected or None),
            ask_about_work=True,
        )

    def _check_requirements_then(
        self,
        config: Config,
        on_ready: Callable[[RunPreflight], None],
        *,
        recognition: bool = True,
        files: list[Path] | None = None,
        ask_about_work: bool = False,
    ) -> None:
        """Check the requirements of *config* off the GUI thread, then call *on_ready*.

        *on_ready* receives the preflight result, so a caller can ask about what the run would
        replace before it starts.

        *recognition* says whether the run reads pages; without it the text recognition
        engine is not required (see ``check_run_preflight``).  *files* are the documents the
        run takes: with text recognition switched off, a run over scans only is refused.
        *ask_about_work* also finds the documents whose corrections the teacher worked on (it
        reads the files), which only a check before a run needs.

        The checks contact Ollama, so they must not block the GUI.  Until the result is
        in, Run is disabled and :meth:`_run_in_progress` reports busy, so no second
        worker can be started from the Run button or the right-click menu.
        """
        refusal = self._app_service.run.output_refusal_for_run(config)
        if refusal:
            _dialogs.notify(self, dlg.output_folder_refused(refusal))
            return
        self._preflight_running = True
        self._run_btn.setEnabled(False)
        self._run_controller.show_checking()
        service = self._app_service
        recognised = self._recognised_files()
        run_in_background(
            lambda: service.run.check_run_preflight(
                config,
                recognition=recognition,
                files=files,
                recognised=recognised,
                ask_about_work=ask_about_work,
            ),
            lambda result, error: self._on_preflight_done(
                lambda preflight: self._confirm_data_flow_then(config, lambda: on_ready(preflight)),
                result,
                error,
            ),
            owner=self,
        )

    def _on_preflight_done(
        self,
        on_ready: Callable[[RunPreflight], None],
        result: object,
        error: BaseException | None,
    ) -> None:
        """Show the first unmet requirement, or continue with *on_ready*."""
        self._preflight_running = False
        self._run_btn.setEnabled(True)
        self._run_controller.show_idle()
        if error is not None or not isinstance(result, RunPreflight):
            # The check itself failed: do not block; the pipeline reports real problems.
            on_ready(RunPreflight())
            return
        if result.blocked:
            self._show_blocking_preflight(result)
            return
        note = wait_detail_text(result.ollama_wait)
        if note:
            # A note, not a question: the run works, the first file just waits.
            self._log.append_text(note + "\n")
        if result.memory_warning:
            self._confirm_memory_warning(result.memory_warning, lambda: on_ready(result))
            return
        on_ready(result)

    def _confirm_memory_warning(self, warning: str, on_ready: Callable[[], None]) -> None:
        """Ask before a run whose models do not fit the memory that is free now.

        A warning rather than a refusal, because free memory changes from run to run — so
        there is no "don't ask again" here, and Cancel is the default button: Enter must not
        start a run that could make the computer swap.
        """
        if _dialogs.ask(self, dlg.not_enough_memory(warning)):
            on_ready()

    def _show_blocking_preflight(self, result: RunPreflight) -> None:
        """Explain the requirement that stops the run, with a button for each way to fix it."""
        self._readiness_controller.show_blocking(result)

    def _settings_store(self) -> QSettings:
        """Return a QSettings for the window's GUI preferences (not pipeline settings)."""
        return QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP)

    def _acknowledgements(self) -> FlowAcknowledgements:
        """Return the store of "don't ask again" answers (read and written through QSettings)."""
        return FlowAcknowledgements(lambda: QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP))

    def _confirm_data_flow_then(self, config: Config, on_ready: Callable[[], None]) -> None:
        """Ask once before a run that sends content away from this computer."""
        flow = self._app_service.data_flow.describe_data_flow(config)
        if not flow.leaves_computer or flow.fingerprint in self._acknowledgements().fingerprints():
            on_ready()
            return
        question = dlg.documents_leave_computer(
            level_text(flow.level),
            [(stage.title, stage.badge) for stage in flow.outbound],
            [advice_text(tip) for tip in flow.advice],
        )
        answer = _dialogs.choose(self, question)
        if not answer.accepted:
            return
        if answer.checked:
            self._acknowledgements().remember(flow)
        on_ready()

    def _confirm_teacher_work_then(
        self, config: Config, worked_on: WorkedOn, on_ready: Callable[[], None]
    ) -> None:
        """Ask what to do with corrections the teacher worked on before a run replaces them.

        *Keep my changes* is the default: it records the answer for these settings, so the same
        settings never ask again and the run keeps the proofreading.  *Proofread again* drops
        those proofreadings, and with them the corrections written on them.
        """
        if not worked_on.unanswered and not worked_on.unbound:
            on_ready()
            return
        question = dlg.worked_on_before_run(
            [path.name for path in worked_on.unanswered],
            [path.name for path in worked_on.unbound],
        )
        answer = _dialogs.choose(self, question)
        if answer.choice is None or answer.choice.declines:
            return
        service = self._app_service
        if answer.choice.id == dlg.KEEP_MY_CHANGES:
            service.commands.keep_teacher_work(config, files=worked_on.unanswered)
        elif answer.choice.id == dlg.PROOFREAD_AGAIN:
            failures = service.commands.invalidate_from_stage(
                config, files=worked_on.unanswered, stage="correction"
            )
            if failures:
                _dialogs.notify(self, dlg.cache_invalidation_failed(list(failures)))
                return
        # The teacher was told that a new proofreading will not contain these corrections and
        # went on: they belong to no stored answer, so they are not asked about again.
        service.commands.drop_unbound_corrections(config, files=worked_on.unbound)
        on_ready()

    def _continue_run(self, config: Config) -> None:
        """Queue the files and start the worker once all requirements are met."""
        queued_source_ids = self._results_table.queued_source_ids()
        queue_was_empty = not queued_source_ids
        if queue_was_empty:
            queued_files = self._app_service.run.collect_input_files(config)
            queued_source_ids = [str(file.resolve()) for file in queued_files]
            self._results_table.set_queue(
                self._app_service.documents.queue_rows(config, queued_files)
            )

        if not queued_source_ids:
            _dialogs.notify(self, dlg.no_files_found(str(config.input_dir)))
            return

        selected_source_ids = self._results_table.selected_source_ids()
        if not selected_source_ids and queue_was_empty:
            selected_source_ids = queued_source_ids
        if not selected_source_ids:
            _dialogs.notify(self, dlg.no_files_selected())
            return

        self._run_controller.start(config, selected_source_ids)

    def _on_stage_run_requested(self, selected_source_ids: list[str], stage: str) -> None:
        """Check the requirements, then invalidate cache from *stage* onward and run rows."""
        if self._run_in_progress():
            self._run_controller.warn_busy()
            return
        try:
            config = self._config_panel.to_config()
        except ValueError as exc:
            _dialogs.notify(self, dlg.configuration_error(str(exc)))
            return

        # One stage by hand: the service builds that config, so the GUI holds no variant of
        # its own that could drift from what a run does.
        config = self._app_service.run.config_for_stage_run(config, stage)

        # Check before touching the cache: a re-run that cannot work (Ollama stopped, model
        # missing) must not throw away the results that are there.
        # Re-running recognition reads the pages again; the later stages work on the stored text.
        self._check_requirements_then(
            config,
            lambda _preflight: self._run_stage(config, selected_source_ids, stage),
            recognition=stage == "ocr" or self._recognition_needed(selected_source_ids),
            files=self._queued_files(selected_source_ids),
        )

    def _run_stage(self, config: Config, selected_source_ids: list[str], stage: str) -> None:
        """Ask what an explicit re-run would drop, then invalidate the cache and start the worker."""
        # A typed edit is stored first: after the invalidation it would be stored on top of the
        # state the run is about to work on, and a run would redo or skip the wrong thing.
        self._check_controller.page.flush_pending_edit()
        if stage == "evaluation":
            # A content review is made from the corrected text; nothing of the teacher's goes.
            self._invalidate_and_run(config, selected_source_ids, stage)
            return
        # The question needs the files hashed, which is not work for the GUI thread.
        files = self._queued_files(selected_source_ids)
        service = self._app_service
        run_in_background(
            lambda: service.documents.worked_on_documents(
                config, files, run_proofreads=stage == "correction"
            ),
            lambda result, error: self._confirm_rerun_then(
                config, selected_source_ids, stage, result, error
            ),
            owner=self,
        )

    def _confirm_rerun_then(
        self,
        config: Config,
        selected_source_ids: list[str],
        stage: str,
        result: object,
        error: BaseException | None,
    ) -> None:
        """Ask before an explicit re-run drops what the teacher worked on, then run it.

        If what they worked on cannot be found out, nothing is dropped: the question cannot be
        asked, so the answer cannot be taken for granted.
        """
        if error is not None or not isinstance(result, WorkedOn):
            _dialogs.notify(self, dlg.could_not_check_work())
            return
        if stage == "correction":
            names = result.worked_on
        else:
            # Reading again takes both parts of the work: the transcription and the corrections.
            names = tuple(dict.fromkeys((*result.transcribed, *result.worked_on)))
        if names:
            question = (
                dlg.proofread_again([path.name for path in names])
                if stage == "correction"
                else dlg.read_again([path.name for path in names])
            )
            if not _dialogs.ask(self, question):
                return
        self._invalidate_and_run(config, selected_source_ids, stage)

    def _invalidate_and_run(
        self, config: Config, selected_source_ids: list[str], stage: str
    ) -> None:
        """Invalidate the cache from *stage* onward and start the worker on the rows."""
        files = self._queued_files(selected_source_ids)
        if not files:
            _dialogs.notify(self, dlg.selection_mismatch())
            return

        failures = self._app_service.commands.invalidate_from_stage(
            config, files=files, stage=stage
        )
        if failures:
            _dialogs.notify(self, dlg.cache_invalidation_failed(list(failures)))
            return

        self._run_controller.start(config, [str(file.resolve()) for file in files], files)

    def _restore_all_settings(self) -> None:
        """Reset every setting to its default and save (Settings already asked twice)."""
        self._config_panel.restore_defaults()
        # Not part of the settings file: the "hide personal information" view and the
        # remembered "Don't ask again" answers (so the next run asks before sending).
        self._privacy_controller.apply_display_anonymized(DISPLAY_ANONYMIZED_DEFAULT)
        self._acknowledgements().forget_all()
        self._check_controller.page.forget_anonymization()
        self._save_settings(notify=False)
        self._readiness_controller.start_check()
        self._refresh_queue_from_inputs()

    def _on_results_changed(self) -> None:
        """Step "Results" changed: save it, and show what is cached for the new folder."""
        self._save_settings(notify=False)
        # Also asks the readiness check again ("Text file, anonymized" needs name detection).
        self._refresh_queue_from_inputs()

    def _on_instructions_changed(self) -> None:
        """The proofreading instructions changed: save them, and re-count the parts."""
        self._save_settings(notify=False)

    # ── Worker signal handlers ────────────────────────────────────────────

    def _sync_row(self, source_id: str) -> None:
        """Show what the store now says about *source_id* in its row.

        The row is the store's answer, so every action that changes a document ends here
        instead of patching cells: a stage that ran, an edit, a *Checked* mark.
        """
        source = Path(source_id)
        try:
            config = self._config_panel.to_config()
        except ValueError:
            # A setting that is not valid yet (no output folder) must not hide stored results:
            # the lookup config names the same state folder and needs no valid folder.
            config = self._config_panel.lookup_config()
        try:
            row = self._app_service.documents.queue_row(config, source)
        except OSError, ValueError:
            return  # nothing readable: the row keeps what it showed
        self._results_table.update_row(row)

    # ── Readiness line ────────────────────────────────────────────────────

    def _provider_display_name(self, provider: str) -> str:
        """Return the name the teacher knows *provider* by."""
        info = self._app_service.providers.get_provider_info(provider)
        return str(info.get("display_name") or provider.title())

    def changeEvent(self, event: QEvent) -> None:
        """Look again when the window becomes active: something may have been installed.

        A key set outside the app, ``ollama pull`` run in a terminal, or a spaCy model
        installed with pip is only seen then.
        """
        super().changeEvent(event)
        if event.type() != QEvent.Type.ActivationChange or not self.isActiveWindow():
            return
        self._app_service.anonymization.reread_installed_models()
        self._privacy_controller.refresh_availability()
        if self._readiness_controller.blocker is not None:
            self._readiness_controller.start_check(fresh=True)

    # ── Ollama pull progress (status bar area) ────────────────────────────────────

    def _on_pull_started(self, model_name: str) -> None:
        self._readiness_controller.show_download(model_name)

    def _on_pull_progress(self, _status: str, completed: int, total: int) -> None:
        self._readiness_controller.set_download_progress(completed, total)

    def _on_pull_finished(self, _model_name: str) -> None:
        # Also covers pulls that finish after the Settings window was closed:
        # without this the cached model list stays stale for minutes.
        self._app_service.recommendations.on_ollama_models_changed(
            self._pull_manager.current_url or self._current_ollama_url()
        )
        self._end_download()

    def _on_pull_error(self, msg: str) -> None:
        self._readiness_controller.model_download_failed(msg)

    def _end_download(self) -> None:
        """Hand the line back; the model that was missing may be there now."""
        self._readiness_controller.end_download()

    # ── Drag-and-drop ─────────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        urls = mime.urls() if mime is not None else []
        if not urls:
            return
        paths = [Path(url.toLocalFile()) for url in urls]
        if len(paths) == 1 and paths[0].is_dir():
            self._config_panel.set_input_dir(str(paths[0]))
        elif all(p.is_file() for p in paths):
            self._config_panel.set_selected_files(paths)
        else:
            self._config_panel.set_input_dir(str(paths[0].parent))

    def _ensure_hardware_known_for_cache_state(self, config: Config) -> None:
        """Probe this computer in the background when the cache keys need to know the card.

        With "My computer" on a local server, the context window a run uses — and so the cache
        key of its results — depends on the card.  Until this computer has been probed the keys
        are worked out for the base window; once it has, the queue is shown again with the
        real ones.  The probe never runs on the GUI thread.
        """
        if (
            self._app_service.recommendations.cached_hardware_profile(
                config.hardware_profile, config.ollama_url
            )
            is not None
        ):
            return

        def detect() -> object:
            return self._app_service.recommendations.detect_hardware(config.ollama_url)

        def done(_result: object, error: BaseException | None) -> None:
            # A failed probe keeps the base window; asking again would only fail again.
            if error is None:
                self._refresh_queue_from_inputs()

        self._cache_state_task.submit(detect, done)

    def _on_data_flow_changed(self, flow: object) -> None:
        """Say under the proofreading and content-review cells where their text goes."""
        if not isinstance(flow, DataFlow):
            return
        self._results_table.set_stage_tooltips(stage_tooltips(flow))
        self._privacy_controller.refresh_availability()  # the document language may have changed
        self._readiness_controller.schedule_check()  # ... and what a run needs with it

    def _build_update_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("UpdateBanner")
        # Never grow taller than the content: the splitter below takes all spare height.
        banner.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        banner.setStyleSheet(
            "#UpdateBanner { background: #6a5200; border-radius: 4px; padding: 2px; }"
        )
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(8, 4, 8, 4)
        lbl = QLabel()
        lbl.setObjectName("UpdateBannerLabel")
        lbl.setOpenExternalLinks(True)
        layout.addWidget(lbl, 1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setFlat(True)
        close_btn.clicked.connect(banner.hide)
        layout.addWidget(close_btn)
        banner.hide()
        return banner

    def _show_update_banner(self, version: str, url: str) -> None:
        lbl = self._update_banner.findChild(QLabel, "UpdateBannerLabel")
        if lbl is not None:
            lbl.setText(
                f"Version <b>{version}</b> is available — "
                f'<a href="{url}" style="color:#ffd966;">Download</a>'
                f" · turn this check off under {WHERE_GENERAL}"
            )
        self._update_banner.show()

    def _current_ollama_url(self) -> str:
        return str(self._config_panel.get_settings_dict().get("ollama_url", DEFAULTS["ollama_url"]))

    # ── Settings window ───────────────────────────────────────────────────

    def _dialog_parent(self) -> QWidget:
        """Return the window a new dialog belongs to: the open Settings window, else this one."""
        window = self._settings_window
        if window is not None and window.isVisible():
            return window
        return self

    def _settings_context(self) -> SettingsContext:
        language, anonymizer_config = self._config_panel.anonymizer_context()
        return SettingsContext(
            settings=self._config_panel.get_settings_dict(),
            app_service=self._app_service,
            language=language,
            anonymizer_config=anonymizer_config,
            acknowledgements=self._acknowledgements(),
            display_anonymized=self._privacy_controller.display_anonymized,
            privacy_available=self._privacy_controller.privacy_available,
            pull_manager=self._pull_manager,
        )

    def open_settings(
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
        if self._settings_window is not None and self._settings_window.isVisible():
            self._settings_window.show_page(page, focus, preselect)
            return
        window = SettingsWindow(
            self._settings_context(),
            parent or self,
            page=page,
            focus=focus,
            preselect=preselect,
        )
        window.preprocess_preview_requested.connect(
            lambda ocr_config: self._preprocess_preview.request(ocr_config, parent=window)
        )
        window.packages_changed.connect(self.packages_changed)
        window.addon_installed.connect(self._on_addon_installed)
        # What the Privacy page showed: its checkbox was usable only if hiding was possible
        # when the window opened (later changes to the panel must not decide that).
        hiding_was_possible = self._privacy_controller.privacy_available
        self._settings_window = window
        try:
            code = window.exec()
            result = window.values()
        finally:
            self._settings_window = None
        if result.restore_all:
            self._restore_all_settings()
        elif code == QDialog.DialogCode.Accepted:
            self._apply_settings_result(result, hiding_was_possible=hiding_was_possible)
        window.deleteLater()

    def _apply_settings_result(self, result: SettingsResult, *, hiding_was_possible: bool) -> None:
        """Take over what the Settings window changed, then save once."""
        self._config_panel.update_settings(result.settings)
        if result.turn_on_anonymization:
            # The AI models page said it would: an online service was chosen while names would
            # be sent as written.  The checkbox's own handler guards the stage and refreshes
            # the data-flow box; it comes before the save so the choice is kept.
            self._config_panel.set_anonymization_enabled(True)
        if result.anonymizer_config is not None:
            before = self._config_panel.build_anonymizer_config()
            self._config_panel.update_anonymizer_config(result.anonymizer_config)
            if self._config_panel.build_anonymizer_config() != before:
                # New models or patterns produce different placeholders for the shown text.
                self._check_controller.page.forget_anonymization()
        if result.display_anonymized is not None and hiding_was_possible:
            # Without the add-on the checkbox is disabled and shows "off"; applying that
            # would overwrite the saved preference.
            self._privacy_controller.apply_display_anonymized(result.display_anonymized)
        self._save_settings(notify=False)
        self._readiness_controller.start_check()
        self._refresh_queue_from_inputs()

    def _on_packages_changed(self) -> None:
        """Something was installed: forget what was cached about it, then look again.

        The one place that clears the caches which depend on what is installed, so every
        installer — whichever window it ran in, and whether that window is still open — has
        the same effect.
        """
        self._app_service.anonymization.clear_anonymizer_cache()  # also forgets the installed models
        self._config_panel.refresh_data_flow()  # also refreshes the privacy availability
        self._privacy_controller.refresh_availability()
        self._readiness_controller.start_check()

    def _on_install_round_finished(self, installed_any: bool) -> None:
        """A round of installs ended: a package may have arrived, so look again."""
        if installed_any:
            self.packages_changed.emit()

    def _on_addon_installed(self, addon: str) -> None:
        if addon == PRIVACY_ADDON_ID:
            self.packages_changed.emit()  # spaCy may be available now
            _dialogs.notify(self._dialog_parent(), dlg.privacy_addon_installed())
        elif addon == READER_ADDON_ID:
            # The built-in reader's models arrived: the readiness line turns green on the
            # check below; nothing else to tell.
            self._log.append_text("The built-in reader is downloaded.\n")
        elif addon == PAGE_MODELS_ADDON_ID:
            self._log.append_text("The page models are downloaded.\n")
        self._readiness_controller.start_check()


# ── Entry point ────────────────────────────────────────────────────────────


def _default_window_factory() -> MainWindow:
    """Build the window with the process's services, for ``teachers-teammate-gui``."""
    from .composition import build_main_window  # noqa: PLC0415  # imports this module

    return build_main_window()


def main_gui(
    *,
    window_factory: Callable[[], MainWindow] | None = None,
    early_splash: QSplashScreen | None = None,
) -> None:
    """Launch the Teacher's Teammate GUI.

    Args:
        window_factory: Override the default window factory (used in tests).
        early_splash:   A :class:`QSplashScreen` that was shown before the heavy GUI imports
                        so it is already visible during startup.  When provided the internal
                        splash-creation logic is skipped and the caller's splash is reused.
    """
    # Opt-in (--diagnose): where the start and any freeze of the window spend their time.
    diagnostics = start_diagnostics(_APP_VERSION)
    if diagnostics is not None:
        diagnostics.phase("main_gui entered (imports and the early splash are done)")
        diagnostics.start_watching()
    # Idempotent: run_gui.py and __main__.py did it already; a wheel install's entry point
    # did not, and a language model downloaded in a session would be lost after a restart.
    inject_addon_packages_dir()
    diagnostics_phase("add-on packages folder on sys.path")
    # Start the slow own-host-name lookup before any widget asks where a step runs.
    OllamaServerService.warm_up_locality_checks()
    # Suppress Qt's D-Bus tray warning when org.kde.StatusNotifierWatcher is absent.
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.tray*=false")
    if sys.stderr is not None:
        logging.basicConfig(
            level=logging.WARNING, stream=sys.stderr, format="%(levelname)s: %(message)s"
        )
    # Theme is applied after the splash is shown (below) so startup feels fast.
    # _app_icon is passed as a factory: it builds a QPixmap, which must not run
    # before the QApplication exists.
    app = create_app(icon_factory=_app_icon, apply_theme=False)
    diagnostics_phase("QApplication created")
    if diagnostics is not None:
        # The GUI thread's heartbeat: the watching thread reports when it stays silent.
        heartbeat = QTimer(app)
        heartbeat.timeout.connect(diagnostics.beat)
        heartbeat.start(50)

    # ── Splash screen ──────────────────────────────────────────────────────
    # Reuse an early splash that was shown before the heavy imports when
    # available; otherwise fall back to creating one now.
    _splash: QSplashScreen | None = early_splash
    if _splash is None:
        _splash_img = Path(__file__).parent.parent / "assets" / "teachers_teammate.png"
        if _splash_img.exists():
            _pix = load_pixmap(_splash_img)
            if not _pix.isNull():
                # Scale down if the image is very large while preserving aspect ratio.
                if _pix.width() > 600 or _pix.height() > 600:
                    _pix = _pix.scaled(
                        600,
                        600,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                _splash = QSplashScreen(_pix)
                _splash.show()
                assert isinstance(app, QApplication)
                app.processEvents()

    assert isinstance(app, QApplication)
    app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api="pyside6"))
    diagnostics_phase("theme applied")

    factory = window_factory or _default_window_factory
    try:
        window = factory()
        diagnostics_phase("main window built")
        window.show()
        diagnostics_phase("main window shown")
        QTimer.singleShot(0, lambda: diagnostics_phase("first turn of the event loop"))
    except BaseException:
        # Never leave the splash up behind an error dialog or a failed start.
        if _splash is not None:
            _splash.close()
        raise

    if _splash is not None:
        _splash.finish(window)

    finish_process(app.exec())
