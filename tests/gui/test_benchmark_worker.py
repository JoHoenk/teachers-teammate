"""Behaviour tests for the benchmark background worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.domain.setting_values import PreprocessMethod
from teachers_teammate.application.benchmark_service import (
    BenchmarkRunError,
    BenchmarkRunStopped,
)
from teachers_teammate.config import OcrConfig
from teachers_teammate.gui.benchmark._benchmark_worker import BenchmarkWorker
from teachers_teammate.infrastructure.benchmark.run_store import StoredRun


def _stored_run() -> StoredRun:
    return StoredRun(
        schema_version=1,
        run_id="r1",
        document_hash="doc1",
        document_path="/docs/sample.pdf",
        display_name="sample.pdf",
        ocr_config_hash="abc12345",
        ocr=OcrConfig(engine="ppocr", model="", preprocess_method=PreprocessMethod.NONE),
        language="English",
        raw_text="text",
        preview_img="",
        timestamp="2026-06-06T10:00:00",
        elapsed_s=1.0,
    )


class _OkService:
    def run_and_store(self, source, *, ocr, language, ollama_url, stop_event, on_progress):
        _ = (source, ocr, language, ollama_url, stop_event)
        on_progress("working")
        return _stored_run()


class _FailService:
    def run_and_store(self, source, *, ocr, language, ollama_url, stop_event, on_progress):
        _ = (source, ocr, language, ollama_url, stop_event, on_progress)
        raise BenchmarkRunError("engine missing")


class _StoppedService:
    def run_and_store(self, source, *, ocr, language, ollama_url, stop_event, on_progress):
        _ = (source, ocr, language, ollama_url, stop_event, on_progress)
        raise BenchmarkRunStopped


def _worker(service) -> BenchmarkWorker:
    return BenchmarkWorker(
        Path("/docs/sample.pdf"), OcrConfig(engine="ppocr"), "English", app_service=service
    )


@pytest.mark.gui
@pytest.mark.use_case("OCR_Benchmark_Run")
def test_worker_emits_finished_ok(qtbot) -> None:
    """
    Given  a service whose run_and_store succeeds
    When   the worker runs
    Then   finished_ok fires with the StoredRun and the progress it reported is emitted
    """
    worker = _worker(_OkService())
    lines: list[str] = []
    worker.progress.connect(lines.append)
    with qtbot.waitSignal(worker.finished_ok, timeout=3000) as blocker:
        worker.start()
    worker.wait(3000)

    assert isinstance(blocker.args[0], StoredRun)
    assert lines == ["working"]


@pytest.mark.gui
def test_worker_emits_finished_err(qtbot) -> None:
    """
    Given  a service whose run_and_store raises BenchmarkRunError
    When   the worker runs
    Then   finished_err fires with the error's own words and does not crash
    """
    worker = _worker(_FailService())
    with qtbot.waitSignal(worker.finished_err, timeout=3000) as blocker:
        worker.start()
    worker.wait(3000)

    assert "engine missing" in blocker.args[0]


@pytest.mark.gui
def test_worker_reports_a_stopped_run_as_no_failure(qtbot) -> None:
    """
    Given  a service whose run_and_store raises BenchmarkRunStopped
    When   the worker runs
    Then   finished_stopped fires, so the window shows no failure notice
    """
    worker = _worker(_StoppedService())
    with qtbot.waitSignal(worker.finished_stopped, timeout=3000):
        worker.start()
    worker.wait(3000)
