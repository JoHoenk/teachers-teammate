"""Unit tests for teachers_teammate.infrastructure.workflow.ocr_service."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from teachers_teammate.exceptions import OCRError
from teachers_teammate.infrastructure.workflow.ocr_service import OCRStageService


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
    Then   result is ([], None) and processor is not called
    """
    svc, processor = _make_svc()
    texts, err = svc.run_pages([], "English")

    assert texts == []
    assert err is None
    processor.process_image.assert_not_called()


# ── Single page ────────────────────────────────────────────────────────────


def test_run_pages_single_page_calls_processor_once(tmp_path: Path) -> None:
    """
    Given  a single image path
    When   run_pages is called
    Then   processor.process_image is called once and the text is returned
    """
    img = tmp_path / "page.png"
    svc, processor = _make_svc(process_return="hello world")

    texts, err = svc.run_pages([img], "English")

    assert texts == ["hello world"]
    assert err is None
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

    texts, err = svc.run_pages([img], "English")

    assert texts == ["Hello world"]
    assert err is None


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

    texts, err = svc.run_pages([p1, p2], "German")

    assert texts == ["page one", "page two"]
    assert err is None
    assert processor.process_image.call_count == 2


# ── OCRError ───────────────────────────────────────────────────────────────


def test_run_pages_ocr_error_single_page_returns_image_label(tmp_path: Path) -> None:
    """
    Given  process_image raises OCRError on the only page
    When   run_pages is called
    Then   result is ([], 'OCR failed (image): ...') with 'image' label
    """
    img = tmp_path / "doc.png"
    svc, _ = _make_svc(process_side_effect=OCRError("timeout"))

    texts, err = svc.run_pages([img], "English")

    assert texts == []
    assert err is not None
    assert "OCR failed (image)" in err
    assert "timeout" in err


def test_run_pages_ocr_error_multi_page_returns_page_label(tmp_path: Path) -> None:
    """
    Given  two pages and process_image raises OCRError on the second page
    When   run_pages is called
    Then   result contains 'page 2' in the error label
    """
    p1 = tmp_path / "p1.png"
    p2 = tmp_path / "p2.png"
    processor = MagicMock()
    processor.process_image.side_effect = ["first ok", OCRError("read error")]
    svc = OCRStageService(processor=processor, stop_event=None)

    texts, err = svc.run_pages([p1, p2], "English")

    assert texts == []
    assert err is not None
    assert "page 2" in err


# ── Stop event ─────────────────────────────────────────────────────────────


def test_run_pages_stop_event_set_before_loop_returns_stopped(tmp_path: Path) -> None:
    """
    Given  stop_event is already set before run_pages begins iterating
    When   run_pages is called with one image
    Then   result is ([], 'Stopped by user.') and processor is not called
    """
    img = tmp_path / "doc.png"
    stop = threading.Event()
    stop.set()
    svc, processor = _make_svc(stop_event=stop)

    texts, err = svc.run_pages([img], "English")

    assert texts == []
    assert err == "Stopped by user."
    processor.process_image.assert_not_called()


def test_run_pages_stop_event_mid_iteration(tmp_path: Path) -> None:
    """
    Given  two pages and stop_event is set after the first page is processed
    When   run_pages is called
    Then   the second page is not processed and ([], 'Stopped by user.') is returned
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

    texts, err = svc.run_pages([p1, p2], "English")

    assert err == "Stopped by user."
    assert call_count == 1
