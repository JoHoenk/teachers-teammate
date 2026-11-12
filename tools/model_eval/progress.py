"""How far a long run is: a bar, the position, the elapsed time and a rough time left.

A gate-1 run is 126 requests of up to four minutes each, and a comparison run is a few hundred
texts; without a position, an evening of waiting says nothing about whether the run is half done
or stuck on one request.  The display goes to **stderr**, so ``> record.txt`` keeps the record
clean, and it is one line rewritten in place on a terminal and one plain line per request when
the output is redirected.

The estimate is rough on purpose: models differ in speed by a factor of ten, so the time left is
shown in minutes and never in seconds.  Seconds are shown for the one figure that is exact — how
long the current request has been waiting.

The module is pure apart from the stream and the waiting thread: the clock is injected, so a test
moves time instead of sleeping, and :meth:`Progress.tick` is what the thread calls.
"""

from __future__ import annotations

from collections.abc import Callable
import sys
import threading
import time
from typing import TextIO

STYLES: tuple[str, ...] = ("auto", "lines", "off")
"""What ``--progress`` accepts: ``auto`` picks by the stream, ``lines`` prints one line per
request, ``off`` prints nothing."""

REDRAW_SECONDS = 5.0
"""How often the waiting display redraws on a terminal."""

WAITING_SECONDS = 60.0
"""How often it prints a "still waiting" line when the output is redirected."""

BAR_WIDTH = 20
"""How many cells the bar has."""

_FILLED = "#"
_EMPTY = "."


def _minutes(seconds: float) -> str:
    """Return *seconds* as a rough duration in minutes, never in seconds."""
    minutes = round(seconds / 60)
    return "<1 min" if minutes < 1 else f"{minutes} min"


def _is_terminal(stream: TextIO) -> bool:
    """Whether *stream* is a terminal, so one line can be rewritten in place."""
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _resolve(style: str, stream: TextIO) -> str:
    """Return the style to use: ``auto`` is a bar on a terminal and lines otherwise."""
    if style != "auto":
        return style
    return "bar" if _is_terminal(stream) else "lines"


