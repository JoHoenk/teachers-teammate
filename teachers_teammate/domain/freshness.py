"""When a stored result no longer holds: the stage hashes and what they say is stale.

A result is stored with the hash of the settings it was made under.  A run computes the hash its
settings ask for today and compares the two; a different hash means the result was made by other
settings, so the file is read (and sent) again.  Nothing here does I/O: the facts a hash needs
from the machine are passed in (see ``teachers_teammate.cache_keys``).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import TYPE_CHECKING

from .document_state import DocumentState, machine_texts
from .stages import Stage
from .teacher_work import current_texts, text_identity

if TYPE_CHECKING:
    from .teacher_work import TeacherWork


def compute_config_hash(*parts: str) -> str:
    """Return SHA-256 hash over ordered configuration parts."""
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()


@dataclass(frozen=True)
class StageHashes:
    """Hash snapshot used to detect stale pipeline stages."""

    ocr_done: bool
    ocr_config_hash: str
    expected_ocr_config_hash: str
    correction_done: bool
    correction_config_hash: str
    expected_correction_config_hash: str
    evaluation_done: bool
    eval_config_hash: str
    expected_eval_config_hash: str
    ocr_frozen: bool = False
    """The teacher fixed or checked the transcription, so the reading is not taken again.

    Re-reading would change nothing they see (their text wins) and would cascade into a new
    proofreading of the same text, which would break the binding of their corrections to the
    answer they were written on.  Only an explicit re-read reads the pages again.
    """
    correction_source: str = ""
    """Identity of the transcription the stored proofreading was made from."""
    expected_correction_source: str = ""
    """Identity of the transcription the document has now."""
    correction_kept: bool = False
    """The teacher worked on the stored proofreading, so a run keeps it by default."""
    correction_enabled: bool = True
    """The run proofreads: only then can its settings make a stored proofreading stale."""
    evaluation_enabled: bool = True
    """The run reviews the content: only then can its settings make a stored review stale."""


@dataclass(frozen=True)
class ExpectedHashes:
    """The stage hashes the current settings and the document's content ask for."""

    ocr_config_hash: str
    correction_config_hash: str
    eval_config_hash: str
    correction_enabled: bool = True
    """The run proofreads: only then can its settings make a stored proofreading stale."""
    evaluation_enabled: bool = True
    """The run reviews the content: only then can its settings make a stored review stale."""


def stage_hashes(
    state: DocumentState, work: TeacherWork | None, expected: ExpectedHashes
) -> StageHashes:
    """Return the freshness snapshot of *state* under *expected*, with the teacher's work.

    The one place that turns a work into the three flags freshness needs: a transcription part
    freezes the reading, and so does a corrections part the teacher worked on (reading the pages
    again could change the transcription the proofreading was made from, and the corrections
    would stop applying); the current transcription is what a proofreading must have been made
    from, and a corrections part the teacher worked on keeps its answer.  Both the run
    (``CacheReconciliationService.prepare``) and the queue (``reconciled_view``) ask
    here, so they agree on what is stale and what is kept.
    """
    texts = current_texts(machine_texts(state), work)
    corrections = texts.corrections_work
    return StageHashes(
        ocr_done=state.ocr_done,
        ocr_config_hash=state.ocr_config_hash,
        expected_ocr_config_hash=expected.ocr_config_hash,
        correction_done=state.correction_done,
        correction_config_hash=state.correction_config_hash,
        expected_correction_config_hash=expected.correction_config_hash,
        evaluation_done=state.evaluation_done,
        eval_config_hash=state.eval_config_hash,
        expected_eval_config_hash=expected.eval_config_hash,
        ocr_frozen=(work is not None and work.transcription is not None)
        or (corrections is not None and corrections.worked_on),
        correction_source=state.correction_source_hash,
        expected_correction_source=text_identity(texts.transcription),
        correction_kept=corrections is not None and corrections.worked_on,
        correction_enabled=expected.correction_enabled,
        evaluation_enabled=expected.evaluation_enabled,
    )


def first_stale_stage_for_hashes(hashes: StageHashes) -> Stage | None:
    """Return the earliest stale stage caused by runtime hash drift, if any.

    A frozen reading is not stale by its settings: the teacher's transcription is what the
    document says.  A proofreading is stale whenever it was made from another transcription
    than the current one — whatever the settings — and stale by its settings only when the
    teacher did not work on it.

    A stage the run does not do cannot make its stored result stale: switched off means "this
    run does not do it", not "throw it away".  A new transcription still invalidates the
    proofreading made from the old one, whatever the switch.
    """
    if (
        hashes.ocr_done
        and hashes.ocr_config_hash != hashes.expected_ocr_config_hash
        and not hashes.ocr_frozen
    ):
        return Stage.OCR
    if hashes.correction_done and (
        hashes.correction_source != hashes.expected_correction_source
        or (
            hashes.correction_enabled
            and hashes.correction_config_hash != hashes.expected_correction_config_hash
            and not hashes.correction_kept
        )
    ):
        return Stage.CORRECTION
    if (
        hashes.evaluation_enabled
        and hashes.evaluation_done
        and hashes.eval_config_hash != hashes.expected_eval_config_hash
    ):
        return Stage.EVALUATION
    return None


def correction_kept_over_settings(hashes: StageHashes) -> bool:
    """Whether a stored proofreading is stale only by its settings and was kept by the teacher.

    The queue says "kept" for it and the run-start question does not list it: the answer is
    still the one the teacher worked on, made with other settings.
    """
    return (
        hashes.correction_done
        and hashes.correction_kept
        and hashes.correction_source == hashes.expected_correction_source
        and hashes.correction_config_hash != hashes.expected_correction_config_hash
    )


def should_invalidate_preview_artifact(
    *,
    current_preview_config_hash: str,
    expected_preview_config_hash: str,
    has_preview_artifact: bool,
) -> bool:
    """Return whether preview artifacts are stale under current runtime config."""
    return has_preview_artifact and current_preview_config_hash != expected_preview_config_hash
