"""Unit tests for teachers_teammate.domain.model_catalog."""

from __future__ import annotations

from typing import cast

import pytest

from teachers_teammate._model_defaults import _DEFAULT_MODELS
from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    DEFAULT_WRITING_MODEL,
    HARDWARE_PROFILES,
    MEMORY_BUDGET_GB,
    OLLAMA_CATALOG,
    PROFILE_SETTINGS,
    RECOMMENDED,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    ModelUses,
    Suitability,
    catalog_uses,
    choose_profile,
    find_spec,
    models_for,
    normalize_tag,
    suitability,
    uses_from_capabilities,
)
from teachers_teammate.infrastructure.providers.ollama import PROVIDER_INFO


# ── catalog consistency ──────────────────────────────────────────────────────


def test_catalog_tags_are_unique_and_normalised() -> None:
    """
    Given  the Ollama model catalog
    When   tags are inspected
    Then   every tag is unique and already normalised (name:tag)
    """
    tags = [spec.tag for spec in OLLAMA_CATALOG]
    assert len(tags) == len(set(tags))
    for tag in tags:
        assert tag == normalize_tag(tag)


def test_catalog_memory_and_download_sizes_are_plausible() -> None:
    """
    Given  the Ollama model catalog
    When   memory_gb and download_gb are inspected
    Then   memory_gb >= download_gb > 0 for every entry
    """
    for spec in OLLAMA_CATALOG:
        assert spec.download_gb > 0
        assert spec.memory_gb >= spec.download_gb


@pytest.mark.use_case("Model_Category_Guidance")
def test_recommended_lists_reference_existing_catalog_entries() -> None:
    """
    Given  the RECOMMENDED mapping
    When   every (category, profile) list is inspected
    Then   each list is non-empty and every tag exists in the catalog for that category
    """
    for category, per_profile in RECOMMENDED.items():
        for profile in HARDWARE_PROFILES:
            tags = per_profile[profile]
            assert tags, f"RECOMMENDED[{category}][{profile}] must not be empty"
            for tag in tags:
                spec = find_spec(tag)
                assert spec is not None, f"{tag} is not in the catalog"
                assert category in catalog_uses(spec)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_every_best_model_fits_its_profile_budget() -> None:
    """
    Given  the RECOMMENDED mapping
    When   the BEST model of each (category, profile) is inspected
    Then   its memory_gb stays within the profile budget
    """
    for _category, per_profile in RECOMMENDED.items():
        for profile in HARDWARE_PROFILES:
            best_tag = per_profile[profile][0]
            spec = find_spec(best_tag)
            assert spec is not None
            assert spec.memory_gb <= MEMORY_BUDGET_GB[profile]


# ── normalize_tag ────────────────────────────────────────────────────────────


def test_normalize_tag_appends_latest_when_missing() -> None:
    """
    Given  a bare model name without a tag suffix
    When   normalize_tag is called
    Then   ":latest" is appended
    """
    assert normalize_tag("deepseek-ocr") == "deepseek-ocr:latest"


def test_normalize_tag_leaves_tagged_names_unchanged() -> None:
    """
    Given  model tags that already carry a ":tag" suffix
    When   normalize_tag is called
    Then   the tags are returned unchanged (apart from whitespace)
    """
    assert normalize_tag("qwen3-vl:8b") == "qwen3-vl:8b"
    assert normalize_tag(" glm-ocr:q8_0 ") == "glm-ocr:q8_0"


# ── suitability ──────────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("tag", "profile", "expected"),
    [
        # BEST: first entry of the RECOMMENDED list
        (DEFAULT_OCR_MODEL, "8gb", Suitability.BEST),
        (DEFAULT_OCR_MODEL, "16gb", Suitability.BEST),
        ("glm-ocr:q8_0", "cpu", Suitability.BEST),
        ("qwen3.5:9b", "8gb", Suitability.BEST),
        ("gpt-oss:20b", "16gb", Suitability.BEST),
        # GOOD: further entries of the RECOMMENDED list
        ("qwen3-vl:8b", "8gb", Suitability.GOOD),
        ("deepseek-ocr:latest", "8gb", Suitability.GOOD),
        ("mistral:latest", "8gb", Suitability.GOOD),
        # WORKS: catalogued and within the budget but not recommended
        ("qwen2.5vl:7b", "8gb", Suitability.WORKS),
        ("mistral:latest", "16gb", Suitability.WORKS),
        ("gemma4:latest", "8gb", Suitability.WORKS),
        # TOO_LARGE: memory need exceeds the profile budget
        ("qwen3-vl:8b", "cpu", Suitability.TOO_LARGE),
        ("gpt-oss:20b", "8gb", Suitability.TOO_LARGE),
        ("mistral-nemo:12b", "8gb", Suitability.TOO_LARGE),
    ],
)
def test_suitability_examples_per_profile(tag: str, profile: str, expected: Suitability) -> None:
    """
    Given  a catalogued model and a hardware profile
    When   suitability is computed for the model's own category
    Then   the rating matches the recommendation table (BEST/GOOD/WORKS/TOO_LARGE)
    """
    category = find_spec(tag).category
    assert suitability(tag, category, cast(HardwareProfile, profile)) is expected


