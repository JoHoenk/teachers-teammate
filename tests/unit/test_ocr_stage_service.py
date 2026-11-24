"""Unit tests for teachers_teammate.infrastructure.workflow.ocr_service."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from teachers_teammate.exceptions import OCRError, RunStoppedError, StageError, StageErrorKind
from teachers_teammate.infrastructure.workflow.ocr_service import OCRStageService
from teachers_teammate.messages import MessageKey

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_svc(
    *,
    process_side_effect: object = None,
    process_return: str = "extracted text",
    stop_event: threading.Event | None = None,
) -> tuple[OCRStageService, MagicMock]:
    processor = MagicMock()
    if process_side_effect is not None:
        processor.process_image.side_effect = process_side_effect
    else:
        processor.process_image.return_value = process_return
    return OCRStageService(processor=processor, stop_event=stop_event), processor


# ── Empty input list ───────────────────────────────────────────────────────


def test_run_pages_empty_input_returns_empty(tmp_path: Path) -> None:
    """
    Given  an empty list of OCR inputs
    When   run_pages is called
    Then   the result is an empty list and processor is not called
    """
    svc, processor = _make_svc()
    texts = svc.run_pages([], "English")

    assert texts == []
    processor.process_image.assert_not_called()


# ── Page progress callback ─────────────────────────────────────────────────


def test_run_pages_reports_each_page_before_processing(tmp_path: Path) -> None:
    """
    Given  two OCR input pages and an on_page callback
    When   run_pages is called
    Then   the callback is invoked once per page with (page_number, total), in order
    """
    p1 = tmp_path / "p1.png"
    p2 = tmp_path / "p2.png"
    calls: list[tuple[int, int]] = []
    svc, _processor = _make_svc()

    svc.run_pages([p1, p2], "English", on_page=lambda page, total: calls.append((page, total)))

    assert calls == [(1, 2), (2, 2)]


# ── Single page ────────────────────────────────────────────────────────────


def test_run_pages_single_page_calls_processor_once(tmp_path: Path) -> None:
    """
    Given  a single image path
    When   run_pages is called
    Then   processor.process_image is called once and the text is returned
    """
    img = tmp_path / "page.png"
    svc, processor = _make_svc(process_return="hello world")

    texts = svc.run_pages([img], "English")

    assert texts == ["hello world"]
    processor.process_image.assert_called_once_with(img, language="English")


# ── Output cleaning ────────────────────────────────────────────────────────


def test_run_pages_cleans_processor_output(tmp_path: Path) -> None:
    """
    Given  a processor that returns text polluted with reasoning/template artifacts
    When   run_pages is called
    Then   the returned page text has the artifacts stripped
    """
    img = tmp_path / "page.png"
    svc, _ = _make_svc(
        process_return="<think>reading…</think><\uff5cAssistant\uff5c>Hello world<|im_end|>"
    )

    texts = svc.run_pages([img], "English")

    assert texts == ["Hello world"]


# ── Multi-page ─────────────────────────────────────────────────────────────


def test_run_pages_two_pages_calls_processor_in_order(tmp_path: Path) -> None:
    """
    Given  two image paths
    When   run_pages is called
    Then   processor.process_image is called twice in order and both texts are returned
    """
    p1 = tmp_path / "p1.png"
    p2 = tmp_path / "p2.png"
    processor = MagicMock()
    processor.process_image.side_effect = ["page one", "page two"]
    svc = OCRStageService(processor=processor, stop_event=None)

    texts = svc.run_pages([p1, p2], "German")

    assert texts == ["page one", "page two"]
    assert processor.process_image.call_count == 2


# ── OCRError ───────────────────────────────────────────────────────────────


def test_run_pages_ocr_error_single_page_returns_image_label(tmp_path: Path) -> None:
    """
    Given  process_image raises OCRError on the only page
    When   run_pages is called
    Then   a StageError of the reader carries the OCR-failed message without a page number —
           the error is about the image — and the reason, and no page text comes back
    """
    img = tmp_path / "doc.png"
    svc, _ = _make_svc(process_side_effect=OCRError("timeout"))

    with pytest.raises(StageError) as raised:
        svc.run_pages([img], "English")

    assert raised.value.kind is StageErrorKind.ENGINE
    assert raised.value.stage == "ocr"
    message = raised.value.args[0]
    assert message.key is MessageKey.OCR_FAILED
    assert message.args["page"] is None
    assert message.args["reason"].args["text"] == "timeout"


def test_run_pages_ocr_error_multi_page_returns_page_label(tmp_path: Path) -> None:
    """
    Given  two pages and process_image raises OCRError on the second page
    When   run_pages is called
    Then   the StageError's message names page 2
    """
    p1 = tmp_path / "p1.png"
    p2 = tmp_path / "p2.png"
    processor = MagicMock()
    processor.process_image.side_effect = ["first ok", OCRError("read error")]
    svc = OCRStageService(processor=processor, stop_event=None)

    with pytest.raises(StageError) as raised:
        svc.run_pages([p1, p2], "English")

    message = raised.value.args[0]
    assert message.key is MessageKey.OCR_FAILED
    assert message.args["page"] == 2


# ── Stop event ─────────────────────────────────────────────────────────────


def test_run_pages_stop_event_set_before_loop_returns_stopped(tmp_path: Path) -> None:
    """
    Given  stop_event is already set before run_pages begins iterating
    When   run_pages is called with one image
    Then   RunStoppedError is raised (a stop is no OCR error) and processor is not called
    """
    img = tmp_path / "doc.png"
    stop = threading.Event()
    stop.set()
    svc, processor = _make_svc(stop_event=stop)

    with pytest.raises(RunStoppedError):
        svc.run_pages([img], "English")

    processor.process_image.assert_not_called()


def test_run_pages_stop_event_mid_iteration(tmp_path: Path) -> None:
    """
    Given  two pages and stop_event is set after the first page is processed
    When   run_pages is called
    Then   the second page is not processed and RunStoppedError is raised
    """
    p1 = tmp_path / "p1.png"
    p2 = tmp_path / "p2.png"
    stop = threading.Event()
    processor = MagicMock()

    call_count = 0

    def process_and_maybe_stop(path: Path, *, language: str) -> str:
        nonlocal call_count
        call_count += 1
        stop.set()  # set after first call so second iteration checks it
        return "first page"

    processor.process_image.side_effect = process_and_maybe_stop
    svc = OCRStageService(processor=processor, stop_event=stop)

    with pytest.raises(RunStoppedError):
        svc.run_pages([p1, p2], "English")

    assert call_count == 1


def test_run_pages_ocr_error_while_stopping_is_a_stop_not_an_error(tmp_path: Path) -> None:
    """
    Given  the engine raises OCRError for a request that "Stop now" cut off (stop_event is set)
    When   run_pages is called
    Then   RunStoppedError is raised instead of an "OCR failed" message
    """
    stop = threading.Event()
    processor = MagicMock()

    def fail_while_stopping(path: Path, *, language: str) -> str:
        stop.set()
        raise OCRError("connection closed")

    processor.process_image.side_effect = fail_while_stopping
    svc = OCRStageService(processor=processor, stop_event=stop)

    with pytest.raises(RunStoppedError):
        svc.run_pages([tmp_path / "p1.png"], "English")
