"""The step protocol and the pipeline that runs a list of steps on one image.

A step takes the current image (a NumPy array) and returns a :class:`StepResult`.  The
pipeline loads the file once, runs the steps in order, lists the names of the steps that acted
(``preprocess_steps`` of a stored result keeps meaning what it meant), writes a PNG, and puts
back the DPI a scan carried.  Which steps run, and in which order, is the caller's list — the
app's settings build it in ``recipes.py``, the evaluation tool builds its own.

An empty list is the setting "Original page": the file goes on as it is, except that a photo's
rotation tag (EXIF orientation) is applied — a phone photo must not reach the reader sideways.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Protocol

from PIL import Image as _PILImage

from ...exceptions import PipelineInputError
from ...interfaces import ImagePreprocessor
from ..image_io import read_image, write_image

_logger = logging.getLogger(__name__)

TURNED_BY_TAG = "turned by tag"
"""The name listed when the original page was only turned by its rotation tag."""

_EXIF_ORIENTATION = 274
_EXIF_MAKE = 271
_EXIF_MODEL = 272


@dataclass(frozen=True)
class StepResult:
    """What one step made of the image."""

    image: Any
    acted: bool
    """Whether the step did its work.  Only steps that acted are listed and snapshotted."""
    reason: str = ""
    """Why the step left the image alone or what it decided; goes to the debug log."""


@dataclass
class StepContext:
    """What a step may know besides the image."""

    source_dpi: tuple[float, float] | None = None
    """The DPI of the source file, when it said so."""
    camera_tags: bool = False
    """Whether the source file carries a camera's make or model (a photo, not a scan)."""
    measurements: dict[str, Any] = field(default_factory=dict)
    """Values one step leaves for the later ones (an estimated angle, a page box, …)."""


class Step(Protocol):
    """One operation of the image preparation."""

    name: str
    """The name listed in ``preprocess_steps`` (and, lower-cased, in a snapshot's file name)."""

    def apply(self, image: Any, ctx: StepContext) -> StepResult:
        """Return the image after this step."""
        ...


class StepPipeline(ImagePreprocessor):
    """Runs a list of steps on an image file and writes the result as ``<stem>_preprocessed.png``.

    An empty list returns the original file (the method "none"), as a PNG only when its rotation
    tag has to be applied.  With *save_steps* and a *tmp_dir*, a JPEG snapshot follows every step
    that acted.
    """

    def __init__(
        self,
        steps: Sequence[Step],
        tmp_dir: Path | None = None,
        save_steps: bool = False,
    ) -> None:
        self._steps = list(steps)
        self._tmp_dir = tmp_dir
        self._save_steps = save_steps

    def preprocess(self, image_path: Path) -> tuple[Path, list[str]]:
        """Prepare *image_path*; return ``(output_path, names of the steps that acted)``.

        The output goes to *tmp_dir*, or beside the source file without one; the caller cleans
        it up.  Raises :exc:`~teachers_teammate.exceptions.PipelineInputError` for a file that
        cannot be read.
        """
        info = _read_source_info(image_path)
        if not self._steps and info.orientation in (None, 1):
            return image_path, []
        ctx = StepContext(source_dpi=info.dpi, camera_tags=info.camera_tags)
        image: Any = read_image(image_path)  # applies the rotation tag
        if image is None:
            raise PipelineInputError(f"Cannot read image: {image_path}")

        names: list[str] = [] if self._steps else [TURNED_BY_TAG]
        for step in self._steps:
            result = step.apply(image, ctx)
            if result.reason:
                _logger.debug("%s: %s", step.name, result.reason)
            if not result.acted:
                continue
            image = result.image
            names.append(step.name)
            self._save_snapshot(image, image_path.stem, len(names), step.name)

        out_path = self._output_path(image_path)
        write_image(out_path, image)
        _restore_dpi(out_path, ctx.source_dpi)
        return out_path, names

    def _save_snapshot(self, image: Any, stem: str, number: int, name: str) -> None:
        """Write a snapshot of *image* to *tmp_dir* if step-saving is enabled."""
        if not self._save_steps or self._tmp_dir is None:
            return
        label = name.lower().replace(" ", "_")
        write_image(self._tmp_dir / f"{stem}_step{number}_{label}.jpg", image)

    def _output_path(self, image_path: Path) -> Path:
        # PNG is lossless and compresses binary images far better than JPEG;
        # it also avoids the grey fringe artefacts JPEG introduces on 0/255 edges.
        out_name = f"{image_path.stem}_preprocessed.png"
        if self._tmp_dir is not None:
            return self._tmp_dir / out_name
        return image_path.parent / out_name


@dataclass(frozen=True)
class _SourceInfo:
    """What the source file says about itself, read before OpenCV strips it."""

    dpi: tuple[float, float] | None = None
    orientation: int | None = None
    """The EXIF orientation (1 = upright); ``None`` if the file has none."""
    camera_tags: bool = False


def _read_source_info(image_path: Path) -> _SourceInfo:
    """Return the DPI, rotation tag and camera tags *image_path* carries (empty if unreadable)."""
    try:
        with _PILImage.open(image_path) as source:
            dpi = source.info.get("dpi")
            exif = source.getexif()
            return _SourceInfo(
                dpi=dpi,
                orientation=exif.get(_EXIF_ORIENTATION),
                camera_tags=bool(exif.get(_EXIF_MAKE) or exif.get(_EXIF_MODEL)),
            )
    except Exception as exc:  # noqa: BLE001  # PIL may raise format-specific errors; log and continue
        _logger.warning("Could not read the metadata of %s: %s", image_path, exc)
        return _SourceInfo()


def _restore_dpi(out_path: Path, dpi: tuple[float, float] | None) -> None:
    """Put the source's DPI back on *out_path*, which OpenCV wrote without it."""
    if not dpi:
        return
    try:
        with _PILImage.open(out_path) as written:
            written.save(str(out_path), dpi=dpi)
    except Exception as exc:  # noqa: BLE001  # PIL save errors (permissions, disk full, unsupported format) are non-fatal; log and continue
        _logger.warning("Could not restore DPI metadata on %s: %s", out_path, exc)
