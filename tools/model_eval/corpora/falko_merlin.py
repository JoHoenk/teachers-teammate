"""The Falko-MERLIN GEC corpus (Boyd 2018): German learner texts with reference corrections.

Falko CC BY 3.0, MERLIN CC BY-SA 4.0.  The corpus is not part of the repository: download it, point
``--corpus falko-merlin=<the extracted data/ directory>`` at it and keep the built set outside the
repository too.

The files of a split ``s`` are ``fm-{s}.m2`` (the annotators' labels) and the two tokenised source
files ``source/falko-id-ctok-zh1.txt.{s}`` and ``source/merlin-id-ctok-zh1.txt.{s}``.  Splits:
``test`` (the default), ``dev``, ``train``.

Pointers, in the corpus's own terms:

* ``kind="errors"``: ``{"origin": "falko"|"merlin", "text": <text id>, "sentences": [start, end]}``
  — a run of consecutive sentences of that text;
* ``kind="correct"``: one pointer per sentence, ``{"origin": ..., "text": ..., "index": <sentence
  index>}``, each sentence unchanged in the corpus.

A pointer may name its ``split``; without it the default split is meant.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
import re
from typing import Any

from .. import error_labels
from ..error_labels import M2Sentence, ReferenceLabel
from ._base import CorpusAdapter, ResolvedItem, pointers


def detokenize(text: str) -> str:
    """Undo the space-separated tokenisation of the Falko-MERLIN files."""
    text = re.sub(r"\s+([.,;:!?%)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _sentence_chars(pairs: Sequence[tuple[str, str]]) -> list[tuple[int, int]]:
    """Return the character span of every sentence of the joined source of *pairs*.

    The sentences are joined with one space each, so sentence ``i`` starts after the lengths of the
    earlier ones plus one space per join.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for source, _ in pairs:
        spans.append((cursor, cursor + len(source)))
        cursor += len(source) + 1
    return spans


