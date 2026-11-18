"""The one place a stored document becomes the view a reader shows.

The cache holds the machine's texts; the teacher's work holds theirs.  Every reader — the queue,
the Check tab, Export, Copy, *What is sent* — shows the document as it is *now*, which is the
merge of the two.  That merge happens here and nowhere else: nothing outside the cache's own code
reads the texts of a ``DocumentState``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..domain.freshness import correction_kept_over_settings, first_stale_stage_for_hashes
from ..domain.teacher_work import TeacherWork, current_texts, work_status
from ..infrastructure.state_repository import (
    DocumentState,
    DocumentStateView,
    StateRepository,
    machine_texts,
)
from ..infrastructure.workflow.cache_service import ExpectedHashes, stage_hashes


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
        correction_text=texts.corrected,
        machine_correction_text=texts.machine_corrected,
        work=work_status(work, texts, kept_over_settings=kept_over_settings),
    )


def reconciled_view(
    store: StateRepository,
    file: Path,
    state: DocumentState,
    work: TeacherWork | None,
    expected: ExpectedHashes,
) -> DocumentStateView:
    """Return the view of *state* as the next run would find it, merged with *work*.

    A stage whose settings changed is shown as not done, unless the teacher's work keeps it
    (:func:`~teachers_teammate.infrastructure.workflow.cache_service.stage_hashes` decides, the
    same way for the queue, the run and the commands that edit), and a proofreading kept over
    other settings says so.
    """
    flags = stage_hashes(state, work, expected)
    stale_stage = first_stale_stage_for_hashes(flags)
    if stale_stage == "ocr":
        state = replace(state, ocr_done=False, correction_done=False, evaluation_done=False)
    elif stale_stage == "correction":
        state = replace(state, correction_done=False, evaluation_done=False)
    elif stale_stage == "evaluation":
        state = replace(state, evaluation_done=False)
    return merged_view(
        store.to_view(file, state),
        state,
        work,
        kept_over_settings=correction_kept_over_settings(flags),
    )
