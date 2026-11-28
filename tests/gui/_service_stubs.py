"""Shared offline stand-in for the hardware / model-recommendation service API.

The GUI test files each define a small app-service stub for the calls their
widgets make.  The hardware-profile and model-rating methods are identical
everywhere, so they live here once: mix :class:`RecommendationServiceStub` into
a stub class instead of re-implementing them.

:class:`FinishedThreadStub` is the same idea for a fake install thread: the few ``QThread``
methods the app calls on a thread it has started.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import dataclasses
from pathlib import Path
from teachers_teammate.domain.anonymization import AnonymizerConfig
from teachers_teammate.domain.stages import Stage
import tempfile
import threading
from typing import TYPE_CHECKING

from teachers_teammate._model_defaults import default_model_for_task
from teachers_teammate.application.api_keys import ApiKeys
from teachers_teammate.domain.credentials import KeyLocation
from teachers_teammate.infrastructure.credential_store import CredentialStore
from teachers_teammate.application.composition import AppServices
from teachers_teammate.application.context_choice import ContextChoice, context_choice
from teachers_teammate.application.data_flow import text_goes_online
from teachers_teammate.application.speed_estimates import SpeedChoices, SpeedSummary
from teachers_teammate.config import Config
from teachers_teammate.settings_schema import DEFAULTS
from teachers_teammate.domain.data_location import (
    STAGE_ANONYMIZATION,
    DataFlow,
    DataFlowStage,
    DataLocation,
    StageLocation,
)
from teachers_teammate.domain.hardware_profile import (
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    concrete_profile,
)
from teachers_teammate.domain.model_catalog import (
    OLLAMA_CATALOG,
    ModelCategory,
    ModelUses,
    find_spec,
)
from teachers_teammate.domain.model_fit import ModelRating, catalog_uses, rate_model
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.domain.ollama_snapshot import ServerSnapshot
from teachers_teammate.domain.performance_estimate import ProbeResult, TimeRange
from teachers_teammate.domain.providers import ProviderInfo, display_name_of

from tests.conftest import make_api_keys

if TYPE_CHECKING:
    # The panel pulls Qt in, which the domain- and CLI-level tests' deps do not carry.
    from teachers_teammate.gui._config_panel import ConfigPanel


def services_of(stub: object) -> AppServices:
    """Hand one flat stub to a widget as the grouped services it asks for.

    The stubs answer for every service, so each group points at the same object: a test keeps
    overriding the one method it cares about, and a new service needs no change here.
    """
    return AppServices(
        **{field.name: stub for field in dataclasses.fields(AppServices)}  # ty: ignore[invalid-argument-type]
    )


def make_config_panel(service: object | None = None) -> ConfigPanel:
    """Return a config panel whose data flow and requirement checks ask *service*.

    Without one the panel asks :class:`LocalDataFlowStub`: every step runs on this computer and
    every requirement is met, so a test that cares about neither names neither.
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    return ConfigPanel(services_of(service if service is not None else LocalDataFlowStub()))


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
    """Reports every step as running on this computer (the common, uneventful case).

    Also answers for the built-in reader's model files, which every GUI stub shares.
    """

    #: Whether the built-in reader's model files "are downloaded" (a test sets it per stub).
    reader_present: bool = True
    reader_progress: tuple[int, int] = (0, 0)
    reader_downloads: int = 0

    def reader_installed(self) -> bool:
        """Answer whether the reader's model files are downloaded."""
        return self.reader_present

    #: Whether the page models of the automatic preparation "are downloaded".
    page_models_present: bool = True
    page_models_downloads: int = 0

    def page_models_installed(self) -> bool:
        """Answer whether the page models are downloaded."""
        return self.page_models_present

    def download_page_models(self, on_progress=None, cancel=None) -> None:
        """Pretend to download: report once, then the files are there."""
        self.page_models_downloads += 1
        if on_progress:
            on_progress(1_000_000, 2_000_000)
            on_progress(2_000_000, 2_000_000)
        self.page_models_present = True

    def models_dir(self) -> Path:
        """Return a folder name for the dialogs to show."""
        return Path(tempfile.gettempdir()) / "teachers-teammate-models"

    def download_reader(self, on_progress=None, cancel=None) -> None:
        """Pretend to download: report once, then the files are there."""
        self.reader_downloads += 1
        if on_progress:
            on_progress(1_000_000, 2_000_000)
            on_progress(2_000_000, 2_000_000)
        self.reader_present = True

    def is_local(self, url: str, *, include_wsl_host: bool = False) -> bool:
        """Answer as the real check does for the addresses the pages use."""
        _ = include_wsl_host
        return url.startswith("http://127.0.0.1") or url.startswith("http://localhost")

    def normalize(self, url: str) -> str:
        """Strip a trailing slash, as the real normaliser does."""
        return url.rstrip("/")

    def addon_license(self, package: str) -> str | None:
        """No license list in a test: the tooltip stays empty."""
        _ = package
        return None

    def system_language(self) -> str | None:
        """No system locale in a test."""
        return None

    def read_gpu_usage(self):
        """No graphics card in a test."""
        return None

    def read_system_usage(self):
        """No live system stats in a test."""
        return None

    def describe_data_flow(self, config) -> DataFlow:
        _ = config
        here = StageLocation(DataLocation.THIS_COMPUTER, "Ollama")
        return DataFlow((DataFlowStage(Stage.OCR, here, "page images"),))

    def describe_steps(self, config) -> DataFlow:
        """Every step as if it were ticked, all on this computer."""
        _ = config
        here = StageLocation(DataLocation.THIS_COMPUTER, "Ollama")
        return DataFlow(
            (
                DataFlowStage(Stage.OCR, here, "page images"),
                DataFlowStage(STAGE_ANONYMIZATION, here, "text", detail="Off"),
                DataFlowStage(Stage.CORRECTION, here, "text"),
                DataFlowStage(Stage.EVALUATION, here, "text"),
            )
        )

    def check_stage_requirements(self, config, stage: str) -> list:
        """Every requirement of *stage* is met in a test unless a stub says otherwise."""
        _ = config, stage
        return []

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

    def anonymizer_available(self, language: str, config: AnonymizerConfig | None = None) -> bool:
        _ = (language, config)
        return self.anonymizer_ready

    def list_providers(self) -> list[str]:
        """Name no providers; the fakes that show providers override this."""
        return []

    def get_provider_info(self, provider: str) -> ProviderInfo:
        """Provider metadata; the fakes that are asked about providers override this."""
        return ProviderInfo(name=provider, display_name=provider)

    def display_name(self, provider: str) -> str:
        """Mirror the real service: the name a teacher reads, or the provider id."""
        return display_name_of(provider, self.get_provider_info(provider))

    def env_key(self, provider: str) -> str:
        """Mirror the real service: the variable the provider reads its key from."""
        info = self.get_provider_info(provider)
        return info.env_key if info is not None else ""

    def provider_needs_key(self, provider: str) -> bool:
        """Mirror the real service: whether the provider cannot answer without a key."""
        info = self.get_provider_info(provider)
        return info is not None and info.needs_api_key

    def key_page(self, env_key: str) -> str:
        """Mirror the real service: the first provider's key page for *env_key*."""
        for provider in self.list_providers():
            info = self.get_provider_info(provider)
            if info is not None and info.env_key == env_key and info.key_url:
                return info.key_url
        return ""

    def package_services(self, pip: str) -> list[str]:
        """Mirror the real service: the names of the providers that declare the package *pip*."""
        return [
            self.display_name(provider)
            for provider in self.list_providers()
            if (info := self.get_provider_info(provider)) is not None
            and info.package is not None
            and info.package.pip == pip
        ]

    def package_is_connector(self, provider: str) -> bool:
        """Mirror the real service: whether the provider's package is an optional connector."""
        info = self.get_provider_info(provider)
        return info is not None and info.package is not None and info.package_optional

    def provider_key_missing(self, provider: str) -> bool:
        """Mirror the real service: a key is missing when the provider needs one and none is set."""
        if not self.provider_needs_key(provider):
            return False
        return not self.key(self.env_key(provider))

    # api keys ────────────────────────────────────────────────────────────────
    # The app's keys, which the Settings window edits and the providers are given.  A test
    # that needs a key stored sets it through ``key``/``remember``, as the window does.
    api_keys: ApiKeys | None = None

    def key(self, env_key: str) -> str:
        """Mirror the app: the key it holds for *env_key*, else the environment's."""
        return self._api_keys().key(env_key)

    def remember(self, env_key: str, value: str | None) -> None:
        """Mirror the app: hold *value* for *env_key*, or hold nothing when it is ``None``."""
        self._api_keys().remember(env_key, value)

    def held(self, env_key: str) -> str | None:
        """Mirror the app: what it holds for *env_key* (``None`` = nothing of its own)."""
        return self._api_keys().held(env_key)

    def set_outside_app(self, env_key: str) -> bool:
        """Mirror the app: whether the key was exported before the app started."""
        return self._api_keys().set_outside_app(env_key)

    def save(self, env_key: str, value: str) -> KeyLocation:
        """Mirror the app: keep *value* for *env_key* in the store, wherever that puts it."""
        return self._api_keys().save(env_key, value)

    def forget(self, env_key: str) -> None:
        """Mirror the app: forget the key for *env_key* wherever it is kept."""
        self._api_keys().forget(env_key)

    def location(self) -> KeyLocation:
        """Mirror the app: where a key saved now would be kept."""
        return self._api_keys().location()

    def load_stored(self, env_keys: Iterable[str]) -> None:
        """Mirror the app: hand the stored keys to what the app holds."""
        self._api_keys().load_stored(env_keys)

    def _api_keys(self) -> ApiKeys:
        if self.api_keys is None:
            self.api_keys = make_api_keys(CredentialStore())
        return self.api_keys

    # ── speed test and time estimates (the panel on the AI models page) ───────
    def choices(self, settings: Mapping[str, object]) -> SpeedChoices:
        """Answer with the real rule: the models a run would use for these settings."""
        return SpeedChoices.from_settings(settings, effective_model=self.effective_model)

    def choices_for_config(self, config: Config) -> SpeedChoices:
        """Answer with the real rule: the models of the steps the run switches on."""
        return SpeedChoices.from_config(config, effective_model=self.effective_model)

    def speed_summary(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
    ) -> SpeedSummary:
        """Answer with nothing measured: a stub has no store to read."""
        _ = choices, hardware
        return SpeedSummary()

    def snapshot(self, url: str, *, max_age: float = 0.0) -> ServerSnapshot:
        """Answer with an empty server: a stub has none to ask."""
        _ = url, max_age
        return ServerSnapshot()

    def page_times(
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
        on_progress=None,
    ) -> ProbeResult:
        """Refuse: a stub has no server to measure."""
        _ = choices, hardware, cancel_event, on_progress
        raise RuntimeError("a stub has no server to measure")


