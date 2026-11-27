"""What the reader saw on a page: its text, the lines it stands on, and where they sit in the text.

The built-in reader finds boxes, groups them into rows and reads each box.  A :class:`ReadLine`
is one such row with what pointing at it later needs: its text, how sure the reader was, and its
boxes' corners as fractions of the page, so a mark drawn over a stored image of another size
still sits on the line.

With a reader that gives no layout (an Ollama reader), the built-in reader reads each page
alongside, and the two texts are matched once per document by their characters:
the places that match are exact, the places that differ are stretched.  What a line maps to is
kept as a :class:`ScanBox` — its page, how sure the reader was, its corners, and its range in a
clean text of the document.  Three ranges of one document matter:

``R``
    the stored reading, in plain; what the places are made against and stored in.
``T``
    the teacher's transcription; what the scan is marked through.
``C``
    the corrected text; where the cursor of *Final text* and *With corrections* stands.

A mark is shown only where its place is sure: a line whose stretch in ``R`` is not similar to
what the reader read stays unplaced, and a sentence is marked only when enough of its characters
sit on placed lines (:data:`COVER_MIN`).  Otherwise the scan shows the page.

Pure: no I/O, no image library, no provider imports.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any

from .formatted_text import PAGE_LABEL
from .proofreading.sentences import sentence_index, sentence_spans
from .proofreading.word_diff import OffsetMap

Corner = tuple[float, float]
"""One corner of a box, as fractions of the page's width and height."""

Box = tuple[Corner, ...]
"""The four corners of one box of a line, top-left first, as fractions of the page."""

Opcode = tuple[str, int, int, int, int]
"""One stretch of an alignment: its tag, its range in the first text, and in the second.

The tags are those of :func:`difflib.SequenceMatcher.get_opcodes` and of RapidFuzz's own
``opcodes``: ``equal``, ``replace``, ``delete`` (nothing in the second text) and ``insert``
(nothing in the first).
"""

Span = tuple[int, int]
"""A stretch of a text, half-open."""

#: The version of the places stored with a reading.  Bump it when a line is placed or carried
#: differently; a stored layout of another version is not used: the document shows no marks
#: until it is read again.
LAYOUT_VERSION = 2

#: How similar a line's own text and its stretch of the reading must be for the line to be
#: placed.  Measured on 8 handwritten pages: below this the stretches are misread words, and a
#: mark there would sit on words the teacher cannot find.
PLACE_MIN = 0.55

#: The share of a sentence's characters that must sit on placed lines for the sentence to be
#: marked at all.
COVER_MIN = 0.6

#: The mean confidences at which a mark takes one more step of shading, clearest first.  The
#: steps themselves are drawn by the window: a wrong-word share of 74 % at 0.70 and 16 % at 0.95.
CONFIDENCE_BANDS: tuple[float, ...] = (0.95, 0.85, 0.70)

#: A span that ends in a single dotted letter (``z.`` of ``z. B.``) goes on into the next one.
_LETTER_END = re.compile(r"(?:^|\s)[A-Za-z]\.$")

#: A span that ends in a dotted number: ``15.`` of a date or an ordinal, or a year.
_NUMBER_END = re.compile(r"(?:^|\s)(\d+)\.$")

#: A blank line between two spans: what stands before it ends there.
_BLANK_LINE = re.compile(r"\n[ \t]*\n")

#: The most digits a dotted number has that is a day, a month or an ordinal rather than a year.
_ORDINAL_DIGITS = 2

#: The beginning of a list item: a dash or bullet, or a number with a dot or bracket.
_LIST_ITEM = re.compile(r"(?:[-*\u2022\u2013]|\d+[.)])\s")

#: The most words of a line that stands on its own when no mark ends it: a heading, a date, a
#: greeting.  A line of a sentence that goes on below holds more.
_SHORT_LINE_WORDS = 5

