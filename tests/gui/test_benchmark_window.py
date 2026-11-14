"""GUI tests for the benchmark window using pytest-qt."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.config import DEFAULTS, OcrConfig
from teachers_teammate.domain.benchmark import compare_pair
from teachers_teammate.gui.benchmark.composition import build_benchmark_window
from teachers_teammate.infrastructure.benchmark.run_store import StoredRun

from ._service_stubs import RecommendationServiceStub


def _run(run_id: str, text: str) -> StoredRun:
    return StoredRun(
        schema_version=1,
        run_id=run_id,
        document_hash="doc1",
        document_path="/docs/sample.pdf",
        display_name="sample.pdf",
        ocr_config_hash="abc12345",
        ocr=OcrConfig(engine="ppocr", model="", preprocess_method="none"),
        language="English",
        raw_text=text,
        preview_img="",
        timestamp=f"2026-06-06T10:00:0{run_id}",
        elapsed_s=1.0,
    )


class _FakeService(RecommendationServiceStub):
    """In-memory benchmark service satisfying the window's calls and the selector's queries."""

    def __init__(self, saved_settings: dict | None = None) -> None:
        super().__init__()
        self.runs: list[StoredRun] = []
        self.deleted: list[str] = []
        self.cleared = False
        self.saved_settings = saved_settings or {}

    # selector queries
    def list_providers(self) -> list[str]:
        return ["ollama"]

    def list_ocr_engines(self) -> list[str]:
        return ["ollama", "ppocr"]

    def get_provider_info(self, provider: str) -> dict:
        return {"models": [], "default_model": "", "needs_api_key": False, "env_key": ""}

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        return []

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        return []

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None: ...

    def is_module_importable(self, module: str) -> bool:
        return True

    def load_saved_settings(self) -> dict:
        return dict(self.saved_settings)

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
        def __init__(self, source, ocr, language, *, ollama_url, app_service) -> None:
            started["ocr"] = ocr
            started["source"] = source
            started["ollama_url"] = ollama_url

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
    assert started["ollama_url"] == DEFAULTS["ollama_url"]  # nothing saved by the main app


@pytest.mark.gui
@pytest.mark.use_case("Hardware_Profile_Override")
def test_benchmark_follows_the_saved_hardware_profile_and_ollama_url(qtbot) -> None:
    """
    Given  settings saved by the main app: a 16 GB computer and Ollama on another host
    When   the Benchmark window opens and a run is started
    Then   its model suggestions are for the 16 GB computer and the run uses that server
    """
    service = _FakeService({"hardware_profile": "16gb", "ollama_url": "http://192.168.1.50:11434"})
    win = build_benchmark_window(app_service=service)  # ty: ignore[invalid-argument-type]
    qtbot.addWidget(win)
    win._source = Path("/docs/sample.pdf")

    selector = win._selector._picker._hw_selector
    qtbot.waitUntil(lambda: selector.resolved() is not None, timeout=5000)
    assert selector.setting() == "16gb"
    assert selector.resolved().profile == "16gb"

    started = {}

    class _FakeWorker:
        def __init__(self, source, ocr, language, *, ollama_url, app_service) -> None:
            started["ollama_url"] = ollama_url
            self.log_line = self.finished_ok = self.finished_err = type(
                "_Sig", (), {"connect": lambda self, fn: None}
            )()

        def start(self) -> None: ...

        def isRunning(self) -> bool:
            return False

    win._worker_factory = _FakeWorker
    win._on_run()
    assert started["ollama_url"] == "http://192.168.1.50:11434"


@pytest.mark.gui
def test_reload_settings_follows_a_server_and_profile_saved_since(qtbot, monkeypatch) -> None:
    """
    Given  a Benchmark window built with the default server and profile
    When   the main app has saved another server and a 16 GB computer, and the window reloads
    Then   the window, its picker and its hardware choice follow the saved values, and the
           models are asked from the new server only (once)
    """
    service = _FakeService()
    win = build_benchmark_window(app_service=service)  # ty: ignore[invalid-argument-type]
    qtbot.addWidget(win)
    reloads: list[int] = []
    monkeypatch.setattr(win._selector, "reload_models", lambda: reloads.append(1))
    service.saved_settings = {"hardware_profile": "16gb", "ollama_url": "http://192.168.1.50:11434"}

    win.reload_settings()

    assert win._ollama_url == "http://192.168.1.50:11434"
    assert win._selector._picker._ollama_url == "http://192.168.1.50:11434"
    assert win._selector._picker._hw_selector.setting() == "16gb"
    assert reloads == []  # set_ollama_url already fetched from the new server


@pytest.mark.gui
def test_reload_settings_asks_for_the_models_again_when_the_server_is_unchanged(
    qtbot, monkeypatch
) -> None:
    """
    Given  a Benchmark window whose model fetch was cut short when it was closed
    When   it reloads while the saved server is still the same
    Then   the model list is asked for again, without resetting the chosen settings
    """
    win = build_benchmark_window(app_service=_FakeService())  # ty: ignore[invalid-argument-type]
    qtbot.addWidget(win)
    reloads: list[int] = []
    monkeypatch.setattr(win._selector, "reload_models", lambda: reloads.append(1))

    win.reload_settings()

    assert reloads == [1]


class _RunningWorker:
    """A worker that is still going; records the stop request."""

    def __init__(self) -> None:
        import threading  # noqa: PLC0415

        self.stop_event = threading.Event()

    def isRunning(self) -> bool:
        return True

    def wait(self, _ms: int) -> bool:
        return True


class _UserClose:
    """A close event from the window system (the user pressed the window's close button)."""

    @staticmethod
    def make():
        from PySide6.QtGui import QCloseEvent  # noqa: PLC0415

        class _Spontaneous(QCloseEvent):
            def spontaneous(self) -> bool:
                return True

        return _Spontaneous()


@pytest.mark.gui
@pytest.mark.parametrize(("answer", "closes"), [("No", False), ("Yes", True)])
def test_closing_by_hand_during_a_run_asks_before_stopping_it(
    window, monkeypatch, answer: str, closes: bool
) -> None:
    """
    Given  a Benchmark window with a run going
    When   the user closes it and answers the question "Stop the run and close?"
    Then   a "No" keeps the window open and the run going, a "Yes" stops the run and closes
    """
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    from teachers_teammate.gui.benchmark import _benchmark_window  # noqa: PLC0415

    win, _service = window
    worker = _RunningWorker()
    win._worker = worker  # ty: ignore[invalid-assignment]  # test injects a running worker stub
    monkeypatch.setattr(
        _benchmark_window.QMessageBox,
        "question",
        lambda *_a, **_k: getattr(QMessageBox.StandardButton, answer),
    )
    event = _UserClose.make()

    win.closeEvent(event)

    assert event.isAccepted() is closes
    assert worker.stop_event.is_set() is closes


@pytest.mark.gui
def test_closing_detaches_a_benchmark_worker_that_does_not_stop_in_time(
    window, monkeypatch
) -> None:
    """
    Given  a Benchmark window whose worker is still running after the 2 s wait
    When   the window is closed
    Then   the worker is detached, so the running thread is not destroyed with the window
    """
    from teachers_teammate.gui.benchmark import _benchmark_window  # noqa: PLC0415

    win, _service = window
    worker = _RunningWorker()
    worker.wait = lambda _ms: False  # ty: ignore[invalid-assignment]  # the stub never stops
    win._worker = worker  # ty: ignore[invalid-assignment]  # test injects a running worker stub
    detached: list = []
    monkeypatch.setattr(_benchmark_window, "detach_running_worker", detached.append)

    win.close()

    assert detached == [worker]


@pytest.mark.gui
def test_closing_from_the_main_window_stops_a_run_without_asking(window, monkeypatch) -> None:
    """
    Given  a Benchmark window with a run going
    When   the main window closes it as the app quits (not an event from the window system)
    Then   the run is stopped and nobody is asked
    """
    from teachers_teammate.gui.benchmark import _benchmark_window  # noqa: PLC0415

    win, _service = window
    worker = _RunningWorker()
    win._worker = worker  # ty: ignore[invalid-assignment]  # test injects a running worker stub

    def _no_question(*_a, **_k):
        raise AssertionError("closing from the main window must not ask")

    monkeypatch.setattr(_benchmark_window.QMessageBox, "question", _no_question)

    win.close()

    assert worker.stop_event.is_set()
