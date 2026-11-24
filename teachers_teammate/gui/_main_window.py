"""Main application window and entry point."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QProgressBar,
    QPushButton,
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
    log_folder,
    start_diagnostics,
    start_logging,
)
from ..application.ollama_server import OllamaServerService
from ..application.queue import StageState
from ..config import Config
from ..domain.data_location import DataFlow
from ..domain.install_item import InstallItem
from ..domain.local_models import PAGE_MODELS_ADDON_ID, PRIVACY_ADDON_ID, READER_ADDON_ID
from ..settings_schema import DEFAULTS
from ..texts import app as app_text
from ..texts import check as check_text
from ..texts import dialogs as dlg
from ..texts import main_window as win_text
from ..texts.privacy import stage_tooltips
from . import _dialogs, _handles
from ._app_bootstrap import create_app, finish_process
from ._background import LatestTask, wait_for_background_tasks
from ._chart_widget import ChartWidget
from ._check_controller import CheckController, CheckHooks
from ._config_panel import ConfigPanel
from ._credential_store import CredentialStore
from ._export_controller import ExportController
from ._image_utils import load_icon, load_pixmap
from ._log_widget import LogWidget
from ._menu_bar import MenuBar, MenuHooks
from ._preprocess_preview import PreprocessPreview
from ._privacy_settings import DISPLAY_ANONYMIZED_DEFAULT, FlowAcknowledgements
from ._privacy_view_controller import PrivacyViewController
from ._pull_manager import PullManager
from ._qsettings import app_settings
from ._qt import present
from ._readiness_controller import ReadinessController, ReadinessHooks
from ._readiness_line import ReadinessLine
from ._results_table import ResultsTable
from ._run_controller import RunController, RunHooks, WorkerLike
from ._run_session import RunSession
from ._run_start import RunStartController, RunStartHooks, RunStartView
from ._settings_file import SettingsFile, SettingsFileHooks
from ._settings_launcher import SettingsHooks, SettingsLauncher
from ._setup_launcher import SetupHooks, SetupLauncher
from ._stats_widget import SystemStatsWidget
from ._system_monitor import SystemMonitorWindow
from ._update_banner import build_update_banner, show_update
from ._update_check import check_for_update
from .settings import SettingsPage
from .settings._page import FOCUS_DETECTION

if TYPE_CHECKING:
    from .benchmark._benchmark_window import BenchmarkWindow


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
        self.setWindowTitle(app_text.MAIN_WINDOW_TITLE)
        self.resize(1500, 860)
        self._app_service = app_service
        self._worker_factory = worker_factory
        _icon = _app_icon()
        if _icon is not None:
            self.setWindowIcon(_icon)
        self._init_runtime_state()

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
        """Declare the menu bar and what its tools open later."""
        #: Built in ``_build_ui``; the window lays nothing out on it.
        self._menu_bar: MenuBar
        #: The Benchmark window once opened (parentless, see ``_open_benchmark``).
        self._benchmark_window: BenchmarkWindow | None = None
        #: Opens the Setup Assistant, also on a first start.
        self._setup_launcher: SetupLauncher

    def _init_runtime_state(self) -> None:
        """Initialise per-window runtime state and declare widgets built later."""
        #: Set once the teacher agreed to stop the run and close (File → Quit asks itself).
        self._close_confirmed = False
        self._settings_file: SettingsFile
        self._pull_manager = PullManager(self, service=self._app_service)
        #: What the run in progress is doing: the file, the step, the pace and the Stop stage.
        self._run_session = RunSession()
        self._preprocess_preview: PreprocessPreview
        #: Opens the Settings window and takes over what it changed.
        self._settings_launcher: SettingsLauncher
        self._init_menu_state()

        # Declared here; assigned in _build_widgets and _init_controllers.
        self._config_panel: ConfigPanel
        self._readiness_line: ReadinessLine
        self._export_controller: ExportController
        self._run_controller: RunController
        self._run_start: RunStartController
        self._readiness_controller: ReadinessController
        self._check_controller: CheckController
        self._privacy_controller: PrivacyViewController
        # The same for the expected cache keys of the queue: they depend on the card.
        self._cache_state_task = LatestTask(self)
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
        _style = present(self.style(), "style")
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
        """Hand the stored provider API keys to the app (a key in the environment wins)."""
        env_keys = {
            self._app_service.providers.env_key(provider)
            for provider in self._app_service.providers.list_providers()
        }
        CredentialStore().load(self._app_service.keys, sorted(key for key in env_keys if key))

    def _build_ui(self) -> None:
        """Build the widgets, hand them to the controllers, then lay them out."""
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        self._update_banner = build_update_banner()
        root.addWidget(self._update_banner)

        self._build_widgets()
        self._init_controllers()
        root.addWidget(self._build_tabs(), 1)
        self._menu_bar = MenuBar(
            self,
            panel=self._config_panel,
            export=self._export_controller,
            hooks=self._menu_hooks(),
        )
        self._connect_pages()
        self._settings_file = SettingsFile(
            self,
            services=self._app_service,
            panel=self._config_panel,
            splitter=self._main_splitter,
            log=self._log,
            settings_store=self._settings_store,
            hooks=SettingsFileHooks(
                ask_first_start=lambda: self._setup_launcher.open(first_start=True),
                queue_loaded=self._refresh_queue_from_inputs,
            ),
        )

        # The switch is read once, at start: turning it off takes effect at the next start.
        if self._config_panel.get_settings_dict().get(
            "check_for_updates", DEFAULTS["check_for_updates"]
        ):
            check_for_update(self._show_update_banner, owner=self)

    def _build_widgets(self) -> None:
        """Build what the window shows and the controllers work on."""
        self._config_panel = ConfigPanel()
        self._config_panel.set_service(self._app_service)
        self._results_table = ResultsTable()
        self._results_table.setObjectName(_handles.RESULTS_TABLE)
        self._log = LogWidget()
        # The tabs are filled once the controllers exist: the Check page is one of them.
        self._tabs = QTabWidget()
        self._tabs.setObjectName(_handles.MAIN_TABS)
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
            preflight_running=lambda: self._run_start.preflight_running,
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
            run_stage=lambda ids, stage: self._run_start.start_stage(ids, stage),  # noqa: PLW0108
            open_settings=lambda page, focus: self.open_settings(page, focus=focus),
            privacy_available=lambda: self._privacy_controller.privacy_available,
            export=lambda ids, kind: self._export_controller.on_requested(ids, kind),  # noqa: PLW0108
        )

    def _menu_hooks(self) -> MenuHooks:
        """What the menu bar asks of the window."""
        return MenuHooks(
            quit_requested=self._on_quit_requested,
            open_settings=self.open_settings,
            open_benchmark=self._open_benchmark,
            open_monitor=self._open_system_monitor,
            open_setup_assistant=self._setup_launcher.open,
            open_log_folder=self._open_log_folder,
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
        self._init_run_start()
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
        self._setup_launcher = SetupLauncher(
            self,
            services=self._app_service,
            panel=self._config_panel,
            pull_manager=self._pull_manager,
            privacy=self._privacy_controller,
            log=self._log,
            hooks=self._setup_hooks(),
        )
        self._settings_launcher = SettingsLauncher(
            self,
            services=self._app_service,
            panel=self._config_panel,
            check=self._check_controller,
            privacy=self._privacy_controller,
            pull_manager=self._pull_manager,
            acknowledgements=self._acknowledgements,
            hooks=self._settings_hooks(),
        )

    def _init_run_start(self) -> None:
        """Hand the run its start: what a run asks the teacher before it begins."""
        self._run_start = RunStartController(
            self._app_service,
            view=RunStartView(
                window=self,
                config_panel=self._config_panel,
                results_table=self._results_table,
                run_button=self._run_btn,
                log=self._log,
            ),
            hooks=RunStartHooks(
                run_controller=self._run_controller,
                readiness_controller=self._readiness_controller,
                check_controller=self._check_controller,
                run_session=self._run_session,
                queued_files=lambda source_ids=None: self._queued_files(source_ids),
                recognised_files=lambda: self._recognised_files(),  # noqa: PLW0108
                recognition_needed=lambda source_ids=None: self._recognition_needed(source_ids),
                run_in_progress=lambda: self._run_in_progress(),  # noqa: PLW0108
                acknowledgements=lambda: self._acknowledgements(),  # noqa: PLW0108
            ),
        )
        self._stop_btn = self._run_controller.stop_button
        self._progress = self._run_controller.progress_bar
        self._run_btn.clicked.connect(self._run_start.start)
        self._stop_btn.clicked.connect(self._run_controller.on_stop_clicked)
        self._check_controller.page.stop_requested.connect(self._run_controller.on_stop_clicked)

    def _connect_pages(self) -> None:
        """Wire the panels and the pages to the window."""
        self._results_table.check_requested.connect(self._check_controller.open_document)
        self._results_table.stage_run_requested.connect(self._run_start.start_stage)
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
        self._tabs.addTab(self._main_splitter, check_text.TAB_DOCUMENTS)

        self._tabs.addTab(self._check_controller.page, check_text.TAB_CHECK)
        self._tabs.setTabEnabled(1, False)  # nothing to check before a document has a result
        self._tabs.currentChanged.connect(self._check_controller.on_tab_changed)
        return self._tabs

    def _open_system_monitor(self) -> None:
        self._monitor.show_and_raise()

    def _open_log_folder(self) -> None:
        """Help → Open Log Folder: show where the app writes down what went wrong."""
        folder = log_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # nothing to show: the data folder cannot be written at all
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

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
        layout.addWidget(self._run_controller.awake_note)
        layout.addWidget(self._run_controller.battery_note)

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

    def _load_toml_if_present(self) -> None:
        """Read the settings file, or ask the first-start questions when there is none."""
        self._settings_file.load_if_present()

    def _settings_store(self) -> QSettings:
        """Return the store of the window's GUI preferences (not pipeline settings)."""
        return app_settings()

    def _acknowledgements(self) -> FlowAcknowledgements:
        """Return the store of "don't ask again" answers (read and written through QSettings)."""
        return FlowAcknowledgements(app_settings)

    def _save_settings(self, *, notify: bool = True, silent_on_error: bool = False) -> None:
        """Write the settings file and remember the window geometry."""
        self._settings_file.save(notify=notify, silent_on_error=silent_on_error)

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
        self._setup_launcher.shutdown()
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
        self._tray.showMessage(app_text.NAME, text, icon, 4000)

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

    def _restore_all_settings(self) -> None:
        """Reset every setting to its default and save (Settings already asked twice)."""
        self._config_panel.restore_defaults()
        # Not part of the settings file: the "hide personal information" view and the
        # remembered "Don't ask again" answers (so the next run asks before sending).
        self._privacy_controller.apply_display_anonymized(DISPLAY_ANONYMIZED_DEFAULT)
        self._acknowledgements().forget_all()
        self._check_controller.page.forget_anonymization()
        self._save_settings(notify=False)
        self._after_settings_applied()

    def _on_results_changed(self) -> None:
        """Step "Results" changed: save it, and show what is cached for the new folder."""
        self._save_settings(notify=False)
        # Also asks the readiness check again ("Text file, anonymized" needs name detection).
        self._refresh_queue_from_inputs()

    def _on_instructions_changed(self) -> None:
        """The proofreading instructions changed: save them, and re-count the parts."""
        self._save_settings(notify=False)

    # ── Worker signal handlers ────────────────────────────────────────────

    def _sync_row(self, *source_ids: str) -> None:
        """Show what the store now says about *source_ids* in their rows, read in one pass.

        A row is the store's answer, so every action that changes a document ends here
        instead of patching cells: a stage that ran, an edit, a *Checked* mark.
        """
        try:
            config = self._config_panel.to_config()
        except ValueError:
            # A setting that is not valid yet (no output folder) must not hide stored results:
            # the lookup config names the same state folder and needs no valid folder.
            config = self._config_panel.lookup_config()
        try:
            rows = self._app_service.documents.queue_rows(config, [Path(s) for s in source_ids])
        except OSError, ValueError:
            return  # nothing readable: the rows keep what they showed
        for row in rows:
            self._results_table.update_row(row)

    # ── Readiness line ────────────────────────────────────────────────────

    def _provider_display_name(self, provider: str) -> str:
        """Return the name the teacher knows *provider* by."""
        return self._app_service.providers.display_name(provider)

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
        self._readiness_controller.end_download()

    def _on_pull_error(self, msg: str) -> None:
        self._readiness_controller.model_download_failed(msg)

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

    def _show_update_banner(self, version: str, url: str) -> None:
        show_update(self._update_banner, version, url)

    def _current_ollama_url(self) -> str:
        return str(self._config_panel.get_settings_dict().get("ollama_url", DEFAULTS["ollama_url"]))

    # ── Settings window ───────────────────────────────────────────────────

    def _dialog_parent(self) -> QWidget:
        """Return the window a new dialog belongs to: the open Settings window, else this one."""
        return self._settings_launcher.visible_window or self

    def open_settings(
        self,
        page: SettingsPage,
        *,
        focus: str = "",
        preselect: Sequence[InstallItem] = (),
        parent: QWidget | None = None,
    ) -> None:
        """Open the Settings window on *page* (focused on *focus*) and apply it on OK."""
        self._settings_launcher.open(page, focus=focus, preselect=preselect, parent=parent)

    def _setup_hooks(self) -> SetupHooks:
        """What the Setup Assistant asks of the window."""
        return SetupHooks(
            save=lambda: self._save_settings(notify=False),
            applied=self._after_settings_applied,
            install_round_finished=self._on_install_round_finished,
            select_folder=self._config_panel.select_folder,
        )

    def _settings_hooks(self) -> SettingsHooks:
        """What the Settings window asks of the window."""
        return SettingsHooks(
            restored_all=self._restore_all_settings,
            save=lambda: self._save_settings(notify=False),
            applied=self._after_settings_applied,
            addon_installed=self._on_addon_installed,
            packages_changed=self.packages_changed.emit,
            preprocess_preview=lambda ocr_config, parent: self._preprocess_preview.request(
                ocr_config, parent=parent
            ),
        )

    def _after_settings_applied(self) -> None:
        """Look again at what a run needs, and at what is cached for the new folder."""
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
            self._log.append_text(win_text.LOG_READER_DOWNLOADED)
        elif addon == PAGE_MODELS_ADDON_ID:
            self._log.append_text(win_text.LOG_PAGE_MODELS_DOWNLOADED)
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
    # Idempotent: frozen_entry.py and __main__.py did it already; a wheel install's entry point
    # did not, and a language model downloaded in a session would be lost after a restart.
    inject_addon_packages_dir()
    diagnostics_phase("add-on packages folder on sys.path")
    # Start the slow own-host-name lookup before any widget asks where a step runs.
    OllamaServerService.warm_up_locality_checks()
    # Suppress Qt's D-Bus tray warning when org.kde.StatusNotifierWatcher is absent.
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.tray*=false")
    # Where a warning or an unexpected error goes: the log file, and stderr when there is one.
    start_logging()
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
                app.processEvents()

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
