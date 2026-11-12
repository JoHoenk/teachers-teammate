"""Run a test set through the app's own corrector and turn the answers into result rows.

The runner keeps the app's real request path: the corrector a teacher's run uses
(:class:`~teachers_teammate.infrastructure.correction.NativeOllamaCorrector`, wrapped in the
chunking decorator), so a long text goes out in pieces exactly as it does in the app.  Only the
factory is injected, so a test can run the same loop with a corrector that returns scripted
answers; :func:`~.app_corrector.app_corrector_factory` builds the real one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
import time
from typing import TYPE_CHECKING, Protocol

from teachers_teammate.domain.correction_review import CorrectionReview, align

from .answer_mode import DEFAULT_MODE, AnswerMode
from .app_corrector import CorrectorFactory, pieces_of
from .error_labels import category_agreement
from .item_scores import item_scores
from .languages import LANGUAGE_NAMES
from .performance import Performance
from .quality import (
    count_explained,
    ordered_edits,
    unchanged_sentences,
)
from .results import (
    ResultRow,
    TestRecord,
    edit_score,
    record_key,
    review_for_counting,
    strip_added_marks,
)

if TYPE_CHECKING:
    from teachers_teammate.domain.correction_review import AlignedReview
    from teachers_teammate.interfaces import Corrector


class Instrumentation(Protocol):
    """Measures what one text costs: ``begin`` before it, ``end`` after (see :mod:`.measure`)."""

    def begin(self) -> None:
        """Note the state before the text."""

    def end(self) -> Performance:
        """Return what the text cost."""


class WritingModelComparison:
    """Compare writing models on a test set with reference corrections.

    Args:
        corrector_factory: Builds the corrector for one model and temperature.
        clock: Returns the current monotonic time; injected so a test can time a fixed run.
        language_names: How to name a language code when the corrector is called.
        instrumentation: Measures requests, durations and memory per text; ``None`` records
            nothing beyond the seconds of the whole text.
        mode: The answer mode the factory's correctors ask in; recorded in every row.  The
            caller gives the factory and the comparison the same value.
    """

    def __init__(
        self,
        corrector_factory: CorrectorFactory,
        clock: Callable[[], float] = time.monotonic,
        language_names: dict[str, str] | None = None,
        instrumentation: Instrumentation | None = None,
        mode: AnswerMode = DEFAULT_MODE,
    ) -> None:
        self._mode = mode
        self._instrumentation = instrumentation
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
        on_request: Callable[[TestRecord], None] | None = None,
    ) -> Iterator[ResultRow]:
        """Yield one result row per test record, skipping the ids in *done*.

        Args:
            records: The test set, in the order it should be run.
            model: The model tag to ask.
            temperature: The temperature to ask it at.
            done: Ids already stored in an earlier, interrupted run.
            on_request: Called with each record before it is sent, so a progress display can
                name the text that is running.

        Raises:
            ValueError: if a record is in a language the corrector cannot be asked in; checked
                before the first request, so a run never stops halfway.
        """
        if unknown := sorted({record.language for record in records} - set(self._language_names)):
            raise ValueError(
                f"no prompt language for {', '.join(repr(code) for code in unknown)}: add it to "
                "languages.LANGUAGE_NAMES, which needs a new eval version"
            )
        corrector = self._corrector_factory(model, temperature)
        skip = done or set()
        for record in records:
            key = record_key(record)
            if key in skip:
                continue
            if on_request is not None:
                on_request(record)
            yield self._row(corrector, record, key, model, temperature)

    def _row(
        self, corrector: Corrector, record: TestRecord, key: str, model: str, temperature: float
    ) -> ResultRow:
        """Send one test record to *corrector* and return the row describing the answer."""
        from teachers_teammate.infrastructure.correction import FALLBACK_WARNING  # noqa: PLC0415

        language = self._language_names[record.language]
        if self._instrumentation is not None:
            self._instrumentation.begin()
        started = self._clock()
        outcome = corrector.correct(record.source, language)
        seconds = self._clock() - started
        performance = self._instrumentation.end() if self._instrumentation is not None else None
        # The scores are computed on the text without the marks the model added, the same text the
        # explanations, the changes and the category agreement use: a "highlighted" fix is a fix.
        corrected = strip_added_marks(record.source, outcome.text)
        score = edit_score(record.kind, record.source, corrected, record.reference)
        aligned = (
            align(record.source, corrected, outcome.review) if outcome.review is not None else None
        )
        review = review_for_counting(self._mode, outcome.review, record.source, corrected)
        explained = count_explained(record.source, corrected, review, aligned=aligned)
        agreement = category_agreement(
            record.source,
            corrected,
            record.reference,
            outcome.review,
            record.labels,
            aligned=aligned,
        )
        return ResultRow(
            id=key,
            model=model,
            temperature=temperature,
            language=record.language,
            kind=record.kind,
            origin=record.origin,
            seconds=round(seconds, 1),
            failed=outcome.failed,
            # A chunked text joins the warnings of its pieces, so the fallback is looked for.
            fallback=FALLBACK_WARNING in (outcome.warning or ""),
            has_review=outcome.review is not None,
            source=record.source,
            reference=record.reference,
            output=outcome.text,
            warning=outcome.warning or "",
            notes=notes_of(outcome.review),
            changes=changes_of(record.source, corrected, aligned),
            unclear=aligned.unclear_count if aligned is not None else 0,
            explained=explained.explained,
            needed=explained.needed,
            orphans=explained.orphans,
            true_positives=score.true_positives,
            false_positives=score.false_positives,
            false_negatives=score.false_negatives,
            sentences=tuple(record.sentences),
            unchanged=unchanged_sentences(list(record.sentences), corrected),
            band=record.band,
            scores=(
                item_scores(
                    record.source,
                    corrected,
                    record.reference,
                    sentence_spans=record.sentence_spans,
                    unchanged_sentences=record.unchanged_sentences,
                )
                if record.kind == "errors"
                else {}
            ),
            # The mode is the one the factory was built for: the caller passes both.
            mode=self._mode,
            pieces=pieces_of(corrector, record.source, language),
            category_checked=agreement.checked,
            category_agreed=agreement.agreed,
            performance=performance,
        )


def notes_of(review: CorrectionReview | None) -> tuple[dict, ...]:
    """Return a review's notes as plain dicts, for the spot-check sheet.

    It turns a review into the dicts of :attr:`~.results.ResultRow.notes`, which is the runner's
    work: the renderer only reads them.
    """
    if review is None:
        return ()
    return tuple(
        note
        for sentence in review.sentences
        for note in (
            {
                "sentence": sentence.corrected,
                "original": change.original,
                "corrected": change.corrected,
                "category": change.category,
                "explanation": change.explanation,
            }
            for change in sentence.changes
        )
    )


def changes_of(source: str, corrected: str, aligned: AlignedReview | None) -> tuple[dict, ...]:
    """Return the model's changes with the note that explains each, in text order.

    The changes are the hunks of the word diff of *source* against *corrected*, the same list
    :func:`~.correction_review.align` numbered, so one entry of *aligned* belongs to one change.
    A note that explains no change (an orphan) is appended with ``edit: None``, because the
    export has to keep it too: it is what "the model claimed an edit it did not make" looks like.

    Args:
        source: The text before proofreading.
        corrected: The text after proofreading, without the marks the model added.
        aligned: The aligned review, or ``None`` in corrections-only mode.

    Returns:
        One dict per change (and per orphan) as the results row stores them.
    """
    if aligned is None:
        return ()
    entries: list[dict] = [
        {
            "edit": [start, end, "".join(replacement)],
            "category": change.category,
            "explanation": change.explanation,
        }
        for (start, end, replacement), change in zip(
            ordered_edits(source, corrected), aligned.changes, strict=True
        )
    ]
    entries.extend(
        {
            "edit": None,
            "category": note.category,
            "explanation": note.explanation,
            "orphan": True,
        }
        for note in aligned.orphan_notes
    )
    return tuple(entries)
