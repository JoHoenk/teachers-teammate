"""GUI tests for the benchmark window using pytest-qt."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.domain.benchmark import compare_pair
from teachers_teammate.gui.benchmark.composition import build_benchmark_window
from teachers_teammate.infrastructure.benchmark.run_store import StoredRun


def _run(run_id: str, text: str) -> StoredRun:
    return StoredRun(
        schema_version=1,
        run_id=run_id,
        document_hash="doc1",
        document_path="/docs/sample.pdf",
        display_name="sample.pdf",
        ocr_config_hash="abc12345",
        ocr=OcrConfig(engine="tesseract", model="", preprocess_method="none"),
        language="English",
        raw_text=text,
        preview_img="",
        timestamp=f"2026-06-06T10:00:0{run_id}",
        elapsed_s=1.0,
    )


class _FakeService:
    """In-memory benchmark service satisfying the window's calls and the selector's queries."""

    def __init__(self) -> None:
        self.runs: list[StoredRun] = []
        self.deleted: list[str] = []
        self.cleared = False

    # selector queries
    def list_providers(self) -> list[str]:
        return ["ollama"]

    def list_ocr_engines(self) -> list[str]:
        return ["ollama", "tesseract"]

    def default_preprocess_for_engine(self, engine: str) -> str:
        return "none"

    def get_provider_info(self, provider: str) -> dict:
        return {"models": [], "default_model": "", "needs_api_key": False, "env_key": ""}

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        return []

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        return []

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None: ...

    def is_module_importable(self, module: str) -> bool:
        return True

    # window operations
    def list_supported_suffixes(self):
        return frozenset({".png", ".pdf"})

    def list_runs(self, source: Path) -> list[StoredRun]:
        return list(self.runs)

    def delete_run(self, source: Path, run_id: str) -> None:
        self.deleted.append(run_id)
        self.runs = [r for r in self.runs if r.run_id != run_id]

    def delete_all_runs(self, source: Path) -> None:
        self.cleared = True
        self.runs = []

    def compare(self, a: StoredRun, b: StoredRun):
        return compare_pair(a.raw_text, b.raw_text)


@pytest.fixture
def window(qtbot):
    service = _FakeService()
    # ty: the in-memory fake intentionally stands in for the real service.
    win = build_benchmark_window(app_service=service)  # ty: ignore[invalid-argument-type]
    qtbot.addWidget(win)
    win._source = Path("/docs/sample.pdf")  # test setup
    return win, service


@pytest.mark.gui
@pytest.mark.use_case("OCR_Run_Comparison")
def test_runs_populate_and_compare_renders(window) -> None:
    """
    Given  a document with two stored runs
    When   the runs are assigned as A and B
    Then   the compare view shows a similarity score (computed via the service)
    """
    win, service = window
    service.runs = [_run("1", "hello world"), _run("2", "hello there")]
    win._refresh_runs()

    runs_list = win._runs_list
    runs_list._list.setCurrentRow(0)
    runs_list._assign("a")
    runs_list._list.setCurrentRow(1)
    runs_list._assign("b")

    assert "Similarity:" in win._compare._similarity.text()


@pytest.mark.gui
@pytest.mark.use_case("Benchmark_Run_Deletion")
def test_delete_goes_through_service(window) -> None:
    """
    Given  a stored run selected in the list
    When   delete is requested
    Then   the window asks the service to delete it (never the store directly)
    """
    win, service = window
    service.runs = [_run("1", "a"), _run("2", "b")]
    win._refresh_runs()
    win._on_delete_run("1")

    assert service.deleted == ["1"]
    assert [r.run_id for r in win._runs_list._runs] == ["2"]


@pytest.mark.gui
def test_run_uses_injected_worker(window, monkeypatch) -> None:
    """
    Given  an injected worker factory
    When   Run & store is clicked
    Then   the window builds a worker with the selected OcrConfig and starts it
    """
    win, service = window
    started = {}

    class _FakeWorker:
        def __init__(self, source, ocr, language, *, app_service) -> None:
            started["ocr"] = ocr
            started["source"] = source

            class _Sig:
                def connect(self, _fn) -> None: ...

            self.log_line = _Sig()
            self.finished_ok = _Sig()
            self.finished_err = _Sig()

        def start(self) -> None:
            started["started"] = True

        def isRunning(self) -> bool:
            return False

    win._worker_factory = _FakeWorker
    win._on_run()

    assert started.get("started") is True
    assert isinstance(started["ocr"], OcrConfig)
