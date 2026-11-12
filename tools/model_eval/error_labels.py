"""Error labels of the public learner corpora, attached to the reference changes of a test text.

The corpora (Falko-MERLIN, W&I+LOCNESS) ship, besides the corrected text, an M2 file that lists
every correction with a label such as ``R:DET:FORM`` (a determiner replaced by another form) or
``M:PUNCT`` (a missing mark).  The comparison scores a model against the *corrected text*, and the
word diff of source → reference gives the reference changes; this module hands each of those
changes the label the annotators gave it, so a report can answer "which kinds of error does a
model miss?" and "does it call the error what the corpus calls it?".

Only the *codes* are kept (``ReferenceLabel``), never the corpus text, so the labelled test set
stays as shareable as the unlabelled one: not at all, outside the licence of the corpora.

How a label finds its change.  The M2 file counts tokens of its own tokenisation, the word diff
counts the app's tokens.  Both are made of the same characters, so the M2 tokens are first found
again in the text, one after another (:func:`locate`), which gives them character spans.  A
reference change then takes the label of the annotated edit it overlaps (:func:`label_reference`).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from teachers_teammate.domain.correction_review import align, tokenize

from .quality import (
    Edit,
    Span,
    edits,
    match_edits,
    spans_touch,
    word_positions,
)

if TYPE_CHECKING:
    from teachers_teammate.domain.correction_review import AlignedReview, CorrectionReview

UNLABELLED = "unlabelled"
"""Name of the group that holds the reference changes no annotated edit could be found for."""

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

_CLOSED_CLASS = frozenset(
    {"DET", "PREP", "ADP", "PRON", "PART", "CONJ", "SCONJ", "AUX", "CONTR", "MORPH"}
)
_OPEN_CLASS = frozenset({"NOUN", "VERB", "ADJ", "ADV", "PNOUN"})
_FORM_SUBTYPES = frozenset({"FORM", "INFL", "NUM", "SVA", "TENSE", "POSS"})


@dataclass(frozen=True)
class ReferenceLabel:
    """One change the reference correction makes, with the corpus label of that error."""

    start: int
    """First source token of the change (the app's tokens, see ``quality.edits``)."""

    end: int
    """One past the last source token; equal to ``start`` for an insertion."""

    replacement: tuple[str, ...]
    """The tokens that replace the source tokens; empty for a deletion."""

    code: str
    """The corpus label (``R:DET:FORM``); empty when no annotated edit matched this change."""

    @property
    def edit(self) -> Edit:
        """Return the change in the form the scoring uses."""
        return (self.start, self.end, self.replacement)

    @property
    def category(self) -> str:
        """Return the app's category for this error (see :func:`category_of`); ``""`` if none."""
        return category_of(self.code)

    @property
    def main_type(self) -> str:
        """Return the kind of word the error is about (``DET``, ``PUNCT``…), or the unlabelled name."""
        return main_type(self.code)

    @classmethod
    def from_dict(cls, record: dict) -> ReferenceLabel:
        """Build a label from its stored form."""
        return cls(
            start=int(record["start"]),
            end=int(record["end"]),
            replacement=tuple(record["replacement"]),
            code=str(record["code"]),
        )


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
    """Return every reference change of *source* → *reference* with its corpus label.

    Args:
        source: The learner text.
        reference: Its reference correction.
        located: The M2 sentences of the text, each with the character spans of its tokens
            (:func:`locate`).

    Returns:
        One label per reference change, in text order.  A change no annotated edit lies within
        one word of keeps an empty code.
    """
    tokens = tokenize(source)
    offsets = [0]
    for token in tokens:
        offsets.append(offsets[-1] + len(token))
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
        labels.append(ReferenceLabel(start, end, replacement, code))
    return tuple(labels)


def _rank(change: Span, annotated: Span) -> tuple[int, int, int]:
    """Return how well an annotated edit fits a change: smaller is better.

    The edits that share words with the change come first, then the nearest one (in words).
    """
    if spans_touch(change, annotated):
        return (0, 0, -(min(change[1], annotated[1]) - max(change[0], annotated[0])))
    gap = max(change[0], annotated[0]) - min(change[1], annotated[1])
    return (1, max(gap, 0), 0)


# ── from corpus label to the app's categories ─────────────────────────────────


def _parts(code: str) -> tuple[str, str, str]:
    """Return ``(operation, type, subtype)`` of a label; empty strings for what is missing."""
    parts = code.split(":")
    if parts and parts[0] in {"M", "R", "U"}:
        operation, parts = parts[0], parts[1:]
    else:
        operation = ""
    return operation, parts[0] if parts else "", parts[1] if len(parts) > 1 else ""


def main_type(code: str) -> str:
    """Return the type of a label without operation and subtype: ``R:VERB:FORM`` → ``VERB``."""
    return _parts(code)[1] or UNLABELLED


def category_of(code: str) -> str:
    """Return the app's category (``grammar``, ``spelling``…) a corpus label stands for.

    A coarse mapping, for the one question "did the model call the error roughly what the
    corpus calls it?": a label with no clear counterpart (``OTHER``, a missing noun) maps to
    ``other``, and an empty code to ``""``.  Neither is checked.
    """
    if not code:
        return ""
    operation, kind, sub = _parts(code)
    if kind == "PUNCT":
        return "punctuation"
    if kind in {"SPELL", "ORTH"}:
        return "spelling"
    if kind == "WO":
        return "word_order"
    if kind in _CLOSED_CLASS:
        return "grammar"
    if kind in _OPEN_CLASS:
        if sub in _FORM_SUBTYPES or (operation in {"M", "U"} and kind == "VERB"):
            return "grammar"
        return "word_choice" if operation == "R" else "other"
    return "other"


# ── does the model call the error what the corpus calls it? ───────────────────


@dataclass(frozen=True)
class CategoryAgreement:
    """How many found errors the model gave the category the corpus label stands for."""

    checked: int
    """Errors the model fixed exactly, with a note, and whose label maps to a category."""

    agreed: int
    """Of those, the notes whose category is the one the label maps to."""


def category_agreement(
    source: str,
    output: str,
    reference: str,
    review: CorrectionReview | None,
    labels: Sequence[ReferenceLabel],
    *,
    aligned: AlignedReview | None = None,
) -> CategoryAgreement:
    """Return how often the model's category matches the corpus label of the errors it fixed.

    Only errors the model fixed *exactly* count (the same words and replacement), because only
    then it is certain which note belongs to which error, and only errors whose label maps to a
    category (not ``other``).  The corpus labels were not made for these six categories, so
    this is a rough diagnostic and not a verdict on a single note.

    Args:
        source: The text before proofreading.
        output: The text after proofreading, without the marks the model added.
        reference: The reference correction of the same text.
        review: The model's explanations, or ``None`` in corrections-only mode.
        labels: The corpus label of every reference change.
        aligned: The aligned review of *source* against *output*, when the caller has already
            computed it for another purpose (the comparison stores it on the row).
    """
    if review is None or not labels:
        return CategoryAgreement(0, 0)
    exact = {match.edit for match in match_edits(source, output, reference) if match.exact}
    offsets = [0]
    for token in tokenize(source):
        offsets.append(offsets[-1] + len(token))
    if aligned is None:
        aligned = align(source, output, review)
    noted = {
        change.position: change.note.category
        for change in aligned.changes
        if change.note is not None
    }
    checked = agreed = 0
    for label in labels:
        wanted = label.category
        if label.edit not in exact or wanted in {"", "other"}:
            continue
        given = noted.get(offsets[label.start])
        if given is None:
            continue
        checked += 1
        agreed += given == wanted
    return CategoryAgreement(checked, agreed)
