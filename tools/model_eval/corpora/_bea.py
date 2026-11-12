"""The BEA-2019 JSON format that W&I and LOCNESS share: reading a file, applying its edits.

Both corpora come from the BEA-2019 shared task and use the same two shapes: a JSON file with one
essay per line (``id``, ``cefr``, ``text``, ``edits``), and an M2 file with the annotators' labels.
The two corpora stay two adapters — the manifest records them as two, with different licences and
different strictness — but the reading and the two item shapes are one code path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
from typing import Any

from .. import error_labels
from ..error_labels import M2Sentence, ReferenceLabel
from ._base import CorpusAdapter, ResolvedItem, pointers


def read_rows(path: Path) -> list[dict]:
    """Return the rows of a BEA JSON file, in file order."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def apply_edits(text: str, edits: list) -> str:
    """Apply the first annotator's ``[start, end, replacement]`` edits (``None`` = no correction).

    Sorted by span only: the tail is applied first, so the earlier spans keep their offsets.  The
    replacement cannot be part of the key, because it may be ``None``.
    """
    for start, end, replacement in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
        if replacement is not None:
            text = text[:start] + replacement + text[end:]
    return text


def index_of(rows: Sequence[dict], document: str, level: str, split: str) -> int:
    """Return the position in *rows* of the row of *level* whose id is *document*.

    Raises:
        ValueError: if the split has no such document.
    """
    for index, row in enumerate(rows):
        if row["id"] == document:
            return index
    raise ValueError(f"{document!r} is not a document of the English {level} {split} split")


def row_of(rows: Sequence[dict], document: str, level: str, split: str) -> dict:
    """Return the row of *level* whose id is *document*.

    Raises:
        ValueError: if the split has no such document.
    """
    return rows[index_of(rows, document, level, split)]


def essay_item(  # one keyword per reading the essay needs
    adapter: CorpusAdapter,
    item: Mapping[str, Any],
    *,
    rows: Mapping[str, list[dict]],
    sentences: Mapping[str, list[M2Sentence]],
    blocks: Mapping[str, list[tuple[int, list] | None]],
    split: str,
) -> ResolvedItem:
    """Build an essay with errors from its one pointer: the whole essay, corrected.

    Raises:
        ValueError: if the item is not one pointer, or the pointer names no document of its level.
    """
    found = pointers(item)
    if len(found) != 1:
        raise ValueError(f"{item['id']}: an English essay is one pointer, not {len(found)}")
    pointer = found[0]
    level = pointer["level"]
    index = index_of(rows.get(level, ()), pointer["document"], level, split)
    row = rows[level][index]
    if not row["edits"]:
        raise ValueError(f"{item['id']}: document {row['id']!r} has no annotator edits")
    reference = re.sub(r"[ \t]{2,}", " ", apply_edits(row["text"], row["edits"][0][1]))
    block = blocks[level][index]
    return adapter.make(
        item,
        origin=f"W&I+LOCNESS {split}, level {level} ({row['cefr']}), id {row['id']}",
        source=row["text"],
        reference=reference,
        labels=labels_of(row["text"], reference, sentences[level], block),
        sentence_chars=_sentence_chars(block),
        corpus_unchanged=_corpus_unchanged(sentences[level], block),
    )


def _sentence_chars(block: tuple[int, list] | None) -> list[tuple[int, int]]:
    """Return the character span of every located sentence of an essay.

    ``locate`` normalises quotes and dashes character for character, so its offsets are offsets
    into the essay.  A located sentence without tokens has no span and is skipped.
    """
    if block is None:
        return []
    _, located = block
    return [(spans[0][0], spans[-1][1]) for spans in located if spans]


def _corpus_unchanged(
    sentences: Sequence[M2Sentence], block: tuple[int, list] | None
) -> list[bool]:
    """Return, per located sentence, whether the corpus itself calls it unchanged.

    A sentence with an edit is changed; so is one whose only annotation is ``UNK``, an error the
    annotator saw but did not correct.  A located sentence without tokens is skipped, as in
    :func:`_sentence_chars`, so the two lists stay the same length.
    """
    if block is None:
        return []
    first, located = block
    return [
        not sentence.edits and not sentence.uncorrected
        for sentence, spans in zip(sentences[first : first + len(located)], located, strict=True)
        if spans
    ]


def correct_item(
    adapter: CorpusAdapter,
    item: Mapping[str, Any],
    *,
    rows: Sequence[dict],
    level: str,
    split: str,
    origin: str,
) -> ResolvedItem:
    """Build a group of already-correct sentences from its pointers, one per sentence.

    Raises:
        ValueError: if a pointer names no document of *level*.
    """
    sentences: list[str] = []
    for pointer in pointers(item):
        row = row_of(rows, pointer["document"], level, split)
        start, end = (int(value) for value in pointer["span"])
        sentences.append(row["text"][start:end])
    text = " ".join(sentences)
    return adapter.make(item, origin=origin, source=text, reference=text, sentences=sentences)


def labels_of(
    source: str, reference: str, sentences: Sequence[M2Sentence], block: tuple[int, list] | None
) -> tuple[ReferenceLabel, ...]:
    """Return the corpus labels of an English essay, or none when its M2 sentences are missing."""
    if block is None:
        print(f"no labels for an English essay starting {source[:40]!r}: M2 sentences not found")
        return ()
    first, spans = block
    return error_labels.label_reference(
        source, reference, list(zip(sentences[first : first + len(spans)], spans, strict=True))
    )


__all__ = [
    "apply_edits",
    "correct_item",
    "essay_item",
    "index_of",
    "labels_of",
    "read_rows",
    "row_of",
]
