"""Run a test set through the app's own corrector and turn the answers into result rows.

The runner keeps the app's real request path: the corrector a teacher's run uses
(:class:`~teachers_teammate.infrastructure.correction.NativeOllamaCorrector`, wrapped in the
chunking decorator).  Only the factory is injected, so a test can run the same loop with a
corrector that returns scripted answers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
import time
from typing import TYPE_CHECKING

from teachers_teammate.domain.proofreading_quality import (
    EditScore,
    count_explained,
    score_edits,
    unchanged_sentences,
)

from .report import notes_of
from .results import ResultRow, TestRecord

if TYPE_CHECKING:
    from teachers_teammate.interfaces import Corrector

LANGUAGE_NAMES: dict[str, str] = {"de": "German", "en": "English", "fr": "French"}

CorrectorFactory = Callable[[str, float], "Corrector"]
"""``(model, temperature) -> corrector``; replaced in tests."""


class WritingModelComparison:
    """Compare writing models on a test set with reference corrections.

    Args:
        corrector_factory: Builds the corrector for one model and temperature.
        clock: Returns the current monotonic time; injected so a test can time a fixed run.
        language_names: How to name a language code when the corrector is called.
    """

    def __init__(
        self,
        corrector_factory: CorrectorFactory,
        clock: Callable[[], float] = time.monotonic,
        language_names: dict[str, str] | None = None,
    ) -> None:
        self._corrector_factory = corrector_factory
        self._clock = clock
        self._language_names = dict(language_names or LANGUAGE_NAMES)

    def run(
        self,
        records: Sequence[TestRecord],
        *,
        model: str,
        temperature: float,
        done: set[str] | None = None,
    ) -> Iterator[ResultRow]:
        """Yield one result row per test record, skipping the ids in *done*.

        Args:
            records: The test set, in the order it should be run.
            model: The model tag to ask.
            temperature: The temperature to ask it at.
            done: Ids already stored in an earlier, interrupted run.
        """
        corrector = self._corrector_factory(model, temperature)
        skip = done or set()
        for record in records:
            key = f"{record.language}/{record.id}"
            if key in skip:
                continue
            yield self._row(corrector, record, key, model, temperature)

    def _row(
        self, corrector: Corrector, record: TestRecord, key: str, model: str, temperature: float
    ) -> ResultRow:
        """Send one test record to *corrector* and return the row describing the answer."""
        from teachers_teammate.infrastructure.correction import FALLBACK_WARNING  # noqa: PLC0415

        started = self._clock()
        outcome = corrector.correct(record.source, self._language_names[record.language])
        seconds = self._clock() - started
        score = self._score(record, outcome.text)
        explained = self._explained(record, outcome.text, outcome.review)
        return ResultRow(
            id=key,
            model=model,
            temperature=temperature,
            language=record.language,
            kind=record.kind,
            origin=record.origin,
            seconds=round(seconds, 1),
            failed=outcome.failed,
            fallback=outcome.warning == FALLBACK_WARNING,
            has_review=outcome.review is not None,
            source=record.source,
            reference=record.reference,
            output=outcome.text,
            warning=outcome.warning or "",
            notes=notes_of(outcome.review),
            explained=explained.explained,
            needed=explained.needed,
            orphans=explained.orphans,
            true_positives=score.true_positives,
            false_positives=score.false_positives,
            false_negatives=score.false_negatives,
            sentences=tuple(record.sentences),
            unchanged=self._unchanged(record, outcome.text),
        )

    @staticmethod
    def _explained(record: TestRecord, output: str, review):
        return count_explained(record.source, output, review)

    @staticmethod
    def _score(record: TestRecord, output: str) -> EditScore:
        if record.kind != "errors":
            return EditScore(true_positives=0, false_positives=0, false_negatives=0)
        return score_edits(record.source, output, record.reference)

    @staticmethod
    def _unchanged(record: TestRecord, output: str) -> int:
        return unchanged_sentences(list(record.sentences), output)
