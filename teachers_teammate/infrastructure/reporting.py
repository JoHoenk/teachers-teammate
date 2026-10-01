"""Reporter seam for the user-facing pipeline narrative.

The pipeline and its workflow services emit human-readable progress and warnings
through a :class:`Reporter` instead of printing directly.  This lets each adapter
choose the sink:

* the CLI uses :class:`StdoutReporter` (status → stdout, warnings → stderr),
  preserving the console output exactly;
* the GUI uses :class:`CallbackReporter` to forward every line to its log pane
  via a Qt signal — removing the previous ``redirect_stdout`` coupling.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import sys
from typing import Protocol, runtime_checkable

OnFileStartedFn = Callable[[str, str, int, int], None]  # (source_id, name, idx, total)
OnStageStartedFn = Callable[[str, str], None]  # (source_id, stage)
OnOcrDoneFn = Callable[[str, str], None]  # (source_id, name)
OnFileDoneFn = Callable[[str, str, bool, str, str, str, str, str], None]


@dataclass(frozen=True)
class ProgressCallbacks:
    """Structured per-file progress hooks for adapters (GUI); every hook is optional.

    ``on_file_started(source_id, name, idx, total)`` fires at the start of each file.
    ``on_stage_started(source_id, stage)`` fires when a file enters the ``"preprocessing"``
    or ``"ocr"`` stage (not when OCR is served from the cache).
    ``on_ocr_done(source_id, name)`` fires after OCR, before correction.
    ``on_file_done(source_id, name, ok, message, preview_img, raw_text, correction_text,
    evaluation_text)`` fires after a file completes (success or failure); text strings are
    empty when the associated stage output was not produced.
    """

    on_file_started: OnFileStartedFn | None = None
    on_stage_started: OnStageStartedFn | None = None
    on_ocr_done: OnOcrDoneFn | None = None
    on_file_done: OnFileDoneFn | None = None


@runtime_checkable
class Reporter(Protocol):
    """Sink for the pipeline's progress narrative and advisory warnings."""

    def status(self, line: str) -> None:
        """Report a normal progress line."""

    def warn(self, line: str) -> None:
        """Report an advisory/warning line."""


class StdoutReporter:
    """Default reporter: ``status`` → stdout, ``warn`` → stderr.

    Reproduces the prior ``print()`` behaviour so CLI output is unchanged.
    """

    def status(self, line: str) -> None:
        print(line, flush=True)

    def warn(self, line: str) -> None:
        print(line, file=sys.stderr, flush=True)


class CallbackReporter:
    """Routes every line (status and warning alike) to a single callback.

    Used by the GUI worker to feed both progress and warnings into the log pane.
    """

    def __init__(self, sink: Callable[[str], None]) -> None:
        self._sink = sink

    def status(self, line: str) -> None:
        self._sink(line)

    def warn(self, line: str) -> None:
        self._sink(line)
