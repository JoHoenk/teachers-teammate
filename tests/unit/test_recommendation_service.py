"""What the hardware probe, the model classification and the ratings answer.

The service owns three caches (the process-lifetime probe, the classifications per server, the
server facts behind the speed estimates are elsewhere) — these tests pin what it tells the GUI:
which profile this computer gets, which models fit on it, and what changes when a pull finished.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import cast

import pytest

from teachers_teammate.application.model_recommendations import ModelRecommendationService
from teachers_teammate.infrastructure.gpu_detector import SystemHardwareProbe
from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain.model_catalog import (
    DetectionReason,
    GpuInfo,
    HardwareDetection,
    HardwareProfile,
    ModelCategory,
    ModelUses,
    best_model,
)

_URL = "http://127.0.0.1:11434"
_REMOTE_URL = "http://192.168.1.50:11434"


def _effective_model(provider: str, model: str, task: str) -> str:
    """Resolve a model the way the provider service does: a named one, else the default."""
    _ = provider
    key = "correction_model" if task == "correction" else "evaluate_model"
    return model or str(DEFAULTS[key])


def _service(
    *,
    forget_models: list[str] | None = None,
    forget_facts: list[str] | None = None,
    machine_factor: float | None = None,
) -> ModelRecommendationService:
    """Return the service, wired as the app wires it, with the calls it makes recorded."""
    return ModelRecommendationService(
        effective_model=_effective_model,
        invalidate_model_list=(forget_models.append if forget_models is not None else None),
        invalidate_speed_facts=(forget_facts.append if forget_facts is not None else None),
        machine_factor=(
            (lambda _url, _category: machine_factor) if machine_factor is not None else None
        ),
        hardware=SystemHardwareProbe(),
    )


# ── this computer ───────────────────────────────────────────────────────────


@pytest.mark.use_case("Hardware_Profile_Override")
def test_resolve_hardware_profile_manual_setting_skips_detection(monkeypatch) -> None:
    """
    Given  a concrete hardware_profile setting ("16gb")
    When   resolve_hardware_profile is called
    Then   a MANUAL detection is returned and no detection runs
    """
    service = _service()
    calls: list[str] = []
    monkeypatch.setattr(service, "detect_hardware", lambda url, **_kw: calls.append(url))
    detection = service.resolve_hardware_profile("16gb", _URL)
    assert detection.profile == "16gb"
    assert detection.reason is DetectionReason.MANUAL
    assert calls == []


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_hardware_probes_this_computer_once(monkeypatch) -> None:
    """
    Given  a service with patched GPU detection
    When   detect_hardware is called repeatedly, also with differently written URLs
    Then   the detector runs once (process-lifetime probe); refresh re-runs it
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    calls: list[str] = []

    def fake_detect_gpus():
        calls.append("gpu")
        return [GpuInfo("nvidia", "RTX 4060", 8188)]

    monkeypatch.setattr(gpu_detector, "detect_gpus", fake_detect_gpus)
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)

    service = _service()
    first = service.detect_hardware(_URL)
    second = service.detect_hardware(_URL)
    # Editing the URL (trailing slash, host spelling) must not probe again.
    third = service.detect_hardware("http://localhost:11434/")
    assert first.profile == "8gb"
    assert first.reason is DetectionReason.GPU_DETECTED
    assert second == first
    assert third == first
    assert calls == ["gpu"]

    refreshed = service.detect_hardware(_URL, refresh=True)
    assert isinstance(refreshed, HardwareDetection)
    assert calls == ["gpu", "gpu"]


