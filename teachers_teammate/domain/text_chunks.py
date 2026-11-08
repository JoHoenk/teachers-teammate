"""Splitting a long text into pieces that fit one request, without losing a character.

A local model has a fixed context window, so a text longer than what fits cannot be
proofread in one request.  Older versions refused such a file; this module cuts it into
pieces instead.

The cut is at paragraph boundaries (blank lines).  A paragraph that is still too long is cut
at sentence ends, because the model can only fix a sentence it sees whole.  Where a sentence
is still too long (handwriting without punctuation, a list or table with one entry per line)
the cut falls at a line break, and as the last resort between two words.  Markdown keeps
fenced code blocks together: a fence opened in one piece and closed in the next would be a
different document, so a closed fence is never cut, however long it is.

The invariant that makes the pieces safe to reassemble::

    "".join(chunk.text + chunk.separator for chunk in chunks) == text

Every character of the source is in exactly one :class:`Chunk`, either in its ``text`` or in
the ``separator`` that follows it.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .formatted_text import TextFormat

#: One paragraph break: a blank line, possibly with trailing spaces or more blank lines.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n[ \t\n]*")

#: A sentence end followed by whitespace; the split keeps the whitespace.
_SENTENCE_BREAK = re.compile(r"((?<=[.!?\u2026])\s+)")

#: A line break; the split keeps it.
_LINE_BREAK = re.compile(r"(\n+)")

#: Whitespace between two words; the split keeps it.
_WORD_BREAK = re.compile(r"(\s+)")

#: Where an over-long paragraph is cut, tried in this order: a coarser cut keeps more context.
_BREAKS = (_SENTENCE_BREAK, _LINE_BREAK, _WORD_BREAK)

#: A fence that opens or closes a code block (three or more backticks or tildes).
_FENCE = re.compile(r"^[ \t]*(?:`{3,}|~{3,})", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    """One piece of a text, plus the whitespace that followed it in the source."""

    text: str
    """The piece itself, without the separator that follows it."""

    separator: str = ""
    """Whitespace between this piece and the next one; empty for the last piece."""

    @property
    def length(self) -> int:
        """Return how many characters the piece carries."""
        return len(self.text)


def split_for_budget(
    text: str, max_chars: int, text_format: TextFormat = "plain"
) -> tuple[Chunk, ...]:
    """Split *text* into pieces of at most *max_chars* characters where possible.

    Args:
        text: The text to split.
        max_chars: How many characters one request may carry.
        text_format: Notation of *text*; ``"markdown"`` keeps fenced code blocks whole.

    Returns:
        The pieces, in order.  ``"".join(chunk.text + chunk.separator ...)`` gives *text* back.
        A word, or a closed Markdown code fence, longer than *max_chars* cannot be cut: it
        becomes a piece of its own and the request that carries it reports that it is too long.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return (Chunk(text=text),)
    pieces: list[Chunk] = []
    for block in _blocks(text, text_format):
        if block.length <= max_chars or block.whole:
            pieces.append(block.chunk)
        else:
            pieces.extend(_split_oversized(block.chunk, max_chars, _BREAKS))
    return tuple(_pack(pieces, max_chars))


@dataclass(frozen=True)
class _Block:
    """A unit of the text: a paragraph, or a Markdown code fence that must stay whole."""

    chunk: Chunk
    whole: bool = False
    """``True`` when the unit must not be cut, however long it is."""

    @property
    def length(self) -> int:
        """Return how many characters the unit carries."""
        return self.chunk.length


def _paragraphs(text: str) -> list[Chunk]:
    """Return the paragraphs of *text*, each with the whitespace that followed it."""
    chunks: list[Chunk] = []
    cursor = 0
    for match in _PARAGRAPH_BREAK.finditer(text):
        chunks.append(Chunk(text=text[cursor : match.start()], separator=match.group()))
        cursor = match.end()
    chunks.append(Chunk(text=text[cursor:]))
    return chunks


def _fence_lines(paragraph: Chunk) -> int:
    """Return how many lines of *paragraph* open or close a code block."""
    return len(_FENCE.findall(paragraph.text))


def _toggles_fence(paragraph: Chunk) -> bool:
    """Whether *paragraph* opens or closes a code block (an odd number of fence lines)."""
    return _fence_lines(paragraph) % 2 == 1


def _join(paragraphs: list[Chunk]) -> Chunk:
    """Return the paragraphs as one chunk, with their separators between them."""
    text = "".join(p.text + p.separator for p in paragraphs[:-1]) + paragraphs[-1].text
    return Chunk(text=text, separator=paragraphs[-1].separator)


def _blocks(text: str, text_format: TextFormat) -> list[_Block]:
    """Return the units of *text*: paragraphs, with each closed Markdown fence kept whole.

    A fence that is never closed is no code block (OCR noise, a stray backtick line); its
    paragraphs stay ordinary ones, so one stray fence cannot make the rest of the text
    uncuttable.
    """
    paragraphs = _paragraphs(text)
    if text_format != "markdown":
        return [_Block(paragraph) for paragraph in paragraphs]
    blocks: list[_Block] = []
    index = 0
    while index < len(paragraphs):
        if _toggles_fence(paragraphs[index]):
            closing = next(
                (
                    later
                    for later in range(index + 1, len(paragraphs))
                    if _toggles_fence(paragraphs[later])
                ),
                None,
            )
            if closing is not None:
                blocks.append(_Block(_join(paragraphs[index : closing + 1]), whole=True))
                index = closing + 1
                continue
        # A paragraph that opens and closes a fence on its own is a code block, too.
        fenced = _fence_lines(paragraphs[index]) > 0 and not _toggles_fence(paragraphs[index])
        blocks.append(_Block(paragraphs[index], whole=fenced))
        index += 1
    return blocks


def _cut(chunk: Chunk, pattern: re.Pattern[str]) -> list[Chunk]:
    """Cut *chunk* at every match of *pattern*; each part keeps the whitespace that followed."""
    parts = pattern.split(chunk.text)
    cut = [
        Chunk(text=parts[index], separator=parts[index + 1] if index + 1 < len(parts) else "")
        for index in range(0, len(parts), 2)
    ]
    cut[-1] = Chunk(text=cut[-1].text, separator=chunk.separator)
    return cut


def _split_oversized(
    chunk: Chunk, max_chars: int, breaks: tuple[re.Pattern[str], ...]
) -> list[Chunk]:
    """Cut one over-long chunk at the coarsest break that works, then pack the parts.

    A part that is still too long is cut again at the next, finer break.  With no break left
    the part stays whole (a word longer than a request).
    """
    if not breaks:
        return [chunk]
    pieces: list[Chunk] = []
    for part in _cut(chunk, breaks[0]):
        if part.length > max_chars:
            pieces.extend(_split_oversized(part, max_chars, breaks[1:]))
        else:
            pieces.append(part)
    return _pack(pieces, max_chars)


def _pack(pieces: list[Chunk], max_chars: int) -> list[Chunk]:
    """Join consecutive pieces greedily while they fit *max_chars*; never cut one apart."""
    packed: list[Chunk] = []
    current: Chunk | None = None
    for piece in pieces:
        if current is None:
            current = piece
            continue
        joined = current.text + current.separator + piece.text
        if len(joined) <= max_chars:
            current = Chunk(text=joined, separator=piece.separator)
        else:
            packed.append(current)
            current = piece
    if current is not None:
        packed.append(current)
    return packed
