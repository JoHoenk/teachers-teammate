"""The LOCNESS part of W&I+LOCNESS v2.1: native-speaker English essays.

LOCNESS: no part to a third party.  So this corpus is ``RESTRICTED``: a run over its items stores no
words at all, only hashes and numbers.  The corpus is not part of the repository: point
``--corpus locness=<the extracted wi+locness/ directory>`` at it.

The adapter reads one file, ``json/N.dev.json``, and pins exactly that one.  It has no splits: the
native-speaker essays come with a dev split only.

Pointer, in the corpus's own terms: ``kind="correct"``, one per sentence,
``{"level": "N", "document": <essay id>, "span": [start, end]}`` — the character span of one
sentence no annotator touched.  An essay with errors is refused: labelling it would need
``m2/N.dev.gold.bea19.m2``, which this adapter does not pin.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ._base import CorpusAdapter, ResolvedItem, pointers
from ._bea import correct_item, read_rows


class Locness(CorpusAdapter):
    """The already-correct English sentences: the native-speaker essays, level ``N``."""

    NAME = "locness"
    LICENCE = "LOCNESS: no part to a third party"
    RESTRICTED = True
    LANGUAGES = ("en",)
    HINT = "<the extracted wi+locness/ directory>"

    LEVEL = "N"
    """The one level this corpus has."""

    ORIGIN = "W&I+LOCNESS dev, N (unedited sentences)"
    """Where the correct sentences come from, in the builder's words."""

    def _files_of(self, split: str) -> dict[str, Path]:  # noqa: ARG002  # no splits
        return {"json/N.dev.json": self.root / "json" / "N.dev.json"}

    def rows(self) -> list[dict]:
        """The rows of ``json/N.dev.json``, in file order."""
        return self._reading("rows", "", lambda: read_rows(self.root / "json" / "N.dev.json"))

    def resolve(self, item: Mapping[str, Any]) -> ResolvedItem:
        """Build a group of already-correct sentences from its pointers, one per sentence."""
        if item["kind"] != "correct":
            raise ValueError(
                f"{item['id']}: the corpus {self.NAME!r} has no {item['kind']!r} items: labelling "
                "an essay would need m2/N.dev.gold.bea19.m2, which it does not pin"
            )
        for pointer in pointers(item):
            self.split_name(pointer.get("split"))
            if pointer["level"] != self.LEVEL:
                raise ValueError(
                    f"{item['id']}: the corpus {self.NAME!r} has no level {pointer['level']!r}; "
                    f"only {self.LEVEL!r}"
                )
        return correct_item(
            self, item, rows=self.rows(), level=self.LEVEL, split="dev", origin=self.ORIGIN
        )


ADAPTER = Locness

__all__ = ["ADAPTER", "Locness"]
