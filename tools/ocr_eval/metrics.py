"""What one reading is worth against the transcription beside its page.

The answer goes through the app's own OCR answer handling — the cleaning every page gets, the
post-processor a run stores a document's text with, then the plain text a teacher reads — so a model whose answer format the app
mangles scores badly here: that is a finding, not a fault of this module.  Both texts are then cut
into the same tokens, a word or one punctuation mark, so ``Fehler,`` is the word and the comma on
either side.  Spaces and line breaks do not count — Ollama models join written lines into
paragraphs, the built-in reader answers one row per line — so reading order is what the
bag-of-words figure is for.

A transcription writes the student's mistakes as they are, and marks them: a misspelt word in
braces as the student wrote it (``{Fehler}``, ``{zu sammen}``), ``[?]`` for a word nobody can
read.  ``[?]`` makes any one answer word right there.  The share of braced words a model read
exactly as the student wrote them is its *kept mistakes*: a model that silently corrects the
student hides what proofreading is there to find.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import re
import unicodedata

from rapidfuzz.distance import Levenshtein

from teachers_teammate.infrastructure.ocr_formats import OcrOutputPostprocessor, plain_text_of
from teachers_teammate.infrastructure.ocr_text_cleaner import clean_ocr_text

from .records import Flags

ILLEGIBLE = "[?]"
"""The transcription's mark for a word nobody can read; any one answer word is right there."""

LOOP_GRAM = 3
"""The length of the word sequence a loop repeats."""

LOOP_REPEATS = 5
"""A sequence of :data:`LOOP_GRAM` words this often is a model repeating itself."""

LONG_SHARE = 1.5
"""An answer this many times the transcription's length is not a reading of the page."""

_TOKEN = re.compile(r"\w+(?:[-'\u2019]\w+)*|[^\w\s]")
"""A token of either text: a word (``E-Mail`` and ``don't`` are one) or one punctuation mark."""

_MARK = re.compile(r"\{([^{}]*)\}|\[\?\]")
"""A transcription's mark: a misspelt passage in braces, or a word nobody can read."""


@dataclass(frozen=True)
class Reference:
    """The transcription's tokens, and what the transcription says about each."""

    words: tuple[str, ...]
    braced: frozenset[int] = frozenset()
    """The indexes of the words the student misspelt; the word is as the student wrote it."""
    illegible: frozenset[int] = frozenset()
    """The indexes of the ``[?]`` marks: any one answer word is right there."""


@dataclass(frozen=True)
class Score:
    """One reading against one transcription."""

    cer: float
    """The share of the transcription's characters read wrongly."""
    wer: float
    """The share of the transcription's tokens read wrongly."""
    bag_wer: float
    """The same, in any order: how much the model misread, not how it laid the page out."""
    kept: float | None
    """The share of the student's misspelt words read as written; ``None`` when it holds none."""
    words: int
    """The transcription's tokens, the illegible marks not counted."""
    characters: int
    """The characters the CER is a share of: the legible tokens, one space between each two."""
    flags: Flags


def tokens(text: str) -> list[str]:
    """Return *text*'s tokens, in Unicode's composed form so ``ü`` is one letter either way."""
    return _TOKEN.findall(unicodedata.normalize("NFC", text))


def plain_answer(answer: str) -> str:
    """Return *answer* as a run stores it and a teacher reads it, as one line of text.

    The app strips reasoning and control tokens from every page; its post-processor then takes
    off a fence around the whole answer, reads Markdown or layout HTML and drops page numbers;
    what it stores is shown without markup.
    """
    processed = OcrOutputPostprocessor().process_pages([clean_ocr_text(answer)], label_pages=False)
    return " ".join(plain_text_of(processed.text, processed.text_format).split())


def reference_of(text: str) -> Reference:
    """Return the transcription's tokens, its braces removed and its illegible marks marked."""
    text = unicodedata.normalize("NFC", text)
    words: list[str] = []
    braced: set[int] = set()
    illegible: set[int] = set()
    position = 0
    for mark in _MARK.finditer(text):
        words.extend(tokens(text[position : mark.start()]))
        if mark.group(1) is None:
            illegible.add(len(words))
            words.append(ILLEGIBLE)
        else:
            for word in tokens(mark.group(1)):
                braced.add(len(words))
                words.append(word)
        position = mark.end()
    words.extend(tokens(text[position:]))
    return Reference(tuple(words), frozenset(braced), frozenset(illegible))


