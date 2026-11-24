"""Unit tests for teachers_teammate.domain.model_catalog."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from teachers_teammate._model_defaults import _DEFAULT_MODELS, default_model_for_task
from teachers_teammate.settings_schema import DEFAULTS
from teachers_teammate.domain import model_catalog
from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    DEFAULT_REQUEST_PROFILE,
    DEFAULT_WRITING_MODEL,
    DISK_MARGIN_GB,
    HARDWARE_PROFILES,
    CARD_USABLE_SHARE,
    CPU_COMFORT_GB,
    FULL_SPEED_SHARE,
    OLLAMA_CATALOG,
    PROFILE_SETTINGS,
    RECOMMENDED,
    RequestProfile,
    UNKNOWN_PROFILE,
    active_profile,
    best_model,
    DetectionReason,
    DiskVerdict,
    HardwareDetection,
    HardwareProfile,
    HardwareProfileSetting,
    ModelUses,
    Suitability,
    card_budget_gb,
    catalog_uses,
    choose_profile,
    concrete_profile,
    profile_setting,
    disk_verdict,
    find_spec,
    is_very_slow,
    licence_note,
    memory_for_models_gb,
    models_for,
    normalize_tag,
    ram_needed_gb,
    rate_model,
    request_profile,
    short_model_name,
    suitability,
    suitability_rank,
    system_reserve_gb,
    uses_from_capabilities,
)
from teachers_teammate.domain.performance_estimate import SpeedBand, SpeedSource
from teachers_teammate.domain.stages import Stage


# ── short names ──────────────────────────────────────────────────────────────


def test_short_model_name_fits_a_narrow_line() -> None:
    """
    Given  a Hugging Face tag, a ":latest" tag, a plain tag and an empty string
    When   each is shortened
    Then   the repository name, the bare name, the unchanged tag and "" come back
    """
    assert short_model_name("hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0") == "chandra-ocr-2"
    assert short_model_name("hf.co/org/repo:Q4_K_M") == "repo"
    assert short_model_name("gemma4:latest") == "gemma4"
    assert short_model_name("gemma4:e4b") == "gemma4:e4b"
    assert short_model_name("deepseek-ocr") == "deepseek-ocr"
    assert short_model_name("") == ""


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


def test_every_catalog_entry_names_its_licence_and_where_to_read_it() -> None:
    """
    Given  the Ollama model catalog
    When   the licence of every entry is inspected
    Then   each names its terms and links to them, so the app can say what a teacher agrees to
    """
    for spec in OLLAMA_CATALOG:
        assert spec.licence is not None, spec.tag
        assert spec.licence.name, spec.tag
        assert spec.licence.url.startswith("https://"), spec.tag


def test_only_a_licence_that_restricts_use_gets_a_sentence() -> None:
    """
    Given  the default reading model (modified OpenRAIL-M) and a model under Apache-2.0
    When   the sentence for a model's row is asked for
    Then   the restrictive one has one, the permissive one and an unknown tag have none
    """
    assert "check the licence" in licence_note(DEFAULT_OCR_MODEL)
    assert licence_note(DEFAULT_WRITING_MODEL) == ""
    assert licence_note("a-model-the-app-does-not-know:latest") == ""


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
            if profile == HardwareProfile.CPU:
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
        (DEFAULT_OCR_MODEL, HardwareProfile.CARD_8GB, Suitability.BEST),
        (DEFAULT_OCR_MODEL, HardwareProfile.CARD_16GB, Suitability.BEST),
        ("glm-ocr:q8_0", HardwareProfile.CPU, Suitability.BEST),
        ("gemma4:e4b", HardwareProfile.CPU, Suitability.BEST),
        ("gemma4:e4b", HardwareProfile.CARD_8GB, Suitability.BEST),
        ("gemma4:12b", HardwareProfile.CARD_16GB, Suitability.BEST),
        (DEFAULT_WRITING_MODEL, HardwareProfile.CARD_8GB, Suitability.BEST),
        # GOOD: further entries of the RECOMMENDED list
        ("qwen3-vl:8b", HardwareProfile.CARD_8GB, Suitability.GOOD),
        ("deepseek-ocr:latest", HardwareProfile.CARD_8GB, Suitability.GOOD),
        ("qwen3.5:9b", HardwareProfile.CARD_8GB, Suitability.GOOD),
        ("qwen3.5:9b", HardwareProfile.CARD_16GB, Suitability.GOOD),
        # WORKS: catalogued and within the card, but not recommended
        ("qwen3:4b", HardwareProfile.CPU, Suitability.WORKS),
        ("qwen2.5vl:7b", HardwareProfile.CARD_8GB, Suitability.WORKS),
        ("mistral:latest", HardwareProfile.CARD_8GB, Suitability.WORKS),
        ("mistral:latest", HardwareProfile.CARD_16GB, Suitability.WORKS),
        ("gemma4:e4b", HardwareProfile.CARD_16GB, Suitability.WORKS),
        # TOO_LARGE: more than the card holds, and no figure for the computer's memory
        ("qwen3-vl:8b", HardwareProfile.CPU, Suitability.TOO_LARGE),
        ("gpt-oss:20b", HardwareProfile.CARD_8GB, Suitability.TOO_LARGE),
        ("mistral-nemo:12b", HardwareProfile.CARD_8GB, Suitability.TOO_LARGE),
        ("gemma4:12b", HardwareProfile.CARD_8GB, Suitability.TOO_LARGE),
    ],
)
def test_suitability_examples_per_profile(
    tag: str, profile: HardwareProfile, expected: Suitability
) -> None:
    """
    Given  a catalogued model and a hardware profile
    When   suitability is computed for the model's own category
    Then   the rating matches the recommendation table (BEST/GOOD/WORKS/TOO_LARGE); without a
           figure for the computer's memory nothing is slow
    """
    category = find_spec(tag).category
    assert suitability(tag, category, profile) is expected


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("profile", "system_memory_gb", "expected"),
    [
        (
            HardwareProfile.CARD_8GB,
            32.0,
            Suitability.SLOW,
        ),  # 7.2 GB of 14 run on the processor; 24 GB are free
        (
            HardwareProfile.CARD_8GB,
            16.0,
            Suitability.SLOW,
        ),  # 8 GB stay free for the system, 8 GB remain >= 7.2
        (
            HardwareProfile.CARD_8GB,
            15.0,
            Suitability.TOO_LARGE,
        ),  # 7 GB remain < 7.2: it would have to swap
        (HardwareProfile.CARD_8GB, 8.0, Suitability.TOO_LARGE),  # nothing remains after the reserve
        (
            HardwareProfile.CARD_8GB,
            None,
            Suitability.TOO_LARGE,
        ),  # memory unknown (remote server): as before
        (
            HardwareProfile.CPU,
            22.0,
            Suitability.SLOW,
        ),  # all 14 GB on the processor: 22 - 8 = 14 remain
        (HardwareProfile.CPU, 21.0, Suitability.TOO_LARGE),  # 13 remain < 14
        (HardwareProfile.CPU, 16.0, Suitability.TOO_LARGE),  # 8 remain < 14
        (
            HardwareProfile.CARD_16GB,
            8.0,
            Suitability.WORKS,
        ),  # fits the card: main memory is irrelevant
    ],
)
def test_a_model_too_big_for_the_card_is_slow_only_when_main_memory_holds_the_rest(
    profile: HardwareProfile, system_memory_gb: float | None, expected: Suitability
) -> None:
    """
    Given  gpt-oss:20b (about 14 GB) and a computer with a given graphics profile and memory
    When   suitability is computed
    Then   it is "slow" when what does not fit on the card fits into the main memory left after
           the system's reserve (never counting swap), too large when it does not, unchanged
           without a memory figure, and "works" when it fits the card
    """
    rated = suitability("gpt-oss:20b", "general", profile, system_memory_gb=system_memory_gb)

    assert rated is expected


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_model_that_fits_the_card_is_not_called_slow() -> None:
    """
    Given  a model within the card's budget
    When   it is rated on a computer with plenty of memory
    Then   it is still "works" (slow is only for what does not fit the card)
    """
    assert (
        suitability("mistral:latest", "general", HardwareProfile.CARD_16GB, system_memory_gb=64.0)
        is Suitability.WORKS
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
    assert (
        rate_model("gpt-oss:20b", "general", HardwareProfile.CARD_8GB).suitability
        is Suitability.TOO_LARGE
    )
    assert (
        rate_model(
            "gpt-oss:20b", "general", HardwareProfile.CARD_8GB, system_memory_gb=32.0
        ).suitability
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

    assert (local.profile, local.system_memory_gb) == (HardwareProfile.CARD_16GB, 32.0)
    assert remote.system_memory_gb is None
    assert (no_gpu.profile, no_gpu.system_memory_gb) == (HardwareProfile.CPU, 16.0)


@pytest.mark.use_case("Model_Category_Guidance")
def test_suitability_general_model_for_ocr_task_is_wrong_kind() -> None:
    """
    Given  a writing model asked for as a reading model
    When   suitability is computed for the "ocr" category
    Then   the result is WRONG_KIND
    """
    assert suitability("mistral:latest", "ocr", HardwareProfile.CARD_8GB) is Suitability.WRONG_KIND
    assert suitability("gemma4:e4b", "ocr", HardwareProfile.CARD_8GB) is Suitability.WRONG_KIND


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
    assert suitability("qwen3.5:9b", "ocr", HardwareProfile.CARD_8GB) is Suitability.GOOD
    assert suitability("qwen3.5:9b", "general", HardwareProfile.CARD_8GB) is Suitability.GOOD


@pytest.mark.use_case("Model_Category_Guidance")
@pytest.mark.parametrize(
    "profile", [HardwareProfile.CPU, HardwareProfile.CARD_8GB, HardwareProfile.CARD_16GB]
)
@pytest.mark.parametrize("tag", [spec.tag for spec in OLLAMA_CATALOG if spec.category == "ocr"])
def test_suitability_catalog_reading_model_for_general_task_is_wrong_kind(
    tag: str, profile: HardwareProfile
) -> None:
    """
    Given  any catalogued reading model (dedicated OCR models do not follow a proofreading prompt)
    When   suitability is computed for the "general" (writing) task
    Then   the result is WRONG_KIND on every profile
    """
    assert suitability(tag, "general", profile) is Suitability.WRONG_KIND


@pytest.mark.use_case("Model_Category_Guidance")
def test_suitability_unknown_tag_without_detected_uses_is_unknown() -> None:
    """
    Given  a model that is neither catalogued nor classified
    When   suitability is computed
    Then   the result is UNKNOWN
    """
    assert suitability("llama3.2:latest", "ocr", HardwareProfile.CARD_8GB) is Suitability.UNKNOWN


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
    assert (
        suitability("gemma3:4b", "ocr", HardwareProfile.CPU, detected_uses=both)
        is Suitability.WORKS
    )
    assert (
        suitability("gemma3:4b", "general", HardwareProfile.CPU, detected_uses=both)
        is Suitability.WORKS
    )
    assert suitability(
        "llama3.2:latest", "general", HardwareProfile.CARD_8GB, detected_uses=writing_only
    ) is (Suitability.WORKS)
    assert suitability(
        "llama3.2:latest", "ocr", HardwareProfile.CARD_8GB, detected_uses=writing_only
    ) is (Suitability.WRONG_KIND)


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
    rows = models_for("ocr", HardwareProfile.CARD_8GB)
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
        ([], None, False, HardwareProfile.CPU, DetectionReason.NO_GPU_FOUND),
        ([("GT 1030", 6.0)], None, False, HardwareProfile.CPU, DetectionReason.GPU_DETECTED),
        ([("RTX 4060", 7.6)], None, False, HardwareProfile.CARD_8GB, DetectionReason.GPU_DETECTED),
        ([("RX 7600", 12.0)], None, False, HardwareProfile.CARD_8GB, DetectionReason.GPU_DETECTED),
        (
            [("RTX 4090", 15.9)],
            None,
            False,
            HardwareProfile.CARD_16GB,
            DetectionReason.GPU_DETECTED,
        ),
        (
            [("RTX 4090", 24.0)],
            None,
            False,
            HardwareProfile.CARD_16GB,
            DetectionReason.GPU_DETECTED,
        ),
        # The largest single GPU wins.
        (
            [("GT 1030", 4.0), ("RTX 4090", 16.0)],
            None,
            False,
            HardwareProfile.CARD_16GB,
            DetectionReason.GPU_DETECTED,
        ),
        # GPU found but memory unreadable.
        (
            [("Mystery GPU", None)],
            None,
            False,
            HardwareProfile.CARD_8GB,
            DetectionReason.GPU_MEMORY_UNKNOWN,
        ),
        # Apple unified memory thresholds (0.66 x RAM).
        ([], 8.0, False, HardwareProfile.CPU, DetectionReason.APPLE_UNIFIED_MEMORY),
        ([], 16.0, False, HardwareProfile.CARD_8GB, DetectionReason.APPLE_UNIFIED_MEMORY),
        ([], 24.0, False, HardwareProfile.CARD_16GB, DetectionReason.APPLE_UNIFIED_MEMORY),
        # A remote Ollama server ignores the local GPU entirely.
        ([("RTX 4090", 24.0)], None, True, HardwareProfile.CARD_8GB, DetectionReason.REMOTE_SERVER),
    ],
)
def test_choose_profile_thresholds(
    gpus: list,
    apple: float | None,
    remote: bool,
    expected_profile: HardwareProfile,
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
    assert detection.other_gpu_names == ("GT 1030",)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_choose_profile_names_the_further_cards_after_the_one_that_decides() -> None:
    """
    Given  three GPUs, one of them without a readable memory size
    When   choose_profile picks the largest one
    Then   the other card with a known size is named, so the status line can say it was found
           too (a card whose size could not be read cannot have decided the profile)
    """
    detection = choose_profile(
        gpus=[("Intel UHD Graphics", None), ("RTX 4060", 8.0), ("RTX 3050", 4.0)],
        apple_unified_memory_gb=None,
        ollama_is_remote=False,
    )

    assert detection.gpu_name == "RTX 4060"
    assert detection.other_gpu_names == ("RTX 3050",)


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
    assert Stage.OCR not in _DEFAULT_MODELS  # no online provider ever reads pages


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
    assert _DEFAULT_MODELS[Stage.CORRECTION]["ollama"] == expected
    assert _DEFAULT_MODELS[Stage.EVALUATION]["ollama"] == expected


def test_an_unknown_provider_or_task_has_no_default_model() -> None:
    """
    Given  the per-task model defaults
    When   a default model is asked for a name no provider declares, or for text recognition
    Then   it raises instead of answering with another service's model
    """
    with pytest.raises(ValueError, match="provider"):
        default_model_for_task("no_such_provider", Stage.CORRECTION)
    with pytest.raises(ValueError, match="task"):
        default_model_for_task("ollama", Stage.OCR)


def test_hardware_detection_dataclass_defaults() -> None:
    """
    Given  a HardwareDetection with only profile and reason
    When   the optional fields are inspected
    Then   gpu_name and memory_gb default to None
    """
    detection = HardwareDetection(profile=HardwareProfile.CPU, reason=DetectionReason.NO_GPU_FOUND)
    assert detection.gpu_name is None
    assert detection.memory_gb is None


def test_a_profile_chosen_by_hand_wins_before_and_after_a_detection() -> None:
    """
    Given  the teacher has chosen a 16 GB card by hand
    When   the active profile is asked for before and after a detection lands
    Then   the choice decides both times, so the suggestion does not jump when it arrives
    """
    card = HardwareDetection(profile=HardwareProfile.CARD_8GB, reason=DetectionReason.MANUAL)
    chosen = HardwareProfileSetting.CARD_16GB

    assert active_profile(None, chosen) == HardwareProfile.CARD_16GB
    assert active_profile(card, chosen) == HardwareProfile.CARD_16GB


def test_the_active_profile_follows_the_detection_while_the_choice_is_auto() -> None:
    """
    Given  the setting left to the app, and no detection yet
    When   the active profile is asked for
    Then   the weakest profile is assumed, and a detection replaces it once it lands
    """
    card = HardwareDetection(profile=HardwareProfile.CARD_16GB, reason=DetectionReason.GPU_DETECTED)

    assert active_profile(None, "auto") == UNKNOWN_PROFILE
    assert active_profile(None, HardwareProfileSetting.AUTO) == UNKNOWN_PROFILE
    assert active_profile(card, "auto") == HardwareProfile.CARD_16GB


def test_profile_settings_cover_auto_and_all_profiles() -> None:
    """
    Given  the profile setting tuple
    When   compared against HARDWARE_PROFILES
    Then   it contains "auto" plus every concrete profile
    """
    assert PROFILE_SETTINGS == ("auto", *HARDWARE_PROFILES)


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        (HardwareProfileSetting.AUTO, None),
        ("auto", None),
        (HardwareProfileSetting.CARD_8GB, HardwareProfile.CARD_8GB),
        ("16gb", HardwareProfile.CARD_16GB),
        (" 16GB ", HardwareProfile.CARD_16GB),
        ("2gb", None),
        ("", None),
    ],
)
def test_concrete_profile_names_the_profile_a_setting_stands_for(
    setting: HardwareProfileSetting | str, expected: HardwareProfile | None
) -> None:
    """
    Given  a profile setting, as the config, the GUI or a TOML file may hand it over
    When   the concrete profile is asked for
    Then   a chosen tier gives its profile, and auto or a value the setting does not take
           gives None
    """
    assert concrete_profile(setting) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (HardwareProfileSetting.CARD_8GB, HardwareProfileSetting.CARD_8GB),
        ("cpu", HardwareProfileSetting.CPU),
        (" 16GB ", HardwareProfileSetting.CARD_16GB),
        ("auto", HardwareProfileSetting.AUTO),
        ("bogus", HardwareProfileSetting.AUTO),
        (None, HardwareProfileSetting.AUTO),
    ],
)
def test_profile_setting_reads_a_value_with_one_rule(
    value: object, expected: HardwareProfileSetting
) -> None:
    """
    Given  a value from a settings dict, a TOML file or a widget
    When   it is read as a profile setting
    Then   case and surrounding spaces are folded, and a value the setting does not take
           is "auto"
    """
    assert profile_setting(value) is expected


# ── detection details (review findings #6, remote host) ─────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("ram_gb", "expected_profile"),
    [
        (8.0, HardwareProfile.CPU),
        (16.0, HardwareProfile.CARD_8GB),
        (24.0, HardwareProfile.CARD_16GB),
    ],
)
def test_apple_detection_reports_the_real_memory(
    ram_gb: float, expected_profile: HardwareProfile
) -> None:
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
    best = rate_model(DEFAULT_OCR_MODEL, "ocr", HardwareProfile.CARD_8GB)
    assert best.suitability is Suitability.BEST
    assert best.model_uses == frozenset({"ocr"})
    assert best.memory_gb == 6.5
    assert best.note

    custom = rate_model(
        "my-vision:latest",
        "ocr",
        HardwareProfile.CARD_8GB,
        detected_uses=frozenset({"ocr", "general"}),
    )
    assert custom.suitability is Suitability.WORKS
    assert custom.model_uses == frozenset({"ocr", "general"})
    assert custom.memory_gb is None
    assert custom.note == ""

    unknown = rate_model("mystery:latest", "ocr", HardwareProfile.CARD_8GB)
    assert unknown.suitability is Suitability.UNKNOWN
    assert unknown.model_uses is None


# ── the speed a model is expected to reach ─────────────────────────────────


def test_a_model_that_was_never_measured_gets_the_band_it_is_expected_to_reach() -> None:
    """
    Given  a computer that reaches half the reference machine's writing speed
    When   gemma4:e4b is rated for writing
    Then   it carries the band that half speed gives, and says the figure is expected
    """
    rating = rate_model("gemma4:e4b", "general", HardwareProfile.CARD_8GB, machine_factor=0.5)

    assert rating.speed is SpeedBand.OK
    assert rating.speed_source is SpeedSource.EXPECTED


def test_a_slower_computer_gets_a_slower_band() -> None:
    """
    Given  a computer at a twentieth of the reference machine's writing speed
    When   gemma4:e4b is rated for writing
    Then   the band is very slow, because a page takes minutes there
    """
    rating = rate_model("gemma4:e4b", "general", HardwareProfile.CARD_8GB, machine_factor=0.05)

    assert rating.speed is SpeedBand.VERY_SLOW


def test_without_a_measured_model_there_is_no_expected_speed() -> None:
    """
    Given  no machine factor, because nothing was measured on this server
    When   a catalogued model is rated
    Then   it carries no band, so the memory rating is all a teacher sees
    """
    rating = rate_model("gemma4:e4b", "general", HardwareProfile.CARD_8GB)

    assert rating.speed is None
    assert rating.speed_source is None


def test_a_model_that_does_not_fit_the_card_gets_no_expected_speed() -> None:
    """
    Given  a machine factor and a model too large for the card
    When   it is rated
    Then   it stays too large and carries no band, because it is not a download to consider
    """
    rating = rate_model("gpt-oss:20b", "general", HardwareProfile.CARD_8GB, machine_factor=0.5)

    assert rating.suitability is Suitability.TOO_LARGE
    assert rating.speed is None


def test_a_model_that_spills_to_the_processor_gets_no_expected_speed() -> None:
    """
    Given  a machine factor and a model that only fits with the help of the main memory
    When   it is rated
    Then   it stays slow and carries no band, because spilling breaks the factor
    """
    rating = rate_model(
        "gpt-oss:20b",
        "general",
        HardwareProfile.CARD_8GB,
        system_memory_gb=47.0,
        machine_factor=0.5,
    )

    assert rating.suitability is Suitability.SLOW
    assert rating.speed is None


def test_a_model_of_the_wrong_kind_gets_no_expected_speed() -> None:
    """
    Given  a machine factor and a reading model rated for writing
    When   it is rated
    Then   it carries no band, because it is not used for this task
    """
    rating = rate_model("glm-ocr:q8_0", "general", HardwareProfile.CARD_8GB, machine_factor=0.5)

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

    assert (detection.profile, detection.system_memory_gb) == (HardwareProfile.CARD_8GB, None)
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
    assert card_budget_gb(HardwareProfile.CARD_8GB) == pytest.approx(8.0 * CARD_USABLE_SHARE)
    assert card_budget_gb(HardwareProfile.CARD_16GB) == pytest.approx(16.0 * CARD_USABLE_SHARE)
    assert card_budget_gb(HardwareProfile.CPU) == 0.0


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
    assert rate_model(
        "gemma4:e4b", "general", HardwareProfile.CPU, system_memory_gb=8.0
    ).suitability is (Suitability.TOO_LARGE)
    assert rate_model(
        "gemma4:e4b", "general", HardwareProfile.CPU, system_memory_gb=12.0
    ).suitability is (Suitability.TOO_LARGE)
    assert rate_model(
        "gemma4:e4b", "general", HardwareProfile.CPU, system_memory_gb=16.0
    ).suitability is (Suitability.BEST)
    assert rate_model("gemma4:e4b", "general", HardwareProfile.CPU).suitability is Suitability.BEST


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_the_rating_uses_the_real_card_not_the_nominal_one() -> None:
    """
    Given  gemma4:12b (9.5 GB) on a computer in the 8gb profile with 47 GB of memory
    When   it is rated with a 12 GB card (10.2 GB usable) and with the nominal 8 GB card
    Then   it works on the larger card and is "slower" (a share of 0.72 of it fits) on the
           nominal one
    """
    big = rate_model(
        "gemma4:12b", "general", HardwareProfile.CARD_8GB, system_memory_gb=47.0, card_gb=10.2
    )
    nominal = rate_model("gemma4:12b", "general", HardwareProfile.CARD_8GB, system_memory_gb=47.0)

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
    huge = rate_model("gpt-oss:20b", "general", HardwareProfile.CARD_8GB, system_memory_gb=47.0)
    large = rate_model("gemma4:12b", "general", HardwareProfile.CARD_8GB, system_memory_gb=47.0)

    assert (huge.suitability, huge.very_slow) == (Suitability.SLOW, True)
    assert (large.suitability, large.very_slow) == (Suitability.SLOW, False)
    assert is_very_slow(14.0, card_budget_gb(HardwareProfile.CARD_8GB)) and not is_very_slow(
        9.5, card_budget_gb(HardwareProfile.CARD_8GB)
    )


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_computer_without_a_card_runs_small_models_comfortably_and_others_very_slowly() -> None:
    """
    Given  a computer without a graphics card and 32 GB of main memory
    When   a 5 GB model and a 9.5 GB model are rated
    Then   the first works, the second is slow, very slowly (all of it runs on the processor)
    """
    small = rate_model("gemma4:e4b", "general", HardwareProfile.CPU, system_memory_gb=32.0)
    large = rate_model("gemma4:12b", "general", HardwareProfile.CPU, system_memory_gb=32.0)

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
    assert memory_for_models_gb(HardwareProfile.CARD_8GB, 16.0) == pytest.approx(6.8 + 8.0)
    assert memory_for_models_gb(HardwareProfile.CPU, 12.0) == pytest.approx(4.0)
    assert memory_for_models_gb(HardwareProfile.CARD_8GB, None) is None


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
    manual = HardwareDetection(profile=HardwareProfile.CARD_8GB, reason=DetectionReason.MANUAL)
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
    small = rate_model(
        "gemma4:e4b", "general", HardwareProfile.CPU, system_memory_gb=16.0, card_gb=card
    )
    large = rate_model(
        "qwen3.5:9b", "general", HardwareProfile.CPU, system_memory_gb=16.0, card_gb=card
    )

    assert card == pytest.approx(5.1)
    assert small.suitability is Suitability.BEST  # it fits the card entirely; also recommended
    assert (large.suitability, large.very_slow) == (Suitability.SLOW, False)
    assert ram_needed_gb(7.2, HardwareProfile.CPU, card) == pytest.approx(2.1)


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

    assert detection.profile == HardwareProfile.CPU
    assert detection.card_gb == pytest.approx(6.0 * CARD_USABLE_SHARE)


# ── the request profile ────────────────────────────────────────────────────


def test_unknown_models_get_the_default_profile_and_the_measured_step_one_temperature() -> None:
    """
    Given  a tag the catalog does not know
    When   its request profile is asked for
    Then   it is the default apart from step ①, which runs at the measured 0.4
    """
    profile = request_profile("some-model:7b")

    assert profile.compact_json is False
    assert profile.exchange == DEFAULT_REQUEST_PROFILE.exchange
    assert profile.explain == DEFAULT_REQUEST_PROFILE.explain
    assert profile.unclear == DEFAULT_REQUEST_PROFILE.unclear
    assert profile.temperature == 0.4


def test_a_variant_of_a_low_temperature_model_keeps_its_models_step_one_temperature() -> None:
    """
    Given  a quantised tag of a model that falls with every step up in temperature
    When   its request profile is asked for
    Then   step ① runs at that model's 0.05, not at the default 0.4, and a longer name that only
           starts like a measured tag does not
    """
    assert request_profile("qwen3.5:4b-q8_0").temperature == 0.05
    assert request_profile("ministral-3:8b-instruct-2512-q4_K_M").temperature == 0.05
    assert request_profile("qwen3.5:40b").temperature == 0.4


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
    Then   the catalog's profile is the base, not the default
    """
    spec = replace(
        find_spec("gemma4:e4b"),  # ty: ignore[invalid-argument-type]  # the tag is in the catalog
        request_profile=RequestProfile(compact_json=True),
    )
    catalog = tuple(spec if other.tag == spec.tag else other for other in OLLAMA_CATALOG)
    with patch.object(model_catalog, "OLLAMA_CATALOG", catalog):
        assert request_profile("gemma4:e4b").compact_json is True
        assert request_profile("gemma4").compact_json is False


