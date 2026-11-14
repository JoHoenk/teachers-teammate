"""Unit tests for teachers_teammate.infrastructure.preprocessing (steps, recipes, pipeline)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.exceptions import PipelineInputError
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.preprocessing.recipes import steps_for
from teachers_teammate.infrastructure.preprocessing.steps import (
    TURNED_BY_TAG,
    StepContext,
    StepPipeline,
    StepResult,
)


def _pipeline(
    tmp_dir: Path | None = None,
    method: str = "auto",
    save_steps: bool = False,
    engine: str = "ollama",
) -> StepPipeline:
    """Return the pipeline the app builds for *method*, with no page models downloaded."""
    ocr = OcrConfig(engine=engine, preprocess_method=method)
    store = ModelStore((tmp_dir or Path("/nonexistent")) / "no_models")
    return StepPipeline(steps_for(ocr, store), tmp_dir=tmp_dir, save_steps=save_steps)


def _jpeg_with_tags(path: Path, *, orientation: int | None, camera: bool) -> Path:
    """Write a 200 x 100 JPEG (white left strip) with the given EXIF orientation and camera."""
    from PIL import Image  # noqa: PLC0415

    image = Image.new("RGB", (200, 100), (30, 30, 30))
    image.paste((255, 255, 255), (0, 0, 50, 100))
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    if camera:
        exif[271] = "Camera Maker"
        exif[272] = "Model X"
    image.save(path, exif=exif, quality=100)
    return path


@pytest.mark.parametrize("method", ["auto", "none"])
def test_all_methods_produce_existing_file(tmp_path: Path, sample_png: Path, method: str) -> None:
    """
    Given  a step pipeline configured with either method
    When   preprocess() is called on a valid PNG
    Then   the returned output path exists on disk
    """
    out, _ = _pipeline(tmp_dir=tmp_path, method=method).preprocess(sample_png)
    assert out.exists()


def test_none_method_returns_original_path(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a step pipeline with method="none" ("Original page") and an image without a rotation tag
    When   preprocess() is called
    Then   the original file path is returned unchanged and the steps list is empty
    """
    out, steps = _pipeline(tmp_dir=tmp_path, method="none").preprocess(sample_png)
    assert out == sample_png
    assert steps == []


def test_none_method_applies_the_rotation_tag(tmp_path: Path) -> None:
    """
    Given  a JPEG with the EXIF orientation 6 (a phone photo held sideways), 200 x 100 pixels
    When   preprocess() runs with method="none"
    Then   a PNG of the upright page (100 x 200) is written, and the listed step is "turned by
           tag"
    """
    from PIL import Image  # noqa: PLC0415

    source = _jpeg_with_tags(tmp_path / "photo.jpg", orientation=6, camera=True)

    out, steps = _pipeline(tmp_dir=tmp_path, method="none").preprocess(source)

    assert out != source
    assert out.suffix == ".png"
    assert steps == [TURNED_BY_TAG]
    with Image.open(out) as image:
        assert image.size == (100, 200)


@pytest.mark.parametrize("orientation", [None, 1])
def test_none_method_passes_a_jpeg_without_a_turn_on_unchanged(
    tmp_path: Path, orientation: int | None
) -> None:
    """
    Given  a JPEG with no rotation tag, or the tag 1 (upright)
    When   preprocess() runs with method="none"
    Then   the original path is returned and no step is listed
    """
    source = _jpeg_with_tags(tmp_path / "photo.jpg", orientation=orientation, camera=True)

    out, steps = _pipeline(tmp_dir=tmp_path, method="none").preprocess(source)

    assert out == source
    assert steps == []


