"""Main application window and entry point."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import Enum
import logging
import os
from pathlib import Path
import shutil
import sys
import time
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Protocol, cast

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
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
from ..application.addons import PRIVACY_ADDON
from ..application.check_document import CheckDocument
from ..application.commands import WorkNotSavedError
from ..application.composition import (
    AppServices,
    diagnostics_phase,
    inject_addon_packages_dir,
    start_diagnostics,
)
from ..application.documents import WorkedOn
from ..application.export import NO_TEXT_MESSAGE, DocxExport
from ..application.ollama_server import OllamaServerService
from ..application.queue import StageState
from ..application.run import IssueKind, RequirementIssue, RunPreflight
from ..application.sent_text import SentTexts
from ..config import DEFAULTS, Config, OcrConfig, dump_config_file, load_config_file
from ..domain.data_location import STAGE_CORRECTION, DataFlow
from ..domain.local_models import PAGE_MODELS_ADDON_ID, READER_ADDON_ID
from ..domain.model_catalog import HardwareDetection, short_model_name
from ..domain.ollama_discovery import (
    OLLAMA_DOWNLOAD_URL,
    DiscoveredServer,
    DiscoveryScope,
    ServerKind,
    WaitDetail,
    WaitKind,
)
from ..domain.performance_estimate import TimeRange
from ..domain.run_summary import FileStatus, KeepAwakeState, RunSummary
from ..domain.settings_notice import SettingNotice, SettingNoticeKind
from ..domain.stages import (
    PROGRESS_STAGE_CORRECTION,
    PROGRESS_STAGE_OCR,
    PROGRESS_STAGE_PREPROCESSING,
)
from ..texts import check as check_text
from ..texts import dialogs as dlg
from ..texts import privacy
from ..texts.run import BATTERY_WARNING
from ..texts.settings import WHERE_ADDONS, WHERE_GENERAL
from . import _dialogs
from . import _user_text as txt
from ._app_bootstrap import create_app, finish_process
from ._background import LatestTask, run_in_background, wait_for_background_tasks
from ._chart_widget import ChartWidget
from ._config_panel import ConfigPanel
from ._credential_store import CredentialStore
from ._data_flow_summary import advice_text, level_text, stage_tooltip
from ._export_menu import ExportKind, build_export_menu
from ._fix_targets import (
    COMMAND_CHOOSE_FOLDER,
    COMMAND_GET_OLLAMA,
    COMMAND_RETRY,
    COMMAND_START_OLLAMA,
    COMMAND_TURN_ON_RECOGNITION,
    COMMAND_USE_SERVER,
    RESULTS_NO_FOLDER,
    RESULTS_TEXT_IN_INPUT,
    Fix,
    FixAction,
    fix_for,
    found_server_fix,
    results_fix,
)
from ._help_dialog import AboutDialog, HelpDialog, ThirdPartyLicensesDialog
from ._image_utils import load_icon, load_pixmap
from ._image_view import ZoomableImageView
from ._log_widget import LogWidget
from ._model_fetch import _start_thread
from ._model_wording import profile_text
from ._privacy_settings import (
    DISPLAY_ANONYMIZED_DEFAULT,
    FlowAcknowledgements,
    display_anonymized,
    set_display_anonymized,
)
from ._readiness_line import ReadinessLine, readiness
from ._results_table import ResultsTable
from ._run_status import RunPace, RunProgress, RunTone, describe_run
from ._stats_widget import SystemStatsWidget
from ._system_monitor import SystemMonitorWindow
from ._types import FileDoneEvent
from ._update_check import UpdateCheckThread
from ._view_settings import (
    check_card_split,
    check_split,
    set_check_card_split,
    set_check_split,
    set_text_point_size,
    text_point_size,
)
from ._what_is_sent_dialog import WhatIsSentDialog
from ._window_geometry import maximize_without_saved_geometry, restore_geometry
from ._worker import OCRWorker, detach_running_worker
from .check import CheckLayout, CheckPage, CopyKind, CopyState, RunState
from .settings import (
    PAGE_TITLES,
    SettingsContext,
    SettingsPage,
    SettingsResult,
    SettingsWindow,
    last_page,
)
from .settings._page import FOCUS_DETECTION, FOCUS_OCR, FOCUS_PROOFREADING
from .settings._window import SUB_PAGES

if TYPE_CHECKING:
    from .benchmark._benchmark_window import BenchmarkWindow
    from .setup import SetupResult


class _TextExportOutcome(NamedTuple):
    """Result of a background text export."""

    written: list[Path]
    failed: list[str]


def _debounce_timer(parent: QWidget, interval_ms: int, slot: Callable[[], None]) -> QTimer:
    """Return a single-shot timer that calls *slot* once *interval_ms* have passed."""
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(interval_ms)
    timer.timeout.connect(slot)
    return timer


def _spacy_problem(exc: BaseException) -> str | None:
    """Explain a missing spaCy install / model (``None`` for any other error)."""
    if isinstance(exc, ImportError):
        return privacy.spacy_missing(str(exc), installed_app=bool(getattr(sys, "frozen", False)))
    if isinstance(exc, OSError):
        return privacy.spacy_model_missing(str(exc))
    return None


class _PreprocessPreviewThread(QThread):
    """Run preprocess_preview() in a background thread for the preprocessing preview dialog.

    Preprocessing a full-resolution scan (denoise, deskew, ...) can take several
    seconds; running it on the GUI thread would freeze the window until it returns.
    """

    done = Signal(object, object, object, str)  # original_path, processed_path, steps, error

    def __init__(
        self,
        app_service: AppServices,
        source_path: Path,
        ocr_config: OcrConfig,
        tmp_dir: Path,
    ) -> None:
        super().__init__()
        self._svc = app_service
        self._source_path = source_path
        self._ocr_config = ocr_config
        self._tmp_dir = tmp_dir

    def run(self) -> None:
        try:
            orig, proc, steps = self._svc.reader.preprocess_preview(
                self._source_path, self._ocr_config, self._tmp_dir
            )
            self.done.emit(orig, proc, steps, "")
        except Exception as exc:  # noqa: BLE001  # preprocess_preview raises for unsupported files, missing deps, or I/O errors; report via the done signal
            self.done.emit(None, None, None, str(exc))


class _StopStage(Enum):
    """Where the two-step Stop button is: the first click finishes the document being
    processed, the second stops at once."""

    IDLE = "idle"
    RUNNING = "running"
    AFTER_FILE = "after_file"
    NOW = "now"


STOP_TEXT = "Stop"
STOP_TOOLTIP = "Finish the document being processed, then stop."
STOP_NOW_TEXT = "Stop now"
STOP_NOW_TOOLTIP = (
    "Stop at once. The document being processed is left unfinished; what is already done "
    "is kept and reused next time."
)
STOPPING_SUFFIX = " — stopping after this document"
#: How often the bar and the time left follow the clock while a run is going.
PROGRESS_TICK_MS = 1000
#: Steps of the bar once it follows the time of the run.
PROGRESS_RESOLUTION = 1000
STOPPING_NOW_TEXT = "Stopping…"
#: How long closing the window waits for a stopping run (milliseconds): enough for a cancelled
#: Ollama request and a state write, never for the run itself.
CLOSE_GRACE_MS = 3000
AWAKE_HELD_TEXT = (
    "Keeping the computer awake while documents are processed. The screen may turn off."
)
AWAKE_REFUSED_TEXT = "This computer did not allow staying awake; it may go to sleep during the run."
AWAKE_UNAVAILABLE_TEXT = (
    "The app cannot keep this computer awake (the system decides); "
    "check its power settings if a long run must not be interrupted."
)


def _wait_detail_text(detail: WaitDetail) -> str:
    """Return what to say about a request to Ollama that has no answer yet."""
    if detail.kind is WaitKind.BUSY:
        return check_text.RUN_WAITING_FOR_MODEL.format(other=detail.other)
    if detail.kind is WaitKind.LOADING:
        return check_text.RUN_LOADING_MODEL.format(model=detail.model)
    if detail.kind is WaitKind.QUEUED:
        return check_text.RUN_QUEUED_FOR_MODEL.format(other=detail.other, model=detail.model)
    return ""


class _WorkerLike(Protocol):
    log_line: Any
    file_started: Any
    stage_started: Any
    ocr_done: Any
    stage_detail: Any
    first_token: Any
    file_done: Any
    keep_awake_changed: Any
    run_finished: Any
    stop_event: Any
    stop_after_file_event: Any

    def start(self) -> None: ...

    def request_stop(self, *, now: bool) -> None: ...

    def wait(self, msecs: int, /) -> bool: ...

    def isRunning(self) -> bool: ...


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


class OllamaDownloadManager(QObject):
    """Manages a single active Ollama model pull thread, outliving any dialog.

    MainWindow owns this object; Settings → Add-ons subscribes while it is open and
    unsubscribes when the window closes, so pulls continue in the background.
    """

    progress = Signal(str, float, float)  # (status, completed_bytes, total_bytes)
    started = Signal(str)  # model_name
    finished = Signal(str)  # model_name (success)
    error = Signal(str)  # error message

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        service: AppServices,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._thread: QThread | None = None
        self._current_model: str = ""
        self._current_url: str = ""
        #: Models waiting for the running pull; pulled one after the other, also after the
        #: Settings window that asked for them was closed.
        self._queue: list[str] = []

    @property
    def is_active(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def current_model(self) -> str:
        return self._current_model

    @property
    def current_url(self) -> str:
        """Ollama server of the latest (or running) pull."""
        return self._current_url

    @property
    def queued(self) -> list[str]:
        """Return the models still waiting for the running pull (a copy)."""
        return list(self._queue)

    def start(self, url: str, model_name: str, queue: Sequence[str] = ()) -> None:
        """Pull *model_name* from *url*, then each model of *queue*.

        While a pull is running (also one that outlived the Settings window) the models are
        added to its queue instead, so a request is never dropped silently; a model that is
        already pulling or waiting is not added twice.  A failed or aborted pull drops the
        queue.
        """
        if self.is_active:
            for model in (model_name, *queue):
                if model != self._current_model and model not in self._queue:
                    self._queue.append(model)
            return
        self._queue = list(queue)
        self._begin(url, model_name)

    def _begin(self, url: str, model_name: str) -> None:
        from ._addon_installer_dialog import _OllamaPullThread  # noqa: PLC0415

        self._current_model = model_name
        self._current_url = url
        thread = _OllamaPullThread(self._service, url, model_name)
        thread.progress.connect(self.progress)
        thread.finished_ok.connect(self._on_finished_ok)
        thread.finished_err.connect(self._on_finished_err)
        # Kept alive until it ends: the thread reports from run(), so the reference dropped
        # in _on_finished_ok may be the last one while the thread is still running.
        _start_thread(thread)
        self._thread = thread
        self.started.emit(model_name)

    def abort(self) -> None:
        """Request the active pull to stop after the current chunk (and drop the queue)."""
        self._queue.clear()
        if self._thread is not None:
            self._thread.request_abort()  # ty: ignore[unresolved-attribute]  # _thread holds an abortable _OllamaPullThread; base QThread lacks request_abort

    def _on_finished_ok(self, model_name: str) -> None:
        self._thread = None
        # Subscribers see the queue as it was when this pull ended: a non-empty one means
        # the round goes on.
        self.finished.emit(model_name)
        if self._queue:
            self._begin(self._current_url, self._queue.pop(0))

    def _on_finished_err(self, msg: str) -> None:
        self._thread = None
        self._queue.clear()
        self.error.emit(msg)


#: The two texts the Check tab lets the teacher type in, as the store names them.
_EDIT_RAW = "raw"
_EDIT_CORRECTED = "corrected"

#: How much of a document's name the Check tab label shows before it is shortened.
_CHECK_TAB_NAME_CHARS = 28

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
        worker_factory: Callable[..., _WorkerLike] | None = None,
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
        self._export_actions: list[QAction] = []
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
        """Declare what the window knows about a run that is going: stopping, notes, closing."""
        #: Where the two-step Stop button is (see :class:`_StopStage`).
        self._stop_stage = _StopStage.IDLE
        #: The progress text without the "stopping" note (see :meth:`_set_progress_text`).
        self._progress_base = "Idle"
        #: Set once the teacher agreed to stop the run and close (File → Quit asks itself).
        self._close_confirmed = False
        #: How fast the current run is going and how much of it is left (see ``RunPace``).
        self._pace = RunPace()
        #: Where the run is at the last tick: the bar and the Check strip both read this.
        self._progress_snapshot = RunProgress()
        #: Pages of the files of the current run, by source id; filled in the background.
        self._run_pages: dict[str, int] = {}
        #: The bar follows the time of the run (0 to 1000) instead of counting files; it does
        #: once the pace gives a figure.
        self._time_driven = False
        #: Moves the bar and counts the time left down between two events of the run.
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(PROGRESS_TICK_MS)
        self._progress_timer.timeout.connect(self._refresh_progress)
        #: The documents of the running batch, for the progress on the Documents tab label.
        self._run_source_ids: list[str] = []
        self._pages_task = LatestTask(self)
        #: Under the progress bar; built in ``_build_left_panel``.
        self._awake_note: QLabel
        self._battery_note: QLabel

    def _init_queue_state(self) -> None:
        """Declare what the window knows about the files in its queue."""
        self._queued_files_by_source_id: dict[str, Path] = {}
        # A silent search for Ollama on this computer, made when the saved address is
        # unreachable: the server it found (offered, never taken over by itself) and the
        # address the search was made for (one search per address).
        self._found_server: DiscoveredServer | None = None
        self._server_searched_for = ""
        self._server_search_task = LatestTask(self)
        # Queued scans the last check found a run would leave out (text recognition is off).
        self._skipped_scans: set[Path] = set()
        #: Files of the current run that were skipped (text recognition is off).

    def _init_runtime_state(self) -> None:
        """Initialise per-window runtime state and declare widgets built later."""
        self._worker: _WorkerLike | None = None
        self._config_path: Path | None = None
        self._pull_manager = OllamaDownloadManager(self, service=self._app_service)
        self._sent_texts_task = LatestTask(self)
        self._last_sent_texts: SentTexts | None = None
        self._anonymize_task = LatestTask(self)
        self._anonymize_running = False
        #: A run was asked for in the Check tab: the tab stays when the run starts.
        self._run_from_check = False
        #: What the run does with the document it works on now, for the Check tab's run strip:
        #: the model that proofreads (``""`` when the run does not proofread) and the step.
        self._run_model = ""
        self._run_step = ""
        #: ``"[3/12] essay.pdf"``: the file the run is on, for the progress line.
        self._run_file_label = ""
        #: What the running stage says about itself, which a wait note replaces and the first
        #: token of the answer brings back: the strip's step and the progress line's label.
        self._stage_step = ""
        self._stage_label = ""
        #: Whether the strip and the progress line carry a note about the server's wait.
        self._wait_shown = False
        #: Whether the run's proofreading waits for an Ollama server, so its clock starts
        #: at the first token instead of at the request.
        self._run_waits_for_ollama = False
        #: Whether the pace is holding its clock for that first token.
        self._pace_waits_for_first_token = False
        #: The "Hide personal information in texts" view setting (not part of Config).
        self._display_anonymized = display_anonymized(self._settings_store)
        #: Whether the texts can be hidden for the current document language.
        self._privacy_available = True
        #: What the Copy menu offers for the open document, so a privacy change can redraw it.
        self._copy_state: CopyState | None = None
        #: The text and language of the running hiding job, to not start it twice.
        self._anonymize_key: tuple[str, str] | None = None
        self._first_run_task = LatestTask(self)
        self._preprocess_preview_thread: _PreprocessPreviewThread | None = None
        #: The Settings window while it is open (dialogs started from it use it as parent).
        self._settings_window: SettingsWindow | None = None
        self._init_menu_state()
        self._result_names: list[str] = []
        self._result_ocr_times: list[float] = []
        self._result_correction_times: list[float] = []
        self._init_queue_state()
        self._steps_per_file: int = 1
        #: The document the Check tab shows; empty when none is open.
        self._current_preview_source_id: str = ""
        #: The documents of the running batch whose own result has not arrived yet.
        self._run_pending: set[str] = set()
        #: The document the run is working on right now, or ``""``.
        self._run_current: str = ""

        # Declared here; assigned in _build_left_panel / _build_right_panel.
        self._config_panel: ConfigPanel
        self._readiness_line: ReadinessLine
        # The readiness line: the last answer of the background check (None: nothing blocks),
        # whether the first check has answered, and whether "Checking…" may be shown yet.
        self._blocker: tuple[str, RequirementIssue] | None = None
        self._first_check_answered = False
        self._checking_visible = False
        self._readiness_task = LatestTask(self)
        # True while the requirement check of a Run / stage re-run is in flight.
        self._preflight_running = False
        self._text_export_running = False
        # Settings change in bursts (a checkbox, a model, a language): check once, soon after.
        self._readiness_debounce = _debounce_timer(
            self, self._READINESS_DEBOUNCE_MS, self._start_readiness_check
        )
        # A drag of a pane reports every pixel: the settings are written once it settles.
        self._pending_check_layout: CheckLayout | None = None
        self._layout_debounce = _debounce_timer(
            self, self._READINESS_DEBOUNCE_MS, self._flush_check_layout
        )
        # An unreachable Ollama is checked again every few seconds: it may be started after
        # the app.
        self._ollama_retry_timer = _debounce_timer(
            self, self._OLLAMA_RETRY_MS, self._start_readiness_check
        )
        # "Checking…" shows only when the first answer takes a noticeable moment.
        self._checking_timer = _debounce_timer(self, self._CHECKING_DELAY_MS, self._show_checking)
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
        self._check: CheckPage
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
        self._build_menu_bar()
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        self._update_banner = self._build_update_banner()
        root.addWidget(self._update_banner)

        root.addWidget(self._build_tabs(), 1)

        self._results_table.check_requested.connect(self._open_check)
        self._results_table.stage_run_requested.connect(self._on_stage_run_requested)
        self._results_table.what_is_sent_requested.connect(self._on_what_is_sent_requested)
        self._config_panel.data_flow_changed.connect(self._on_data_flow_changed)
        self._results_table.itemSelectionChanged.connect(self._update_readiness)
        # The box rendered once while the panels were built, before these connections.
        self._on_data_flow_changed(self._config_panel.data_flow())
        self._results_table.export_requested.connect(self._on_export_requested)
        self._config_panel.paths_changed.connect(self._refresh_queue_from_inputs)
        self._config_panel.anonymizer_configure_requested.connect(
            lambda _language, _config: self.open_settings(
                SettingsPage.PRIVACY, focus=FOCUS_DETECTION
            )
        )
        self._config_panel.results_changed.connect(self._on_results_changed)
        self._config_panel.instructions_changed.connect(self._on_instructions_changed)
        self._connect_check_page()
        self._refresh_privacy_availability()
        self._check.set_display_anonymized(self._display_anonymized)
        # Kick off the first readiness check once the window is laid out.
        # Pass self as the receiver so the callback is cancelled if the window is destroyed.
        QTimer.singleShot(500, self, self._start_readiness_check)

        # No parent: the thread outlives the window if it is closed during the request.
        # The switch is read once, at start: turning it off takes effect at the next start.
        if self._config_panel.get_settings_dict().get(
            "check_for_updates", DEFAULTS["check_for_updates"]
        ):
            self._update_thread = UpdateCheckThread()
            self._update_thread.update_available.connect(self._show_update_banner)
            QTimer.singleShot(0, self._update_thread.start)

    def _build_tabs(self) -> QTabWidget:
        """The two tabs of the window: the work, and one document to check.

        The Check tab holds the preview panel full width, so the queue keeps its own tab.
        """
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        _tab_bar_font = self._tabs.tabBar().font()
        _tab_bar_font.setPointSize(14)
        self._tabs.tabBar().setFont(_tab_bar_font)

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.addWidget(self._build_left_panel())
        self._main_splitter.addWidget(self._build_right_panel())
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._tabs.addTab(self._main_splitter, "Documents")

        self._check = CheckPage()
        self._check.set_point_size(text_point_size(self._settings_store))
        self._check.set_layout(
            CheckLayout(
                split=check_split(self._settings_store),
                card_split=check_card_split(self._settings_store),
            )
        )
        self._tabs.addTab(self._check, "Check")
        self._tabs.setTabEnabled(1, False)  # nothing to check before a document has a result
        self._tabs.currentChanged.connect(self._on_tab_changed)
        return self._tabs

    def _connect_check_page(self) -> None:
        """Wire the Check tab's header to the window, and the two tab shortcuts."""
        self._check.previous_requested.connect(self._on_check_previous)
        self._check.next_requested.connect(self._on_check_next)
        self._check.copy_requested.connect(self._on_check_copy)
        self._check.save_requested.connect(self._on_check_save)
        self._check.back_requested.connect(self._show_documents_tab)
        self._check.raw_text_edited.connect(self._on_ocr_text_edited)
        self._check.corrected_text_edited.connect(self._on_correction_text_edited)
        self._check.proofread_requested.connect(self._on_check_proofread_requested)
        self._check.proofreading_setup_requested.connect(
            lambda: self.open_settings(SettingsPage.AI_MODELS, focus=FOCUS_PROOFREADING)
        )
        self._check.read_settings_requested.connect(
            lambda: self.open_settings(SettingsPage.AI_MODELS, focus=FOCUS_OCR)
        )
        self._check.stop_requested.connect(self._on_stop)
        self._check.checked_toggled.connect(self._on_checked_toggled)
        self._check.item_shown.connect(self._on_item_shown)
        self._check.display_anonymized_changed.connect(self._apply_display_anonymized)
        self._check.anonymization_needed.connect(self._on_anonymization_needed)
        self._check.text_zoom_changed.connect(self._store_text_zoom)
        self._check.layout_changed.connect(self._store_check_layout)
        # Ctrl+1 / Ctrl+2 reach the tabs from anywhere in the window; Esc is the Check page's.
        QShortcut(QKeySequence("Ctrl+1"), self, self._show_documents_tab)
        QShortcut(QKeySequence("Ctrl+2"), self, self._show_check_tab)

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
        export_menu.triggered.connect(lambda action: self._on_export_toolbar(kinds[action]))
        file_menu.addMenu(export_menu)
        # Enabled together with the Export button: there is nothing to export before a run.
        self._export_actions = list(kinds)
        for action in self._export_actions:
            action.setEnabled(False)
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

    def _build_export_button(self) -> QPushButton:
        """Return the Export button; its menu offers Word, text and anonymized text."""
        style = self.style()
        assert style is not None
        button = QPushButton("Export")
        button.setIcon(
            QIcon.fromTheme(
                "document-export",
                style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton),
            )
        )
        button.setMinimumHeight(32)
        button.setEnabled(False)
        button.setToolTip(
            "Save results as Word documents or text files, optionally anonymized "
            "(also available via right-click)"
        )
        menu, kinds = build_export_menu(button)
        menu.triggered.connect(lambda action: self._on_export_toolbar(kinds[action]))
        button.setMenu(menu)
        return button

    def _build_left_panel(self) -> QWidget:
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._config_panel = ConfigPanel()
        self._config_panel.set_service(self._app_service)
        self._config_panel.open_settings_requested.connect(
            lambda page, focus, preselect: self.open_settings(
                SettingsPage(page), focus=str(focus), preselect=list(preselect)
            )
        )
        layout.addWidget(self._config_panel)

        # Whether a run can start, and what to press if it can't -- right above Run.
        self._readiness_line = ReadinessLine()
        self._readiness_line.action_chosen.connect(self._run_fix)
        layout.addWidget(self._readiness_line)

        _style = self.style()
        assert _style is not None

        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        self._run_btn = QPushButton("Run Selected")
        self._run_btn.setIcon(
            QIcon.fromTheme(
                "media-playback-start",
                _style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
            )
        )
        self._run_btn.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; font-weight: bold; "
            "font-size: 10pt; padding: 6px 12px; border-radius: 4px; } "
            "QPushButton:hover { background: #219a52; } "
            "QPushButton:disabled { background: #95a5a6; }"
        )
        self._run_btn.clicked.connect(self._on_run)
        self._stop_btn = QPushButton(STOP_TEXT)
        self._stop_btn.setToolTip(STOP_TOOLTIP)
        self._stop_btn.setIcon(
            QIcon.fromTheme(
                "media-playback-stop",
                _style.standardIcon(QStyle.StandardPixmap.SP_MediaStop),
            )
        )
        self._stop_btn.setMinimumHeight(32)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        self._export_btn = self._build_export_button()
        btn_layout.addWidget(self._run_btn)
        btn_layout.addWidget(self._stop_btn)
        btn_layout.addWidget(self._export_btn)
        layout.addWidget(btn_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("Idle")
        layout.addWidget(self._progress)

        self._awake_note = QLabel()
        self._awake_note.setWordWrap(True)
        self._awake_note.setStyleSheet("color: #888888; font-size: 11px;")
        self._awake_note.setVisible(False)
        layout.addWidget(self._awake_note)
        self._battery_note = QLabel(BATTERY_WARNING)
        self._battery_note.setWordWrap(True)
        self._battery_note.setStyleSheet("color: #e6a23c; font-size: 11px;")
        self._battery_note.setVisible(False)
        layout.addWidget(self._battery_note)

        left.setMinimumWidth(340)
        left.setMaximumWidth(420)
        return left

    def _build_right_panel(self) -> QWidget:
        results_splitter = QSplitter(Qt.Orientation.Vertical)
        self._results_table = ResultsTable()
        self._log = LogWidget()
        results_splitter.addWidget(self._results_table)
        results_splitter.addWidget(self._log)
        results_splitter.setStretchFactor(0, 3)
        results_splitter.setStretchFactor(1, 1)

        # Built now and kept hidden, so the chart collects the data of every run.  Parentless on
        # purpose (see SystemMonitorWindow): closeEvent closes it with this window.
        self._monitor = SystemMonitorWindow(self._app_service.recommendations.read_gpu_usage)
        self._chart = self._monitor.chart
        self._stats = self._monitor.stats

        return results_splitter

    # ── Settings persistence ──────────────────────────────────────────────

    #: How often an unreachable Ollama is checked again (the server may start after the app).
    _OLLAMA_RETRY_MS = 10_000
    #: How long the readiness check waits after a settings change, so a burst checks once.
    _READINESS_DEBOUNCE_MS = 400
    #: How long the first readiness check may take before the line says "Checking…".
    _CHECKING_DELAY_MS = 300
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
            privacy_available=self._privacy_available,
            first_start=first_start,
            display_anonymized=self._display_anonymized,
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
            # view follows it as soon as hiding is possible (_refresh_privacy_availability).
            set_display_anonymized(self._settings_store, result.display_anonymized)
            self._display_anonymized = result.display_anonymized
            self._check.set_display_anonymized(result.display_anonymized)
        self._save_settings(notify=False)
        self._start_readiness_check()
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
        self._start_readiness_check()

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

    def _run_active(self) -> bool:
        """Return whether an OCR worker is running right now."""
        return bool(self._worker and self._worker.isRunning())

    def _confirm_close_during_run(self) -> bool:
        """Ask whether to stop the run and close; the default answer keeps the run going."""
        return _dialogs.ask(self, dlg.close_during_run())

    def _on_quit_requested(self) -> None:
        """File → Quit: ask first when a run is going, then close like the window's X."""
        if self._run_active() and not self._confirm_close_during_run():
            return
        self._close_confirmed = True
        self.close()

    def _stop_worker_for_close(self) -> None:
        """Stop the run at once, forget its signals and give it a moment to end.

        Never waits for the run to finish: a worker that is still running after the grace
        period (an online service cannot be interrupted) is detached, and the process ends
        without destroying it (see :func:`~._app_bootstrap.finish_process`).
        """
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.request_stop(now=True)
        for name in (
            "log_line",
            "file_started",
            "stage_started",
            "ocr_done",
            "file_done",
            "keep_awake_changed",
            "run_finished",
        ):
            try:
                getattr(worker, name).disconnect()
            except RuntimeError, TypeError, AttributeError:
                pass  # nothing connected, or a test double without disconnect
        if not worker.wait(CLOSE_GRACE_MS):
            detach_running_worker(worker)  # ty: ignore[invalid-argument-type]  # a QThread

    def closeEvent(self, event: Any) -> None:
        if (
            self._run_active()
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
        self._layout_debounce.stop()
        self._flush_check_layout()
        # Only close() runs the Benchmark window's own closeEvent, which stops its worker;
        # deleting it with a parent would not.  Closing the last window ends the app.
        if self._benchmark_window is not None:
            self._benchmark_window.close()
        # The System Monitor is parentless too: closing the last visible window ends the app.
        self._monitor.close()
        self._first_run_task.cancel()
        self._ollama_retry_timer.stop()
        self._readiness_debounce.stop()
        self._checking_timer.stop()
        self._readiness_task.cancel()
        # Let the window disappear before waiting: running background tasks are
        # given a moment to end, and the user should not see a frozen window.
        self.hide()
        self._sent_texts_task.cancel()
        self._anonymize_task.cancel()
        self._stop_worker_for_close()
        wait_for_background_tasks()
        event.accept()

    # ── Run / Stop ────────────────────────────────────────────────────────

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
        self._queued_files_by_source_id = {str(file.resolve()): file for file in files}

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
            self._set_export_enabled(True)
        # The readiness line says what is in the queue and what to do next; the bar only
        # reports progress, so it stays "Idle" until something runs.
        if not self._run_in_progress():
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            self._progress.setFormat("Idle")
        self._sync_check_with_queue()
        self._update_check_tab()
        self._update_readiness()
        # Other documents may need another engine (text files need none, recognised ones
        # no more recognition): ask again what a run over them requires.
        self._schedule_readiness_check()

    def _queued_files(self, source_ids: list[str] | None = None) -> list[Path]:
        """Return the queued files of *source_ids* (``None``: the whole queue)."""
        queued = self._queued_files_by_source_id
        return [queued[i] for i in (queued if source_ids is None else source_ids) if i in queued]

    def _recognised_files(self) -> set[Path]:
        """Return the queued files whose recognised text is stored.

        A run does not read them again, so a missing text recognition engine must not stop a
        run over only these.
        """
        queued = self._queued_files_by_source_id
        return {
            queued[source_id]
            for source_id in self._results_table.queued_source_ids()
            if source_id in queued
            and (row := self._results_table.row_for(source_id)) is not None
            and row.ocr is StageState.DONE
        }

    def _recognition_needed(self, source_ids: list[str] | None = None) -> bool:
        """Return whether a run over *source_ids* (default: the whole queue) reads pages."""
        return self._app_service.run.needs_text_recognition(
            self._queued_files(source_ids), recognised=self._recognised_files()
        )

    def _run_in_progress(self) -> bool:
        """Return whether a requirement check or an OCR worker is already active."""
        return self._preflight_running or self._run_active()

    def _warn_busy(self) -> None:
        _dialogs.notify(self, dlg.busy_with_run())

    def _on_run(self) -> None:
        if self._run_in_progress():
            return
        self._run_from_check = False
        try:
            config = self._config_panel.to_config()
        except ValueError as exc:
            _dialogs.notify(self, dlg.configuration_error(str(exc)))
            return
        selected = self._results_table.selected_source_ids()
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
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("Checking requirements…")
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
        self._progress.setFormat("Idle")
        if error is not None or not isinstance(result, RunPreflight):
            # The check itself failed: do not block; the pipeline reports real problems.
            on_ready(RunPreflight())
            return
        if result.blocked:
            self._show_blocking_preflight(result)
            return
        note = _wait_detail_text(result.ollama_wait)
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
        blocker = result.blocker
        if blocker is None:
            return
        from ._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

        stage, issue = blocker
        # A step can miss several things at once; the first one decides the buttons.
        issues = list(result.stage_issues) if result.stage_issues else [issue]
        fix = fix_for(
            stage, issue, self._provider_display_name, language=self._config_panel.lookup_language()
        )
        guard = DependencyGuardDialog(txt.RUN_BLOCKED_TITLE, issues, fix.actions, self)
        guard.action_chosen.connect(self._run_fix)
        guard.exec()
        # The server may have been started while the dialog was open: look again.
        self._blocker = blocker
        self._update_readiness()
        self._start_readiness_check(fresh=True)

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
            self._queued_files_by_source_id = {str(file.resolve()): file for file in queued_files}
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

        self._steps_per_file = 1 if not config.correction_enabled else 2
        self._start_worker(config, selected_source_ids)

    def _start_worker(
        self,
        config: Config,
        selected_source_ids: list[str],
        files: list[Path] | None = None,
    ) -> None:
        """Start an OCR worker for *selected_source_ids*; *files* are their queued files."""
        if files is None:
            files = self._queued_files(selected_source_ids)
        self._log.clear_log()
        self._save_settings(notify=False)
        self._results_table.set_output_dir(config.output_dir)
        self._chart.clear_data()
        self._run_source_ids = list(selected_source_ids)
        # A document the run redoes is read-only from the run's start until its own result
        # arrives: an edit stored meanwhile would race the run. It stays the open document, so
        # its result reloads it (without a tab switch), and a run that ends before reaching it
        # reloads it too. Documents the run leaves alone stay open for work.
        self._run_pending = set(selected_source_ids)
        self._run_current = ""
        self._run_model = (
            short_model_name(config.effective_correction_model) if config.correction_enabled else ""
        )
        self._run_step = ""
        self._run_file_label = ""
        self._stage_step = ""
        self._stage_label = ""
        self._wait_shown = False
        self._run_waits_for_ollama = (
            config.correction_enabled and config.correction_provider == "ollama"
        )
        self._pace_waits_for_first_token = False
        self._apply_lock()
        self._result_names = []
        self._result_ocr_times = []
        self._result_correction_times = []
        self._time_driven = False
        self._progress.setRange(0, len(selected_source_ids) * self._steps_per_file)
        self._progress.setValue(0)
        self._start_pace(config, files)
        self._progress_timer.start()
        self._set_progress_text(f"Selected {len(selected_source_ids)} file(s) — Starting…")
        if self._run_from_check:
            # Started from the Check header: the teacher stays with the document.
            self._run_from_check = False
        else:
            self._tabs.setCurrentIndex(0)
        self._update_check_tab()

        worker_factory = self._worker_factory or OCRWorker
        self._worker = worker_factory(
            config,
            files,
            app_service=self._app_service,
        )
        self._worker.log_line.connect(self._log.append_text)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.stage_started.connect(self._on_stage_started)
        self._worker.ocr_done.connect(self._on_ocr_done)
        self._worker.stage_detail.connect(self._on_stage_detail)
        self._worker.first_token.connect(self._on_first_token)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.keep_awake_changed.connect(self._on_keep_awake)
        self._worker.run_finished.connect(self._on_run_finished)

        self._run_btn.setEnabled(False)
        self._set_stop_stage(_StopStage.RUNNING)
        self._awake_note.setVisible(False)
        self._battery_note.setVisible(False)
        self._worker.start()

    def _start_pace(self, config: Config, files: list[Path]) -> None:
        """Start the time-left figure: the estimate from the store, then the page counts.

        The estimate is for *config*, the configuration this run really starts with (after a
        right-click on a single step has switched the others off), not for the panel's ticks.

        The estimate is read from the store on this thread (a small file); the page counts open
        PDFs, so they are counted in the background and the figure is redrawn when they arrive.
        """
        self._pace = RunPace(self._estimate_per_page(config))
        self._pace.set_files(len(files))
        self._progress_snapshot = RunProgress()
        self._run_pages = {}
        if not files:
            return
        service = self._app_service
        self._pages_task.submit(lambda: service.run.page_counts(files), self._on_pages_known)

    def _estimate_per_page(self, config: Config) -> TimeRange | None:
        """Return how long one page takes on this computer, or ``None`` when nothing is known."""
        try:
            choices = self._app_service.speeds.choices_for_config(config)
            return self._app_service.speeds.speed_summary(choices).per_page
        except Exception:  # noqa: BLE001  # an estimate must never keep a run from starting
            return None

    def _on_pages_known(self, result: object, error: BaseException | None) -> None:
        """Take the page counts of the run's files and redraw the time left."""
        if error is not None or not isinstance(result, dict):
            return
        pages = cast("dict[str, int]", result)
        self._run_pages = pages
        self._pace.set_pages(sum(pages.values()))
        self._take_snapshot()
        self._set_progress_text(self._progress_base)
        self._refresh_check_run()

    def _on_stage_run_requested(self, selected_source_ids: list[str], stage: str) -> None:
        """Check the requirements, then invalidate cache from *stage* onward and run rows."""
        if self._run_in_progress():
            self._warn_busy()
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
        self._check.flush_pending_edit()
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

        self._steps_per_file = 1 if not config.correction_enabled else 2
        self._start_worker(config, [str(file.resolve()) for file in files], files)

    def _on_export_toolbar(self, kind: ExportKind) -> None:
        source_ids = (
            self._results_table.selected_source_ids() or self._results_table.queued_source_ids()
        )
        if source_ids:
            self._on_export_requested(source_ids, kind)

    def _on_export_requested(self, source_ids: list[str], kind: ExportKind) -> None:
        if kind.is_text and self._text_export_running:
            _dialogs.notify(self, dlg.busy_with_text_export())
            return
        targets = self._pick_export_targets(source_ids, kind)
        if not targets:
            return
        # One folder for several documents: two inputs with the same name would collide.
        targets, errors = self._drop_colliding_targets(targets)
        if len(source_ids) > 1 and not self._confirm_replace_existing(targets):
            return
        if kind.is_text and not self._confirm_text_export_folder(targets):
            return
        if kind is ExportKind.DOCX:
            self._export_docx(targets, errors)
        else:
            self._export_text(targets, errors, anonymize=kind is ExportKind.TEXT_ANONYMIZED)

    def _pick_export_targets(
        self, source_ids: list[str], kind: ExportKind
    ) -> list[tuple[str, Path]]:
        """Ask where to save and return ``(source_id, output path)`` pairs (empty = cancelled)."""
        # Suggested starting directory: last run output dir → config output → input dir
        start_dir = self._results_table.get_output_dir()
        if start_dir is None:
            d = self._config_panel.get_output_dir() or self._config_panel.get_input_dir()
            start_dir = Path(d) if d else Path.home()

        # For a single file show Save As; for multiple files show a folder picker.
        if len(source_ids) == 1:
            suggested = str(start_dir / (Path(source_ids[0]).stem + kind.file_suffix))
            dest = _dialogs.save_file(
                self, dlg.save_export(kind.label, kind.file_filter, suggested)
            )
            return [(source_ids[0], dest)] if dest else []
        folder = _dialogs.pick_folder(self, dlg.save_export_to_folder(kind.label, str(start_dir)))
        if folder is None:
            return []
        # Same-named documents from different subfolders keep their subfolders.
        input_dir = self._config_panel.get_input_dir()
        return [
            (
                sid,
                self._app_service.export.export_target(
                    Path(sid),
                    Path(input_dir) if input_dir else None,
                    Path(folder),
                    kind.file_suffix,
                ),
            )
            for sid in source_ids
        ]

    @staticmethod
    def _drop_colliding_targets(
        targets: list[tuple[str, Path]],
    ) -> tuple[list[tuple[str, Path]], list[str]]:
        """Keep the first target per output file; report the later ones that would replace it."""
        kept: list[tuple[str, Path]] = []
        errors: list[str] = []
        claimed: set[Path] = set()
        for source_id, out_path in targets:
            if out_path.resolve() in claimed:
                errors.append(
                    f"{Path(source_id).name}: {out_path.name} would overwrite another file "
                    "of this export."
                )
            else:
                claimed.add(out_path.resolve())
                kept.append((source_id, out_path))
        return kept, errors

    def _confirm_replace_existing(self, targets: list[tuple[str, Path]]) -> bool:
        """Ask once before a folder export replaces files (the Save dialog asks for one file)."""
        existing_paths = [out for _sid, out in targets if out.exists()]
        if not existing_paths:
            return True
        # Files of different subfolders can share a name, so show where they are.
        root = Path(os.path.commonpath([out.parent for out in existing_paths]))
        existing = [out.relative_to(root).as_posix() for out in existing_paths]
        shown = "\n".join(existing[:10]) + ("\n…" if len(existing) > 10 else "")
        return _dialogs.ask(self, dlg.replace_existing_files(len(existing), shown))

    def _confirm_text_export_folder(self, targets: list[tuple[str, Path]]) -> bool:
        """Warn when text files would land in the input folder, where the next run reads them.

        A one-off export only asks; a run with text files configured there is refused
        (:meth:`~teachers_teammate.application.run.RunService.output_refusal_for_run`)
        since it would repeat.
        """
        input_dir = self._config_panel.get_input_dir()
        # With an explicit file selection a run reads only those files.
        if not input_dir or self._config_panel.get_selected_files():
            return True
        # Folder mode always scans subfolders (see ConfigPanel.to_config).
        if not any(
            self._app_service.export.is_in_input_folder(out.parent, Path(input_dir), recursive=True)
            for _sid, out in targets
        ):
            return True
        return _dialogs.ask(self, dlg.save_into_input_folder())

    def _export_docx(self, targets: list[tuple[str, Path]], errors: list[str]) -> None:
        settings = self._config_panel.get_settings_dict()
        docx_format = str(settings.get("docx_format", "table"))
        # "Same as the text" (empty) means the language of the text, as in the pipeline.
        explanation_language = str(
            settings.get("explanation_language") or self._config_panel.to_dict().get("language", "")
        )
        try:
            config = self._config_panel.to_config()
        except ValueError as exc:
            errors.append(str(exc))
            _dialogs.notify(self, dlg.word_export_failed(errors))
            return
        for source_id, out_path in targets:
            try:
                # The texts, their notation and the review come from the stored document, so the
                # file carries what the Check tab shows.
                self._app_service.export.export_docx(
                    config,
                    Path(source_id),
                    out_path,
                    options=DocxExport(
                        docx_format=docx_format,
                        labels_language=explanation_language,
                    ),
                )
                self._log.append_text(f"DOCX saved: {out_path}\n")
            except Exception as exc:  # noqa: BLE001  # python-docx/PIL errors vary; collect per-file and continue the batch
                errors.append(f"{Path(source_id).name}: {exc}")

        if errors:
            _dialogs.notify(self, dlg.word_export_failed(errors))

    def _export_text(
        self, targets: list[tuple[str, Path]], errors: list[str], *, anonymize: bool
    ) -> None:
        """Write the extracted text of each target; anonymizing is slow, so it runs off-thread."""
        service = self._app_service
        language = str(self._config_panel.to_dict().get("language") or "English")
        anon_config = self._config_panel.build_anonymizer_config() if anonymize else None
        try:
            config = self._config_panel.to_config()
        except ValueError as exc:
            errors.append(str(exc))
            self._show_text_export_errors(errors)
            return
        # Every input of the run is protected, not just the row's own file: exporting
        # ``essay.png`` as ``essay.txt`` must not replace an ``essay.txt`` that is an input too.
        protected = [Path(sid) for sid in self._results_table.queued_source_ids()]
        jobs: list[tuple[Path, Path]] = []
        for source_id, out_path in targets:
            # The text and its notation come from the stored document, so the file carries the
            # teacher's transcription where they fixed or checked it.
            view = service.documents.load_view(config, Path(source_id))
            if view is None or not view.raw_text.strip():
                errors.append(f"{Path(source_id).name}: {NO_TEXT_MESSAGE}")
            else:
                jobs.append((Path(source_id), out_path))
        if not jobs:
            self._show_text_export_errors(errors)
            return
        preflight_errors = list(errors)

        def work() -> _TextExportOutcome:
            if anonymize:
                # Fails once, up front, when spaCy or its model is missing.
                service.anonymization.warm_up_anonymizer(language, anon_config)
            written: list[Path] = []
            failed = list(preflight_errors)
            for source, out_path in jobs:
                try:
                    service.export.export_text(
                        config,
                        source,
                        out_path,
                        anonymize=anonymize,
                        language=language,
                        anonymizer_config=anon_config,
                        protected=protected,
                    )
                    written.append(out_path)
                except Exception as exc:  # noqa: BLE001  # NER/IO errors vary; collect per-file and continue the batch
                    failed.append(f"{source.name}: {exc}")
            return _TextExportOutcome(written, failed)

        def done(result: object, error: BaseException | None) -> None:
            self._text_export_running = False
            if error is not None:
                message = (_spacy_problem(error) if anonymize else None) or str(error)
                _dialogs.notify(self, dlg.text_export_error(message, preflight_errors))
                return
            if not isinstance(result, _TextExportOutcome):
                return
            for out_path in result.written:
                self._log.append_text(f"Text saved: {out_path}\n")
            self._show_text_export_errors(result.failed)

        self._text_export_running = True
        run_in_background(work, done, owner=self)

    def _show_text_export_errors(self, errors: list[str]) -> None:
        if errors:
            _dialogs.notify(self, dlg.text_export_failed(errors))

    def _set_export_enabled(self, enabled: bool) -> None:
        """Enable the Export button and the File → Export entries together."""
        self._export_btn.setEnabled(enabled)
        for action in self._export_actions:
            action.setEnabled(enabled)

    def _restore_all_settings(self) -> None:
        """Reset every setting to its default and save (Settings already asked twice)."""
        self._config_panel.restore_defaults()
        # Not part of the settings file: the "hide personal information" view and the
        # remembered "Don't ask again" answers (so the next run asks before sending).
        self._apply_display_anonymized(DISPLAY_ANONYMIZED_DEFAULT)
        self._acknowledgements().forget_all()
        self._check.forget_anonymization()
        self._save_settings(notify=False)
        self._start_readiness_check()
        self._refresh_queue_from_inputs()

    def _on_results_changed(self) -> None:
        """Step "Results" changed: save it, and show what is cached for the new folder."""
        self._save_settings(notify=False)
        # Also asks the readiness check again ("Text file, anonymized" needs name detection).
        self._refresh_queue_from_inputs()

    def _on_instructions_changed(self) -> None:
        """The proofreading instructions changed: save them, and re-count the parts."""
        self._save_settings(notify=False)

    def _on_stop(self) -> None:
        """First click: finish the document being processed; second click: stop at once."""
        if not (self._worker and self._worker.isRunning()):
            return
        if self._stop_stage is _StopStage.RUNNING:
            self._worker.request_stop(now=False)
            self._set_stop_stage(_StopStage.AFTER_FILE)
        elif self._stop_stage is _StopStage.AFTER_FILE:
            self._worker.request_stop(now=True)
            self._set_stop_stage(_StopStage.NOW)

    def _set_stop_stage(self, stage: _StopStage) -> None:
        """Move the Stop button to *stage*: its text, tooltip and whether it can be pressed."""
        self._stop_stage = stage
        if stage is _StopStage.AFTER_FILE:
            self._stop_btn.setText(STOP_NOW_TEXT)
            self._stop_btn.setToolTip(STOP_NOW_TOOLTIP)
            self._stop_btn.setEnabled(True)
            self._set_progress_text(self._progress_base)
        elif stage is _StopStage.NOW:
            self._stop_btn.setEnabled(False)
            self._progress.setFormat(STOPPING_NOW_TEXT)
        else:
            self._stop_btn.setText(STOP_TEXT)
            self._stop_btn.setToolTip(STOP_TOOLTIP)
            self._stop_btn.setEnabled(stage is _StopStage.RUNNING)
        self._refresh_check_run()

    def _set_progress_text(self, text: str) -> None:
        """Show *text* in the progress bar, with a note while the run stops after this file."""
        self._progress_base = text
        if self._stop_stage is _StopStage.NOW:
            return
        suffix = STOPPING_SUFFIX if self._stop_stage is _StopStage.AFTER_FILE else ""
        self._progress.setFormat(text + self._time_left_suffix() + suffix)

    def _time_left_suffix(self) -> str:
        """Return the note about the time left, and put its range in the bar's tooltip.

        One figure in the bar, the range behind it: a range in the bar itself would not fit,
        and the teacher who wants to know how sure the figure is can hover over it.
        """
        snapshot = self._progress_snapshot
        self._progress.setToolTip(snapshot.tooltip)
        return " · " + snapshot.note if snapshot.note else ""

    def _take_snapshot(self) -> RunProgress:
        """Ask the pace where the run is now, and keep the answer for both displays."""
        self._progress_snapshot = self._pace.snapshot(time.monotonic())
        return self._progress_snapshot

    def _on_keep_awake(self, state: KeepAwakeState) -> None:
        """Say under the progress bar whether the computer is kept awake, and warn on battery."""
        if state.enabled:
            if state.held:
                text = AWAKE_HELD_TEXT
            elif state.available:
                text = AWAKE_REFUSED_TEXT
            else:
                text = AWAKE_UNAVAILABLE_TEXT
            self._awake_note.setText(text)
        self._awake_note.setVisible(state.enabled)
        self._battery_note.setVisible(bool(state.enabled and state.on_battery))

    # ── Worker signal handlers ────────────────────────────────────────────

    def _set_steps_done(self, steps: int) -> None:
        """Move the bar to *steps* finished steps, unless it follows the time of the run."""
        if not self._time_driven:
            self._progress.setValue(steps)

    def _refresh_progress(self) -> None:
        """Move the bar and the time left to where the clock says the run is.

        Without an estimate the pace has no figure and the bar keeps counting steps.
        """
        fraction = self._take_snapshot().fraction
        if fraction is None or not self._run_in_progress():
            return
        if not self._time_driven:
            self._time_driven = True
            self._progress.setRange(0, PROGRESS_RESOLUTION)
        # Full only when the run says so: a file that is nearly done is not yet done.
        self._progress.setValue(min(round(fraction * PROGRESS_RESOLUTION), PROGRESS_RESOLUTION - 1))
        self._set_progress_text(self._progress_base)
        self._refresh_check_run()

    def _on_file_started(self, source_id: str, name: str, idx: int, total: int) -> None:
        self._set_steps_done((idx - 1) * self._steps_per_file)
        self._pace.file_started(self._run_pages.get(source_id, 1))
        self._run_file_label = f"[{idx}/{total}] {name}"
        self._set_progress_text(f"{self._run_file_label} — starting…")
        self._results_table.mark_processing(source_id)
        self._run_current = source_id
        self._run_step = ""
        self._stage_step = ""
        self._stage_label = ""
        self._wait_shown = False
        self._apply_lock()
        # The text format is deliberately kept: the row still shows the previous texts, and
        # a format that no longer matches them would save an edit in the wrong notation.

    _STAGE_PROGRESS_LABEL: ClassVar[dict[str, str]] = {
        PROGRESS_STAGE_PREPROCESSING: check_text.PROGRESS_PREPROCESSING,
        PROGRESS_STAGE_OCR: check_text.PROGRESS_OCR,
        PROGRESS_STAGE_CORRECTION: check_text.PROGRESS_CORRECTING,
    }

    #: What the Check tab's run strip says while a step of the run's file is on.
    _RUN_STEP_TEXT: ClassVar[dict[str, str]] = {
        PROGRESS_STAGE_PREPROCESSING: check_text.RUN_PREPARING,
        PROGRESS_STAGE_OCR: check_text.RUN_READING,
        PROGRESS_STAGE_CORRECTION: "",  # empty: the strip names the proofreading model
    }

    def _on_stage_started(
        self, _source_id: str, name: str, stage: str, idx: int, total: int
    ) -> None:
        # A proofreading on an Ollama server may wait for the model: its clock starts at the first
        # token (see _on_first_token), so neither the bar nor the time left move during a wait.
        self._pace_waits_for_first_token = bool(
            stage == PROGRESS_STAGE_CORRECTION and self._run_waits_for_ollama
        )
        if not self._pace_waits_for_first_token:
            self._pace.work_started(time.monotonic())
        self._run_file_label = f"[{idx}/{total}] {name}"
        label = self._STAGE_PROGRESS_LABEL.get(stage)
        if label is not None:
            self._set_progress_text(f"{self._run_file_label} — {label}")
            self._stage_label = label
        self._stage_step = self._RUN_STEP_TEXT.get(stage, self._run_step)
        self._run_step = self._stage_step
        self._wait_shown = False
        self._refresh_check_run()

    def _on_stage_detail(self, source_id: str, detail: object) -> None:
        """Say what Ollama is doing with the request, in the bar and in the Check tab."""
        if source_id != self._run_current or not isinstance(detail, WaitDetail):
            return
        text = _wait_detail_text(detail)
        if not text:
            return
        self._wait_shown = True
        self._run_step = text
        self._set_progress_text(f"{self._run_file_label} — {text}")
        self._refresh_check_run()

    def _on_first_token(self, source_id: str) -> None:
        """The model began answering: a wait note goes, and the proofreading's clock starts."""
        if source_id != self._run_current:
            return
        if self._pace_waits_for_first_token:
            self._pace_waits_for_first_token = False
            self._pace.work_started(time.monotonic())
        if self._wait_shown:
            self._wait_shown = False
            self._run_step = self._stage_step
            if self._stage_label:
                self._set_progress_text(f"{self._run_file_label} — {self._stage_label}")
        self._refresh_check_run()

    def _on_file_done(self, event: FileDoneEvent) -> None:
        outcome = event.outcome
        source_id = outcome.source_id
        name = outcome.name
        ocr_s = event.ocr_s
        correction_s = event.correction_s
        self._results_table.add_result(event)
        self._set_export_enabled(True)
        # A skipped or stopped file is not an error: its row says why, the final line counts it.
        if outcome.status is FileStatus.FAILED and outcome.error:
            _dialogs.notify(self, dlg.processing_error(name, outcome.error))
        self._result_names.append(name)
        self._result_ocr_times.append(ocr_s)
        self._result_correction_times.append(correction_s)
        self._pace.file_done(event.total_s, self._run_pages.get(source_id, 1))
        self._set_steps_done(len(self._result_names) * self._steps_per_file)
        self._set_progress_text(self._progress_base)
        self._refresh_progress()
        self._chart.update_data(
            self._result_names,
            self._result_ocr_times,
            self._result_correction_times,
        )

        # Only the open document is reloaded: a finished file never takes the tab away from
        # the document the teacher is working on, and never switches tabs.
        self._run_pending.discard(source_id)
        if self._run_current == source_id:
            self._run_current = ""
        # The open document is read once, for the page and the row together; any other row
        # is read for itself.  Both refresh from the persisted state even on failure or stop,
        # when on_ocr_done never fired.
        if not (source_id == self._current_preview_source_id and self._load_check(source_id)):
            self._sync_row(source_id)
            self._apply_lock()
        self._update_check_tab()

    def _sync_row(self, source_id: str) -> None:
        """Show what the store now says about *source_id* in its row.

        The row is the store's answer, so every action that changes a document ends here
        instead of patching cells: a stage that ran, an edit, a *Checked* mark.
        """
        source = self._queued_files_by_source_id.get(source_id) or Path(source_id)
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

    def _start_readiness_check(self, *, fresh: bool = False) -> None:
        """Ask in the background whether a run could start, then update the line.

        *fresh* asks the Ollama server again instead of using the model list it gave a few
        minutes ago: wanted when the teacher asks to look again (a model pulled in a terminal).

        The check contacts Ollama, may import spaCy and looks for the built-in reader, so it never runs
        on the GUI thread.  Only the latest answer counts.  Every place that changes what a
        run needs calls this: Settings, a finished download, a new window activation.
        """
        self._readiness_debounce.stop()
        if not self._first_check_answered:
            self._checking_timer.start()
        service = self._app_service
        config = self._config_panel.lookup_config()
        recognition = self._recognition_needed()
        files = self._queued_files()
        recognised = self._recognised_files()
        self._readiness_task.submit(
            lambda: service.run.check_run_preflight(
                config,
                memory=False,
                recognition=recognition,
                fresh=fresh,
                files=files,
                recognised=recognised,
            ),
            self._on_readiness_check_done,
        )

    def _schedule_readiness_check(self) -> None:
        """Check soon, once, however many settings changed in a row."""
        self._readiness_debounce.start()

    def _on_readiness_check_done(self, result: object, error: BaseException | None) -> None:
        self._checking_timer.stop()
        self._first_check_answered = True
        if error is not None or not isinstance(result, RunPreflight):
            # The check itself failed: say nothing wrong; Run checks again and reports.
            self._blocker = None
            self._skipped_scans = set()
        else:
            self._blocker = result.blocker
            self._skipped_scans = set(result.skipped)
        unreachable = self._blocker is not None and self._blocker[1].kind in _OLLAMA_KINDS
        if unreachable:
            self._ollama_retry_timer.start()
            self._search_local_server()
        else:
            self._ollama_retry_timer.stop()
            self._found_server = None
            self._server_searched_for = ""
        self._update_readiness()

    def _search_local_server(self) -> None:
        """Look quietly for Ollama on this computer (and the Windows host of WSL), once.

        Only this computer is asked, never the network.  A hit is offered on the readiness
        line; the saved address changes only when the teacher presses the button.
        """
        url = self._current_ollama_url()
        if self._server_searched_for == url:
            return
        self._server_searched_for = url
        service = self._app_service
        self._server_search_task.submit(
            lambda: service.ollama.discover_ollama_servers(DiscoveryScope.THIS_COMPUTER),
            lambda result, error: self._on_local_server_search_done(url, result, error),
        )

    def _on_local_server_search_done(
        self, url: str, result: object, error: BaseException | None
    ) -> None:
        if error is not None or not isinstance(result, list) or url != self._current_ollama_url():
            return

        saved = self._app_service.ollama.normalize(url)
        self._found_server = next(
            (
                s
                for s in result
                if isinstance(s, DiscoveredServer)
                and self._app_service.ollama.normalize(s.url) != saved
            ),
            None,
        )
        self._update_readiness()

    def _use_ollama_server(self, url: str) -> None:
        """Take over the address of a server the teacher chose on the readiness line."""
        self._found_server = None
        self._config_panel.update_settings({"ollama_url": url})
        self._save_settings(notify=False)
        self._start_readiness_check(fresh=True)
        self._refresh_queue_from_inputs()

    def _show_checking(self) -> None:
        self._checking_visible = not self._first_check_answered
        self._update_readiness()

    def _update_readiness(self) -> None:
        """Show what the last check and the cheap facts (documents, selection, folder) say.

        Instant and on the GUI thread: it asks no server.  The line keeps the last answer
        while a newer check runs, so it doesn't flicker.
        """
        panel = self._config_panel
        blocker_fix: Fix | None = None
        if self._blocker is not None:
            stage, issue = self._blocker
            blocker_fix = fix_for(
                stage, issue, self._provider_display_name, language=panel.lookup_language()
            )
            if issue.kind in _OLLAMA_KINDS and self._found_server is not None:
                blocker_fix = found_server_fix(
                    _SERVER_PLACES[self._found_server.kind], self._found_server.url
                )
        results = self._results_problem()
        # The Check tab's next-step button proofreads, or sets proofreading up when the last
        # check found the proofreading step unable to run.
        self._check.set_proofreading_ready(
            self._blocker is None or self._blocker[0] != "correction"
        )
        self._show_recognition_state()
        self._readiness_line.show_state(
            readiness(
                blocker=blocker_fix,
                results=results,
                inputs_chosen=bool(panel.get_input_dir()),
                files=len(self._results_table.queued_source_ids()),
                selected=len(self._results_table.selected_source_ids()),
                checking=self._checking_visible and not self._first_check_answered,
            )
        )

    def _show_recognition_state(self) -> None:
        """Say what the "Recognize text" switch does to the queue: the note and the rows.

        Off: the scans the last check found are marked "skipped" and counted under the
        switch.  On: no row is marked, and the note says so when no document needs reading.
        """
        panel = self._config_panel
        off = not panel.recognition_enabled()
        skipped = self._skipped_scans if off else set()
        self._results_table.set_skipped_sources(
            {str(path.resolve()) for path in skipped}, txt.RECOGNITION_OFF_REASON
        )
        files = self._queued_files()
        panel.show_recognition_note(
            skipped=len(skipped),
            not_needed=bool(files)
            and not self._app_service.run.needs_text_recognition(
                files, recognised=self._recognised_files()
            ),
        )

    def _results_problem(self) -> Fix | None:
        """Return the fix for a Results step that a run would refuse, or ``None``."""
        panel = self._config_panel
        if panel.results_problem():
            return results_fix(RESULTS_NO_FOLDER)
        if panel.get_input_dir() and self._app_service.run.output_refusal_for_run(
            panel.lookup_config()
        ):
            return results_fix(RESULTS_TEXT_IN_INPUT)
        return None

    def _run_fix(self, action: FixAction) -> None:
        """Do what a fix button says: open its Settings page, check again, or choose a folder."""
        if action.page is not None:
            self.open_settings(action.page, focus=action.focus, preselect=list(action.preselect))
        elif action.command == COMMAND_RETRY:
            self._start_readiness_check(fresh=True)
        elif action.command == COMMAND_CHOOSE_FOLDER:
            self._config_panel.choose_output_dir()
        elif action.command == COMMAND_TURN_ON_RECOGNITION:
            self._config_panel.turn_on_recognition()
        elif action.command == COMMAND_USE_SERVER:
            self._use_ollama_server(action.argument)
        elif action.command == COMMAND_GET_OLLAMA:
            QDesktopServices.openUrl(QUrl(OLLAMA_DOWNLOAD_URL))
        elif action.command == COMMAND_START_OLLAMA:
            self._start_ollama()

    def _start_ollama(self) -> None:
        """Start the installed Ollama, then look again once it has had time to come up.

        Starting it takes a moment, so the check runs a few seconds later instead of at once;
        the 10 s retry keeps looking until the server answers.
        """
        run_in_background(self._app_service.ollama.start_ollama, self._on_ollama_start_done)

    def _on_ollama_start_done(self, _result: object, error: BaseException | None) -> None:
        """Look again in a few seconds, or tell the teacher in the log why Ollama did not start."""
        if error is not None:
            self._log.append_text(f"NOTICE: Ollama could not be started: {error}\n")
            return
        QTimer.singleShot(3000, lambda: self._start_readiness_check(fresh=True))

    def changeEvent(self, event: QEvent) -> None:
        """Look again when the window becomes active: something may have been installed.

        A key set outside the app, ``ollama pull`` run in a terminal, or a spaCy model
        installed with pip is only seen then.
        """
        super().changeEvent(event)
        if event.type() != QEvent.Type.ActivationChange or not self.isActiveWindow():
            return
        self._app_service.anonymization.reread_installed_models()
        self._refresh_privacy_availability()
        if self._blocker is not None:
            self._start_readiness_check(fresh=True)

    # ── Ollama pull progress (status bar area) ────────────────────────────────────

    def _on_pull_started(self, model_name: str) -> None:
        self._readiness_line.show_download(model_name)

    def _on_pull_progress(self, _status: str, completed: int, total: int) -> None:
        if total > 0:
            mb_done = completed / 1_048_576
            mb_total = total / 1_048_576
            self._readiness_line.set_download_progress(
                int(100 * completed / total), f"{mb_done:.1f} / {mb_total:.1f} MB"
            )

    def _on_pull_finished(self, _model_name: str) -> None:
        # Also covers pulls that finish after the Settings window was closed:
        # without this the cached model list stays stale for minutes.
        self._app_service.recommendations.on_ollama_models_changed(
            self._pull_manager.current_url or self._current_ollama_url()
        )
        self._end_download()

    def _on_pull_error(self, msg: str) -> None:
        self._end_download()
        _dialogs.notify(self, dlg.model_download_failed(msg))

    def _end_download(self) -> None:
        """Hand the line back and look again: the model that was missing may be there now."""
        self._readiness_line.end_download()
        QTimer.singleShot(0, self._start_readiness_check)

    def _on_run_finished(self, summary: RunSummary) -> None:
        """Announce how the run ended: done, stopped (not an error) or finished with errors."""
        self._run_btn.setEnabled(True)
        self._set_stop_stage(_StopStage.IDLE)
        self._awake_note.setVisible(False)
        self._battery_note.setVisible(False)
        self._progress_timer.stop()
        text = describe_run(summary)
        if text.complete:
            self._progress.setValue(self._progress.maximum())
        self._progress_base = text.progress
        self._progress.setFormat(text.progress)
        self._progress.setToolTip("")
        self._pace = RunPace()
        self._progress_snapshot = RunProgress()
        self._pace_waits_for_first_token = False
        self._run_source_ids = []
        reload = self._current_preview_source_id in self._run_pending
        self._run_pending = set()
        self._run_current = ""
        if reload:
            # The run ended (stopped, failed) before it reached the open document: show what
            # it still has instead of leaving the tab locked.
            self._load_check(self._current_preview_source_id)
        else:
            self._apply_lock()
        self._update_check_tab()
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if text.tone is RunTone.WARNING
            else QSystemTrayIcon.MessageIcon.Information
        )
        self._tray.showMessage("Teacher's Teammate", text.tray, icon, 4000)

    def _on_ocr_done(self, source_id: str, name: str, idx: int, total: int) -> None:
        self._run_step = ""  # the reading is over: what goes on now is the proofreading
        self._stage_step = ""
        self._stage_label = check_text.PROGRESS_CORRECTING
        self._wait_shown = False
        self._refresh_check_run()
        if self._steps_per_file > 1:
            self._set_steps_done((idx - 1) * 2 + 1)
            self._set_progress_text(f"[{idx}/{total}] {name} — {check_text.PROGRESS_CORRECTING}")
            # The reading is stored, so the row can be opened while the proofreading runs.
            self._sync_row(source_id)
            self._results_table.mark_stage_running(source_id, STAGE_CORRECTION)

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

    # ── The Check tab ─────────────────────────────────────────────────────

    def _load_check(self, source_id: str) -> bool:
        """Show *source_id* in the Check tab, with its texts and the items of its review.

        The loader never switches tabs. A finished file reloads the open document, and only
        the teacher (or a run start, which goes to Documents) decides which tab is in front.
        Returns whether the document was shown; when it was not, the teacher was told why and
        what was open before stays open.
        """
        doc = self._check_document(source_id)
        if doc is None:
            _dialogs.notify(self, dlg.check_without_state(Path(source_id).name))
            # What was open stays open; a lock the run put on it must still lift with the run.
            self._apply_lock()
            self._update_check_tab()
            return False
        self._current_preview_source_id = source_id
        self._check.show_document(doc, *self._check_position(source_id))
        self._set_copy_state(doc.raw_text, doc.corrected_text)
        self._sync_row(source_id)
        self._apply_lock()
        self._update_check_tab()
        return True

    def _check_document(self, source_id: str) -> CheckDocument | None:
        """Return what the Check tab shows about *source_id*, or ``None`` without a state."""
        try:
            config = self._config_panel.to_config()
        except ValueError:
            # A setting that is not valid yet (no output folder) must not hide stored results:
            # reading them needs no more than the lookup config does.
            config = self._config_panel.lookup_config()
        return self._app_service.documents.check_document(config, Path(source_id))

    def _apply_lock(self) -> None:
        """Make the open document read-only while the run works on it, or free it again."""
        source_id = self._current_preview_source_id
        if not source_id:
            return
        if source_id == self._run_current:
            self._check.set_lock(txt.BEING_PROOFREAD)
        elif source_id in self._run_pending:
            self._check.set_lock(txt.WAITING_FOR_RUN)
        else:
            self._check.set_lock(None)
        self._refresh_check_run()

    def _refresh_check_run(self) -> None:
        """Tell the Check tab what the run does with the open document: working, waiting, or not.

        Fed from what the window already follows (the run's file, step and pace, and the Stop
        stage), so the teacher who proofreads from Check sees the run without leaving the tab.
        """
        source_id = self._current_preview_source_id
        if not source_id or not (source_id == self._run_current or source_id in self._run_pending):
            self._check.set_run(None)
            return
        stop_text = {
            _StopStage.RUNNING: STOP_TEXT,
            _StopStage.AFTER_FILE: STOP_NOW_TEXT,
            _StopStage.NOW: STOP_NOW_TEXT,
        }.get(self._stop_stage, "")
        stop_enabled = self._stop_stage in (_StopStage.RUNNING, _StopStage.AFTER_FILE)
        if source_id != self._run_current:
            self._check.set_run(
                RunState(
                    check_text.RUN_WAITING,
                    busy=False,
                    stop_text=stop_text,
                    stop_enabled=stop_enabled,
                )
            )
            return
        step = self._run_step or (
            check_text.RUN_PROOFREADING.format(model=self._run_model)
            if self._run_model
            else check_text.RUN_READING
        )
        snapshot = self._progress_snapshot
        self._check.set_run(
            RunState(
                step,
                time_left=snapshot.note,
                time_left_tip=snapshot.tooltip,
                stop_text=stop_text,
                stop_enabled=stop_enabled,
            )
        )

    def _store_text_zoom(self, point_size: int) -> None:
        """Remember the text size the teacher zoomed to, for the next document and restart."""
        set_text_point_size(self._settings_store, point_size)

    def _store_check_layout(self, layout: CheckLayout) -> None:
        """Note how the teacher sized the Check tab's panes; stored once the drag settles."""
        self._pending_check_layout = layout
        self._layout_debounce.start()

    def _flush_check_layout(self) -> None:
        """Remember the last pane sizes, for the next restart."""
        layout, self._pending_check_layout = self._pending_check_layout, None
        if layout is None:
            return
        set_check_split(self._settings_store, layout.split)
        set_check_card_split(self._settings_store, layout.card_split)

    def _on_check_proofread_requested(self) -> None:
        """Proofread the open document: the header's button and ⋯ → *Proofread again*."""
        source_id = self._current_preview_source_id
        if not source_id:
            return
        if self._run_in_progress():
            self._warn_busy()  # nothing starts, so the tab must not be marked as the origin
            return
        self._run_from_check = True
        self._on_stage_run_requested([source_id], "correction")

    def _on_item_shown(self, key: str) -> None:
        """An item became the one the card shows: record that the teacher looked at it.

        Stored at once, a few KB, and never undone.  Looking at an item is not work a run has to
        keep, so it does not make the proofreading one to keep.
        """
        source_id = self._current_preview_source_id
        if not source_id or not self._check.mark_looked_at(key):
            return
        try:
            config = self._config_panel.to_config()
        except ValueError:
            config = self._config_panel.lookup_config()
        self._app_service.commands.mark_looked_at(config, source=Path(source_id), keys=[key])

    def _on_checked_toggled(self, checked: bool) -> None:
        """The teacher pressed *Checked*: store the decision and show what the store says.

        The toggle marks what the document has now — the corrections with a proofreading, the
        transcription without one — and is its own undo.  It does not move to the next document;
        ▶ does.  A typed edit is stored first, so the mark names the text the teacher sees.
        """
        source_id = self._current_preview_source_id
        if not source_id:
            return
        self._check.flush_pending_edit()
        source = Path(source_id)
        try:
            config = self._config_panel.to_config()
        except ValueError:
            # Without folders there is no store to write to: the button goes back to what the
            # document says, not to what was pressed.
            _dialogs.notify(self, dlg.checked_not_saved())
            self._check.refresh_checked()
            return
        try:
            view = self._app_service.commands.set_checked(config, source=source, checked=checked)
        except WorkNotSavedError as exc:
            _dialogs.notify(self, dlg.work_not_saved(exc.name, too_new=exc.too_new))
            self._check.refresh_checked()
            return
        if view is None:
            _dialogs.notify(self, dlg.checked_not_saved())
            # The button goes back to what the store says, not to what was pressed.
            self._load_check(source_id)
            return
        doc = self._app_service.documents.check_document(config, source)
        self._sync_row(source_id)
        if doc is not None:
            self._check.update_document(doc)
        self._update_check_tab()

    def _check_position(self, source_id: str) -> tuple[int, int]:
        """Return where *source_id* sits among the documents the Check tab can open."""
        openable = self._results_table.checkable_source_ids()
        position = openable.index(source_id) + 1 if source_id in openable else 0
        return position, len(openable)

    def _open_check(self, source_id: str) -> None:
        """Show *source_id* in the Check tab: what a double-click, Enter or the menu asks for.

        The row is selected too, so the queue shows the document that is open.  Clicking the
        tab itself does not do this: a selection is how rows are chosen for a run, and reading
        it on every tab click would swap the essay under the teacher.
        """
        self._load_check(source_id)
        self._results_table.select_source(source_id)
        self._tabs.setCurrentIndex(1)

    def _set_copy_state(self, raw_txt: str, corr_txt: str) -> None:
        """Say what the Copy menu may offer for a document with these texts."""
        self._copy_state = CopyState(
            has_raw=bool(raw_txt.strip()),
            has_corrected=bool(corr_txt.strip()),
            can_hide_names=self._privacy_available,
        )
        self._check.set_copy_state(self._copy_state)

    def _refresh_copy_state(self) -> None:
        """Bring the Copy menu of the open document up to date (names may now be hideable)."""
        if self._copy_state is None:
            return
        self._check.set_copy_state(
            CopyState(
                has_raw=self._copy_state.has_raw,
                has_corrected=self._copy_state.has_corrected,
                can_hide_names=self._privacy_available,
            )
        )

    def _update_check_tab(self) -> None:
        """Label both tabs and enable Check only while a document can be opened."""
        self._tabs.setTabEnabled(1, bool(self._results_table.checkable_source_ids()))
        self._tabs.setTabText(0, self._documents_tab_label())
        self._tabs.setTabText(1, self._check_tab_label())
        self._tabs.setTabToolTip(0, "Documents (Ctrl+1)")
        self._tabs.setTabToolTip(1, self._check_tab_tooltip())

    def _documents_tab_label(self) -> str:
        """``Documents``, or how far the running batch has got."""
        if not self._run_in_progress() or not self._run_source_ids:
            return "Documents"
        return f"Documents \u00b7 {len(self._result_names)} of {len(self._run_source_ids)}"

    def _check_tab_label(self) -> str:
        """``Check``, or the name of the open document."""
        name = self._open_document_name()
        if not name:
            return "Check"
        if len(name) > _CHECK_TAB_NAME_CHARS:
            name = name[: _CHECK_TAB_NAME_CHARS - 1] + "\u2026"
        return f"Check \u2014 {name}"

    def _check_tab_tooltip(self) -> str:
        """The full name of the open document above the shortcut, so a short label stays clear."""
        name = self._open_document_name()
        return f"{name}\nCheck (Ctrl+2)" if name else "Check (Ctrl+2)"

    def _open_document_name(self) -> str:
        """The name of the document the Check tab shows, or ``""`` when none is open."""
        row = self._results_table.row_for(self._current_preview_source_id)
        return row.name if row is not None else ""

    def _sync_check_with_queue(self) -> None:
        """Close the Check tab when its document is no longer in the queue."""
        if not self._current_preview_source_id:
            return
        if self._results_table.row_for(self._current_preview_source_id) is not None:
            return
        self._close_check()

    def _close_check(self) -> None:
        """Forget the open document and go back to Documents."""
        self._current_preview_source_id = ""
        self._check.clear()
        self._tabs.setCurrentIndex(0)

    def _on_tab_changed(self, index: int) -> None:
        """Opening the Check tab with nothing open shows a document; the tab is never empty."""
        if index == 0:
            self._run_from_check = False  # a request that never started a run is forgotten
        if index != 1 or self._current_preview_source_id:
            return
        target = self._check_target()
        if target:
            self._load_check(target)

    def _check_target(self) -> str:
        """The document Check opens when none is open: the current row, else the first."""
        openable = self._results_table.checkable_source_ids()
        selected = self._results_table.selected_source_ids()
        current = selected[0] if selected else ""
        if current in openable:
            return current
        return openable[0] if openable else ""

    def _show_documents_tab(self) -> None:
        """Go back to the queue: Ctrl+1, or Esc in the Check tab."""
        self._tabs.setCurrentIndex(0)

    def _show_check_tab(self) -> None:
        """Go to the Check tab, opening a document when none is open (Ctrl+2)."""
        if self._tabs.isTabEnabled(1):
            self._tabs.setCurrentIndex(1)  # _on_tab_changed opens a document when none is

    def _on_check_previous(self) -> None:
        """Show the document before the open one in the queue."""
        self._step_check(-1)

    def _on_check_next(self) -> None:
        """Show the document after the open one in the queue."""
        self._step_check(1)

    def _step_check(self, step: int) -> None:
        """Move *step* documents on and select the row that is now shown.

        The selection follows the open document, because a run takes its documents from the
        selected rows: the teacher moving through the set keeps the queue in step.
        """
        openable = self._results_table.checkable_source_ids()
        if self._current_preview_source_id not in openable:
            return
        index = openable.index(self._current_preview_source_id) + step
        if not 0 <= index < len(openable):
            return
        target = openable[index]
        self._load_check(target)
        self._results_table.select_source(target)

    def _on_check_copy(self, kind: CopyKind) -> None:
        """Put the open document's text on the clipboard, as the Copy menu asked."""
        source_id = self._current_preview_source_id
        if not source_id:
            return
        self._check.flush_pending_edit()  # a typed edit must be in the text that is copied
        try:
            config = self._config_panel.to_config()
        except ValueError:
            return
        service = self._app_service
        # The text comes from the stored document, so a copy carries what the Check tab shows.
        view = service.documents.load_view(config, Path(source_id))
        if view is None:
            return
        if not kind.is_anonymized:
            QApplication.clipboard().setText(
                service.export.text_for_copy(view, corrected=kind.is_corrected)
            )
            self._check.show_copied()
            return
        language = self._config_panel.lookup_language()
        anon_config = self._config_panel.build_anonymizer_config()

        def _copy() -> object:
            return service.export.text_for_copy(
                view,
                corrected=kind.is_corrected,
                anonymize=True,
                language=language,
                anonymizer_config=anon_config,
            )

        def _on_done(result: object, error: BaseException | None) -> None:
            if error is not None:
                _dialogs.notify(self, dlg.copy_failed(_spacy_problem(error) or str(error)))
                return
            QApplication.clipboard().setText(str(result))
            self._check.show_copied()

        # Hiding names runs spaCy, which is not a job for the GUI thread.
        run_in_background(_copy, _on_done, owner=self)

    def _on_check_save(self, kind: ExportKind) -> None:
        """Save the open document's text through the window's own export path."""
        source_id = self._current_preview_source_id
        if not source_id:
            return
        self._check.flush_pending_edit()
        self._on_export_requested([source_id], kind)

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

    def _open_source_for_edit(self) -> tuple[Path, bool] | None:
        """Resolve the open document to its input path and whether it has a transcription.

        An edit belongs to the document the Check tab shows, not to the selected row: the
        selection is how a run picks its documents, and the two may differ.
        """
        source_id = self._current_preview_source_id
        if not source_id:
            return None
        source = Path(source_id)
        if not source.exists():
            return None
        return source, self._results_table.has_text(source_id)

    def _on_ocr_text_edited(self, edited_text: str) -> None:
        """The transcription was typed into: store it and show what the store now says."""
        self._store_edit(_EDIT_RAW, edited_text)

    def _on_correction_text_edited(self, edited_text: str) -> None:
        """The final text was typed into: store it and show what the store now says."""
        self._store_edit(_EDIT_CORRECTED, edited_text)

    def _store_edit(self, kind: str, edited_text: str) -> None:
        """Store one typed edit of the open document, then show the document it left behind.

        One handler for both texts: they differ only in which part of the teacher's work the
        store writes, and everything after it — the row, the page, the tab label — is the same.
        """
        selected = self._open_source_for_edit()
        if selected is None:
            return
        source, has_text = selected
        if kind == _EDIT_CORRECTED and not has_text:
            return
        try:
            config = self._config_panel.to_config()
        except ValueError:
            return
        source_id = str(source.resolve())
        text_format = self._results_table.text_format_for_source(source_id)
        try:
            if kind == _EDIT_RAW:
                view = self._app_service.commands.edit_transcription(
                    config, source=source, text=edited_text, text_format=text_format
                )
            else:
                view = self._app_service.commands.edit_corrected_text(
                    config, source=source, text=edited_text
                )
        except WorkNotSavedError as exc:
            # What was typed stays in the text view, so the teacher can still copy it.
            _dialogs.notify(self, dlg.work_not_saved(exc.name, too_new=exc.too_new))
            return
        if view is None:
            return
        # One read of the stored state serves the row and the page.
        doc = self._app_service.documents.check_document(config, source)
        self._sync_row(source_id)
        if doc is None:
            return
        if kind == _EDIT_RAW:
            # The proofreading went with the transcription: the page falls back to it.
            self._check.show_document(doc, *self._check_position(source_id), typed=True)
            self._set_copy_state(doc.raw_text, doc.corrected_text)
        else:
            self._check.update_document(doc)
        self._update_check_tab()

    def _on_what_is_sent_requested(self, source_id: str) -> None:
        """Open "What is sent for this file…" for one row of the results table."""
        row = self._results_table.row_for(source_id)
        name = row.name if row is not None else Path(source_id).name
        if not self._results_table.has_text(source_id):
            _dialogs.notify(self, privacy.what_is_sent_without_text())
            return

        config = self._config_panel.lookup_config()

        busy = QDialog(self)
        busy.setWindowTitle(privacy.what_is_sent_title(name))
        busy_vbox = QVBoxLayout(busy)
        busy_vbox.addWidget(QLabel(privacy.WHAT_IS_SENT_WORKING))
        service = self._app_service

        def _describe() -> object:
            # The stored results say what really went out; the report says what would go next.
            view = service.documents.load_view(config, Path(source_id))
            if view is None:
                return None
            return service.documents.describe_sent_texts(config, view)

        def _on_done(result: object, error: BaseException | None) -> None:
            busy.accept()
            if error is not None:
                _dialogs.notify(
                    self, privacy.what_is_sent_failed(_spacy_problem(error) or str(error))
                )
                return
            if not isinstance(result, SentTexts):
                _dialogs.notify(self, privacy.what_is_sent_unreadable())
                return
            self._show_what_is_sent(result, name)

        self._sent_texts_task.submit(_describe, _on_done)
        busy.exec()

    # ── hiding personal information in the view ──────────────────────────────────

    def _apply_display_anonymized(self, enabled: bool) -> None:
        """Store the view setting and show the texts accordingly.

        Called by the eye button in the preview panel and by Settings → Privacy, so both
        stay in step and the preference survives a restart.
        """
        enabled = bool(enabled) and self._privacy_available
        set_display_anonymized(self._settings_store, enabled)
        self._display_anonymized = enabled
        self._check.set_display_anonymized(enabled)

    def _refresh_privacy_availability(self) -> None:
        """Enable the setting only while personal information can actually be hidden.

        Needs the Privacy add-on and the spaCy models the view would load, resolved the way a
        run resolves them — so a chosen primary model counts.  Asked every time instead of
        cached: the answer is cheap, and a cache is what went stale when a download finished
        after the window that started it had closed.
        """
        language = self._config_panel.lookup_language()
        available = self._app_service.anonymization.anonymizer_available(
            language, self._config_panel.build_anonymizer_config()
        )
        changed = available != self._privacy_available
        self._privacy_available = available
        self._check.set_display_anonymized_available(available)
        self._refresh_copy_state()
        if changed and available and self._display_anonymized:
            # The setting was on but could not be applied; it can now.
            self._check.set_display_anonymized(True)

    def _on_anonymization_needed(self, raw_text: str) -> None:
        """Start hiding the personal information of the text the preview shows."""
        if not raw_text.strip():
            self._check.set_anonymization({})
            return
        language = self._config_panel.lookup_language()
        if self._anonymize_running and self._anonymize_key == (raw_text, language):
            return  # already being worked out
        self._anonymize_key = (raw_text, language)
        self._anonymize_running = True
        service = self._app_service
        anon_config = self._config_panel.build_anonymizer_config()

        def _anonymize() -> object:
            return service.anonymization.anonymize_preview(raw_text, language, anon_config)

        def _on_done(result: object, error: BaseException | None) -> None:
            self._on_anonymization_done(result, error)

        # Only the latest request counts: a slow result for a text the preview already left
        # is dropped, and no thread object can be destroyed while it still runs.
        self._anonymize_task.submit(_anonymize, _on_done)

    def _on_anonymization_done(self, payload: object, error: BaseException | None) -> None:
        """Hand the placeholders to the Check tab (only the newest request gets here)."""
        self._anonymize_key = None
        self._anonymize_running = False
        if error is not None or not isinstance(payload, tuple) or len(payload) != 2:
            reason = (
                (_spacy_problem(error) or str(error)) if error is not None else dlg.UNKNOWN_ERROR
            )
            self._check.set_anonymization({}, error=reason)
            return
        _anonymized, raw_mapping = payload
        mapping = (
            {str(key): str(value) for key, value in raw_mapping.items()}
            if isinstance(raw_mapping, dict)
            else {}
        )
        self._check.set_anonymization(mapping)

    def _on_data_flow_changed(self, flow: object) -> None:
        """Say under the proofreading and content-review cells where their text goes."""
        if not isinstance(flow, DataFlow):
            return
        self._results_table.set_stage_tooltips(
            {stage.stage: stage_tooltip(stage) for stage in flow.stages if not stage.detail}
        )
        self._refresh_privacy_availability()  # the document language may have changed
        self._schedule_readiness_check()  # ... and what a run needs with it

    def _show_what_is_sent(self, report: SentTexts, name: str) -> None:
        """Show the report for one file, remembering it so tests and callers can read it."""
        self._last_sent_texts = report
        WhatIsSentDialog(report, file_name=name, parent=self).exec()

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
            display_anonymized=self._display_anonymized,
            privacy_available=self._privacy_available,
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
            lambda ocr_config: self._on_preprocess_preview_requested(ocr_config, parent=window)
        )
        window.packages_changed.connect(self.packages_changed)
        window.addon_installed.connect(self._on_addon_installed)
        # What the Privacy page showed: its checkbox was usable only if hiding was possible
        # when the window opened (later changes to the panel must not decide that).
        hiding_was_possible = self._privacy_available
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
                self._check.forget_anonymization()
        if result.display_anonymized is not None and hiding_was_possible:
            # Without the add-on the checkbox is disabled and shows "off"; applying that
            # would overwrite the saved preference.
            self._apply_display_anonymized(result.display_anonymized)
        self._save_settings(notify=False)
        self._start_readiness_check()
        self._refresh_queue_from_inputs()

    def _on_packages_changed(self) -> None:
        """Something was installed: forget what was cached about it, then look again.

        The one place that clears the caches which depend on what is installed, so every
        installer — whichever window it ran in, and whether that window is still open — has
        the same effect.
        """
        self._app_service.anonymization.clear_anonymizer_cache()  # also forgets the installed models
        self._config_panel.refresh_data_flow()  # also refreshes the privacy availability
        self._refresh_privacy_availability()
        self._start_readiness_check()

    def _on_install_round_finished(self, installed_any: bool) -> None:
        """A round of installs ended: a package may have arrived, so look again."""
        if installed_any:
            self.packages_changed.emit()

    def _on_addon_installed(self, addon: str) -> None:
        if addon == PRIVACY_ADDON:
            self.packages_changed.emit()  # spaCy may be available now
            _dialogs.notify(self._dialog_parent(), dlg.privacy_addon_installed())
        elif addon == READER_ADDON_ID:
            # The built-in reader's models arrived: the readiness line turns green on the
            # check below; nothing else to tell.
            self._log.append_text("The built-in reader is downloaded.\n")
        elif addon == PAGE_MODELS_ADDON_ID:
            self._log.append_text("The page models are downloaded.\n")
        self._start_readiness_check()

    def _on_preprocess_preview_requested(
        self, ocr_config: OcrConfig, *, parent: QWidget | None = None
    ) -> None:
        """Preview *ocr_config*'s image preparation; dialogs open on top of *parent*."""
        owner = parent or self
        thread = self._preprocess_preview_thread
        if thread is not None and thread.isRunning():
            return  # a preview is already in flight; ignore the extra click

        image_suffixes = self._app_service.run.list_supported_suffixes() - {".txt"}
        # Prefer the file the user selected in the queue so the preview matches the
        # document they are looking at, not just whichever file sorts first.
        source_path: Path | None = next(
            (
                Path(sid)
                for sid in self._results_table.selected_source_ids()
                if Path(sid).suffix.lower() in image_suffixes and Path(sid).is_file()
            ),
            None,
        )
        input_dir_text = self._config_panel.get_input_dir()
        if source_path is None and input_dir_text:
            input_dir = Path(input_dir_text)
            if input_dir.is_dir():
                source_path = next(
                    (
                        f
                        for f in sorted(input_dir.iterdir())
                        if f.is_file() and f.suffix.lower() in image_suffixes
                    ),
                    None,
                )
        if source_path is None:
            supported_glob = " ".join(
                f"*{s}" for s in sorted(self._app_service.run.list_supported_suffixes() - {".txt"})
            )
            chosen = _dialogs.pick_file(owner, dlg.select_image_or_pdf(supported_glob))
            if chosen is None:
                return
            source_path = chosen

        tmp_dir = self._app_service.reader.resolve_preview_tmp_dir()

        thread = _PreprocessPreviewThread(self._app_service, source_path, ocr_config, tmp_dir)
        self._preprocess_preview_thread = thread
        # QueuedConnection: guarantees the callback (which builds widgets) runs on the
        # GUI thread even though the lambda itself isn't a QObject the auto-connection
        # logic can use to infer that.
        thread.done.connect(
            lambda orig, proc, steps, error: self._on_preprocess_preview_done(
                orig, proc, steps, error, ocr_config, tmp_dir, parent=owner
            ),
            Qt.ConnectionType.QueuedConnection,
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        thread.start()

    def _on_preprocess_preview_done(
        self,
        orig_path: Path | None,
        proc_path: Path | None,
        steps: list[str] | None,
        error: str,
        ocr_config: OcrConfig,
        tmp_dir: Path,
        *,
        parent: QWidget | None = None,
    ) -> None:
        QApplication.restoreOverrideCursor()
        # The Settings window may have been closed while the preview was being made.
        owner = parent if parent is not None and _is_shown(parent) else self
        if error:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            _dialogs.notify(owner, dlg.preprocessing_preview_failed(error))
            return
        assert orig_path is not None
        assert proc_path is not None
        assert steps is not None

        orig_pix = load_pixmap(orig_path)
        proc_pix = load_pixmap(proc_path)

        preview = QDialog(owner)
        preview.setWindowTitle(f"Preprocessing Preview — {ocr_config.preprocess_method}")
        preview.resize(1000, 620)

        vbox = QVBoxLayout(preview)
        hint = QLabel("Ctrl + scroll wheel to zoom")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #888; font-size: 8pt;")
        vbox.addWidget(hint)

        img_row = QHBoxLayout()
        step_label = ", ".join(steps) if steps else "unchanged"
        for title, pix in [("Original", orig_pix), (f"Processed ({step_label})", proc_pix)]:
            col = QVBoxLayout()
            hdr = QLabel(title)
            hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            zoom_view = ZoomableImageView()
            zoom_view.set_pixmap(pix)
            col.addWidget(hdr)
            col.addWidget(zoom_view, stretch=1)
            img_row.addLayout(col)
        vbox.addLayout(img_row, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(preview.reject)
        vbox.addWidget(buttons)
        preview.finished.connect(lambda _: shutil.rmtree(tmp_dir, ignore_errors=True))
        preview.exec()


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
