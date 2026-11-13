"""GUI worker/thread tests using pytest-qt."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.gui._qt_stream import QtStream
from teachers_teammate.gui._worker import (
    OCRWorker,
    _ConnectionCheckThread,
)
from teachers_teammate.domain.run_summary import RunStatus, RunSummary
from teachers_teammate.infrastructure.reporting import FileDoneResult, KeepAwakeState
from tests.conftest import make_config


class _DummyAppService:
    def run_selected(
        self,
        _config,
        *,
        selected_source_paths=None,
        stop_event=None,
        stop_after_file_event=None,
        reporter=None,
        progress=None,
    ) -> RunSummary:
        _ = stop_event
        _ = stop_after_file_event
        _ = selected_source_paths
        _ = reporter
        source_id = "/tmp/a.png"
        if progress is not None:
            if progress.on_keep_awake:
                progress.on_keep_awake(KeepAwakeState(enabled=True, held=True, on_battery=False))
            if progress.on_file_started:
                progress.on_file_started(source_id, "a.png", 1, 1)
            if progress.on_stage_started:
                progress.on_stage_started(source_id, "preprocessing")
                progress.on_stage_started(source_id, "ocr")
            if progress.on_ocr_done:
                progress.on_ocr_done(source_id, "a.png")
            if progress.on_file_done:
                progress.on_file_done(
                    FileDoneResult(
                        source_id,
                        "a.png",
                        True,
                        "saved",
                        raw_text="raw.txt",
                        text_format="markdown",
                    )
                )
        return RunSummary(succeeded=("a.png",))


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
    Then   file_started, ocr_done, file_done, keep_awake_changed and run_finished are emitted
    """
    cfg = make_config(tmp_path)
    worker = OCRWorker(cfg, app_service=_DummyAppService())

    seen: dict[str, int] = {"started": 0, "ocr": 0, "done": 0, "awake": 0, "finished": 0}
    worker.file_started.connect(lambda *_: seen.__setitem__("started", seen["started"] + 1))
    worker.ocr_done.connect(lambda *_: seen.__setitem__("ocr", seen["ocr"] + 1))
    worker.file_done.connect(lambda *_: seen.__setitem__("done", seen["done"] + 1))
    worker.keep_awake_changed.connect(lambda *_: seen.__setitem__("awake", seen["awake"] + 1))
    worker.run_finished.connect(lambda *_: seen.__setitem__("finished", seen["finished"] + 1))

    worker.run()
    qtbot.wait(10)

    assert seen == {"started": 1, "ocr": 1, "done": 1, "awake": 1, "finished": 1}


@pytest.mark.gui
def test_ocr_worker_emits_stage_started_with_file_position(qtbot, tmp_path: Path) -> None:
    """
    Given  a pipeline that reports the preprocessing and OCR stages for a file
    When   run() is invoked
    Then   stage_started is emitted for each stage with the file name and its idx/total
    """
    cfg = make_config(tmp_path)
    worker = OCRWorker(cfg, app_service=_DummyAppService())
    stages: list[tuple] = []
    worker.stage_started.connect(lambda *args: stages.append(args))

    worker.run()
    qtbot.wait(10)

    assert stages == [
        ("/tmp/a.png", "a.png", "preprocessing", 1, 1),
        ("/tmp/a.png", "a.png", "ocr", 1, 1),
    ]


@pytest.mark.gui
def test_ocr_worker_passes_selected_source_paths(qtbot, tmp_path: Path) -> None:
    """
    Given  an OCRWorker created with selected source paths
    When   run() is invoked
    Then   selected source paths are forwarded to OCRPipeline
    """

    captured: dict[str, list[str]] = {"selected": []}

    class _CaptureAppService(_DummyAppService):
        def run_selected(self, _config, *, selected_source_paths=None, **kwargs) -> RunSummary:
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
    Then   a fatal log message is emitted and run_finished carries a failed RunSummary
    """

    class _RaiseAppService:
        def run_selected(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    cfg = make_config(tmp_path)
    worker = OCRWorker(cfg, app_service=_RaiseAppService())

    logs: list[str] = []
    summaries: list[RunSummary] = []
    worker.log_line.connect(lambda text: logs.append(text))
    worker.run_finished.connect(summaries.append)

    worker.run()
    qtbot.wait(10)

    assert any("FATAL ERROR" in line for line in logs)
    assert [(s.status, s.error) for s in summaries] == [(RunStatus.FAILED, "boom")]


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


@pytest.mark.gui
def test_ocr_worker_file_done_event_carries_the_text_format(qtbot, tmp_path: Path) -> None:
    """
    Given  a pipeline that reports the text format of a finished file
    When   run() is invoked
    Then   the file_done event carries that format, so the preview does not depend on a
           later state lookup
    """
    worker = OCRWorker(make_config(tmp_path), app_service=_DummyAppService())
    events: list = []
    worker.file_done.connect(events.append)

    worker.run()
    qtbot.wait(10)

    assert [event.text_format for event in events] == ["markdown"]


@pytest.mark.gui
def test_ocr_worker_request_stop_sets_the_right_events(tmp_path: Path) -> None:
    """
    Given  an OCRWorker
    When   request_stop(now=False) and then request_stop(now=True) are called
    Then   the first sets only the stop-after-file event, the second also "Stop now"
    """
    worker = OCRWorker(make_config(tmp_path), app_service=_DummyAppService())

    worker.request_stop(now=False)
    assert worker.stop_after_file_event.is_set() is True
    assert worker.stop_event.is_set() is False

    worker.request_stop(now=True)
    assert worker.stop_event.is_set() is True


@pytest.mark.gui
def test_ocr_worker_passes_both_stop_events_to_the_service(qtbot, tmp_path: Path) -> None:
    """
    Given  an OCRWorker
    When   run() is invoked
    Then   the application service receives the worker's two stop events
    """
    seen: dict = {}

    class _Capture(_DummyAppService):
        def run_selected(self, _config, **kwargs) -> RunSummary:
            seen.update(kwargs)
            return RunSummary()

    worker = OCRWorker(make_config(tmp_path), app_service=_Capture())

    worker.run()
    qtbot.wait(10)

    assert seen["stop_event"] is worker.stop_event
    assert seen["stop_after_file_event"] is worker.stop_after_file_event


@pytest.mark.gui
def test_ocr_worker_file_done_event_carries_the_stopped_flag(qtbot, tmp_path: Path) -> None:
    """
    Given  a pipeline that reports a file as stopped
    When   run() is invoked
    Then   the file_done event has stopped set and is no success
    """

    class _Stopped(_DummyAppService):
        def run_selected(self, _config, *, progress=None, **_kwargs) -> RunSummary:
            assert progress is not None and progress.on_file_done is not None
            progress.on_file_done(
                FileDoneResult(
                    "/tmp/a.png", "a.png", False, "Stopped before proofreading.", stopped=True
                )
            )
            return RunSummary(stopped=("a.png",))

    worker = OCRWorker(make_config(tmp_path), app_service=_Stopped())
    events: list = []
    worker.file_done.connect(events.append)

    worker.run()
    qtbot.wait(10)

    assert [(e.stopped, e.ok) for e in events] == [(True, False)]
