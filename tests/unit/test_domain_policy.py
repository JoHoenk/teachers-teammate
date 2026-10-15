"""Unit tests for domain stage and freshness policy helpers."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.freshness import (
    StageHashes,
    first_stale_stage_for_hashes,
    reset_policy_from_stage,
    should_invalidate_preview_artifact,
)
from teachers_teammate.domain.pdf_text_layer import (
    MAX_IMAGE_COVERAGE,
    MIN_FREEHAND_PATHS,
    PageTextStats,
    normalize_pdf_text_layer,
    parse_pdf_text_layer,
    page_has_usable_text,
    use_text_layer,
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


# ── PDF text layer policy ──────────────────────────────────────────────────

_CLEAN = PageTextStats(char_count=500, printable_ratio=1.0, image_coverage=0.0)


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        (_CLEAN, True),
        (PageTextStats(10, 1.0, 0.0), False),
        (PageTextStats(500, 0.5, 0.0), False),
        (PageTextStats(500, 1.0, 0.9), False),
        (PageTextStats(500, 1.0, MAX_IMAGE_COVERAGE), True),
        (PageTextStats(500, 1.0, MAX_IMAGE_COVERAGE + 0.01), False),
        (PageTextStats(500, 1.0, 0.0, has_content_annotations=True), False),
        (PageTextStats(500, 1.0, 0.0, freehand_path_count=MIN_FREEHAND_PATHS - 1), True),
        (PageTextStats(500, 1.0, 0.0, freehand_path_count=MIN_FREEHAND_PATHS), False),
    ],
    ids=[
        "clean",
        "too_short",
        "garbled",
        "scanned",
        "small_logo",
        "pasted_answer_image",
        "annotation",
        "no_paths",
        "any_freehand_path",
    ],
)
def test_page_has_usable_text(stats: PageTextStats, expected: bool) -> None:
    """
    Given  per-page text statistics
    When   page_has_usable_text() is called
    Then   only clean, text-only pages with enough characters qualify
    """
    assert page_has_usable_text(stats) is expected


def test_use_text_layer_modes() -> None:
    """
    Given  one clean page and one scanned page that still holds some text
    When   use_text_layer() is called in each mode
    Then   never always declines, auto needs every page clean, always needs any text everywhere
    """
    scanned = PageTextStats(char_count=20, printable_ratio=0.6, image_coverage=1.0)

    assert use_text_layer("never", [_CLEAN]) is False
    assert use_text_layer("auto", [_CLEAN, _CLEAN]) is True
    assert use_text_layer("auto", [_CLEAN, scanned]) is False
    assert use_text_layer("always", [_CLEAN, scanned]) is True
    assert use_text_layer("always", [_CLEAN, PageTextStats(0, 0.0, 1.0)]) is False
    assert use_text_layer("auto", []) is False


def test_use_text_layer_stops_measuring_after_first_unusable_page() -> None:
    """
    Given  a lazily measured document whose first page is scanned
    When   use_text_layer() is called in auto mode
    Then   it declines without asking for the remaining pages
    """
    measured: list[int] = []

    def pages():
        for number in range(3):
            measured.append(number)
            yield PageTextStats(500, 1.0, 1.0 if number == 0 else 0.0)

    assert use_text_layer("auto", pages()) is False
    assert measured == [0]


def test_normalize_pdf_text_layer_falls_back_to_auto() -> None:
    """
    Given  valid, differently-cased and invalid mode strings
    When   normalize_pdf_text_layer() is called
    Then   valid modes are normalized and anything else becomes "auto"
    """
    assert normalize_pdf_text_layer(" Never ") == "never"
    assert normalize_pdf_text_layer("always") == "always"
    assert normalize_pdf_text_layer("bogus") == "auto"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("never", "never"), ("  Always ", "always"), ("auto", "auto"), ("bogus", None), (False, None)],
)
def test_parse_pdf_text_layer(value: object, expected: str | None) -> None:
    """
    Given  a raw setting value of any type
    When   parse_pdf_text_layer() is called
    Then   a valid mode (case/whitespace folded) is returned, anything else gives None
    """
    assert parse_pdf_text_layer(value) == expected
