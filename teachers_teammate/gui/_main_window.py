"""Main application window and entry point."""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
import logging
import os
from pathlib import Path
import shutil
import sys
from typing import Any, ClassVar, Protocol

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
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
    QSplashScreen,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
import qdarkstyle

from ..application.service import (
    ADDON_PADDLE,
    ADDON_PRIVACY,
    ProcessingApplicationService,
)
from ..config import DEFAULTS, Config, dump_config_file, load_config_file
from ._app_bootstrap import create_app
from ._chart_widget import ChartWidget
from ._config_panel import ConfigPanel
from ._help_dialog import AboutDialog, HelpDialog, ThirdPartyLicensesDialog
from ._image_utils import load_icon, load_pixmap
from ._log_widget import LogWidget
from ._preview_panel import DiffWidget, PreviewPanel, ZoomableImageView
from ._results_table import ResultsTable
from ._settings_dialog import (
    CorrectionSettingsDialog,
    EvaluationSettingsDialog,
    OCRSettingsDialog,
    SettingsDialog,
)
from ._stats_widget import SystemStatsWidget
from ._types import FileDoneEvent
from ._update_check import UpdateCheckThread
from ._worker import OCRWorker, _ConnectionCheckThread


class _AnonymizeThread(QThread):
    """Run spaCy anonymization in a background thread for the privacy preview diff."""

    done = Signal(str, str, str)  # original, anonymized, error_message

    def __init__(
        self,
        app_service: ProcessingApplicationService,
        text: str,
        language: str,
        config: object = None,
    ) -> None:
        super().__init__()
        self._svc = app_service
        self._text = text
        self._language = language
        self._config = config

    def run(self) -> None:
        try:
            anonymized, _ = self._svc.anonymize_preview(self._text, self._language, self._config)
            self.done.emit(self._text, anonymized, "")
        except ImportError as exc:
            if getattr(sys, "frozen", False):
                msg = "spaCy is not installed.\n\nUse the Install Privacy Addon button in Settings."
            else:
                msg = f'spaCy not installed: {exc}\n\nInstall: pip install "teachers-teammate[privacy]"'
            self.done.emit("", "", msg)
        except OSError as exc:
            self.done.emit(
                "",
                "",
                f"spaCy model missing: {exc}\n\nRun: python -m spacy download <model>",
            )
        except Exception as exc:  # noqa: BLE001  # anonymization may raise anything off-thread; report via the done signal
            self.done.emit("", "", str(exc))


