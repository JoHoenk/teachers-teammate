"""Unit tests for teachers_teammate.infrastructure.image_preprocessor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


# ── Pre-step tests ────────────────────────────────────────────────────────────


def test_deskew_produces_existing_output(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with deskew=True
    When   preprocess() is called on a valid PNG
    Then   the output file exists and is PIL-readable
    """
    from PIL import Image  # noqa: PLC0415

    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale", deskew=True)
    out, steps = p.preprocess(sample_png)
    assert out.exists()
    assert "deskew" in steps
    img = Image.open(out)
    assert img.size[0] > 0


def test_deskew_skipped_when_angle_near_zero(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with deskew=True and minAreaRect returns a near-zero angle
    When   preprocess() is called
    Then   no rotation is applied but the step is still reported
    """
    from unittest.mock import patch  # noqa: PLC0415
    import cv2 as _cv2  # noqa: PLC0415

    original_min_area_rect = _cv2.minAreaRect

    def _fake_min_area_rect(coords):
        box, size, angle = original_min_area_rect(coords)
        return box, size, 0.3  # below 0.5° threshold

    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale", deskew=True)
    with patch(
        "teachers_teammate.infrastructure.image_preprocessor.cv2.minAreaRect", _fake_min_area_rect
    ):
        out, steps = p.preprocess(sample_png)
    assert out.exists()
    # Step is listed even when rotation is skipped (the function was called)
    assert "deskew" in steps


def test_denoise_produces_valid_output(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with denoise=True
    When   preprocess() is called
    Then   the output file exists and is PIL-readable
    """
    from PIL import Image  # noqa: PLC0415

    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale", denoise=True)
    out, steps = p.preprocess(sample_png)
    assert out.exists()
    assert "denoise" in steps
    img = Image.open(out)
    assert img.size[0] > 0


def test_dewarp_noop_on_flat_scan(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with dewarp=True on a flat white-background scan
    When   preprocess() is called
    Then   the output file exists (dewarp returns original when no clear quad is found)
    """
    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale", dewarp=True)
    out, steps = p.preprocess(sample_png)
    assert out.exists()
    assert "dewarp" in steps


def test_border_crop_reduces_or_preserves_dimensions(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with border_crop=True
    When   preprocess() is called
    Then   the output file exists; its dimensions are <= the input dimensions
    """
    from PIL import Image  # noqa: PLC0415

    with Image.open(sample_png) as src:
        orig_w, orig_h = src.size

    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale", border_crop=True)
    out, steps = p.preprocess(sample_png)
    assert out.exists()
    assert "border_crop" in steps
    with Image.open(out) as dst:
        assert dst.size[0] <= orig_w
        assert dst.size[1] <= orig_h


def test_gamma_brightens_image(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with gamma=True on a grayscale image
    When   preprocess() is called
    Then   the mean pixel value of the output is >= the mean of the grayscale input
    """
    import cv2 as _cv2  # noqa: PLC0415
    import numpy as _np  # noqa: PLC0415  # pylint: disable=import-error

    # First get grayscale mean
    p_gray = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale")
    gray_out, _ = p_gray.preprocess(sample_png)
    gray_arr = _cv2.imread(str(gray_out), _cv2.IMREAD_GRAYSCALE)
    assert gray_arr is not None
    gray_mean = float(_np.mean(gray_arr))

    tmp2 = tmp_path / "gamma"
    tmp2.mkdir()
    p_gamma = HandwritingPreprocessor(tmp_dir=tmp2, method="grayscale", gamma=True)
    gamma_out, steps = p_gamma.preprocess(sample_png)
    gamma_arr = _cv2.imread(str(gamma_out), _cv2.IMREAD_GRAYSCALE)
    assert gamma_arr is not None
    gamma_mean = float(_np.mean(gamma_arr))

    assert "gamma" in steps
    assert gamma_mean >= gray_mean


def test_pre_step_execution_order(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with all pre-steps enabled
    When   preprocess() is called
    Then   steps are reported in fixed order: dewarp, deskew, border_crop, denoise, gamma
    """
    p = HandwritingPreprocessor(
        tmp_dir=tmp_path,
        method="grayscale",
        dewarp=True,
        deskew=True,
        border_crop=True,
        denoise=True,
        gamma=True,
    )
    _, steps = p.preprocess(sample_png)
    pre_steps = [s for s in steps if s not in ("grayscale",)]
    expected_order = ["dewarp", "deskew", "border_crop", "denoise", "gamma"]
    assert pre_steps == expected_order


def test_step_names_reported_for_enabled_pre_steps(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with deskew and denoise enabled
    When   preprocess() is called
    Then   steps list contains exactly 'deskew' and 'denoise' among the pre-step names
    """
    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale", deskew=True, denoise=True)
    _, steps = p.preprocess(sample_png)
    assert "deskew" in steps
    assert "denoise" in steps
    assert "dewarp" not in steps
    assert "border_crop" not in steps
    assert "gamma" not in steps


def test_save_steps_includes_pre_step_snapshots(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a HandwritingPreprocessor with save_steps=True and deskew enabled
    When   preprocess() is called
    Then   at least one snapshot file contains 'deskew' in its name
    """
    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale", save_steps=True, deskew=True)
    p.preprocess(sample_png)
    step_files = list(tmp_path.glob("*_step*.jpg"))
    assert any("deskew" in f.name for f in step_files)


# ── Behaviour tests (assert the algorithm does the right thing) ──────────────────


def _residual_tilt(gray: Any) -> float:
    """Return the deskew correction (degrees) the preprocessor would still apply to *gray*.

    Uses the same minAreaRect normalisation as ``_deskew_image`` (legacy [-90, 0) range
    on the pinned opencv-python), so a straight image yields ~0.
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415  # pylint: disable=import-error

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(binary > 0))
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    return float(angle)


@pytest.mark.use_case("Preview_Preprocessing")
def test_deskew_straightens_a_tilted_block(tmp_path: Path) -> None:
    """
    Given  an image containing a text-like block rotated by +10 degrees
    When   preprocess() runs with deskew=True
    Then   the residual skew of the output is much smaller than the 10-degree input tilt

    Regression guard: a deskew that rotates the wrong way (or by ~90 degrees on the
    wrong angle convention) would leave a large residual tilt, which this catches.
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415  # pylint: disable=import-error

    canvas = np.full((400, 400), 255, dtype=np.uint8)
    # A single wide, short bar gives minAreaRect an unambiguous orientation.
    cv2.rectangle(canvas, (40, 190), (360, 215), 0, thickness=-1)
    rot = cv2.getRotationMatrix2D((200, 200), 10.0, 1.0)
    tilted = cv2.warpAffine(canvas, rot, (400, 400), borderValue=255)
    src = tmp_path / "tilted.png"
    cv2.imwrite(str(src), tilted)

    assert abs(_residual_tilt(tilted)) > 5.0  # input really is tilted

    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale", deskew=True)
    out, steps = p.preprocess(src)
    assert "deskew" in steps
    result = cv2.imread(str(out), cv2.IMREAD_GRAYSCALE)
    assert result is not None
    assert abs(_residual_tilt(result)) < 2.0


@pytest.mark.use_case("Preview_Preprocessing")
def test_dewarp_remaps_a_quadrilateral(tmp_path: Path) -> None:
    """
    Given  an image whose dominant content is a non-axis-aligned bright quadrilateral
    When   preprocess() runs with dewarp=True
    Then   the output is remapped to the quad's bounding rectangle (dimensions change)
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415  # pylint: disable=import-error

    canvas = np.zeros((400, 400), dtype=np.uint8)
    quad = np.array([[50, 50], [350, 80], [330, 360], [70, 340]], dtype=np.int32)
    cv2.fillPoly(canvas, [quad], 255)
    src = tmp_path / "quad.png"
    cv2.imwrite(str(src), canvas)

    p = HandwritingPreprocessor(tmp_dir=tmp_path, method="grayscale", dewarp=True)
    out, steps = p.preprocess(src)
    assert "dewarp" in steps
    result = cv2.imread(str(out), cv2.IMREAD_GRAYSCALE)
    assert result is not None
    # A successful perspective remap crops to the quad: smaller than the 400x400 source.
    assert result.shape[0] < 400 or result.shape[1] < 400
