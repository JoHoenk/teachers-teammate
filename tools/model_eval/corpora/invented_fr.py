"""The invented French set: eight texts and ten correct sentences, written by an AI model.

No public learner corpus with corrections exists for French, so ``tools/gec_fr_invented.jsonl``
holds invented texts instead (see ``tools/prepare_gec_testset.py``): French results are indicative
only.  The file is part of the repository, so this corpus needs no path on the command line.

Pointer, in the corpus's own terms: ``{"text": <id in tools/gec_fr_invented.jsonl>}``, for both
kinds.  The corpus has no splits.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from ._base import CorpusAdapter, ResolvedItem, pointers


class InventedFrench(CorpusAdapter):
    """The invented French texts: the one corpus that is in the repository."""

    NAME = "invented-fr"
    LICENCE = "ours"
    RESTRICTED = False
    LANGUAGES = ("fr",)
    HINT = "the invented French set (default: tools/gec_fr_invented.jsonl)"
    DEFAULT_ROOT = Path(__file__).parents[2] / "gec_fr_invented.jsonl"
    """The file next to the tools, found from this module's own path."""

    def _files_of(self, split: str) -> dict[str, Path]:  # noqa: ARG002  # no splits
        return {"tools/gec_fr_invented.jsonl": self.root}

    def records(self) -> dict[str, dict]:
        """``{id: record}`` of the invented set, in file order."""
        return self._reading("records", "", self._read_records)

    def resolve(self, item: Mapping[str, Any]) -> ResolvedItem:
        """Build an invented French text or group of sentences from its one pointer."""
        found = pointers(item)
        if len(found) != 1:
            raise ValueError(f"{item['id']}: a French text is one pointer, not {len(found)}")
        self.split_name(found[0].get("split"))
        key = found[0]["text"]
        record = self.records().get(key)
        if record is None:
            raise ValueError(f"{key!r} is not a text of the invented French set")
        if record["kind"] != item["kind"]:
            raise ValueError(f"{key}: the corpus makes it a {record['kind']!r} text")
        if item["kind"] == "correct":
            sentences = tuple(record["sentences"])
            text = " ".join(sentences)
            return self.make(
                item,
                origin=record["origin"],
                source=text,
                reference=text,
                sentences=sentences,
            )
        return self.make(
            item,
            origin=record["origin"],
            source=record["source"],
            reference=record["reference"],
        )

    def _read_records(self) -> dict[str, dict]:
        records: dict[str, dict] = {}
        for line in self.root.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                records[record["id"]] = record
        return records


ADAPTER = InventedFrench

__all__ = ["ADAPTER", "InventedFrench"]
