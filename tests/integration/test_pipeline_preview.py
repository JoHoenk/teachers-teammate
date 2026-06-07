"""Integration tests for teachers_teammate.infrastructure.pipeline.preprocess_preview.

No OCR engine, correction LLM, or external service is needed — only the
OpenCV / Pillow preprocessing stack is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.infrastructure.pipeline import OCRPipeline, preprocess_preview
from tests.conftest import make_config


@pytest.mark.use_case("Preview_Preprocessing")
@pytest.mark.parametrize("method", ["adaptive_threshold", "clahe", "grayscale", "none"])
def test_preprocess_preview_returns_existing_paths(
    tmp_path: Path, sample_png: Path, method: str
) -> None:
    """
    Given  a PNG image and a preprocessing method
    When   preprocess_preview() is called
    Then   both the original and the preprocessed image paths exist on disk
    """
    original, processed, _steps = preprocess_preview(sample_png, method, tmp_path)
    assert original.exists(), f"Original image path does not exist for method={method}"
    assert processed.exists(), f"Processed image path does not exist for method={method}"


@pytest.mark.use_case("Preview_Preprocessing")
def test_preprocess_preview_none_returns_same_path_for_original_and_processed(
    tmp_path: Path, sample_png: Path
) -> None:
    """
    Given  a PNG image and method="none"
    When   preprocess_preview() is called
    Then   original and processed paths are equal (no processing) and steps is empty
    """
    original, processed, steps = preprocess_preview(sample_png, "none", tmp_path)
    assert original == processed
    assert steps == []


@pytest.mark.use_case("Preview_Preprocessing")
@pytest.mark.parametrize("method", ["adaptive_threshold", "clahe", "grayscale"])
def test_preprocess_preview_processing_methods_return_different_path(
    tmp_path: Path, sample_png: Path, method: str
) -> None:
    """
    Given  a PNG image and an active preprocessing method (not "none")
    When   preprocess_preview() is called
    Then   the processed path differs from the original and at least one step is recorded
    """
    original, processed, steps = preprocess_preview(sample_png, method, tmp_path)
    assert original != processed
    assert len(steps) >= 1


@pytest.mark.use_case("Preview_Preprocessing")
def test_preprocess_preview_works_with_pdf_input(tmp_path: Path, sample_pdf: Path) -> None:
    """
    Given  a single-page PDF and method="grayscale"
    When   preprocess_preview() is called
    Then   both returned paths exist and the original is a PNG (first page rendered from PDF)
    """
    original, processed, _steps = preprocess_preview(sample_pdf, "grayscale", tmp_path)
    assert original.exists()
    assert processed.exists()
    # PDF first page is extracted, so original is a rendered PNG (not the PDF itself)
    assert original.suffix.lower() == ".png"


@pytest.mark.use_case("Preview_Preprocessing")
def test_pipeline_run_preview_only_propagates_failure_for_corrupt_image(tmp_path: Path) -> None:
    """
    Given  a PNG file containing bytes that are not a valid image
    When   run_preview_only() is called
    Then   returns 1 (the file is counted as failed, not silently skipped)
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="grayscale",
    )
    corrupt_png = cfg.input_dir / "corrupt.png"
    corrupt_png.write_bytes(b"this is not a valid image file")

    rc = OCRPipeline(cfg).run_preview_only()

    assert rc == 1
