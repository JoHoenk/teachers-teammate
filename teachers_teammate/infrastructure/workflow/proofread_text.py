"""Hide the names of one text, proofread it, put the names back.

A run does this for every document it proofreads, and the comparison window does it for every
writing model it tries, so the three steps live here, once:

1. **Hide** — the text goes to a model with personal data replaced, unless hiding is off.
2. **Proofread** — the corrector answers about what was sent, not about the student's text.
3. **Restore** — the answer and its review get the real names back.  A name is rarely as long as
   its placeholder, so every edit of the review after a name would point at the wrong words unless
   the review is remapped through the offsets of the restore; a review that then does not fit the
   text is dropped, which keeps the corrected text and costs the explanations.

What the run keeps stays with the run: the cache, the stop results, the storing, and the status
lines of a cache that answers instead.  What one text needs is here.

The anonymizer fails as :exc:`~teachers_teammate.exceptions.AnonymizerError` with its
:class:`~teachers_teammate.messages.Message`, so a file it cannot handle ends with a sentence a
teacher reads instead of ending the run.

Not pure: it talks to a model.  No Qt, no provider imports of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Protocol

from ...domain.anonymization import leftover_placeholders, restore_with_offsets
from ...domain.formatted_text import TextFormat
from ...domain.performance_estimate import SpeedStep
from ...domain.proofreading.result import CorrectionOutcome
from ...domain.proofreading.review import CorrectionReview, fits
from ...domain.stages import Stage
from ...exceptions import AnonymizerError, RunStoppedError
from ...interfaces import AnonymizationMap, Anonymizer, Corrector, Reporter
from ...messages import message_of, personal_info_removal_failed, personal_info_restore_failed
from ...texts import report as report_text
from ...texts.review import REVIEW_UNMATCHED, personal_info_still_hidden
from ..ocr_formats import plain_text_of

_logger = logging.getLogger(__name__)


def hide_personal_info(
    anonymizer: Anonymizer | None,
    text: str,
    *,
    stage: Stage | str = "",
    reporter: Reporter,
    before_model: bool = False,
) -> tuple[str, AnonymizationMap]:
    """Hide personal data in *text* before a model sees it (no-op without an anonymizer).

    *stage* names the stage a failure belongs to; *before_model* is a pass made for one request
    of its own (the review) rather than the one the proofreading shares.

    The one place that calls the anonymizer, so every call — the pass the proofreading uses, the
    one before the content review and the one before the anonymized text file — ends a failing
    file the same way instead of ending the run.

    Raises:
        AnonymizerError: the anonymizer could not do its work; the file ends with its sentence.
    """
    if anonymizer is None:
        return text, {}
    try:
        anonymized, anon_map = anonymizer.anonymize(text)
    except Exception as exc:  # spaCy and name-model errors vary; report them per file
        raise AnonymizerError(personal_info_removal_failed(message_of(exc)), stage=stage) from exc
    if before_model:
        reporter.status(report_text.anonymized_before(stage))
    else:
        reporter.status(report_text.personal_info_removed(len(anon_map)))
    return anonymized, anon_map


def restore_personal_info(
    anonymizer: Anonymizer | None,
    text: str,
    anon_map: AnonymizationMap,
    *,
    stage: Stage | str = "",
) -> str:
    """Put the real personal data back into *text*.

    Raises:
        AnonymizerError: the words do not line up with the placeholders; the file ends with its
            sentence.
    """
    if not (anon_map and text and anonymizer is not None):
        return text
    try:
        return anonymizer.restore(text, anon_map)
    except (ValueError, KeyError) as exc:
        raise AnonymizerError(personal_info_restore_failed(message_of(exc)), stage=stage) from exc


def names_left_hidden(text: str, anon_map: AnonymizationMap) -> str:
    """Return the warning about personal data still hidden in *text*, or ``""``."""
    left = leftover_placeholders(text, anon_map)
    return personal_info_still_hidden(left) if left else ""


def restore_review(
    anonymizer: Anonymizer | None,
    review: CorrectionReview | None,
    anon_map: AnonymizationMap,
    *,
    sent_text: str,
    raw_text: str,
    text_format: TextFormat,
    name: str = "",
) -> tuple[CorrectionReview | None, bool]:
    """Put the real personal data back into *review* and keep its offsets right.

    The corrector answers about the anonymized text, so the review's offsets are in the plain
    text of what was sent and its words quote placeholders.  The review is remapped through the
    offsets of the restore and then checked against the plain text of the raw text.

    Args:
        anonymizer: The anonymizer that made *anon_map*; ``None`` when hiding is off.
        review: The model's explanations, or ``None`` in corrections-only mode.
        anon_map: What was hidden in *sent_text*.
        sent_text: The text the model saw.
        raw_text: The text the answer belongs to, with its names.
        text_format: Notation of both texts.
        name: The document's name, for the log line only; empty in a comparison.

    Returns:
        The restored review (or ``None``) and whether it had to be dropped.
    """
    if review is None or not anon_map or anonymizer is None:
        return review, False
    try:
        sent_plain = plain_text_of(sent_text, text_format)
        restored_plain, offsets = restore_with_offsets(sent_plain, anon_map)
        restored = review.map_text(lambda value: anonymizer.restore(value, anon_map))
    except (ValueError, KeyError) as exc:
        _logger.warning("Could not restore PII in the review of %s: %s", name, exc)
        return None, True
    raw_plain = plain_text_of(raw_text, text_format)
    remapped = restored.remapped(offsets)
    if restored_plain != raw_plain or not fits(remapped, raw_plain):
        _logger.warning("Could not match the review of %s to the names in the text", name)
        return None, True
    return remapped, False


class SpeedSampler(Protocol):
    """What a proofreading reports its time to, as a run's statistics collector does."""

    def begin_sample(self, step: SpeedStep) -> None:
        """Mark *step* as running, so the requests it sends become one sample."""

    def finish_sample(self, *, words: int = 0, mode: str = "", usable: bool = True) -> None:
        """Turn the requests of *step* into one sample, or drop them when they are not usable."""