#: Abbreviations that end in a full stop without ending the sentence, lower case, without the dot.
ABBREVIATIONS = frozenset(
    {
        "bspw",
        "bzw",
        "ca",
        "dr",
        "evtl",
        "ggf",
        "inkl",
        "max",
        "min",
        "nr",
        "prof",
        "sog",
        "str",
        "vgl",
        "z",
    }
)

#: Abbreviations that often end a sentence too: they go on only into a lower-case word or a number.
ENDING_ABBREVIATIONS = frozenset({"dgl", "etc", "usw"})

#: Closing quotes and brackets that stand between a sentence's last mark and the whitespace.
_CLOSERS = "\"'„“”‘’»)]"  # noqa: RUF001  # the quotes are the point


@dataclass(frozen=True)
class ReadLine:
    """One line the reader returned from a page."""

    text: str
    """The line's words, joined with a space."""

    confidence: float
    """The lowest confidence of the boxes the line is made of, 0.0 to 1.0."""

    boxes: tuple[Box, ...]
    """The boxes the line was read from, left to right, as fractions of the page."""

    page: int = 0
    """The page's index in the document; the pipeline numbers the pages when it joins them."""

    box_texts: tuple[str, ...] = ()
    """What each box reads, in the order of :attr:`boxes`; empty when the reader does not say."""


@dataclass(frozen=True)
class PageReading:
    """What reading one page gave: the text, and the lines it stands on.

    ``lines`` is empty for an engine that has no layout to give: an Ollama reader and a PDF's
    text layer return the text alone.
    """

    text: str
    lines: tuple[ReadLine, ...] = ()


@dataclass(frozen=True)
class LineAt:
    """One line of a page, and where its text stands in the document's text."""

    line: ReadLine
    """The line as the reader returned it."""

    start: int
    """Where the line's text begins in the text it was read from, half-open."""

    end: int
    """Where the line's text ends."""


@dataclass(frozen=True)
class ScanBox:
    """One line of the scan, with its place in a clean text of the document.

    The same type is used three times over, and only the text its range points into differs: a
    line placed in ``R`` (what is stored with the reading), a line carried into ``T`` (what the
    window marks through), and a line of the reading itself while the places are made.
    """

    page: int
    """The page's index in the document, from 0."""

    start: int
    """Where the line stands in that text, half-open."""

    end: int
    """Where the line ends in that text."""

    confidence: float
    """How sure the reader was of the line, 0.0 to 1.0."""

    boxes: tuple[Box, ...]
    """The boxes the line was read from, left to right, as fractions of the page."""

    shares: tuple[float, ...] = ()
    """How much of the line's text each box holds, 0.0 to 1.0, summing to 1.0.

    Empty when the reader gave one box for the line: the marks then share the line evenly.
    """

    def to_dict(self) -> dict[str, Any]:
        """Return the box as the cache stores it."""
        return {
            "page": self.page,
            "start": self.start,
            "end": self.end,
            "confidence": round(self.confidence, 4),
            "boxes": [[[round(x, 5), round(y, 5)] for x, y in box] for box in self.boxes],
            "shares": [round(share, 4) for share in self.shares],
        }


@dataclass(frozen=True)
class Mark:
    """What the scan draws for one sentence: the boxes it stands on, and how hard they were."""

    polygons: tuple[tuple[int, Box], ...]
    """The page and the box of every word the sentence stands on, in page order."""

    confidence: float
    """The mean confidence of the lines the polygons come from, 0.0 to 1.0."""

    @property
    def shade(self) -> int:
        """Return the band the mark is drawn in, 0 for the surest."""
        return shade(self.confidence)


@dataclass(frozen=True)
class Sentence:
    """One sentence of a plain text, and whether it ends there."""

    start: int
    """Where the sentence begins, half-open."""

    end: int
    """Where it ends."""

    closed: bool
    """Whether it ends with a sentence mark, so it stands on its own.

    A heading or a list item does not; the scan marks the line under the cursor there.
    """


# ── Placing the lines in the reading ──────────────────────────────────────────


