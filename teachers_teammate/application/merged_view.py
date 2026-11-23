"""The one place a stored document becomes the view a reader shows.

The cache holds the machine's texts; the teacher's work holds theirs.  Every reader — the queue,
the Check tab, Export, Copy, *What is sent* — shows the document as it is *now*, which is the
merge of the two.  That merge happens here and nowhere else: nothing outside the cache's own code
reads the texts of a ``DocumentState``.

A stored review that no longer quotes the words it points at in the transcription is dropped here,
with one logged warning, so the queue, the Check tab and Export all see the same document.
"""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import logging
from pathlib import Path

from ..domain.formatted_text import TextFormat
from ..domain.freshness import (
    correction_kept_over_settings,
    first_stale_stage_for_hashes,
)
from ..domain.proofreading.review import CorrectionReview, fits
from ..domain.stages import Stage
from ..domain.teacher_work import TeacherWork, current_texts, work_status
from ..infrastructure.state_repository import DocumentState, machine_texts, reset_fields
from ..infrastructure.workflow.cache_service import ExpectedHashes, stage_hashes
from .document_view import DocumentStateView, to_view
from .formatting import TextFormatter

_logger = logging.getLogger(__name__)


def merged_view(
    view: DocumentStateView,
    state: DocumentState,
    work: TeacherWork | None,
    *,
    kept_over_settings: bool = False,
) -> DocumentStateView:
    """Return *view* with the texts the document has now, and what the teacher did to it.

    *kept_over_settings* says the stored answer is stale only by its settings and was kept
    because the teacher worked on it; only a caller that knows the expected hashes can say so.
    """
    texts = current_texts(machine_texts(state), work)
    return replace(
        view,
        raw_text=texts.transcription,
        raw_text_format=texts.transcription_format,
        correction_review=_fitted(
            view.correction_review,
            texts.transcription,
            texts.transcription_format,
            source_id=view.source_id,
            name=view.name,
        ),
        correction_text=texts.corrected,
        machine_correction_text=texts.machine_corrected,
        work=work_status(work, texts, kept_over_settings=kept_over_settings),
    )


def _fitted(
    review: CorrectionReview | None,
    transcription: str,
    text_format: TextFormat,
    *,
    source_id: str,
    name: str,
) -> CorrectionReview | None:
    """Return *review*, or ``None`` when it no longer quotes the words it points at.

    A review that does not fit the transcription would put the model's explanations on other
    words than the ones it changed, and a half-right review is worse than none: the text still
    diffs against the corrected one, so the changes stay visible as changes without a reason.
    """
    if review is None:
        return None
    if fits(review, TextFormatter.plain_text(transcription, text_format)):
        return review
    _warn_dropped(source_id, name, review)
    return None


@lru_cache(maxsize=256)
def _warn_dropped(
    source_id: str,  # noqa: ARG001  # the cache key: one warning per document and review
    name: str,
    review: CorrectionReview,  # noqa: ARG001
) -> None:
    """Log the dropped review of one document once: every read of it comes through here."""
    _logger.warning("Dropped the review of %s: it does not match the text it was made for", name)


def reconciled_view(
    file: Path,
    state: DocumentState,
    work: TeacherWork | None,
    expected: ExpectedHashes,
) -> DocumentStateView:
    """Return the view of *state* as the next run would find it, merged with *work*.

    A stage whose settings changed is shown as not done, unless the teacher's work keeps it
    (:func:`~teachers_teammate.infrastructure.workflow.cache_service.stage_hashes` decides, the
    same way for the queue, the run and the commands that edit), and a proofreading kept over
    other settings says so.  The texts stay: the queue still shows them.
    """
    flags = stage_hashes(state, work, expected)
    stale_stage = first_stale_stage_for_hashes(flags)
    if stale_stage is not None:
        state = _without_done_flags(state, stale_stage)
    return merged_view(
        to_view(file, state),
        state,
        work,
        kept_over_settings=correction_kept_over_settings(flags),
    )


def _without_done_flags(state: DocumentState, stale_stage: Stage) -> DocumentState:
    """Return *state* with the ``*_done`` flags of *stale_stage* and later cleared."""
    return replace(
        state,
        **{
            name: value
            for name, value in reset_fields(stale_stage).items()
            if name.endswith("_done")
        },
    )
