"""Integration tests for teachers_teammate.infrastructure.pipeline.preprocess_preview.

No OCR engine, correction LLM, or external service is needed — only the
OpenCV / Pillow preprocessing stack is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.pipeline import OCRPipeline, preprocess_preview
from tests.conftest import make_config


@pytest.mark.use_case("Preview_Preprocessing")
@pytest.mark.parametrize("method", ["auto", "none"])
def test_preprocess_preview_returns_existing_paths(
    tmp_path: Path, sample_png: Path, method: str
) -> None:
    """
    Given  a PNG image and a preprocessing method
    When   preprocess_preview() is called
    Then   both the original and the preprocessed image paths exist on disk
    """
    original, processed, _steps = preprocess_preview(
        sample_png, OcrConfig(preprocess_method=method), tmp_path
    )
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
    original, processed, steps = preprocess_preview(
        sample_png, OcrConfig(preprocess_method="none"), tmp_path
    )
    assert original == processed
    assert steps == []


@pytest.mark.use_case("Preview_Preprocessing")
def test_preprocess_preview_of_the_automatic_preparation_writes_a_new_image(
    tmp_path: Path, sample_png: Path
) -> None:
    """
    Given  a PNG image and the automatic preparation for Ollama (which reads gray pages)
    When   preprocess_preview() is called
    Then   the processed path differs from the original and the gray conversion is recorded
    """
    original, processed, steps = preprocess_preview(
        sample_png, OcrConfig(engine="ollama", preprocess_method="auto"), tmp_path
    )
    assert original != processed
    assert steps == ["grayscale"]


@pytest.mark.use_case("Preview_Preprocessing")
def test_preprocess_preview_works_with_pdf_input(tmp_path: Path, sample_pdf: Path) -> None:
    """
    Given  a single-page PDF and the automatic preparation
    When   preprocess_preview() is called
    Then   both returned paths exist and the original is a PNG (first page rendered from PDF)
    """
    original, processed, _steps = preprocess_preview(
        sample_pdf, OcrConfig(preprocess_method="auto"), tmp_path
    )
    assert original.exists()
    assert processed.exists()
    # PDF first page is extracted, so original is a rendered PNG (not the PDF itself)
    assert original.suffix.lower() == ".png"


@pytest.mark.use_case("Preview_Preprocessing")
def test_preprocess_preview_applies_the_steps_a_page_needs(tmp_path: Path) -> None:
    """
    Given  a page lit unevenly (brightness 40 % at the left edge)
    When   preprocess_preview() is called with the automatic preparation
    Then   the reported steps include the light step (parity with a real run)
    """
    import numpy as np  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    page = Image.new("RGB", (400, 120), (255, 255, 255))
    gradient = np.linspace(0.4, 1.0, 400)[None, :, None]
    source = tmp_path / "shadow.png"
    Image.fromarray((np.asarray(page, dtype=np.float32) * gradient).astype(np.uint8)).save(source)

    _original, _processed, steps = preprocess_preview(
        source, OcrConfig(preprocess_method="auto"), tmp_path
    )

    assert "light" in steps


@pytest.mark.use_case("Preview_Preprocessing")
def test_preprocess_preview_uses_the_given_build_preprocessor(
    tmp_path: Path, sample_png: Path
) -> None:
    """
    Given  a build_preprocessor callable that differs from the default constructor
    When   preprocess_preview() is called with it
    Then   it is used (called with the same tmp_dir/save_steps/ocr) instead of
           stage_builder.default_build_preprocessor

    Regression guard: a caller whose OCRPipeline was built with a non-default
    PipelineComponentFactory expects the preview to match what that pipeline would
    actually run; calling the default constructor directly would silently diverge.
    """
    calls: list[tuple[Path, bool, OcrConfig, ModelStore | None]] = []
    ocr = OcrConfig(preprocess_method="auto")

    class _StubPreprocessor:
        def preprocess(self, image_path: Path) -> tuple[Path, list[str]]:
            return image_path, ["stub_step"]

    store = ModelStore(tmp_path / "models")

    def _stub_build_preprocessor(
        tmp_dir: Path, save_steps: bool, ocr_arg: OcrConfig, store_arg: ModelStore | None
    ):
        calls.append((tmp_dir, save_steps, ocr_arg, store_arg))
        return _StubPreprocessor()

    _original, _processed, steps = preprocess_preview(
        sample_png, ocr, tmp_path, _stub_build_preprocessor, model_store=store
    )
    assert steps == ["stub_step"]
    assert calls == [(tmp_path, False, ocr, store)]


@pytest.mark.use_case("Preview_Preprocessing")
def test_preprocess_preview_uses_configured_pdf_dpi(tmp_path: Path, sample_pdf: Path) -> None:
    """
    Given  an OcrConfig with a non-default pdf_render_dpi
    When   preprocess_preview() is called on a PDF
    Then   the rendered original image reflects the configured DPI (larger than the default)
    """
    from PIL import Image  # noqa: PLC0415

    low_dir = tmp_path / "low"
    high_dir = tmp_path / "high"
    low_dir.mkdir()
    high_dir.mkdir()
    default_original, _p, _s = preprocess_preview(
        sample_pdf, OcrConfig(preprocess_method="none", pdf_render_dpi=100), low_dir
    )
    high_original, _p2, _s2 = preprocess_preview(
        sample_pdf, OcrConfig(preprocess_method="none", pdf_render_dpi=300), high_dir
    )
    with Image.open(default_original) as low_img, Image.open(high_original) as high_img:
        assert high_img.size[0] > low_img.size[0]


@pytest.mark.use_case("Preview_Preprocessing")
def test_pipeline_run_preview_only_propagates_failure_for_corrupt_image(tmp_path: Path) -> None:
    """
    Given  a PNG file containing bytes that are not a valid image
    When   run_preview_only() is called
    Then   returns 1 (the file is counted as failed, not silently skipped)
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="grayscale",
    )
    corrupt_png = cfg.input_dir / "corrupt.png"
    corrupt_png.write_bytes(b"this is not a valid image file")

    rc = OCRPipeline(cfg).run_preview_only()

    assert rc == 1