def place(
    lines: Sequence[LineAt],
    opcodes: Sequence[Opcode],
    reading: str,
    *,
    place_min: float = PLACE_MIN,
) -> tuple[ScanBox, ...]:
    """Return the lines of a page with their range in *reading*, dropping the unsure ones.

    *opcodes* is the character alignment of the text the lines were read from against *reading*
    — the same page, read twice.  A line whose stretch of *reading* is not similar enough to
    what the reader read is left out: the two readings disagreed there, and a mark would point
    at words nobody wrote.
    """
    placed: list[ScanBox] = []
    carrier = _Carrier(opcodes, len(reading))
    for line in lines:
        start, end = carrier.offset(line.start), carrier.offset(line.end, close=True)
        if end <= start:
            continue
        if _similarity(line.line.text, reading[start:end]) < place_min:
            continue
        placed.append(
            ScanBox(
                page=line.line.page,
                start=start,
                end=end,
                confidence=line.line.confidence,
                boxes=line.line.boxes,
                shares=_shares(line.line),
            )
        )
    return tuple(placed)


def carried(boxes: Sequence[ScanBox], offset_map: OffsetMap) -> tuple[ScanBox, ...]:
    """Return *boxes* with their ranges carried through *offset_map*.

    A box whose carried range holds nothing is dropped: the text there was replaced, so the
    line has no place in the text the mark is drawn in.
    """
    moved: list[ScanBox] = []
    for box in boxes:
        start = offset_map.forward(box.start)
        end = offset_map.forward(box.end, end=True)
        if end <= start:
            continue
        moved.append(
            ScanBox(
                page=box.page,
                start=start,
                end=end,
                confidence=box.confidence,
                boxes=box.boxes,
                shares=box.shares,
            )
        )
    return tuple(moved)


def layout_from(raw: Sequence[object]) -> tuple[ScanBox, ...]:
    """Return the stored places of a reading, skipping the rows this version cannot read."""
    boxes: list[ScanBox] = []
    for row in raw:
        box = _box_from(row)
        if box is not None:
            boxes.append(box)
    return tuple(boxes)


def _box_from(raw: object) -> ScanBox | None:
    """Return one stored place, or ``None`` when it is not a place this version can draw."""
    if not isinstance(raw, Mapping):
        return None  # a row of another shape: the page shows instead
    try:
        boxes = tuple(tuple((float(x), float(y)) for x, y in box) for box in raw.get("boxes") or ())
        return ScanBox(
            page=int(raw["page"]),
            start=int(raw["start"]),
            end=int(raw["end"]),
            confidence=float(raw["confidence"]),
            boxes=boxes,
            shares=tuple(float(share) for share in raw.get("shares") or ()),
        )
    except KeyError, TypeError, ValueError:
        return None  # a place of another version or a broken row: the page shows instead


class _Carrier:
    """Carries offsets of the first text of an alignment into the second."""

    def __init__(self, opcodes: Sequence[Opcode], length: int) -> None:
        # The stretches of the first text, in order; an insertion holds nothing of it.
        self._opcodes = [opcode for opcode in opcodes if opcode[0] != "insert"]
        self._starts = [opcode[1] for opcode in self._opcodes]
        self._length = length

    def offset(self, offset: int, *, close: bool = False) -> int:
        """Return where *offset* of the first text stands in the second.

        Inside a stretch both texts share, the offset moves one to one.  Inside a stretch that
        was replaced or dropped, it widens to that stretch: what stood there is what stands here
        now.  A *close* offset is the end of a range: it belongs to the stretch it ends, not to
        the one that begins there.
        """
        if close:
            index = bisect_left(self._starts, offset) - 1
            if index < 0:
                return 0
        else:
            index = bisect_right(self._starts, offset) - 1
        if index < 0 or index >= len(self._opcodes):
            return self._length
        tag, i1, i2, j1, j2 = self._opcodes[index]
        if offset > i2 or (offset == i2 and not close):
            return self._length
        if tag == "equal":
            return j1 + (offset - i1)
        return j2 if close else j1


