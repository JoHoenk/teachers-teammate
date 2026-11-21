"""What a run of the main window is doing, as one object.

The window shows a run, the Check tab shows the same run, and a controller asks about it
instead of reading a dozen attributes off the window.  Qt-free, so the state a run goes
through is tested without a window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..config import Config
from ..domain.model_catalog import short_model_name
from ..domain.ollama_discovery import WaitDetail, WaitKind
from ..domain.performance_estimate import TimeRange
from ..domain.stages import (
    PROGRESS_STAGE_CORRECTION,
    PROGRESS_STAGE_OCR,
    PROGRESS_STAGE_PREPROCESSING,
)
from ..texts import check as check_text
from ._run_status import RunPace, RunProgress
from ._types import FileDoneEvent


def wait_detail_text(detail: WaitDetail) -> str:
    """Return what to say about a request to Ollama that has no answer yet."""
    if detail.kind is WaitKind.BUSY:
        return check_text.RUN_WAITING_FOR_MODEL.format(other=detail.other)
    if detail.kind is WaitKind.LOADING:
        return check_text.RUN_LOADING_MODEL.format(model=detail.model)
    if detail.kind is WaitKind.QUEUED:
        return check_text.RUN_QUEUED_FOR_MODEL.format(other=detail.other, model=detail.model)
    return ""


class StopStage(Enum):
    """Where the two-step Stop button is: the first click finishes the document being
    processed, the second stops at once."""

    IDLE = "idle"
    RUNNING = "running"
    AFTER_FILE = "after_file"
    NOW = "now"


STOP_TEXT = "Stop"
STOP_TOOLTIP = "Finish the document being processed, then stop."
STOP_NOW_TEXT = "Stop now"
STOP_NOW_TOOLTIP = (
    "Stop at once. The document being processed is left unfinished; what is already done "
    "is kept and reused next time."
)
STOPPING_SUFFIX = " — stopping after this document"
STOPPING_NOW_TEXT = "Stopping…"
#: How often the bar and the time left follow the clock while a run is going.
PROGRESS_TICK_MS = 1000
#: Steps of the bar once it follows the time of the run.
PROGRESS_RESOLUTION = 1000

#: What the progress line says while a step of the run's file is on.
_STAGE_PROGRESS_LABEL = {
    PROGRESS_STAGE_PREPROCESSING: check_text.PROGRESS_PREPROCESSING,
    PROGRESS_STAGE_OCR: check_text.PROGRESS_OCR,
    PROGRESS_STAGE_CORRECTION: check_text.PROGRESS_CORRECTING,
}

#: What the Check tab's run strip says while a step of the run's file is on.
_RUN_STEP_TEXT = {
    PROGRESS_STAGE_PREPROCESSING: check_text.RUN_PREPARING,
    PROGRESS_STAGE_OCR: check_text.RUN_READING,
    PROGRESS_STAGE_CORRECTION: "",  # empty: the strip names the proofreading model
}


@dataclass
class RunSession:
    """One run, from its first document to the closing message."""

    stop_stage: StopStage = StopStage.IDLE
    """Where the two-step Stop button is."""

    progress_base: str = ""
    """The progress text without the time left and the "stopping" note."""

    pace: RunPace = field(default_factory=RunPace)
    """How fast the run goes and how much of it is left."""

    snapshot: RunProgress = field(default_factory=RunProgress)
    """Where the run is at the last tick: the bar and the Check strip both read this."""

    pages: dict[str, int] = field(default_factory=dict)
    """Pages of the files of the current run, by source id; filled in the background."""

    time_driven: bool = False
    """The bar follows the time of the run instead of counting files."""

    source_ids: list[str] = field(default_factory=list)
    """The documents of the running batch, in queue order."""

    pending: set[str] = field(default_factory=set)
    """The documents whose own result has not arrived yet."""

    current: str = ""
    """The document the run is working on right now, or ``""``."""

    model: str = ""
    """The model that proofreads, for the Check tab's run strip (``""``: no proofreading)."""

    step: str = ""
    """What the run does with the current document, as the strip says it."""

    file_label: str = ""
    """``"[3/12] essay.pdf"``: the file the run is on, for the progress line."""

    stage_step: str = ""
    stage_label: str = ""
    """What the running stage says about itself, which a wait note replaces."""

    wait_shown: bool = False
    """Whether the strip and the progress line carry a note about the server's wait."""

    waits_for_ollama: bool = False
    """Whether the run's proofreading waits for an Ollama server, so its clock starts at the
    first token instead of at the request."""

    pace_waits_for_first_token: bool = False
    """Whether the pace is holding its clock for that first token."""

    steps_per_file: int = 1
    """How many steps of the bar one file has: reading, and proofreading when it runs."""

    from_check: bool = False
    """A run was asked for in the Check tab: the tab stays when the run starts."""

    names: list[str] = field(default_factory=list)
    ocr_times: list[float] = field(default_factory=list)
    correction_times: list[float] = field(default_factory=list)
    """What each finished file is called and how long its stages took, for the chart."""

    def start(self, config: Config, source_ids: list[str]) -> None:
        """Begin a run over *source_ids*, taken with *config*."""
        self.source_ids = list(source_ids)
        self.pending = set(source_ids)
        self.current = ""
        self.model = (
            short_model_name(config.effective_correction_model) if config.correction_enabled else ""
        )
        self.steps_per_file = 2 if config.correction_enabled else 1
        self.waits_for_ollama = config.correction_enabled and config.correction_provider == "ollama"
        self.stop_stage = StopStage.RUNNING
        self.progress_base = f"Selected {len(source_ids)} file(s) — Starting…"
        self.step = ""
        self.file_label = ""
        self.stage_step = ""
        self.stage_label = ""
        self.wait_shown = False
        self.pace_waits_for_first_token = False
        self.names = []
        self.ocr_times = []
        self.correction_times = []
        self.time_driven = False
        self.pages = {}

    def start_pace(self, estimate: TimeRange | None, files: int) -> None:
        """Start the time-left figure at *estimate* per page over *files* documents."""
        self.pace = RunPace(estimate)
        self.pace.set_files(files)
        self.snapshot = RunProgress()

    @property
    def steps_total(self) -> int:
        """How many steps the bar counts: every file, times the steps of one file."""
        return len(self.source_ids) * self.steps_per_file

    def steps_done(self) -> int:
        """How many steps of the bar the finished files account for."""
        return len(self.names) * self.steps_per_file

    def file_started(self, source_id: str, name: str, idx: int, total: int) -> None:
        """Take the file the run works on now."""
        self.pace.file_started(self.pages.get(source_id, 1))
        self.file_label = f"[{idx}/{total}] {name}"
        self.progress_base = f"{self.file_label} — starting…"
        self.current = source_id
        self.step = ""
        self.stage_step = ""
        self.stage_label = ""
        self.wait_shown = False

    def stage_started(self, name: str, stage: str, idx: int, total: int, now: float) -> str | None:
        """Take the step *stage* of the file the run is on; return the progress text to show.

        A proofreading on an Ollama server may wait for the model: its clock starts at the first
        token (:meth:`first_token`), so neither the bar nor the time left move during a wait.
        ``None`` means the progress line keeps what it says.
        """
        self.pace_waits_for_first_token = bool(
            stage == PROGRESS_STAGE_CORRECTION and self.waits_for_ollama
        )
        if not self.pace_waits_for_first_token:
            self.pace.work_started(now)
        self.file_label = f"[{idx}/{total}] {name}"
        label = _STAGE_PROGRESS_LABEL.get(stage)
        if label is not None:
            self.stage_label = label
        self.step = _RUN_STEP_TEXT.get(stage, self.step)
        self.stage_step = self.step
        self.wait_shown = False
        if label is None:
            return None
        return f"{self.file_label} — {label}"

    def stage_detail(self, source_id: str, text: str) -> str | None:
        """Say what Ollama is doing with the request while the answer has not begun.

        *text* is empty when the detail says nothing a teacher would read.
        """
        if source_id != self.current or not text:
            return None
        self.wait_shown = True
        self.step = text
        return f"{self.file_label} — {text}"

    def first_token(self, source_id: str, now: float) -> str | None:
        """The model began answering: a wait note goes, and the proofreading's clock starts."""
        if source_id != self.current:
            return None
        if self.pace_waits_for_first_token:
            self.pace_waits_for_first_token = False
            self.pace.work_started(now)
        if not self.wait_shown:
            return None
        self.wait_shown = False
        self.step = self.stage_step
        if not self.stage_label:
            return None
        return f"{self.file_label} — {self.stage_label}"

    def ocr_done(self) -> None:
        """The reading is over: what goes on now is the proofreading."""
        self.step = ""
        self.stage_step = ""
        self.stage_label = check_text.PROGRESS_CORRECTING
        self.wait_shown = False

    def file_done(self, event: FileDoneEvent) -> None:
        """Record one finished file: what it is called, how long it took, how far the bar is."""
        outcome = event.outcome
        source_id = outcome.source_id
        self.names.append(outcome.name)
        self.ocr_times.append(event.ocr_s)
        self.correction_times.append(event.correction_s)
        self.pace.file_done(event.total_s, self.pages.get(source_id, 1))
        self.pending.discard(source_id)
        if self.current == source_id:
            self.current = ""

    def finished(self) -> None:
        """The run is over: nothing of it is left to show."""
        self.stop_stage = StopStage.IDLE
        self.pace = RunPace()
        self.snapshot = RunProgress()
        self.pace_waits_for_first_token = False
        self.source_ids = []
        self.pending = set()
        self.current = ""
