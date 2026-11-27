"""Where the lines of a scan stand in the text read from it, made while the pages are read.

With the built-in reader the run's own lines are used.  With another reader the built-in reader
reads each page alongside, on a thread of its own, while the run's reader waits for its answers;
the places are made once both are done.  The places are an addition to the reading: no failure
of theirs, and no stop, costs the reading itself.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
from pathlib import Path
import threading

from ...domain.page_layout import PageReading, ScanBox
from ...exceptions import RunStoppedError, stop_requested
from ...interfaces import OCRProcessor
from ...messages import log_failure
from ..text_alignment import places

_logger = logging.getLogger(__name__)


class ScanPlaces:
    """Makes the places of a run's readings; built only when the run keeps them."""

    def __init__(
        self, alongside: OCRProcessor | None, stop_event: threading.Event | None = None
    ) -> None:
        """*alongside* reads each page next to the run's reader; ``None`` with the built-in one."""
        self._alongside = alongside
        self._stop_event = stop_event

    def begin(self, pages: Sequence[Path], language: str) -> PendingPlaces:
        """Start what the places of *pages* need while the run's reader reads them."""
        pending = PendingPlaces(self._stop_event)
        if self._alongside is not None and pages:
            pending.start(self._alongside, tuple(pages), language)
        return pending


class PendingPlaces:
    """The places of one document, from its pages being read to the text they are made in."""

    def __init__(self, stop_event: threading.Event | None) -> None:
        self._stop_event = stop_event
        self._abandoned = threading.Event()
        self._thread: threading.Thread | None = None
        self._read: tuple[PageReading, ...] = ()

    def start(self, reader: OCRProcessor, pages: tuple[Path, ...], language: str) -> None:
        """Read *pages* with *reader* on a thread of its own."""
        self._thread = threading.Thread(
            target=self._read_pages, args=(reader, pages, language), name="scan-places", daemon=True
        )
        self._thread.start()

    def places(
        self,
        readings: Sequence[PageReading],
        text: str,
        text_format: str,
        *,
        name: str,
        on_wait: Callable[[], None] | None = None,
    ) -> tuple[ScanBox, ...]:
        """Return where the lines of the pages stand in *text*, or none when they cannot be had.

        *on_wait* is called once when the reading alongside is still on a page, before the run
        waits for it.
        """
        try:
            if any(reading.lines for reading in readings):
                pages = readings
            else:
                pages = self._wait(on_wait)
            if not pages or stop_requested(self._stop_event):
                return ()
            return places(pages, text, text_format)
        except Exception as exc:  # noqa: BLE001  # the places are an addition; the reading stays
            log_failure(_logger, f"The places of {name} stay away", exc)
            return ()

    def close(self) -> None:
        """Let the reading alongside go; it ends after the page it is on."""
        self._abandoned.set()
        if self._thread is not None:
            self._thread.join()

    def _wait(self, on_wait: Callable[[], None] | None) -> tuple[PageReading, ...]:
        """Return the pages read alongside, once they are all read; none without them."""
        if self._thread is None:
            return ()
        if on_wait is not None and self._thread.is_alive():
            on_wait()
        self._thread.join()
        return self._read

    def _read_pages(self, reader: OCRProcessor, pages: tuple[Path, ...], language: str) -> None:
        read: list[PageReading] = []
        try:
            for page in pages:
                if self._abandoned.is_set() or stop_requested(self._stop_event):
                    return
                read.append(reader.read_page(page, language=language))
        except RunStoppedError:
            return  # stopped: places of half the pages would point at half a document
        except Exception as exc:  # noqa: BLE001  # a page the reader cannot read costs only the places
            log_failure(_logger, "A page read for the places failed", exc)
            return
        self._read = tuple(read)