def test_step_one_gets_the_measured_temperature_and_the_other_steps_the_makers() -> None:
    """
    Given  models with and without a measured step-① temperature, with and without ``:latest``
    When   their request profile and review temperature are asked for
    Then   step ① gets the measured value (0.4 where none was measured), steps ② and ③ the
           maker's, and the review the maker's or 0.7
    """
    from teachers_teammate.domain.model_catalog import review_temperature  # noqa: PLC0415

    assert request_profile("gemma4:e4b").correct.temperature == 0.4
    assert request_profile("gemma4:e4b").explain.temperature == 1.0
    assert request_profile("qwen3.5:9b").correct.temperature == 0.05
    assert request_profile("qwen3.5:9b").explain.temperature == 0.7
    assert request_profile("ministral-3:8b").correct.temperature == 0.05
    assert request_profile("gemma4:e4b:latest").correct.temperature == 0.4
    assert request_profile("gpt-oss:20b").correct.temperature == 0.4
    assert request_profile("hf.co/some/candidate:Q4").correct.temperature == 0.4
    assert review_temperature("gemma4:e4b") == 1.0
    assert review_temperature("gpt-oss:20b") == 0.7


def test_the_temperature_tables_apply_to_ollama_only() -> None:
    """
    Given  an online provider asked about a name that is also a recommended Ollama tag
    When   the request profile and review temperature are asked for
    Then   neither table applies: every step gets 0.2, the review 0.7
    """
    from teachers_teammate.domain.model_catalog import review_temperature  # noqa: PLC0415

    assert request_profile("gemma4:e4b", "google").correct.temperature == 0.2
    assert request_profile("gemma4:e4b", "google").explain.temperature == 0.2
    assert review_temperature("gemma4:e4b", "google") == 0.7


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_best_model_skips_a_model_too_large_for_the_main_memory() -> None:
    """
    Given  a computer without a graphics card and 12 GB of main memory (4 GB usable)
    When   the BEST writing model is asked for, and again with 32 GB and with no figure
    Then   it is gemma4:e4b, which suitability() marks TOO_LARGE on the small computer and
           BEST with enough memory: the list offers no smaller writing model, because the one
           it used to offer (qwen3:4b) fails gate 1
    """
    assert best_model("general", HardwareProfile.CPU, system_memory_gb=12.0) == "gemma4:e4b"
    assert (
        suitability("gemma4:e4b", "general", HardwareProfile.CPU, system_memory_gb=12.0)
        is Suitability.TOO_LARGE
    )
    assert (
        suitability("gemma4:e4b", "general", HardwareProfile.CPU, system_memory_gb=32.0)
        is Suitability.BEST
    )
    assert best_model("general", HardwareProfile.CPU, system_memory_gb=32.0) == "gemma4:e4b"
    assert best_model("general", HardwareProfile.CPU) == "gemma4:e4b"


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


@pytest.mark.parametrize(
    ("needed_gb", "free_gb", "expected"),
    [
        (9.3, 212.0, DiskVerdict.ENOUGH),
        (9.3, 9.3 + DISK_MARGIN_GB, DiskVerdict.ENOUGH),
        (9.3, 9.3 + DISK_MARGIN_GB - 0.1, DiskVerdict.NOT_ENOUGH),
        (9.3, 5.1, DiskVerdict.NOT_ENOUGH),
        (9.3, None, DiskVerdict.UNKNOWN),
        (0.0, 5.1, DiskVerdict.UNKNOWN),
    ],
)
def test_disk_verdict_keeps_a_margin_and_admits_what_it_cannot_know(
    needed_gb: float, free_gb: float | None, expected: DiskVerdict
) -> None:
    """
    Given  downloads of a known size and the free space of the disk Ollama uses
    When   the verdict is asked for
    Then   it is enough only with the margin to spare, not enough below it, and unknown when
           the free space or the size is not known
    """
    assert disk_verdict(needed_gb, free_gb) is expected
