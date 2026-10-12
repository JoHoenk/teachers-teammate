"""Shared offline stand-in for the hardware / model-recommendation service API.

The GUI test files each define a small app-service stub for the calls their
widgets make.  The hardware-profile and model-rating methods are identical
everywhere, so they live here once: mix :class:`RecommendationServiceStub` into
a stub class instead of re-implementing them.
"""

from __future__ import annotations

from typing import cast

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


class RecommendationServiceStub:
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

    def __init__(self) -> None:
        self.detect_calls: list[tuple[str, bool]] = []
        self.changed_urls: list[str] = []
        self.gpu_calls: list[bool] = []  # the ``refresh`` flag of every detect_gpus call

    # hardware ───────────────────────────────────────────────────────────────
    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        self.detect_calls.append((ollama_url, refresh))
        return self.detection

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection:
        if setting in ("cpu", "8gb", "16gb"):
            return HardwareDetection(
                profile=cast(HardwareProfile, setting), reason=DetectionReason.MANUAL
            )
        return self.detect_hardware(ollama_url)

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
    ) -> list[ModelRating]:
        classification = self.classify_ollama_models(tags, ollama_url, cached_only=cached_only)
        return [
            rate_model(tag, task, profile, detected_uses=classification.get(tag)) for tag in tags
        ]

    def on_ollama_models_changed(self, ollama_url: str) -> None:
        self.changed_urls.append(ollama_url)
