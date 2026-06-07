"""Behaviour tests for the benchmark background worker."""
# pylint: disable=W0621,W0613  # pytest fixtures shadow module-scope names / injected by name

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.application.benchmark_service import BenchmarkRunError
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
        ocr=OcrConfig(engine="tesseract", model="", preprocess_method="none"),
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
        print("engine log line")  # exercised via the QtStream bridge
        return _stored_run()


class _FailService:
    def run_and_store(self, source, *, ocr, language, ollama_url, stop_event, on_progress):
        _ = (source, ocr, language, ollama_url, stop_event, on_progress)
        raise BenchmarkRunError("engine missing")


def _worker(service) -> BenchmarkWorker:
    return BenchmarkWorker(
        Path("/docs/sample.pdf"), OcrConfig(engine="tesseract"), "English", app_service=service
    )


@pytest.mark.gui
@pytest.mark.use_case("OCR_Benchmark_Run")
def test_worker_emits_finished_ok(qtbot) -> None:
    """
    Given  a service whose run_and_store succeeds
    When   the worker runs
    Then   finished_ok fires with the StoredRun and stdout is bridged to log_line
    """
    worker = _worker(_OkService())
    logs: list[str] = []
    worker.log_line.connect(logs.append)
    with qtbot.waitSignal(worker.finished_ok, timeout=3000) as blocker:
        worker.start()
    worker.wait(3000)

    assert isinstance(blocker.args[0], StoredRun)
    assert any("engine log line" in line for line in logs)


@pytest.mark.gui
def test_worker_emits_finished_err(qtbot) -> None:
    """
    Given  a service whose run_and_store raises BenchmarkRunError
    When   the worker runs
    Then   finished_err fires with the error message and does not crash
    """
    worker = _worker(_FailService())
    with qtbot.waitSignal(worker.finished_err, timeout=3000) as blocker:
        worker.start()
    worker.wait(3000)

    assert "engine missing" in blocker.args[0]