@dataclass(frozen=True)
class ProofreadOutcome:
    """One proofread text: what the answer was, and the names are back in it."""

    outcome: CorrectionOutcome
    """The corrector's answer, as it came back — its failure and fallback flags are read here."""

    text: str
    """The corrected text with the real names back; empty when the answer carried none."""

    review: CorrectionReview | None
    """The explanations with the real names back, or ``None`` when they were dropped."""

    warnings: tuple[str, ...]
    """What the teacher should know: the answer's own warning, a dropped review, names left."""

    stored_warning: str
    """The part of :attr:`warnings` that belongs to a stored result."""

    pieces: int | None
    """How many requests the proofreading takes, or ``None`` when the corrector does not split."""

    names_hidden: int
    """How many places were hidden for this answer; ``0`` when nothing was sent anonymously."""

    sent_text: str
    """The text the model saw — the extracted text itself when nothing was hidden."""

    sent_map: AnonymizationMap
    """What was hidden in :attr:`sent_text`; empty when nothing was."""


class ProofreadText:
    """Hide the names of one text, proofread it, and put the names back.

    One instance per proofreading, because the corrector, the anonymizer and the language belong
    to it; the report it writes to and the sample it records come with it too.
    """

    def __init__(
        self,
        corrector: Corrector,
        anonymizer: Anonymizer | None,
        language: str,
        *,
        reporter: Reporter,
        sampler: SpeedSampler | None = None,
    ) -> None:
        self._corrector = corrector
        self._anonymizer = anonymizer
        self._language = language
        self._reporter = reporter
        self._sampler = sampler

    def __call__(
        self,
        text: str,
        text_format: TextFormat = "plain",
        *,
        name: str = "",
        stop_requested: Callable[[], bool] | None = None,
        hide: Callable[[str, TextFormat], tuple[str, AnonymizationMap]] | None = None,
    ) -> ProofreadOutcome:
        """Proofread *text* and return its answer with the names restored.

        Args:
            text: The transcription to proofread.
            text_format: Notation of *text*.
            name: The document's name, for the log lines only; empty in a comparison.
            stop_requested: Asked before the request and after the hiding, so a run that the
                teacher stopped does not wait for a full pass over the names.
            hide: The hiding, when the caller already has the text with its names replaced — a
                run hides it once and uses it for the anonymized text file too.  ``None`` hides
                it here.

        Raises:
            RunStoppedError: *stop_requested* said so.
            AnonymizerError: hiding or restoring the names failed.
            StageError: a request never reached the model.
        """
        if stop_requested is not None and stop_requested():
            raise RunStoppedError()
        if hide is None:
            sent_text, sent_map = hide_personal_info(
                self._anonymizer, text, stage=Stage.CORRECTION, reporter=self._reporter
            )
        else:
            sent_text, sent_map = hide(text, text_format)
        # A text that goes out in several pieces cannot be changed across a piece boundary;
        # say so before the requests start, so the teacher knows what to expect.
        pieces = self._corrector.pieces(sent_text, self._language, text_format=text_format)
        if pieces is not None and pieces > 1:
            self._reporter.status(report_text.proofreading_in_parts(name, pieces))
        if stop_requested is not None and stop_requested():  # the hiding may have taken a while
            raise RunStoppedError()
        outcome = self._proofread(sent_text, text_format)
        return self._with_names(text, text_format, pieces, outcome, sent_text, sent_map, name)

    def _proofread(self, text: str, text_format: TextFormat) -> CorrectionOutcome:
        """Return the corrector's answer to *text*, with the sample of the run recorded."""
        if self._sampler is not None:
            self._sampler.begin_sample(SpeedStep.PROOFREADING)
        outcome = self._corrector.correct(text, self._language, text_format=text_format)
        if self._sampler is not None:
            # The mode the answer was really written in: a run that came back without a review
            # answered in text mode, and its answer length is the text-mode one.
            self._sampler.finish_sample(
                words=len(text.split()),
                mode="exchange" if outcome.review is not None else "text",
                usable=not (outcome.failed or outcome.fell_back),
            )
        return outcome

    def _with_names(
        self,
        raw_text: str,
        text_format: TextFormat,
        pieces: int | None,
        outcome: CorrectionOutcome,
        sent_text: str,
        sent_map: AnonymizationMap,
        name: str,
    ) -> ProofreadOutcome:
        """Return the answer with the real names back in its text, its review and its warning."""
        corrected_text = restore_personal_info(
            self._anonymizer, outcome.text or "", sent_map, stage=Stage.CORRECTION
        )
        review, unmatched = restore_review(
            self._anonymizer,
            outcome.review,
            sent_map,
            sent_text=sent_text,
            raw_text=raw_text,
            text_format=text_format,
            name=name,
        )
        warnings: list[str] = [outcome.warning] if outcome.warning else []
        if unmatched:
            warnings.append(REVIEW_UNMATCHED)
        left_hidden = names_left_hidden(corrected_text, sent_map)
        if left_hidden:
            warnings.append(left_hidden)
        return ProofreadOutcome(
            outcome=outcome,
            text=corrected_text,
            review=review,
            warnings=tuple(warnings),
            stored_warning="; ".join(w for w in (outcome.warning, left_hidden) if w),
            pieces=pieces,
            names_hidden=len(sent_map),
            sent_text=sent_text,
            sent_map=sent_map,
        )