@pytest.mark.use_case("Model_Category_Guidance")
def test_suitability_general_model_for_ocr_task_is_wrong_kind() -> None:
    """
    Given  a writing model asked for as a reading model
    When   suitability is computed for the "ocr" category
    Then   the result is WRONG_KIND
    """
    assert suitability("mistral:latest", "ocr", "8gb") is Suitability.WRONG_KIND
    assert suitability("gemma4:latest", "ocr", "8gb") is Suitability.WRONG_KIND


@pytest.mark.use_case("Model_Category_Guidance")
def test_qwen35_is_a_good_reading_alternative_and_still_the_best_writing_model() -> None:
    """
    Given  qwen3.5:9b, a writing model that can also read scanned pages
    When   it is rated for reading and for writing on the 8gb profile
    Then   it is GOOD for reading and BEST for writing
    """
    spec = find_spec("qwen3.5:9b")
    assert spec is not None
    assert catalog_uses(spec) == frozenset({"ocr", "general"})
    assert suitability("qwen3.5:9b", "ocr", "8gb") is Suitability.GOOD
    assert suitability("qwen3.5:9b", "general", "8gb") is Suitability.BEST


@pytest.mark.use_case("Model_Category_Guidance")
@pytest.mark.parametrize("profile", ["cpu", "8gb", "16gb"])
@pytest.mark.parametrize("tag", [spec.tag for spec in OLLAMA_CATALOG if spec.category == "ocr"])
def test_suitability_catalog_reading_model_for_general_task_is_wrong_kind(
    tag: str, profile: str
) -> None:
    """
    Given  any catalogued reading model (dedicated OCR models do not follow a proofreading prompt)
    When   suitability is computed for the "general" (writing) task
    Then   the result is WRONG_KIND on every profile
    """
    assert suitability(tag, "general", cast(HardwareProfile, profile)) is Suitability.WRONG_KIND


@pytest.mark.use_case("Model_Category_Guidance")
def test_suitability_unknown_tag_without_detected_uses_is_unknown() -> None:
    """
    Given  a model that is neither catalogued nor classified
    When   suitability is computed
    Then   the result is UNKNOWN
    """
    assert suitability("llama3.2:latest", "ocr", "8gb") is Suitability.UNKNOWN


@pytest.mark.use_case("Model_Category_Guidance")
def test_suitability_unknown_tag_with_detected_uses_is_rated() -> None:
    """
    Given  an uncatalogued model whose server-reported uses are known
    When   suitability is computed
    Then   a model that can read images works for BOTH tasks, a text-only model only for
           writing (reading is WRONG_KIND), and neither is ever TOO_LARGE (size unknown)
    """
    both: ModelUses = frozenset({"ocr", "general"})
    writing_only: ModelUses = frozenset({"general"})
    assert suitability("gemma3:4b", "ocr", "cpu", detected_uses=both) is Suitability.WORKS
    assert suitability("gemma3:4b", "general", "cpu", detected_uses=both) is Suitability.WORKS
    assert suitability("llama3.2:latest", "general", "8gb", detected_uses=writing_only) is (
        Suitability.WORKS
    )
    assert suitability("llama3.2:latest", "ocr", "8gb", detected_uses=writing_only) is (
        Suitability.WRONG_KIND
    )


