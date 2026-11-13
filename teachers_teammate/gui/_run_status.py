"""The words a finished run is announced with: progress bar text, tray message, icon.

Pure function over :class:`~teachers_teammate.domain.run_summary.RunSummary`, so the wording is
tested without a window.  Failed beats stopped beats done: a run with one failed file says so
even when it was also stopped.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..domain.run_summary import RunStatus, RunSummary


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
