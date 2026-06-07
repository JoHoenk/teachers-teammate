"""Unit tests for teachers_teammate.infrastructure.image_preprocessor."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.infrastructure.image_preprocessor import HandwritingPreprocessor


@pytest.mark.parametrize("method", ["adaptive_threshold", "clahe", "grayscale", "none"])
def test_all_methods_produce_existing_file(tmp_path: Path, sample_png: Path, method: str) -> None:
    """
    Given  a HandwritingPreprocessor configured with any supported method
    When   preprocess() is called on a valid PNG
    Then   the returned output path exists on disk
    """
    p = HandwritingPreprocessor(tmp_dir=tmp_path, method=method)
    out, _ = p.preprocess(sample_png)
    assert out.exists()


def test_none_method_returns_original_path(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with method="none"
    When   preprocess() is called
    Then   the original file path is returned unchanged and the steps list is empty
    """
    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="none")
    out, steps = p.preprocess(sample_png)
    assert out == sample_png
    assert steps == []


@pytest.mark.parametrize("method", ["adaptive_threshold", "clahe", "grayscale"])
def test_non_none_method_returns_different_path(
    tmp_path: Path, sample_png: Path, method: str
) -> None:
    """
    Given  a HandwritingPreprocessor with an active preprocessing method
    When   preprocess() is called
    Then   the returned output path differs from the input path
    """
    p = HandwritingPreprocessor(tmp_dir=tmp_path, method=method)
    out, _ = p.preprocess(sample_png)
    assert out != sample_png


@pytest.mark.parametrize("method", ["adaptive_threshold", "clahe", "grayscale"])
def test_non_none_method_reports_steps(tmp_path: Path, sample_png: Path, method: str) -> None:
    """
    Given  a HandwritingPreprocessor with an active preprocessing method
    When   preprocess() is called
    Then   the returned steps list contains at least one step label
    """
    p = HandwritingPreprocessor(tmp_dir=tmp_path, method=method)
    _, steps = p.preprocess(sample_png)
    assert len(steps) >= 1


def test_save_steps_creates_intermediate_files(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with save_steps=True
    When   preprocess() is called
    Then   JPEG snapshot files named *_step*.jpg are written to tmp_dir
    """
    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="adaptive_threshold", save_steps=True)
    _, steps = p.preprocess(sample_png)
    assert len(steps) >= 1
    # Step files are saved as {stem}_step{n}_{name}.jpg in tmp_dir
    step_files = list(tmp_path.glob("*_step*.jpg"))
    assert len(step_files) >= 1


def test_output_is_openable_by_pil(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with method="clahe"
    When   preprocess() is called and the output path is opened with Pillow
    Then   the image loads successfully and has a non-zero width
    """
    from PIL import Image  # noqa: PLC0415

    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="clahe")
    out, _ = p.preprocess(sample_png)
    img = Image.open(out)
    assert img.size[0] > 0


def test_default_method_is_adaptive_threshold(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor created with no explicit method argument
    When   preprocess() is called
    Then   the output path differs from the input (default method is adaptive_threshold, not none)
    """
    p = HandwritingPreprocessor(tmp_dir=tmp_path)
    # Default method is adaptive_threshold — output should differ from input
    out, _ = p.preprocess(sample_png)
    assert out != sample_png


def test_preprocessor_raises_value_error_for_unreadable_file(tmp_path: Path) -> None:
    """
    Given  a file that is not a valid image (a plain text file)
    When   preprocess() is called with an active method (grayscale)
    Then   a ValueError is raised mentioning 'Cannot read image'
    """
    bad_file = tmp_path / "not_an_image.txt"
    bad_file.write_text("this is not an image")

    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale")
    with pytest.raises(ValueError, match="Cannot read image"):
        p.preprocess(bad_file)


def test_preprocessor_writes_output_alongside_source_when_no_tmp_dir(
    sample_png: Path,
) -> None:
    """
    Given  a HandwritingPreprocessor with tmp_dir=None
    When   preprocess() is called with method='grayscale'
    Then   the output file is placed in the same directory as the source image
    """
    p = HandwritingPreprocessor(tmp_dir=None, method="grayscale")
    out, steps = p.preprocess(sample_png)

    assert out.parent == sample_png.parent
    assert out.exists()
    assert len(steps) >= 1


def test_preprocessor_grayscale_dpi_failure_prints_warning(
    tmp_path: Path,
    sample_png: Path,
    caplog,
) -> None:
    """
    Given  a grayscale preprocessor and PIL fails to restore DPI on the output image
    When   preprocess() is called
    Then   a WARNING is logged and the output file still exists
    """
    import logging  # noqa: PLC0415
    from unittest.mock import patch, MagicMock  # noqa: PLC0415

    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale")

    # Make _PILImage.open raise an exception to trigger the DPI-restore warning
    with (
        caplog.at_level(logging.WARNING),
        patch("teachers_teammate.infrastructure.image_preprocessor._PILImage") as mock_pil,
    ):
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = MagicMock()
        mock_ctx.__exit__.side_effect = Exception("disk full")
        mock_pil.open.return_value = mock_ctx
        out, steps = p.preprocess(sample_png)

    assert out.exists()
    assert any("DPI" in r.message or "dpi" in r.message.lower() for r in caplog.records)


def test_preprocessor_adaptive_threshold_dpi_failure_prints_warning(
    tmp_path: Path,
    sample_png: Path,
    caplog,
) -> None:
    """
    Given  an adaptive_threshold preprocessor and PIL fails to restore DPI on output
    When   preprocess() is called
    Then   a WARNING is logged and the output file still exists
    """
    import logging  # noqa: PLC0415
    from unittest.mock import patch, MagicMock  # noqa: PLC0415

    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="adaptive_threshold")

    with (
        caplog.at_level(logging.WARNING),
        patch("teachers_teammate.infrastructure.image_preprocessor._PILImage") as mock_pil,
    ):
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = MagicMock()
        mock_ctx.__exit__.side_effect = Exception("disk full")
        mock_pil.open.return_value = mock_ctx
        out, steps = p.preprocess(sample_png)

    assert out.exists()
    assert any("DPI" in r.message or "dpi" in r.message.lower() for r in caplog.records)
