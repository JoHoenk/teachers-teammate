"""The words a finished run is announced with: progress bar text, tray message, icon.

Pure function over :class:`~teachers_teammate.domain.run_summary.RunSummary`, so the wording is
tested without a window.  Failed beats stopped beats done: a run with one failed file says so
even when it was also stopped.

The same module holds :class:`RunPace`, which says how much of a *running* run is left: the
progress bar shows one figure, the tooltip the range behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..domain.performance_estimate import TimeRange
from ..domain.run_summary import RunStatus, RunSummary
from ._user_text import format_duration

#: Files whose own pace is blended with the estimate.  One file says little about a class set,
#: and a first file that met a cold model would otherwise promise a time the run cannot keep.
BLEND_FILES = 3

#: The note the progress bar carries while a run is going.
TIME_LEFT = "about {left} left"
#: The tooltip behind that note: the range the figure comes from.
TIME_LEFT_TOOLTIP = "Between {low} and {high} left, at the pace of this run."


class RunTone(Enum):
    """How serious the announcement is (the window maps it to a tray icon)."""

    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True)
class RunStatusText:
    """What to show when a run ended."""

    progress: str
    """Text in the progress bar."""
    tray: str
    """Text of the notification."""
    tone: RunTone
    complete: bool
    """Whether the bar goes to 100 %: only a run in which nothing was left undone."""


def describe_run(summary: RunSummary) -> RunStatusText:
    """Return the announcement for *summary*."""
    status = summary.status
    done = len(summary.succeeded)

    if status is RunStatus.FAILED:
        text = "Finished with errors"
        if summary.failed:
            text += f" — {len(summary.failed)} failed"
        return RunStatusText(text, text, RunTone.WARNING, complete=False)

    if status is RunStatus.STOPPED:
        total = done + len(summary.skipped) + len(summary.stopped) + len(summary.not_started)
        parts = [f"{done} of {total} done"]
        if summary.skipped:
            parts.append(f"{len(summary.skipped)} skipped")
        if summary.stopped:
            parts.append(f"{len(summary.stopped)} unfinished")
        if summary.not_started:
            parts.append(f"{len(summary.not_started)} not started")
        text = "Stopped — " + ", ".join(parts)
        return RunStatusText(text, text, RunTone.INFO, complete=False)

    tray = f"Done — {done} file(s) processed"
    if summary.skipped:
        tray += f", {len(summary.skipped)} skipped"
    return RunStatusText("Done \u2713", tray, RunTone.INFO, complete=True)


def time_left_note(seconds: float) -> str:
    """Return the note the progress bar carries: ``"about 40 min left"``."""
    return TIME_LEFT.format(left=format_duration(seconds))


def time_left_tooltip(low_s: float, high_s: float) -> str:
    """Return the tooltip behind the note: the range the figure comes from."""
    return TIME_LEFT_TOOLTIP.format(
        low=format_duration(low_s),
        high=format_duration(high_s),
    )


@dataclass
class RunPace:
    """How fast the current run is going, and how much of it is left.

    Before the first file is done the estimate comes from the store: the speed test or earlier
    runs.  After that the run's own pace takes over, blended with the estimate for the first
    :data:`BLEND_FILES` files.  Nothing known at all gives no figure, which is better than a
    guess the teacher would plan around.
    """

    estimate: TimeRange | None = None
    """Seconds per page from the store, or ``None`` when nothing was measured."""
    _done_s: list[float] = field(default_factory=list)
    _done_pages: list[int] = field(default_factory=list)
    _files_total: int = 0
    _pages_total: int = 0

    def set_files(self, files: int) -> None:
        """Say how many files the run has."""
        self._files_total = max(0, files)

    def set_pages(self, pages: int) -> None:
        """Say how many pages all the files of the run have, when that is known."""
        self._pages_total = max(0, pages)

    def file_done(self, seconds: float, pages: int) -> None:
        """Record one finished file: the *seconds* it took and the *pages* it had."""
        self._done_s.append(max(0.0, seconds))
        self._done_pages.append(max(1, pages))

    def pages_left(self) -> int:
        """Return how many pages the files that are not done yet have.

        From the page counts of the whole run when they are known; otherwise from the average
        of the files done so far, which is all a run can say about the rest.
        """
        left = max(0, self._files_total - len(self._done_s))
        if self._pages_total > 0:
            return max(0, self._pages_total - sum(self._done_pages))
        if not self._done_pages:
            return 0
        return round(left * sum(self._done_pages) / len(self._done_pages))

    def own_per_page(self) -> float | None:
        """Return the seconds per page this run is going at, or ``None`` before a file is done."""
        pages = sum(self._done_pages)
        if pages <= 0:
            return None
        return sum(self._done_s) / pages

    def time_left(self) -> TimeRange | None:
        """Return how long the rest of the run is expected to take, or ``None``."""
        pages = self.pages_left()
        if pages <= 0:
            return None
        own = self.own_per_page()
        if own is None:
            return None if self.estimate is None else self.estimate.scaled(pages)
        if self.estimate is None:
            return TimeRange(own * pages, own * pages)
        weight = min(1.0, len(self._done_s) / BLEND_FILES)
        low = weight * own + (1 - weight) * self.estimate.low_s
        high = weight * own + (1 - weight) * self.estimate.high_s
        return TimeRange(low * pages, high * pages)