def test_auto_method_returns_different_path(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a step pipeline with the automatic preparation
    When   preprocess() is called
    Then   the returned output path differs from the input path
    """
    out, _ = _pipeline(tmp_dir=tmp_path).preprocess(sample_png)
    assert out != sample_png


def test_auto_method_hands_ollama_a_gray_page_and_lists_it(
    tmp_path: Path, sample_png: Path
) -> None:
    """
    Given  the automatic preparation for Ollama and a colour page
    When   preprocess() is called
    Then   the output has one channel and the step "grayscale" is listed
    """
    import cv2  # noqa: PLC0415

    out, steps = _pipeline(tmp_dir=tmp_path, engine="ollama").preprocess(sample_png)

    assert "grayscale" in steps
    assert cv2.imread(str(out), cv2.IMREAD_UNCHANGED).ndim == 2


def test_auto_method_keeps_colour_for_the_built_in_reader(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  the automatic preparation for the built-in reader and a clean colour scan
    When   preprocess() is called
    Then   the output has three channels and no step is listed (the clean scan passes unchanged)
    """
    import cv2  # noqa: PLC0415

    out, steps = _pipeline(tmp_dir=tmp_path, engine="ppocr").preprocess(sample_png)

    assert steps == []
    assert cv2.imread(str(out), cv2.IMREAD_UNCHANGED).shape[2] == 3


def test_save_steps_creates_snapshots_of_the_steps_that_acted(
    tmp_path: Path, sample_png: Path
) -> None:
    """
    Given  a step pipeline with save_steps=True (Ollama: the gray conversion acts)
    When   preprocess() is called
    Then   a JPEG snapshot named after the step is written to tmp_dir
    """
    _, steps = _pipeline(tmp_dir=tmp_path, save_steps=True).preprocess(sample_png)

    assert steps == ["grayscale"]
    assert [p.name for p in tmp_path.glob("*_step*.jpg")] == ["sample_step1_grayscale.jpg"]


def test_output_is_openable_by_pil(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a step pipeline with the automatic preparation
    When   preprocess() is called and the output path is opened with Pillow
    Then   the image loads successfully and has a non-zero width
    """
    from PIL import Image  # noqa: PLC0415

    out, _ = _pipeline(tmp_dir=tmp_path).preprocess(sample_png)
    with Image.open(out) as img:
        assert img.size[0] > 0


def test_default_method_is_auto(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  an OcrConfig created with no explicit method
    When   the recipe lists its steps
    Then   it is the automatic preparation (eight steps, the first one "measure")
    """
    store = ModelStore(tmp_path / "no_models")
    names = [step.name for step in steps_for(OcrConfig(), store)]
    assert names[0] == "measure"
    assert len(names) == 8


def test_preprocessor_raises_for_unreadable_file(tmp_path: Path) -> None:
    """
    Given  a file that is not a valid image (a plain text file)
    When   preprocess() is called with the automatic preparation
    Then   a ValueError is raised mentioning 'Cannot read image'
    """
    bad_file = tmp_path / "not_an_image.txt"
    bad_file.write_text("this is not an image")

    with pytest.raises(ValueError, match="Cannot read image"):
        _pipeline(tmp_dir=tmp_path).preprocess(bad_file)


def test_preprocessor_writes_output_alongside_source_when_no_tmp_dir(sample_png: Path) -> None:
    """
    Given  a step pipeline with tmp_dir=None
    When   preprocess() is called
    Then   the output file is placed in the same directory as the source image
    """
    out, steps = _pipeline(tmp_dir=None).preprocess(sample_png)

    assert out.parent == sample_png.parent
    assert out.exists()
    assert len(steps) >= 1


def test_preprocessor_dpi_failure_prints_warning(
    tmp_path: Path, sample_png: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Given  a preprocessor and PIL fails to restore the DPI on the output image
    When   preprocess() is called
    Then   a WARNING is logged and the output file still exists
    """
    import logging  # noqa: PLC0415
    from unittest.mock import MagicMock, patch  # noqa: PLC0415

    p = _pipeline(tmp_dir=tmp_path)

    # Make _PILImage.open raise an exception to trigger the DPI-restore warning
    with (
        caplog.at_level(logging.WARNING),
        patch("teachers_teammate.infrastructure.preprocessing.steps._PILImage") as mock_pil,
    ):
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = MagicMock()
        mock_ctx.__exit__.side_effect = Exception("disk full")
        mock_pil.open.return_value = mock_ctx
        out, _steps = p.preprocess(sample_png)

    assert out.exists()
    assert any("dpi" in r.message.lower() for r in caplog.records)


def test_non_ascii_path_is_read_and_written(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  an image whose name contains a non-ASCII character (märz_page0.png)
           and OpenCV file functions that cannot open such paths (as on Windows)
    When   preprocess() is called with save_steps enabled
    Then   the image is preprocessed, and the output and step snapshots are written
    """
    from unittest.mock import patch  # noqa: PLC0415

    src = tmp_path / "märz_page0.png"
    src.write_bytes(sample_png.read_bytes())

    def ascii_only(path: Any, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(f"OpenCV must not open file paths directly: {path}")

    with (
        patch("teachers_teammate.infrastructure.image_io.cv2.imread", ascii_only),
        patch("teachers_teammate.infrastructure.image_io.cv2.imwrite", ascii_only),
    ):
        out, _ = _pipeline(tmp_dir=tmp_path, save_steps=True).preprocess(src)

    assert out.name == "märz_page0_preprocessed.png"
    assert out.stat().st_size > 0
    assert list(tmp_path.glob("märz_page0_step*.jpg"))


def test_unreadable_image_raises_pipeline_input_error(tmp_path: Path) -> None:
    """
    Given  a file with a non-ASCII name that is not a decodable image
    When   preprocess() is called
    Then   PipelineInputError("Cannot read image") is raised
    """
    bad = tmp_path / "märz.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(PipelineInputError, match="Cannot read image"):
        _pipeline(tmp_dir=tmp_path).preprocess(bad)


def test_missing_image_raises_pipeline_input_error(tmp_path: Path) -> None:
    """
    Given  a path that does not exist
    When   preprocess() is called
    Then   PipelineInputError("Cannot read image") is raised
    """
    with pytest.raises(PipelineInputError, match="Cannot read image"):
        _pipeline(tmp_dir=tmp_path).preprocess(tmp_path / "x.png")


# ── steps and the pipeline ───────────────────────────────────────────────────


def test_the_recipe_lists_the_automatic_steps_in_order_and_none_lists_nothing(
    tmp_path: Path,
) -> None:
    """
    Given  the two methods
    When   steps_for() lists the steps
    Then   "auto" is measure, page, flatten, turn, size, light, deskew, grayscale (for Ollama);
           "none" gives no step at all
    """

    def names(method: str) -> list[str]:
        return [step.name for step in _pipeline(tmp_path, method=method)._steps]

    assert names("auto") == [
        "measure",
        "page",
        "flatten",
        "turn",
        "size",
        "light",
        "deskew",
        "grayscale",
    ]
    assert names("none") == []


class _Gray:
    """A step that always acts."""

    name = "gray"

    def apply(self, image: Any, ctx: StepContext) -> StepResult:
        import cv2  # noqa: PLC0415

        return StepResult(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), acted=True)


def test_a_step_that_does_not_act_is_neither_listed_nor_snapshotted(
    tmp_path: Path, sample_png: Path
) -> None:
    """
    Given  a list of steps in which one declines to act and one acts
    When   the pipeline prepares an image with step snapshots on
    Then   only the acting step is listed and snapshotted, and it is numbered 1
    """

    class _Declines:
        name = "declines"

        def apply(self, image: Any, ctx: Any) -> StepResult:
            return StepResult(image, acted=False, reason="nothing to do here")

    pipeline = StepPipeline([_Declines(), _Gray()], tmp_dir=tmp_path, save_steps=True)

    out, names = pipeline.preprocess(sample_png)

    assert names == ["gray"]
    assert out.exists()
    assert sorted(p.name for p in tmp_path.glob("*_step*")) == ["sample_step1_gray.jpg"]


def test_a_step_can_leave_a_measurement_for_a_later_one(tmp_path: Path, sample_png: Path) -> None:
    """
    Given  a first step that stores a value in the context and a second that reads it
    When   the pipeline runs both
    Then   the second step sees the value, and the context carries the source's DPI
    """
    seen: dict[str, Any] = {}

    class _Measures:
        name = "measure"

        def apply(self, image: Any, ctx: Any) -> StepResult:
            ctx.measurements["angle"] = 3.5
            return StepResult(image, acted=True)

    class _Reads:
        name = "read"

        def apply(self, image: Any, ctx: Any) -> StepResult:
            seen.update(ctx.measurements)
            seen["dpi"] = ctx.source_dpi
            return StepResult(image, acted=True)

    StepPipeline([_Measures(), _Reads()], tmp_dir=tmp_path).preprocess(sample_png)

    assert seen["angle"] == 3.5


def test_the_context_knows_whether_the_file_carries_camera_tags(tmp_path: Path) -> None:
    """
    Given  a JPEG with camera tags and one without
    When   a step reads the context
    Then   camera_tags is true for the first and false for the second
    """
    seen: list[bool] = []

    class _Looks:
        name = "look"

        def apply(self, image: Any, ctx: StepContext) -> StepResult:
            seen.append(ctx.camera_tags)
            return StepResult(image, acted=False)

    pipeline = StepPipeline([_Looks()], tmp_dir=tmp_path)
    pipeline.preprocess(_jpeg_with_tags(tmp_path / "a.jpg", orientation=None, camera=True))
    pipeline.preprocess(_jpeg_with_tags(tmp_path / "b.jpg", orientation=None, camera=False))

    assert seen == [True, False]
