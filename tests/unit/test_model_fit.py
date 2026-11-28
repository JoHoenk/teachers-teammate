"""Unit tests for the model fit and rating rules (``domain/model_fit.py``)."""

from __future__ import annotations

import pytest
from teachers_teammate.domain.performance_estimate import SpeedBand, SpeedSource

from teachers_teammate.domain.hardware_profile import (
    CARD_USABLE_SHARE,
    CPU_COMFORT_GB,
    FULL_SPEED_SHARE,
    HARDWARE_PROFILES,
    HardwareProfile,
    card_budget_gb,
    choose_profile,
    is_very_slow,
    ram_needed_gb,
)
from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    DEFAULT_WRITING_MODEL,
    ModelUses,
    OLLAMA_CATALOG,
    find_spec,
)
from teachers_teammate.domain.model_fit import (
    DISK_MARGIN_GB,
    DiskVerdict,
    RECOMMENDED,
    Suitability,
    best_model,
    catalog_uses,
    disk_verdict,
    models_for,
    rate_model,
    suitability,
    suitability_rank,
    uses_from_capabilities,
)


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