def _distance(
    reference: Sequence[str],
    answer: Sequence[str],
    matches: Callable[[int, int], bool],
    free: frozenset[int] = frozenset(),
) -> tuple[int, list[tuple[int | None, int | None]]]:
    """Return the edit distance between two word lists, and the pairs the best edit makes.

    *matches(reference index, answer index)* says whether an answer word counts as a reading of a
    reference word; a reference word in *free* costs nothing when the answer has no word for it, so
    a word nobody can read is not a word the model had to read.  The trace is deterministic: a
    substitution is taken before a deletion when both cost the same.
    """

    def dropped(row: int) -> int:
        """What a reference word the answer has no word for costs, by its index."""
        return 0 if row in free else 1

    rows, columns = len(reference), len(answer)
    cost = [[0] * (columns + 1) for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        cost[row][0] = cost[row - 1][0] + dropped(row - 1)
    for column in range(1, columns + 1):
        cost[0][column] = column
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            substitution = cost[row - 1][column - 1] + (0 if matches(row - 1, column - 1) else 1)
            cost[row][column] = min(
                substitution, cost[row - 1][column] + dropped(row - 1), cost[row][column - 1] + 1
            )
    pairs: list[tuple[int | None, int | None]] = []
    row, column = rows, columns
    while row or column:
        if (
            row
            and column
            and cost[row][column]
            == cost[row - 1][column - 1] + (0 if matches(row - 1, column - 1) else 1)
        ):
            pairs.append((row - 1, column - 1))
            row, column = row - 1, column - 1
        elif row and cost[row][column] == cost[row - 1][column] + dropped(row - 1):
            pairs.append((row - 1, None))
            row -= 1
        else:
            pairs.append((None, column - 1))
            column -= 1
    pairs.reverse()
    return cost[rows][columns], pairs


def _share(part: int, whole: int) -> float:
    """Return *part* of *whole* as a share; a wrong answer against nothing is all wrong."""
    if whole:
        return part / whole
    return 0.0 if part == 0 else 1.0


def _bag(reference: Reference, answer: Sequence[str], unreadable: set[int]) -> float:
    """Return how far the answer's words are from the transcription's, order not counting."""
    wanted = Counter(
        word for index, word in enumerate(reference.words) if index not in reference.illegible
    )
    heard = Counter(word for index, word in enumerate(answer) if index not in unreadable)
    missing = sum(max(0, wanted[word] - heard[word]) for word in wanted)
    extra = sum(max(0, heard[word] - wanted[word]) for word in heard)
    return _share(missing + extra, sum(wanted.values()))


def _loops(words: Sequence[str]) -> bool:
    """Whether one :data:`LOOP_GRAM`-word sequence repeats as often as a loop repeats."""
    if len(words) < LOOP_GRAM * LOOP_REPEATS:
        return False
    grams = Counter(
        tuple(words[index : index + LOOP_GRAM]) for index in range(len(words) - LOOP_GRAM + 1)
    )
    return max(grams.values()) >= LOOP_REPEATS


def score(answer: str, reference: str, *, cut_off: bool = False) -> Score:
    """Return what *answer* is worth against *reference*, a transcription of one page.

    *cut_off* says the model stopped before the end of the page — at its token cap, or at the
    run's deadline — which the answer alone cannot tell.
    """
    transcription = reference_of(reference)
    words = tokens(plain_answer(answer))

    def matches(index: int, other: int) -> bool:
        return index in transcription.illegible or transcription.words[index] == words[other]

    distance, pairs = _distance(transcription.words, words, matches, transcription.illegible)
    paired = {index: other for index, other in pairs if index is not None}
    unreadable = {
        other for index, other in pairs if index in transcription.illegible and other is not None
    }
    legible = [
        word
        for index, word in enumerate(transcription.words)
        if index not in transcription.illegible
    ]
    expected = " ".join(legible)
    read = " ".join(word for index, word in enumerate(words) if index not in unreadable)
    kept = sum(
        1
        for index in transcription.braced
        if (other := paired.get(index)) is not None and words[other] == transcription.words[index]
    )
    return Score(
        cer=_share(Levenshtein.distance(expected, read), len(expected)),
        wer=_share(distance, len(legible)),
        bag_wer=_bag(transcription, words, unreadable),
        kept=_share(kept, len(transcription.braced)) if transcription.braced else None,
        words=len(legible),
        characters=len(expected),
        flags=Flags(
            empty=not words,
            cut_off=cut_off,
            loop=_loops(words),
            long=bool(legible) and len(words) > LONG_SHARE * len(legible),
        ),
    )
