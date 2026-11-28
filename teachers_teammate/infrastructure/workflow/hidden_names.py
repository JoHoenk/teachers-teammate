"""Hiding personal data in one file's texts, and putting it back.

Anonymization is off in most runs: then every method here answers with the text it was given,
and the model sees the document as the teacher does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ...domain.stages import Stage
from ...interfaces import AnonymizationMap, Anonymizer, Reporter
from .proofread_text import hide_personal_info, names_left_hidden, restore_personal_info


class LazyAnonymization:
    """The anonymized extracted text of one file: computed on the first call, then reused."""

    def __init__(self, compute: Callable[[Stage | str], tuple[str, AnonymizationMap]]) -> None:
        self._compute = compute
        #: ``(text, mapping)`` once it was computed, else ``None``.
        self.result: tuple[str, AnonymizationMap] | None = None

    def __call__(self, stage: Stage | str) -> tuple[str, AnonymizationMap]:
        if self.result is None:
            self.result = self._compute(stage)
        return self.result


@dataclass(frozen=True)
class NameHiding:
    """Hides the personal data of one file's texts before a model sees them."""

    anonymizer: Anonymizer | None
    reporter: Reporter

    @property
    def enabled(self) -> bool:
        """Whether personal data is hidden at all."""
        return self.anonymizer is not None

    def reusable_text(
        self, anonymized: LazyAnonymization, text_anonymizer: Anonymizer | None
    ) -> str | None:
        """Return the anonymized text of *anonymized*, or ``None`` when nothing may reuse it.

        Only an anonymizer that also writes the anonymized text file can: its pass would be the
        same one, over the same text.
        """
        done = anonymized.result
        if done is None or text_anonymizer is None or text_anonymizer is not self.anonymizer:
            return None
        return done[0]

    def lazy(self, text: str) -> LazyAnonymization:
        """Return a callable that hides personal data in the extracted *text* once.

        Nothing runs until it is called.  A failing anonymizer (spaCy missing, a text over its
        length limit, …) ends this file with a message instead of the whole run.
        """
        return LazyAnonymization(lambda stage: self.hide(text, stage=stage))

    def hide(
        self, text: str, *, stage: Stage | str = "", before_model: bool = False
    ) -> tuple[str, AnonymizationMap]:
        """Hide personal data in *text* before a model sees it (no-op without an anonymizer)."""
        return hide_personal_info(
            self.anonymizer, text, stage=stage, reporter=self.reporter, before_model=before_model
        )

    def restore(self, text: str, anon_map: AnonymizationMap, *, stage: Stage | str = "") -> str:
        """Put the real personal data back into *text*; end the file when that fails."""
        return restore_personal_info(self.anonymizer, text, anon_map, stage=stage)

    @staticmethod
    def left_hidden(text: str, anon_map: AnonymizationMap) -> str:
        """Return the warning about personal data still hidden in *text*, or ``""``."""
        return names_left_hidden(text, anon_map)