@pytest.mark.use_case("Model_Category_Guidance")
def test_uses_from_capabilities_vision_means_reading_and_writing() -> None:
    """
    Given  capability sets as reported by Ollama
    When   uses_from_capabilities is called
    Then   vision maps to both tasks, anything else to writing only
    """
    assert uses_from_capabilities({"completion", "vision"}) == frozenset({"ocr", "general"})
    assert uses_from_capabilities({"completion", "tools"}) == frozenset({"general"})
    assert uses_from_capabilities(set()) == frozenset({"general"})


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_models_for_sorts_by_suitability_then_name() -> None:
    """
    Given  the reading-model catalog
    When   models_for is called for the 8gb profile
    Then   rows are sorted BEST → GOOD → WORKS → TOO_LARGE, then by name
    """
    rows = models_for("ocr", "8gb")
    tags = [spec.tag for spec, _value in rows]
    values = [value for _spec, value in rows]
    assert tags[0] == DEFAULT_OCR_MODEL
    assert values[0] is Suitability.BEST
    # the GOOD rows are sorted by name afterwards
    assert tags[1:5] == ["deepseek-ocr:latest", "glm-ocr:q8_0", "qwen3-vl:8b", "qwen3.5:9b"]
    assert values[1:5] == [Suitability.GOOD] * 4
    assert tags[-1] == "qwen2.5vl:7b"
    assert values[-1] is Suitability.WORKS


# ── choose_profile ───────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("gpus", "apple", "remote", "expected_profile", "expected_reason"),
    [
        ([], None, False, "cpu", DetectionReason.NO_GPU_FOUND),
        ([("GT 1030", 6.0)], None, False, "cpu", DetectionReason.GPU_DETECTED),
        ([("RTX 4060", 7.6)], None, False, "8gb", DetectionReason.GPU_DETECTED),
        ([("RX 7600", 12.0)], None, False, "8gb", DetectionReason.GPU_DETECTED),
        ([("RTX 4090", 15.9)], None, False, "16gb", DetectionReason.GPU_DETECTED),
        ([("RTX 4090", 24.0)], None, False, "16gb", DetectionReason.GPU_DETECTED),
        # The largest single GPU wins.
        ([("GT 1030", 4.0), ("RTX 4090", 16.0)], None, False, "16gb", DetectionReason.GPU_DETECTED),
        # GPU found but memory unreadable.
        ([("Mystery GPU", None)], None, False, "8gb", DetectionReason.GPU_MEMORY_UNKNOWN),
        # Apple unified memory thresholds (0.66 x RAM).
        ([], 8.0, False, "cpu", DetectionReason.APPLE_UNIFIED_MEMORY),
        ([], 16.0, False, "8gb", DetectionReason.APPLE_UNIFIED_MEMORY),
        ([], 24.0, False, "16gb", DetectionReason.APPLE_UNIFIED_MEMORY),
        # A remote Ollama server ignores the local GPU entirely.
        ([("RTX 4090", 24.0)], None, True, "8gb", DetectionReason.REMOTE_SERVER),
    ],
)
def test_choose_profile_thresholds(
    gpus: list,
    apple: float | None,
    remote: bool,
    expected_profile: str,
    expected_reason: DetectionReason,
) -> None:
    """
    Given  raw GPU / Apple / remote-URL detection results
    When   choose_profile is called
    Then   the profile and detection reason follow the threshold table
    """
    detection = choose_profile(gpus=gpus, apple_unified_memory_gb=apple, ollama_is_remote=remote)
    assert detection.profile == expected_profile
    assert detection.reason is expected_reason


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_choose_profile_reports_largest_gpu_details() -> None:
    """
    Given  two GPUs with known memory
    When   choose_profile picks the largest one
    Then   the detection names that GPU and its memory
    """
    detection = choose_profile(
        gpus=[("GT 1030", 4.0), ("RTX 4090", 24.0)],
        apple_unified_memory_gb=None,
        ollama_is_remote=False,
    )
    assert detection.gpu_name == "RTX 4090"
    assert detection.memory_gb == 24.0


# ── single source of truth for defaults ─────────────────────────────────────


@pytest.mark.use_case("Model_Category_Guidance")
def test_default_ocr_model_is_a_catalogued_reading_model() -> None:
    """
    Given  the static DEFAULTS and per-task model defaults
    When   the Ollama OCR default is inspected
    Then   it is a catalogued reading model (regression: gpt-oss cannot read images)
           and no online provider has an OCR default
    """
    assert DEFAULTS["ocr_model"] == DEFAULT_OCR_MODEL
    spec = find_spec(DEFAULTS["ocr_model"])
    assert spec is not None
    assert spec.category == "ocr"
    assert "ocr" not in _DEFAULT_MODELS  # no online provider ever reads pages


