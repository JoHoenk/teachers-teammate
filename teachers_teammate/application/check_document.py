"""Everything the Check tab shows about one document, in one query.

This module is the one place that reads a stored document and says what the tab shows — the
texts, the review aligned with them, the items the list numbers, the pages of the scan, how the
results were sent, and what the box of *What was read* holds.  The window asks for it once per
document and once per stored edit; nothing else assembles that state.

Pure apart from the state it is built from: no Qt, no I/O of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from ..domain.data_location import SentStatus
from ..domain.formatted_text import Style, TextFormat, normalize_text_format
from ..domain.proofreading.review import AlignedReview, CorrectionReview, align
from ..domain.stages import Stage
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
    sent_text: str
    """The text the stored proofreading was sent as, in the notation of the document.

    Empty when names were hidden but nothing was found, and when they were not hidden at all;
    :attr:`sent` tells those two apart, and :func:`sent_view_of` reads it."""
    sent_map: Mapping[str, str]
    """The placeholder map of :attr:`sent_text`: placeholder → the original the model found."""
    work: WorkStatus
    """What the teacher changed and decided about this document."""
    looked_at_progress: tuple[int, int]
    """``(looked at, countable)`` over the items the list counts, the minor group as one."""

    @property
    def has_proofreading(self) -> bool:
        """Whether the document has a proofread text to show."""
        return bool(self.corrected_text.strip())


def check_document_of(view: DocumentStateView) -> CheckDocument:
    """Return the Check tab's data for a stored document."""
    text_format = normalize_text_format(view.raw_text_format)
    raw_plain = TextFormatter.plain_text(view.raw_text, text_format)
    corrected_plain = TextFormatter.plain_text(view.correction_text, text_format)
    machine_text = view.machine_correction_text or view.correction_text
    machine_plain = TextFormatter.plain_text(machine_text, text_format)
    review = view.correction_review
    aligned = _aligned(raw_plain, corrected_plain, review, view.correction_text, machine_plain)
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
        review=review,
        warning=view.correction_warning,
        aligned=aligned,
        items=items,
        pages=pages,
        original_pages=_original_pages(view, len(pages)),
        preprocess=PreprocessInfo(view.original_img, view.preprocess_steps),
        content_review=view.evaluation_text,
        sent=view.sent,
        sent_text=view.sent_text,
        sent_map=view.sent_map,
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


# ── What the box of *What was read* holds ─────────────────────────────────────


class SentKind(StrEnum):
    """Why the box of *What was read* holds the text it holds."""

    SENT = "sent"
    """The text of a stored proofreading went out like this."""
    WILL_BE_SENT = "will_be_sent"
    """Names are replaced here, and the proofreading has not run yet — this is what it gets."""
    WORKING = "working"
    """The names are being replaced; until they are, the box shows the text as read."""
    AS_WRITTEN = "as_written"
    """Names go out as written, so there is nothing to show but the transcription."""
    NAMES_SHOWN = "names_shown"
    """Names are hidden when sent, but the teacher reads them here: hiding is off."""
    UNAVAILABLE = "unavailable"
    """Names cannot be hidden at all: the Privacy add-on is missing."""


@dataclass(frozen=True)
class SentView:
    """What the box of *What was read* shows, and what its placeholders stand for."""

    text: str
    """The text to show, in the notation of the document."""
    placeholders: tuple[str, ...]
    """The placeholder tokens inside :attr:`text`, in map order."""
    names: Mapping[str, str]
    """Placeholder → the original text, for a tooltip and for reading a chip."""
    kind: SentKind
    struck: bool
    """Whether :attr:`text` holds words the student crossed out; they are not sent."""


def sent_view_of(
    doc: CheckDocument,
    *,
    available: bool,
    hiding: bool,
    preview: tuple[str, Mapping[str, str]] | None = None,
) -> SentView:
    """Return what the box of *What was read* shows for *doc*.

    The one rule behind the box, so a window cannot guess and a test needs no spaCy.  What a
    request is made from beats what the box could reconstruct: a proofreading that went out with
    names hidden is shown as it was stored, a name the model spelled differently included.

    *available* says names can be hidden at all and *hiding* whether the texts are shown with
    placeholders.  *preview* is the anonymizer's ``(text, map)`` of the current transcription —
    ``None`` until it arrives, and after a failure, when the names stand in what would go out.
    """
    if doc.has_proofreading:
        if not _sent_hidden(doc):
            return _shown(doc.raw_text, doc, SentKind.AS_WRITTEN)
        if hiding:
            return _shown(doc.sent_text or doc.raw_text, doc, SentKind.SENT, doc.sent_map)
        return _shown(doc.raw_text, doc, SentKind.NAMES_SHOWN)
    if not available:
        return _shown(doc.raw_text, doc, SentKind.UNAVAILABLE)
    if not hiding:
        return _shown(doc.raw_text, doc, SentKind.NAMES_SHOWN)
    if preview is None:
        return _shown(doc.raw_text, doc, SentKind.WORKING)
    text, names = preview
    return _shown(text, doc, SentKind.WILL_BE_SENT, names)


def _shown(
    text: str,
    doc: CheckDocument,
    kind: SentKind,
    names: Mapping[str, str] | None = None,
) -> SentView:
    """Return *text* as the box shows it, with the placeholders of *names* inside it."""
    found = {token: original for token, original in (names or {}).items() if token in text}
    return SentView(text, tuple(found), found, kind, _struck(text, doc.text_format))


def _sent_hidden(doc: CheckDocument) -> bool:
    """Whether the request behind the proofreading of *doc* carried placeholders.

    The stored record answers, and that answer does not change when the teacher changes the
    settings afterwards.  Without a record — an older store — nothing was found to hide, so the
    transcription went as written.
    """
    status = doc.sent.get(Stage.CORRECTION)
    return bool(status is not None and status.record.hiding_on)


def _struck(text: str, text_format: TextFormat) -> bool:
    """Whether *text* holds words the student crossed out, which never leave this computer."""
    if text_format != "markdown":
        return False
    for block in TextFormatter.document(text, text_format).blocks:
        cells = [span for row in block.rows for cell in row for span in cell]
        if any(Style.STRIKE in span.style for span in (*block.spans, *cells)):
            return True
    return False
