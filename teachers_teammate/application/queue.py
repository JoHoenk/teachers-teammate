"""What the queue shows about one file, worked out from the store.

The queue is a view of the store, not a second copy of it: a row says what the next run would
find (a stage done, stale or kept) and what the document has (text, a page, a review).  The
texts themselves are read through the service when an action needs them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ..domain.data_location import SentStatus
from ..domain.formatted_text import TextFormat
from ..domain.proofreading.review import CorrectionReview
from .document_view import DocumentStateView


class StageState(StrEnum):
    """What the queue says about one stage of one file."""

    NONE = "none"
    """Nothing is stored for this stage."""
    DONE = "done"
    """A result the current settings ask for."""
    STALE = "stale"
    """A result the current settings no longer ask for."""
    KEPT = "kept"
    """A result made with other settings, kept because the teacher worked on it."""


@dataclass(frozen=True)
class QueueRow:
    """One file of the queue, as the store says it is now."""

    source_id: str
    name: str
    ocr: StageState = StageState.NONE
    correction: StageState = StageState.NONE
    evaluation: StageState = StageState.NONE
    has_text: bool = False
    """The document has a transcription, so Check and *What is sent* have something to show."""
    has_scan: bool = False
    """The document has a page image, so Check has something to show without text."""
    unclear_count: int = 0
    """How many passages the proofreading's review marked as unclear."""
    sent: Mapping[str, SentStatus] = field(default_factory=dict)
    """How each stored result was sent, keyed by stage."""
    checked: str = ""
    """What the teacher checked: ``"checked"``, ``"text"`` or ``""``."""
    text_format: TextFormat = "plain"
    """Notation of the stored texts."""
    review: CorrectionReview | None = None
    """Explanations and unclear expressions, ``None`` when the file has no review."""
    correction_warning: str = ""
    """Why the file has no (or only a partial) review; empty when there is nothing to say."""

    @classmethod
    def empty(cls, source: Path) -> QueueRow:
        """Return the row of a file the store knows nothing about."""
        return cls(source_id=str(source.resolve()), name=source.name)

    @property
    def openable(self) -> bool:
        """Whether Check has something to show for this file."""
        return self.has_text or self.has_scan


def _stage_state(done: bool, stored: bool, *, kept: bool = False) -> StageState:
    """Return the state of one stage: done, kept, stale or nothing."""
    if done:
        return StageState.KEPT if kept else StageState.DONE
    return StageState.STALE if stored else StageState.NONE


@dataclass(frozen=True)
class StoredStages:
    """Which stages the store has a result for, whatever the current settings ask for."""

    ocr: bool = False
    correction: bool = False
    evaluation: bool = False


def queue_row(view: DocumentStateView, stored: StoredStages) -> QueueRow:
    """Return the queue row of *view*: what the next run finds, and what is stored.

    *view* is the reconciled view (a stage whose settings changed is not done), *stored* says
    whether there was a result at all — that is what tells a stale result from one that was
    never made.
    """
    return QueueRow(
        source_id=view.source_id,
        name=view.name,
        ocr=_stage_state(view.ocr_done, stored.ocr),
        correction=_stage_state(
            view.correction_done, stored.correction, kept=view.work.kept_over_settings
        ),
        evaluation=_stage_state(view.evaluation_done, stored.evaluation),
        has_text=bool(view.raw_text.strip()),
        has_scan=bool(view.preview_img or view.original_img),
        unclear_count=view.correction_review.unclear_count if view.correction_review else 0,
        sent=view.sent,
        checked=view.work.checked_label,
        text_format="markdown" if view.raw_text_format == "markdown" else "plain",
        review=view.correction_review,
        correction_warning=view.correction_warning,
    )
