"""The proofreading stage: one file's transcription proofread, or the answer a run stored."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from pathlib import Path

from ...domain.proofreading.review import CorrectionReview, fitted_review
from ...domain.stages import ProgressStage, Stage
from ...domain.teacher_work import CurrentTexts, text_identity
from ...exceptions import RunStoppedError
from ...interfaces import Corrector
from ...texts import report as report_text
from ...texts.review import REVIEW_DOES_NOT_FIT
from ..ocr_formats import plain_text_of
from .cache_service import CacheContext
from .hidden_names import LazyAnonymization, NameHiding
from .processing_result import Stopped, stopped_result
from .proofread_text import ProofreadOutcome, ProofreadText
from .stage_shared import StageShared

_logger = logging.getLogger(__name__)


def _served_review(
    ctx: CacheContext, texts: CurrentTexts, file: Path
) -> tuple[CorrectionReview | None, list[str]]:
    """Return the stored review and the warnings its serving adds.

    A review whose words are no longer at the offsets it points at is dropped, by the same rule
    every reader of a stored document applies: the corrected text stays, the explanations do not,
    and the stored review is left as it was written — this run does not write it.
    """
    stored = CorrectionReview.from_dict(ctx.state.correction_review)
    review = fitted_review(stored, plain_text_of(texts.transcription, texts.transcription_format))
    if stored is not None and review is None:
        _logger.warning(
            "Dropped the review of %s: it does not match the text it was made for", file.name
        )
        return None, [REVIEW_DOES_NOT_FIT]
    return review, []


@dataclass(frozen=True)
class Proofread:
    """What the proofreading hands to the review and to the outputs."""

    review: CorrectionReview | None
    """The explanations that go with the corrected text, when the answer has any."""
    warnings: tuple[str, ...]
    """What is worth telling the teacher about this proofreading."""
    ctx: CacheContext
    """The cache state the proofreading left: the corrected text is in it."""

    @property
    def stored_warning(self) -> str:
        """The warnings as the one sentence the cache and a result carry."""
        return "; ".join(self.warnings)


class CorrectionStage:
    """Proofreads one file, or serves the answer an earlier run stored."""

    def __init__(
        self, shared: StageShared, *, correction: Corrector | None, names: NameHiding
    ) -> None:
        self._shared = shared
        self._correction = correction
        self._names = names

    def run(
        self,
        file: Path,
        texts: CurrentTexts,
        preview_img: str,
        ctx: CacheContext,
        anonymized: LazyAnonymization,
    ) -> Proofread:
        """Proofread the current transcription, or return the stored answer.

        The teacher's transcription is what is sent: it is what the student wrote, as they read
        it.  A stored answer is served as it is, and the teacher's corrections of it are applied
        by the caller, which reads the current texts again.
        """
        if self._correction is None:
            return Proofread(review=None, warnings=(), ctx=ctx)
        if ctx.state.correction_done and ctx.state.correction_text:
            return self._stored_correction(file, texts, ctx)

        # Before the anonymization: it is a full spaCy pass that a stop should not wait for.
        if self._shared.stopping():
            raise self._stop(preview_img, texts)
        self._shared.reporter.status(report_text.RUNNING_CORRECTION)
        self._shared.notify(file, ProgressStage.CORRECTION)
        try:
            result = ProofreadText(
                self._correction,
                self._names.anonymizer,
                self._shared.language,
                reporter=self._shared.reporter,
                sampler=self._shared.sampler,
            )(
                texts.transcription,
                texts.transcription_format,
                name=file.name,
                stop_requested=self._shared.stopping,
                # The file's anonymized text is made once and shared with the anonymized text
                # file; the unit asks for it rather than hiding the same text again.
                hide=lambda _text, _format: anonymized(Stage.CORRECTION),
            )
        except RunStoppedError as exc:
            raise self._stop(preview_img, texts) from exc
        return self._keep_correction(file, texts, ctx, result)

    def _stored_correction(self, file: Path, texts: CurrentTexts, ctx: CacheContext) -> Proofread:
        """Return the answer of an earlier run, with the note that says where it comes from."""
        if ctx.proofreading_kept:
            self._shared.reporter.status(report_text.KEPT_PROOFREADING)
        else:
            self._shared.reporter.status(report_text.CACHED_CORRECTION)
        review, dropped = _served_review(ctx, texts, file)
        warnings = [ctx.state.correction_warning] if ctx.state.correction_warning else []
        return Proofread(review=review, warnings=tuple(warnings + dropped), ctx=ctx)

    def _stop(self, preview_img: str, texts: CurrentTexts) -> Stopped:
        """Return the stop that ends a file at the proofreading."""
        return stopped_result(
            Stage.CORRECTION,
            preview_img=preview_img,
            raw_text=texts.transcription,
            text_format=texts.transcription_format,
        )

    def _keep_correction(
        self, file: Path, texts: CurrentTexts, ctx: CacheContext, result: ProofreadOutcome
    ) -> Proofread:
        """Store the answer and say what is worth a warning."""
        # A failed outcome is the unchanged source; caching it would present that as a finished
        # correction and the next run would never try again.  (A corrections-only fallback
        # *is* stored: the model answered, and asking it for explanations again would repeat
        # the failed requests.)
        if result.text and not result.outcome.failed:
            ctx = self._store_correction(file, texts, ctx, result)
        return Proofread(review=result.review, warnings=tuple(result.warnings), ctx=ctx)

    def _store_correction(
        self, file: Path, texts: CurrentTexts, ctx: CacheContext, result: ProofreadOutcome
    ) -> CacheContext:
        """Store the answer with what was sent; a write that fails leaves it this run's only."""
        review = result.review.to_dict() if result.review is not None else None
        try:
            return self._shared.cache_service.record_correction(
                file,
                ctx,
                correction_text=result.text,
                correction_review=review,
                correction_warning=result.stored_warning,
                # The text the proofreading was made from, stored with its result so the window
                # can show what really went out, also when nothing was found to hide and the
                # transcription changes later.  With hiding off it is the extracted text itself,
                # so nothing is duplicated.
                sent_text=result.sent_text if self._names.enabled else "",
                sent_map=dict(result.sent_map) if result.sent_map else None,
                # The transcription the answer was made from, so a proofreading made from a
                # text the document no longer has is stale.
                source_text=texts.transcription,
            )
        except OSError as exc:
            _logger.warning("Could not save correction state for %s: %s", file.name, exc)
            # The answer is still this run's: the outputs and the review are made from it, even
            # though the next run will not find it in the cache.
            return replace(
                ctx,
                state=replace(
                    ctx.state,
                    correction_done=True,
                    correction_text=result.text,
                    correction_review=review,
                    correction_warning=result.stored_warning,
                    correction_source_hash=text_identity(texts.transcription),
                ),
            )
