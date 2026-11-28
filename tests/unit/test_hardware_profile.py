"""Unit tests for the hardware profiles and their detection (``domain/hardware_profile.py``)."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.hardware_profile import (
    CARD_USABLE_SHARE,
    DetectionReason,
    HARDWARE_PROFILES,
    HardwareDetection,
    HardwareProfile,
    HardwareProfileSetting,
    PROFILE_SETTINGS,
    UNKNOWN_PROFILE,
    active_profile,
    card_budget_gb,
    choose_profile,
    concrete_profile,
    memory_for_models_gb,
    profile_setting,
    system_reserve_gb,
)
from teachers_teammate.domain.model_fit import Suitability, rate_model


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
def test_a_computer_without_a_card_runs_small_models_comfortably_and_others_very_slowly() -> None:
    """
    Given  a computer without a graphics card and 32 GB of main memory
    When   a 3.6 GB model and a 9.5 GB model are rated
    Then   the first works, the second is slow, very slowly (all of it runs on the processor)
    """
    small = rate_model("gemma4:e2b", "general", HardwareProfile.CPU, system_memory_gb=32.0)
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
