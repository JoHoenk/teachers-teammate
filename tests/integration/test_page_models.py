"""The page models (turn, flatten) on their real, pinned model files.

Bazel fetches the files (``MODULE.bazel``, ``http_file``); under plain pytest, where they are not
provided, these tests skip.  Every other test fakes the models.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from teachers_teammate.infrastructure.preprocessing import auto
from teachers_teammate.infrastructure.preprocessing.page_models import ORIENTATIONS, PageModels
from teachers_teammate.infrastructure.preprocessing.steps import StepContext
from tests.conftest import page_models_store, skip_no_page_models
from tests.unit import preprocessing_auto_golden_data as pictures

pytestmark = [pytest.mark.needs_page_models, skip_no_page_models]

_LINES = [
    "Dear parents,",
    "this term the class has read three short stories and",
    "written a report about each one of them. Please sign",
    "the form below and return it by Friday.",
    "Homework: exercises 4 to 9 on page 52 of the workbook.",
    "Thank you very much for your help and support.",
]


def _paragraph_page() -> Any:
    """Return an upright page of real, printed-looking text (a TrueType font, 18 lines)."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    image = Image.new("RGB", (pictures.PAGE_W, pictures.PAGE_H), (250, 250, 250))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=34)
    for i in range(18):
        draw.text((70, 80 + i * 60), _LINES[i % len(_LINES)], fill=(20, 20, 20), font=font)
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def _run_steps(image: Any, store: Any) -> dict[str, Any]:
    ctx = StepContext()
    answers: dict[str, Any] = {}
    for step in auto.auto_steps("ppocr", store):
        answer = step.apply(image, ctx)
        answers[step.name] = answer
        image = answer.image
    answers["image"] = image
    return answers


@pytest.mark.parametrize(
    ("rotation", "degrees"),
    [
        (None, 0),
        (cv2.ROTATE_90_CLOCKWISE, 90),
        (cv2.ROTATE_180, 180),
        (cv2.ROTATE_90_COUNTERCLOCKWISE, 270),
    ],
)
def test_the_orientation_model_names_how_far_a_page_is_turned(
    tmp_path: Path, rotation: int | None, degrees: int
) -> None:
    """
    Given  an upright page of text, turned 0, 90, 180 and 270 degrees clockwise
    When   the real orientation model looks at it
    Then   it names the right class with a confidence of at least 0.9
    """
    models = PageModels(page_models_store(tmp_path))
    page = _paragraph_page()
    image = page if rotation is None else cv2.rotate(page, rotation)

    answer, confidence = models.orientation(image)

    assert answer == degrees
    assert confidence >= auto.TURN_MIN_CONFIDENCE
    assert answer in ORIENTATIONS


@pytest.mark.parametrize(
    "rotation", [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE]
)
def test_turn_restores_a_page_turned_by_a_quarter_or_half_turn(
    tmp_path: Path, rotation: int
) -> None:
    """
    Given  an upright page of text turned 90, 180 or 270 degrees, and the real page models
    When   the automatic steps run
    Then   `turn` acts and the result is the upright page again, pixel for pixel
    """
    page = _paragraph_page()

    answers = _run_steps(cv2.rotate(page, rotation), page_models_store(tmp_path))

    assert answers["turn"].acted
    assert np.array_equal(answers["image"], page)


def _line_sharpness(image: Any) -> float:
    """Return how well the ink lines up in rows: higher is straighter lines."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (600, int(600 * gray.shape[0] / gray.shape[1])))
    ink = (gray < 100).astype(np.float32)
    return float(np.var(ink.sum(axis=1)) / max(ink.mean(), 1e-6))


def test_flatten_straightens_the_lines_of_a_curved_page(tmp_path: Path) -> None:
    """
    Given  a photo of a page bent by a sine wave, and the real page models
    When   the automatic steps run
    Then   `flatten` acts and the text lines are clearly straighter than before
    """
    curved = pictures.curved_photo()

    answers = _run_steps(curved, page_models_store(tmp_path))

    assert not answers["page"].acted
    assert answers["flatten"].acted
    assert _line_sharpness(answers["image"]) > 1.2 * _line_sharpness(curved)


def test_flatten_gives_an_image_at_the_size_it_was_given(tmp_path: Path) -> None:
    """
    Given  a photo whose long side is under the model's input cap
    When   the real flatten model runs on it
    Then   the answer has the photo's size
    """
    photo = pictures.curved_photo()

    flat = PageModels(page_models_store(tmp_path)).flatten(photo)

    assert flat.shape == photo.shape
