"""Unit tests for teachers_teammate.infrastructure.stage_builder wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.infrastructure.image_preprocessor import HandwritingPreprocessor
from teachers_teammate.infrastructure.stage_builder import (
    PipelineComponentFactory,
    StageBuilder,
)
from tests.conftest import make_config


@pytest.mark.use_case("Batch_OCR_Processing")
def test_build_preprocessor_propagates_all_preprocessing_flags(tmp_path: Path) -> None:
    """
    Given  a Config whose OcrConfig enables a subset of pre-steps
    When   StageBuilder.build_preprocessor() constructs the preprocessor
    Then   each flag reaches the HandwritingPreprocessor (no positional mis-wiring)
    """
    ocr = OcrConfig(
        engine="tesseract",
        preprocess_method="clahe",
        dewarp=True,
        deskew=False,
        border_crop=True,
        denoise=False,
        gamma=True,
    )
    config = make_config(tmp_path, ocr=ocr)
    builder = StageBuilder(config=config, factory=PipelineComponentFactory())

    pre = builder.build_preprocessor(tmp_path)

    assert isinstance(pre, HandwritingPreprocessor)
    assert pre._method == "clahe"
    assert pre._dewarp is True
    assert pre._deskew is False
    assert pre._border_crop is True
    assert pre._denoise is False
    assert pre._gamma is True


@pytest.mark.use_case("Batch_OCR_Processing")
def test_build_preprocessor_service_uses_configured_pdf_dpi(tmp_path: Path) -> None:
    """
    Given  a Config with a non-default OCR pdf_render_dpi
    When   the preprocessor service builds a PDF input provider
    Then   that provider is constructed with the configured DPI
    """
    from teachers_teammate.infrastructure.input_providers import (  # noqa: PLC0415
        PdfInputProvider,
    )

    config = make_config(tmp_path, ocr=OcrConfig(engine="tesseract", pdf_render_dpi=150))
    builder = StageBuilder(config=config, factory=PipelineComponentFactory())

    service = builder.build_preprocessor_service(tmp_path)
    provider = service._provider_factory(".pdf", tmp_path)

    assert isinstance(provider, PdfInputProvider)
    assert provider._pdf_dpi == 150
