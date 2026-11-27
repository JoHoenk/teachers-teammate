"""Whether the Check tab hides names, and the map that hides them.

The window owns the setting and the anonymizer; this holds what the views need to know about it:
whether the texts are shown with placeholders, whether the result has arrived, why it failed, the
map that turns a name into its placeholder, and the text the anonymizer made of the open one.  The
views ask it and never touch the anonymizer themselves.

Pure Python over :mod:`teachers_teammate.domain.anonymization`: no Qt, no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...domain.anonymization import mask_with_offsets
from ...domain.offset_map import OffsetMap
from ...texts import review as txt


class Masking:
    """The placeholders of the open document, and whether they are shown."""

    def __init__(self) -> None:
        self._available = False
        self._run_hides = False
        self._enabled = False
        self._mapping: dict[str, str] = {}
        self._text = ""
        self._ready = False
        self._error = ""
        self._requested = ""
        self._masked: dict[str, tuple[str, OffsetMap]] = {}

    # ── what the views ask ────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """Whether the texts are shown with placeholders."""
        return self._enabled

    @property
    def available(self) -> bool:
        """Whether names can be hidden at all (the Privacy add-on is installed)."""
        return self._available

    @property
    def run_hides(self) -> bool:
        """Whether the next proofreading is made with the names hidden (the run's setting)."""
        return self._run_hides

    @property
    def preview(self) -> tuple[str, Mapping[str, str]] | None:
        """The anonymizer's text of the open transcription with its map, or ``None``.

        ``None`` until the answer arrives, after a failure, when the answer holds no text, and
        while the answer to the text now open is out: then the names stand in what would go
        out, which is what the box of *What was read* shows.
        """
        if not self._ready or self._error or not self._text:
            return None
        return self._text, dict(self._mapping)

    @property
    def pending(self) -> bool:
        """Whether the placeholders are still being worked out, or could not be."""
        return self._enabled and (not self._ready or bool(self._error))

    @property
    def working(self) -> bool:
        """Whether the placeholders of the open text were asked for and have not arrived yet.

        Until they do the names stand in the text, so the 👁 toggle says so.
        """
        return self._enabled and bool(self._requested) and not self._ready

    @property
    def error(self) -> str:
        """Why hiding the names failed, or ``""``."""
        return self._error

    @property
    def placeholders(self) -> tuple[str, ...]:
        """The placeholders of the open document."""
        return tuple(self._mapping) if self._enabled else ()

    def note(self) -> str:
        """What the banner above the text says about hiding, or ``""`` for none.

        Only a hide that failed is said there: the names then stand in the text while the 👁
        toggle says they are hidden.  Whether the names are hidden at all is the toggle's own
        business, and why the texts cannot be typed into is a tooltip on them.
        """
        if not self._error:
            return ""
        return txt.hiding_personal_info_failed(self._error)

    def mask(self, text: str) -> str:
        """Return *text* with every name replaced by its placeholder (unchanged when off)."""
        if not self._enabled:
            return text
        return self._masked_text(text)[0]

    def carry(self, span: tuple[int, int], text: str) -> tuple[int, int]:
        """Return *span* of *text* as a span of the masked text (unchanged when off)."""
        if not self._enabled:
            return span
        offset_map = self._masked_text(text)[1]
        return offset_map.start(span[0]), offset_map.end(span[1])

    def back(self, offset: int, text: str) -> int:
        """Return where *offset* of the masked *text* stands in *text* itself (unchanged when off).

        The teacher's cursor stands in the placeholders where a name stands in the text: the
        sentence it belongs to is the one the name is in.
        """
        if not self._enabled:
            return offset
        return self._masked_text(text)[1].back(offset)

    # ── what the window pushes ────────────────────────────────────────────

    def set_available(self, available: bool) -> None:
        """Say whether names can be hidden at all; without the add-on they are shown."""
        self._available = available
        if not available:
            self._enabled = False

    def set_run_hides(self, hides: bool) -> None:
        """Say whether the next proofreading is made with the names hidden."""
        self._run_hides = bool(hides)

    def set_enabled(self, enabled: bool) -> None:
        """Show the texts with placeholders, or show the names again."""
        self._enabled = bool(enabled) and self._available

    def forget(self) -> None:
        """Drop the placeholders: they belong to a text that is no longer shown."""
        self._mapping = {}
        self._text = ""
        self._ready = False
        self._error = ""
        self._requested = ""
        self._masked = {}

    def set_result(self, mapping: Mapping[str, str], *, text: str = "", error: str = "") -> None:
        """Hand over the placeholder map and the anonymized text of the open document.

        *text* is the string the anonymizer made — the one a request is built from, marks the
        Markdown shield passes on included.  Empty when there is none, or on *error*.
        """
        self._mapping = dict(mapping)
        self._text = text
        self._ready = True
        self._error = error
        self._masked = {}

    def needs_request(self, raw_text: str) -> bool:
        """Whether the window has to hide the names of *raw_text* now.

        Asked once per text: a text whose placeholders were asked for is not asked again,
        whether the answer has arrived or not, and a text that changed is asked again.
        """
        return bool(raw_text.strip()) and self._requested != raw_text

    def mark_requested(self, raw_text: str) -> None:
        """Remember that the names of *raw_text* were asked for, so they are asked once.

        The anonymized text held so far was made from another text, so it is no preview of this
        one.
        """
        if raw_text != self._requested:
            self._text = ""
        self._requested = raw_text

    def _masked_text(self, text: str) -> tuple[str, OffsetMap]:
        """Return *text* masked, and the map that carries a span of it over."""
        cached = self._masked.get(text)
        if cached is None:
            cached = mask_with_offsets(text, self._mapping)
            self._masked[text] = cached
        return cached
