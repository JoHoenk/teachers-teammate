"""Golden test: the automatic preparation gives the same pixels until AUTO_VERSION is bumped.

Five pictures (the sample PNG, the sample PDF page, a page photographed on a dark desk, a page
under a shadow, a page tilted by 3 degrees) go through the automatic preparation without page
models.  A change in the pixels or in the reported step names that comes with the same
``AUTO_VERSION`` fails: stored pages would then differ from what a new run produces, and the
cache would not know.  When the change is on purpose, bump ``AUTO_VERSION`` in
``infrastructure/preprocessing/identity.py`` and ``GOLDEN_VERSION`` below, run this test under
Bazel and paste the digests its failures name into ``preprocessing_auto_golden_data.GOLDEN``.

The digests belong to the pinned OpenCV build on Linux (a new OpenCV in the lock may legitimately
move them: look at the pictures, then regenerate).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
from typing import Any

import cv2
import numpy as np
import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.preprocessing.identity import AUTO_VERSION
from teachers_teammate.infrastructure.preprocessing.recipes import steps_for
from teachers_teammate.infrastructure.preprocessing.steps import StepPipeline

from . import preprocessing_auto_golden_data as pictures

DRAWN = ("photo", "shadow", "tilt")
"""The pictures this module draws; ``sample_png`` and ``sample_pdf`` come from the fixtures."""

#: The version the digests in the data module were taken at.
GOLDEN_VERSION = 1


def _drawn(name: str) -> Any:
    return {
        "photo": pictures.photo_of_page,
        "shadow": pictures.shadow_page,
        "tilt": lambda: pictures.tilted_page(3.0),
    }[name]()


def digest_of(image_path: Path, steps: list[str]) -> str:
    """Return a short digest of the decoded pixels and the reported step names."""
    pixels = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    h = hashlib.sha256()
    h.update(repr(pixels.shape).encode())
    h.update(np.ascontiguousarray(pixels).tobytes())
    h.update("|".join(steps).encode())
    return h.hexdigest()[:16]


def prepare(source: Path, folder: Path) -> tuple[Path, list[str]]:
    """Prepare *source* the way a run does for the built-in reader without page models."""
    ocr = OcrConfig(engine="ppocr", preprocess_method="auto")
    pipeline = StepPipeline(steps_for(ocr, ModelStore(folder / "no_models")), tmp_dir=folder)
    return pipeline.preprocess(source)


def _write_drawn(name: str, folder: Path) -> Path:
    path = folder / f"{name}.png"
    path.write_bytes(cv2.imencode(".png", _drawn(name))[1].tobytes())
    return path


def _sample_pdf_page(sample_pdf: Path, folder: Path) -> Path:
    from teachers_teammate.infrastructure.input_provider_factory import (  # noqa: PLC0415
        InputOptions,
        get_input_provider,
    )

    provider = get_input_provider(
        ".pdf", tmp_dir=folder, options=InputOptions(pdf_dpi=150, pdf_text_layer="never")
    )
    payload = provider.load(sample_pdf)
    page = next(u.image_path for u in payload.units if u.image_path is not None)
    assert page is not None
    return page


needs_linux = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="the digests belong to the Linux OpenCV build"
)


def test_the_digests_were_taken_at_the_current_auto_version() -> None:
    """
    Given  the golden digests and the version they were taken at
    When   AUTO_VERSION is bumped
    Then   this test fails until the digests are regenerated (a reminder, not a guard)
    """
    assert GOLDEN_VERSION == AUTO_VERSION


@needs_linux
@pytest.mark.parametrize("name", [*DRAWN, "sample_png", "sample_pdf"])
def test_the_automatic_preparation_gives_the_pixels_it_always_gave(
    name: str, tmp_path: Path, sample_png: Path, sample_pdf: Path
) -> None:
    """
    Given  one of five pictures and the automatic preparation without page models
    When   the picture is prepared
    Then   the digest of the pixels and of the listed steps is the golden one
    """
    if name == "sample_png":
        source = sample_png
    elif name == "sample_pdf":
        source = _sample_pdf_page(sample_pdf, tmp_path)
    else:
        source = _write_drawn(name, tmp_path)

    out, steps = prepare(source, tmp_path)

    actual = digest_of(out, steps)
    assert actual == pictures.GOLDEN.get(name), f'"{name}": "{actual}"  (steps: {steps})'
