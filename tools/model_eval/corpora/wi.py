"""The Write & Improve part of W&I+LOCNESS v2.1 (BEA-2019): English learner essays.

Cambridge W&I: non-commercial research, excerpts under 100 words.  The corpus is not part of the
repository: download it, point ``--corpus wi=<the extracted wi+locness/ directory>`` at it and keep
the built set outside the repository too.

The files of a split ``s`` are, for each of the levels ``A``, ``B`` and ``C``, ``json/{L}.{s}.json``
(the essays) and ``m2/{L}.{s}.gold.bea19.m2`` (the annotators' labels).  Splits: ``dev`` (the
default), ``train``.  The native-speaker level ``N`` is :mod:`.locness`, a corpus of its own.

Pointer, in the corpus's own terms: ``kind="errors"``, ``{"level": "A"|"B"|"C", "document": <essay
id>}`` — the whole essay.  A pointer may name its ``split``; without it the default split is meant.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .. import error_labels
from ..error_labels import M2Sentence
from ._base import CorpusAdapter, ResolvedItem, pointers
from ._bea import essay_item, read_rows


class WriteAndImprove(CorpusAdapter):
    """The English essays with errors: levels A, B and C of the W&I part."""

    NAME = "wi"
    LICENCE = "Cambridge W&I: non-commercial research, excerpts under 100 words"
    RESTRICTED = False
    LANGUAGES = ("en",)
    HINT = "<the extracted wi+locness/ directory>"
    SPLITS = ("dev", "train")

    LEVELS = ("A", "B", "C")
    """The CEFR levels of the W&I part; ``N`` is the native-speaker corpus, :mod:`.locness`."""

    def _files_of(self, split: str) -> dict[str, Path]:
        files: dict[str, Path] = {}
        for level in self.LEVELS:
            files[f"json/{level}.{split}.json"] = self.root / "json" / f"{level}.{split}.json"
            files[f"m2/{level}.{split}.gold.bea19.m2"] = (
                self.root / "m2" / f"{level}.{split}.gold.bea19.m2"
            )
        return files

    def rows(self, split: str | None = None) -> dict[str, list[dict]]:
        """``{level: [row of the json file]}``, in file order."""
        name = self.split_name(split)
        return self._reading(
            "rows",
            name,
            lambda: {
                level: read_rows(self.root / "json" / f"{level}.{name}.json")
                for level in self.LEVELS
            },
        )

    def sentences(self, split: str | None = None) -> dict[str, list[M2Sentence]]:
        """``{level: [M2 sentence]}``; empty for a level without an M2 file."""
        name = self.split_name(split)
        return self._reading("sentences", name, lambda: self._read_sentences(name))

    def blocks(self, split: str | None = None) -> dict[str, list[tuple[int, list] | None]]:
        """``{level: [first M2 sentence and its spans per row, or None]}`` (``split_into_texts``)."""
        name = self.split_name(split)
        return self._reading("blocks", name, lambda: self._read_blocks(name))

    def resolve(self, item: Mapping[str, Any]) -> ResolvedItem:
        """Build an English essay with errors from its one pointer."""
        if item["kind"] != "errors":
            raise ValueError(
                f"{item['id']}: the corpus {self.NAME!r} has no {item['kind']!r} items; "
                "the correct sentences come from locness"
            )
        found = pointers(item)
        if len(found) != 1:
            raise ValueError(f"{item['id']}: an English essay is one pointer, not {len(found)}")
        level = found[0]["level"]
        if level not in self.LEVELS:
            raise ValueError(
                f"{item['id']}: the corpus {self.NAME!r} has no level {level!r}; "
                f"known: {', '.join(self.LEVELS)}"
            )
        split = self.split_name(found[0].get("split"))
        return essay_item(
            self,
            item,
            rows=self.rows(split),
            sentences=self.sentences(split),
            blocks=self.blocks(split),
            split=split,
        )

    def _read_sentences(self, split: str) -> dict[str, list[M2Sentence]]:
        sentences: dict[str, list[M2Sentence]] = {}
        for level in self.LEVELS:
            path = self.root / "m2" / f"{level}.{split}.gold.bea19.m2"
            sentences[level] = error_labels.read_m2(path) if path.exists() else []
        return sentences

    def _read_blocks(self, split: str) -> dict[str, list[tuple[int, list] | None]]:
        rows, sentences = self.rows(split), self.sentences(split)
        return {
            level: error_labels.split_into_texts(
                [row["text"] for row in rows[level]], sentences[level]
            )
            for level in self.LEVELS
        }


ADAPTER = WriteAndImprove

__all__ = ["ADAPTER", "WriteAndImprove"]
