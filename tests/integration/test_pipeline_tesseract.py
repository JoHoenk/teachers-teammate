"""Integration tests for the full OCR pipeline using Tesseract.

These tests require the ``tesseract`` binary to be installed and on PATH.
They are skipped automatically if Tesseract is not available.

Run explicitly on CI with:
    pytest tests/integration/test_pipeline_tesseract.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import make_config, skip_no_tesseract


@pytest.mark.needs_tesseract
@skip_no_tesseract
def test_pipeline_run_returns_zero_exit_code(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a Config with ocr_engine="tesseract", correction disabled, and one PNG in the input dir
    When   OCRPipeline.run() is called
    Then   the exit code is 0 (success)
    """
    from teachers_teammate.infrastructure.pipeline import OCRPipeline  # noqa: PLC0415

    in_dir = tmp_path / "input"
    in_dir.mkdir()
    import shutil  # noqa: PLC0415

    shutil.copy(sample_png, in_dir / "page.png")

    cfg = make_config(
        tmp_path,
        input_dir=in_dir,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
    )
    pipeline = OCRPipeline(cfg)
    exit_code = pipeline.run()
    assert exit_code == 0


@pytest.mark.needs_tesseract
@skip_no_tesseract
def test_pipeline_on_file_done_callback_is_invoked(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  an OCRPipeline with an on_file_done callback and one PNG in the input dir
    When   run() completes
    Then   the callback is invoked exactly once with a string raw OCR result
    """
    from teachers_teammate.infrastructure.pipeline import OCRPipeline  # noqa: PLC0415

    in_dir = tmp_path / "input"
    in_dir.mkdir()
    import shutil  # noqa: PLC0415

    shutil.copy(sample_png, in_dir / "page.png")

    results: list[tuple] = []

    def _on_done(
        _source_id: str,
        path: str,
        success: bool,
        _msg: str,
        _preview_img: object,
        raw: str,
        corrected: str,
        _evaluated: str,
    ) -> None:
        results.append((path, success, raw, corrected))

    cfg = make_config(
        tmp_path,
        input_dir=in_dir,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
    )
    pipeline = OCRPipeline(cfg, on_file_done=_on_done)
    pipeline.run()

    assert len(results) == 1
    _path, _success, raw, _corrected = results[0]
    assert isinstance(raw, str)


@pytest.mark.needs_tesseract
@skip_no_tesseract
def test_pipeline_on_file_started_callback_is_invoked(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  an OCRPipeline with an on_file_started callback and one PNG in the input dir
    When   run() completes
    Then   the callback is invoked exactly once
    """
    from teachers_teammate.infrastructure.pipeline import OCRPipeline  # noqa: PLC0415

    in_dir = tmp_path / "input"
    in_dir.mkdir()
    import shutil  # noqa: PLC0415

    shutil.copy(sample_png, in_dir / "page.png")

    started: list[str] = []

    def _on_started(_source_id: str, path: str, _idx: int, _total: int) -> None:
        started.append(path)

    cfg = make_config(
        tmp_path,
        input_dir=in_dir,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
    )
    pipeline = OCRPipeline(cfg, on_file_started=_on_started)
    pipeline.run()

    assert len(started) == 1


@pytest.mark.needs_tesseract
@skip_no_tesseract
def test_pipeline_stop_event_halts_processing(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  an OCRPipeline with 5 PNG files and a stop_event that is set after the first file starts
    When   run() is called
    Then   fewer than 5 files are processed (the stop event interrupts the loop)
    """
    import shutil  # noqa: PLC0415
    import threading  # noqa: PLC0415

    from teachers_teammate.infrastructure.pipeline import OCRPipeline  # noqa: PLC0415

    in_dir = tmp_path / "input"
    in_dir.mkdir()
    # Create several files so there is something to cancel
    for i in range(5):
        shutil.copy(sample_png, in_dir / f"page{i}.png")

    stop = threading.Event()
    results: list = []

    def _on_started(_source_id: str, path: str, _idx: int, _total: int) -> None:
        results.append(path)
        stop.set()  # signal stop after the first file starts

    cfg = make_config(
        tmp_path,
        input_dir=in_dir,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
    )
    pipeline = OCRPipeline(cfg, stop_event=stop, on_file_started=_on_started)
    pipeline.run()

    # Only the first file (or very few) should have been processed
    assert len(results) < 5