class FalkoMerlin(CorpusAdapter):
    """The German half of the test set: Falko essays and MERLIN letters, with their M2 labels."""

    NAME = "falko-merlin"
    LICENCE = "Falko CC BY 3.0, MERLIN CC BY-SA 4.0"
    RESTRICTED = False
    LANGUAGES = ("de",)
    HINT = "<the extracted Falko-MERLIN data/ directory>"
    SPLITS = ("test", "dev", "train")

    ORIGINS = ("falko", "merlin")
    """The two halves of the corpus, in the order the builder reads them."""

    def _files_of(self, split: str) -> dict[str, Path]:
        files = {f"fm-{split}.m2": self.root / f"fm-{split}.m2"}
        for origin in self.ORIGINS:
            key = f"source/{origin}-id-ctok-zh1.txt.{split}"
            files[key] = self.root / "source" / f"{origin}-id-ctok-zh1.txt.{split}"
        return files

    def rows(self, split: str | None = None) -> dict[str, list[tuple[str, str, str]]]:
        """``{origin: [(text id, source, reference) per sentence]}`` of one split."""
        name = self.split_name(split)
        return self._reading("rows", name, lambda: self._read_rows(name))

    def texts(self, split: str | None = None) -> dict[str, dict[str, list[tuple[str, str]]]]:
        """``{origin: {"<origin>:<text id>": [(source, reference) per sentence]}}``."""
        name = self.split_name(split)
        return self._reading("texts", name, lambda: self._read_texts(name))

    def m2(self, split: str | None = None) -> dict[str, dict[str, list[M2Sentence]]]:
        """``{origin: {"<origin>:<text id>": [M2 sentence per sentence]}}``.

        ``{}`` per origin when the M2 file is missing or does not fit the test files, so no labels
        are available.
        """
        name = self.split_name(split)
        return self._reading("m2", name, lambda: self._read_m2(name))

    def resolve(self, item: Mapping[str, Any]) -> ResolvedItem:
        """Build a German text or a group of unchanged German sentences from its pointer."""
        if item["kind"] == "correct":
            return self._correct(item)
        if item["kind"] != "errors":
            raise ValueError(f"{item['id']}: unknown kind {item['kind']!r}")
        found = pointers(item)
        if len(found) != 1:
            raise ValueError(
                f"{item['id']}: a German text with errors is one pointer, not {len(found)}"
            )
        pointer = found[0]
        split = self.split_name(pointer.get("split"))
        pairs = self._pairs(pointer, split)
        start, end = (int(value) for value in pointer["sentences"])
        chosen = pairs[start:end]
        if not chosen:
            raise ValueError(f"{item['id']}: no sentence {start} to {end} in {self._key(pointer)}")
        source = " ".join(sentence for sentence, _ in chosen)
        reference = " ".join(sentence for _, sentence in chosen)
        sentences = self.m2(split).get(pointer["origin"], {}).get(self._key(pointer), [])[start:end]
        origin = f"Falko-MERLIN {split}, {self._key(pointer)}" + (
            " (excerpt)" if len(chosen) < len(pairs) else ""
        )
        return self.make(
            item,
            origin=origin,
            source=source,
            reference=reference,
            labels=self._labels(source, reference, sentences),
            sentence_chars=_sentence_chars(chosen),
            corpus_unchanged=[
                source_line == reference_line for source_line, reference_line in chosen
            ],
        )

    def _correct(self, item: Mapping[str, Any]) -> ResolvedItem:
        """Build the group of sentences the annotators left alone, one pointer per sentence."""
        found = pointers(item)
        sentences: list[str] = []
        for pointer in found:
            pairs = self._pairs(pointer, self.split_name(pointer.get("split")))
            index = int(pointer["index"])
            if not 0 <= index < len(pairs):
                raise ValueError(
                    f"{item['id']}: no sentence {index} in {self._key(pointer)} "
                    f"({len(pairs)} sentences)"
                )
            source, reference = pairs[index]
            if source != reference:
                raise ValueError(
                    f"{item['id']}: sentence {index} of {self._key(pointer)} is not unchanged"
                )
            sentences.append(source)
        text = " ".join(sentences)
        split = self.split_name(found[0].get("split"))
        return self.make(
            item,
            origin=f"Falko-MERLIN {split} (unchanged sentences)",
            source=text,
            reference=text,
            sentences=sentences,
        )

    def _read_rows(self, split: str) -> dict[str, list[tuple[str, str, str]]]:
        rows: dict[str, list[tuple[str, str, str]]] = {}
        for origin in self.ORIGINS:
            entries: list[tuple[str, str, str]] = []
            path = self.root / "source" / f"{origin}-id-ctok-zh1.txt.{split}"
            for line in path.read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                if len(parts) == 3:
                    entries.append((parts[0], detokenize(parts[1]), detokenize(parts[2])))
            rows[origin] = entries
        return rows

    def _read_texts(self, split: str) -> dict[str, dict[str, list[tuple[str, str]]]]:
        texts: dict[str, dict[str, list[tuple[str, str]]]] = {}
        for origin, rows in self.rows(split).items():
            by_id: dict[str, list[tuple[str, str]]] = defaultdict(list)
            for text_id, source, reference in rows:
                by_id[f"{origin}:{text_id}"].append((source, reference))
            texts[origin] = dict(by_id)
        return texts

    def _read_m2(self, split: str) -> dict[str, dict[str, list[M2Sentence]]]:
        path = self.root / f"fm-{split}.m2"
        empty: dict[str, dict[str, list[M2Sentence]]] = {origin: {} for origin in self.ORIGINS}
        if not path.exists():
            return empty
        sentences = error_labels.read_m2(path)
        rows = self.rows(split)
        if len(sentences) != sum(len(entries) for entries in rows.values()):
            print(f"{path}: {len(sentences)} sentences, not as many as the test files: no labels")
            return empty
        result: dict[str, dict[str, list[M2Sentence]]] = {}
        first = 0
        for origin, entries in rows.items():
            by_id: dict[str, list[M2Sentence]] = defaultdict(list)
            for index, (text_id, _, _) in enumerate(entries):
                by_id[f"{origin}:{text_id}"].append(sentences[first + index])
            result[origin] = dict(by_id)
            first += len(entries)
        return result

    @staticmethod
    def _key(pointer: Mapping[str, Any]) -> str:
        """Return the ``origin:text`` key a German pointer names."""
        return f"{pointer['origin']}:{pointer['text']}"

    def _pairs(self, pointer: Mapping[str, Any], split: str) -> list[tuple[str, str]]:
        """Return every sentence pair of the German text a pointer names.

        Raises:
            ValueError: if the split has no such text.
        """
        origin = pointer["origin"]
        key = self._key(pointer)
        pairs = self.texts(split).get(origin, {}).get(key)
        if pairs is None:
            raise ValueError(f"{key} is not a text of the German {split} split")
        return pairs

    @staticmethod
    def _labels(
        source: str, reference: str, sentences: Sequence[M2Sentence]
    ) -> tuple[ReferenceLabel, ...]:
        """Return the corpus labels of a German excerpt, or none when its M2 sentences do not fit."""
        located = error_labels.locate(source, sentences) if sentences else None
        if located is None or len(located) != len(sentences):
            print(f"no labels for a German text starting {source[:40]!r}: M2 sentences do not fit")
            return ()
        return error_labels.label_reference(
            source, reference, list(zip(sentences, located, strict=True))
        )


ADAPTER = FalkoMerlin

__all__ = ["ADAPTER", "FalkoMerlin", "detokenize"]
