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
    """Return the earliest stale stage caused by runtime hash drift, if any."""
    if hashes.ocr_done and hashes.ocr_config_hash != hashes.expected_ocr_config_hash:
        return "ocr"
    if (
        hashes.correction_done
        and hashes.correction_config_hash != hashes.expected_correction_config_hash
    ):
        return "correction"
    if hashes.evaluation_done and hashes.eval_config_hash != hashes.expected_eval_config_hash:
        return "evaluation"
    return None


def should_invalidate_preview_artifact(
    *,
    current_preview_config_hash: str,
    expected_preview_config_hash: str,
    has_preview_artifact: bool,
) -> bool:
    """Return whether preview artifacts are stale under current runtime config."""
    return has_preview_artifact and current_preview_config_hash != expected_preview_config_hash
