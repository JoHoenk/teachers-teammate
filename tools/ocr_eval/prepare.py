"""The app's own image preparation, one preset at a time, with what every step measured.

A run prepares a page with ``infrastructure/preprocessing``'s own steps in the app's own order
(`auto_steps`), and takes exactly one of them out for a ``R-<step>`` preset.  Nothing is copied
from the app: a preset the tool cannot build is a preset the app does not have.  The steps are
built once per run, as a run of the app builds them, so the page models load once.

The steps' reasons are kept for the record, because a step that silently does nothing (a page
model that is not downloaded, a tilt below the threshold) reads like any other run.  A reason is
cut at its first ``": "``: what follows is a message from outside the app — a library's, which can
hold a path — while the app's own measurements never hold one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from teachers_teammate.domain.data_location import ENGINE_PPOCR
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.preprocessing.auto import Measure, Output, auto_steps
from teachers_teammate.infrastructure.preprocessing.steps import (
    Step,
    StepContext,
    StepPipeline,
    StepResult,
)

from .presets import Preset
from .records import MEASUREMENT_LIMIT, StepTrace


@dataclass
class _Traced:
    """One step, recording what it did on the page being prepared."""

    step: Step
    traces: list[StepTrace] = field(default_factory=list)

    @property
    def name(self) -> str:
        """The step's name, as ``preprocess_steps`` lists it."""
        return self.step.name

    def apply(self, image: Any, ctx: StepContext) -> StepResult:
        """Run the step and keep its answer."""
        result = self.step.apply(image, ctx)
        self.traces.append(StepTrace(self.step.name, result.acted, measured(result.reason)))
        return result


def measured(reason: str) -> str:
    """Return *reason* as a record may hold it: the app's own phrase and numbers."""
    head, _, _tail = reason.partition(": ")
    return head[:MEASUREMENT_LIMIT]


@dataclass(frozen=True)
class Prepared:
    """One page, prepared, and what the preparation did to it."""

    image: Path
    """The copy the engine reads.  The caller keeps the temporary folder alive."""
    steps: tuple[StepTrace, ...]


def leave_outs(store: ModelStore) -> tuple[str, ...]:
    """Return the steps a ``R-<step>`` preset may leave out, by their name, in the app's order.

    Every step of the automatic preparation but the one that only measures the page and the one
    that hands it over in the engine's colour (which ``R-colour`` is for).
    """
    return tuple(
        step.name
        for step in auto_steps(ENGINE_PPOCR, store)
        if not isinstance(step, (Measure, Output))
    )


def steps_of(preset: Preset, *, engine: str, store: ModelStore) -> list[Step]:
    """Return the app's steps for *preset*: its own list, one step taken out, or no step at all.

    Raises:
        ValueError: when the preset leaves out a step the app's list does not have, which would
            otherwise be a preset that measures nothing.
    """
    if preset.method == "none":
        return []
    if preset.without and preset.without not in leave_outs(store):
        raise ValueError(
            f"{preset.name}: no step of the automatic preparation is called {preset.without}; "
            f"name one of {', '.join(leave_outs(store))}"
        )
    steps: list[Step] = list(auto_steps(engine, store))
    if preset.colour:
        steps = [Output(gray=False) if isinstance(step, Output) else step for step in steps]
    return [step for step in steps if step.name != preset.without]


class Preparation:
    """The steps of one preset, built once, and run page by page."""

    def __init__(self, steps: Sequence[Step]) -> None:
        self._steps = [_Traced(step) for step in steps]

    @classmethod
    def of(cls, preset: Preset, *, engine: str, store: ModelStore) -> Preparation:
        """Return the preparation of *preset* for *engine*.

        Raises:
            ValueError: for a preset that leaves out a step the app does not have.
        """
        return cls(steps_of(preset, engine=engine, store=store))

    def prepare(self, page: Path, tmp_dir: Path) -> Prepared:
        """Prepare *page* into *tmp_dir* and return the copy and what each step did to it.

        Raises:
            PipelineInputError: if the page cannot be read.
        """
        for step in self._steps:
            step.traces.clear()
        prepared, _acted = StepPipeline(self._steps, tmp_dir=tmp_dir).preprocess(page)
        return Prepared(
            image=prepared,
            steps=tuple(trace for step in self._steps for trace in step.traces),
        )