class _WorkerLike(Protocol):
    log_line: Any
    file_started: Any
    ocr_done: Any
    file_done: Any
    finished_with_code: Any
    stop_event: Any

    def start(self) -> None: ...

    def isRunning(self) -> bool: ...


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

    MainWindow owns this object; the DownloadsDialog subscribes when open and
    unsubscribes on close, so pulls continue in the background.
    """

    progress = Signal(str, float, float)  # (status, completed_bytes, total_bytes)
    started = Signal(str)  # model_name
    finished = Signal(str)  # model_name (success)
    error = Signal(str)  # error message

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._current_model: str = ""

    @property
    def is_active(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def current_model(self) -> str:
        return self._current_model

    def start(self, url: str, model_name: str) -> None:
        """Start pulling *model_name* from *url*. No-op if a pull is already running."""
        if self.is_active:
            return
        from ._addon_installer_dialog import _OllamaPullThread  # noqa: PLC0415

        self._current_model = model_name
        thread = _OllamaPullThread(url, model_name)
        thread.progress.connect(self.progress)
        thread.finished_ok.connect(self._on_finished_ok)
        thread.finished_err.connect(self._on_finished_err)
        thread.start()
        self._thread = thread
        self.started.emit(model_name)

    def abort(self) -> None:
        """Request the active pull to stop after the current chunk."""
        if self._thread is not None:
            self._thread.request_abort()  # ty: ignore[unresolved-attribute]  # _thread holds an abortable _OllamaPullThread; base QThread lacks request_abort

    def _on_finished_ok(self, model_name: str) -> None:
        self._thread = None
        self.finished.emit(model_name)

    def _on_finished_err(self, msg: str) -> None:
        self._thread = None
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

    def _init_runtime_state(self) -> None:
        """Initialise per-window runtime state and declare widgets built later."""
        self._worker: _WorkerLike | None = None
        self._config_path: Path | None = None
        self._pull_manager = OllamaDownloadManager(self)
        self._privacy_preview_thread: _AnonymizeThread | None = None
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
        self._llm_check_thread: _ConnectionCheckThread | None = None
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
        qs = QSettings(self._QSETTINGS_ORG, self._QSETTINGS_APP)
        qs.beginGroup("credentials")
        for key in qs.childKeys():
            if not os.environ.get(key):
                value = qs.value(key)
                if isinstance(value, str) and value:
                    os.environ[key] = value
        qs.endGroup()

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
        root.addLayout(content_row)

        self._results_table.preview_requested.connect(self._on_preview_requested)
        self._results_table.stage_run_requested.connect(self._on_stage_run_requested)
        self._results_table.privacy_preview_requested.connect(self._on_privacy_preview_requested)
        self._results_table.export_docx_requested.connect(self._on_export_docx_requested)
        self._config_panel.paths_changed.connect(self._refresh_queue_from_inputs)
        self._config_panel.anonymizer_configure_requested.connect(
            self._open_anonymizer_config_dialog
        )
        self._preview.ocr_text_edited.connect(self._on_ocr_text_edited)
        self._preview.correction_text_edited.connect(self._on_correction_text_edited)
        self._preview.run_stage_requested.connect(self._on_preview_stage_run_requested)
        # Kick off an initial LLM connectivity check once the window is laid out.
        # Pass self as the receiver so the callback is cancelled if the window is destroyed.
        QTimer.singleShot(500, self, self._check_llm_status)

        self._update_thread = UpdateCheckThread(self)
        self._update_thread.update_available.connect(self._show_update_banner)
        QTimer.singleShot(0, self._update_thread.start)

    def _build_menu_bar(self) -> None:
        bar = self.menuBar()
        assert bar is not None

        # Simple settings for everyday use
        settings_menu = bar.addMenu("Settings")
        assert settings_menu is not None
        ocr_act = QAction("Text Recognition\u2026", self)
        ocr_act.triggered.connect(self._open_ocr_settings)
        settings_menu.addAction(ocr_act)
        correction_act = QAction("Proofreading Settings\u2026", self)
        correction_act.triggered.connect(self._open_correction_settings)
        settings_menu.addAction(correction_act)
        evaluation_act = QAction("Content Review Settings\u2026", self)
        evaluation_act.triggered.connect(self._open_evaluation_settings)
        settings_menu.addAction(evaluation_act)
        doc_output_act = QAction("Output Settings\u2026", self)
        doc_output_act.triggered.connect(self._open_output_settings_dialog)
        settings_menu.addAction(doc_output_act)

        # Advanced settings for power users
        advanced_menu = bar.addMenu("Advanced Settings")
        assert advanced_menu is not None
        conn_act = QAction("Connections & Credentials\u2026", self)
        conn_act.triggered.connect(self._open_settings_dialog)
        advanced_menu.addAction(conn_act)
        downloads_act = QAction("Downloads\u2026", self)
        downloads_act.triggered.connect(self._open_downloads_dialog)
        advanced_menu.addAction(downloads_act)
        anon_act = QAction("Anonymizer Settings\u2026", self)
        anon_act.triggered.connect(self._open_anonymizer_settings_from_menu)
        advanced_menu.addAction(anon_act)

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

        self._config_panel = ConfigPanel(
            preset_prompts=self._app_service.list_correction_prompt_presets(),
            default_prompt=self._app_service.default_correction_prompt(),
        )
        self._config_panel.set_service(self._app_service)
        self._config_panel.open_downloads_requested.connect(
            lambda tab: self._open_downloads_dialog_ollama_tab(start_tab=tab)
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
        self._export_btn = QPushButton("Export DOCX…")
        self._export_btn.setIcon(
            QIcon.fromTheme(
                "document-export",
                _style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton),
            )
        )
        self._export_btn.setMinimumHeight(32)
        self._export_btn.setEnabled(False)
        self._export_btn.setToolTip(
            "Save results as Word documents (also available via right-click)"
        )
        self._export_btn.clicked.connect(self._on_export_docx_toolbar)
        btn_layout.addWidget(self._run_btn)
        btn_layout.addWidget(self._stop_btn)
        btn_layout.addWidget(self._export_btn)
        layout.addWidget(btn_row)

        restore_btn = QPushButton("Restore Defaults")
        restore_btn.setToolTip("Reset all settings back to built-in defaults.")
        restore_btn.clicked.connect(self._on_restore_defaults)
        layout.addWidget(restore_btn)

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
        self._stats.open_downloads_requested.connect(self._open_downloads_dialog)
        chart_splitter.addWidget(self._chart)
        chart_splitter.addWidget(self._stats)
        chart_splitter.setStretchFactor(0, 3)
        chart_splitter.setStretchFactor(1, 1)
        self._tabs.addTab(chart_splitter, "Stats")

        return self._tabs

    # ── Settings persistence ──────────────────────────────────────────────

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

        if config_path is None:
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
        except Exception as exc:  # noqa: BLE001  # config file may be missing, malformed, or use an unexpected type; log the warning and continue
            self._log.append_text(f"WARNING: Could not load {config_path}: {exc}\n")

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
            if view.ocr_done:
                any_ocr_cached = True
        if any_ocr_cached:
            self._export_btn.setEnabled(True)
        if file_names:
            self._progress.setRange(0, 1)
            self._progress.setValue(0)
            self._progress.setFormat(f"Found {len(file_names)} file(s) — select rows and press Run")
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            self._progress.setFormat("Idle")

    def _on_run(self) -> None:
        try:
            config = self._config_panel.to_config()
        except ValueError as exc:
            QMessageBox.warning(self, "Configuration error", str(exc))
            return

        missing_models = self._app_service.check_ollama_models_ready(config)
        if missing_models:
            model_list = "".join(f"\n  • {m}" for m in missing_models)
            box = QMessageBox(self)
            box.setWindowTitle("AI model not yet downloaded")
            box.setText(
                f"The following model(s) are not installed in Ollama:{model_list}\n\n"
                "Please download them first using Advanced Settings → Downloads."
            )
            box.setIcon(QMessageBox.Icon.Warning)
            box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            downloads_btn = box.addButton("Open Downloads →", QMessageBox.ButtonRole.ActionRole)
            box.exec()
            if box.clickedButton() is downloads_btn:
                self._open_downloads_dialog_ollama_tab()
            return

        stages_to_check: list[tuple[str, int]] = []
        if config.anonymization_enabled:
            from ._downloads_dialog import DownloadsDialog  # noqa: PLC0415

            stages_to_check.append(("anonymizer", DownloadsDialog.TAB_SPACY))
        if config.correction_enabled:
            stages_to_check.append(("correction", 0))
        if config.evaluation_enabled:
            stages_to_check.append(("evaluation", 0))
        for stage, tab in stages_to_check:
            issues = self._app_service.check_stage_requirements(config, stage)
            if issues:
                from ._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

                dlg = DependencyGuardDialog(stage, issues, self)
                dlg.open_downloads.connect(
                    lambda t=tab: self._open_downloads_dialog_ollama_tab(start_tab=t)
                )
                dlg.exec()
                return

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
        self._worker.ocr_done.connect(self._on_ocr_done)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.finished_with_code.connect(self._on_finished)

        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._worker.start()

    def _on_stage_run_requested(self, selected_source_ids: list[str], stage: str) -> None:
        """Invalidate cache from *stage* onward and run selected rows."""
        try:
            config = self._config_panel.to_config()
        except ValueError as exc:
            QMessageBox.warning(self, "Configuration error", str(exc))
            return

        # Running a stage from the right-click menu is an explicit, per-entry action:
        # force the requested stage on for this run regardless of the enable checkboxes
        # (which only govern the main "Run"). Evaluation also needs correction text.
        if stage == "correction":
            config = dataclasses.replace(config, correction_enabled=True)
        elif stage == "evaluation":
            config = dataclasses.replace(config, evaluation_enabled=True, correction_enabled=True)

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

    def _on_export_docx_toolbar(self) -> None:
        source_ids = (
            self._results_table.selected_source_ids() or self._results_table.queued_source_ids()
        )
        if source_ids:
            self._on_export_docx_requested(source_ids)

    def _on_export_docx_requested(self, source_ids: list[str]) -> None:
        # Suggested starting directory: last run output dir → config output → input dir
        start_dir = self._results_table.get_output_dir()
        if start_dir is None:
            d = self._config_panel.get_output_dir() or self._config_panel.get_input_dir()
            start_dir = Path(d) if d else Path.home()

        docx_format = str(self._config_panel.get_settings_dict().get("docx_format", "table"))

        # For a single file show Save As; for multiple files show a folder picker.
        if len(source_ids) == 1:
            source = Path(source_ids[0])
            suggested = str(start_dir / (source.stem + ".docx"))
            dest, _ = QFileDialog.getSaveFileName(
                self, "Save DOCX", suggested, "Word Document (*.docx)"
            )
            if not dest:
                return
            targets = [(source_ids[0], Path(dest))]
        else:
            folder = QFileDialog.getExistingDirectory(
                self, "Save DOCX files to folder", str(start_dir)
            )
            if not folder:
                return
            targets = [(sid, Path(folder) / (Path(sid).stem + ".docx")) for sid in source_ids]

        errors: list[str] = []
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
                    docx_format=docx_format,
                )
                self._log.append_text(f"DOCX saved: {out_path}\n")
            except Exception as exc:  # noqa: BLE001  # python-docx/PIL errors vary; collect per-file and continue the batch
                errors.append(f"{Path(source_id).name}: {exc}")

        if errors:
            QMessageBox.warning(
                self,
                "Export DOCX",
                "Could not export the following file(s):\n\n" + "\n".join(errors),
            )

    def _on_restore_defaults(self) -> None:
        reply = QMessageBox.question(
            self,
            "Restore Defaults",
            "Reset all settings to their built-in default values?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._config_panel.restore_defaults()

    def _on_stop(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.stop_event.set()
            self._stop_btn.setEnabled(False)
            self._progress.setFormat("Stopping\u2026 (finishing current phase)")

    # ── Worker signal handlers ────────────────────────────────────────────

    def _on_file_started(self, source_id: str, name: str, idx: int, total: int) -> None:
        self._progress.setValue((idx - 1) * self._steps_per_file)
        self._progress.setFormat(f"[{idx}/{total}] {name} — OCR…")
        self._results_table.mark_processing(source_id)

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
        self._export_btn.setEnabled(True)
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

    # ── Service status indicator ──────────────────────────────────────────

    _SERVICE_DISPLAY: ClassVar[dict[str, str]] = {
        "ollama": "Ollama",
        "tesseract": "Tesseract",
        "paddleocr": "PaddleOCR",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "google": "Google",
        "mistral": "Mistral",
        "cohere": "Cohere",
        "langchain": "LangChain",
    }

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
            display = self._SERVICE_DISPLAY.get(svc, svc.title())
            lbl = QLabel(f"● {display}")
            lbl.setStyleSheet("color: #888; font-size: 9pt; padding: 1px 3px;")
            self._status_labels[svc] = lbl
            self._status_row_layout.addWidget(lbl)

            if svc == "ollama":
                pass  # resolved asynchronously below
            else:
                is_engine = svc in self._app_service.list_ocr_engines()
                if is_engine:
                    connected, model_ok, _ = self._app_service.check_connection(engine=svc, url=url)
                else:
                    connected, model_ok, _ = self._app_service.check_connection(
                        provider=svc, url=url
                    )
                self._set_service_status(svc, connected, model_ok)

        self._status_row_layout.addStretch()

        if "ollama" in services:
            ollama_models = self._collect_ollama_models(
                settings, correction_enabled, evaluation_enabled
            )
            check_model = ollama_models[0] if ollama_models else ""
            self._llm_check_thread = _ConnectionCheckThread(
                engine="ollama", url=url, model=check_model
            )
            self._llm_check_thread.check_done.connect(self._on_llm_check_done)
            self._llm_check_thread.start()

    def _collect_ollama_models(
        self,
        settings: dict,
        correction_enabled: bool,
        evaluation_enabled: bool,
    ) -> list[str]:
        """Return deduplicated list of Ollama model names currently configured."""
        models: list[str] = []
        if settings.get("ocr_engine") == "ollama":
            m = settings.get("ocr_model", "")
            if m:
                models.append(m)
        if correction_enabled and settings.get("correction_provider") == "ollama":
            m = settings.get("correction_model", "")
            if m and m not in models:
                models.append(m)
        if evaluation_enabled and settings.get("evaluate_provider") == "ollama":
            m = settings.get("evaluate_model", "")
            if m and m not in models:
                models.append(m)
        return models

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
                    )
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
        try:
            self._preview.load(pixmap, raw_txt, correction_txt, evaluation_txt)
        except Exception as exc:  # noqa: BLE001  # preview load may raise on missing/corrupt files; warn instead of crashing
            QMessageBox.warning(self, "Preview", f"Could not load preview content:\n{exc}")
            return
        self._tabs.setCurrentIndex(1)
        # Determine which source is being previewed and update stage status badges.
        source_id, *_ = self._results_table.selected_entry()
        if not source_id:
            source_id = self._current_preview_source_id
        self._current_preview_source_id = source_id
        self._update_preview_stage_statuses(source_id)

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
        self._preview.load(
            load_pixmap(state.preview_img),
            state.raw_text,
            state.correction_text,
            state.evaluation_text,
        )
        self._update_preview_stage_statuses(source_id)
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
        self._preview.load(
            load_pixmap(state.preview_img),
            state.raw_text,
            state.correction_text,
            state.evaluation_text,
        )
        self._update_preview_stage_statuses(source_id)
        self._tabs.setCurrentIndex(1)

    def _on_privacy_preview_requested(self, _source_id: str, raw_txt: str) -> None:
        if not raw_txt.strip():
            QMessageBox.information(
                self, "Privacy Preview", "No OCR text available for this file yet."
            )
            return

        language = str(self._config_panel.to_dict().get("language") or "English")

        anon_config = self._config_panel.build_anonymizer_config()

        dlg = QDialog(self)
        dlg.setWindowTitle("Personal Information Preview — What Will Be Hidden")
        dlg.resize(900, 600)
        vbox = QVBoxLayout(dlg)

        status_lbl = QLabel("Running anonymization…")
        vbox.addWidget(status_lbl)

        diff = DiffWidget()
        diff.setVisible(False)
        vbox.addWidget(diff, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        vbox.addWidget(buttons)

        thread = _AnonymizeThread(self._app_service, raw_txt, language, anon_config)
        self._privacy_preview_thread = thread

        def _on_done(original: str, anonymized: str, error: str) -> None:
            if error:
                status_lbl.setText(error)
            else:
                status_lbl.setVisible(False)
                diff.setVisible(True)
                diff.set_texts(original, anonymized)

        thread.done.connect(_on_done)
        thread.start()
        dlg.exec()

    def _open_settings_dialog(self) -> None:
        """Open the Connections & Credentials dialog."""
        dialog = SettingsDialog(
            self._config_panel.get_settings_dict(),
            self,
            input_dir=self._config_panel.get_input_dir(),
            app_service=self._app_service,
        )
        dialog.addon_installed.connect(self._on_addon_installed)
        dialog.open_downloads_requested.connect(self._open_downloads_dialog_ollama_tab)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._config_panel.update_settings(dialog.get_values())
            self._save_settings(notify=False)
            self._check_llm_status()
            self._refresh_queue_from_inputs()

    def _open_ocr_settings(self) -> None:
        """Open the focused OCR Settings dialog."""
        dialog = OCRSettingsDialog(
            self._config_panel.get_settings_dict(),
            self,
            app_service=self._app_service,
        )
        dialog.preprocess_preview_requested.connect(self._on_preprocess_preview_requested)
        dialog.addon_installed.connect(self._on_addon_installed)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._config_panel.update_settings(dialog.get_values())
            self._save_settings(notify=False)
            self._check_llm_status()
            self._refresh_queue_from_inputs()

    def _open_correction_settings(self) -> None:
        """Open the focused Correction Settings dialog (provider, model, system prompt)."""
        settings = self._config_panel.get_settings_dict()
        dialog = CorrectionSettingsDialog(
            settings,
            self._config_panel.get_correction_prompt(),
            self,
            app_service=self._app_service,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._config_panel.update_settings(dialog.get_values())
            self._config_panel.set_correction_prompt(dialog.get_prompt())
            self._save_settings(notify=False)
            self._check_llm_status()

    def _open_evaluation_settings(self) -> None:
        """Open the focused Evaluation Settings dialog (provider, model, system prompt)."""
        settings = self._config_panel.get_settings_dict()
        dialog = EvaluationSettingsDialog(
            settings,
            self._config_panel.get_evaluation_prompt(),
            self,
            app_service=self._app_service,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._config_panel.update_settings(dialog.get_values())
            self._config_panel.set_evaluation_prompt(dialog.get_prompt())
            self._save_settings(notify=False)
            self._check_llm_status()

    def _build_update_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("UpdateBanner")
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

    def _open_downloads_dialog(self) -> None:
        self._open_downloads_dialog_ollama_tab(start_tab=0)

    def _open_downloads_dialog_ollama_tab(self, *, start_tab: int = 2) -> None:
        from ._downloads_dialog import DownloadsDialog  # noqa: PLC0415

        ollama_url = str(self._config_panel.get_settings_dict().get("ollama_url", ""))
        dialog = DownloadsDialog(
            ollama_url=ollama_url,
            start_tab=start_tab,
            parent=self,
            pull_manager=self._pull_manager,
        )
        dialog.packages_changed.connect(self._check_llm_status)
        dialog.exec()

    def _on_addon_installed(self, addon: str) -> None:
        if addon == ADDON_PRIVACY:
            self._app_service.clear_anonymizer_cache()
            QMessageBox.information(
                self,
                "Privacy Addon Installed",
                "The Privacy add-on has been installed.\n\n"
                "Open Advanced Settings → Anonymizer Settings to download a language model\n"
                "before using personal information removal.",
            )
        elif addon == ADDON_PADDLE:
            QMessageBox.information(
                self,
                "PaddleOCR Addon Installed",
                "PaddleOCR has been installed.\n\n"
                "Go to Settings → Text Recognition and select 'paddleocr' as the recognition method.",
            )
        self._check_llm_status()

    def _open_anonymizer_settings_from_menu(self) -> None:
        self._config_panel.request_anonymizer_config()

    def _open_output_settings_dialog(self) -> None:
        from ._settings_dialog import OutputSettingsDialog  # noqa: PLC0415

        dialog = OutputSettingsDialog(self._config_panel.get_settings_dict(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._config_panel.update_settings(dialog.get_values())
            self._save_settings(notify=False)
            self._refresh_queue_from_inputs()

    def _open_anonymizer_config_dialog(self, language: str, config: object) -> None:
        from ._anonymizer_config_dialog import AnonymizerConfigDialog  # noqa: PLC0415

        dialog = AnonymizerConfigDialog(language, config, self._app_service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._config_panel.update_anonymizer_config(dialog.anonymizer_config)
            self._save_settings(notify=False)

    def _on_preprocess_preview_requested(self, method: str) -> None:
        input_dir_text = self._config_panel.get_input_dir()
        source_path: Path | None = None
        if input_dir_text:
            input_dir = Path(input_dir_text)
            if input_dir.is_dir():
                image_suffixes = self._app_service.list_supported_suffixes() - {".txt"}
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
                self, "Select image or PDF", "", f"Images & PDFs ({supported_glob})"
            )
            if not path:
                return
            source_path = Path(path)

        tmp_dir = self._app_service.resolve_preview_tmp_dir()
        try:
            orig_path, proc_path, steps = self._app_service.preprocess_preview(
                source_path,
                method,
                tmp_dir,
            )
        except Exception as exc:  # noqa: BLE001  # preprocess_preview raises for unsupported files, missing deps, or I/O errors; show a dialog instead of crashing
            shutil.rmtree(tmp_dir, ignore_errors=True)
            QMessageBox.warning(self, "Preview failed", str(exc))
            return

        orig_pix = load_pixmap(orig_path)
        proc_pix = load_pixmap(proc_path)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Preprocessing Preview — {method}")
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
                _splash = QSplashScreen(_pix, Qt.WindowType.WindowStaysOnTopHint)
                _splash.show()
                assert isinstance(app, QApplication)
                app.processEvents()

    assert isinstance(app, QApplication)
    app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api="pyside6"))

    factory = window_factory or build_main_window
    window = factory()
    window.show()

    if _splash is not None:
        _splash.finish(window)

    sys.exit(app.exec())
