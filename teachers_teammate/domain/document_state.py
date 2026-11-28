"""The record of one document's results, and what a reset of a stage clears.

A stored result is described by the settings it was made under (a stage's config hash) and by
the texts it produced.  This module is that record as a value, plus the field table that says
which fields belong to which stage — the rule behind "a reset from the OCR stage onward clears
the reading, the proofreading and the review".

Pure: no I/O.  ``state_repository`` turns a record into JSON and back.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .formatted_text import normalize_text_format
from .page_layout import LAYOUT_VERSION, ScanBox, layout_from
from .proofreading.review import CorrectionReview
from .stages import Stage, normalize_stage
from .task_text import passage_parts
from .teacher_work import MachineTexts, TeacherWork, current_texts, text_identity


def machine_texts(state: DocumentState) -> MachineTexts:
    """Return what the cache holds for *state*, as the domain reads it.

    The machine's own texts, untouched by the teacher: the merge point
    (``domain.teacher_work.current_texts``) puts their work over them.
    """
    return MachineTexts(
        raw_text=state.raw_text,
        raw_text_format=normalize_text_format(state.raw_text_format),
        corrected_text=state.correction_text,
        review=state.correction_review,
        correction_source_hash=state.correction_source_hash,
    )


def stored_layout(state: DocumentState) -> tuple[ScanBox, ...]:
    """Return the places of *state*'s reading, or none when they are stale or of another version.

    A layout belongs to the reading it was made from: the text it was aligned with, and the
    version of the placing.  Places are made only while a page is read: a reading without them
    gets them when it is read again.
    """
    if state.layout_version != LAYOUT_VERSION or not state.raw_layout:
        return ()
    if state.layout_source != text_identity(state.raw_text):
        return ()
    return layout_from(state.raw_layout)


@dataclass(frozen=True)
class DocumentState:
    """Versioned processing state record for one source document."""

    schema_version: int
    source_path: str
    source_hash: str
    source_image: str = ""
    raw_text: str = ""
    correction_text: str = ""
    # Explanations and unclear expressions of the proofreading (``CorrectionReview.to_dict``);
    # empty/None when the file was corrected without a review (corrections-only mode).  The
    # offsets are in the plain text of ``raw_text``, crossed-out words included.
    correction_review: dict | None = None
    # Why ``correction_review`` is missing or incomplete (e.g. the fallback to corrections only,
    # or suggestions that could not be placed); replayed when the cached correction is reused.
    correction_warning: str = ""
    # The text the stored proofreading was made from, with its placeholder map, and how it was
    # sent (``SentRecord.to_dict()``).  Only when names were hidden: with hiding off the text
    # that was sent is ``raw_text`` itself, and an edit of ``raw_text`` clears the result, so
    # the two cannot drift apart.  Stored with the result and never overwritten while it
    # exists, so the window can show what really went out after the settings changed.
    correction_sent_text: str = ""
    correction_sent_map: dict[str, str] | None = None
    correction_sent: dict | None = None
    evaluation_text: str = ""
    # What the content review's run reported, replayed when the cached review is reused.
    evaluation_warning: str = ""
    # How the stored content review was sent (``SentRecord.to_dict()``).  Its text is not
    # stored: the content review is being redesigned, and what it sends is stored with that.
    evaluation_sent: dict | None = None
    # Notation of ``raw_text`` and ``correction_text`` (the corrector answers in the notation
    # it was given): "plain" (verbatim) or "markdown".  The default keeps state written
    # before this field existed rendering as plain text.
    raw_text_format: str = "plain"
    # The lines of the reading, each with its range in the plain text of ``raw_text``
    # (``ScanBox.to_dict``): what the Check tab marks on the page.  Made while the pages are read,
    # never afterwards.
    raw_layout: list[dict] = field(default_factory=list)
    # The version the places were made with, and the identity of the ``raw_text`` they were made
    # from.  A layout of another version, or of a text the document no longer has, is stale.
    layout_version: int = 0
    layout_source: str = ""
    # The passages of the document that are printed text, not the student's writing: what a
    # detection found, rebuilt by the next one.  The texts stay as they were read, so diffs,
    # metrics and exports do not change; the marks only decide what is not sent.  The teacher's
    # "not task" entries over them are their work (``TeacherWork.not_task``).
    task_passages: list[str] = field(default_factory=list)
    ocr_done: bool = False
    correction_done: bool = False
    evaluation_done: bool = False
    ocr_config_hash: str = ""
    correction_config_hash: str = ""
    # ``text_identity`` of the transcription the stored proofreading was made from.  A stored
    # proofreading is stale when that differs from the current transcription, whatever the
    # settings: it guards against a store emptied on one side only (the teacher's transcription
    # gone while the cache still holds a proofreading made from it).  Empty means "made from
    # ``raw_text``", which is what state written before this field existed holds.
    correction_source_hash: str = ""
    eval_config_hash: str = ""
    preview_config_hash: str = ""
    # Every prepared page of the document, in page order.  The first entry is the preview the
    # window shows; a document read before the app kept more than one page has the one page it
    # had (``load`` folds an old ``preview_img`` into this list).
    page_images: list[str] = field(default_factory=list)
    # The page as it was scanned, for every prepared page, in the same order.  Empty when
    # preparation changed nothing.
    original_images: list[str] = field(default_factory=list)
    # Preprocessing steps applied to produce ``preview_img``.  ``None`` means unknown
    # (state written before this field existed); ``[]`` means none were applied.
    preprocess_steps: list[str] | None = None

    @property
    def task_marks(self) -> tuple[str, ...]:
        """Return the passages the document leaves alone; a stored row of another shape is none."""
        return tuple(row for row in self.task_passages if isinstance(row, str))

    @property
    def review(self) -> CorrectionReview | None:
        """Return the stored proofreading review, or ``None`` when there is none to show."""
        return CorrectionReview.from_dict(self.correction_review)

    @property
    def preview_img(self) -> str:
        """Return the prepared page the window shows: the first of :attr:`page_images`."""
        return self.page_images[0] if self.page_images else ""

    @property
    def original_img(self) -> str:
        """Return the scanned page next to the preview: the first of :attr:`original_images`."""
        return self.original_images[0] if self.original_images else ""

    @property
    def pages(self) -> tuple[str, ...]:
        """Return the stored pages in page order."""
        return tuple(self.page_images)

    @property
    def originals(self) -> tuple[str, ...]:
        """Return the stored original pages in page order."""
        return tuple(self.original_images)


@dataclass(frozen=True)
class ResetPolicy:
    """Describes which cached outputs should be cleared for a reset request."""

    clear_ocr: bool
    clear_correction: bool
    clear_evaluation: bool
    clear_preview_artifacts: bool


def reset_policy_from_stage(stage: str) -> ResetPolicy:
    """Return reset policy for stage invalidation beginning at *stage*."""
    stage_name = normalize_stage(stage)
    if stage_name is Stage.OCR:
        return ResetPolicy(
            clear_ocr=True,
            clear_correction=True,
            clear_evaluation=True,
            clear_preview_artifacts=True,
        )
    if stage_name is Stage.CORRECTION:
        return ResetPolicy(
            clear_ocr=False,
            clear_correction=True,
            clear_evaluation=True,
            clear_preview_artifacts=False,
        )
    return ResetPolicy(
        clear_ocr=False,
        clear_correction=False,
        clear_evaluation=True,
        clear_preview_artifacts=False,
    )


#: The fields that describe the preview artifact.  They are cleared together: a page image
#: without its steps, or the other way round, says nothing true.
_PREVIEW_FIELDS: Mapping[str, object] = {
    "page_images": (),
    "original_images": (),
    "source_image": "",
    "preprocess_steps": None,
    "preview_config_hash": "",
}

#: What a reset of each stage clears.  One entry per stage, so a field added to
#: ``DocumentState`` for a stage belongs here — ``tests/unit/test_state_repository.py`` fails
#: on a stage field the table does not name.  ``()`` stands for an empty list;
#: :func:`fresh` makes it a new one.
_CLEARED: Mapping[Stage, Mapping[str, object]] = {
    Stage.OCR: {
        **_PREVIEW_FIELDS,
        "ocr_done": False,
        "ocr_config_hash": "",
        "raw_text": "",
        "raw_text_format": "plain",
        "raw_layout": (),
        "layout_version": 0,
        "layout_source": "",
    },
    Stage.CORRECTION: {
        "correction_done": False,
        "correction_config_hash": "",
        "correction_source_hash": "",
        "correction_text": "",
        "correction_review": None,
        "correction_warning": "",
        "correction_sent_text": "",
        "correction_sent_map": None,
        "correction_sent": None,
    },
    Stage.EVALUATION: {
        "evaluation_done": False,
        "eval_config_hash": "",
        "evaluation_text": "",
        "evaluation_warning": "",
        "evaluation_sent": None,
    },
}


def fresh(fields: Mapping[str, object]) -> dict[str, object]:
    """Return *fields* with a new list for every ``()`` placeholder."""
    return {
        name: list(value) if isinstance(value, tuple) else value for name, value in fields.items()
    }


def cleared_fields(stage: Stage) -> dict[str, object]:
    """Return the fields a reset of *stage* clears, with fresh values."""
    return fresh(_CLEARED[stage])


def cleared_preview_fields() -> dict[str, object]:
    """Return the fields that describe the preview artifact, cleared."""
    return fresh(_PREVIEW_FIELDS)


def reset_fields(stage: Stage) -> dict[str, object]:
    """Return the fields a reset from *stage* onward clears, with fresh values."""
    policy = reset_policy_from_stage(stage)
    cleared: dict[str, object] = {}
    if policy.clear_ocr:
        cleared.update(cleared_fields(Stage.OCR))
    if policy.clear_correction:
        cleared.update(cleared_fields(Stage.CORRECTION))
    if policy.clear_evaluation:
        cleared.update(cleared_fields(Stage.EVALUATION))
    return cleared


def task_text_of(
    state: DocumentState, work: TeacherWork | None, plain_of: Callable[[str, str], str]
) -> tuple[str, ...]:
    """Return what the marks of *state* really cover in the transcription the document has now.

    The document's part of its proofreading key: marking a passage, or taking it back,
    re-proofreads this document and no other.  The marks are looked for in the current
    transcription, the teacher's where they fixed it, because that is the text the proofreading
    cuts.  A mark the text no longer holds counts for nothing, and a "not task" entry of the
    teacher takes its line back out.  *plain_of* converts the text from its notation as the
    proofreading does, so both see the same lines.
    """
    marks = state.task_marks
    if not marks:
        return ()
    texts = current_texts(machine_texts(state), work)
    plain = plain_of(texts.transcription, texts.transcription_format)
    return passage_parts(plain, marks, work.not_task if work is not None else ())
