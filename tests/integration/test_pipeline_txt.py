"""Integration tests for text-file input through :class:`teachers_teammate.infrastructure.pipeline.OCRPipeline`.

These tests exercise the real pipeline with the text ingestion lane and
validate that OCR is bypassed for plain-text inputs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.infrastructure.pipeline import OCRPipeline
from teachers_teammate.infrastructure.state_repository import StateRepository
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from tests.conftest import make_config


@pytest.mark.use_case("TXT_Ingestion_Without_OCR")
def test_pipeline_run_txt_input_writes_raw_text_without_ocr(tmp_path: Path) -> None:
    """
    Given  a Config with one UTF-8 .txt file and correction disabled
    When   OCRPipeline.run() is called
    Then   the exit code is 0 and JSON cache state contains the original text content
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
    )
    source = cfg.input_dir / "note.txt"
    source.write_text("First line\nSecond line", encoding="utf-8")

    rc = OCRPipeline(cfg).run()

    assert rc == 0
    state = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(source)
    assert state is not None
    assert state.raw_text == "First line\nSecond line"


@pytest.mark.use_case("TXT_Ingestion_Without_OCR")
def test_pipeline_txt_input_triggers_on_file_done_callback(tmp_path: Path) -> None:
    """
    Given  an OCRPipeline with one .txt file and an on_file_done callback
    When   run() completes
    Then   the callback reports success and includes the raw text payload
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
    )
    source = cfg.input_dir / "callback_note.txt"
    source.write_text("Callback payload", encoding="utf-8")

    seen: list[tuple[bool, str]] = []

    def _on_done(
        _source_id: str,
        _name: str,
        success: bool,
        _msg: str,
        _preview: str,
        raw_txt_path: str,
        _corr_txt_path: str,
        _eval_txt_path: str,
    ) -> None:
        seen.append((success, raw_txt_path))

    rc = OCRPipeline(cfg, on_file_done=_on_done).run()

    assert rc == 0
    assert len(seen) == 1
    assert seen[0][0] is True
    assert seen[0][1] == "Callback payload"


@pytest.mark.use_case("TXT_Ingestion_Without_OCR")
@pytest.mark.use_case("AI_Proofreading_Correction")
def test_pipeline_txt_input_enters_correction_pipeline(tmp_path: Path) -> None:
    """
    Given  correction_enabled=True and a UTF-8 TXT file
    When   OCRPipeline.run() is called
    Then   the correction LLM is invoked with the TXT content and correction_text is stored in state
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=True,
        docx_enabled=False,
        correction_provider="openai",
    )
    source = cfg.input_dir / "document.txt"
    source.write_text("Text to be corrected", encoding="utf-8")

    mock_corrector = MagicMock()
    mock_corrector.correct.return_value = ("corrected text", None)

    with (
        patch("teachers_teammate.infrastructure.stage_builder.build_llm", return_value=MagicMock()),
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainCorrector",
            return_value=mock_corrector,
        ),
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    call_args = mock_corrector.correct.call_args
    assert call_args is not None
    assert call_args.args[0] == "Text to be corrected"
    state = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(source)
    assert state is not None
    assert state.correction_done is True
    assert state.correction_text == "corrected text"


@pytest.mark.use_case("TXT_Ingestion_Without_OCR")
def test_pipeline_txt_input_non_utf8_falls_back_to_latin1(tmp_path: Path) -> None:
    """
    Given  a TXT file whose bytes are not valid UTF-8 but are valid latin-1
    When   OCRPipeline.run() is called
    Then   returns 0 — TextInputProvider falls back to latin-1 and reads the file
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
    )
    source = cfg.input_dir / "latin1.txt"
    source.write_bytes(b"\xff\xfe latin-1 text \x80\x81")

    seen: list[bool] = []

    def _on_done(
        _source_id: str,
        _name: str,
        success: bool,
        _msg: str,
        _preview: str,
        _raw: str,
        _corr: str,
        _eval: str,
    ) -> None:
        seen.append(success)

    rc = OCRPipeline(cfg, on_file_done=_on_done).run()

    assert rc == 0
    assert seen == [True]