@pytest.mark.use_case("Hardware_Profile_Override")
def test_detect_hardware_remote_url_skips_local_gpus(monkeypatch) -> None:
    """
    Given  an Ollama URL pointing at another computer
    When   detect_hardware is called
    Then   the REMOTE profile is returned without consulting local GPUs
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    def exploding_detect_gpus():
        raise AssertionError("local GPU detection must not run for remote URLs")

    monkeypatch.setattr(gpu_detector, "detect_gpus", exploding_detect_gpus)
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", exploding_detect_gpus)

    detection = _service().detect_hardware(_REMOTE_URL)
    assert detection.profile == "8gb"
    assert detection.reason is DetectionReason.REMOTE_SERVER
    assert detection.host == "192.168.1.50"
    assert detection.gpu_name is None


@pytest.mark.use_case("Hardware_Profile_Override")
def test_resolve_hardware_profile_unknown_setting_treated_as_auto(monkeypatch) -> None:
    """
    Given  an invalid hardware_profile setting value
    When   resolve_hardware_profile is called
    Then   the value is treated as "auto" and detection runs
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(gpu_detector, "detect_gpus", lambda: [GpuInfo("nvidia", "RTX 4060", 8188)])
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)

    detection = _service().resolve_hardware_profile("bogus", _URL)
    assert detection.profile == "8gb"
    assert detection.reason is DetectionReason.GPU_DETECTED


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_hardware_reports_the_computers_memory_from_the_probe(monkeypatch) -> None:
    """
    Given  a computer with a 16 GB card and 32 GB of memory
    When   detect_hardware is called for the local server
    Then   the detection carries both, and a model too big for the card is rated slow with it
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(gpu_detector, "detect_gpus", lambda: [GpuInfo("nvidia", "RTX 3080", 16384)])
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    monkeypatch.setattr(gpu_detector, "detect_system_memory_gb", lambda: 32.0)
    service = _service()

    detection = service.detect_hardware(_URL)

    assert (detection.profile, detection.memory_gb, detection.system_memory_gb) == (
        "16gb",
        16.0,
        32.0,
    )
    rating = service.rate_ollama_models(
        ["gpt-oss:20b"], "general", "8gb", _URL, system_memory_gb=32.0
    )[0]
    assert rating.suitability.value == "slow"


@pytest.mark.unit
def test_a_chosen_profile_carries_the_memory_only_for_a_local_server(monkeypatch) -> None:
    """
    Given  a chosen profile ("8gb") and a computer with 32 GB of memory
    When   it is resolved for the local server and for a server on another computer
    Then   the local one reports the memory without probing the graphics card; the remote one
           reports none (that memory belongs to another computer)
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    def no_graphics_probe(*_a, **_k):
        raise AssertionError("a chosen profile must not probe the graphics card")

    monkeypatch.setattr(gpu_detector, "detect_gpus", no_graphics_probe)
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    monkeypatch.setattr(gpu_detector, "detect_system_memory_gb", lambda: 32.0)
    service = _service()

    local = service.resolve_hardware_profile("8gb", _URL)
    remote = service.resolve_hardware_profile("8gb", _REMOTE_URL)

    assert local.system_memory_gb == 32.0
    assert remote.system_memory_gb is None


