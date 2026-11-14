"""Unit tests for the automatic image preparation (``preprocessing/auto.py``)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.preprocessing import auto
from teachers_teammate.infrastructure.preprocessing.page_models import PageModels
from teachers_teammate.infrastructure.preprocessing.steps import StepContext, StepResult

from tests.conftest import fake_page_models_store
from . import preprocessing_auto_golden_data as pictures


def _run(
    image: Any,
    tmp_path: Path,
    *,
    engine: str = "ppocr",
    camera: bool = False,
    store: ModelStore | None = None,
    net_factory: Any = None,
) -> tuple[Any, dict[str, StepResult]]:
    """Run the automatic steps on *image*; return the result and what each step answered."""
    steps = auto.auto_steps(engine, store or ModelStore(tmp_path / "no_models"), net_factory)
    ctx = StepContext(camera_tags=camera)
    answers: dict[str, StepResult] = {}
    for step in steps:
        answer = step.apply(image, ctx)
        answers[step.name] = answer
        image = answer.image
    return image, answers


class _FakeNet:
    """A page model that answers a fixed array (or the input, for the flatten model)."""

    def __init__(self, answer: Any = None) -> None:
        self._answer = answer

    def run(self, blob: np.ndarray) -> np.ndarray:
        return blob if self._answer is None else np.asarray(self._answer, dtype=np.float32)


def _fake_models(tmp_path: Path, orientation: list[float] | None = None) -> tuple[ModelStore, Any]:
    """Return a store that looks like it holds the page models, and a net factory for them."""
    store = fake_page_models_store(tmp_path)

    def factory(path: Path) -> _FakeNet:
        return _FakeNet(orientation if "doc_ori" in str(path) else None)

    return store, factory


# ── the page ─────────────────────────────────────────────────────────────────


def test_a_photo_of_a_page_on_a_desk_is_cut_out(tmp_path: Path) -> None:
    """
    Given  a page photographed at an angle on a dark desk
    When   the automatic steps run
    Then   `page` acts and the result is about the page's size, upright and flat
    """
    photo = pictures.photo_of_page()

    result, answers = _run(photo, tmp_path)

    assert answers["page"].acted
    assert answers["flatten"].acted is False
    height, width = result.shape[:2]
    assert abs(width / height - pictures.PAGE_W / pictures.PAGE_H) < 0.05
    assert height > 0.85 * pictures.PAGE_H


def test_find_outline_names_the_corners_of_a_photographed_page() -> None:
    """
    Given  a page photographed at an angle on a dark desk
    When   find_outline() looks at it
    Then   it is a flat page and its corners are within 20 pixels of the drawn ones
    """
    outline = auto.find_outline(cv2.cvtColor(pictures.photo_of_page(), cv2.COLOR_BGR2GRAY))

    assert outline is not None
    assert outline.is_page
    assert outline.corners is not None
    drawn = np.array([[200, 150], [1050, 100], [1100, 1450], [120, 1500]], np.float32)
    assert float(np.abs(outline.corners - drawn).max()) < 20


def test_a_scan_with_a_framed_table_is_not_taken_for_a_page(tmp_path: Path) -> None:
    """
    Given  a clean scan with a big framed box: white inside and outside the frame
    When   the automatic steps run
    Then   `page` does not act, nothing else acts either, and the image is unchanged
    """
    scan = pictures.framed_table_scan()

    result, answers = _run(scan, tmp_path)

    assert not answers["page"].acted
    assert not any(answer.acted for answer in answers.values())
    assert np.array_equal(result, scan)


def test_a_flat_scan_passes_unchanged(tmp_path: Path) -> None:
    """
    Given  a flat, evenly lit, upright scan and the built-in reader (colour)
    When   the automatic steps run
    Then   no step acts, `light` says the paper is even, and the pixels are those of the scan
    """
    scan = pictures.text_page()

    result, answers = _run(scan, tmp_path)

    assert not any(answer.acted for answer in answers.values())
    assert "unevenness" in answers["light"].reason
    assert np.array_equal(result, scan)


# ── light, size, tilt ────────────────────────────────────────────────────────


def test_a_shadow_across_the_page_is_evened_out(tmp_path: Path) -> None:
    """
    Given  a page lit from the right: brightness falls to 45 % at the left edge
    When   the automatic steps run
    Then   `light` acts and the paper's brightness at the left edge is within 10 % of the right
    """
    page = pictures.shadow_page()

    result, answers = _run(page, tmp_path)

    assert answers["light"].acted
    margin_left = float(result[:, 5:25].mean())
    margin_right = float(result[:, -25:-5].mean())
    assert abs(margin_left - margin_right) < 0.1 * margin_right


def test_light_keeps_faint_pencil_darker_than_the_paper(tmp_path: Path) -> None:
    """
    Given  faint pencil text (grey 150) on grey paper (215) under a shadow
    When   the automatic steps run
    Then   `light` acts and the strokes are still clearly darker than the paper around them

    Regression guard for the risk that dividing by the background fades pencil away.
    """
    page = pictures.pencil_photo()

    result, answers = _run(page, tmp_path)

    assert answers["light"].acted
    gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
    paper = float(np.percentile(gray, 90))
    ink = float(np.percentile(gray, 1))
    assert paper - ink > 40


@pytest.mark.parametrize("engine", ["ppocr", "ollama"])
def test_a_page_larger_than_a4_at_300_dpi_is_scaled_down(tmp_path: Path, engine: str) -> None:
    """
    Given  a page of 3500 x 4950 pixels
    When   the automatic steps run
    Then   `size` acts and the long side is 3508
    """
    big = cv2.resize(pictures.text_page(), (3500, 4950))

    result, answers = _run(big, tmp_path, engine=engine)

    assert answers["size"].acted
    assert max(result.shape[:2]) == auto.SIZE_LONG_SIDE


def test_a_page_up_to_the_limit_is_not_scaled(tmp_path: Path) -> None:
    """
    Given  a page whose long side is exactly 3508 pixels
    When   the automatic steps run
    Then   `size` does not act
    """
    page = cv2.resize(pictures.text_page(), (2480, 3508))

    _result, answers = _run(page, tmp_path)

    assert not answers["size"].acted


def test_a_tilt_of_three_degrees_is_corrected(tmp_path: Path) -> None:
    """
    Given  a page tilted by 3 degrees
    When   the automatic steps run
    Then   `deskew` acts and what is left of the tilt is below 0.5 degrees
    """
    result, answers = _run(pictures.tilted_page(3.0), tmp_path)

    assert answers["deskew"].acted
    assert abs(auto.estimate_skew_angle(cv2.cvtColor(result, cv2.COLOR_BGR2GRAY))) < 0.5


def test_a_tilt_of_fifteen_degrees_is_left_alone(tmp_path: Path) -> None:
    """
    Given  a page tilted by 15 degrees (more than the 10 the preparation corrects)
    When   the automatic steps run
    Then   `deskew` does not act and says the tilt is too much
    """
    page = pictures.tilted_page(15.0)

    result, answers = _run(page, tmp_path)

    assert not answers["deskew"].acted
    assert "too much" in answers["deskew"].reason
    assert np.array_equal(result, page)


def _tilted_text_page(bg: int, tilt: float, border: int | None = None) -> Any:
    """Return a gray page of *bg*-toned paper with text lines, rotated by *tilt* degrees."""
    canvas = np.full((1400, 1000), bg, np.uint8)
    for y in range(150, 1250, 60):
        cv2.putText(
            canvas, "The quick brown fox jumps", (80, y), cv2.FONT_HERSHEY_SIMPLEX, 1.3, 0, 3
        )
    rot = cv2.getRotationMatrix2D((500, 700), tilt, 1.0)
    return cv2.warpAffine(canvas, rot, (1000, 1400), borderValue=bg if border is None else border)


@pytest.mark.parametrize("tilt", [0.0, 3.0, -6.0, 10.0])
@pytest.mark.parametrize("border", [None, 60])
@pytest.mark.parametrize("bg", [255, 235, 215])
def test_estimate_skew_angle_recovers_known_tilt(bg: int, border: int | None, tilt: float) -> None:
    """
    Given  a text page toned *bg* (white, off-white, or gray), rotated by a known *tilt*,
           optionally with a dark scanner-bed border exposed by the rotation
    When   estimate_skew_angle() scores the page
    Then   the estimated angle cancels the known tilt to within 0.6 degrees
    """
    assert abs(auto.estimate_skew_angle(_tilted_text_page(bg, tilt, border)) + tilt) < 0.6


def test_estimate_skew_angle_leaves_blank_and_straight_pages_alone() -> None:
    """
    Given  a blank white page, and a straight page of text on grey paper
    When   estimate_skew_angle() scores them
    Then   both answers are 0 (a blank page must not be turned by the first angle searched)
    """
    assert auto.estimate_skew_angle(np.full((400, 400), 255, np.uint8)) == 0.0
    assert auto.estimate_skew_angle(_tilted_text_page(215, 0.0)) == 0.0


def test_estimate_skew_angle_ignores_a_few_dust_specks() -> None:
    """
    Given  a page tilted by -5 degrees with a few isolated dark dust specks near the corners
    When   estimate_skew_angle() scores it
    Then   the answer is still about +5 degrees
    """
    page = _tilted_text_page(255, -5.0)
    for x, y in [(15, 15), (985, 1385), (20, 1380)]:
        cv2.circle(page, (x, y), 6, 0, -1)

    assert abs(auto.estimate_skew_angle(page) - 5.0) < 1.0


# ── the output ───────────────────────────────────────────────────────────────


def test_the_built_in_reader_gets_colour_and_ollama_gets_gray(tmp_path: Path) -> None:
    """
    Given  the same colour page
    When   the automatic steps run for each engine
    Then   the reader's output has three channels, Ollama's has one
    """
    page = pictures.text_page()

    reader, _ = _run(page, tmp_path, engine="ppocr")
    ollama, answers = _run(page, tmp_path, engine="ollama")

    assert reader.ndim == 3 and reader.shape[2] == 3
    assert ollama.ndim == 2
    assert answers["grayscale"].acted


# ── the model steps ──────────────────────────────────────────────────────────


def test_without_the_page_models_turn_and_flatten_leave_the_page_and_say_why(
    tmp_path: Path,
) -> None:
    """
    Given  a photo that needs flattening (curved) and no page models downloaded
    When   the automatic steps run
    Then   `turn` and `flatten` do not act, and both say the page models are not downloaded
    """
    _result, answers = _run(pictures.curved_photo(), tmp_path)

    for name in ("turn", "flatten"):
        assert not answers[name].acted
        assert "page models not downloaded" in answers[name].reason


def test_missing_page_models_are_logged_once_per_run_not_per_page(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Given  one list of automatic steps without page models, used for three pages
    When   the three pages run through it
    Then   the skipped turn and flatten are logged once at info level
    """
    steps = auto.auto_steps("ppocr", ModelStore(tmp_path / "no_models"))
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            ctx = StepContext(camera_tags=True)
            image: Any = pictures.text_page()
            for step in steps:
                image = step.apply(image, ctx).image

    notes = [r for r in caplog.records if "page models not downloaded" in r.getMessage()]
    assert len(notes) == 1


