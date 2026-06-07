"""End-to-end GUI integration test for the benchmark app.

Drives the real BenchmarkWindow → BenchmarkApplicationService → BenchmarkRunStore
→ domain metrics, with only the OCR runner stubbed, exercising run&store, list,
compare, and delete through the UI.
"""
# pylint: disable=W0621,W0613  # pytest fixtures shadow module-scope names / injected by name

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from teachers_teammate.application.benchmark_service import BenchmarkApplicationService
from teachers_teammate.gui.benchmark.composition import build_benchmark_window
from teachers_teammate.infrastructure.benchmark.run_store import BenchmarkRunStore
from teachers_teammate.infrastructure.benchmark.runner import OcrRunResult


class _SyncSignal:
    """Minimal synchronous signal stand-in for a deterministic worker."""

    def __init__(self) -> None:
        self._cbs: list = []

    def connect(self, cb) -> None:
        self._cbs.append(cb)

    def emit(self, *args) -> None:
        for cb in self._cbs:
            cb(*args)


class _SyncWorker:
    """Worker that runs run_and_store synchronously so the test is deterministic."""

    def __init__(self, source, ocr, language, *, app_service) -> None:
        self._source, self._ocr, self._language = source, ocr, language
        self._service = app_service
        self.log_line = _SyncSignal()
        self.finished_ok = _SyncSignal()
        self.finished_err = _SyncSignal()
        self.stop_event = threading.Event()

    def isRunning(self) -> bool:
        return False

    def start(self) -> None:
        try:
            run = self._service.run_and_store(self._source, ocr=self._ocr, language=self._language)
        except Exception as exc:  # noqa: BLE001  # mirror the real worker's error path
            self.finished_err.emit(str(exc))
            return
        self.finished_ok.emit(run)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path / "storage"))
    texts = iter(["alpha beta gamma", "alpha beta delta", "x"])
    service = BenchmarkApplicationService(
        run_store=BenchmarkRunStore(tmp_path / "bench"),
        runner=lambda *a, **k: OcrRunResult(next(texts), None, 0.1, None),
    )
    win = build_benchmark_window(app_service=service, worker_factory=_SyncWorker)
    qtbot.addWidget(win)
    source = tmp_path / "doc.txt"
    source.write_text("content", encoding="utf-8")
    win._source = source
    win._refresh_runs()
    return win, service, source


@pytest.mark.gui
@pytest.mark.use_case("OCR_Benchmark_Run")
def test_benchmark_end_to_end_run_compare_delete(window) -> None:
    """
    Given  a benchmark window wired to a real service/store with a stubbed runner
    When   two runs are produced, assigned A/B, then one is deleted
    Then   runs persist and list, the compare view shows a similarity, and delete
           removes the run from the store
    """
    win, service, source = window

    # Two run&store cycles (the SyncWorker runs them inline).
    win._on_run()
    win._on_run()
    runs = service.list_runs(source)
    assert len(runs) == 2

    # Assign A and B → compare view renders a similarity score.
    win._runs_list._list.setCurrentRow(0)
    win._runs_list._assign("a")
    win._runs_list._list.setCurrentRow(1)
    win._runs_list._assign("b")
    assert "Similarity:" in win._compare._similarity.text()

    # Delete one run through the window → store now has one.
    win._on_delete_run(runs[0].run_id)
    assert len(service.list_runs(source)) == 1
