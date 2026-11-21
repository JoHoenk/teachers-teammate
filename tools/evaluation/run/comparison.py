"""Run a test set through a corrector and turn the answers into the run file's answer lines.

The runner keeps the app's real request path: the corrector a teacher's run uses, wrapped in the
chunking decorator, so a long text goes out in pieces exactly as it does in the app.  Only the
factory is injected, so a test can run the same loop with a corrector that returns scripted
answers; :func:`~.app_corrector.app_corrector_factory` builds the real one.

What this module does **not** do is score.  An :class:`~..core.records.Answer` holds what the model
said and what it cost, and nothing derived: the edit counts, the explanation counts, the
category agreement and the item scores are :mod:`..report.score`'s work, computed when a report
reads the file.  That is why a scorer change needs no new run, and why the run cannot disagree with
the report about the same answer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
import time
from typing import TYPE_CHECKING, Protocol

from teachers_teammate.domain.proofreading.result import CorrectionOutcome
from teachers_teammate.exceptions import StageError

from ..core.languages import LANGUAGE_NAMES
from ..core.performance import Performance
from ..core.records import Answer, text_id
from .app_corrector import CorrectorFactory, pieces_of

if TYPE_CHECKING:
    from teachers_teammate.interfaces import Corrector

    from ..core.records import TestRecord


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
    """

    def __init__(
        self,
        corrector_factory: CorrectorFactory,
        clock: Callable[[], float] = time.monotonic,
        language_names: dict[str, str] | None = None,
        instrumentation: Instrumentation | None = None,
    ) -> None:
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
    ) -> Iterator[Answer]:
        """Yield one answer per test record, skipping the ids in *done*.

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
                "core.languages.LANGUAGE_NAMES, which needs a new eval version"
            )
        corrector = self._corrector_factory(model, temperature)
        skip = done or set()
        for record in records:
            key = text_of(record)
            if key in skip:
                continue
            if on_request is not None:
                on_request(record)
            yield self._answer(corrector, record, key)

    def _answer(self, corrector: Corrector, record: TestRecord, key: str) -> Answer:
        """Send one test record to *corrector* and return the answer line describing it."""
        language = self._language_names[record.language]
        if self._instrumentation is not None:
            self._instrumentation.begin()
        started = self._clock()
        failure = ""
        try:
            outcome = corrector.correct(record.source, language)
        except StageError as exc:
            # A proofreading that did not happen is a row in the run file, not the end of the
            # run: the answer keeps the source and says what the stage reported.
            outcome, failure = CorrectionOutcome(text=record.source), str(exc)
        if outcome.failed and not failure:
            # The tools' own single-request correctors report a failed call in the outcome
            # instead of raising it.
            failure = outcome.warning or ""
        seconds = self._clock() - started
        performance = self._instrumentation.end() if self._instrumentation is not None else None
        return Answer(
            text=key,
            text_sha256=record.text_sha256,
            output=outcome.text,
            failed=bool(failure) or outcome.failed,
            fallback=outcome.fell_back,
            warning=failure or outcome.warning or "",
            left_out=outcome.left_out,
            review=outcome.review,
            requests=performance.requests if performance is not None else (),
            loaded=performance.loaded if performance is not None else (),
            shared=performance.shared if performance is not None else False,
            vram_baseline_mb=performance.vram_baseline_mb if performance is not None else None,
            vram_peak_mb=performance.vram_peak_mb if performance is not None else None,
            pieces=pieces_of(corrector, record.source, language),
            seconds=round(seconds, 1),
        )


def text_of(record: TestRecord) -> str:
    """Return the id an answer uses for *record*: ``<language>/<record id>``.

    The one name a text has outside its own set file; :func:`..core.records.text_id` is the
    definition, and this alias keeps the run layer's own vocabulary.
    """
    return text_id(record)


__all__ = ["Instrumentation", "WritingModelComparison", "text_of"]
