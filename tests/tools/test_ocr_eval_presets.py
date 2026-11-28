"""The presets a run may name, and the app's preparation they are built into."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
import pytest

from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.preprocessing.auto import auto_steps
from teachers_teammate.infrastructure.preprocessing.steps import StepContext, StepResult
from tools.ocr_eval import prepare, presets
from tools.ocr_eval.records import StepTrace


def test_raw_is_the_app_s_original_page() -> None:
    """Given the preset ``raw``
    When   it is parsed
    Then   no step runs, which is the app's "Original page"
    """
    preset = presets.parse("raw")

    assert preset.method == "none"
    assert preset.without == ""
    assert preset.colour is False


def test_the_automatic_preparation_is_the_app_s_own() -> None:
    """Given the preset ``R``
    When   it is parsed
    Then   it is the app's automatic method, nothing left out
    """
    preset = presets.parse("R")

    assert preset.method == "auto"
    assert preset.without == ""


def test_a_preset_may_leave_one_step_out() -> None:
    """Given ``R-light`` and ``R-deskew``
    When   they are parsed
    Then   each names the step it leaves out
    """
    assert presets.parse("R-light").without == "light"
    assert presets.parse("R-deskew").without == "deskew"


def test_colour_is_kept_for_an_engine_that_would_get_a_gray_page() -> None:
    """Given the preset ``R-colour``
    When   it is parsed
    Then   it is the automatic preparation with colour kept
    """
    assert presets.parse(presets.COLOUR).colour is True


def test_a_name_that_is_no_preset_is_refused() -> None:
    """Given a name that is neither raw nor a preset of the automatic preparation
    When   it is parsed
    Then   it is refused, naming the presets there are
    """
    with pytest.raises(ValueError, match="R-<step>"):
        presets.parse("auto")


def test_the_steps_a_preset_may_leave_out_are_the_app_s_own() -> None:
    """Given the steps the app's automatic preparation runs
    When   the steps a preset may leave out are asked for
    Then   they are each of the app's, in its order, but the one that only measures and the colour
           output
    """
    names = tuple(step.name for step in auto_steps("ppocr", ModelStore()))

    assert prepare.leave_outs(ModelStore()) == tuple(
        name for name in names if name not in ("measure", "grayscale")
    )


def test_a_preset_that_leaves_out_a_step_the_app_lacks_is_refused() -> None:
    """Given a preset that leaves out a step the app has no step for
    When   its steps are built
    Then   it is refused, naming the steps the app does have
    """
    unknown = presets.parse("R-despeckle")

    with pytest.raises(ValueError, match=r"no step of the automatic preparation.*light"):
        prepare.steps_of(unknown, engine="ppocr", store=ModelStore())


def test_a_preset_that_leaves_a_step_out_has_every_other_step() -> None:
    """Given ``R-light``
    When   its steps are built
    Then   they are the app's own, the light step alone taken out
    """
    every = [step.name for step in auto_steps("ppocr", ModelStore())]

    built = prepare.steps_of(presets.parse("R-light"), engine="ppocr", store=ModelStore())

    assert [step.name for step in built] == [name for name in every if name != "light"]


@dataclass
class _CountingStep:
    """A step that acts on every page and says what it measured, after a library's colon."""

    name: str = "light"
    applied: int = 0

    def apply(self, image: Any, ctx: StepContext) -> StepResult:
        """Count the page and hand it on unchanged."""
        self.applied += 1
        return StepResult(image, acted=True, reason="the model failed: /home/anna/models")


def test_a_preparation_runs_its_steps_on_each_page_and_traces_each_page_alone(
    tmp_path: Path,
) -> None:
    """Given one preparation of one step, and two pages
    When   both pages are prepared
    Then   the one step ran on both, each page's trace holds that page's step only, and what a
           library said after the colon is not in it
    """
    step = _CountingStep()
    preparation = prepare.Preparation([step])
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    for page in (first, second):
        Image.new("RGB", (32, 32), "white").save(page)
        (tmp_path / page.stem).mkdir()

    traces = [preparation.prepare(page, tmp_path / page.stem).steps for page in (first, second)]

    assert step.applied == 2
    assert traces == [(StepTrace("light", True, "the model failed"),)] * 2


def test_a_reason_is_cut_at_the_first_colon_whatever_its_words() -> None:
    """Given a step reason that names no failure but carries a library's message after a colon
    When   it is made fit for a record
    Then   only the app's own phrase is kept
    """
    assert prepare.measured("turn skipped: C:\\Users\\anna\\model.onnx") == "turn skipped"
    assert prepare.measured("tilt 0.4°") == "tilt 0.4°"
