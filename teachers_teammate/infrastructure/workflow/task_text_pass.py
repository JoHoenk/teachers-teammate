"""The reading pass: a run reads its queue once, so the batch can find the print they share.

Printed text repeats in every document of a worksheet; the student's own words do not.  Finding it
takes the transcription of the whole queue, so the run makes two passes — this one keeps every
document up to its transcription, and the pass that follows reads the same files from what it
stored and reports all of them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import logging
from pathlib import Path

from ...domain.task_text import TaskTextFound
from ...exceptions import UNEXPECTED_ERROR_MESSAGE
from ...interfaces import Reporter
from ...messages import log_failure
from ...texts import report as report_text
from .processing_result import ProcessingResult

_logger = logging.getLogger(__name__)

#: Keeps one file up to its transcription; ``None`` when it was read, else what its turn reports.
ReadFn = Callable[[Path], ProcessingResult | None]

#: Finds the printed text the queue shares and stores it with each document.
MarkFn = Callable[[Sequence[Path]], TaskTextFound]

#: Whether the run was asked to stop.
StoppingFn = Callable[[], bool]

#: Says which file the pass reads now: the file, its place in the queue, the queue's length.
StartedFn = Callable[[Path, int, int], None]


@dataclass(frozen=True)
class ReadingPass:
    """What the reading pass leaves the run."""

    found: TaskTextFound | None = None
    """What the detection found; ``None`` when the pass was stopped and nothing was marked."""
    unread: dict[Path, ProcessingResult] = field(default_factory=dict)
    """The files that could not be read, with what their turn reports instead of reading them
    again: a failure, or a file left out."""


def mark_task_text(
    files: Sequence[Path],
    *,
    read: ReadFn,
    mark: MarkFn,
    stopping: StoppingFn,
    reporter: Reporter,
    started: StartedFn | None = None,
) -> ReadingPass:
    """Read *files* first, mark the printed text they share, and say what was found.

    A stop, by either button, ends the pass: the files read so far keep their reading ("stop
    after this file" lets the one being read finish), nothing is marked or proofread, and the
    caller's run reports every file as not started.
    """
    reporter.status(report_text.reading_the_queue(len(files)))
    unread: dict[Path, ProcessingResult] = {}
    for index, file in enumerate(files, start=1):
        if stopping():
            return ReadingPass()
        if started is not None:
            started(file, index, len(files))
        try:
            result = read(file)
        except Exception as exc:  # noqa: BLE001  # the net of the run, as for a file's turn
            log_failure(_logger, f"Unexpected error while reading {file.name}", exc)
            result = ProcessingResult.failure(UNEXPECTED_ERROR_MESSAGE)
        if result is None:
            continue
        if result.stopped:
            return ReadingPass()
        unread[file] = result
    found = mark(files)
    if found.documents:
        reporter.status(report_text.task_text_found(found.documents, len(files), found.worksheets))
    return ReadingPass(found, unread)