@pytest.mark.parametrize(
    ("scores", "acts", "shape"),
    [
        ([0.01, 0.97, 0.01, 0.01], True, (990, 1400)),  # turned 90 clockwise: turn back
        ([0.01, 0.01, 0.97, 0.01], True, (1400, 990)),  # upside down
        ([0.01, 0.01, 0.01, 0.97], True, (990, 1400)),
        ([0.02, 0.60, 0.30, 0.08], False, (1400, 990)),  # not sure enough
        ([0.97, 0.01, 0.01, 0.01], False, (1400, 990)),  # upright
    ],
)
def test_turn_acts_only_when_the_model_is_sure_the_page_is_turned(
    tmp_path: Path, scores: list[float], acts: bool, shape: tuple[int, int]
) -> None:
    """
    Given  the page models and an orientation model that answers *scores*
    When   the automatic steps run on an upright page
    Then   `turn` acts only for a confident 90, 180 or 270 (the page is rotated back)
    """
    store, factory = _fake_models(tmp_path, orientation=scores)

    result, answers = _run(pictures.text_page(), tmp_path, store=store, net_factory=factory)

    assert answers["turn"].acted is acts
    assert result.shape[:2] == shape


def test_flatten_acts_on_a_photo_the_corners_could_not_cut_out(tmp_path: Path) -> None:
    """
    Given  the page models, and a photo whose page outline is curved (no flat page)
    When   the automatic steps run
    Then   `page` does not act and `flatten` does
    """
    store, factory = _fake_models(tmp_path)

    _result, answers = _run(pictures.curved_photo(), tmp_path, store=store, net_factory=factory)

    assert not answers["page"].acted
    assert answers["flatten"].acted