class Progress:
    """How far a long run is, and how long the request it waits for has been running.

    One request at a time: :meth:`start` names the request that begins, :meth:`finish` counts it
    as done, and :meth:`grow` adds requests the total could not know in advance (a second round,
    a retry, a session that grew).  The waiting display is a daemon thread that :meth:`start`
    starts and :meth:`finish` stops; :meth:`tick` is what it calls, so a test drives the display
    with a fake clock instead of sleeping.

    Args:
        total: How many requests the run is expected to make.
        stream: Where the display goes; stderr by default, so a redirected record stays clean.
        clock: Returns the current monotonic time; injected so a test can move it.
        style: ``auto`` (a bar on a terminal, one line per request otherwise), ``lines`` or
            ``off``.

    Raises:
        ValueError: if *style* is not one of :data:`STYLES`.
    """

    def __init__(
        self,
        total: int,
        *,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
        style: str = "auto",
    ) -> None:
        if style not in STYLES:
            raise ValueError(f"progress style {style!r} is not one of {', '.join(STYLES)}")
        self._stream = sys.stderr if stream is None else stream
        self._clock = clock
        self._style = _resolve(style, self._stream)
        self._total = max(total, 0)
        self._done = 0
        self._started: float | None = None
        self._label = ""
        self._request_started: float | None = None
        self._last_waiting = 0.0
        self._drawn = 0
        self._closed = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def total(self) -> int:
        """How many requests the run is expected to make."""
        return self._total

    @property
    def done(self) -> int:
        """How many requests are finished."""
        return self._done

    def grow(self, n: int) -> None:
        """Add *n* requests to the total: a second round or a retry the total could not know."""
        self._total += max(n, 0)
        self._closed = False

    def skip(self, n: int) -> None:
        """Take *n* requests off the total: a setup that was skipped will never send them.

        The total never falls below the requests already done, so the position stays true, and a
        run that ends this way still closes its line and shows a full bar.
        """
        if n <= 0 or self._closed:
            return
        self._total = max(self._total - n, self._done)
        if self._style == "bar" and self._started is not None:
            self._redraw()
        self._close()

    def start(self, label: str) -> None:
        """Say that a request starts: *label* names it, and the waiting display begins."""
        now = self._clock()
        if self._started is None:
            self._started = now
        self._label = label
        self._request_started = now
        self._last_waiting = now
        self._closed = False
        if self._style == "off":
            return
        if self._style == "bar":
            self._redraw()
        self._begin_waiting()

    def finish(self) -> None:
        """Say that the request named by the last :meth:`start` is done."""
        self._end_waiting()
        self._done += 1
        self._request_started = None
        if self._style == "off":
            return
        if self._style == "lines":
            self._write(self._line(self._clock()))
            return
        self._redraw()
        self._close()

    def _close(self) -> None:
        """End the rewritten line once the run is complete, so the next output starts clean."""
        if (
            self._style == "bar"
            and not self._closed
            and self._started is not None
            and (self._done >= self._total)
        ):
            self._closed = True
            self._write("")

    def tick(self) -> None:
        """Redraw the waiting display: what the thread calls, and what a test calls by hand."""
        if self._style == "off" or self._request_started is None:
            return
        now = self._clock()
        if self._style == "lines":
            if now - self._last_waiting < WAITING_SECONDS:
                return
            self._last_waiting = now
            waited = now - self._request_started
            self._write(f"{self._position()}  {self._label} · still waiting {waited:.0f}s")
            return
        self._redraw()

    def _line(self, now: float) -> str:
        """Return the display line: the bar, the position, the label and the times."""
        parts = [self._position(), self._label]
        if self._started is not None:
            parts.append(_minutes(now - self._started))
        left = self._left(now)
        if left is not None:
            parts.append(f"~{_minutes(left)} left")
        if self._request_started is not None:
            parts.append(f"waiting {now - self._request_started:.0f}s")
        body = "  ".join(part for part in parts if part)
        return f"{self._bar()} {body}" if self._style == "bar" else body

    def _position(self) -> str:
        """Return the position of the run as ``done/total``."""
        return f"{self._done}/{self._total}"

    def _left(self, now: float) -> float | None:
        """Return the rough seconds left, or ``None`` when there is nothing to estimate from."""
        if self._started is None or self._done <= 0 or self._done >= self._total:
            return None
        return (now - self._started) / self._done * (self._total - self._done)

    def _bar(self) -> str:
        """Return the bar: one cell per twentieth of the run, filled up to the position."""
        share = self._done / self._total if self._total else 1.0
        filled = min(BAR_WIDTH, round(BAR_WIDTH * share))
        return f"[{_FILLED * filled}{_EMPTY * (BAR_WIDTH - filled)}]"

    def _begin_waiting(self) -> None:
        """Start the daemon thread that redraws while a request runs."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._wait_loop, name="progress", daemon=True)
        self._thread.start()

    def _end_waiting(self) -> None:
        """Stop the waiting thread, if it runs."""
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=1.0)

    def _wait_loop(self) -> None:
        """Redraw every few seconds until :meth:`finish` stops the thread."""
        interval = REDRAW_SECONDS if self._style == "bar" else WAITING_SECONDS
        while not self._stop.wait(interval):
            self.tick()

    def _write(self, text: str, *, end: str = "\n") -> None:
        """Write one line to the stream and flush it, so a redirected file shows it at once."""
        self._stream.write(f"{text}{end}")
        self._stream.flush()

    def _redraw(self) -> None:
        """Rewrite the one line of a terminal, padded over what was there before."""
        line = self._line(self._clock())
        padded = line.ljust(self._drawn)
        self._drawn = len(line)
        self._stream.write(f"\r{padded}")
        self._stream.flush()


__all__ = ["BAR_WIDTH", "REDRAW_SECONDS", "STYLES", "WAITING_SECONDS", "Progress"]
