"""GUI worker/thread tests using pytest-qt."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.gui._qt_stream import QtStream
from teachers_teammate.gui._worker import (
    OCRWorker,
    _ConnectionCheckThread,
)
from tests.conftest import make_config


class _DummyAppService:
    def run_selected(
        self,
        _config,
        *,
        selected_source_paths=None,
        stop_event=None,
        reporter=None,
        on_file_started=None,
        on_ocr_done=None,
        on_file_done=None,
    ) -> int:
        _ = stop_event
        _ = selected_source_paths
        _ = reporter
        source_id = "/tmp/a.png"
        if on_file_started:
            on_file_started(source_id, "a.png", 1, 1)
        if on_ocr_done:
            on_ocr_done(source_id, "a.png")
        if on_file_done:
            on_file_done(source_id, "a.png", True, "saved", "", "raw.txt", "", "")
        return 0


@pytest.mark.gui
def test_qt_stream_forwards_text() -> None:
    """
    Given  a QtStream bound to a callback signal-like object
    When   write() is called with text
    Then   the text is forwarded and the written length is returned
    """

    class _Signal:
        def __init__(self) -> None:
            self.value = ""

        def emit(self, text: str) -> None:
            self.value += text

    sig = _Signal()
    stream = QtStream(sig)
    n = stream.write("hello")
    assert n == 5
    assert sig.value == "hello"


@pytest.mark.gui
def test_ocr_worker_emits_lifecycle_signals(qtbot, tmp_path: Path) -> None:
    """
    Given  an OCRWorker with a pipeline that emits one started/ocr-done/file-done cycle
    When   run() is invoked
    Then   file_started, ocr_done, file_done, and finished_with_code are emitted
    """
    cfg = make_config(tmp_path)
    worker = OCRWorker(cfg, app_service=_DummyAppService())

    seen: dict[str, int] = {"started": 0, "ocr": 0, "done": 0, "finished": 0}
    worker.file_started.connect(lambda *_: seen.__setitem__("started", seen["started"] + 1))
    worker.ocr_done.connect(lambda *_: seen.__setitem__("ocr", seen["ocr"] + 1))
    worker.file_done.connect(lambda *_: seen.__setitem__("done", seen["done"] + 1))
    worker.finished_with_code.connect(lambda *_: seen.__setitem__("finished", seen["finished"] + 1))

    worker.run()
    qtbot.wait(10)

    assert seen == {"started": 1, "ocr": 1, "done": 1, "finished": 1}


@pytest.mark.gui
def test_ocr_worker_passes_selected_source_paths(qtbot, tmp_path: Path) -> None:
    """
    Given  an OCRWorker created with selected source paths
    When   run() is invoked
    Then   selected source paths are forwarded to OCRPipeline
    """

    captured: dict[str, list[str]] = {"selected": []}

    class _CaptureAppService(_DummyAppService):
        def run_selected(self, _config, *, selected_source_paths=None, **kwargs) -> int:
            captured["selected"] = list(selected_source_paths or [])
            return super().run_selected(
                _config, selected_source_paths=selected_source_paths, **kwargs
            )

    cfg = make_config(tmp_path)
    worker = OCRWorker(
        cfg,
        selected_source_paths=["/tmp/a.png", "/tmp/b.png"],
        app_service=_CaptureAppService(),
    )

    worker.run()
    qtbot.wait(10)

    assert captured["selected"] == ["/tmp/a.png", "/tmp/b.png"]


@pytest.mark.gui
def test_ocr_worker_emits_fatal_error_on_pipeline_exception(qtbot, tmp_path: Path) -> None:
    """
    Given  an OCRWorker whose application service run path raises
    When   run() is invoked
    Then   a fatal log message is emitted and finished_with_code emits 1
    """

    class _RaiseAppService:
        def run_selected(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    cfg = make_config(tmp_path)
    worker = OCRWorker(cfg, app_service=_RaiseAppService())

    logs: list[str] = []
    codes: list[int] = []
    worker.log_line.connect(lambda text: logs.append(text))
    worker.finished_with_code.connect(lambda code: codes.append(code))

    worker.run()
    qtbot.wait(10)

    assert any("FATAL ERROR" in line for line in logs)
    assert codes == [1]


@pytest.mark.gui
def test_connection_thread_no_engine_or_provider(qtbot) -> None:
    """
    Given  a _ConnectionCheckThread with neither engine nor provider
    When   run() is invoked
    Then   check_done emits False with a 'Nothing to check' message

    The engine/provider matrix for check_connection itself is covered in
    tests/unit/test_service.py; here we only exercise the GUI thread wiring.
    """
    thread = _ConnectionCheckThread()
    results: list[tuple[bool, str]] = []
    thread.check_done.connect(lambda ok, _model_ok, msg: results.append((ok, msg)))
    thread.run()
    qtbot.wait(10)
    assert results and results[0][0] is False
    assert "Nothing" in results[0][1]
