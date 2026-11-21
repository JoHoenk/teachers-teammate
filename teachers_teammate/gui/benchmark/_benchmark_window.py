"""Benchmark main window: run OCR configs, store history, compare two runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...application.benchmark_service import BenchmarkApplicationService
from ...application.composition import inject_addon_packages_dir
from ...application.service import ProcessingApplicationService
from ...config import DEFAULTS
from ...texts import dialogs as dlg
from .. import _dialogs
from .._app_bootstrap import create_app, finish_process
from .._log_widget import LogWidget
from .._ocr_config_selector import OcrConfigSelector
from .._worker import detach_running_worker
from ._benchmark_worker import BenchmarkWorker
from ._compare_view import CompareView
from ._runs_list import RunsList


class BenchmarkWindow(QMainWindow):
    """Thin controller wiring the shared widgets to the benchmark service."""

    def __init__(
        self,
        *,
        app_service: BenchmarkApplicationService,
        worker_factory=None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Teacher's Teammate — OCR Benchmark")
        self.resize(1100, 720)
        self._service = app_service
        self._worker_factory = worker_factory or BenchmarkWorker
        self._worker: BenchmarkWorker | None = None
        self._source: Path | None = None
        self._language = DEFAULTS["language"]
        # Follow the main app: its Ollama server and its "My computer" choice.
        self._ollama_url, self._hardware_profile = self._saved_server_and_profile()

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
        self._selector = OcrConfigSelector(
            app_service=self._service,
            ollama_url=self._ollama_url,
            hardware_profile=self._hardware_profile,
            show_preview_button=False,
            show_pdf_text_layer=False,
            expert_collapsed=False,
        )
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
        chosen = _dialogs.pick_file(self, dlg.choose_document(pattern))
        if chosen is None:
            return
        self._source = chosen
        self._source_label.setText(self._source.name)
        self._refresh_runs()

    def _on_run(self) -> None:
        if self._source is None:
            _dialogs.notify(self, dlg.no_document())
            return
        ocr = self._selector.get_ocr_config()
        self._set_running(True)
        self._log.append_text(f"Starting OCR run for {self._source.name}…\n")
        worker = self._worker_factory(
            self._source,
            ocr,
            self._language,
            ollama_url=self._ollama_url,
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
        if _dialogs.ask(self, dlg.clear_all_runs(self._source.name)):
            self._service.delete_all_runs(self._source)
            self._refresh_runs()

    def _saved_server_and_profile(self) -> tuple[str, str]:
        """Return the Ollama server and the "My computer" choice the main app saved."""
        saved = self._service.load_saved_settings()
        return (
            str(saved.get("ollama_url") or DEFAULTS["ollama_url"]),
            str(saved.get("hardware_profile") or DEFAULTS["hardware_profile"]),
        )

    def reload_settings(self) -> None:
        """Follow the main app again when this window is shown a second time.

        The window is kept between uses, so a server or "My computer" choice changed in the
        Settings window since it was built would otherwise go unnoticed.  The model list is
        asked for again too: closing the window cut off a fetch that was still running.
        """
        url, profile = self._saved_server_and_profile()
        url_changed = url != self._ollama_url
        self._ollama_url, self._hardware_profile = url, profile
        self._selector.set_hardware_profile(profile)
        self._selector.set_ollama_url(url)  # asks the new server for its models
        if not url_changed:
            self._selector.reload_models()

    def _confirm_stop_running_worker(self) -> bool:
        """Ask whether to stop the running benchmark when the user closes the window."""
        return _dialogs.ask(self, dlg.benchmark_still_running())

    def closeEvent(self, event: Any) -> None:
        """Stop background threads before the window is destroyed.

        The user closing the window while a run is going is asked first; the main window
        closing it as it quits (not a ``spontaneous`` event) stops the run without asking.
        """
        running = self._worker is not None and self._worker.isRunning()
        if running and event.spontaneous() and not self._confirm_stop_running_worker():
            event.ignore()
            return
        self._selector.stop_threads()
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop_event.set()
            if not self._worker.wait(2000):
                # A running QThread must not be destroyed with its window.
                detach_running_worker(self._worker)
        super().closeEvent(event)

    # ── helpers ───────────────────────────────────────────────────────────

    def _refresh_runs(self) -> None:
        runs = self._service.list_runs(self._source) if self._source else []
        self._runs_list.set_runs(runs)

    def _set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)


def _default_window_factory() -> BenchmarkWindow:
    """Build the window with the process's services, for ``teachers-teammate-benchmark``."""
    from .composition import build_benchmark_window  # noqa: PLC0415  # imports this module

    return build_benchmark_window()


def main_benchmark(*, window_factory=None) -> None:
    """Launch the benchmark GUI."""
    inject_addon_packages_dir()  # idempotent; a wheel install's entry point starts here
    from .._main_window import _app_icon  # noqa: PLC0415  reuse the shared app icon

    # Start the slow own-host-name lookup before any widget asks where a step runs.
    ProcessingApplicationService.warm_up_locality_checks()
    app = create_app(icon_factory=_app_icon)
    factory = window_factory or _default_window_factory
    window = factory()
    window.show()
    finish_process(app.exec())
