"""Unit tests for domain stage and freshness policy helpers."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.freshness import (
    StageHashes,
    first_stale_stage_for_hashes,
    reset_policy_from_stage,
    should_invalidate_preview_artifact,
)
from teachers_teammate.domain.stages import normalize_stage


def test_normalize_stage_normalizes_case_and_whitespace() -> None:
    """
    Given  a stage label with mixed case and surrounding whitespace
    When   normalize_stage() is called
    Then   the canonical lowercase stage name is returned
    """
    assert normalize_stage("  CoRRection ") == "correction"


def test_normalize_stage_rejects_unknown_stage() -> None:
    """
    Given  an unsupported stage label
    When   normalize_stage() is called
    Then   a ValueError is raised
    """
    with pytest.raises(ValueError, match="Invalid stage"):
        normalize_stage("postprocess")


def test_reset_policy_from_stage_ocr_clears_all_outputs() -> None:
    """
    Given  an invalidation request from the ocr stage
    When   reset_policy_from_stage() is called
    Then   OCR, correction, evaluation, and preview artifacts are cleared
    """
    policy = reset_policy_from_stage("ocr")
    assert policy.clear_ocr is True
    assert policy.clear_correction is True
    assert policy.clear_evaluation is True
    assert policy.clear_preview_artifacts is True


def test_first_stale_stage_for_hashes_returns_earliest_stale_stage() -> None:
    """
    Given  stale OCR and correction hashes for a document
    When   first_stale_stage_for_hashes() is called
    Then   OCR is returned because it is the earliest stale stage
    """
    stale_stage = first_stale_stage_for_hashes(
        StageHashes(
            ocr_done=True,
            ocr_config_hash="old-ocr",
            expected_ocr_config_hash="new-ocr",
            correction_done=True,
            correction_config_hash="old-correction",
            expected_correction_config_hash="new-correction",
            evaluation_done=True,
            eval_config_hash="old-eval",
            expected_eval_config_hash="new-eval",
        )
    )
    assert stale_stage == "ocr"


def test_should_invalidate_preview_artifact_requires_existing_artifact() -> None:
    """
    Given  a changed preview hash but no preview artifact on disk
    When   should_invalidate_preview_artifact() is called
    Then   preview invalidation is skipped
    """
    should_invalidate = should_invalidate_preview_artifact(
        current_preview_config_hash="old",
        expected_preview_config_hash="new",
        has_preview_artifact=False,
    )
    assert should_invalidate is False


def test_should_invalidate_preview_artifact_with_artifact_and_changed_hash() -> None:
    """
    Given  a preview artifact exists on disk and the preview config hash has changed
    When   should_invalidate_preview_artifact() is called
    Then   True is returned (artifact must be regenerated)
    """
    should_invalidate = should_invalidate_preview_artifact(
        current_preview_config_hash="old",
        expected_preview_config_hash="new",
        has_preview_artifact=True,
    )
    assert should_invalidate is True


def test_should_invalidate_preview_artifact_with_artifact_and_equal_hash() -> None:
    """
    Given  a preview artifact exists on disk and the preview config hash is unchanged
    When   should_invalidate_preview_artifact() is called
    Then   False is returned (artifact is still valid)
    """
    should_invalidate = should_invalidate_preview_artifact(
        current_preview_config_hash="same",
        expected_preview_config_hash="same",
        has_preview_artifact=True,
    )
    assert should_invalidate is False