@pytest.mark.unit
def test_a_chosen_profile_on_apple_silicon_carries_no_separate_memory(monkeypatch) -> None:
    """
    Given  an Apple Silicon computer (its graphics share is part of the shared memory)
    When   a profile is chosen by hand for the local server
    Then   no separate memory figure is reported, so a model is not offloaded into memory
           the profile already counts
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: 16.0)
    monkeypatch.setattr(gpu_detector, "detect_system_memory_gb", lambda: 16.0)

    detection = _service().resolve_hardware_profile("8gb", _URL)

    assert detection.system_memory_gb is None


@pytest.mark.unit
def test_hardware_detection_is_logged(monkeypatch, caplog) -> None:
    """
    Given  a probe of this computer
    When   detect_hardware is called
    Then   the probe and the resulting profile are logged, so a wrong suggestion can be traced
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(gpu_detector, "detect_gpus", lambda: [GpuInfo("nvidia", "RTX 3080", None)])
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    monkeypatch.setattr(gpu_detector, "detect_system_memory_gb", lambda: 16.0)

    with caplog.at_level(logging.INFO):
        _service().detect_hardware(_URL)

    text = caplog.text
    assert "Probed this computer" in text and "RTX 3080" in text
    assert "Hardware profile" in text and "gpu_memory_unknown" in text


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_gpus_is_served_from_the_shared_probe(monkeypatch) -> None:
    """
    Given  hardware detection that already probed this computer
    When   detected_gpus() is called afterwards (the "Detect GPU" button)
    Then   the machine is not probed a second time
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    calls: list[str] = []

    def fake_detect_gpus():
        calls.append("gpu")
        return [GpuInfo("nvidia", "RTX 4060", 8188)]

    monkeypatch.setattr(gpu_detector, "detect_gpus", fake_detect_gpus)
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    service = _service()
    service.detect_hardware(_URL)
    gpus = service.detected_gpus()
    assert [g.name for g in gpus] == ["RTX 4060"]
    assert calls == ["gpu"]


@pytest.mark.unit
def test_the_free_memory_and_the_gpu_load_are_read_on_demand(monkeypatch) -> None:
    """
    Given  a computer whose free memory and graphics load the probe can read
    When   the "My computer" line and the System Monitor ask for them
    Then   the two figures are read fresh, never taken from the detection cache
    """
    from teachers_teammate.domain.model_catalog import GpuUsage  # noqa: PLC0415
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(gpu_detector, "detect_available_memory_gb", lambda: 6.5)
    monkeypatch.setattr(gpu_detector, "read_gpu_usage", lambda: GpuUsage(37))
    service = _service()

    assert service.available_memory_gb() == 6.5
    assert service.read_gpu_usage() == GpuUsage(37)


# ── classification and ratings ──────────────────────────────────────────────


@pytest.mark.use_case("Model_Category_Guidance")
def test_classify_ollama_models_uses_catalog_and_caches_capabilities(monkeypatch) -> None:
    """
    Given  a mix of catalogued and custom model tags
    When   classify_ollama_models is called twice
    Then   catalogued tags resolve from the catalog, unknown tags via
           model_capabilities, and the result is cached (no second server call)
    """
    service = _service()
    caps_calls: list[str] = []

    class _FakeClient:
        def __init__(self, url: str, *, timeout: float = 0.0) -> None:
            _ = url, timeout

        def model_capabilities(self, tag: str):
            caps_calls.append(tag)
            return frozenset({"vision"}) if "vl" in tag else frozenset({"completion"})

    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(ollama_utils, "OllamaClient", _FakeClient)

    tags = ["qwen3-vl:8b", "my-custom-vl:latest", "my-custom-text:latest"]
    result = service.classify_ollama_models(tags, _URL)
    assert result["qwen3-vl:8b"] == frozenset({"ocr"})
    # A model that can read images may be used for reading AND writing.
    assert result["my-custom-vl:latest"] == frozenset({"ocr", "general"})
    assert result["my-custom-text:latest"] == frozenset({"general"})
    assert sorted(caps_calls) == ["my-custom-text:latest", "my-custom-vl:latest"]

    # Second call is fully cached — the server is not asked again.
    service.classify_ollama_models(tags, _URL)
    assert len(caps_calls) == 2

    # A pull clears the model list and the classification cache.
    service.on_ollama_models_changed(_URL)
    service.classify_ollama_models(tags, _URL)
    assert len(caps_calls) == 4

    # ... also when the URL is written differently (trailing slash).
    service.on_ollama_models_changed(f"{_URL}/")
    service.classify_ollama_models(tags, _URL)
    assert len(caps_calls) == 6


@pytest.mark.use_case("Model_Category_Guidance")
def test_unknown_classification_is_not_cached(monkeypatch) -> None:
    """
    Given  a custom model whose capabilities could not be read (None)
    When   it is classified twice
    Then   the server is asked both times — "unknown" is never cached, so a
           model that was not pulled yet is recognised as soon as it is
    """
    service = _service()
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    answers = iter([None, frozenset({"vision"})])
    asked: list[str] = []

    class _FakeClient:
        def __init__(self, url: str, *, timeout: float = 0.0) -> None:
            _ = url, timeout

        def model_capabilities(self, tag: str):
            asked.append(tag)
            return next(answers)

    monkeypatch.setattr(ollama_utils, "OllamaClient", _FakeClient)
    assert service.classify_ollama_models(["my-model:latest"], _URL) == {"my-model:latest": None}
    assert service.classify_ollama_models(["my-model:latest"], _URL) == {
        "my-model:latest": frozenset({"ocr", "general"})
    }
    assert asked == ["my-model:latest", "my-model:latest"]


@pytest.mark.use_case("Model_Category_Guidance")
def test_unreachable_server_is_asked_once_not_per_model(monkeypatch) -> None:
    """
    Given  several custom models and a server that cannot be reached
    When   they are classified
    Then   the server is contacted only for the first model (one timeout, not one per
           model), every model maps to None, and a later call asks again
    """
    from teachers_teammate.exceptions import OllamaConnectionError  # noqa: PLC0415
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    asked: list[str] = []
    timeouts: list[float] = []

    class _DownClient:
        def __init__(self, url: str, *, timeout: float = 0.0) -> None:
            _ = url
            timeouts.append(timeout)

        def model_capabilities(self, tag: str):
            asked.append(tag)
            raise OllamaConnectionError("down")

    monkeypatch.setattr(ollama_utils, "OllamaClient", _DownClient)
    service = _service()
    tags = ["a:latest", "b:latest", "c:latest"]
    assert service.classify_ollama_models(tags, _URL) == dict.fromkeys(tags)
    assert asked == ["a:latest"]
    assert timeouts and all(0 < t <= 5 for t in timeouts)  # a short, bounded wait

    service.classify_ollama_models(tags, _URL)
    assert asked == ["a:latest", "a:latest"]  # nothing was cached


@pytest.mark.use_case("Model_Category_Guidance")
def test_cached_only_classification_never_contacts_the_server(monkeypatch) -> None:
    """
    Given  a custom model that is not classified yet
    When   classify_ollama_models is called with cached_only=True
    Then   the server is not contacted and the model maps to None
    """
    service = _service()

    def exploding_client(url, **_kw):
        raise AssertionError("cached_only must not contact the server")

    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(ollama_utils, "OllamaClient", exploding_client)
    result = service.classify_ollama_models(
        ["qwen3-vl:8b", "custom:latest"], "http://x:1", cached_only=True
    )
    assert result == {"qwen3-vl:8b": frozenset({"ocr"}), "custom:latest": None}


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_rate_ollama_models_rates_catalog_and_custom_models(monkeypatch) -> None:
    """
    Given  a catalogued model and a custom vision model on the server
    When   rate_ollama_models is called for the reading task on 8 GB
    Then   the catalog model is BEST with its memory/note and the custom one is
           a WORKS model for reading (the single rating path of the widgets)
    """
    from teachers_teammate.domain.model_catalog import Suitability  # noqa: PLC0415
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    class _FakeClient:
        def __init__(self, url: str, *, timeout: float = 0.0) -> None:
            _ = url, timeout

        def model_capabilities(self, tag: str):
            _ = tag
            return frozenset({"vision"})

    monkeypatch.setattr(ollama_utils, "OllamaClient", _FakeClient)
    service = _service()
    ratings = service.rate_ollama_models(["qwen3-vl:8b", "my-vision:latest"], "ocr", "8gb", _URL)
    best, custom = ratings
    assert best.suitability is Suitability.GOOD  # BEST on 8 GB is the default reading model
    assert best.memory_gb == 7.0
    assert best.note
    assert custom.suitability is Suitability.WORKS
    assert custom.model_uses == frozenset({"ocr", "general"})
    assert custom.memory_gb is None


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_rating_carries_how_much_slower_this_computer_is() -> None:
    """
    Given  two services, one of them on a computer that is half as fast as the reference machine
    When   the widgets rate a catalogued writing model
    Then   the fast one's rating carries no speed guess and the slow one's carries the band,
           so the widget never promises the reference machine's speed here
    """
    from teachers_teammate.domain.performance_estimate import SpeedBand, SpeedSource  # noqa: PLC0415

    unknown = _service().rate_ollama_models(["gemma4:e4b"], "general", "8gb", _URL)[0]
    measured = _service(machine_factor=0.5).rate_ollama_models(
        ["gemma4:e4b"], "general", "8gb", _URL
    )[0]

    assert unknown.speed is None  # nothing measured on this server: no guess
    assert measured.speed is SpeedBand.OK
    assert measured.speed_source is SpeedSource.EXPECTED


@pytest.mark.use_case("Model_Category_Guidance")
def test_on_ollama_models_changed_evicts_list_classification_and_server_facts() -> None:
    """
    Given  the caches a pull makes stale: the model list, the classifications, the server facts
    When   on_ollama_models_changed is called (a pull finished)
    Then   the owner of the list and of the stored measurements hear of it, so nothing is asked
           against the digest of the model that was replaced
    """
    forget_models: list[str] = []
    forget_facts: list[str] = []
    service = _service(forget_models=forget_models, forget_facts=forget_facts)

    service.on_ollama_models_changed(f"{_URL}/")

    # Both are told with the normalised URL, so the cache keys match.
    assert forget_models == [_URL]
    assert forget_facts == [_URL]


# ── first start ─────────────────────────────────────────────────────────────


@pytest.mark.use_case("Recommended_Models_On_First_Start")
@pytest.mark.parametrize(
    "profile",
    ["cpu", "8gb", "16gb"],
)
def test_first_run_model_settings_return_best_models(profile: str) -> None:
    """
    Given  a hardware profile
    When   first_run_model_settings is called
    Then   ocr_model is the BEST reading model and correction/evaluate are the
           BEST writing model for that profile
    """
    settings = _service().first_run_model_settings(cast(HardwareProfile, profile))
    assert settings["ocr_model"] == best_model("ocr", cast(HardwareProfile, profile))
    assert settings["correction_model"] == best_model("general", cast(HardwareProfile, profile))
    assert settings["evaluate_model"] == best_model("general", cast(HardwareProfile, profile))


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_first_run_model_settings_respect_the_main_memory() -> None:
    """
    Given  a computer without a graphics card and 12 GB of main memory
    When   first_run_model_settings is called with those figures
    Then   the writing steps get gemma4:e4b, the only writing model the app offers without a
           card: the smaller one it used to offer (qwen3:4b) fails gate 1
    """
    settings = _service().first_run_model_settings("cpu", system_memory_gb=12.0)
    assert settings["correction_model"] == "gemma4:e4b"
    assert settings["evaluate_model"] == "gemma4:e4b"


# ── the context choice ──────────────────────────────────────────────────────


def test_the_context_choice_resolves_the_models_a_run_would_use() -> None:
    """
    Given  settings that leave both models empty
    When   context_choice is asked what "auto" gives
    Then   the resolved models decide it (an empty field is the default model, not "none")
    """
    asked: list[tuple[str, str, str]] = []

    def effective_model(provider: str, model: str, task: str) -> str:
        asked.append((provider, model, task))
        return model or str(
            DEFAULTS[f"{'correction' if task == 'correction' else 'evaluate'}_model"]
        )

    service = ModelRecommendationService(
        effective_model=effective_model, hardware=SystemHardwareProbe()
    )
    settings: Mapping[str, object] = {
        "correction_provider": "ollama",
        "correction_model": "",
        "evaluate_provider": "ollama",
        "evaluate_model": "",
        "proofreading_context": "auto",
    }

    choice = service.context_choice(settings, card_gb=8.0)

    assert [(provider, task) for provider, _model, task in asked] == [
        ("ollama", "correction"),
        ("ollama", "evaluation"),
    ]
    assert choice.auto_proofreading > 0 and choice.auto_review > 0