def test_default_writing_models_equal_the_default_writing_model() -> None:
    """
    Given  the static DEFAULTS, per-task model defaults, and provider metadata
    When   the Ollama writing-model defaults are inspected
    Then   all equal DEFAULT_WRITING_MODEL, a catalogued writing model
    """
    expected = DEFAULT_WRITING_MODEL
    spec = find_spec(expected)
    assert spec is not None
    assert spec.category == "general"
    assert DEFAULTS["correction_model"] == expected
    assert DEFAULTS["evaluate_model"] == expected
    assert _DEFAULT_MODELS["correction"]["ollama"] == expected
    assert _DEFAULT_MODELS["evaluation"]["ollama"] == expected
    assert PROVIDER_INFO["default_model"] == expected


def test_hardware_detection_dataclass_defaults() -> None:
    """
    Given  a HardwareDetection with only profile and reason
    When   the optional fields are inspected
    Then   gpu_name and memory_gb default to None
    """
    detection = HardwareDetection(profile="cpu", reason=DetectionReason.NO_GPU_FOUND)
    assert detection.gpu_name is None
    assert detection.memory_gb is None


def test_profile_settings_cover_auto_and_all_profiles() -> None:
    """
    Given  the profile setting tuple
    When   compared against HARDWARE_PROFILES
    Then   it contains "auto" plus every concrete profile
    """
    assert PROFILE_SETTINGS == ("auto", *HARDWARE_PROFILES)


# ── detection details (review findings #6, remote host) ─────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("ram_gb", "expected_profile"),
    [(8.0, "cpu"), (16.0, "8gb"), (24.0, "16gb")],
)
def test_apple_detection_reports_the_real_memory(ram_gb: float, expected_profile: str) -> None:
    """
    Given  Apple Silicon with a given amount of unified memory
    When   choose_profile derives the profile
    Then   the profile uses the GPU share but memory_gb is the memory the
           computer actually has (it used to report the scaled 0.66 share)
    """
    detection = choose_profile(gpus=[], apple_unified_memory_gb=ram_gb, ollama_is_remote=False)
    assert detection.profile == expected_profile
    assert detection.memory_gb == ram_gb


@pytest.mark.use_case("Hardware_Profile_Override")
def test_remote_detection_carries_the_host() -> None:
    """
    Given  an Ollama server on another computer
    When   choose_profile is called with its host name
    Then   the host is stored in `host`, not misused as a GPU name
    """
    detection = choose_profile(
        gpus=[], apple_unified_memory_gb=None, ollama_is_remote=True, host="gpu-box.lan"
    )
    assert detection.reason is DetectionReason.REMOTE_SERVER
    assert detection.host == "gpu-box.lan"
    assert detection.gpu_name is None


@pytest.mark.use_case("Model_Category_Guidance")
def test_rate_model_bundles_everything_a_widget_needs() -> None:
    """
    Given  a catalogued model and a custom, detected vision model
    When   rate_model is called for the reading task
    Then   the rating carries suitability, memory, note and what the model can be used for
    """
    from teachers_teammate.domain.model_catalog import rate_model  # noqa: PLC0415

    best = rate_model(DEFAULT_OCR_MODEL, "ocr", "8gb")
    assert best.suitability is Suitability.BEST
    assert best.model_uses == frozenset({"ocr"})
    assert best.memory_gb == 6.5
    assert best.note

    custom = rate_model(
        "my-vision:latest", "ocr", "8gb", detected_uses=frozenset({"ocr", "general"})
    )
    assert custom.suitability is Suitability.WORKS
    assert custom.model_uses == frozenset({"ocr", "general"})
    assert custom.memory_gb is None
    assert custom.note == ""

    unknown = rate_model("mystery:latest", "ocr", "8gb")
    assert unknown.suitability is Suitability.UNKNOWN
    assert unknown.model_uses is None


@pytest.mark.use_case("Model_Category_Guidance")
def test_catalog_notes_do_not_claim_unverified_capabilities() -> None:
    """
    Given  the catalog
    When   the notes of writing models are read
    Then   none claims image reading unless it is catalogued as a reading model too
    """
    for spec in OLLAMA_CATALOG:
        if spec.category == "general" and "ocr" not in spec.also_uses:
            assert "image" not in spec.note.lower(), spec.tag
