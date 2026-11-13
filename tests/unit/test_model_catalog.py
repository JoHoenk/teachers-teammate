"""Unit tests for teachers_teammate.domain.model_catalog."""

from __future__ import annotations

from dataclasses import replace
from typing import cast
from unittest.mock import patch

import pytest

from teachers_teammate._model_defaults import _DEFAULT_MODELS
from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain import model_catalog
from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    DEFAULT_REQUEST_PROFILE,
    DEFAULT_WRITING_MODEL,
    HARDWARE_PROFILES,
    CARD_USABLE_SHARE,
    CPU_COMFORT_GB,
    FULL_SPEED_SHARE,
    OLLAMA_CATALOG,
    PROFILE_SETTINGS,
    RECOMMENDED,
    RequestProfile,
    best_model,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    ModelUses,
    Suitability,
    card_budget_gb,
    catalog_uses,
    choose_profile,
    find_spec,
    is_very_slow,
    memory_for_models_gb,
    models_for,
    normalize_tag,
    ram_needed_gb,
    rate_model,
    request_profile,
    suitability,
    suitability_rank,
    system_reserve_gb,
    uses_from_capabilities,
)
from teachers_teammate.domain.performance_estimate import SpeedBand, SpeedSource
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
    Then   memory_gb is at least 70 % of download_gb for every entry: a download can hold parts
           that are not loaded for text (gemma4's image-reading projector is about 1 GB)
    """
    for spec in OLLAMA_CATALOG:
        assert spec.download_gb > 0
        assert spec.memory_gb >= 0.7 * spec.download_gb, spec.tag


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
def test_every_best_model_runs_at_full_speed_on_its_profile() -> None:
    """
    Given  the RECOMMENDED mapping
    When   the BEST model of each (category, profile) is inspected
    Then   it fits the card's usable memory at full speed (for a computer without a card: it is
           within the size that is comfortable on the processor)
    """
    for _category, per_profile in RECOMMENDED.items():
        for profile in HARDWARE_PROFILES:
            best_tag = per_profile[profile][0]
            spec = find_spec(best_tag)
            assert spec is not None
            if profile == "cpu":
                assert spec.memory_gb <= CPU_COMFORT_GB, best_tag
            else:
                assert spec.memory_gb * FULL_SPEED_SHARE <= card_budget_gb(profile), best_tag


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
        ("gemma4:e4b", "cpu", Suitability.BEST),
        ("gemma4:e4b", "8gb", Suitability.BEST),
        ("gemma4:12b", "16gb", Suitability.BEST),
        (DEFAULT_WRITING_MODEL, "8gb", Suitability.BEST),
        # GOOD: further entries of the RECOMMENDED list
        ("qwen3-vl:8b", "8gb", Suitability.GOOD),
        ("deepseek-ocr:latest", "8gb", Suitability.GOOD),
        ("qwen3:4b", "cpu", Suitability.GOOD),
        ("qwen3.5:9b", "8gb", Suitability.GOOD),
        ("qwen3.5:9b", "16gb", Suitability.GOOD),
        # WORKS: catalogued and within the card, but not recommended
        ("qwen2.5vl:7b", "8gb", Suitability.WORKS),
        ("mistral:latest", "8gb", Suitability.WORKS),
        ("mistral:latest", "16gb", Suitability.WORKS),
        ("gemma4:e4b", "16gb", Suitability.WORKS),
        # TOO_LARGE: more than the card holds, and no figure for the computer's memory
        ("qwen3-vl:8b", "cpu", Suitability.TOO_LARGE),
        ("gpt-oss:20b", "8gb", Suitability.TOO_LARGE),
        ("mistral-nemo:12b", "8gb", Suitability.TOO_LARGE),
        ("gemma4:12b", "8gb", Suitability.TOO_LARGE),
    ],
)
def test_suitability_examples_per_profile(tag: str, profile: str, expected: Suitability) -> None:
    """
    Given  a catalogued model and a hardware profile
    When   suitability is computed for the model's own category
    Then   the rating matches the recommendation table (BEST/GOOD/WORKS/TOO_LARGE); without a
           figure for the computer's memory nothing is slow
    """
    category = find_spec(tag).category
    assert suitability(tag, category, cast(HardwareProfile, profile)) is expected


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("profile", "system_memory_gb", "expected"),
    [
        ("8gb", 32.0, Suitability.SLOW),  # 7.2 GB of 14 run on the processor; 24 GB are free
        ("8gb", 16.0, Suitability.SLOW),  # 8 GB stay free for the system, 8 GB remain >= 7.2
        ("8gb", 15.0, Suitability.TOO_LARGE),  # 7 GB remain < 7.2: it would have to swap
        ("8gb", 8.0, Suitability.TOO_LARGE),  # nothing remains after the reserve
        ("8gb", None, Suitability.TOO_LARGE),  # memory unknown (remote server): as before
        ("cpu", 22.0, Suitability.SLOW),  # all 14 GB on the processor: 22 - 8 = 14 remain
        ("cpu", 21.0, Suitability.TOO_LARGE),  # 13 remain < 14
        ("cpu", 16.0, Suitability.TOO_LARGE),  # 8 remain < 14
        ("16gb", 8.0, Suitability.WORKS),  # fits the card: main memory is irrelevant
    ],
)
def test_a_model_too_big_for_the_card_is_slow_only_when_main_memory_holds_the_rest(
    profile: str, system_memory_gb: float | None, expected: Suitability
) -> None:
    """
    Given  gpt-oss:20b (about 14 GB) and a computer with a given graphics profile and memory
    When   suitability is computed
    Then   it is "slow" when what does not fit on the card fits into the main memory left after
           the system's reserve (never counting swap), too large when it does not, unchanged
           without a memory figure, and "works" when it fits the card
    """
    rated = suitability(
        "gpt-oss:20b", "general", cast(HardwareProfile, profile), system_memory_gb=system_memory_gb
    )

    assert rated is expected


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_model_that_fits_the_card_is_not_called_slow() -> None:
    """
    Given  a model within the card's budget
    When   it is rated on a computer with plenty of memory
    Then   it is still "works" (slow is only for what does not fit the card)
    """
    assert (
        suitability("mistral:latest", "general", "16gb", system_memory_gb=64.0) is Suitability.WORKS
    )


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_slow_models_sort_between_works_and_too_large() -> None:
    """
    Given  the suitability sort ranks
    When   they are compared
    Then   BEST < GOOD < WORKS < SLOW < TOO_LARGE, so a slow model is listed before one that
           is too large and after every model that runs well
    """
    order = [
        Suitability.BEST,
        Suitability.GOOD,
        Suitability.WORKS,
        Suitability.SLOW,
        Suitability.TOO_LARGE,
    ]
    ranks = [suitability_rank(value) for value in order]

    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_rate_model_passes_the_computers_memory_on() -> None:
    """
    Given  a model too big for an 8 GB card
    When   it is rated with and without the computer's memory
    Then   the rating changes from too large to slow
    """
    assert rate_model("gpt-oss:20b", "general", "8gb").suitability is Suitability.TOO_LARGE
    assert (
        rate_model("gpt-oss:20b", "general", "8gb", system_memory_gb=32.0).suitability
        is Suitability.SLOW
    )


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_choose_profile_carries_the_computers_memory_but_not_for_a_remote_server() -> None:
    """
    Given  a local computer with a 16 GB card and 32 GB of memory, and a remote Ollama server
    When   the profile is chosen
    Then   the local detection reports the memory, the remote one does not (it is the memory
           of another computer)
    """
    local = choose_profile(
        gpus=[("RTX 3080 Laptop GPU", 16.0)],
        apple_unified_memory_gb=None,
        ollama_is_remote=False,
        system_memory_gb=32.0,
    )
    remote = choose_profile(
        gpus=[("RTX 3080 Laptop GPU", 16.0)],
        apple_unified_memory_gb=None,
        ollama_is_remote=True,
        host="server",
        system_memory_gb=32.0,
    )
    no_gpu = choose_profile(
        gpus=[], apple_unified_memory_gb=None, ollama_is_remote=False, system_memory_gb=16.0
    )

    assert (local.profile, local.system_memory_gb) == ("16gb", 32.0)
    assert remote.system_memory_gb is None
    assert (no_gpu.profile, no_gpu.system_memory_gb) == ("cpu", 16.0)


@pytest.mark.use_case("Model_Category_Guidance")
def test_suitability_general_model_for_ocr_task_is_wrong_kind() -> None:
    """
    Given  a writing model asked for as a reading model
    When   suitability is computed for the "ocr" category
    Then   the result is WRONG_KIND
    """
    assert suitability("mistral:latest", "ocr", "8gb") is Suitability.WRONG_KIND
    assert suitability("gemma4:e4b", "ocr", "8gb") is Suitability.WRONG_KIND


@pytest.mark.use_case("Model_Category_Guidance")
def test_qwen35_is_a_good_reading_alternative_and_a_good_writing_model() -> None:
    """
    Given  qwen3.5:9b, a writing model that can also read scanned pages
    When   it is rated for reading and for writing on the 8gb profile
    Then   it is GOOD for both: the first choice for neither
    """
    spec = find_spec("qwen3.5:9b")
    assert spec is not None
    assert catalog_uses(spec) == frozenset({"ocr", "general"})
    assert suitability("qwen3.5:9b", "ocr", "8gb") is Suitability.GOOD
    assert suitability("qwen3.5:9b", "general", "8gb") is Suitability.GOOD


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


# ── the speed a model is expected to reach ─────────────────────────────────


def test_a_model_that_was_never_measured_gets_the_band_it_is_expected_to_reach() -> None:
    """
    Given  a computer that reaches half the reference machine's writing speed
    When   gemma4:e4b is rated for writing
    Then   it carries the band that half speed gives, and says the figure is expected
    """
    rating = rate_model("gemma4:e4b", "general", "8gb", machine_factor=0.5)

    assert rating.speed is SpeedBand.OK
    assert rating.speed_source is SpeedSource.EXPECTED


def test_a_slower_computer_gets_a_slower_band() -> None:
    """
    Given  a computer at a twentieth of the reference machine's writing speed
    When   gemma4:e4b is rated for writing
    Then   the band is very slow, because a page takes minutes there
    """
    rating = rate_model("gemma4:e4b", "general", "8gb", machine_factor=0.05)

    assert rating.speed is SpeedBand.VERY_SLOW


def test_without_a_measured_model_there_is_no_expected_speed() -> None:
    """
    Given  no machine factor, because nothing was measured on this server
    When   a catalogued model is rated
    Then   it carries no band, so the memory rating is all a teacher sees
    """
    rating = rate_model("gemma4:e4b", "general", "8gb")

    assert rating.speed is None
    assert rating.speed_source is None


def test_a_model_that_does_not_fit_the_card_gets_no_expected_speed() -> None:
    """
    Given  a machine factor and a model too large for the card
    When   it is rated
    Then   it stays too large and carries no band, because it is not a download to consider
    """
    rating = rate_model("gpt-oss:20b", "general", "8gb", machine_factor=0.5)

    assert rating.suitability is Suitability.TOO_LARGE
    assert rating.speed is None


def test_a_model_that_spills_to_the_processor_gets_no_expected_speed() -> None:
    """
    Given  a machine factor and a model that only fits with the help of the main memory
    When   it is rated
    Then   it stays slow and carries no band, because spilling breaks the factor
    """
    rating = rate_model("gpt-oss:20b", "general", "8gb", system_memory_gb=47.0, machine_factor=0.5)

    assert rating.suitability is Suitability.SLOW
    assert rating.speed is None


def test_a_model_of_the_wrong_kind_gets_no_expected_speed() -> None:
    """
    Given  a machine factor and a reading model rated for writing
    When   it is rated
    Then   it carries no band, because it is not used for this task
    """
    rating = rate_model("glm-ocr:q8_0", "general", "8gb", machine_factor=0.5)

    assert rating.suitability is Suitability.WRONG_KIND
    assert rating.speed is None


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


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_apple_silicon_does_not_offload_into_memory_its_profile_already_counts() -> None:
    """
    Given  an Apple Silicon computer with 16 GB of shared memory (profile 8gb: 66% of it)
    When   the profile is chosen and gpt-oss:20b (about 14 GB) is rated with the result
    Then   no separate memory figure is carried and the model stays "too large", because the
           graphics share is carved out of that same memory
    """
    detection = choose_profile(
        gpus=[], apple_unified_memory_gb=16.0, ollama_is_remote=False, system_memory_gb=16.0
    )

    assert (detection.profile, detection.system_memory_gb) == ("8gb", None)
    rated = suitability(
        "gpt-oss:20b", "general", detection.profile, system_memory_gb=detection.system_memory_gb
    )
    assert rated is Suitability.TOO_LARGE


@pytest.mark.use_case("Model_Category_Guidance")
def test_writing_models_offered_first_pass_the_structured_output_check() -> None:
    """
    Given  the default writing model and the RECOMMENDED[general] lists
    When   their catalog flags are inspected
    Then   the model the app picks for proofreading and the first entry of every general
           list are marked as answering the review schema — the rule that an
           unverified model must not be the one a teacher gets by default
    """
    default = find_spec(DEFAULT_WRITING_MODEL)
    assert default is not None
    assert default.structured_output, f"{DEFAULT_WRITING_MODEL} must support explanations"

    for profile in HARDWARE_PROFILES:
        first = find_spec(RECOMMENDED["general"][profile][0])
        assert first is not None
        assert first.structured_output, f"{first.tag} is the first choice for {profile}"


# ── memory-aware rating (the computer's own memory, never swap) ─────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("system_memory_gb", "expected_reserve"),
    [(8.0, 8.0), (16.0, 8.0), (40.0, 8.0), (48.0, 9.6), (64.0, 12.8)],
)
def test_the_system_reserve_is_the_larger_of_8_gb_and_20_percent(
    system_memory_gb: float, expected_reserve: float
) -> None:
    """
    Given  computers with different amounts of main memory
    When   the reserve for the system is computed
    Then   it is 8 GB up to 40 GB of memory and 20 % above
    """
    assert system_reserve_gb(system_memory_gb) == pytest.approx(expected_reserve)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_the_card_budget_is_the_usable_share_of_the_nominal_card() -> None:
    """
    Given  the three hardware profiles
    When   the card budget is computed without a known card size
    Then   it is 85 % of the profile's nominal size, and 0 for a computer typed "cpu"
           (a real card arrives as a known size instead)
    """
    assert card_budget_gb("8gb") == pytest.approx(8.0 * CARD_USABLE_SHARE)
    assert card_budget_gb("16gb") == pytest.approx(16.0 * CARD_USABLE_SHARE)
    assert card_budget_gb("cpu") == 0.0


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_recommended_model_is_not_best_where_it_cannot_work() -> None:
    """
    Given  the recommended model for computers without a card (5 GB) and computers with 8, 12
           and 16 GB of main memory, of which 8 GB are reserved for the system
    When   it is rated with the computer's memory known, and without it
    Then   it is too large where the memory beyond the reserve is under 5 GB (swapping does not
           count), best from 16 GB, and keeps its recommendation when the memory is unknown (a
           remote server)
    """
    assert rate_model("gemma4:e4b", "general", "cpu", system_memory_gb=8.0).suitability is (
        Suitability.TOO_LARGE
    )
    assert rate_model("gemma4:e4b", "general", "cpu", system_memory_gb=12.0).suitability is (
        Suitability.TOO_LARGE
    )
    assert rate_model("gemma4:e4b", "general", "cpu", system_memory_gb=16.0).suitability is (
        Suitability.BEST
    )
    assert rate_model("gemma4:e4b", "general", "cpu").suitability is Suitability.BEST


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_the_rating_uses_the_real_card_not_the_nominal_one() -> None:
    """
    Given  gemma4:12b (9.5 GB) on a computer in the 8gb profile with 47 GB of memory
    When   it is rated with a 12 GB card (10.2 GB usable) and with the nominal 8 GB card
    Then   it works on the larger card and is "slower" (a share of 0.72 of it fits) on the
           nominal one
    """
    big = rate_model("gemma4:12b", "general", "8gb", system_memory_gb=47.0, card_gb=10.2)
    nominal = rate_model("gemma4:12b", "general", "8gb", system_memory_gb=47.0)

    assert big.suitability is Suitability.WORKS
    assert nominal.suitability is Suitability.SLOW
    assert nominal.very_slow is False


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_model_of_which_little_fits_the_card_is_very_slow() -> None:
    """
    Given  gpt-oss:20b (14 GB) on the 8gb profile and gemma4:12b (9.5 GB)
    When   they are rated with plenty of main memory
    Then   both are slow, gpt-oss:20b very slowly (under 70 % of it fits the card), gemma4:12b
           only slower (72 % fits; measured: 13 tokens per second against 77 for the small one)
    """
    huge = rate_model("gpt-oss:20b", "general", "8gb", system_memory_gb=47.0)
    large = rate_model("gemma4:12b", "general", "8gb", system_memory_gb=47.0)

    assert (huge.suitability, huge.very_slow) == (Suitability.SLOW, True)
    assert (large.suitability, large.very_slow) == (Suitability.SLOW, False)
    assert is_very_slow(14.0, card_budget_gb("8gb")) and not is_very_slow(
        9.5, card_budget_gb("8gb")
    )


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_computer_without_a_card_runs_small_models_comfortably_and_others_very_slowly() -> None:
    """
    Given  a computer without a graphics card and 32 GB of main memory
    When   a 5 GB model and a 9.5 GB model are rated
    Then   the first works, the second is slow, very slowly (all of it runs on the processor)
    """
    small = rate_model("gemma4:e4b", "general", "cpu", system_memory_gb=32.0)
    large = rate_model("gemma4:12b", "general", "cpu", system_memory_gb=32.0)

    assert small.suitability is Suitability.BEST  # also the recommendation
    assert (large.suitability, large.very_slow) == (Suitability.SLOW, True)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_memory_for_models_is_the_card_plus_what_the_reserve_leaves() -> None:
    """
    Given  an 8gb computer with 16 GB of main memory, and one without a card with 12 GB
    When   the memory a model may use is computed
    Then   it is the usable card plus the main memory beyond the 8 GB reserve; unknown memory
           gives no figure
    """
    assert memory_for_models_gb("8gb", 16.0) == pytest.approx(6.8 + 8.0)
    assert memory_for_models_gb("cpu", 12.0) == pytest.approx(4.0)
    assert memory_for_models_gb("8gb", None) is None


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_the_detection_reports_the_graphics_memory_models_can_use() -> None:
    """
    Given  detections of a 16 GB card, Apple Silicon with 16 GB, a hand-chosen profile and a
           computer without a card
    When   card_gb is read
    Then   a card gives 85 % of its size, Apple 66 % of its unified memory, and the others no
           figure (the rating assumes the profile's nominal size, or none without a card)
    """
    card = choose_profile(
        gpus=[("RTX", 16.0)], apple_unified_memory_gb=None, ollama_is_remote=False
    )
    apple = choose_profile(gpus=[], apple_unified_memory_gb=16.0, ollama_is_remote=False)
    manual = HardwareDetection(profile="8gb", reason=DetectionReason.MANUAL)
    plain = choose_profile(gpus=[], apple_unified_memory_gb=None, ollama_is_remote=False)

    assert card.card_gb == pytest.approx(16.0 * 0.85)
    assert apple.card_gb == pytest.approx(16.0 * 0.66)
    assert manual.card_gb is None
    assert plain.card_gb is None


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_small_card_decides_the_rating_even_though_the_profile_is_cpu() -> None:
    """
    Given  a 6 GB graphics card (5.1 GB usable) with 16 GB of main memory
    When   the writing models are rated — the card is under 7 GB, so the profile is "cpu"
    Then   the 5.0 GB model works (it fits the card entirely) and the 7.2 GB model is slow but
           not very slow (71 % of it fits), and only the spill needs main memory
    """
    card = 6.0 * CARD_USABLE_SHARE
    small = rate_model("gemma4:e4b", "general", "cpu", system_memory_gb=16.0, card_gb=card)
    large = rate_model("qwen3.5:9b", "general", "cpu", system_memory_gb=16.0, card_gb=card)

    assert card == pytest.approx(5.1)
    assert small.suitability is Suitability.BEST  # it fits the card entirely; also recommended
    assert (large.suitability, large.very_slow) == (Suitability.SLOW, False)
    assert ram_needed_gb(7.2, "cpu", card) == pytest.approx(2.1)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_card_under_seven_gigabytes_is_reported_although_the_profile_is_cpu() -> None:
    """
    Given  a detected 6 GB card, which gives the "cpu" profile
    When   the detection's card size is read
    Then   it reports the usable card size (the card is there, it only is not a profile tier)
    """
    detection = choose_profile(
        gpus=[("GTX 1660", 6.0)], apple_unified_memory_gb=None, ollama_is_remote=False
    )

    assert detection.profile == "cpu"
    assert detection.card_gb == pytest.approx(6.0 * CARD_USABLE_SHARE)


# ── the request profile ────────────────────────────────────────────────────


def test_unknown_models_get_the_default_profile() -> None:
    """
    Given  a tag the catalog does not know
    When   its request profile is asked for
    Then   it is the default: no compact-JSON rule, and the app's own temperature 0.2
    """
    profile = request_profile("some-model:7b")

    assert profile == DEFAULT_REQUEST_PROFILE
    assert profile.compact_json is False
    assert profile.temperature == 0.2


def test_every_catalog_model_starts_on_the_default_profile() -> None:
    """
    Given  the catalog
    When   each model's request profile is read
    Then   all of them are the default, so no request changes until a gate-1 run decides
    """
    assert {spec.request_profile for spec in OLLAMA_CATALOG} == {DEFAULT_REQUEST_PROFILE}


def test_a_catalog_profile_is_found_by_its_tag() -> None:
    """
    Given  a catalog model whose profile asks for compact JSON
    When   the profile is asked for by tag, with and without the ``:latest`` suffix
    Then   the catalog's profile comes back, not the default
    """
    spec = replace(
        find_spec("gemma4:e4b"),  # ty: ignore[invalid-argument-type]  # the tag is in the catalog
        request_profile=RequestProfile(compact_json=True, temperature=1.0),
    )
    catalog = tuple(spec if other.tag == spec.tag else other for other in OLLAMA_CATALOG)
    with patch.object(model_catalog, "OLLAMA_CATALOG", catalog):
        assert request_profile("gemma4:e4b") == spec.request_profile
        assert request_profile("gemma4").compact_json is False


def test_a_model_gets_the_temperature_its_maker_recommends() -> None:
    """
    Given  models with and without a recommended temperature, with and without ``:latest``
    When   their request profile and review temperature are asked for
    Then   a recommended model gets it, the rest the defaults 0.2 and 0.7
    """
    from teachers_teammate.domain.model_catalog import review_temperature  # noqa: PLC0415

    assert request_profile("gemma4:e4b").temperature == 1.0
    assert request_profile("qwen3:4b").temperature == 0.7
    assert request_profile("ministral-3:8b").temperature == 0.05
    assert request_profile("gemma4:e4b:latest").temperature == 1.0
    assert request_profile("gpt-oss:20b").temperature == 0.2
    assert request_profile("hf.co/some/candidate:Q4").temperature == 0.2
    assert review_temperature("gemma4:e4b") == 1.0
    assert review_temperature("gpt-oss:20b") == 0.7


def test_the_recommended_temperatures_apply_to_ollama_only() -> None:
    """
    Given  an online provider asked about a name that is also a recommended Ollama tag
    When   the request profile and review temperature are asked for
    Then   the table does not apply
    """
    from teachers_teammate.domain.model_catalog import review_temperature  # noqa: PLC0415

    assert request_profile("gemma4:e4b", "google").temperature == 0.2
    assert review_temperature("gemma4:e4b", "google") == 0.7


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_best_model_skips_a_model_too_large_for_the_main_memory() -> None:
    """
    Given  a computer without a graphics card and 12 GB of main memory (4 GB usable)
    When   the BEST writing model is asked for, and again with 32 GB and with no figure
    Then   it is qwen3:4b on the small computer, which suitability() marks BEST and gemma4:e4b
           (5 GB) TOO_LARGE; with enough memory or without a figure it is gemma4:e4b
    """
    assert best_model("general", "cpu", system_memory_gb=12.0) == "qwen3:4b"
    assert suitability("qwen3:4b", "general", "cpu", system_memory_gb=12.0) is Suitability.BEST
    assert (
        suitability("gemma4:e4b", "general", "cpu", system_memory_gb=12.0) is Suitability.TOO_LARGE
    )
    assert best_model("general", "cpu", system_memory_gb=32.0) == "gemma4:e4b"
    assert best_model("general", "cpu") == "gemma4:e4b"


@pytest.mark.use_case("Model_Request_Profile")
def test_the_request_profile_asks_gpt_oss_for_a_level_not_the_flag() -> None:
    """
    Given  gpt-oss (which ignores think=False), a model of another family and an online provider
    When   the request profile of each is read
    Then   gpt-oss gets the lowest reasoning level and the others keep the flag off, so the cache
           key, the evaluation tools and the request read one value
    """
    assert request_profile("gpt-oss:20b").think == "low"
    assert request_profile("hf.co/unsloth/gpt-oss-20b-GGUF:UD-Q4_K_XL").think == "low"
    assert request_profile("gemma4:e4b").think is False
    assert request_profile("gpt-oss:20b", "openai").think is False
