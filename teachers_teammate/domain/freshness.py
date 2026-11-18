"""Domain freshness and reset policy helpers for cached document state."""

from __future__ import annotations

from dataclasses import dataclass

from .stages import StageName, normalize_stage


@dataclass(frozen=True)
class ResetPolicy:
    """Describes which cached outputs should be cleared for a reset request."""

    clear_ocr: bool
    clear_correction: bool
    clear_evaluation: bool
    clear_preview_artifacts: bool


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


def reset_policy_from_stage(stage: str) -> ResetPolicy:
    """Return reset policy for stage invalidation beginning at *stage*."""
    stage_name = normalize_stage(stage)
    if stage_name == "ocr":
        return ResetPolicy(
            clear_ocr=True,
            clear_correction=True,
            clear_evaluation=True,
            clear_preview_artifacts=True,
        )
    if stage_name == "correction":
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


def first_stale_stage_for_hashes(hashes: StageHashes) -> StageName | None:
    """Return the earliest stale stage caused by runtime hash drift, if any.

    A frozen reading is not stale by its settings: the teacher's transcription is what the
    document says.  A proofreading is stale whenever it was made from another transcription
    than the current one — whatever the settings — and stale by its settings only when the
    teacher did not work on it.
    """
    if (
        hashes.ocr_done
        and hashes.ocr_config_hash != hashes.expected_ocr_config_hash
        and not hashes.ocr_frozen
    ):
        return "ocr"
    if hashes.correction_done and (
        hashes.correction_source != hashes.expected_correction_source
        or (
            hashes.correction_config_hash != hashes.expected_correction_config_hash
            and not hashes.correction_kept
        )
    ):
        return "correction"
    if hashes.evaluation_done and hashes.eval_config_hash != hashes.expected_eval_config_hash:
        return "evaluation"
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
