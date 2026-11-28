"""The content-review stage: one file's corrected text reviewed, or the review a run stored."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from ...domain.performance_estimate import SpeedStep
from ...domain.stages import Stage
from ...domain.teacher_work import CurrentTexts
from ...exceptions import RunStoppedError
from ...interfaces import Evaluator
from ...texts import report as report_text
from .correction_stage import Proofread
from .hidden_names import NameHiding
from .processing_result import Stopped, stopped_result
from .stage_shared import StageShared

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reviewed:
    """What the review found: the reviewed text, and what is worth a warning about it."""

    text: str
    """The text the review wrote; ``""`` when it had nothing to say."""
    warnings: tuple[str, ...] = ()
    """What the review could not do, or what it left unsaid."""


class ReviewStage:
    """Reviews the content of one file, or serves the review an earlier run stored."""

    def __init__(
        self, shared: StageShared, *, evaluation: Evaluator | None, names: NameHiding
    ) -> None:
        self._shared = shared
        self._evaluation = evaluation
        self._names = names

    def run(
        self,
        file: Path,
        texts: CurrentTexts,
        preview_img: str,
        *,
        proofread: Proofread,
    ) -> Reviewed:
        """Run the content review, or return the cached one.

        The review is made from the current corrected text — the teacher's where they changed
        it: it judges the student's content, which their corrections hardly change.

        *proofread* is what the review is made from, and what a file stopped here hands back
        with the stored correction, so its row keeps the explanations of the proofreading.
        """
        ctx = proofread.ctx
        correction_text = texts.corrected
        if self._evaluation is None or not correction_text:
            return Reviewed(text="")

        cache_state = ctx.state
        if cache_state.evaluation_done and cache_state.evaluation_text:
            self._shared.reporter.status(report_text.CACHED_EVALUATION)
            warnings = [cache_state.evaluation_warning] if cache_state.evaluation_warning else []
            return Reviewed(text=cache_state.evaluation_text, warnings=tuple(warnings))

        stored = cache_state.correction_done
        review, review_warning = proofread.review, proofread.stored_warning

        def stopped() -> Stopped:
            return stopped_result(
                Stage.EVALUATION,
                preview_img=preview_img,
                raw_text=texts.transcription,
                correction_text=correction_text if stored else "",
                text_format=texts.transcription_format,
                correction_review=review if stored else None,
                correction_warning=review_warning if stored else "",
            )

        # Before the anonymization: it is a spaCy pass that a stop should not wait for.
        if self._shared.stopping():
            raise stopped()
        self._shared.reporter.status(report_text.RUNNING_EVALUATION)
        # The proofread text has its real names back, so hide them again: the review may
        # run on an online service just like proofreading.
        text_for_evaluation, anon_map = self._names.hide(
            correction_text, stage=Stage.EVALUATION, before_model=True
        )
        if self._shared.stopping():
            raise stopped()
        self._shared.sampler.begin_sample(SpeedStep.REVIEW)
        try:
            evaluation = self._evaluation.evaluate(
                text_for_evaluation,
                self._shared.language,
                text_format=texts.transcription_format,
            )
        except RunStoppedError as exc:
            raise stopped() from exc
        self._shared.sampler.finish_sample(
            words=len(text_for_evaluation.split()), usable=evaluation.warning is None
        )
        eval_text = self._names.restore(evaluation.text or "", anon_map, stage=Stage.EVALUATION)
        eval_warnings: list[str] = [evaluation.warning] if evaluation.warning else []
        if left_hidden := self._names.left_hidden(eval_text, anon_map):
            eval_warnings.append(left_hidden)
        if eval_text:
            try:
                self._shared.cache_service.record_evaluation(
                    file,
                    ctx,
                    evaluation_text=eval_text,
                    evaluation_warning="; ".join(eval_warnings),
                    names_hidden=len(anon_map),
                )
            except OSError as exc:
                _logger.warning("Could not save evaluation state for %s: %s", file.name, exc)
        return Reviewed(text=eval_text or "", warnings=tuple(eval_warnings))
