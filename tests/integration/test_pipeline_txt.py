"""Integration tests for text-file input through :class:`teachers_teammate.infrastructure.pipeline.OCRPipeline`.

These tests exercise the real pipeline with the text ingestion lane and
validate that OCR is bypassed for plain-text inputs.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.domain.proofreading.result import CorrectionOutcome
from teachers_teammate.infrastructure.file_discovery import FileDiscovery
from teachers_teammate.interfaces import ProgressCallbacks
from teachers_teammate.infrastructure.pipeline import OCRPipeline
from teachers_teammate.domain.run_summary import FileOutcome, FileStatus
from teachers_teammate.infrastructure.stage_builder import StageBuilder
from teachers_teammate.infrastructure.state_repository import StateRepository
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from tests.conftest import make_config, pipeline_dependencies


@pytest.mark.use_case("TXT_Ingestion_Without_OCR")
def test_pipeline_run_txt_input_writes_raw_text_without_ocr(tmp_path: Path) -> None:
    """
    Given  a Config with one UTF-8 .txt file and correction disabled
    When   OCRPipeline.run() is called
    Then   the exit code is 0 and JSON cache state contains the original text content
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=False,
        docx_enabled=False,
    )
    source = cfg.input_dir / "note.txt"
    source.write_text("First line\nSecond line", encoding="utf-8")

    rc = (
        OCRPipeline(
            cfg,
            FileDiscovery().collect_input_files(cfg),
            dependencies=pipeline_dependencies(tmp_path),
        )
        .run()
        .exit_code
    )

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
        ocr_engine="ppocr",
        correction_enabled=False,
        docx_enabled=False,
    )
    source = cfg.input_dir / "callback_note.txt"
    source.write_text("Callback payload", encoding="utf-8")

    seen: list[FileOutcome] = []

    def _on_done(outcome: FileOutcome) -> None:
        seen.append(outcome)

    rc = (
        OCRPipeline(
            cfg,
            FileDiscovery().collect_input_files(cfg),
            progress=ProgressCallbacks(on_file_done=_on_done),
            dependencies=pipeline_dependencies(tmp_path),
        )
        .run()
        .exit_code
    )

    assert rc == 0
    assert len(seen) == 1
    assert seen[0].status is FileStatus.SAVED
    assert seen[0].name == "callback_note.txt"


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
        ocr_engine="ppocr",
        correction_enabled=True,
        docx_enabled=False,
        correction_provider="openai",
    )
    source = cfg.input_dir / "document.txt"
    source.write_text("Text to be corrected", encoding="utf-8")

    mock_corrector = MagicMock()
    mock_corrector.pieces.return_value = None
    mock_corrector.correct.return_value = CorrectionOutcome("corrected text")

    with (
        patch("teachers_teammate.infrastructure.stage_builder.build_llm", return_value=MagicMock()),
        patch.object(StageBuilder, "build_corrector", return_value=mock_corrector),
    ):
        rc = (
            OCRPipeline(
                cfg,
                FileDiscovery().collect_input_files(cfg),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
        )

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
        ocr_engine="ppocr",
        correction_enabled=False,
        docx_enabled=False,
    )
    source = cfg.input_dir / "latin1.txt"
    source.write_bytes(b"\xff\xfe latin-1 text \x80\x81")

    seen: list[FileStatus] = []

    def _on_done(outcome: FileOutcome) -> None:
        seen.append(outcome.status)

    rc = (
        OCRPipeline(
            cfg,
            FileDiscovery().collect_input_files(cfg),
            progress=ProgressCallbacks(on_file_done=_on_done),
            dependencies=pipeline_dependencies(tmp_path),
        )
        .run()
        .exit_code
    )

    assert rc == 0
    assert seen == [FileStatus.SAVED]


def test_pipeline_saves_text_files_below_the_input_subfolder(tmp_path: Path) -> None:
    """
    Given  a recursive run with text files switched on and two inputs of the same name in
           different subfolders
    When   OCRPipeline.run() is called
    Then   each text file is saved in its own subfolder of the output folder, so neither
           replaces the other
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=False,
        docx_enabled=False,
        text_enabled=True,
        recursive=True,
    )
    for name in ("class1", "class2"):
        (cfg.input_dir / name).mkdir()
        (cfg.input_dir / name / "essay.txt").write_text(f"text of {name}", encoding="utf-8")

    rc = (
        OCRPipeline(
            cfg,
            FileDiscovery().collect_input_files(cfg),
            dependencies=pipeline_dependencies(tmp_path),
        )
        .run()
        .exit_code
    )

    assert rc == 0
    for name in ("class1", "class2"):
        assert (cfg.output_dir / name / "essay.txt").read_text(encoding="utf-8") == (
            f"text of {name}"
        )


def test_pipeline_refuses_text_files_in_the_input_folder_before_creating_it(
    tmp_path: Path,
) -> None:
    """
    Given  a recursive run with text files whose not yet existing output folder lies in
           the input folder
    When   OCRPipeline.run() is called
    Then   it fails with 1, reports why, and creates neither the folder nor any file
    """
    cfg = make_config(tmp_path, correction_enabled=False, text_enabled=True, recursive=True)
    cfg = dataclasses.replace(cfg, output_dir=cfg.input_dir / "results")
    (cfg.input_dir / "note.txt").write_text("hello", encoding="utf-8")
    errors: list[str] = []
    reporter = MagicMock()
    reporter.error.side_effect = errors.append

    rc = (
        OCRPipeline(
            cfg,
            FileDiscovery().collect_input_files(cfg),
            dependencies=pipeline_dependencies(tmp_path, reporter=reporter),
        )
        .run()
        .exit_code
    )

    assert rc == 1
    assert "next run would read them as new documents" in errors[0]
    assert not cfg.output_dir.exists()


@pytest.mark.use_case("TXT_Ingestion_Without_OCR")
def test_pipeline_with_recognition_off_uses_text_files_and_skips_scans(
    tmp_path: Path, sample_png: Path
) -> None:
    """
    Given  recognition switched off and an input folder with a text file and an image
    When   OCRPipeline.run() is called
    Then   the text file is processed, the image is reported as skipped (not as failed),
           no OCR engine ran, and the exit code is 0
    """
    import shutil  # noqa: PLC0415

    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        recognition_enabled=False,
        correction_enabled=False,
        docx_enabled=False,
    )
    (cfg.input_dir / "note.txt").write_text("Typed essay", encoding="utf-8")
    shutil.copy(sample_png, cfg.input_dir / "scan.png")
    events: list[FileOutcome] = []
    ocr = MagicMock()

    with patch("teachers_teammate.infrastructure.pipeline.OCRStageService") as stage:
        stage.return_value.read_pages = ocr
        rc = (
            OCRPipeline(
                cfg,
                FileDiscovery().collect_input_files(cfg),
                progress=ProgressCallbacks(on_file_done=events.append),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
        )

    assert rc == 0
    by_name = {e.name: e for e in events}
    assert by_name["note.txt"].status is FileStatus.SAVED
    assert by_name["scan.png"].status is FileStatus.SKIPPED
    assert "switched off" in by_name["scan.png"].error
    ocr.assert_not_called()
