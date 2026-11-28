"""The read-only view of a stored document, as the window and the CLI read it.

The store holds the machine's results; this is what a reader is given: the texts, the review,
the pages, and how each stored result was sent.  It lives in the application layer because
``work`` — what the teacher changed and decided — is filled in here, not by the store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..domain.data_location import SentRecord, SentStatus
from ..domain.document_state import DocumentState, stored_layout
from ..domain.page_layout import ScanBox
from ..domain.proofreading.carry import ReadingFix
from ..domain.proofreading.review import CorrectionReview
from ..domain.stages import Stage
from ..domain.teacher_work import WorkStatus


@dataclass(frozen=True)
class DocumentStateView:
    """Read-only UI view of a valid document state record."""

    source_id: str
    name: str
    preview_img: str
    raw_text: str
    correction_text: str
    evaluation_text: str
    ocr_done: bool
    correction_done: bool
    evaluation_done: bool
    original_img: str = ""
    preprocess_steps: tuple[str, ...] | None = None
    raw_text_format: str = "plain"
    """Notation of ``raw_text`` and ``correction_text`` (the corrector answers in the
    notation it was given)."""
    machine_correction_text: str = ""
    """The machine's own proofread text, before the teacher changed anything.

    Equals :attr:`correction_text` when the teacher changed nothing, and is empty for a view
    built by hand that does not say.  It is what tells a change the model made without an
    explanation from one the teacher made."""

    machine_raw_text: str = ""
    """The transcription as the recognition read it, before the teacher's fix.

    Equals :attr:`raw_text` when they changed nothing, and is empty for a view built by hand
    that does not say.  :attr:`raw_layout`'s places are offsets in this text."""

    machine_raw_text_format: str = ""
    """The format of :attr:`machine_raw_text`; empty for a view built by hand that does not say."""

    raw_layout: tuple[ScanBox, ...] = ()
    """Where the lines of the scan stand in the reading, with their boxes and confidence.

    Empty when the reader had no layout to give, its models were not there, or the places are
    stale — the scan then marks nothing."""

    correction_review: CorrectionReview | None = None
    """Explanations and unclear expressions; ``None`` when the file has no review."""
    correction_warning: str = ""
    """Why the file has no (or only a partial) review; empty when there is nothing to say."""
    task_marks: tuple[str, ...] = ()
    """The passages of the document that are printed text, not the student's writing.

    What a run leaves out of the proofreading; the window shows them in grey and the comparison
    proofreads the same stretches a run would."""
    not_task: tuple[str, ...] = ()
    """The lines the teacher proofreads after all, although a mark covers them: their work, so a
    view built straight from the cache has none."""
    sent: dict[str, SentStatus] = field(default_factory=dict)
    """How each stored result was sent, keyed by stage (``correction``, ``evaluation``).

    Empty for a record written before the app kept this, and for a file with no result."""

    sent_text: str = ""
    """The text the stored proofreading was sent as, in the notation of the document.

    Empty when names were not hidden (the transcription went as written) and for a result an
    older version stored; :attr:`sent` tells names hidden from names as written."""

    sent_map: dict[str, str] = field(default_factory=dict)
    """The placeholder map of :attr:`sent_text`: placeholder → the original the model found."""

    page_images: tuple[str, ...] = ()
    """Every prepared page in page order (the first is ``preview_img``); one page when the
    document was recognized before the app kept all of them."""

    original_images: tuple[str, ...] = ()
    """The page as it was scanned, in the same order (the first is ``original_img``); one page
    when the document was recognized before the app kept all of them, and empty when
    preparation changed nothing."""

    work: WorkStatus = field(default_factory=WorkStatus)
    """What the teacher changed and decided about this document.

    Filled by the application layer, which is the only place that reads the teacher's work; a
    view built straight from the cache says they did nothing."""

    correction_config_hash: str = ""
    """The proofreading key the stored answer was made with; empty when there is no answer.

    What the comparison reads: a model whose key is this one has already answered this document,
    so trying it costs no request."""

    reading_fix: ReadingFix = ReadingFix.NONE
    """What the fix of the reading that this view is the result of did to the proofreading;
    ``NONE`` for a view of anything else."""


def to_view(input_file: Path, state: DocumentState) -> DocumentStateView:
    """Map a validated state object to a read-only UI view."""
    return DocumentStateView(
        source_id=str(input_file.resolve()),
        name=input_file.name,
        preview_img=state.preview_img,
        raw_text=state.raw_text,
        machine_raw_text=state.raw_text,
        machine_raw_text_format=state.raw_text_format,
        raw_layout=stored_layout(state),
        correction_text=state.correction_text,
        evaluation_text=state.evaluation_text,
        ocr_done=state.ocr_done,
        correction_done=state.correction_done,
        evaluation_done=state.evaluation_done,
        original_img=state.original_img,
        preprocess_steps=(
            None if state.preprocess_steps is None else tuple(state.preprocess_steps)
        ),
        raw_text_format=state.raw_text_format,
        machine_correction_text=state.correction_text,
        correction_review=state.review,
        correction_warning=state.correction_warning,
        sent=sent_statuses(state),
        sent_text=state.correction_sent_text,
        sent_map=dict(state.correction_sent_map or {}),
        page_images=state.pages,
        original_images=state.originals,
        correction_config_hash=state.correction_config_hash,
        task_marks=state.task_marks,
    )


def sent_statuses(state: DocumentState) -> dict[str, SentStatus]:
    """Return how each stored result of *state* was sent, keyed by stage name.

    A stage that is not done has no stored result, so it has no line — also when the caller
    marked it stale on top of a loaded state (``reconciled_view``).

    The comparison with the current privacy settings is not made here: this module does not
    know the settings.  The service adds it.
    """
    statuses: dict[str, SentStatus] = {}
    for stage, done, raw in (
        (Stage.CORRECTION, state.correction_done, state.correction_sent),
        (Stage.EVALUATION, state.evaluation_done, state.evaluation_sent),
    ):
        if not done:
            continue
        record = SentRecord.from_dict(raw)
        if record is not None:
            statuses[stage] = SentStatus(record)
    return statuses