def _shares(line: ReadLine) -> tuple[float, ...]:
    """Return what share of *line*'s text each of its boxes holds."""
    words = list(line.box_texts) or [word for word in line.text.split(" ") if word]
    if len(words) != len(line.boxes) or not words:
        return ()
    total = sum(len(word) for word in words)
    return tuple(len(word) / total for word in words)


def _similarity(one: str, other: str) -> float:
    """Return how alike two stretches of text are, 0.0 to 1.0.

    The character alignment of a whole document is the expensive step and happens in
    ``infrastructure``; this is one line at a time, where the standard library is enough.
    """
    if not one or not other:
        return 0.0
    # Case is ignored, as in the alignment: a reader that writes a heading in capitals read it.
    return SequenceMatcher(None, one.lower(), other.lower(), autojunk=False).ratio()


# ── Sentences ─────────────────────────────────────────────────────────────────


def sentences(text: str) -> tuple[Span, ...]:
    """Return the sentence spans of *text*, joining the ones a number or abbreviation broke.

    :func:`~teachers_teammate.domain.proofreading.sentences.sentence_spans` is the splitter the
    stored reviews number their edits by, so it does not change here; a sentence that ends in
    ``15.``, ``z. B.`` or ``bzw.`` is joined with the one after it instead.  A line that stands
    on its own — a page label, a list item, a heading — is cut off at its line break
    (:func:`_stands_alone`), so it is not part of the sentence below it.
    """
    spans = sentence_spans(text)
    merged: list[Span] = []
    index = 0
    while index < len(spans):
        start, end = spans[index]
        following = index + 1
        while following < len(spans) and _opens(spans[following - 1], spans[following], text):
            end = spans[following][1]
            following += 1
        merged.append((start, end))
        index = following
    return tuple(piece for span in merged for piece in _apart(text, span))


def sentence_at(text: str, offset: int) -> Sentence | None:
    """Return the sentence the plain *offset* of *text* belongs to, or ``None`` without one."""
    spans = sentences(text)
    index = sentence_index(spans, offset)
    if index < 0:
        return None
    start, end = spans[index]
    return Sentence(start, end, _closed(text[start:end]))


def _opens(span: Span, following: Span, text: str) -> bool:
    """Whether the sentence of *span* goes on into *following*: its full stop ends no sentence.

    A dotted letter or a known abbreviation always goes on, a day or an ordinal (``15.``) too;
    a year or an abbreviation that often ends a sentence only into a lower-case word or a number.
    A blank line ends what stands before it.
    """
    if _BLANK_LINE.search(text, span[1], following[0]):
        return False
    piece = text[span[0] : span[1]].rstrip(_CLOSERS)
    first = text[following[0] : following[0] + 1]
    goes_on = first.islower() or first.isdigit()
    if _LETTER_END.search(piece):
        return True
    number = _NUMBER_END.search(piece)
    if number is not None:
        return len(number.group(1)) <= _ORDINAL_DIGITS or goes_on
    words = piece.split()
    if not words or not words[-1].endswith("."):
        return False
    word = words[-1][:-1].lower()
    return word in ABBREVIATIONS or (word in ENDING_ABBREVIATIONS and goes_on)


def _apart(text: str, span: Span) -> Iterator[Span]:
    """Yield *span* cut at every line break where the line before or after stands on its own."""
    start, end = span
    piece_start = line_start = start
    while (line_end := text.find("\n", line_start, end)) >= 0:
        next_end = text.find("\n", line_end + 1, end)
        following = text[line_end + 1 : end if next_end < 0 else next_end]
        whole = not text[text.rfind("\n", 0, line_start) + 1 : line_start].strip()
        if _stands_alone(text[line_start:line_end], following, whole=whole):
            yield from _trimmed(text, piece_start, line_end)
            piece_start = line_end + 1
        line_start = line_end + 1
    yield from _trimmed(text, piece_start, end)


