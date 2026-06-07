"""End-to-end scenario tests for the OCR pipeline.

These exercise stages that the other integration modules don't cover:
correction → DOCX (both formats), automatic evaluation, anonymization, the
resume/freshness cache path, and multi-page PDF OCR. LLM-backed stages are
mocked at the stage-builder import sites; the DOCX builder runs for real.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from docx import Document

from teachers_teammate.exceptions import OCRError
from teachers_teammate.infrastructure.pipeline import OCRPipeline
from teachers_teammate.infrastructure.state_repository import StateRepository
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from tests.conftest import make_config, skip_no_tesseract


def _state_for(cfg, source: Path):
    return StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(source)


def _patch_correction(corrector: MagicMock):
    """Patch the correction stage so build_llm + LangChainCorrector return doubles."""
    return (
        patch("teachers_teammate.infrastructure.stage_builder.build_llm", return_value=MagicMock()),
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainCorrector",
            return_value=corrector,
        ),
    )


# ── correction → DOCX (both formats) ─────────────────────────────────────────


@pytest.mark.use_case("DOCX_Report_Export")
@pytest.mark.parametrize("fmt", ["table", "comments"])
def test_correction_produces_openable_docx(tmp_path: Path, fmt: str) -> None:
    """
    Given  correction enabled and docx_enabled with the given format
    When   OCRPipeline.run() processes a TXT file
    Then   a .docx is written for that file and is openable by python-docx
    """
    cfg = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        docx_enabled=True,
        docx_format=fmt,
    )
    source = cfg.input_dir / "essay.txt"
    source.write_text("teh quik broWn fox", encoding="utf-8")

    corrector = MagicMock()
    corrector.correct.return_value = ("the quick brown fox", None)
    build_llm_patch, corrector_patch = _patch_correction(corrector)

    with build_llm_patch, corrector_patch:
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    docx_file = cfg.output_dir / "essay.docx"
    assert docx_file.is_file()
    # Opening confirms it is a structurally valid .docx, not just bytes on disk.
    assert Document(str(docx_file)) is not None


# ── automatic evaluation ─────────────────────────────────────────────────────


@pytest.mark.use_case("Automatic_Content_Evaluation")
def test_evaluation_stage_runs_and_persists(tmp_path: Path) -> None:
    """
    Given  correction and evaluation enabled for a batch run
    When   OCRPipeline.run() processes a TXT file
    Then   the evaluator is invoked with the corrected text and the result is cached
    """
    cfg = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        evaluation_enabled=True,
        evaluate_provider="openai",
        docx_enabled=False,
    )
    source = cfg.input_dir / "graded.txt"
    source.write_text("student answer", encoding="utf-8")

    corrector = MagicMock()
    corrector.correct.return_value = ("corrected answer", None)
    evaluator = MagicMock()
    evaluator.evaluate.return_value = ("Grade: A. Well done.", None)
    build_llm_patch, corrector_patch = _patch_correction(corrector)

    with (
        build_llm_patch,
        corrector_patch,
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainEvaluator",
            return_value=evaluator,
        ),
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    assert evaluator.evaluate.call_args.args[0] == "corrected answer"
    state = _state_for(cfg, source)
    assert state is not None
    assert state.evaluation_done is True
    assert state.evaluation_text == "Grade: A. Well done."


# ── anonymization ────────────────────────────────────────────────────────────


@pytest.mark.use_case("PII_Anonymization_Before_Correction")
def test_anonymizer_runs_before_correction_and_restores(tmp_path: Path) -> None:
    """
    Given  correction + anonymization enabled
    When   OCRPipeline.run() processes a TXT file
    Then   the text is anonymized before correction and the PII is restored afterwards
    """
    cfg = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        anonymization_enabled=True,
        docx_enabled=False,
    )
    source = cfg.input_dir / "letter.txt"
    source.write_text("Dear Alice", encoding="utf-8")

    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("Dear <NAME_0>", {"<NAME_0>": "Alice"})
    anonymizer.restore.return_value = "Dear Alice (corrected)"
    corrector = MagicMock()
    corrector.correct.return_value = ("Dear <NAME_0> (corrected)", None)
    build_llm_patch, corrector_patch = _patch_correction(corrector)

    with (
        build_llm_patch,
        corrector_patch,
        patch(
            "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer",
            return_value=anonymizer,
        ),
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    # Correction must see the anonymized text, not the raw PII.
    assert corrector.correct.call_args.args[0] == "Dear <NAME_0>"
    anonymizer.restore.assert_called_once()
    state = _state_for(cfg, source)
    assert state is not None
    assert state.correction_text == "Dear Alice (corrected)"


# ── resume / freshness ───────────────────────────────────────────────────────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_second_run_reuses_cached_correction(tmp_path: Path) -> None:
    """
    Given  a file already corrected and cached by a first run
    When   OCRPipeline.run() runs again with identical config
    Then   the correction LLM is not invoked again (cached output is reused)
    """
    cfg = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        docx_enabled=False,
    )
    source = cfg.input_dir / "doc.txt"
    source.write_text("original text", encoding="utf-8")

    corrector = MagicMock()
    corrector.correct.return_value = ("corrected text", None)
    build_llm_patch, corrector_patch = _patch_correction(corrector)

    with build_llm_patch, corrector_patch:
        assert OCRPipeline(cfg).run() == 0
        assert corrector.correct.call_count == 1
        # Second run with unchanged config should hit the cache.
        assert OCRPipeline(cfg).run() == 0
        assert corrector.correct.call_count == 1


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_changed_correction_config_recomputes(tmp_path: Path) -> None:
    """
    Given  a file corrected and cached under one correction prompt
    When   OCRPipeline.run() runs again with a different correction prompt
    Then   the correction stage is re-executed (cache is stale)
    """
    source_text = "original text"
    corrector = MagicMock()
    corrector.correct.return_value = ("corrected text", None)

    cfg1 = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        correction_prompt="Fix spelling.",
        docx_enabled=False,
    )
    source = cfg1.input_dir / "doc.txt"
    source.write_text(source_text, encoding="utf-8")

    build_llm_patch, corrector_patch = _patch_correction(corrector)
    with build_llm_patch, corrector_patch:
        assert OCRPipeline(cfg1).run() == 0
    assert corrector.correct.call_count == 1

    # Same input/output dirs, different correction prompt → stale correction stage.
    cfg2 = make_config(
        tmp_path,
        input_dir=cfg1.input_dir,
        output_dir=cfg1.output_dir,
        correction_enabled=True,
        correction_provider="openai",
        correction_prompt="Rewrite formally.",
        docx_enabled=False,
    )
    build_llm_patch2, corrector_patch2 = _patch_correction(corrector)
    with build_llm_patch2, corrector_patch2:
        assert OCRPipeline(cfg2).run() == 0
    assert corrector.correct.call_count == 2


# ── multi-page PDF OCR (real Tesseract) ──────────────────────────────────────


@pytest.mark.needs_tesseract
@skip_no_tesseract
@pytest.mark.use_case("Batch_OCR_Processing")
def test_multipage_pdf_ocr_concatenates_pages(tmp_path: Path) -> None:
    """
    Given  a two-page PDF with legible text on each page
    When   OCRPipeline.run() processes it with the Tesseract engine
    Then   it succeeds and the cached raw text is labelled per page
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    in_dir = tmp_path / "input"
    in_dir.mkdir()
    pages = []
    for words in ("Hello World", "Second Page"):
        img = Image.new("RGB", (600, 200), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=48)
        draw.text((40, 80), words, fill=(0, 0, 0), font=font)
        pages.append(img)
    pdf_path = in_dir / "scan.pdf"
    pages[0].save(str(pdf_path), "PDF", save_all=True, append_images=pages[1:])

    cfg = make_config(
        tmp_path,
        input_dir=in_dir,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
    )
    rc = OCRPipeline(cfg).run()

    assert rc == 0
    state = _state_for(cfg, pdf_path)
    assert state is not None
    assert "Page 1:" in state.raw_text
    assert "Page 2:" in state.raw_text


# ── corrupt inputs ────────────────────────────────────────────────────────────


@pytest.mark.use_case("Batch_OCR_Processing")
def test_pipeline_reports_nonzero_exit_when_all_inputs_corrupt(tmp_path: Path) -> None:
    """
    Given  an input directory containing only a PNG file with corrupt/empty content
    When   OCRPipeline.run() is called with a mocked OCR engine that raises on every image
    Then   the pipeline returns a non-zero exit code
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    corrupt_png = cfg.input_dir / "corrupt.png"
    corrupt_png.write_bytes(b"not a valid png")

    mock_ocr = MagicMock()
    mock_ocr.process_image.side_effect = OCRError("corrupt image data")

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_ocr,
    ):
        rc = OCRPipeline(cfg).run()

    assert rc != 0
