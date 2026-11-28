"""What every stage of one file shares: the reporter, the stop, the cache and the language.

A stage takes these once and then only what it does itself.  It raises
:class:`~teachers_teammate.infrastructure.workflow.processing_result.Stopped` when the teacher
pressed "Stop now", and a :class:`~teachers_teammate.exceptions.StageError` when it could not do
its work.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import threading

from ...domain.stages import ProgressStage
from ...exceptions import StageError, StageErrorKind, stop_requested
from ...interfaces import OnStageStartedFn, Reporter
from ...messages import Message, message_of
from ..run_stats import StepSampler
from .cache_service import CacheReconciliationService


def stage_error(
    sentence: Callable[[Message], Message], exc: BaseException, kind: StageErrorKind
) -> StageError:
    """Return the :class:`StageError` that says *sentence* about *exc*, of *kind*."""
    return StageError(sentence(message_of(exc)), kind=kind)


@dataclass(frozen=True)
class StageShared:
    """The parts of one file's processing that every stage asks for."""

    reporter: Reporter
    sampler: StepSampler
    cache_service: CacheReconciliationService
    language: str
    stop_event: threading.Event | None = None
    on_stage_started: OnStageStartedFn | None = None

    def notify(self, file: Path, stage: ProgressStage) -> None:
        """Say that *stage* of *file* has started."""
        if self.on_stage_started:
            self.on_stage_started(str(file.resolve()), stage)

    def stopping(self) -> bool:
        """Whether the teacher pressed "Stop now" (the other Stop only keeps new files away)."""
        return stop_requested(self.stop_event)
