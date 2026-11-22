"""Tests for the Qt-free wording module behind "My computer" and model hints."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.model_catalog import (
    PROFILE_SETTINGS,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    Suitability,
    choose_profile,
)
from teachers_teammate.texts import models as wording

# ── detection status line ───────────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("apple_gb", "expected"),
    [
        (8.0, "Apple computer with 8 GB shared memory"),
        (16.0, "Apple computer with 16 GB shared memory"),
        (24.0, "Apple computer with 24 GB shared memory"),
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
                profile=HardwareProfile.CARD_8GB,
                reason=DetectionReason.GPU_DETECTED,
                gpu_name="RTX 4060",
                memory_gb=7.8,
            ),
            "Graphics card: RTX 4060 with 8 GB graphics memory",
        ),
        (
            HardwareDetection(profile=HardwareProfile.CPU, reason=DetectionReason.NO_GPU_FOUND),
            "No graphics card",
        ),
        (
            HardwareDetection(profile=HardwareProfile.CARD_16GB, reason=DetectionReason.MANUAL),
            "Chosen by you.",
        ),
        (
            HardwareDetection(
                profile=HardwareProfile.CARD_8GB,
                reason=DetectionReason.GPU_MEMORY_UNKNOWN,
                gpu_name="AMD GPU",
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


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("detection", "expected"),
    [
        (
            HardwareDetection(
                profile=HardwareProfile.CPU,
                reason=DetectionReason.GPU_DETECTED,
                gpu_name="RTX 3050",
                memory_gb=4.0,
            ),
            "Graphics card: RTX 3050 with 4 GB graphics memory\n"
            "Models are suggested for a basic computer.",
        ),
        (
            HardwareDetection(
                profile=HardwareProfile.CARD_8GB,
                reason=DetectionReason.GPU_DETECTED,
                gpu_name="RTX 3060",
                memory_gb=12.0,
            ),
            "Graphics card: RTX 3060 with 12 GB graphics memory\n"
            "Models are suggested for a graphics card with about 8 GB.",
        ),
    ],
)
def test_the_status_names_what_the_suggestions_follow(
    detection: HardwareDetection, expected: str
) -> None:
    """
    Given  a graphics card whose memory maps to a smaller profile (4 GB: a basic computer, 12 GB:
           an 8 GB card)
    When   the status is built
    Then   it has a line for the card and a line that says what the suggestions follow
    """
    assert wording.detection_status_text(detection) == expected


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_the_status_is_one_line_per_fact() -> None:
    """
    Given  a detection that knows the card, the computer's memory and the memory free right now
    When   the status is built
    Then   the card, the memory and what the suggestions follow are one line each; without the
           free figure the memory line is shorter, and without the memory it is gone
    """
    detection = HardwareDetection(
        profile=HardwareProfile.CARD_8GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="RTX 4060",
        memory_gb=8.0,
        system_memory_gb=16.0,
    )

    assert wording.detection_lines(detection, available_memory_gb=9.0) == [
        "Graphics card: RTX 4060 with 8 GB graphics memory",
        "Computer memory: 16 GB, 9 GB free right now",
        "Models are suggested for a graphics card with about 8 GB.",
    ]
    assert wording.detection_lines(detection)[1] == "Computer memory: 16 GB"
    without_memory = HardwareDetection(
        profile=HardwareProfile.CARD_8GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="RTX 4060",
        memory_gb=8.0,
    )
    assert len(wording.detection_lines(without_memory)) == 2


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_second_graphics_card_is_named_after_the_one_that_decides() -> None:
    """
    Given  a computer with two graphics cards
    When   the status is built
    Then   the card that decides the profile comes first and the other has its own line, so a
           teacher who knows their card sees that it was found
    """
    detection = HardwareDetection(
        profile=HardwareProfile.CARD_8GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="NVIDIA GeForce RTX 4060",
        memory_gb=8.0,
        other_gpu_names=("Intel UHD Graphics",),
    )

    lines = wording.detection_lines(detection)

    assert lines[0] == "Graphics card: NVIDIA GeForce RTX 4060 with 8 GB graphics memory"
    assert lines[1] == "Also found: Intel UHD Graphics"


# ── too-large wording: never presents the budget as the user's memory ───────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_text_for_graphics_card_tiers() -> None:
    """
    Given  a 14 GB model on an 8 GB computer type
    When   the explanation is rendered
    Then   it compares the model's need with what computers of this type have
    """
    text = wording.too_large_text(14.0, HardwareProfile.CARD_8GB)
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
    text = wording.too_large_text(14.0, HardwareProfile.CPU)
    assert text == "This model is too large to run quickly without a graphics card."


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_text_without_known_size() -> None:
    """
    Given  a model whose memory need is unknown
    When   the explanation is rendered
    Then   it still reads as a sentence (no "None GB")
    """
    assert "None" not in wording.too_large_text(None, HardwareProfile.CARD_16GB)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_confirmation_paragraph_names_the_model_and_the_consequence() -> None:
    """
    Given  a too-large model on a computer whose memory is known
    When   the confirmation paragraph is built
    Then   it names the model, what it needs and what the computer can offer, and says it would
           not run properly
    """
    text = wording.too_large_confirmation(
        "gpt-oss:20b", 14.0, HardwareProfile.CARD_8GB, system_memory_gb=8.0, card_gb=6.8
    )
    assert text.startswith("gpt-oss:20b: ")
    assert "about 14 GB" in text and "about 6.5 GB" in text
    assert "not run properly" in text


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_text_gives_the_numbers_when_the_computers_memory_is_known() -> None:
    """
    Given  a 14 GB model, an 8 GB card (6.8 GB usable) and 16 GB of main memory
    When   the explanation is rendered
    Then   it says what the computer can offer, how much main memory stays free for the system
           and that the model does not fit
    """
    text = wording.too_large_text(14.0, HardwareProfile.CARD_8GB, system_memory_gb=8.0, card_gb=6.8)

    assert text == (
        "This model needs about 14 GB of memory. This computer can offer about 6.5 GB "
        "(graphics memory and main memory); 8 GB of its 8 GB main memory stay free for the "
        "system. The model does not fit, so it would not run properly."
    )


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_text_without_a_card_talks_about_main_memory_only() -> None:
    """
    Given  a computer without a graphics card and 12 GB of main memory
    When   the explanation is rendered
    Then   it names main memory only, with 8 GB kept free for the system
    """
    text = wording.too_large_text(9.5, HardwareProfile.CPU, system_memory_gb=12.0)

    assert "(main memory)" in text
    assert "about 4 GB" in text and "8 GB of its 12 GB main memory" in text


# ── suitability labels & hint lines ─────────────────────────────────────────


@pytest.mark.use_case("Model_Category_Guidance")
@pytest.mark.parametrize(
    ("value", "task", "label"),
    [
        (Suitability.BEST, "ocr", "★ Best choice"),
        (Suitability.GOOD, "general", "Good choice"),
        (Suitability.WORKS, "ocr", "Works"),
        (Suitability.TOO_LARGE, "general", "Does not fit this computer"),
        (Suitability.TOO_LARGE, "ocr", "Does not fit this computer"),
        (Suitability.WRONG_KIND, "ocr", "Cannot read scans"),
        (Suitability.WRONG_KIND, "general", "Reading model only"),
        (Suitability.UNKNOWN, "ocr", "No suggestion available"),
    ],
)
def test_suitability_labels(value: Suitability, task: str, label: str) -> None:
    """
    Given  a suitability value and the task the model is chosen for
    When   the short label is requested
    Then   the plain-language label of the wording table is returned (a wrong kind of
           model is described differently for reading and for writing)
    """
    assert wording.suitability_text(value, task) == label  # ty: ignore[invalid-argument-type]  # literals


@pytest.mark.use_case("Model_Category_Guidance")
@pytest.mark.parametrize(
    ("value", "task", "label"),
    [
        (Suitability.BEST, "ocr", "★ Best"),
        (Suitability.GOOD, "general", "Good"),
        (Suitability.WORKS, "ocr", "Works"),
        (Suitability.TOO_LARGE, "general", "Won't fit"),
        (Suitability.TOO_LARGE, "ocr", "Won't fit"),
        (Suitability.WRONG_KIND, "ocr", "Can't read scans"),
        (Suitability.WRONG_KIND, "general", "Reading only"),
        (Suitability.UNKNOWN, "ocr", "No suggestion"),
    ],
)
def test_compact_suitability_labels(value: Suitability, task: str, label: str) -> None:
    """
    Given  a suitability value and the task the model is chosen for
    When   the compact label for the narrow downloads column is requested
    Then   a short label is returned (the full wording stays in the tooltip)
    """
    assert wording.compact_suitability_text(value, task) == label  # ty: ignore[invalid-argument-type]  # literals


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
        Suitability.WRONG_KIND,
        HardwareProfile.CARD_8GB,
        task="ocr",
        memory_gb=None,
        best_for_task="qwen3-vl:8b",
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
        Suitability.WRONG_KIND,
        HardwareProfile.CARD_8GB,
        task="general",
        memory_gb=None,
        best_for_task="qwen3.5:9b",
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
    texts = [wording.profile_text(setting) for setting in PROFILE_SETTINGS]
    assert all(texts)
    assert len(set(texts)) == len(PROFILE_SETTINGS) == 4


# ── memory of the computer and slow models ──────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_status_line_names_the_computers_memory_when_known() -> None:
    """
    Given  a detection with a 16 GB graphics card and 32 GB of computer memory
    When   the status line is built
    Then   it names the card and the computer's memory; without the memory figure the
           sentence is the same as before
    """
    detection = HardwareDetection(
        profile=HardwareProfile.CARD_16GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="NVIDIA GeForce RTX 3080 Laptop GPU",
        memory_gb=16.0,
        system_memory_gb=32.0,
    )
    plain = HardwareDetection(
        profile=HardwareProfile.CARD_16GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="NVIDIA GeForce RTX 3080 Laptop GPU",
        memory_gb=16.0,
    )

    assert wording.detection_lines(detection)[:2] == [
        "Graphics card: NVIDIA GeForce RTX 3080 Laptop GPU with 16 GB graphics memory",
        "Computer memory: 32 GB",
    ]
    assert len(wording.detection_lines(plain)) == 2
