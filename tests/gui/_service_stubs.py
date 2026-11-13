"""Shared offline stand-in for the hardware / model-recommendation service API.

The GUI test files each define a small app-service stub for the calls their
widgets make.  The hardware-profile and model-rating methods are identical
everywhere, so they live here once: mix :class:`RecommendationServiceStub` into
a stub class instead of re-implementing them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import tempfile
import threading
from typing import cast

from teachers_teammate._model_defaults import default_model_for_task
from teachers_teammate.application.context_choice import ContextChoice, context_choice
from teachers_teammate.application.data_flow import text_goes_online
from teachers_teammate.application.speed_estimates import SpeedChoices, SpeedSummary
from teachers_teammate.config import DEFAULTS, Config
from teachers_teammate.domain.data_location import (
    STAGE_ANONYMIZATION,
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    STAGE_OCR,
    DataFlow,
    DataFlowStage,
    DataLocation,
    StageLocation,
)
from teachers_teammate.domain.model_catalog import (
    OLLAMA_CATALOG,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    ModelCategory,
    ModelRating,
    ModelUses,
    catalog_uses,
    find_spec,
    rate_model,
)
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.domain.performance_estimate import TimeRange
from teachers_teammate.infrastructure.performance_probe import ProbeResult


def context_choice_from(settings: Mapping[str, object], card_gb: float | None) -> ContextChoice:
    """Answer the context question with the real rule, as the application service does.

    The page must not promise a window the run does not use, so the stubs do not invent one.
    """
    return context_choice(
        proofreading_provider=str(
            settings.get("correction_provider") or DEFAULTS["correction_provider"]
        ),
        proofreading_model=str(settings.get("correction_model") or DEFAULTS["correction_model"]),
        review_provider=str(settings.get("evaluate_provider") or DEFAULTS["evaluate_provider"]),
        review_model=str(settings.get("evaluate_model") or DEFAULTS["evaluate_model"]),
        card_gb=card_gb,
    )


class LocalDataFlowStub:
    """Reports every step as running on this computer (the common, uneventful case)."""

    def describe_data_flow(self, config) -> DataFlow:
        _ = config
        here = StageLocation(DataLocation.THIS_COMPUTER, "Ollama")
        return DataFlow((DataFlowStage(STAGE_OCR, here, "page images"),))

    def describe_steps(self, config) -> DataFlow:
        """Every step as if it were ticked, all on this computer."""
        _ = config
        here = StageLocation(DataLocation.THIS_COMPUTER, "Ollama")
        return DataFlow(
            (
                DataFlowStage(STAGE_OCR, here, "page images"),
                DataFlowStage(STAGE_ANONYMIZATION, here, "text", detail="Off"),
                DataFlowStage(STAGE_CORRECTION, here, "text"),
                DataFlowStage(STAGE_EVALUATION, here, "text"),
            )
        )

    def describe_ocr_location(self, engine: str, model: str, ollama_url: str) -> StageLocation:
        _ = model, ollama_url
        return StageLocation(DataLocation.THIS_COMPUTER, engine)

    def describe_ocr_refusal(self, engine: str, model: str, ollama_url: str) -> str:
        _ = engine, model, ollama_url
        return ""

    def describe_llm_location(self, provider: str, model: str, ollama_url: str) -> StageLocation:
        _ = model, ollama_url
        return StageLocation(DataLocation.THIS_COMPUTER, provider)

    #: What ``anonymizer_available`` answers (name detection can run).
    anonymizer_ready: bool = True

    def default_model_for_task(self, provider: str, task: str) -> str:
        """Answer like the real service: the task's default model of the provider."""
        return default_model_for_task(provider, task)

    def effective_model(self, provider: str, model: str, task: str) -> str:
        """Answer like the real service: an empty model means the task's default."""
        return model.strip() or self.default_model_for_task(provider, task)

    def sends_text_online(self, settings: Mapping[str, object]) -> bool:
        """Answer with the real rule, over this stub's own location and default models."""
        return text_goes_online(settings, self.describe_llm_location, self.effective_model)

    def anonymizer_available(self, language: str) -> bool:
        _ = language
        return self.anonymizer_ready

    def get_provider_info(self, provider: str) -> dict:
        """Provider metadata; the fakes that are asked about providers override this."""
        _ = provider
        return {}

    def provider_key_missing(self, provider: str) -> bool:
        """Mirror the real service: a key is missing when the provider needs one and none is set."""
        info = self.get_provider_info(provider)
        return bool(info.get("needs_api_key")) and not os.environ.get(str(info.get("env_key", "")))

    # ── speed test and time estimates (the panel on the AI models page) ───────
    def speed_choices(self, settings: Mapping[str, object]) -> SpeedChoices:
        """Answer with the real rule: the models a run would use for these settings."""
        return SpeedChoices.from_settings(settings, effective_model=self.effective_model)

    def speed_choices_for_run(self, config: Config) -> SpeedChoices:
        """Answer with the real rule: the models of the steps the run switches on."""
        return SpeedChoices.from_config(config, effective_model=self.effective_model)

    def speed_summary(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cached_only: bool = True,
    ) -> SpeedSummary:
        """Answer with nothing measured: a stub has no store to read."""
        _ = choices, hardware, cached_only
        return SpeedSummary()

    def refresh_speed_server_facts(self, ollama_url: str) -> None:
        """Nothing to fetch: a stub has no server."""
        _ = ollama_url

    def speed_page_times(
        self,
        tags: Sequence[str],
        category: ModelCategory,
        ollama_url: str,
        *,
        hardware: HardwareDetection | None = None,
    ) -> dict[str, TimeRange]:
        """Answer with nothing measured: a stub has no store to read."""
        _ = tags, category, ollama_url, hardware
        return {}

    def test_speed(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ProbeResult:
        """Refuse: a stub has no server to measure."""
        _ = choices, hardware, cancel_event
        raise RuntimeError("a stub has no server to measure")


class RecommendationServiceStub(LocalDataFlowStub):
    """Deterministic ``detect_hardware`` / classification / rating methods.

    ``detection`` is what automatic detection returns (an 8 GB graphics card by
    default); catalogued models classify from the catalog, others as unknown.
    """

    detection: HardwareDetection = HardwareDetection(
        profile="8gb",
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="Test GPU",
        memory_gb=8.0,
    )

    #: Memory of "this computer" reported with a chosen profile (``None`` = unknown).
    system_memory_gb: float | None = None

    #: How much slower this computer is than the reference machine (``None`` = not measured).
    machine_factor: float | None = None

    def __init__(self) -> None:
        self.detect_calls: list[tuple[str, bool]] = []
        self.changed_urls: list[str] = []
        self.gpu_calls: list[bool] = []  # the ``refresh`` flag of every detect_gpus call
        self.injected_packages_dir = False  # inject_addon_packages_dir was called

    # providers ──────────────────────────────────────────────────────────────
    # Defaults for the model-list and package calls the AI-service dialogs make; a stub that
    # has models to offer overrides ``list_provider_models`` / ``get_cached_models``.
    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        _ = provider, base_url
        return []

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        _ = provider, base_url
        return None

    def list_provider_models_checked(self, provider: str, *, base_url: str = "") -> ModelListing:
        return ModelListing(list(self.list_provider_models(provider, base_url=base_url)))

    def get_cached_listing(self, provider: str, *, base_url: str = "") -> ModelListing | None:
        cached = self.get_cached_models(provider, base_url=base_url)  # pylint: disable=assignment-from-none  # subclasses return lists
        return ModelListing(list(cached)) if cached is not None else None

    def missing_provider_package(self, provider: str) -> str | None:
        _ = provider
        return None

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        return {}

    # hardware ───────────────────────────────────────────────────────────────
    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        self.detect_calls.append((ollama_url, refresh))
        return self.detection

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection:
        if setting in ("cpu", "8gb", "16gb"):
            return HardwareDetection(
                profile=cast(HardwareProfile, setting),
                reason=DetectionReason.MANUAL,
                system_memory_gb=self.system_memory_gb,
            )
        return self.detect_hardware(ollama_url)

    def cached_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection | None:
        """Answer as the real service does once it has probed: this stub never probes."""
        return self.resolve_hardware_profile(setting, ollama_url)

    def detect_gpus(self, *, refresh: bool = False) -> list:
        self.gpu_calls.append(refresh)
        return []

    # catalog / classification / ratings ──────────────────────────────────────
    def ollama_catalog(self) -> tuple:
        return OLLAMA_CATALOG

    def classify_ollama_models(
        self, tags: list[str], ollama_url: str, *, cached_only: bool = False
    ) -> dict[str, ModelUses | None]:
        _ = ollama_url, cached_only
        result: dict[str, ModelUses | None] = {}
        for tag in tags:
            spec = find_spec(tag)
            result[tag] = catalog_uses(spec) if spec is not None else None
        return result

    def rate_ollama_models(
        self,
        tags: list[str],
        task: ModelCategory,
        profile: HardwareProfile,
        ollama_url: str,
        *,
        cached_only: bool = False,
        system_memory_gb: float | None = None,
        card_gb: float | None = None,
        machine_factor: float | None = None,
    ) -> list[ModelRating]:
        classification = self.classify_ollama_models(tags, ollama_url, cached_only=cached_only)
        factor = self.machine_factor if machine_factor is None else machine_factor
        return [
            rate_model(
                tag,
                task,
                profile,
                detected_uses=classification.get(tag),
                system_memory_gb=system_memory_gb,
                card_gb=card_gb,
                machine_factor=factor,
            )
            for tag in tags
        ]

    def on_ollama_models_changed(self, ollama_url: str) -> None:
        self.changed_urls.append(ollama_url)

    # ── add-on packages (install rounds) ─────────────────────────────────────
    def addon_packages_dir(self) -> Path:
        return Path(tempfile.gettempdir()) / "teachers-teammate-addons"

    def inject_addon_packages_dir(self) -> None:
        self.injected_packages_dir = True
