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

if TYPE_CHECKING:
    from ..domain.correction_review import CorrectionReview

OnFileStartedFn = Callable[[str, str, int, int], None]  # (source_id, name, idx, total)
OnStageStartedFn = Callable[[str, str], None]  # (source_id, stage)
OnOcrDoneFn = Callable[[str, str], None]  # (source_id, name)


@dataclass(frozen=True)
class FileDoneResult:
    """Outcome of one file, handed to ``ProgressCallbacks.on_file_done``.

    Text strings are empty when the associated stage output was not produced.
    ``message`` is the joined stage warnings on success (empty when every stage succeeded)
    and the error otherwise.
    """

    source_id: str
    name: str
    ok: bool
    message: str
    preview_img: str = ""
    raw_text: str = ""
    correction_text: str = ""
    evaluation_text: str = ""
    text_format: str = "plain"
    """Notation (``"plain"`` or ``"markdown"``) of ``raw_text`` and ``correction_text``."""
    correction_review: CorrectionReview | None = None
    """Explanations and unclear expressions; ``None`` when the file has no review."""
    correction_warning: str = ""
    """Why the file has no review (fallback to corrections-only); empty when there is one."""
    skipped: bool = False
    """The file was left out on purpose (``ok`` is ``False``, ``message`` says why)."""
    stopped: bool = False
    """The run was stopped while this file was processed (``ok`` is ``False``, ``message`` says
    before which step); what was already done is stored and kept."""
    stopped_stage: str = ""
    """With ``stopped``: the step the stop cut short (``"ocr"``, ``"correction"`` or
    ``"evaluation"``); empty when the file did not get as far as telling."""


OnFileDoneFn = Callable[[FileDoneResult], None]


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
    ``on_stage_started(source_id, stage)`` fires when a file enters the ``"preprocessing"``
    or ``"ocr"`` stage (not when OCR is served from the cache).
    ``on_ocr_done(source_id, name)`` fires after OCR, before correction.
    ``on_file_done(result)`` fires with a :class:`FileDoneResult` after a file completes
    (success, failure or stop).
    ``on_keep_awake(state)`` fires once at the start of a run with a :class:`KeepAwakeState`.
    """

    on_file_started: OnFileStartedFn | None = None
    on_stage_started: OnStageStartedFn | None = None
    on_ocr_done: OnOcrDoneFn | None = None
    on_file_done: OnFileDoneFn | None = None
    on_keep_awake: OnKeepAwakeFn | None = None


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
