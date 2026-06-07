"""Unit tests for the benchmark OCR-only runner."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from teachers_teammate.exceptions import OCRError
from teachers_teammate.infrastructure.benchmark.runner import run_ocr
from teachers_teammate.infrastructure.stage_builder import PipelineComponentFactory
from teachers_teammate.interfaces import OCRProcessor

from ..conftest import make_config


class _StubOCR(OCRProcessor):
    def __init__(self, text: str = "", *, raises: bool = False) -> None:
        self._text = text
        self._raises = raises

    def process_image(self, image_path: Path, language: str = "English") -> str:
        if self._raises:
            raise OCRError("engine exploded")
        return self._text


def _factory_with(stub: _StubOCR) -> PipelineComponentFactory:
    return dataclasses.replace(PipelineComponentFactory(), build_tesseract_ocr=lambda: stub)


@pytest.fixture(autouse=True)
def _hermetic_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the runner's scratch artifacts inside the test's tmp dir."""
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path / "storage"))


@pytest.mark.use_case("OCR_Benchmark_Run")
def test_run_ocr_returns_extracted_text(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  an image document and a stub OCR engine
    When   run_ocr() is called with a tesseract config
    Then   the extracted text and a preview image path are returned, no error
    """
    config = make_config(tmp_path, ocr_engine="tesseract", preprocess_method="none")
    result = run_ocr(config, sample_png, component_factory=_factory_with(_StubOCR("recognised")))

    assert result.error is None
    assert result.raw_text == "recognised"
    assert result.preview_img is not None


def test_run_ocr_reports_engine_error_without_raising(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a stub OCR engine that raises OCRError
    When   run_ocr() is called
    Then   the failure is returned on the result rather than propagated
    """
    config = make_config(tmp_path, ocr_engine="tesseract", preprocess_method="none")
    result = run_ocr(config, sample_png, component_factory=_factory_with(_StubOCR(raises=True)))

    assert result.raw_text == ""
    assert result.error is not None
    assert "engine exploded" in result.error


def test_run_ocr_uses_text_input_directly(tmp_path: Path) -> None:
    """
    Given  a TXT document (no image)
    When   run_ocr() is called
    Then   the text content is returned directly, bypassing OCR
    """
    txt = tmp_path / "note.txt"
    txt.write_text("plain text content", encoding="utf-8")
    config = make_config(tmp_path, ocr_engine="tesseract", preprocess_method="none")

    result = run_ocr(config, txt, component_factory=_factory_with(_StubOCR("UNUSED")))
    assert result.error is None
    assert result.raw_text == "plain text content"
