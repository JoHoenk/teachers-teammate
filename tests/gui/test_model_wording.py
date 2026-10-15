"""Tests for the Qt-free wording module behind "My computer" and model hints."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.model_catalog import (
    DetectionReason,
    HardwareDetection,
    Suitability,
    choose_profile,
)
from teachers_teammate.gui import _model_wording as wording

# ── detection status line ───────────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("apple_gb", "expected"),
    [
        (8.0, "Found: Apple computer with 8 GB shared memory."),
        (16.0, "Found: Apple computer with 16 GB shared memory."),
        (24.0, "Found: Apple computer with 24 GB shared memory."),
    ],
)
def test_apple_status_names_the_memory_the_computer_has(apple_gb: float, expected: str) -> None:
    """
    Given  Apple Silicon with a given amount of unified memory, detected through
           the real decision function (not a hand-built detection)
    When   the status line is rendered
    Then   it names the real memory, not the 0.66 share used for the thresholds
    """
    detection = choose_profile(gpus=[], apple_unified_memory_gb=apple_gb, ollama_is_remote=False)
    assert wording.detection_status_text(detection) == expected


@pytest.mark.use_case("Hardware_Profile_Override")
def test_remote_status_names_the_host() -> None:
    """
    Given  an Ollama URL pointing at another computer
    When   the status line is rendered
    Then   the host name appears and the user is told to pick the computer type
    """
    detection = choose_profile(
        gpus=[], apple_unified_memory_gb=None, ollama_is_remote=True, host="gpu-box.lan"
    )
    text = wording.detection_status_text(detection)
    assert "Ollama runs on another computer (gpu-box.lan)" in text
    assert "choose it in the list" in text


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("detection", "fragment"),
    [
        (
            HardwareDetection(
                profile="8gb",
                reason=DetectionReason.GPU_DETECTED,
                gpu_name="RTX 4060",
                memory_gb=7.8,
            ),
            "Found: RTX 4060 with 8 GB graphics memory.",
        ),
        (HardwareDetection(profile="cpu", reason=DetectionReason.NO_GPU_FOUND), "No graphics card"),
        (HardwareDetection(profile="16gb", reason=DetectionReason.MANUAL), "Chosen by you."),
        (
            HardwareDetection(
                profile="8gb", reason=DetectionReason.GPU_MEMORY_UNKNOWN, gpu_name="AMD GPU"
            ),
            "assume about 8 GB",
        ),
    ],
)
def test_status_text_per_reason(detection: HardwareDetection, fragment: str) -> None:
    """
    Given  a detection with a given reason
    When   the status line is rendered
    Then   the plain-language sentence for that situation is produced
    """
    assert fragment in wording.detection_status_text(detection)


# ── too-large wording: never presents the budget as the user's memory ───────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_text_for_graphics_card_tiers() -> None:
    """
    Given  a 14 GB model on an 8 GB computer type
    When   the explanation is rendered
    Then   it compares the model's need with what computers of this type have
    """
    text = wording.too_large_text(14.0, "8gb")
    assert text == (
        "This model needs about 14 GB of graphics memory; computers of this type have about 8 GB."
    )
    assert "7.5" not in text  # the internal budget never reaches the user


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_text_for_basic_computers() -> None:
    """
    Given  a large model on a basic computer
    When   the explanation is rendered
    Then   it talks about running without a graphics card, with no GB comparison
    """
    text = wording.too_large_text(14.0, "cpu")
    assert text == "This model is too large to run quickly without a graphics card."


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_text_without_known_size() -> None:
    """
    Given  a model whose memory need is unknown
    When   the explanation is rendered
    Then   it still reads as a sentence (no "None GB")
    """
    assert "None" not in wording.too_large_text(None, "16gb")


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_confirmation_paragraph_names_the_model_and_the_consequence() -> None:
    """
    Given  a too-large model
    When   the confirmation paragraph is built
    Then   it names the model and says it will still work but be slow
    """
    text = wording.too_large_confirmation("gpt-oss:20b", 14.0, "8gb")
    assert text.startswith("gpt-oss:20b: ")
    assert "still work" in text
    assert "very slow" in text


# ── suitability labels & hint lines ─────────────────────────────────────────


@pytest.mark.use_case("Model_Category_Guidance")
@pytest.mark.parametrize(
    ("value", "profile", "task", "label"),
    [
        (Suitability.BEST, "8gb", "ocr", "★ Best choice"),
        (Suitability.GOOD, "8gb", "general", "Good choice"),
        (Suitability.WORKS, "8gb", "ocr", "Works"),
        (Suitability.TOO_LARGE, "8gb", "general", "Too large for this computer"),
        (Suitability.TOO_LARGE, "cpu", "ocr", "Too slow for this computer"),
        (Suitability.WRONG_KIND, "16gb", "ocr", "Cannot read scans"),
        (Suitability.WRONG_KIND, "16gb", "general", "Reading model only"),
        (Suitability.UNKNOWN, "16gb", "ocr", "No suggestion available"),
    ],
)
def test_suitability_labels(value: Suitability, profile: str, task: str, label: str) -> None:
    """
    Given  a suitability value, a profile and the task the model is chosen for
    When   the short label is requested
    Then   the plain-language label of the wording table is returned (a wrong kind of
           model is described differently for reading and for writing)
    """
    assert wording.suitability_text(value, profile, task) == label  # ty: ignore[invalid-argument-type]  # literals


@pytest.mark.use_case("Model_Category_Guidance")
@pytest.mark.parametrize(
    ("value", "profile", "task", "label"),
    [
        (Suitability.BEST, "8gb", "ocr", "★ Best"),
        (Suitability.GOOD, "8gb", "general", "Good"),
        (Suitability.WORKS, "8gb", "ocr", "Works"),
        (Suitability.TOO_LARGE, "8gb", "general", "Too large"),
        (Suitability.TOO_LARGE, "cpu", "ocr", "Too slow"),
        (Suitability.WRONG_KIND, "16gb", "ocr", "Can't read scans"),
        (Suitability.WRONG_KIND, "16gb", "general", "Reading only"),
        (Suitability.UNKNOWN, "16gb", "ocr", "No suggestion"),
    ],
)
def test_compact_suitability_labels(
    value: Suitability, profile: str, task: str, label: str
) -> None:
    """
    Given  a suitability value, a profile and the task the model is chosen for
    When   the compact label for the narrow downloads column is requested
    Then   a short label is returned (the full wording stays in the tooltip)
    """
    assert wording.compact_suitability_text(value, profile, task) == label  # ty: ignore[invalid-argument-type]  # literals


@pytest.mark.use_case("Model_Category_Guidance")
def test_abilities_text_describes_models_outside_the_catalog() -> None:
    """
    Given  the uses of a model that is not in the catalog
    When   the abilities text is requested
    Then   it says whether the model reads scans and writes, only writes, or is unknown
    """
    assert wording.abilities_text(frozenset({"ocr", "general"})) == "Reads & writes"
    assert wording.abilities_text(frozenset({"general"})) == "Writes only"
    assert wording.abilities_text(None) == "No suggestion"


@pytest.mark.use_case("Model_Category_Guidance")
def test_wrong_kind_hint_suggests_a_reading_model() -> None:
    """
    Given  a model that cannot read scans
    When   the hint line is built
    Then   it is a warning that names a reading model as an example
    """
    hint = wording.model_hint(
        Suitability.WRONG_KIND, "8gb", task="ocr", memory_gb=None, best_for_task="qwen3-vl:8b"
    )
    assert hint.startswith("⚠")
    assert "cannot read scans" in hint
    assert "qwen3-vl:8b" in hint
    assert wording.is_warning(Suitability.WRONG_KIND)
    assert not wording.is_warning(Suitability.BEST)


@pytest.mark.use_case("Model_Category_Guidance")
def test_reading_model_for_writing_hint_suggests_a_writing_model() -> None:
    """
    Given  a reading model chosen for proofreading or review
    When   the hint line is built for the writing task
    Then   it is a warning that the model is not meant for it and names a writing model
    """
    hint = wording.model_hint(
        Suitability.WRONG_KIND, "8gb", task="general", memory_gb=None, best_for_task="qwen3.5:9b"
    )
    assert hint.startswith("⚠")
    assert "reading model" in hint
    assert "qwen3.5:9b" in hint
    assert "cannot read scans" not in hint


@pytest.mark.use_case("Model_Category_Guidance")
def test_profile_texts_cover_every_setting() -> None:
    """
    Given  the four profile settings
    When   their UI texts are looked up
    Then   each has its own, non-empty text
    """
    for setting in ("auto", "cpu", "8gb", "16gb"):
        assert wording.profile_text(setting)
    assert len({wording.profile_text(s) for s in ("auto", "cpu", "8gb", "16gb")}) == 4