class RecommendationServiceStub(LocalDataFlowStub):
    """Deterministic ``detect_hardware`` / classification / rating methods.

    ``detection`` is what automatic detection returns (an 8 GB graphics card by
    default); catalogued models classify from the catalog, others as unknown.
    """

    detection: HardwareDetection = HardwareDetection(
        profile=HardwareProfile.CARD_8GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="Test GPU",
        memory_gb=8.0,
    )

    #: Memory of "this computer" reported with a chosen profile (``None`` = unknown).
    system_memory_gb: float | None = None

    #: How much slower this computer is than the reference machine (``None`` = not measured).
    machine_factor: float | None = None

    #: Main memory free right now, as the "My computer" line reads it (``None`` = unreadable).
    free_memory_gb: float | None = None

    def __init__(self) -> None:
        self.detect_calls: list[tuple[str, bool]] = []
        self.injected_packages_dir = False  # inject_addon_packages_dir was called
        self.outdated_addons = False  # an app update left add-ons that wait for an update
        self.addon_updates = 0  # how often update_addons ran

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
        cached = self.get_cached_models(provider, base_url=base_url)
        return ModelListing(list(cached)) if cached is not None else None

    def missing_provider_package(self, provider: str) -> str | None:
        _ = provider
        return None

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        return {}

    def addons_outdated(self) -> bool:
        return self.outdated_addons

    def update_addons(self, progress_cb) -> None:
        self.addon_updates += 1
        progress_cb("installing the add-ons again")
        self.outdated_addons = False

    # hardware ───────────────────────────────────────────────────────────────
    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        self.detect_calls.append((ollama_url, refresh))
        return self.detection

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection:
        profile = concrete_profile(setting)
        if profile is not None:
            return HardwareDetection(
                profile=profile,
                reason=DetectionReason.MANUAL,
                system_memory_gb=self.system_memory_gb,
            )
        return self.detect_hardware(ollama_url)

    def cached_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection | None:
        """Answer as the real service does once it has probed: this stub never probes."""
        return self.resolve_hardware_profile(setting, ollama_url)

    def available_memory_gb(self) -> float | None:
        """Answer with the free memory a test set, or ``None``."""
        return self.free_memory_gb

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

    def rate_for(self, tags, task, detection, setting, ollama_url, *, cached_only: bool = False):
        from teachers_teammate.domain.hardware_profile import active_profile  # noqa: PLC0415

        return self.rate_ollama_models(
            tags,
            task,
            active_profile(detection, setting),
            ollama_url,
            cached_only=cached_only,
            system_memory_gb=detection.system_memory_gb if detection is not None else None,
            card_gb=detection.card_gb if detection is not None else None,
        )

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

    # ── add-on packages (install rounds) ─────────────────────────────────────
    def addon_packages_dir(self) -> Path:
        return Path(tempfile.gettempdir()) / "teachers-teammate-addons"

    def inject_addon_packages_dir(self) -> None:
        self.injected_packages_dir = True


class FinishedThreadStub:
    """The ``QThread`` methods the app calls on a thread it started, for a fake that is done.

    The app holds every :class:`~teachers_teammate.gui._long_job.LongJobThread` it starts until
    it ends and asks it whether it is finished, so a fake ``QObject`` that does its work inside
    ``start()`` needs these three.  Mix it in before ``QObject``; a fake that must look like a
    thread that is still running overrides them.
    """

    def isRunning(self) -> bool:  # Qt's spelling
        """Return ``False``: the fake's work was done inside ``start()``."""
        return False

    def isFinished(self) -> bool:  # Qt's spelling
        """Return ``True``, so the thread is dropped from the parked ones at the next start."""
        return True

    def wait(self, _timeout: int = 0) -> bool:  # Qt's spelling
        """Return ``True`` at once: there is nothing to wait for."""
        return True
