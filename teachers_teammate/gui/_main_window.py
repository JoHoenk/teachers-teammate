"""Main application window and entry point."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import dataclasses
import logging
import os
from pathlib import Path
import shutil
import sys
from typing import Any, ClassVar, NamedTuple, Protocol

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
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

from .. import __version__
from ..application.sent_text import SentTexts
from ..application.service import (
    ADDON_AMD,
    ADDON_NVIDIA,
    ADDON_PADDLE,
    ADDON_PRIVACY,
    DocxExport,
    ProcessingApplicationService,
    RunPreflight,
)
from ..config import (
    DEFAULTS,
    Config,
    OcrConfig,
    dump_config_file,
    load_config_file,
    unavailable_ocr_engine,
)
from ..domain.data_location import LOCAL_OCR_ENGINES, OCR_ENGINE_NAMES, DataFlow
from ..domain.model_catalog import HardwareDetection
from ..domain.stages import PROGRESS_STAGE_OCR, PROGRESS_STAGE_PREPROCESSING
from . import _user_text as txt
from ._app_bootstrap import create_app
from ._background import LatestTask, run_in_background, wait_for_background_tasks
from ._chart_widget import ChartWidget
from ._config_panel import ConfigPanel
from ._credential_store import CredentialStore
from ._data_flow_summary import advice_text, level_text, stage_tooltip
from ._export_menu import ExportKind, build_export_menu
from ._help_dialog import AboutDialog, HelpDialog, ThirdPartyLicensesDialog
from ._image_utils import load_icon, load_pixmap
from ._log_widget import LogWidget
from ._model_wording import profile_text
from ._preview_panel import PreviewPanel, ZoomableImageView
from ._privacy_settings import (
    DISPLAY_ANONYMIZED_DEFAULT,
    FlowAcknowledgements,
    display_anonymized,
    set_display_anonymized,
)
from ._results_table import ResultsTable
from ._stats_widget import SystemStatsWidget
from ._types import FileDoneEvent, PreprocessInfo
from ._update_check import UpdateCheckThread
from ._what_is_sent_dialog import WhatIsSentDialog
from ._worker import OCRWorker
from .settings import (
    PAGE_TITLES,
    SettingsContext,
    SettingsPage,
    SettingsResult,
    SettingsWindow,
    last_page,
)
from .settings._page import (
    FOCUS_DETECTION,
    FOCUS_GPU,
    FOCUS_LOCAL_MODELS,
    FOCUS_NAME_DETECTION,
    FOCUS_ONLINE_SERVICES,
)


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
        if getattr(sys, "frozen", False):
            return (
                f"spaCy is not installed.\n\nInstall it under {txt.WHERE_ADDONS} (Name detection)."
            )
        return f'spaCy not installed: {exc}\n\nInstall: pip install "teachers-teammate[privacy]"'
    if isinstance(exc, OSError):
        return f"spaCy model missing: {exc}\n\nRun: python -m spacy download <model>"
    return None


class _PreprocessPreviewThread(QThread):
    """Run preprocess_preview() in a background thread for the preprocessing preview dialog.

    Preprocessing a full-resolution scan (denoise, deskew, ...) can take several
    seconds; running it on the GUI thread would freeze the window until it returns.
    """

    done = Signal(object, object, object, str)  # original_path, processed_path, steps, error

    def __init__(
        self,
        app_service: ProcessingApplicationService,
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
            orig, proc, steps = self._svc.preprocess_preview(
                self._source_path, self._ocr_config, self._tmp_dir
            )
            self.done.emit(orig, proc, steps, "")
        except Exception as exc:  # noqa: BLE001  # preprocess_preview raises for unsupported files, missing deps, or I/O errors; report via the done signal
            self.done.emit(None, None, None, str(exc))


class _WorkerLike(Protocol):
    log_line: Any
    file_started: Any
    stage_started: Any
    ocr_done: Any
    file_done: Any
    finished_with_code: Any
    stop_event: Any

    def start(self) -> None: ...

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

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
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
        thread = _OllamaPullThread(url, model_name)
        thread.progress.connect(self.progress)
        thread.finished_ok.connect(self._on_finished_ok)
        thread.finished_err.connect(self._on_finished_err)
        thread.start()
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


class MainWindow(QMainWindow):
    """Teacher's Teammate main application window."""

    def __init__(
        self,
        *,
        app_service: ProcessingApplicationService | None = None,
        worker_factory: Callable[..., _WorkerLike] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Teacher's Teammate")
        self.resize(1500, 860)
        self._app_service = app_service or ProcessingApplicationService()
        self._worker_factory = worker_factory
        _icon = _app_icon()
        if _icon is not None:
            self.setWindowIcon(_icon)
        self._init_runtime_state()

        self._pull_manager.progress.connect(self._on_pull_progress)
        self._pull_manager.started.connect(self._on_pull_started)
        self._pull_manager.finished.connect(self._on_pull_finished)
        self._pull_manager.error.connect(self._on_pull_error)

        self.setAcceptDrops(True)
        self._build_ui()
        self._setup_tray()

        self._load_credentials_from_settings()
        self._load_toml_if_present()
        self._install_gpu_monitoring_in_background()

    def _init_runtime_state(self) -> None:
        """Initialise per-window runtime state and declare widgets built later."""
        self._worker: _WorkerLike | None = None
        self._config_path: Path | None = None
        self._pull_manager = OllamaDownloadManager(self)
        self._sent_texts_task = LatestTask(self)
        self._last_sent_texts: SentTexts | None = None
        self._anonymize_task = LatestTask(self)
        self._anonymize_running = False
        #: The "Hide personal information in texts" view setting (not part of Config).
        self._display_anonymized = display_anonymized(self._settings_store)
        #: Whether the texts can be hidden, per document language (spaCy model installed).
        self._privacy_availability: dict[str, bool] = {}
        #: Whether the texts can be hidden for the current document language.
        self._privacy_available = True
        #: The text and language of the running hiding job, to not start it twice.
        self._anonymize_key: tuple[str, str] | None = None
        self._first_run_task = LatestTask(self)
        self._preprocess_preview_thread: _PreprocessPreviewThread | None = None
        #: The Settings window while it is open (dialogs started from it use it as parent).
        self._settings_window: SettingsWindow | None = None
        self._export_actions: list[QAction] = []
        self._settings_actions: dict[SettingsPage, QAction] = {}
        self._all_settings_action: QAction
        self._result_names: list[str] = []
        self._result_ocr_times: list[float] = []
        self._result_correction_times: list[float] = []
        self._queued_files_by_source_id: dict[str, Path] = {}
        self._steps_per_file: int = 1
        self._current_preview_source_id: str = ""

        # Declared here; assigned in _build_left_panel / _build_right_panel.
        self._config_panel: ConfigPanel
        self._status_container: QWidget
        self._status_row_layout: QHBoxLayout
        self._status_labels: dict[str, QLabel] = {}
        # One latest-wins background check per status-row service.
        self._status_tasks: dict[str, LatestTask] = {}
        # Ollama status that is red is re-checked every few seconds: it may be started
        # after the app.
        self._ollama_connected = True
        # True while the requirement check of a Run / stage re-run is in flight.
        self._preflight_running = False
        self._text_export_running = False
        self._ollama_retry_timer = _debounce_timer(
            self, self._OLLAMA_RETRY_MS, self._recheck_ollama_status
        )
        # The pieces counter follows the shown text and the settings, debounced: it builds
        # the corrector, which is cheap but not free.
        self._text_size_timer = _debounce_timer(
            self, self._TEXT_SIZE_DELAY_MS, self._refresh_text_size_estimate
        )
        # The graphics probe must never run on the GUI thread, so the counter waits for it.
        self._text_size_task = LatestTask(self)
        # The same for the expected cache keys of the queue: they depend on the card.
        self._cache_state_task = LatestTask(self)
        self._update_thread: UpdateCheckThread | None = None
        self._update_banner: QFrame
        self._stats: SystemStatsWidget
        self._run_btn: QPushButton
        self._stop_btn: QPushButton
        self._export_btn: QPushButton
        self._progress: QProgressBar
        self._pull_progress_bar: QProgressBar | None = None
        self._tabs: QTabWidget
        self._log: LogWidget
        self._results_table: ResultsTable
        self._chart: ChartWidget
        self._preview: PreviewPanel
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
            str(self._app_service.get_provider_info(provider).get("env_key", ""))
            for provider in self._app_service.list_providers()
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

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(6)
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.addWidget(self._build_left_panel())
        self._main_splitter.addWidget(self._build_right_panel())
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        content_row.addWidget(self._main_splitter)
        root.addLayout(content_row, 1)

        self._results_table.preview_requested.connect(self._on_preview_requested)
        self._results_table.stage_run_requested.connect(self._on_stage_run_requested)
        self._results_table.what_is_sent_requested.connect(self._on_what_is_sent_requested)
        self._config_panel.data_flow_changed.connect(self._on_data_flow_changed)
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
        self._preview.ocr_text_edited.connect(self._on_ocr_text_edited)
        self._preview.correction_text_edited.connect(self._on_correction_text_edited)
        self._preview.run_stage_requested.connect(self._on_preview_stage_run_requested)
        self._preview.display_anonymized_changed.connect(self._apply_display_anonymized)
        self._preview.anonymization_needed.connect(self._on_anonymization_needed)
        self._refresh_privacy_availability()
        self._preview.set_display_anonymized(self._display_anonymized)
        # Kick off an initial LLM connectivity check once the window is laid out.
        # Pass self as the receiver so the callback is cancelled if the window is destroyed.
        QTimer.singleShot(500, self, self._check_llm_status)

        # No parent: the thread outlives the window if it is closed during the request.
        self._update_thread = UpdateCheckThread()
        self._update_thread.update_available.connect(self._show_update_banner)
        QTimer.singleShot(0, self._update_thread.start)

    def _build_menu_bar(self) -> None:
        bar = self.menuBar()
        assert bar is not None
        self._build_file_menu(bar)
        self._build_settings_menu(bar)

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
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

    def _build_settings_menu(self, bar: Any) -> None:
        """Settings: one entry per page of the Settings window, and "All settings…"."""
        settings_menu = bar.addMenu("Settings")
        assert settings_menu is not None
        tooltips = {
            SettingsPage.AI_MODELS: "Which AI reads the handwriting, proofreads and reviews.",
            SettingsPage.AI_SETUP: (
                "The local AI server and what this computer can run, and the access keys "
                "for online AI services."
            ),
            SettingsPage.SCAN_QUALITY: "How scans are cleaned up before the text is read.",
            SettingsPage.PRIVACY: (
                "What may leave this computer, and how names and other personal information "
                "are found and replaced."
            ),
            SettingsPage.ADDONS: "Download AI models and install optional parts.",
        }
        for page in SettingsPage:
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

        self._config_panel = ConfigPanel(
            preset_prompts=self._app_service.list_correction_prompt_presets(),
            default_prompt=self._app_service.default_correction_prompt(),
        )
        self._config_panel.set_service(self._app_service)
        self._config_panel.open_downloads_requested.connect(
            lambda focus, preselect: self.open_settings(
                SettingsPage.ADDONS, focus=str(focus), preselect=list(preselect)
            )
        )
        layout.addWidget(self._config_panel)

        # Service connectivity status row
        self._status_container = QWidget()
        self._status_row_layout = QHBoxLayout(self._status_container)
        self._status_row_layout.setContentsMargins(2, 2, 2, 2)
        self._status_row_layout.setSpacing(8)
        self._status_container.setToolTip(
            "Shows whether the services needed for your current settings are available."
        )
        layout.addWidget(self._status_container)

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
        self._stop_btn = QPushButton("Stop")
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

        left.setMinimumWidth(340)
        left.setMaximumWidth(420)
        return left

    def _build_right_panel(self) -> QWidget:
        self._tabs = QTabWidget()
        _tab_bar_font = self._tabs.tabBar().font()
        _tab_bar_font.setPointSize(14)
        self._tabs.tabBar().setFont(_tab_bar_font)

        results_splitter = QSplitter(Qt.Orientation.Vertical)
        self._results_table = ResultsTable()
        self._log = LogWidget()
        results_splitter.addWidget(self._results_table)
        results_splitter.addWidget(self._log)
        results_splitter.setStretchFactor(0, 3)
        results_splitter.setStretchFactor(1, 1)
        self._tabs.addTab(results_splitter, "Document Queue")

        self._preview = PreviewPanel()
        self._tabs.addTab(self._preview, "Preview")

        chart_splitter = QSplitter(Qt.Orientation.Vertical)
        self._chart = ChartWidget()
        self._stats = SystemStatsWidget()
        self._stats.open_downloads_requested.connect(
            lambda: self.open_settings(SettingsPage.ADDONS, focus=FOCUS_GPU)
        )
        chart_splitter.addWidget(self._chart)
        chart_splitter.addWidget(self._stats)
        chart_splitter.setStretchFactor(0, 3)
        chart_splitter.setStretchFactor(1, 1)
        self._tabs.addTab(chart_splitter, "Stats")

        return self._tabs

    # ── Settings persistence ──────────────────────────────────────────────

    #: How often a red Ollama status is re-checked (the server may start after the app).
    _OLLAMA_RETRY_MS = 10_000
    #: How long the pieces counter waits after an edit or a settings change.
    _TEXT_SIZE_DELAY_MS = 400
    _QSETTINGS_ORG = "TeachersTeammate"
    _QSETTINGS_APP = "TeachersTeammate"

    def _load_toml_if_present(self) -> None:
        config_path = self._app_service.resolve_config_path(None)
        # Restore window geometry and splitter sizes from QSettings.
        qs = QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP)
        raw_geometry = qs.value("gui/geometry")
        if raw_geometry is not None:
            self.restoreGeometry(raw_geometry)
        sizes = [360, 1140]
        raw_sizes = qs.value("gui/panel_sizes")
        if isinstance(raw_sizes, list) and len(raw_sizes) == 2:
            try:
                restored = [int(v) for v in raw_sizes]
                if restored[0] >= 200 and restored[1] >= 400:
                    sizes = restored
            except (ValueError, TypeError):
                pass
        QTimer.singleShot(0, lambda s=sizes: self._main_splitter.setSizes(s))

        if config_path is None or not config_path.exists():
            # First start (no ocr.toml yet): pick the best models for this
            # computer once detection finishes, then persist the choice.
            self._apply_first_start_model_settings()
            return
        self._config_path = config_path
        try:
            values = load_config_file(config_path)
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
            self._tell_if_ocr_engine_was_replaced(config_path)
        except Exception as exc:  # noqa: BLE001  # config file may be missing, malformed, or use an unexpected type; log the warning and continue
            self._log.append_text(f"WARNING: Could not load {config_path}: {exc}\n")

    def _tell_if_ocr_engine_was_replaced(self, config_path: Path) -> None:
        """Say so when the saved settings named an online text-recognition engine.

        Such a setting is replaced by the default (see ``load_config_file``).  Doing that
        silently would leave the next run failing for a reason the teacher never chose.
        """
        retired = unavailable_ocr_engine(config_path)
        if not retired:
            return
        message = (
            f"Your saved settings used “{retired}” for text recognition. Reading pages "
            "online is no longer offered, because a page cannot be anonymised before it "
            "is read, so the app now uses "
            f"{DEFAULTS['ocr_engine']} on your computer instead "
            f"({', '.join(LOCAL_OCR_ENGINES)} are available).\n\n"
            "Proofreading and content review are not affected."
        )
        self._log.append_text(f"NOTICE: {message}\n")
        QMessageBox.information(self, "Text recognition changed", message)

    #: QSettings: "yes"/"no" once the user answered the GPU-monitoring download question.
    _GPU_MONITORING_CHOICE_KEY = "gpu_monitoring/download"
    #: QSettings: app version in which the automatic download last failed.
    _GPU_MONITORING_FAILED_KEY = "gpu_monitoring/failed_in_version"

    def _install_gpu_monitoring_in_background(self) -> None:
        """Offer the live GPU monitoring package for this computer's GPU, if missing.

        Looks for the need in the background; does nothing in source checkouts,
        without a supported GPU, or when the package is already installed.
        """
        run_in_background(
            self._app_service.pending_gpu_monitoring_addon,
            self._on_gpu_monitoring_needed,
            owner=self,
        )

    def _on_gpu_monitoring_needed(self, addon: object, error: BaseException | None) -> None:
        """Ask once before downloading the monitoring package, then install it."""
        if error is not None or not isinstance(addon, str):
            return
        qs = QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP)
        choice = str(qs.value(self._GPU_MONITORING_CHOICE_KEY, "") or "")
        if choice == "no":
            return
        if str(qs.value(self._GPU_MONITORING_FAILED_KEY, "") or "") == __version__:
            return  # it failed before; Add-ons installs it on request
        if choice != "yes":
            answer = QMessageBox.question(
                self,
                "Show GPU usage?",
                "Teacher's Teammate can show how busy your graphics card is in the "
                "System Stats panel.\n\n"
                "This downloads one small package from the Python Package Index "
                "(PyPI); no documents or personal data are sent.\n\n"
                f"Download it now? You can also do this later under {txt.WHERE_ADDONS}.",
            )
            choice = "yes" if answer == QMessageBox.StandardButton.Yes else "no"
            qs.setValue(self._GPU_MONITORING_CHOICE_KEY, choice)
            if choice == "no":
                return
        self._log.append_text("Downloading the GPU monitoring package for System Stats…\n")
        service = self._app_service
        run_in_background(
            lambda: service.install_gpu_monitoring(addon),
            lambda _result, err: self._on_gpu_monitoring_installed(addon, err),
            owner=self,
        )

    def _on_gpu_monitoring_installed(self, addon: object, error: BaseException | None) -> None:
        if error is not None:
            QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP).setValue(
                self._GPU_MONITORING_FAILED_KEY, __version__
            )
            self._log.append_text(
                f"NOTICE: GPU monitoring could not be installed: {error}\n"
                f"It can be installed later under {txt.WHERE_ADDONS}.\n"
            )
            return
        if addon is None:
            return
        if self._stats.refresh_gpu_monitoring():
            self._log.append_text("Installed GPU monitoring for the System Stats panel.\n")
        else:
            self._log.append_text(
                "NOTICE: GPU monitoring was installed, but the driver gives no readings "
                "for this graphics card.\n"
            )

    def _apply_first_start_model_settings(self) -> None:
        """Start first-start hardware detection in the background (AC12/AC13)."""
        service = self._app_service
        ollama_url = self._current_ollama_url()
        # Detection must never get in the way of starting the app: any failure
        # simply leaves the static defaults in place.
        self._first_run_task.submit(
            lambda: service.detect_hardware(ollama_url), self._on_first_start_detection
        )

    def _on_first_start_detection(self, detection: object, error: BaseException | None) -> None:
        """Replace untouched static defaults with the BEST models for the profile."""
        if error is not None or not isinstance(detection, HardwareDetection):
            return
        settings = self._config_panel.get_settings_dict()
        best = self._app_service.first_run_model_settings(detection.profile)
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
            f"You can download them under {txt.WHERE_ADDONS}.\n"
        )
        self._save_settings(notify=False)
        self._check_llm_status()

    def _save_settings(self, *, notify: bool = True, silent_on_error: bool = False) -> None:
        # Persist window geometry and splitter sizes to QSettings (UI state, not pipeline config)
        qs = QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP)
        qs.setValue("gui/geometry", self.saveGeometry())
        qs.setValue("gui/panel_sizes", self._main_splitter.sizes())

        values = self._config_panel.to_dict()
        out = (
            self._config_path
            if self._config_path is not None
            else self._app_service.default_config_path()
        )
        try:
            out.write_text(dump_config_file(values), encoding="utf-8")
            if notify:
                self._log.append_text(f"Settings saved to {out.resolve()}\n")
        except OSError as exc:
            if not silent_on_error:
                QMessageBox.warning(self, "Save failed", str(exc))

    def closeEvent(self, event: Any) -> None:
        try:
            self._save_settings(notify=False, silent_on_error=True)
        except Exception:  # noqa: BLE001  # never block window close on a settings-save error
            pass
        self._first_run_task.cancel()
        self._ollama_retry_timer.stop()
        for task in self._status_tasks.values():
            task.cancel()
        # Let the window disappear before waiting: running background tasks are
        # given a moment to end, and the user should not see a frozen window.
        self.hide()
        self._sent_texts_task.cancel()
        self._anonymize_task.cancel()
        wait_for_background_tasks()
        event.accept()

    # ── Run / Stop ────────────────────────────────────────────────────────

    def _get_file_names(self, config: Config) -> list[str]:
        """Return queued file names via application-layer file discovery."""
        return [file.name for file in self._app_service.collect_input_files(config)]

    def _scan_input_files(self) -> list[Path]:
        """Scan input folder via application service while config is incomplete."""
        selected = self._config_panel.get_selected_files()
        if selected is not None:
            return selected
        input_text = self._config_panel.get_input_dir()
        if not input_text:
            return []
        input_dir = Path(input_text)
        if not input_dir.is_dir():
            return []
        return self._app_service.collect_input_files_from_dir(input_dir, recursive=True)

    def _refresh_queue_from_inputs(self) -> None:
        """Refresh queue from input selection without showing modal validation errors."""
        files = self._scan_input_files()
        self._queued_files_by_source_id = {str(file.resolve()): file for file in files}
        file_names = [file.name for file in files]
        source_ids = [str(file.resolve()) for file in files]
        self._results_table.set_queue(file_names, source_ids)

        views: dict = {}
        try:
            config = self._config_panel.to_config()
            views = self._app_service.load_views_reconciled(config, files)
            self._ensure_hardware_known_for_cache_state(config)
        except ValueError:
            # to_config() fails when output dir isn't set yet (DOCX requires it).
            # Build a minimal lookup-only Config so cached state is still shown.
            values = self._config_panel.to_dict()
            input_text = str(values.get("input", "")).strip()
            output_text = str(values.get("output", "")).strip()
            if input_text and Path(input_text).is_dir():
                try:
                    lookup_config = self._app_service.build_lookup_config(
                        Path(input_text),
                        Path(output_text) if output_text else Path(input_text),
                    )
                    views = self._app_service.load_views(lookup_config, files)
                except Exception:  # noqa: BLE001  # cached-view lookup is best-effort; ignore any load error
                    pass

        any_ocr_cached = False
        for file in files:
            source_id = str(file.resolve())
            view = views.get(source_id)
            if view is None:
                continue
            self._results_table.mark_cached(
                source_id,
                view.preview_img,
                view.raw_text,
                view.correction_text,
                view.evaluation_text,
                ocr_done=view.ocr_done,
                correction_done=view.correction_done,
                evaluation_done=view.evaluation_done,
            )
            self._results_table.set_correction_review(
                source_id, view.correction_review, view.correction_warning
            )
            self._results_table.set_preprocessing(
                source_id, PreprocessInfo(view.original_img, view.preprocess_steps)
            )
            self._results_table.set_text_format(source_id, view.raw_text_format)
            if view.ocr_done:
                any_ocr_cached = True
        if any_ocr_cached:
            self._set_export_enabled(True)
        if file_names:
            self._progress.setRange(0, 1)
            self._progress.setValue(0)
            self._progress.setFormat(f"Found {len(file_names)} file(s) — select rows and press Run")
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            self._progress.setFormat("Idle")

    def _run_in_progress(self) -> bool:
        """Return whether a requirement check or an OCR worker is already active."""
        return self._preflight_running or bool(self._worker and self._worker.isRunning())

    def _warn_busy(self) -> None:
        QMessageBox.information(
            self,
            "Please wait",
            "A run or requirement check is still in progress. Try again when it has finished.",
        )

    def _on_run(self) -> None:
        if self._run_in_progress():
            return
        try:
            config = self._config_panel.to_config()
        except ValueError as exc:
            QMessageBox.warning(self, "Configuration error", str(exc))
            return
        self._check_requirements_then(config, lambda: self._continue_run(config))

    def _check_requirements_then(self, config: Config, on_ready: Callable[[], None]) -> None:
        """Check the requirements of *config* off the GUI thread, then call *on_ready*.

        The checks contact Ollama, so they must not block the GUI.  Until the result is
        in, Run is disabled and :meth:`_run_in_progress` reports busy, so no second
        worker can be started from the Run button or the right-click menu.
        """
        refusal = self._app_service.output_refusal_for_run(config)
        if refusal:
            QMessageBox.warning(
                self,
                "Choose another output folder",
                f"{refusal}\n\nChange it under {txt.WHERE_RESULTS}.",
            )
            return
        self._preflight_running = True
        self._run_btn.setEnabled(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("Checking requirements…")
        service = self._app_service
        run_in_background(
            lambda: service.check_run_preflight(config),
            lambda result, error: self._on_preflight_done(
                lambda: self._confirm_data_flow_then(config, on_ready), result, error
            ),
            owner=self,
        )

    def _on_preflight_done(
        self, on_ready: Callable[[], None], result: object, error: BaseException | None
    ) -> None:
        """Show the first unmet requirement, or continue with *on_ready*."""
        self._preflight_running = False
        self._run_btn.setEnabled(True)
        self._progress.setFormat("Idle")
        if error is not None or not isinstance(result, RunPreflight):
            # The check itself failed: do not block; the pipeline reports real problems.
            on_ready()
            return
        if result.blocked:
            self._show_blocking_preflight(result)
            return
        if result.memory_warning:
            self._confirm_memory_warning(result.memory_warning, on_ready)
            return
        on_ready()

    def _confirm_memory_warning(self, warning: str, on_ready: Callable[[], None]) -> None:
        """Ask before a run whose models do not fit the memory that is free now.

        A warning rather than a refusal, because free memory changes from run to run — so
        there is no "don't ask again" here, and Cancel is the default button: Enter must not
        start a run that could make the computer swap.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Not enough free memory")
        box.setText(warning)
        run_anyway = box.addButton("Run anyway", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is run_anyway:
            on_ready()

    def _show_blocking_preflight(self, result: RunPreflight) -> None:
        """Show the requirement that stops the run, with the way to fix it."""
        if result.ocr_not_allowed:
            QMessageBox.warning(
                self,
                "Text recognition must stay on your computer",
                f"{result.ocr_not_allowed}\n\nChange it under {txt.WHERE_AI_MODELS}.",
            )
            return
        if result.ollama_unreachable:
            self._ollama_connected = False
            QMessageBox.warning(
                self,
                "Ollama is not running",
                f"{result.ollama_unreachable}\n\nStart it with `ollama serve`, then press Run "
                "again.",
            )
            self._start_ollama_check()
            return
        if result.missing_models:
            self._show_missing_models(result.missing_models)
            return
        from ._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415
        from ._model_guidance import PROVIDER_PREFIX  # noqa: PLC0415

        focus = FOCUS_NAME_DETECTION if result.stage == "anonymizer" else FOCUS_LOCAL_MODELS
        # A missing provider package is installed on the online-services tab.
        packages = [f"{PROVIDER_PREFIX}{i.provider}" for i in result.stage_issues if i.provider]
        if packages:
            focus = FOCUS_ONLINE_SERVICES
        dlg = DependencyGuardDialog(result.stage, list(result.stage_issues), self)
        dlg.open_downloads.connect(
            lambda f=focus, ps=packages: self.open_settings(
                SettingsPage.ADDONS, focus=f, preselect=ps
            )
        )
        dlg.exec()

    def _settings_store(self) -> QSettings:
        """Return a QSettings for the window's GUI preferences (not pipeline settings)."""
        return QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP)

    def _acknowledgements(self) -> FlowAcknowledgements:
        """Return the store of "don't ask again" answers (read and written through QSettings)."""
        return FlowAcknowledgements(lambda: QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP))

    def _confirm_data_flow_then(self, config: Config, on_ready: Callable[[], None]) -> None:
        """Ask once before a run that sends content away from this computer."""
        flow = self._app_service.describe_data_flow(config)
        if not flow.leaves_computer or flow.fingerprint in self._acknowledgements().fingerprints():
            on_ready()
            return
        lines = "\n".join(f"  • {stage.title} — {stage.badge}" for stage in flow.outbound)
        advice = "".join(f"\n\n{advice_text(tip)}" for tip in flow.advice)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Your documents will leave this computer")
        box.setText(
            f"{level_text(flow.level)}\n\nThese steps send content away from this "
            f"computer:\n\n{lines}{advice}\n\n"
            "Make sure your school's data protection rules allow this."
        )
        remember = QCheckBox(
            f"Don't ask again for these settings (change under {txt.WHERE_PRIVACY})"
        )
        box.setCheckBox(remember)
        go = box.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is not go:
            return
        if remember.isChecked():
            self._acknowledgements().remember(flow)
        on_ready()

    def _show_missing_models(self, missing_models: tuple[str, ...]) -> None:
        """Tell the user which configured Ollama models are not downloaded yet."""
        model_list = "".join(f"\n  • {m}" for m in missing_models)
        box = QMessageBox(self)
        box.setWindowTitle("AI model not yet downloaded")
        box.setText(
            f"The following model(s) are not installed in Ollama:{model_list}\n\n"
            f"Please download them first under {txt.WHERE_ADDONS}."
        )
        box.setIcon(QMessageBox.Icon.Warning)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        downloads_btn = box.addButton(txt.OPEN_ADDONS, QMessageBox.ButtonRole.ActionRole)
        box.exec()
        if box.clickedButton() is downloads_btn:
            self.open_settings(
                SettingsPage.ADDONS, focus=FOCUS_LOCAL_MODELS, preselect=list(missing_models)
            )

    def _continue_run(self, config: Config) -> None:
        """Queue the files and start the worker once all requirements are met."""
        queued_source_ids = self._results_table.queued_source_ids()
        queue_was_empty = not queued_source_ids
        if queue_was_empty:
            queued_files = self._app_service.collect_input_files(config)
            self._queued_files_by_source_id = {str(file.resolve()): file for file in queued_files}
            queued_source_ids = [str(file.resolve()) for file in queued_files]
            self._results_table.set_queue(
                [file.name for file in queued_files],
                queued_source_ids,
            )

        if not queued_source_ids:
            QMessageBox.information(
                self,
                "No files found",
                (
                    "No supported files found (PDF, images, or text files) in:\n"
                    f"{config.input_dir}\n\n"
                    "TIP: Plain text files (.txt) are read directly — no text extraction needed."
                ),
            )
            return

        selected_source_ids = self._results_table.selected_source_ids()
        if not selected_source_ids and queue_was_empty:
            selected_source_ids = queued_source_ids
        if not selected_source_ids:
            QMessageBox.information(
                self,
                "No files selected",
                "Select one or more rows in the queue, then press Run.",
            )
            return

        self._steps_per_file = 1 if not config.correction_enabled else 2
        self._start_worker(config, selected_source_ids)

    def _start_worker(self, config, selected_source_ids: list[str]) -> None:
        """Start an OCR worker for *selected_source_ids* using *config*."""
        self._log.clear_log()
        self._save_settings(notify=False)
        self._results_table.set_output_dir(config.output_dir)
        self._chart.clear_data()
        self._preview.clear_preview()
        self._result_names = []
        self._result_ocr_times = []
        self._result_correction_times = []
        self._progress.setRange(0, len(selected_source_ids) * self._steps_per_file)
        self._progress.setValue(0)
        self._progress.setFormat(f"Selected {len(selected_source_ids)} file(s) — Starting…")
        self._tabs.setCurrentIndex(0)

        worker_factory = self._worker_factory or OCRWorker
        self._worker = worker_factory(
            config,
            selected_source_paths=selected_source_ids,
            app_service=self._app_service,
        )
        self._worker.log_line.connect(self._log.append_text)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.stage_started.connect(self._on_stage_started)
        self._worker.ocr_done.connect(self._on_ocr_done)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.finished_with_code.connect(self._on_finished)

        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._worker.start()

    def _on_stage_run_requested(self, selected_source_ids: list[str], stage: str) -> None:
        """Check the requirements, then invalidate cache from *stage* onward and run rows."""
        if self._run_in_progress():
            self._warn_busy()
            return
        try:
            config = self._config_panel.to_config()
        except ValueError as exc:
            QMessageBox.warning(self, "Configuration error", str(exc))
            return

        # Running a stage from the right-click menu is an explicit, per-entry action:
        # run exactly that stage regardless of the enable checkboxes (which only govern
        # the main "Run"), and nothing after it.  The cache of the later stages was just
        # cleared, so leaving them enabled would silently run the whole chain again.
        # Evaluation also needs correction text (cached, or computed first).
        if stage == "ocr":
            config = dataclasses.replace(
                config,
                correction_enabled=False,
                evaluation_enabled=False,
                anonymization_enabled=False,
            )
        elif stage == "correction":
            config = dataclasses.replace(config, correction_enabled=True, evaluation_enabled=False)
        elif stage == "evaluation":
            config = dataclasses.replace(config, evaluation_enabled=True, correction_enabled=True)

        # Check before touching the cache: a re-run that cannot work (Ollama stopped, model
        # missing) must not throw away the results that are there.
        self._check_requirements_then(
            config, lambda: self._run_stage(config, selected_source_ids, stage)
        )

    def _run_stage(self, config: Config, selected_source_ids: list[str], stage: str) -> None:
        """Invalidate the cache from *stage* onward and start the worker on the rows."""
        run_source_ids, failures, no_files = self._app_service.invalidate_from_stage(
            config,
            selected_source_ids=selected_source_ids,
            stage=stage,
        )
        if no_files:
            QMessageBox.information(self, "No files found", "No files are available to re-run.")
            return

        if not run_source_ids:
            QMessageBox.information(
                self,
                "Selection mismatch",
                "Could not map selected queue rows to current input files.",
            )
            return

        if failures:
            QMessageBox.warning(
                self,
                "Cache invalidation failed",
                "Could not reset cache for one or more files:\n\n" + "\n".join(failures),
            )
            return

        self._steps_per_file = 1 if not config.correction_enabled else 2
        self._start_worker(config, run_source_ids)

    def _on_export_toolbar(self, kind: ExportKind) -> None:
        source_ids = (
            self._results_table.selected_source_ids() or self._results_table.queued_source_ids()
        )
        if source_ids:
            self._on_export_requested(source_ids, kind)

    def _on_export_requested(self, source_ids: list[str], kind: ExportKind) -> None:
        if kind.is_text and self._text_export_running:
            QMessageBox.information(self, "Please wait", "A text export is still running.")
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
            dest, _ = QFileDialog.getSaveFileName(
                self, f"Save {kind.label.rstrip('…')}", suggested, kind.file_filter
            )
            return [(source_ids[0], Path(dest))] if dest else []
        folder = QFileDialog.getExistingDirectory(
            self, f"Save {kind.label.rstrip('…')}s to folder", str(start_dir)
        )
        if not folder:
            return []
        # Same-named documents from different subfolders keep their subfolders.
        input_dir = self._config_panel.get_input_dir()
        return [
            (
                sid,
                self._app_service.export_target(
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
        answer = QMessageBox.question(
            self,
            "Replace existing files?",
            f"{len(existing)} file(s) already exist and will be replaced:\n\n{shown}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_text_export_folder(self, targets: list[tuple[str, Path]]) -> bool:
        """Warn when text files would land in the input folder, where the next run reads them.

        A one-off export only asks; a run with text files configured there is refused
        (:meth:`ProcessingApplicationService.output_refusal_for_run`) since it would repeat.
        """
        input_dir = self._config_panel.get_input_dir()
        # With an explicit file selection a run reads only those files.
        if not input_dir or self._config_panel.get_selected_files():
            return True
        # Folder mode always scans subfolders (see ConfigPanel.to_config).
        if not any(
            self._app_service.is_in_input_folder(out.parent, Path(input_dir), recursive=True)
            for _sid, out in targets
        ):
            return True
        answer = QMessageBox.question(
            self,
            "Save into the input folder?",
            "Text files in the input folder are read as new documents the next time you run.\n\n"
            "Save here anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _export_docx(self, targets: list[tuple[str, Path]], errors: list[str]) -> None:
        settings = self._config_panel.get_settings_dict()
        docx_format = str(settings.get("docx_format", "table"))
        # "Same as the text" (empty) means the language of the text, as in the pipeline.
        explanation_language = str(
            settings.get("explanation_language") or self._config_panel.to_dict().get("language", "")
        )
        for source_id, out_path in targets:
            _sid, _name, preview_img, raw_txt, corr_txt, _eval = (
                self._results_table.entry_for_source(source_id)
            )
            if not raw_txt:
                errors.append(f"{Path(source_id).name}: No OCR text — run the pipeline first.")
                continue
            try:
                self._app_service.export_docx(
                    Path(source_id),
                    raw_txt,
                    corr_txt,
                    preview_img,
                    out_path,
                    options=DocxExport(
                        docx_format=docx_format,
                        text_format=self._results_table.text_format_for_source(source_id),
                        review=self._results_table.review_for_source(source_id),
                        labels_language=explanation_language,
                    ),
                )
                self._log.append_text(f"DOCX saved: {out_path}\n")
            except Exception as exc:  # noqa: BLE001  # python-docx/PIL errors vary; collect per-file and continue the batch
                errors.append(f"{Path(source_id).name}: {exc}")

        if errors:
            QMessageBox.warning(
                self,
                "Export Word Document",
                "Could not export the following file(s):\n\n" + "\n".join(errors),
            )

    def _export_text(
        self, targets: list[tuple[str, Path]], errors: list[str], *, anonymize: bool
    ) -> None:
        """Write the extracted text of each target; anonymizing is slow, so it runs off-thread."""
        service = self._app_service
        language = str(self._config_panel.to_dict().get("language") or "English")
        anon_config = self._config_panel.build_anonymizer_config() if anonymize else None
        # Every input of the run is protected, not just the row's own file: exporting
        # ``essay.png`` as ``essay.txt`` must not replace an ``essay.txt`` that is an input too.
        protected = [Path(sid) for sid in self._results_table.queued_source_ids()]
        jobs: list[tuple[Path, str, Path, str]] = []
        for source_id, out_path in targets:
            raw_txt = self._results_table.entry_for_source(source_id)[3]
            if not raw_txt.strip():
                errors.append(f"{Path(source_id).name}: No OCR text — run the pipeline first.")
            else:
                text_format = self._results_table.text_format_for_source(source_id)
                jobs.append((Path(source_id), raw_txt, out_path, text_format))
        if not jobs:
            self._show_text_export_errors(errors)
            return
        preflight_errors = list(errors)

        def work() -> _TextExportOutcome:
            if anonymize:
                # Fails once, up front, when spaCy or its model is missing.
                service.warm_up_anonymizer(language, anon_config)
            written: list[Path] = []
            failed = list(preflight_errors)
            for source, raw_txt, out_path, text_format in jobs:
                try:
                    service.export_text(
                        source,
                        raw_txt,
                        out_path,
                        text_format=text_format,
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
                if preflight_errors:
                    message += "\n\nAlso skipped:\n" + "\n".join(preflight_errors)
                QMessageBox.warning(self, "Export Text", message)
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
            QMessageBox.warning(
                self,
                "Export Text",
                "Could not export the following file(s):\n\n" + "\n".join(errors),
            )

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
        self._preview.invalidate_anonymization()
        self._save_settings(notify=False)
        self._check_llm_status()
        self._refresh_queue_from_inputs()
        self._text_size_timer.start()  # the context setting changes how the text is split

    def _on_results_changed(self) -> None:
        """Step "Results" changed: save it, and show what is cached for the new folder."""
        self._save_settings(notify=False)
        self._refresh_queue_from_inputs()

    def _on_instructions_changed(self) -> None:
        """The proofreading instructions changed: save them, and re-count the parts."""
        self._save_settings(notify=False)
        self._text_size_timer.start()  # longer instructions leave less room for the text

    def _on_stop(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.stop_event.set()
            self._stop_btn.setEnabled(False)
            self._progress.setFormat("Stopping\u2026 (finishing current phase)")

    # ── Worker signal handlers ────────────────────────────────────────────

    def _on_file_started(self, source_id: str, name: str, idx: int, total: int) -> None:
        self._progress.setValue((idx - 1) * self._steps_per_file)
        self._progress.setFormat(f"[{idx}/{total}] {name} — starting…")
        self._results_table.mark_processing(source_id)
        # The previous run's preprocessing info is stale until this run records its own.
        # The text format is deliberately kept: the row still shows the previous texts, and
        # a format that no longer matches them would save an edit in the wrong notation.
        self._results_table.set_preprocessing(source_id, PreprocessInfo())

    _STAGE_PROGRESS_LABEL: ClassVar[dict[str, str]] = {
        PROGRESS_STAGE_PREPROCESSING: "preprocessing…",
        PROGRESS_STAGE_OCR: "OCR…",
    }

    def _on_stage_started(
        self, _source_id: str, name: str, stage: str, idx: int, total: int
    ) -> None:
        label = self._STAGE_PROGRESS_LABEL.get(stage)
        if label is not None:
            self._progress.setFormat(f"[{idx}/{total}] {name} — {label}")

    def _on_file_done(self, event: FileDoneEvent) -> None:
        source_id = event.source_id
        name = event.name
        ok = event.ok
        message = event.message
        ocr_s = event.ocr_s
        correction_s = event.correction_s
        preview_img = event.preview_img
        raw_txt = event.raw_txt
        corr_txt = event.corr_txt
        eval_txt = event.eval_txt
        self._results_table.add_result(event)
        # Refresh from the persisted state even on failure/stop, when on_ocr_done never fired.
        self._refresh_preprocessing_info(source_id, text_format=event.text_format)
        self._set_export_enabled(True)
        if not ok and message:
            QMessageBox.warning(self, "Processing error", f"{name}:\n\n{message}")
        self._result_names.append(name)
        self._result_ocr_times.append(ocr_s)
        self._result_correction_times.append(correction_s)
        self._progress.setValue(len(self._result_names) * self._steps_per_file)
        self._chart.update_data(
            self._result_names,
            self._result_ocr_times,
            self._result_correction_times,
        )

        selected_source_id, selected_name, preview_path, raw_path, corr_path, eval_path = (
            self._results_table.selected_entry()
        )
        if selected_source_id == source_id and selected_name == name:
            pix = load_pixmap(preview_path)
            self._on_preview_requested(pix, raw_path, corr_path, eval_path)
        elif not selected_source_id:
            pix = load_pixmap(preview_img)
            self._current_preview_source_id = source_id
            self._on_preview_requested(pix, raw_txt, corr_txt, eval_txt)

    def _refresh_preprocessing_info(self, source_id: str, *, text_format: str = "plain") -> None:
        """Re-read how *source_id*'s preview was produced; unknown when no valid state exists.

        *text_format* is the notation the pipeline reported for the texts; it is kept when the
        persisted state cannot be read back (e.g. the output directory changed meanwhile).
        """
        info = PreprocessInfo()
        try:
            source = Path(source_id)
            if source.exists():
                view = self._app_service.load_view(self._config_panel.to_config(), source)
                if view is not None:
                    info = PreprocessInfo(view.original_img, view.preprocess_steps)
                    text_format = view.raw_text_format
        except (OSError, ValueError):
            pass
        self._results_table.set_preprocessing(source_id, info)
        self._results_table.set_text_format(source_id, text_format)

    # ── Service status indicator ──────────────────────────────────────────

    def _service_display_name(self, svc: str) -> str:
        """Return the name shown for *svc*: an OCR engine's, else the provider's declared one."""
        if svc in OCR_ENGINE_NAMES:
            return OCR_ENGINE_NAMES[svc]
        info = self._app_service.get_provider_info(svc)
        return str(info.get("display_name") or svc.title())

    def _check_llm_status(self) -> None:
        """Rebuild the per-service status row and run availability checks."""
        settings = self._config_panel.get_settings_dict()
        engine = settings.get("ocr_engine", "ollama")
        correction_enabled = bool(settings.get("correction_enabled", True))
        evaluation_enabled = bool(settings.get("evaluation_enabled", False))
        correction_provider = (
            settings.get("correction_provider", "ollama") if correction_enabled else None
        )
        eval_provider = settings.get("evaluate_provider", "ollama") if evaluation_enabled else None
        url = settings.get("ollama_url", DEFAULTS["ollama_url"])

        # Ordered, deduplicated list of required service keys
        services: list[str] = []
        for svc in [engine, correction_provider, eval_provider]:
            if svc and svc not in services:
                services.append(svc)

        # Rebuild the status row
        while self._status_row_layout.count():
            item = self._status_row_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        self._status_labels = {}

        for svc in services:
            display = self._service_display_name(svc)
            lbl = QLabel(f"● {display}")
            lbl.setStyleSheet("color: #888; font-size: 9pt; padding: 1px 3px;")
            self._status_labels[svc] = lbl
            self._status_row_layout.addWidget(lbl)

        self._status_row_layout.addStretch()

        # Every check can block (HTTP, importing paddle, probing CUDA): run them all in
        # the background; each result colours its label when it arrives.
        for svc in services:
            if svc != "ollama":
                self._start_service_check(svc, url)
        if "ollama" in services:
            self._start_ollama_check()
        else:
            self._ollama_retry_timer.stop()

    def _start_service_check(self, svc: str, url: str, model: str = "") -> None:
        """Check service *svc* in the background and colour its status label."""
        service = self._app_service
        kind = "engine" if svc in service.list_ocr_engines() else "provider"
        task = self._status_tasks.setdefault(svc, LatestTask(self))
        task.submit(
            lambda: service.check_connection(url=url, model=model, **{kind: svc}),
            lambda result, error: self._on_service_check_done(svc, result, error),
        )

    def _on_service_check_done(self, svc: str, result: object, error: BaseException | None) -> None:
        connected, model_ok, message = False, False, ""
        if error is None and isinstance(result, tuple) and len(result) == 3:
            connected, model_ok, message = bool(result[0]), bool(result[1]), str(result[2])
        if svc == "ollama":
            self._on_llm_check_done(connected, model_ok, message)
        else:
            self._set_service_status(svc, connected, model_ok)

    def _start_ollama_check(self) -> None:
        """Check the Ollama server (and the configured model) in the background."""
        settings = self._config_panel.get_settings_dict()
        url = settings.get("ollama_url", DEFAULTS["ollama_url"])
        models = self._app_service.configured_ollama_models(self._config_panel.lookup_config())
        self._start_service_check("ollama", url, models[0] if models else "")

    def _recheck_ollama_status(self) -> None:
        """Re-check Ollama while its status is red (retry timer / window activation)."""
        if "ollama" in self._status_labels and not self._ollama_connected:
            self._start_ollama_check()

    def changeEvent(self, event: QEvent) -> None:
        """Re-check a red Ollama status when the window becomes active again."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._recheck_ollama_status()

    def _set_service_status(self, svc: str, connected: bool, model_ok: bool = True) -> None:
        lbl = self._status_labels.get(svc)
        if lbl is None:
            return
        if connected and model_ok:
            color = "#27ae60"
        elif connected:
            color = "#f39c12"  # yellow: reachable but model not installed
        else:
            color = "#c0392b"
        lbl.setStyleSheet(f"color: {color}; font-size: 9pt; padding: 1px 3px;")

    def _on_llm_check_done(self, connected: bool, model_ok: bool, _msg: str) -> None:
        self._set_service_status("ollama", connected, model_ok)
        self._ollama_connected = connected
        if connected:
            self._ollama_retry_timer.stop()
        else:
            self._ollama_retry_timer.start()

    # ── Ollama pull progress (status bar area) ────────────────────────────────────

    def _on_pull_started(self, model_name: str) -> None:
        while self._status_row_layout.count():
            item = self._status_row_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                # Qt accepts setParent(None) to detach the widget.
                widget.setParent(None)
        _lbl = QLabel(f"Downloading: {model_name}")
        _lbl.setStyleSheet("color: #3498db; font-size: 9pt; padding: 1px 3px;")
        self._pull_progress_bar = QProgressBar()
        self._pull_progress_bar.setRange(0, 100)
        self._pull_progress_bar.setTextVisible(False)
        self._pull_progress_bar.setFixedHeight(10)
        self._pull_progress_bar.setMaximumWidth(160)
        self._status_row_layout.addWidget(_lbl)
        self._status_row_layout.addWidget(self._pull_progress_bar)
        self._status_row_layout.addStretch()

    def _on_pull_progress(self, _status: str, completed: int, total: int) -> None:
        if self._pull_progress_bar is not None and total > 0:
            self._pull_progress_bar.setValue(int(100 * completed / total))
            mb_done = completed / 1_048_576
            mb_total = total / 1_048_576
            self._pull_progress_bar.setToolTip(f"{mb_done:.1f} / {mb_total:.1f} MB")

    def _on_pull_finished(self, _model_name: str) -> None:
        # Also covers pulls that finish after the Settings window was closed:
        # without this the cached model list stays stale for minutes.
        self._app_service.on_ollama_models_changed(
            self._pull_manager.current_url or self._current_ollama_url()
        )
        self._restore_status_labels()

    def _on_pull_error(self, msg: str) -> None:
        self._restore_status_labels()
        QMessageBox.warning(self, "Model download failed", msg)

    def _restore_status_labels(self) -> None:
        """Re-run the LLM status check to restore normal status labels."""
        QTimer.singleShot(0, self._check_llm_status)

    def _on_finished(self, rc: int) -> None:
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if rc == 0:
            self._progress.setValue(self._progress.maximum())
        self._progress.setFormat("Done \u2713" if rc == 0 else "Finished with errors")
        if self._result_names:
            self._tabs.setCurrentIndex(0)
        n = len(self._result_names)
        msg = f"Done — {n} file(s) processed" if rc == 0 else "Finished with errors"
        self._tray.showMessage(
            "Teacher's Teammate",
            msg,
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def _on_ocr_done(self, source_id: str, name: str, idx: int, total: int) -> None:
        if self._steps_per_file > 1:
            self._progress.setValue((idx - 1) * 2 + 1)
            self._progress.setFormat(f"[{idx}/{total}] {name} — correcting…")
            self._results_table.mark_correcting(source_id)

        # Attach preview/raw artefacts as soon as OCR finishes so double-click preview
        # works during correction/evaluation phases.
        try:
            config = self._config_panel.to_config()
            source = Path(source_id)
            if source.exists():
                view = self._app_service.load_view(config, source)
                if view is not None:
                    self._results_table.set_row_artifacts(
                        source_id,
                        view.preview_img,
                        view.raw_text,
                        view.correction_text,
                        view.evaluation_text,
                        preprocess=PreprocessInfo(view.original_img, view.preprocess_steps),
                    )
                    self._results_table.set_text_format(source_id, view.raw_text_format)
        except (OSError, ValueError):
            # Non-fatal: preview metadata will be fully attached on file_done.
            pass

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

    def _on_preview_requested(
        self,
        pixmap: QPixmap,
        raw_txt: str,
        correction_txt: str,
        evaluation_txt: str,
    ) -> None:
        # Determine which source is being previewed (drives preprocessing info and badges).
        source_id, *_ = self._results_table.selected_entry()
        if not source_id:
            source_id = self._current_preview_source_id
        info = self._results_table.preprocessing_for_source(source_id)
        try:
            self._preview.load(
                pixmap,
                raw_txt,
                correction_txt,
                evaluation_txt,
                preprocess=info,
                text_format=self._results_table.text_format_for_source(source_id),
                correction_review=self._results_table.review_for_source(source_id),
                correction_warning=self._results_table.correction_warning_for_source(source_id),
            )
        except Exception as exc:  # noqa: BLE001  # preview load may raise on missing/corrupt files; warn instead of crashing
            QMessageBox.warning(self, "Preview", f"Could not load preview content:\n{exc}")
            return
        self._tabs.setCurrentIndex(1)
        self._current_preview_source_id = source_id
        self._update_preview_stage_statuses(source_id)
        self._refresh_text_size_estimate(source_id)

    def _on_preview_stage_run_requested(self, source_id: str, stage: str) -> None:
        """Forward a per-stage run request from the preview panel to the normal handler."""
        self._on_stage_run_requested([source_id], stage)

    def _update_preview_stage_statuses(self, source_id: str) -> None:
        """Push stage status badges + nav-bar run buttons to the preview panel."""
        settings = self._config_panel.get_settings_dict()
        correction_enabled = bool(settings.get("correction_enabled", True))
        evaluation_enabled = bool(settings.get("evaluation_enabled", False))
        ocr, correction, evaluation = self._results_table.get_stage_statuses_for_source(source_id)
        corr_inv = False
        eval_inv = False
        try:
            config = self._config_panel.to_config()
            doc_state = self._app_service.get_document_state(config, Path(source_id))
            if doc_state is not None:
                corr_inv = doc_state.correction_invalidated
                eval_inv = doc_state.evaluation_invalidated
        except ValueError:
            pass
        self._preview.update_stage_statuses(
            source_id,
            ocr,
            correction,
            evaluation,
            correction_enabled,
            evaluation_enabled,
            correction_invalidated=corr_inv,
            evaluation_invalidated=eval_inv,
        )

    def _refresh_text_size_estimate(self, source_id: str | None = None) -> None:
        """Show how many proofreading requests the text of *source_id* takes.

        The estimate comes from the application service, which builds the same corrector the
        run builds, so the counter and the run cannot disagree.  Nothing is shown when there
        is no text yet or the settings are incomplete: the counter is a hint, never a gate.

        The hardware detection is asked for, never resolved here: with "My computer" on a local
        server the first resolution runs the graphics probe, which would freeze the window.
        While it is unknown the counter stays hidden and the estimate runs again when the
        detection arrives.
        """
        sid = source_id or self._current_preview_source_id
        _sid, _name, _img, raw_txt, _corr, _eval = self._results_table.entry_for_source(sid)
        if not raw_txt:
            self._preview.set_text_size(None)
            return
        try:
            config = self._config_panel.to_config()
        except Exception:  # noqa: BLE001  # an incomplete setup must not break the preview
            self._preview.set_text_size(None)
            return
        hardware = self._app_service.cached_hardware_profile(
            config.hardware_profile, config.ollama_url
        )
        if hardware is None:
            self._preview.set_text_size(None)
            self._detect_hardware_for_text_size(config.ollama_url, sid)
            return
        self._show_text_size_estimate(config, sid, raw_txt, hardware)

    def _ensure_hardware_known_for_cache_state(self, config: Config) -> None:
        """Probe this computer in the background when the cache keys need to know the card.

        With "My computer" on a local server, the context window a run uses — and so the cache
        key of its results — depends on the card.  Until this computer has been probed the keys
        are worked out for the base window; once it has, the queue is shown again with the
        real ones.  The probe never runs on the GUI thread.
        """
        if (
            self._app_service.cached_hardware_profile(config.hardware_profile, config.ollama_url)
            is not None
        ):
            return

        def detect() -> object:
            return self._app_service.detect_hardware(config.ollama_url)

        def done(_result: object, error: BaseException | None) -> None:
            # A failed probe keeps the base window; asking again would only fail again.
            if error is None:
                self._refresh_queue_from_inputs()

        self._cache_state_task.submit(detect, done)

    def _detect_hardware_for_text_size(self, ollama_url: str, source_id: str) -> None:
        """Probe this computer in the background, then estimate the size again."""

        def detect() -> object:
            return self._app_service.detect_hardware(ollama_url)

        def done(_result: object, error: BaseException | None) -> None:
            # A failed probe leaves the counter hidden; the estimate is a hint, not a gate.
            # Asking again would submit the same failing probe in a loop.
            if error is None:
                self._refresh_text_size_estimate(source_id)

        self._text_size_task.submit(detect, done)

    def _show_text_size_estimate(
        self, config: Config, source_id: str, raw_text: str, hardware: HardwareDetection
    ) -> None:
        """Show the estimate for *raw_text*, sized for the *hardware* already detected."""
        try:
            estimate = self._app_service.estimate_text_size(
                config,
                raw_text,
                self._results_table.text_format_for_source(source_id),
                hardware,
            )
        except Exception:  # noqa: BLE001  # an incomplete setup must not break the preview
            self._preview.set_text_size(None)
            return
        self._preview.set_text_size(estimate)

    def _selected_source_for_edit(self) -> tuple[Path, str, str, str, str] | None:
        """Resolve selected source row to its input path and current payloads."""
        source_id, _name, preview_img, raw_txt, corr_txt, eval_txt = (
            self._results_table.selected_entry()
        )
        if not source_id:
            return None
        source = Path(source_id)
        if not source.exists():
            return None
        return source, preview_img, raw_txt, corr_txt, eval_txt

    def _on_ocr_text_edited(self, edited_text: str) -> None:
        selected = self._selected_source_for_edit()
        if selected is None:
            return
        source, preview_img, _raw_txt, _corr_txt, _eval_txt = selected
        source_id = str(source.resolve())

        try:
            config = self._config_panel.to_config()
        except ValueError:
            return
        state = self._app_service.record_manual_ocr_edit(
            config,
            source=source,
            edited_text=edited_text,
            preview_img=preview_img,
            raw_text_format=self._results_table.text_format_for_source(source_id),
        )
        if state is None:
            return

        self._results_table.set_row_artifacts(
            source_id,
            state.preview_img,
            state.raw_text,
            state.correction_text,
            state.evaluation_text,
        )
        self._results_table.mark_cached(
            source_id,
            state.preview_img,
            state.raw_text,
            state.correction_text,
            state.evaluation_text,
            ocr_done=state.ocr_done,
            correction_done=state.correction_done,
            evaluation_done=state.evaluation_done,
        )
        self._results_table.set_correction_review(source_id, state.review, state.correction_warning)
        info = self._results_table.preprocessing_for_source(source_id)
        self._results_table.set_text_format(source_id, state.raw_text_format)
        self._preview.load(
            load_pixmap(state.preview_img),
            state.raw_text,
            state.correction_text,
            state.evaluation_text,
            preprocess=info,
            text_format=state.raw_text_format,
            correction_review=state.review,
            correction_warning=state.correction_warning,
        )
        self._update_preview_stage_statuses(source_id)
        self._text_size_timer.start()  # the edited text may need other parts
        self._tabs.setCurrentIndex(1)

    def _on_correction_text_edited(self, edited_text: str) -> None:
        selected = self._selected_source_for_edit()
        if selected is None:
            return
        source, preview_img, raw_txt, _corr_txt, _eval_txt = selected
        if not raw_txt:
            return
        source_id = str(source.resolve())

        try:
            config = self._config_panel.to_config()
        except ValueError:
            return
        state = self._app_service.record_manual_correction_edit(
            config,
            source=source,
            raw_text=raw_txt,
            preview_img=preview_img,
            edited_text=edited_text,
            raw_text_format=self._results_table.text_format_for_source(source_id),
        )
        if state is None:
            return

        self._results_table.set_row_artifacts(
            source_id,
            state.preview_img,
            state.raw_text,
            state.correction_text,
            state.evaluation_text,
        )
        self._results_table.mark_cached(
            source_id,
            state.preview_img,
            state.raw_text,
            state.correction_text,
            state.evaluation_text,
            ocr_done=state.ocr_done,
            correction_done=state.correction_done,
            evaluation_done=state.evaluation_done,
        )
        self._results_table.set_correction_review(source_id, state.review, state.correction_warning)
        info = self._results_table.preprocessing_for_source(source_id)
        self._results_table.set_text_format(source_id, state.raw_text_format)
        self._preview.load(
            load_pixmap(state.preview_img),
            state.raw_text,
            state.correction_text,
            state.evaluation_text,
            preprocess=info,
            text_format=state.raw_text_format,
            correction_review=state.review,
            correction_warning=state.correction_warning,
        )
        self._update_preview_stage_statuses(source_id)
        self._tabs.setCurrentIndex(1)

    def _on_what_is_sent_requested(self, source_id: str) -> None:
        """Open "What is sent for this file…" for one row of the results table."""
        _sid, name, _img, raw_txt, corr_txt, _eval = self._results_table.entry_for_source(source_id)
        if not raw_txt.strip():
            QMessageBox.information(self, txt.WHAT_IS_SENT_TITLE, txt.NO_TEXT_YET)
            return

        config = self._config_panel.lookup_config()
        text_format = self._results_table.text_format_for_source(source_id) or "plain"

        busy = QDialog(self)
        busy.setWindowTitle(f"{txt.WHAT_IS_SENT_TITLE} — {name}")
        busy_vbox = QVBoxLayout(busy)
        busy_vbox.addWidget(QLabel(txt.WHAT_IS_SENT_WORKING))
        service = self._app_service

        def _describe() -> object:
            return service.describe_sent_texts(config, raw_txt, corr_txt, text_format=text_format)  # ty: ignore[invalid-argument-type]

        def _on_done(result: object, error: BaseException | None) -> None:
            busy.accept()
            if error is not None:
                QMessageBox.warning(
                    self, txt.WHAT_IS_SENT_TITLE, _spacy_problem(error) or str(error)
                )
                return
            if not isinstance(result, SentTexts):
                QMessageBox.warning(self, txt.WHAT_IS_SENT_TITLE, txt.NO_TEXT_YET)
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
        self._preview.set_display_anonymized(enabled)

    def _refresh_privacy_availability(self) -> None:
        """Enable the setting only while personal information can actually be hidden.

        Needs the Privacy add-on and the spaCy model of the document language; without
        them the eye button stays disabled and its tooltip points at Settings → Add-ons.
        The answer is cached per language, because it is asked again whenever the data flow
        changes (that is, on every settings toggle).
        """
        language = self._config_panel.lookup_language()
        if language not in self._privacy_availability:
            self._privacy_availability[language] = self._app_service.anonymizer_available(language)
        available = self._privacy_availability[language]
        changed = available != self._privacy_available
        self._privacy_available = available
        self._preview.set_display_anonymized_available(available)
        if changed and available and self._display_anonymized:
            # The setting was on but could not be applied; it can now.
            self._preview.set_display_anonymized(True)

    def _on_anonymization_needed(self, raw_text: str) -> None:
        """Start hiding the personal information of the text the preview shows."""
        if not raw_text.strip():
            self._preview.set_anonymization("", {}, raw_text=raw_text)
            return
        language = self._config_panel.lookup_language()
        if self._anonymize_running and self._anonymize_key == (raw_text, language):
            return  # already being worked out
        self._anonymize_key = (raw_text, language)
        self._anonymize_running = True
        service = self._app_service
        anon_config = self._config_panel.build_anonymizer_config()

        def _anonymize() -> object:
            return service.anonymize_preview(raw_text, language, anon_config)

        def _on_done(result: object, error: BaseException | None) -> None:
            self._on_anonymization_done(raw_text, result, error)

        # Only the latest request counts: a slow result for a text the preview already left
        # is dropped, and no thread object can be destroyed while it still runs.
        self._anonymize_task.submit(_anonymize, _on_done)

    def _on_anonymization_done(
        self, raw_text: str, payload: object, error: BaseException | None
    ) -> None:
        """Hand the placeholders to the preview (only the newest request gets here)."""
        self._anonymize_key = None
        self._anonymize_running = False
        if error is not None or not isinstance(payload, tuple) or len(payload) != 2:
            reason = (_spacy_problem(error) or str(error)) if error is not None else "unknown error"
            self._preview.set_anonymization("", {}, raw_text=raw_text, error=reason)
            return
        anonymized, raw_mapping = payload
        mapping = (
            {str(key): str(value) for key, value in raw_mapping.items()}
            if isinstance(raw_mapping, dict)
            else {}
        )
        self._preview.set_anonymization(str(anonymized), mapping, raw_text=raw_text)

    def _on_data_flow_changed(self, flow: object) -> None:
        """Say under the proofreading and content-review cells where their text goes."""
        if not isinstance(flow, DataFlow):
            return
        self._results_table.set_stage_tooltips(
            {stage.stage: stage_tooltip(stage) for stage in flow.stages if not stage.detail}
        )
        self._refresh_privacy_availability()  # the document language may have changed
        self._text_size_timer.start()  # language, provider, model or an enabled stage changed

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
        window.packages_changed.connect(self._check_llm_status)
        window.packages_changed.connect(self._on_packages_changed)
        window.packages_changed.connect(self._stats.refresh_gpu_monitoring)
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
        if result.anonymizer_config is not None:
            before = self._config_panel.build_anonymizer_config()
            self._config_panel.update_anonymizer_config(result.anonymizer_config)
            if self._config_panel.build_anonymizer_config() != before:
                # New models or patterns produce different placeholders for the shown text.
                self._preview.invalidate_anonymization()
        if result.display_anonymized is not None and hiding_was_possible:
            # Without the add-on the checkbox is disabled and shows "off"; applying that
            # would overwrite the saved preference.
            self._apply_display_anonymized(result.display_anonymized)
        self._save_settings(notify=False)
        self._check_llm_status()
        self._refresh_queue_from_inputs()
        self._text_size_timer.start()  # the context setting changes how the text is split

    def _on_packages_changed(self) -> None:
        """A download finished: a spaCy model may be available now, so look again."""
        self._app_service.forget_installed_models()
        self._privacy_availability.clear()
        self._config_panel.refresh_data_flow()  # also refreshes the privacy availability
        self._refresh_privacy_availability()

    def _on_addon_installed(self, addon: str) -> None:
        if addon == ADDON_PRIVACY:
            self._app_service.clear_anonymizer_cache()
            self._privacy_availability.clear()  # spaCy may be available now
            self._config_panel.refresh_data_flow()
            self._refresh_privacy_availability()
            QMessageBox.information(
                self._dialog_parent(),
                "Privacy Addon Installed",
                "The Privacy add-on has been installed.\n\n"
                f"Download a name detection model under {txt.WHERE_ADDONS}\n"
                "before using personal information removal.",
            )
        elif addon == ADDON_PADDLE:
            QMessageBox.information(
                self._dialog_parent(),
                "PaddleOCR Addon Installed",
                "PaddleOCR has been installed.\n\n"
                f"Select 'paddleocr' as the recognition method under {txt.WHERE_AI_MODELS}.",
            )
        elif addon in (ADDON_NVIDIA, ADDON_AMD):
            self._stats.refresh_gpu_monitoring()
        self._check_llm_status()

    def _on_preprocess_preview_requested(
        self, ocr_config: OcrConfig, *, parent: QWidget | None = None
    ) -> None:
        """Preview *ocr_config*'s image preparation; dialogs open on top of *parent*."""
        owner = parent or self
        thread = self._preprocess_preview_thread
        if thread is not None and thread.isRunning():
            return  # a preview is already in flight; ignore the extra click

        image_suffixes = self._app_service.list_supported_suffixes() - {".txt"}
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
                f"*{s}" for s in sorted(self._app_service.list_supported_suffixes() - {".txt"})
            )
            path, _ = QFileDialog.getOpenFileName(
                owner, "Select image or PDF", "", f"Images & PDFs ({supported_glob})"
            )
            if not path:
                return
            source_path = Path(path)

        tmp_dir = self._app_service.resolve_preview_tmp_dir()

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
            QMessageBox.warning(owner, "Preview failed", error)
            return
        assert orig_path is not None
        assert proc_path is not None
        assert steps is not None

        orig_pix = load_pixmap(orig_path)
        proc_pix = load_pixmap(proc_path)

        dlg = QDialog(owner)
        dlg.setWindowTitle(f"Preprocessing Preview — {ocr_config.preprocess_method}")
        dlg.resize(1000, 620)

        vbox = QVBoxLayout(dlg)
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
        buttons.rejected.connect(dlg.reject)
        vbox.addWidget(buttons)
        dlg.finished.connect(lambda _: shutil.rmtree(tmp_dir, ignore_errors=True))
        dlg.exec()


# ── Entry point ────────────────────────────────────────────────────────────


def build_main_window() -> MainWindow:
    """Create the default main window instance for GUI bootstrap."""
    return MainWindow()


def main_gui(
    *,
    window_factory: Callable[[], MainWindow] | None = None,
    early_splash: QSplashScreen | None = None,
) -> None:
    """Launch the Teacher's Teammate GUI.

    Args:
        window_factory: Override the default :func:`build_main_window` factory (used in tests).
        early_splash:   A :class:`QSplashScreen` that was shown before the heavy GUI imports
                        so it is already visible during startup.  When provided the internal
                        splash-creation logic is skipped and the caller's splash is reused.
    """
    # Start the slow own-host-name lookup before any widget asks where a step runs.
    ProcessingApplicationService.warm_up_locality_checks()
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

    factory = window_factory or build_main_window
    try:
        window = factory()
        window.show()
    except BaseException:
        # Never leave the splash up behind an error dialog or a failed start.
        if _splash is not None:
            _splash.close()
        raise

    if _splash is not None:
        _splash.finish(window)

    sys.exit(app.exec())
