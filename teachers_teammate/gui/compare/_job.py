"""The one job of the compare window: ask the ticked models, one after the other."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QObject, Signal

from ...application.model_comparison import (
    Candidate,
    CompareSource,
    JobHooks,
    ModelComparisonService,
)
from ...config import Config
from ...exceptions import RunStoppedError
from ...interfaces import Reporter
from ...messages import message_of
from ...texts import compare as txt
from .._long_job import LongJobThread

__all__ = ["CompareJob"]


class CompareJob(LongJobThread):
    """Ask each ticked model about one text, and report every answer as it comes.

    A model at a time, as a comparison must: the next answer starts cold, and a stop ends the
    job between two models, with nothing half stored.
    """

    #: One model's attempt is over: a result, the document's own answer, a stop or a failure.
    row_done = Signal(object)

    #: What the window's progress line says: the model being asked, and what it is doing.
    line = Signal(str)

    def __init__(
        self,
        service: ModelComparisonService,
        config: Config,
        source: CompareSource,
        candidates: Sequence[Candidate],
        *,
        repeat: int = 0,
        reporter: Reporter,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._config = config
        self._source = source
        self._candidates = tuple(candidates)
        self._repeat = repeat
        self._reporter = reporter

    def work(self) -> None:
        """Ask the models in order; a stop before the last one answered ends the job unfinished."""
        attempts = self._service.run(
            self._config,
            self._source,
            self._candidates,
            repeat=self._repeat,
            hooks=JobHooks(
                stop_event=self.stop_event,
                reporter=self._reporter,
                on_start=lambda candidate: self.line.emit(txt.asking(candidate.model)),
                on_answer=self.row_done.emit,
            ),
        )
        if len(attempts) < len(self._candidates) or any(ran.stopped for ran in attempts):
            raise RunStoppedError

    def error_text(self, exc: BaseException) -> str:
        """Return the sentence this window shows for a failure that ended the whole job."""
        return str(message_of(exc))
