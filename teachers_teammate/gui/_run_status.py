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
from ..texts import run
from ..texts.review import format_duration

#: Files whose own pace is blended with the estimate.  One file says little about a class set,
#: and a first file that met a cold model would otherwise promise a time the run cannot keep.
BLEND_FILES = 3

#: The share of its expected time a running file counts for before it is over.
_FILE_CAP = 0.95


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
        text = run.run_failed_count(len(summary.failed)) if summary.failed else run.RUN_FAILED
        return RunStatusText(text, text, RunTone.WARNING, complete=False)

    if status is RunStatus.STOPPED:
        total = done + len(summary.skipped) + len(summary.stopped) + len(summary.not_started)
        text = run.run_stopped(
            done,
            total,
            skipped=len(summary.skipped),
            unfinished=len(summary.stopped),
            not_started=len(summary.not_started),
        )
        return RunStatusText(text, text, RunTone.INFO, complete=False)

    tray = run.run_done_tray(done, len(summary.skipped))
    return RunStatusText(run.RUN_DONE, tray, RunTone.INFO, complete=True)


def time_left_note(seconds: float) -> str:
    """Return the note the progress bar carries: ``"about 40 min left"``."""
    return run.time_left(format_duration(seconds))


def time_left_tooltip(low_s: float, high_s: float) -> str:
    """Return the tooltip behind the note: the range the figure comes from."""
    return run.time_left_range(format_duration(low_s), format_duration(high_s))


@dataclass(frozen=True)
class RunProgress:
    """Where a running run is at one moment: what the bar and the Check strip both show.

    One snapshot per tick, handed to both displays, so the two figures can never disagree.
    """

    fraction: float | None = None
    """How much of the run is done, from 0 to 1, or ``None`` when nothing is known."""
    left: TimeRange | None = None
    """How long the rest is expected to take, or ``None`` when nothing is known."""

    @property
    def note(self) -> str:
        """Return the note the progress bar carries, ``""`` while nothing is known."""
        return time_left_note(self.left.typical_s) if self.left is not None else ""

    @property
    def tooltip(self) -> str:
        """Return the tooltip behind the note, ``""`` while nothing is known."""
        if self.left is None:
            return ""
        return time_left_tooltip(self.left.low_s, self.left.high_s)


@dataclass
class RunPace:
    """How fast the current run is going, and how much of it is left.

    Before the first file is done the estimate comes from the store: the speed test or earlier
    runs.  After that the run's own pace takes over, blended with the estimate for the first
    :data:`BLEND_FILES` files.  Nothing known at all gives no figure, which is better than a
    guess the teacher would plan around.
    """

    estimate: TimeRange | None = None
    """Seconds of a file's first page from the store, or ``None`` when nothing was measured."""
    further: TimeRange | None = None
    """Seconds each further page of a file adds; ``None`` when it costs what the first does."""
    _done_s: list[float] = field(default_factory=list)
    _done_pages: list[int] = field(default_factory=list)
    _files_total: int = 0
    _pages_total: int = 0
    _work_started_at: float | None = None
    _file_pages: int = 1

    def set_files(self, files: int) -> None:
        """Say how many files the run has."""
        self._files_total = max(0, files)

    def set_pages(self, pages: int) -> None:
        """Say how many pages all the files of the run have, when that is known."""
        self._pages_total = max(0, pages)

    def file_started(self, pages: int = 1) -> None:
        """Say that a file of *pages* pages is next; its time counts from :meth:`work_started`."""
        self._work_started_at = None
        self._file_pages = max(1, pages)

    def work_started(self, now: float) -> None:
        """Say that the running file's first real step (preparing, reading, proofreading) began.

        *now* is a monotonic clock reading.  What comes before (cached steps, a model still
        loading) does not count, so the bar does not run ahead; later steps change nothing.
        """
        if self._work_started_at is None:
            self._work_started_at = now

    def file_done(self, seconds: float, pages: int) -> None:
        """Record one finished file: the *seconds* it took and the *pages* it had."""
        self._done_s.append(max(0.0, seconds))
        self._done_pages.append(max(1, pages))
        self._work_started_at = None

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

    def per_page(self) -> TimeRange | None:
        """Return the seconds one page is expected to take, or ``None`` when nothing is known."""
        own = self.own_per_page()
        estimate = self._estimate_per_page()
        if own is None:
            return estimate
        if estimate is None:
            return TimeRange(own, own)
        weight = min(1.0, len(self._done_s) / BLEND_FILES)
        return TimeRange(
            weight * own + (1 - weight) * estimate.low_s,
            weight * own + (1 - weight) * estimate.high_s,
        )

    def _estimate_per_page(self) -> TimeRange | None:
        """Return the store's seconds per page of this run's files, or ``None``.

        Once the page counts are known, what a file pays once is spread over its pages; until
        then every page is charged as a first page.
        """
        if self.estimate is None or self.further is None:
            return self.estimate
        if self._pages_total <= 0 or self._files_total <= 0:
            return self.estimate
        once = self.estimate - self.further
        return self.further + once.scaled(self._files_total / self._pages_total)

    def _credit(self, now: float, per_page_s: float) -> float:
        """Return the seconds of the running file that count as done at *now*.

        The file's own time is held back at ``_FILE_CAP`` of what it is expected to take: a file
        that runs long must not drive the figure to zero before it is over.
        """
        if self._work_started_at is None:
            return 0.0
        elapsed = max(0.0, now - self._work_started_at)
        return min(elapsed, _FILE_CAP * self._file_pages * per_page_s)

    def time_left(self, now: float | None = None) -> TimeRange | None:
        """Return how long the rest of the run is expected to take, or ``None``.

        With *now* the time the running file has already used is taken off, so the figure
        counts down between two events of the run.
        """
        pages = self.pages_left()
        per_page = self.per_page()
        if pages <= 0 or per_page is None:
            return None
        left = per_page.scaled(pages)
        if now is None:
            return left
        credit = self._credit(now, per_page.typical_s)
        return TimeRange(max(0.0, left.low_s - credit), max(0.0, left.high_s - credit))

    def progress(self, now: float) -> float | None:
        """Return how much of the run is done at *now*, from 0 to 1, or ``None`` when unknown.

        Time based: the seconds of the files done plus those of the running one, over those plus
        what is expected for the rest.  ``None`` without an estimate or a page count, so the
        window falls back to counting files.
        """
        per_page = self.per_page()
        if per_page is None:
            return None
        pages = self.pages_left()
        done_s = sum(self._done_s)
        total = done_s + pages * per_page.typical_s
        if total <= 0:
            return None
        return min(1.0, (done_s + self._credit(now, per_page.typical_s)) / total)

    def snapshot(self, now: float) -> RunProgress:
        """Return where the run is at *now*: the bar's fraction and the time left.

        The one place a display gets its figures from, so the bar and the Check strip show the
        same moment of the same run.
        """
        return RunProgress(fraction=self.progress(now), left=self.time_left(now))
