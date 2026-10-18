"""Unit tests for teachers_teammate.infrastructure.stage_builder wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.exceptions import OnlineOcrNotAllowedError
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


@pytest.mark.use_case("Batch_OCR_Processing")
def test_build_preprocessor_service_passes_configured_pdf_text_layer(
    tmp_path: Path,
) -> None:
    """
    Given  a Config with pdf_text_layer="always"
    When   the preprocessor service is built
    Then   the PDF provider uses the configured mode
    """
    config = make_config(tmp_path, ocr=OcrConfig(engine="tesseract", pdf_text_layer="always"))
    builder = StageBuilder(config=config, factory=PipelineComponentFactory())

    provider = builder.build_preprocessor_service(tmp_path)._provider_factory(".pdf", tmp_path)

    assert provider._text_layer == "always"


def test_build_ocr_postprocessor_uses_factory_callable(tmp_path: Path) -> None:
    """
    Given  a PipelineComponentFactory whose build_ocr_postprocessor is overridden
    When   StageBuilder.build_ocr_postprocessor() is called
    Then   the injected instance is returned (so adapters/tests can swap the notation stage)
    """
    import dataclasses  # noqa: PLC0415

    sentinel = object()
    factory = dataclasses.replace(
        PipelineComponentFactory(), build_ocr_postprocessor=lambda: sentinel
    )
    builder = StageBuilder(make_config(tmp_path), factory)

    assert builder.build_ocr_postprocessor() is sentinel


def test_default_ocr_postprocessor_understands_layout_html(tmp_path: Path) -> None:
    """
    Given  the default factory
    When   its postprocessor processes Chandra-style HTML
    Then   Markdown is produced
    """
    builder = StageBuilder(make_config(tmp_path), PipelineComponentFactory())

    result = builder.build_ocr_postprocessor().process_pages(
        ['<div data-label="Text"><p><b>x</b></p></div>']
    )

    assert (result.text, result.text_format) == ("**x**", "markdown")


@pytest.mark.parametrize("engine", ["langchain", "openai"])
def test_build_ocr_refuses_online_engines(tmp_path: Path, engine: str) -> None:
    """
    Given  a Config whose OCR engine is an online vision model
    When   StageBuilder.build_ocr() is called
    Then   it raises OnlineOcrNotAllowedError before any LLM is built
    """
    build_llm = MagicMock()
    factory = PipelineComponentFactory(build_llm=build_llm)
    config = make_config(tmp_path, ocr=OcrConfig(engine=engine, model="gpt-4o"))
    builder = StageBuilder(config=config, factory=factory)

    with pytest.raises(OnlineOcrNotAllowedError, match="whole page"):
        builder.build_ocr()

    build_llm.assert_not_called()


@pytest.mark.parametrize(
    ("model", "url"),
    [
        ("qwen3-vl:235b-cloud", "http://127.0.0.1:11434"),
        ("deepseek-ocr:latest", "https://ollama.com"),
    ],
)
def test_build_ocr_refuses_ollama_cloud(tmp_path: Path, model: str, url: str) -> None:
    """
    Given  Ollama OCR with a cloud model, or pointed at Ollama's hosted API
    When   StageBuilder.build_ocr() is called
    Then   it raises OnlineOcrNotAllowedError and builds no OCR processor
    """
    build_ollama_ocr = MagicMock()
    factory = PipelineComponentFactory(build_ollama_ocr=build_ollama_ocr)
    config = make_config(tmp_path, ocr=OcrConfig(engine="ollama", model=model), ollama_url=url)
    builder = StageBuilder(config=config, factory=factory)

    with pytest.raises(OnlineOcrNotAllowedError, match="Ollama's servers"):
        builder.build_ocr(MagicMock())

    build_ollama_ocr.assert_not_called()


def test_build_ocr_allows_downloaded_ollama_model_on_another_computer(tmp_path: Path) -> None:
    """
    Given  Ollama OCR with a downloaded model on a school server
    When   StageBuilder.build_ocr() is called
    Then   the Ollama OCR processor is built (a server you run is allowed)
    """
    build_ollama_ocr = MagicMock()
    factory = PipelineComponentFactory(build_ollama_ocr=build_ollama_ocr)
    config = make_config(
        tmp_path,
        ocr=OcrConfig(engine="ollama", model="deepseek-ocr:latest"),
        ollama_url="http://school-server:11434",
    )

    StageBuilder(config=config, factory=factory).build_ocr(MagicMock())

    build_ollama_ocr.assert_called_once()
