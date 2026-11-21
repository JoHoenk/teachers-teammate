"""Everything the Check tab shows about one document, in one query.

The tab used to be fed from the queue row: eight fields pushed by hand after every edit, and a
panel that reloaded itself from them.  This module is the one place that reads a stored document
and says what the tab shows — the texts, the review aligned with them, the items the list numbers,
the pages of the scan, how the results were sent.  The window asks for it once per document and
once per stored edit; nothing else assembles that state.

Pure apart from the state it is built from: no Qt, no I/O of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from ..domain.data_location import SentStatus
from ..domain.formatted_text import TextFormat, normalize_text_format
from ..domain.proofreading.review import AlignedReview, CorrectionReview, align
from ..domain.teacher_work import WorkStatus, looked_at_progress
from .check_items import CheckItem, check_items, countable_keys
from .document_view import DocumentStateView
from .formatting import TextFormatter

_CACHE_SIZE = 64


@dataclass(frozen=True)
class PreprocessInfo:
    """How a document's pages were produced.

    ``original_img`` is the first page before preprocessing (``""`` if none was kept).
    ``steps`` lists the applied preprocessing steps: ``None`` = unknown (cache written before
    this was recorded), ``()`` = none applied.
    """

    original_img: str = ""
    steps: tuple[str, ...] | None = None


@dataclass(frozen=True)
class CheckDocument:
    """One document as the Check tab shows it."""

    source_id: str
    name: str
    raw_text: str
    """The transcription as stored, Markdown marks included."""
    corrected_text: str
    """The proofread text as stored; empty when the document has no proofreading."""
    machine_corrected_text: str
    """The machine's own proofread text; equals :attr:`corrected_text` when the teacher changed
    nothing.  It is what tells a change the model made without an explanation from one the
    teacher made."""
    text_format: TextFormat
    raw_plain: str
    """The plain text of :attr:`raw_text` — what the review's offsets are in."""
    corrected_plain: str
    review: CorrectionReview | None
    warning: str
    """Why the review is missing or incomplete; empty when there is nothing to say."""
    aligned: AlignedReview | None
    """The review matched to the current texts; ``None`` without a proofreading.

    Without a stored review the diff is still taken, against an empty review: the changes are
    then corrections without explanation, which is what the list shows for a proofreading whose
    explanations were lost.
    """
    items: tuple[CheckItem, ...]
    """What the list shows, in text order and numbered 1..n."""
    pages: tuple[Path, ...]
    """The prepared pages of the scan, in page order; empty when none was kept."""
    original_pages: tuple[Path | None, ...]
    """The same pages as they were scanned; ``None`` where the original was not kept."""
    preprocess: PreprocessInfo
    content_review: str
    """The stored content review, as Markdown; empty when there is none."""
    sent: Mapping[str, SentStatus]
    """How each stored result was sent, keyed by stage."""
    work: WorkStatus
    """What the teacher changed and decided about this document."""
    looked_at_progress: tuple[int, int]
    """``(looked at, countable)`` over the items the list counts, the minor group as one."""

    @property
    def has_proofreading(self) -> bool:
        """Whether the document has a proofread text to show."""
        return bool(self.corrected_text.strip())

    def realigned(self, raw_text: str, corrected_text: str) -> CheckDocument:
        """Return this document with new texts, aligned and itemised again.

        The path while the teacher types: the pages, the content review and how the results were
        sent stay as they are, and nothing is read from or written to the disk.
        """
        raw_plain = TextFormatter.plain_text(raw_text, self.text_format)
        corrected_plain = TextFormatter.plain_text(corrected_text, self.text_format)
        aligned = _aligned(
            raw_plain,
            corrected_plain,
            self.review,
            corrected_text,
            TextFormatter.plain_text(self.machine_corrected_text, self.text_format),
        )
        items = _items(aligned)
        return replace(
            self,
            raw_text=raw_text,
            corrected_text=corrected_text,
            raw_plain=raw_plain,
            corrected_plain=corrected_plain,
            aligned=aligned,
            items=items,
            looked_at_progress=looked_at_progress(countable_keys(items), self.work.looked_at),
        )


def check_document_of(view: DocumentStateView) -> CheckDocument:
    """Return the Check tab's data for a stored document."""
    text_format = normalize_text_format(view.raw_text_format)
    raw_plain = TextFormatter.plain_text(view.raw_text, text_format)
    corrected_plain = TextFormatter.plain_text(view.correction_text, text_format)
    machine_text = view.machine_correction_text or view.correction_text
    machine_plain = TextFormatter.plain_text(machine_text, text_format)
    aligned = _aligned(
        raw_plain, corrected_plain, view.correction_review, view.correction_text, machine_plain
    )
    pages = tuple(Path(page) for page in view.page_images)
    items = _items(aligned)
    return CheckDocument(
        source_id=view.source_id,
        name=view.name,
        raw_text=view.raw_text,
        corrected_text=view.correction_text,
        machine_corrected_text=machine_text,
        text_format=text_format,
        raw_plain=raw_plain,
        corrected_plain=corrected_plain,
        review=view.correction_review,
        warning=view.correction_warning,
        aligned=aligned,
        items=items,
        pages=pages,
        original_pages=_original_pages(view, len(pages)),
        preprocess=PreprocessInfo(view.original_img, view.preprocess_steps),
        content_review=view.evaluation_text,
        sent=view.sent,
        work=view.work,
        looked_at_progress=looked_at_progress(countable_keys(items), view.work.looked_at),
    )


def _original_pages(view: DocumentStateView, count: int) -> tuple[Path | None, ...]:
    """Return the page as it was scanned for each prepared page, ``None`` where none was kept."""
    if not count:
        return ()
    kept = [Path(page) for page in view.original_images]
    return (*kept[:count], *([None] * max(0, count - len(kept))))


def _items(aligned: AlignedReview | None) -> tuple[CheckItem, ...]:
    """Return the items of *aligned*, or none without a proofreading."""
    if aligned is None:
        return ()
    return check_items(aligned)


def _aligned(
    raw_plain: str,
    corrected_plain: str,
    review: CorrectionReview | None,
    corrected_text: str,
    machine_plain: str,
) -> AlignedReview | None:
    """Return *review* matched to the two plain texts, or ``None`` without a proofreading.

    A proofreading whose review was lost is aligned against an empty review: the diff is still
    the ground truth of what the text says, and every change is then a correction without
    explanation.  *machine_plain* is the machine's own text, so a change it made without an
    explanation is not mistaken for the teacher's.
    """
    if not corrected_text.strip():
        return None
    return _align_cached(raw_plain, corrected_plain, review or CorrectionReview(), machine_plain)


@lru_cache(maxsize=_CACHE_SIZE)
def _align_cached(
    raw_plain: str, corrected_plain: str, review: CorrectionReview, machine_plain: str
) -> AlignedReview:
    """Align one pair of texts against one review, once: typing re-aligns on every pause."""
    return align(raw_plain, corrected_plain, review, machine_plain=machine_plain)