def _stands_alone(line: str, following: str, *, whole: bool) -> bool:
    """Whether the line break between *line* and *following* ends what stands before it.

    A page label always stands alone, and a list item begins at its line.  Otherwise a line no
    mark ends goes on into the next — handwriting breaks a sentence across lines — unless it is
    a *whole* line (no sentence ended earlier on it) that is a list item or short, and the next
    line begins as a sentence does.
    """
    line, following = line.strip(), following.strip()
    if PAGE_LABEL.fullmatch(line) or PAGE_LABEL.fullmatch(following):
        return True
    if _LIST_ITEM.match(following):
        return True
    begins_sentence = following[:1].isupper() or following[:1].isdigit()
    if not whole or _closed(line) or not begins_sentence:
        return False
    return bool(_LIST_ITEM.match(line)) or len(line.split()) <= _SHORT_LINE_WORDS


def _trimmed(text: str, start: int, end: int) -> Iterator[Span]:
    """Yield *start*..*end* without the whitespace at either end; nothing when only that is left."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        yield start, end


def _closed(piece: str) -> bool:
    """Whether *piece* ends with a sentence mark, so it is not a heading or a list item."""
    return piece.rstrip(_CLOSERS).endswith((".", "!", "?", "…"))


# ── Marks ─────────────────────────────────────────────────────────────────────


def marks(
    start: int,
    end: int,
    boxes: Sequence[ScanBox],
    *,
    cover_min: float = COVER_MIN,
) -> Mark | None:
    """Return what the scan draws for the stretch *start*..*end* of the transcription.

    ``None`` when too little of it sits on a placed line: the scan then shows the page, which
    says more than a mark on words the reader never placed.
    """
    if end <= start:
        return None
    covered = 0
    polygons: list[tuple[int, Box]] = []
    confidences: list[float] = []
    for box in boxes:
        overlap_start, overlap_end = max(start, box.start), min(end, box.end)
        if overlap_end <= overlap_start:
            continue
        covered += overlap_end - overlap_start
        confidences.append(box.confidence)
        polygons.extend((box.page, corner) for corner in _inside(box, overlap_start, overlap_end))
    if not polygons or covered / (end - start) < cover_min:
        return None
    return Mark(tuple(polygons), sum(confidences) / len(confidences))


def mark_of_line(offset: int, boxes: Sequence[ScanBox]) -> Mark | None:
    """Return what the scan draws for the line the plain *offset* stands on.

    The fallback for a stretch that no sentence closes — a heading, a list item, a date — where
    the line is the only place the cursor can be pointed at.  A cursor at the end of a line,
    before its line break, stands on that line.
    """
    touched = [box for box in boxes if box.start <= offset < box.end]
    touched = touched or [box for box in boxes if box.end == offset]
    if not touched:
        return None
    polygons = tuple((box.page, corner) for box in touched for corner in box.boxes)
    if not polygons:
        return None
    return Mark(polygons, sum(box.confidence for box in touched) / len(touched))


def _inside(box: ScanBox, start: int, end: int) -> tuple[Box, ...]:
    """Return the boxes of *box* that stand on *start*..*end*, clipped by their character share."""
    if start <= box.start and box.end <= end:
        return box.boxes
    length = box.end - box.start
    if length <= 0:
        return box.boxes
    shares = box.shares or tuple(1 / len(box.boxes) for _ in box.boxes)
    kept: list[Box] = []
    at = box.start
    for corner, share in zip(box.boxes, shares, strict=False):
        word_end = at + round(share * length)
        if at < end and word_end > start:
            kept.append(corner)
        at = word_end
    return tuple(kept)


def shade(confidence: float) -> int:
    """Return the band a mark of *confidence* is drawn in, 0 for the surest."""
    for step, floor in enumerate(CONFIDENCE_BANDS):
        if confidence >= floor:
            return step
    return len(CONFIDENCE_BANDS)
