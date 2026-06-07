"""Benchmark main window: run OCR configs, store history, compare two runs."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...application.benchmark_service import BenchmarkApplicationService
from ...config import DEFAULTS
from .._app_bootstrap import create_app
from .._log_widget import LogWidget
from .._ocr_config_selector import OcrConfigSelector
from ._benchmark_worker import BenchmarkWorker
from ._compare_view import CompareView
from ._runs_list import RunsList


class BenchmarkWindow(QMainWindow):
    """Thin controller wiring the shared widgets to the benchmark service."""

    def __init__(
        self,
        *,
        app_service: BenchmarkApplicationService | None = None,
        worker_factory=None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Teacher's Teammate — OCR Benchmark")
        self.resize(1100, 720)
        self._service = app_service or BenchmarkApplicationService()
        self._worker_factory = worker_factory or BenchmarkWorker
        self._worker: BenchmarkWorker | None = None
        self._source: Path | None = None
        self._language = DEFAULTS["language"]

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.addWidget(self._build_left_panel(), stretch=0)
        root.addWidget(self._build_right_panel(), stretch=1)

    # ── construction ────────────────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMaximumWidth(420)
        layout = QVBoxLayout(panel)

        src_box = QGroupBox("Document")
        src_layout = QVBoxLayout(src_box)
        self._source_label = QLabel("No document selected.")
        self._source_label.setWordWrap(True)
        choose_btn = QPushButton("Choose document…")
        choose_btn.clicked.connect(self._on_choose_document)
        src_layout.addWidget(self._source_label)
        src_layout.addWidget(choose_btn)
        layout.addWidget(src_box)

        cfg_box = QGroupBox("OCR configuration")
        cfg_layout = QVBoxLayout(cfg_box)
        self._selector = OcrConfigSelector(app_service=self._service, show_preview_button=False)
        self._selector.refresh()  # apply default engine's preprocess/model on open
        cfg_layout.addWidget(self._selector)
        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Run && store")
        self._run_btn.clicked.connect(self._on_run)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        run_row.addWidget(self._run_btn)
        run_row.addWidget(self._stop_btn)
        cfg_layout.addLayout(run_row)
        layout.addWidget(cfg_box)

        self._runs_list = RunsList()
        self._runs_list.selection_changed.connect(self._on_selection_changed)
        self._runs_list.delete_requested.connect(self._on_delete_run)
        self._runs_list.clear_all_requested.connect(self._on_clear_all)
        layout.addWidget(self._runs_list, stretch=1)
        return panel

    def _build_right_panel(self) -> QWidget:
        tabs = QTabWidget()
        self._compare = CompareView()
        self._log = LogWidget()
        tabs.addTab(self._compare, "Compare")
        tabs.addTab(self._log, "Log")
        return tabs

    # ── actions ─────────────────────────────────────────────────────────────

    def _on_choose_document(self) -> None:
        suffixes = sorted(self._service.list_supported_suffixes())
        pattern = " ".join(f"*{s}" for s in suffixes)
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose document", "", f"Supported files ({pattern})"
        )
        if not path:
            return
        self._source = Path(path)
        self._source_label.setText(self._source.name)
        self._refresh_runs()

    def _on_run(self) -> None:
        if self._source is None:
            QMessageBox.information(self, "No document", "Choose a document first.")
            return
        ocr = self._selector.get_ocr_config()
        self._set_running(True)
        self._log.append_text(f"Starting OCR run for {self._source.name}…\n")
        worker = self._worker_factory(
            self._source,
            ocr,
            self._language,
            app_service=self._service,
        )
        worker.log_line.connect(self._log.append_text)
        worker.finished_ok.connect(self._on_run_ok)
        worker.finished_err.connect(self._on_run_err)
        self._worker = worker
        worker.start()

    def _on_stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop_event.set()
            self._log.append_text("Stop requested…\n")

    def _on_run_ok(self, _run: object) -> None:
        self._log.append_text("Run stored.\n")
        self._set_running(False)
        self._refresh_runs()

    def _on_run_err(self, message: str) -> None:
        self._log.append_text(f"ERROR: {message}\n")
        self._set_running(False)

    def _on_selection_changed(self, run_id_a: str, run_id_b: str) -> None:
        runs = {r.run_id: r for r in self._service.list_runs(self._source)} if self._source else {}
        run_a = runs.get(run_id_a)
        run_b = runs.get(run_id_b)
        if run_a is not None and run_b is not None:
            self._compare.show_comparison(run_a, run_b, self._service.compare(run_a, run_b))
        else:
            self._compare.clear()

    def _on_delete_run(self, run_id: str) -> None:
        if self._source is None:
            return
        self._service.delete_run(self._source, run_id)
        self._refresh_runs()

    def _on_clear_all(self) -> None:
        if self._source is None:
            return
        confirm = QMessageBox.question(
            self,
            "Clear all runs",
            f"Delete all stored runs for {self._source.name}?",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self._service.delete_all_runs(self._source)
            self._refresh_runs()

    def closeEvent(self, event: Any) -> None:
        """Stop background threads before the window is destroyed."""
        self._selector.stop_threads()
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop_event.set()
            self._worker.wait(2000)
        super().closeEvent(event)

    # ── helpers ───────────────────────────────────────────────────────────

    def _refresh_runs(self) -> None:
        runs = self._service.list_runs(self._source) if self._source else []
        self._runs_list.set_runs(runs)

    def _set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)


def main_benchmark(*, window_factory=None) -> None:
    """Launch the benchmark GUI."""
    from .._main_window import _app_icon  # noqa: PLC0415  reuse the shared app icon

    app = create_app(icon_factory=_app_icon)
    factory = window_factory or build_benchmark_window
    window = factory()
    window.show()
    sys.exit(app.exec())


def build_benchmark_window(
    *,
    app_service: BenchmarkApplicationService | None = None,
    worker_factory=None,
) -> BenchmarkWindow:
    """Create the benchmark window with optional injected dependencies."""
    return BenchmarkWindow(app_service=app_service, worker_factory=worker_factory)