def test_flatten_leaves_a_scan_alone(tmp_path: Path) -> None:
    """
    Given  the page models and a clean scan
    When   the automatic steps run
    Then   `flatten` does not act: scans use neither the corners nor UVDoc
    """
    store, factory = _fake_models(tmp_path)

    _result, answers = _run(pictures.text_page(), tmp_path, store=store, net_factory=factory)

    assert not answers["flatten"].acted


def test_a_camera_tag_makes_a_page_a_photo_for_flatten(tmp_path: Path) -> None:
    """
    Given  the page models and an upright page whose file carries camera tags
    When   the automatic steps run
    Then   `flatten` acts (a photo with no outline to cut out)
    """
    store, factory = _fake_models(tmp_path)

    _result, answers = _run(
        pictures.text_page(), tmp_path, camera=True, store=store, net_factory=factory
    )

    assert answers["flatten"].acted


def test_a_failing_model_does_not_fail_the_page(tmp_path: Path) -> None:
    """
    Given  page models whose network raises when it runs
    When   the automatic steps run on a photo
    Then   `turn` and `flatten` do not act, say the model failed, and the page still comes out
    """
    store = fake_page_models_store(tmp_path)

    class _Broken:
        def run(self, blob: np.ndarray) -> np.ndarray:
            raise RuntimeError("model broke")

    result, answers = _run(
        pictures.curved_photo(), tmp_path, store=store, net_factory=lambda _path: _Broken()
    )

    assert not answers["turn"].acted
    assert "failed" in answers["turn"].reason
    assert not answers["flatten"].acted
    assert "failed" in answers["flatten"].reason
    assert result.size > 0


def test_page_models_decide_availability_when_they_are_made(tmp_path: Path) -> None:
    """
    Given  a store with the page models and one without
    When   PageModels is made over each
    Then   can_turn and can_flatten are true for the first and false for the second
    """
    full = PageModels(fake_page_models_store(tmp_path / "a"))
    empty = PageModels(fake_page_models_store(tmp_path / "b", installed=False))

    assert full.can_turn and full.can_flatten
    assert not empty.can_turn and not empty.can_flatten
