"""Unit tests for the benchmark application service."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.application.benchmark_service import (
    BenchmarkApplicationService,
    BenchmarkRunError,
)
from teachers_teammate.config import OcrConfig
from teachers_teammate.infrastructure.benchmark.run_store import BenchmarkRunStore
from teachers_teammate.infrastructure.benchmark.runner import OcrRunResult


def _service(tmp_path: Path, runner) -> BenchmarkApplicationService:
    return BenchmarkApplicationService(run_store=BenchmarkRunStore(tmp_path), runner=runner)


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "doc.txt"
    src.write_text("content", encoding="utf-8")
    return src


@pytest.mark.use_case("OCR_Benchmark_Run")
def test_run_and_store_persists_and_returns_run(tmp_path: Path) -> None:
    """
    Given  a runner that succeeds
    When   run_and_store() is called
    Then   a StoredRun is returned and is readable via list_runs()
    """
    runner = lambda *a, **k: OcrRunResult("hello text", None, 0.2, None)  # noqa: E731
    service = _service(tmp_path, runner)
    source = _source(tmp_path)

    run = service.run_and_store(
        source, ocr=OcrConfig(engine="tesseract", model=""), language="English"
    )
    assert run.raw_text == "hello text"
    runs = service.list_runs(source)
    assert [r.run_id for r in runs] == [run.run_id]


def test_run_and_store_raises_on_runner_error(tmp_path: Path) -> None:
    """
    Given  a runner that reports an error
    When   run_and_store() is called
    Then   BenchmarkRunError is raised and nothing is stored
    """
    runner = lambda *a, **k: OcrRunResult("", None, 0.1, "boom")  # noqa: E731
    service = _service(tmp_path, runner)
    source = _source(tmp_path)

    with pytest.raises(BenchmarkRunError, match="boom"):
        service.run_and_store(source, ocr=OcrConfig(engine="tesseract"), language="English")
    assert service.list_runs(source) == []


@pytest.mark.use_case("Benchmark_Run_Deletion")
def test_delete_run_and_delete_all(tmp_path: Path) -> None:
    """
    Given  two stored runs for a document
    When   delete_run() then delete_all_runs() are called
    Then   the targeted run, then all runs, are removed
    """
    runner = lambda *a, **k: OcrRunResult("t", None, 0.1, None)  # noqa: E731
    service = _service(tmp_path, runner)
    source = _source(tmp_path)
    r1 = service.run_and_store(source, ocr=OcrConfig(engine="tesseract"), language="English")
    service.run_and_store(source, ocr=OcrConfig(engine="tesseract"), language="English")

    service.delete_run(source, r1.run_id)
    assert all(r.run_id != r1.run_id for r in service.list_runs(source))

    service.delete_all_runs(source)
    assert service.list_runs(source) == []


@pytest.mark.use_case("OCR_Run_Comparison")
def test_compare_returns_similarity(tmp_path: Path) -> None:
    """
    Given  two stored runs with different text
    When   compare() is called
    Then   a PairComparison with a bounded similarity score is returned
    """
    texts = iter(["alpha beta", "alpha gamma"])
    runner = lambda *a, **k: OcrRunResult(next(texts), None, 0.1, None)  # noqa: E731
    service = _service(tmp_path, runner)
    source = _source(tmp_path)
    a = service.run_and_store(source, ocr=OcrConfig(engine="tesseract"), language="English")
    b = service.run_and_store(source, ocr=OcrConfig(engine="tesseract"), language="English")

    result = service.compare(a, b)
    assert 0.0 < result.similarity < 1.0
