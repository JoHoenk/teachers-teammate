"""An invented corpus with the bands of ``gec-2026-12``, small enough to build in a test.

The real set draws on Falko-MERLIN and W&I, which are not part of the repository, so the tests of
the recipe build their own corpus in ``tmp_path``: a Falko-MERLIN ``test`` split and a W&I ``dev``
split, in the corpora's own file formats, with invented sentences whose edits are known.  Every
slot of the recipe gets one candidate more than it needs, so the seed has a choice, and the two
"pages" slots get two candidates each.

The texts that must never be chosen are here too: one with too few reference edits, one that is a
near-rewrite, one of 79 and one of 151 words (outside the short band), one of 1,250 words (outside
the pages band) and four English essays that break the same filters (66 and 160 words) and one whose M2 sentences are missing, so it has no labels.

Every generated word carries a running number: ``SequenceMatcher`` slows down when the same tokens
recur hundreds of times, and a text of 700 words is built from a handful of shapes.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from dataclasses import dataclass
import itertools
import json
from pathlib import Path

from tools.model_eval.testset import Corpora

ERROR_CODE = "R:VERB:FORM"
"""The label every invented error carries."""

ERROR_POSITION = 0
"""Which word of a sentence the invented error replaces."""


@dataclass(frozen=True)
class Text:
    """One invented text: how long it is, how many errors it has, and where it lives."""

    id: str
    origin: str
    """``falko`` or ``merlin`` for German, the W&I level (``A``, ``B``, ``C``) for English."""

    words: int
    sentences: int
    edits: int
    word_length: int = 5
    """How many characters a word has; long words are how a text reaches ``SPLIT_CHARS``."""

    m2: bool = True
    """``False``: the M2 sentences are missing, so the text has no labels and no spans."""


def _texts() -> tuple[Text, ...]:
    """Return every text of the corpus, in the order the files list them."""
    texts: list[Text] = []
    for number in range(1, 6):
        texts.append(Text(f"merlin-short-{number}", "merlin", 100, 10, 3))
    for number in range(1, 4):
        texts.append(Text(f"merlin-page-{number}", "merlin", 300, 20, 5))
    for number in range(1, 5):
        texts.append(Text(f"falko-page-{number}", "falko", 300, 20, 5))
    for number in range(1, 3):
        texts.append(Text(f"falko-whole-{number}", "falko", 700, 50, 8))
        texts.append(Text(f"falko-split-{number}", "falko", 700, 50, 8, word_length=10))
    texts.append(Text("merlin-few-edits", "merlin", 100, 10, 2))
    texts.append(Text("merlin-rewrite", "merlin", 82, 41, 41))
    texts.append(Text("merlin-79", "merlin", 79, 8, 3))
    texts.append(Text("merlin-151", "merlin", 151, 10, 3))
    texts.append(Text("falko-1250", "falko", 1250, 60, 8, word_length=10))
    for number in range(1, 6):
        texts.append(Text(f"a-short-{number}", "A", 100, 10, 3))
    for number in range(1, 5):
        texts.append(Text(f"b-page-{number}", "B", 300, 20, 5))
    for number in range(1, 4):
        texts.append(Text(f"c-page-{number}", "C", 300, 20, 5))
    texts.append(Text("b-whole-1", "B", 700, 50, 8))
    texts.append(Text("c-whole-1", "C", 700, 50, 8))
    texts.append(Text("b-split-1", "B", 700, 50, 8, word_length=10))
    texts.append(Text("c-split-1", "C", 700, 50, 8, word_length=10))
    # English essays count their full stops as words, so the sizes are chosen with that in mind.
    texts.append(Text("a-few-edits", "A", 100, 10, 2))
    texts.append(Text("a-rewrite", "A", 60, 60, 60))
    texts.append(Text("a-66", "A", 60, 6, 3))
    texts.append(Text("a-160", "A", 150, 10, 3))
    texts.append(Text("a-nolabels", "A", 100, 10, 3, m2=False))
    return tuple(texts)


TEXTS = _texts()
"""Every text of the corpus, in the order the files list them."""

NEVER_CHOSEN = (
    "merlin-few-edits",
    "merlin-rewrite",
    "merlin-79",
    "merlin-151",
    "falko-1250",
    "a-few-edits",
    "a-rewrite",
    "a-66",
    "a-160",
    "a-nolabels",
)
"""The texts a filter keeps out, so no selection may contain one."""

GERMAN_ORIGINS = ("falko", "merlin")
"""The two halves of the German corpus, in the order the builder reads them."""

ENGLISH_LEVELS = ("A", "B", "C")
"""The W&I levels, in the order the builder reads them."""


def _word(counter: int, length: int, *, wrong: bool = False) -> str:
    """Return one invented word of *length* characters, unique by *counter*."""
    stem = f"{'f' if wrong else 'w'}{counter}"
    return stem + "e" * (length - len(stem))


def _sizes(words: int, sentences: int) -> list[int]:
    """Return how many words each of *sentences* sentences gets, as evenly as possible."""
    base, rest = divmod(words, sentences)
    return [base + (1 if index < rest else 0) for index in range(sentences)]


def _pairs(text: Text) -> list[tuple[list[str], list[str], int | None]]:
    """Return the sentence pairs of *text*: source tokens, reference tokens, error position."""
    counter: Iterator[int] = itertools.count(1)
    pairs: list[tuple[list[str], list[str], int | None]] = []
    for index, size in enumerate(_sizes(text.words, text.sentences)):
        tokens = [_word(next(counter), text.word_length) for _ in range(size)]
        reference = [*tokens, "."]
        position = None
        if index < text.edits:
            position = ERROR_POSITION
            tokens[position] = _word(next(counter), text.word_length, wrong=True)
        pairs.append(([*tokens, "."], reference, position))
    return pairs


def _m2_sentence(source: list[str], reference: list[str], position: int | None) -> str:
    """Return one M2 sentence with its annotation, or without one when it has no error."""
    lines = ["S " + " ".join(source)]
    if position is not None:
        lines.append(
            f"A {position} {position + 1}|||{ERROR_CODE}|||{reference[position]}"
            "|||REQUIRED|||-NONE-|||0"
        )
    return "\n".join(lines) + "\n\n"


def _german(root: Path, texts: Collection[Text]) -> None:
    """Write the two source files and the M2 file of the German test split."""
    (root / "source").mkdir(parents=True, exist_ok=True)
    m2: list[str] = []
    for origin in GERMAN_ORIGINS:
        lines: list[str] = []
        for text in texts:
            if text.origin != origin:
                continue
            for source, reference, position in _pairs(text):
                lines.append(f"{text.id}\t{' '.join(source)}\t{' '.join(reference)}")
                m2.append(_m2_sentence(source, reference, position))
        path = root / "source" / f"{origin}-id-ctok-zh1.txt.test"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "fm-test.m2").write_text("".join(m2), encoding="utf-8")


def _essay(text: Text, pairs: list[tuple[list[str], list[str], int | None]]) -> dict:
    """Return the BEA JSON row of *text*, with its edits as character offsets."""
    tokens: list[str] = []
    edits: list[list] = []
    cursor = 0
    for source, reference, position in pairs:
        if position is not None:
            start = cursor + sum(len(token) + 1 for token in source[:position])
            edits.append([start, start + len(source[position]), reference[position]])
        tokens.extend(source)
        cursor += sum(len(token) + 1 for token in source)
    return {
        "id": text.id,
        "cefr": text.origin,
        "text": " ".join(tokens),
        "edits": [[0, edits]],
        "userid": "u-1",
    }


def _english(root: Path, texts: Collection[Text]) -> None:
    """Write the JSON and M2 files of the W&I dev split, one pair per level."""
    (root / "json").mkdir(parents=True, exist_ok=True)
    (root / "m2").mkdir(parents=True, exist_ok=True)
    for level in ENGLISH_LEVELS:
        rows: list[str] = []
        m2: list[str] = []
        for text in texts:
            if text.origin != level:
                continue
            pairs = _pairs(text)
            rows.append(json.dumps(_essay(text, pairs)) + "\n")
            if text.m2:
                m2.extend(
                    _m2_sentence(source, reference, position)
                    for source, reference, position in pairs
                )
        (root / "json" / f"{level}.dev.json").write_text("".join(rows), encoding="utf-8")
        (root / "m2" / f"{level}.dev.gold.bea19.m2").write_text("".join(m2), encoding="utf-8")


def build_band_corpus(root: Path, *, leave_out: Collection[str] = ()) -> Corpora:
    """Write the corpus below *root* and return the :class:`Corpora` that reads it.

    *leave_out* names texts to omit, so a test can make a slot's pool too small.
    """
    texts = [text for text in TEXTS if text.id not in set(leave_out)]
    falko = root / "fm" / "data"
    english = root / "bea" / "wi+locness"
    _german(falko, texts)
    _english(english, texts)
    return Corpora({"falko-merlin": falko, "wi": english})


__all__ = [
    "ENGLISH_LEVELS",
    "GERMAN_ORIGINS",
    "NEVER_CHOSEN",
    "TEXTS",
    "Text",
    "build_band_corpus",
]
