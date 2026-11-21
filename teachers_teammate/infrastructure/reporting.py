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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..domain.run_summary import FileOutcome

if TYPE_CHECKING:
    from .ollama_utils import WaitDetail

OnFileStartedFn = Callable[[str, str, int, int], None]  # (source_id, name, idx, total)
OnStageStartedFn = Callable[[str, str], None]  # (source_id, stage)
OnOcrDoneFn = Callable[[str, str], None]  # (source_id, name)
OnStageDetailFn = Callable[[str, "WaitDetail"], None]  # (source_id, what the server is doing)
OnFirstTokenFn = Callable[[str], None]  # (source_id)
OnFileDoneFn = Callable[[FileOutcome], None]


@dataclass(frozen=True)
class KeepAwakeState:
    """Whether the computer is kept from sleeping during a run, and what the GUI should say."""

    enabled: bool
    """The teacher asked for it (``Config.keep_awake``)."""
    held: bool = False
    """The system agreed; ``False`` with ``enabled`` means the computer may sleep anyway."""
    available: bool = True
    """This computer has a way to ask for it; ``False``: nothing was requested (WSL, a platform
    without a tool), so the system did not refuse either."""
    on_battery: bool | None = None
    """Running on battery power; ``None`` when there is no battery or it cannot be read."""


BATTERY_WARNING = "Running on battery: plug in the charger, and keep the lid open."
"""What the banner and the main window say when a run starts on battery power."""


OnKeepAwakeFn = Callable[[KeepAwakeState], None]


@dataclass(frozen=True)
class ProgressCallbacks:
    """Structured per-file progress hooks for adapters (GUI); every hook is optional.

    ``on_file_started(source_id, name, idx, total)`` fires at the start of each file.
    ``on_stage_started(source_id, stage)`` fires when a file enters the ``"preprocessing"``,
    ``"ocr"`` or ``"correction"`` stage (not when that stage is served from the cache).
    ``on_ocr_done(source_id, name)`` fires after OCR, before correction.
    ``on_file_done(outcome)`` fires with a :class:`FileOutcome` after a file completes
    (saved, failed, skipped or stopped).
    ``on_keep_awake(state)`` fires once at the start of a run with a :class:`KeepAwakeState`.
    ``on_stage_detail(source_id, detail)`` fires while a request to Ollama has no answer yet,
    with what the server is doing instead.  ``on_first_token(source_id)`` fires when the model
    begins answering, which is where the run's clock starts.
    """

    on_file_started: OnFileStartedFn | None = None
    on_stage_started: OnStageStartedFn | None = None
    on_ocr_done: OnOcrDoneFn | None = None
    on_file_done: OnFileDoneFn | None = None
    on_keep_awake: OnKeepAwakeFn | None = None
    on_stage_detail: OnStageDetailFn | None = None
    on_first_token: OnFirstTokenFn | None = None


@runtime_checkable
class Reporter(Protocol):
    """Sink for the pipeline's progress narrative, its warnings and its errors."""

    def status(self, line: str) -> None:
        """Report a normal progress line."""

    def warn(self, line: str) -> None:
        """Report an advisory/warning line."""

    def error(self, line: str) -> None:
        """Report a line about something that went wrong."""


class StdoutReporter:
    """Default reporter: ``status`` → stdout, ``warn``/``error`` → stderr.

    ``error`` adds the ``ERROR:`` prefix the callers used to write themselves.
    """

    def status(self, line: str) -> None:
        print(line, flush=True)

    def warn(self, line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    def error(self, line: str) -> None:
        print(f"ERROR: {line}", file=sys.stderr, flush=True)


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

    def error(self, line: str) -> None:
        self._sink(f"ERROR: {line}")
