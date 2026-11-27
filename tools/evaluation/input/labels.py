"""Error labels of the public learner corpora, attached to the reference changes of a test text.

The corpora (Falko-MERLIN, W&I+LOCNESS) ship, besides the corrected text, an M2 file that lists
every correction with a label such as ``R:DET:FORM`` (a determiner replaced by another form) or
``M:PUNCT`` (a missing mark).  The comparison scores a model against the *corrected text*, and the
word diff of source -> reference gives the reference changes; this module hands each of those
changes the label the annotators gave it, so a report can answer "which kinds of error does a model
miss?" and "does it call the error what the corpus calls it?".

Only the *codes* are kept, never the corpus text, so the labelled test set stays as shareable as the
unlabelled one: not at all, outside the licence of the corpora.  What a code *means* - the app's
category, the kind of word - is :mod:`..core.labels`'s business; this module only finds the code.

How a label finds its change.  The M2 file counts tokens of its own tokenisation, the word diff
counts the app's tokens.  Both are made of the same characters, so the M2 tokens are first found
again in the text, one after another (:func:`locate`), which gives them character spans.  A
reference change then takes the label of the annotated edit it overlaps (:func:`label_reference`).

The labels this module returns are :class:`..core.labels.ReferenceLabel`, whose positions are
**characters**: a stored label does not depend on the tokenizer, so a tokenizer change moves
no stored position.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from teachers_teammate.domain.proofreading.edit_agreement import edits, spans_touch, word_positions
from teachers_teammate.domain.proofreading.sentences import Span
from teachers_teammate.domain.proofreading.word_diff import text_offsets, tokenize

from ..core.labels import ReferenceLabel

_NORMALISE = str.maketrans(
    {
        **dict.fromkeys(("\u2019", "\u00b4", "\u2018", "\u2032", "`"), "'"),
        **dict.fromkeys(
            ("\u201c", "\u201d", "\u02dd", "\u00a8", "\u201e", "\u300e", "\u300f"), '"'
        ),
        **dict.fromkeys(("\u2013", "\u2014", "\u2015", "\u00ac"), "-"),
        "\u3001": ",",
        "\uff0c": ",",
        "\uff1a": ":",
        "\uff1b": ";",
        "\uff1f": "?",
        "\uff01": "!",
        "\u0650": " ",
        "\u200b": " ",
    }
)
"""The punctuation normalisation the BEA-2019 conversion applied before it tokenised.  Every
entry maps one character to one character, so a position in the normalised text is a position in
the original."""

_RESYNC_WINDOW = 80
"""How many sentences :func:`split_into_texts` looks ahead for the start of a text."""


@dataclass(frozen=True)
class M2Edit:
    """One annotated edit of an M2 sentence."""

    start: int
    """First token of the sentence the edit covers."""

    end: int
    """One past the last token; equal to ``start`` for an insertion."""

    code: str
    """The label, ``R:DET:FORM`` and the like."""


@dataclass(frozen=True)
class M2Sentence:
    """One sentence of an M2 file: its tokens and the edits of the first annotator."""

    tokens: tuple[str, ...]
    edits: tuple[M2Edit, ...]

    uncorrected: bool = False
    """The annotator marked an error here but did not correct it (``UNK``).

    Such a sentence looks unchanged to the word diff, although the annotator saw an error in it,
    so it must not count as a sentence the reference leaves alone.
    """


def read_m2(path: Path) -> list[M2Sentence]:
    """Return the sentences of an M2 file with the edits of annotator 0.

    ``noop`` (a sentence nothing was changed in) is no edit and is skipped.  ``UNK`` (an error the
    annotator could not correct) adds no edit either, but it marks the sentence as
    :attr:`M2Sentence.uncorrected`.
    """
    sentences: list[M2Sentence] = []
    tokens: tuple[str, ...] | None = None
    found: list[M2Edit] = []
    uncorrected = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("S "):
            if tokens is not None:
                sentences.append(M2Sentence(tokens, tuple(found), uncorrected))
            tokens, found, uncorrected = tuple(line[2:].split()), [], False
        elif line.startswith("A ") and tokens is not None:
            fields = line[2:].split("|||")
            if len(fields) < 6 or fields[-1].strip() != "0":
                continue
            if fields[1] == "UNK":
                uncorrected = True
                continue
            if fields[1] == "noop":
                continue
            start, end = fields[0].split()
            found.append(M2Edit(int(start), int(end), fields[1]))
    if tokens is not None:
        sentences.append(M2Sentence(tokens, tuple(found), uncorrected))
    return sentences


def locate(text: str, sentences: Sequence[M2Sentence]) -> list[tuple[Span, ...]] | None:
    """Return the character span of every token of the sentences that make up *text*.

    Walks the tokens one after another through the text, skipping whitespace, and stops when the
    text is used up, so *sentences* may run on past the end of the text.  The result has one
    entry per sentence that was used.

    Returns:
        ``None`` when a token is not where it should be, or when the sentences end before the
        text does: the sentences are not those of this text.
    """
    normalised = text.translate(_NORMALISE)
    cursor = _skip_whitespace(normalised, 0)
    located: list[tuple[Span, ...]] = []
    for sentence in sentences:
        if cursor >= len(normalised):
            break
        spans: list[Span] = []
        for token in sentence.tokens:
            cursor = _skip_whitespace(normalised, cursor)
            end = cursor + len(token)
            if normalised[cursor:end] != token.translate(_NORMALISE):
                return None
            spans.append((cursor, end))
            cursor = end
        located.append(tuple(spans))
        cursor = _skip_whitespace(normalised, cursor)
    return located if cursor >= len(normalised) else None


def _skip_whitespace(text: str, cursor: int) -> int:
    """Return the first position at or after *cursor* that is not whitespace."""
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return cursor


def split_into_texts(
    texts: Sequence[str], sentences: Sequence[M2Sentence]
) -> list[tuple[int, list[tuple[Span, ...]]] | None]:
    """Return, for every text, the index of its first M2 sentence and the token spans.

    The M2 file of a corpus lists the sentences of all its texts one after another without
    marking where a text ends, in the order of the texts, so the texts are found by walking
    through them.  A text whose sentences cannot be found gets ``None``, and the next text is
    looked for a little further on, so one failure does not lose the rest.
    """
    result: list[tuple[int, list[tuple[Span, ...]]] | None] = []
    first = 0
    for text in texts:
        found = None
        for candidate in range(first, min(first + _RESYNC_WINDOW, len(sentences))):
            located = locate(text, sentences[candidate:])
            if located is not None:
                found = (candidate, located)
                break
        if found is not None:
            first = found[0] + len(found[1])
        result.append(found)
    return result


def label_reference(
    source: str,
    reference: str,
    located: Sequence[tuple[M2Sentence, Sequence[Span]]],
) -> tuple[ReferenceLabel, ...]:
    """Return every reference change of *source* -> *reference* with its corpus label.

    Args:
        source: The learner text.
        reference: Its reference correction.
        located: The M2 sentences of the text, each with the character spans of its tokens
            (:func:`locate`).

    Returns:
        One label per reference change, in text order, its positions in characters of *source*.
        A change no annotated edit lies within one word of keeps an empty code.
    """
    tokens = tokenize(source)
    offsets = text_offsets(source)
    positions = word_positions(tokens)

    annotated: list[tuple[Span, str]] = []
    for sentence, spans in located:
        for edit in sentence.edits:
            if edit.start < edit.end:
                chars = (spans[edit.start][0], spans[edit.end - 1][1])
                first = bisect_right(offsets, chars[0]) - 1
                last = bisect_left(offsets, chars[1])
            else:
                point = spans[edit.start][0] if edit.start < len(spans) else spans[-1][1]
                first = last = min(bisect_left(offsets, point), len(tokens))
            annotated.append(((positions[first], positions[min(last, len(tokens))]), edit.code))

    labels = []
    for start, end, replacement in sorted(edits(source, reference)):
        span = (positions[start], positions[end])
        best = min(
            ((_rank(span, other), code) for other, code in annotated),
            default=None,
            key=lambda pair: pair[0],
        )
        code = best[1] if best is not None and best[0][1] <= 1 else ""
        labels.append(
            ReferenceLabel(
                start=offsets[start],
                end=offsets[end],
                replacement="".join(replacement),
                code=code,
            )
        )
    return tuple(labels)


def _rank(change: Span, annotated: Span) -> tuple[int, int, int]:
    """Return how well an annotated edit fits a change: smaller is better.

    The edits that share words with the change come first, then the nearest one (in words).
    """
    if spans_touch(change, annotated):
        return (0, 0, -(min(change[1], annotated[1]) - max(change[0], annotated[0])))
    gap = max(change[0], annotated[0]) - min(change[1], annotated[1])
    return (1, max(gap, 0), 0)


__all__ = [
    "M2Edit",
    "M2Sentence",
    "label_reference",
    "locate",
    "read_m2",
    "split_into_texts",
]
