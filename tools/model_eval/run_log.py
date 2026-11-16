"""The log of one ``run``: one JSON line per event, so a night can be read afterwards.

A run writes its rows to the results files and its progress to the screen, and neither survives
the terminal: the rows say what was scored, not what was skipped or how long a text took, and the
screen is gone by the morning.  The log is the third record, appended as the run goes, so a run
that was interrupted — Ctrl-C is how a night is paused — still says where it stopped.

One line per event, each with the UTC time it was written: ``complete``, ``session``, ``smoke``,
``row``, and then one of ``finished``, ``refused`` or ``interrupted``.  The file is opened for
every line, so a line is on disk before the next request is sent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


def utc_now() -> str:
    """Return the current time as the log records it: ISO 8601, in UTC, to the second."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class RunLog:
    """One JSON line per event of a run, appended to *path*.

    Args:
        path: The file to append to; its directory is created when it does not exist.
        now: The clock, so a test writes a fixed time; :func:`utc_now` when not given.
    """

    def __init__(self, path: Path, *, now: Callable[[], str] | None = None) -> None:
        self.path = path
        self._now = utc_now if now is None else now
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **fields: Any) -> None:
        """Append one line: the time, the event and whatever the event carries."""
        line = {"time": self._now(), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")


__all__ = ["RunLog", "utc_now"]
