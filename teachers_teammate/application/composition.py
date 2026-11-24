"""The application's services, built once per process.

Each entry point (``gui/composition.py``, ``cli_composition.py``, ``frozen_modes.py``) calls
:func:`build_app_services` once and hands the result down; every adapter below it is given the
service it uses and never looks one up.  A second service would have its own anonymizer cache,
its own model listing cache and its own list of installed models, so a download in one place
would not be seen in another.

This is a composition module: it is the one place in ``application/`` that names the concrete
infrastructure classes, and the one place a service is constructed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import functools
from pathlib import Path
from typing import TYPE_CHECKING

from ..infrastructure import logging_setup, power
from ..infrastructure.addon_manager import AddonPackages
from ..infrastructure.file_discovery import FileDiscovery
from ..infrastructure.gpu_detector import SystemHardwareProbe
from ..infrastructure.key_facts import MachineKeyFacts
from ..infrastructure.ollama_utils import OllamaClient
from ..infrastructure.onnx.store import ModelStore
from ..infrastructure.pipeline import OCRPipeline
from ..infrastructure.spacy_models import SpacyModels
from ..infrastructure.speed_store import SampleStore
from ..infrastructure.stage_builder import LlmBuilderWithKeys, PipelineComponentFactory
from ..infrastructure.state_repository import StateRepository
from ..infrastructure.teacher_work_store import TeacherWorkStore
from ..interfaces import SleepBlocker
from .addons import AddonService
from .anonymization import AnonymizationService
from .api_keys import ApiKeys
from .commands import DocumentCommands
from .data_flow import DataFlowService
from .documents import DocumentQueries
from .export import ExportService
from .model_recommendations import ModelRecommendationService
from .ollama_server import OllamaServerService
from .providers import ProviderService
from .reader import ReaderService
from .run import RunParts, RunService, RunServices, state_store_for, windows_for
from .setup_plan import SetupService
from .speed_estimates import SpeedEstimateService
from .stored_results import StoredResults

if TYPE_CHECKING:
    from ..domain.key_facts import KeyFacts
    from ..infrastructure.diagnostics import Diagnostics


@dataclass(frozen=True)
class AppServices:
    """The application's services, one instance per process.

    Every service is built once here, so the caches that must not exist twice — the anonymizer,
    the model listings, the installed models — are shared by the whole process.  ``run`` is the
    run itself; the rest are the parts of it.
    """

    run: RunService
    """The run: its files, what it needs before it starts, and the summary it leaves."""
    ollama: OllamaServerService
    """The Ollama server: discovery, status, model sizes and pulls."""
    providers: ProviderService
    """The LLM providers: what each is, what it needs, and the model lists it offers."""
    reader: ReaderService
    """Text recognition: the engines, their models, and the reader's model files."""
    recommendations: ModelRecommendationService
    """Hardware detection, model classification, ratings and the context choice."""
    speeds: SpeedEstimateService
    """The speed test, the stored measurements and the estimates made from them."""
    anonymization: AnonymizationService
    """Personal-info removal: the spaCy models, the anonymizer cache and its texts."""
    addons: AddonService
    """The optional packages: what is installed, and the installs themselves."""
    data_flow: DataFlowService
    """Where each step of a run takes place, and what every step would use."""
    setup: SetupService
    """What the Setup Assistant asks the computer, and the settings file of a first start."""
    documents: DocumentQueries
    """What the store says about a document."""
    export: ExportService
    """The Word file, the text file and the text for the clipboard."""
    commands: DocumentCommands
    """The writes: the teacher's texts and decisions."""
    stored: StoredResults
    """The results kept for earlier runs: how much they take, and deleting them."""
    keys: ApiKeys
    """The API keys of the online AI services: what the app holds for each of them."""
    work_store: TeacherWorkStore
    model_store: ModelStore
    sleep_blocker: SleepBlocker
    key_facts: KeyFacts
    """What this computer adds to a cache key: its page models, its language, the exchange."""


@dataclass(frozen=True)
class _Parts:
    """What the services are built from: this process's one store, blocker, server and folders."""

    discovery: FileDiscovery
    pipeline_factory: Callable[..., OCRPipeline]
    state_repository_factory: Callable[[Path], StateRepository]
    component_factory: PipelineComponentFactory
    keys: ApiKeys
    work_store: TeacherWorkStore
    model_store: ModelStore
    sleep_blocker: SleepBlocker
    ollama: OllamaServerService
    hardware: SystemHardwareProbe
    addon_state: AddonPackages
    names: SpacyModels
    samples: SampleStore


def build_app_services(
    *,
    discovery: FileDiscovery | None = None,
    pipeline_factory: Callable[..., OCRPipeline] | None = None,
    state_repository_factory: Callable[[Path], StateRepository] | None = None,
    component_factory: PipelineComponentFactory | None = None,
    sleep_blocker: SleepBlocker | None = None,
    model_store: ModelStore | None = None,
    work_store: TeacherWorkStore | None = None,
) -> AppServices:
    """Build the application's services, sharing the parts that must exist once."""
    keys = ApiKeys()
    parts = _parts(
        keys=keys,
        discovery=discovery,
        pipeline_factory=pipeline_factory,
        state_repository_factory=state_repository_factory,
        component_factory=component_factory,
        sleep_blocker=sleep_blocker,
        model_store=model_store,
        work_store=work_store,
    )
    providers, reader = _reading(parts)
    speeds = _speed_estimates(parts, providers)
    anonymization = AnonymizationService(names=parts.names)
    addons = _addons(parts, providers, reader, anonymization)
    data_flow = _data_flow(providers, anonymization)
    recommendations = _recommendations(parts, providers, speeds)
    setup = _setup(parts, providers, reader, recommendations, anonymization, addons)
    key_facts = MachineKeyFacts(parts.model_store)
    documents, commands = _documents(parts, recommendations, data_flow, anonymization, key_facts)
    run = _run(parts, providers, reader, recommendations, anonymization, addons, documents)
    return AppServices(
        run=run,
        ollama=parts.ollama,
        providers=providers,
        keys=keys,
        reader=reader,
        recommendations=recommendations,
        speeds=speeds,
        anonymization=anonymization,
        addons=addons,
        data_flow=data_flow,
        setup=setup,
        documents=documents,
        export=ExportService(documents=documents, anonymization=anonymization),
        commands=commands,
        stored=StoredResults(activity=run, work_store=parts.work_store),
        work_store=parts.work_store,
        model_store=parts.model_store,
        sleep_blocker=parts.sleep_blocker,
        key_facts=key_facts,
    )


def _parts(
    *,
    keys: ApiKeys,
    discovery: FileDiscovery | None,
    pipeline_factory: Callable[..., OCRPipeline] | None,
    state_repository_factory: Callable[[Path], StateRepository] | None,
    component_factory: PipelineComponentFactory | None,
    sleep_blocker: SleepBlocker | None,
    model_store: ModelStore | None,
    work_store: TeacherWorkStore | None,
) -> _Parts:
    """Return what the services are built from, each part built here or handed in."""
    return _Parts(
        keys=keys,
        discovery=discovery or FileDiscovery(),
        pipeline_factory=pipeline_factory or OCRPipeline,
        state_repository_factory=state_repository_factory or StateRepository,
        component_factory=component_factory
        or PipelineComponentFactory(build_llm=LlmBuilderWithKeys(keys.key_for)),
        work_store=work_store or TeacherWorkStore(),
        model_store=model_store or ModelStore(),
        sleep_blocker=sleep_blocker or power.default_sleep_blocker(),
        ollama=OllamaServerService(),
        hardware=SystemHardwareProbe(),
        addon_state=AddonPackages(),
        names=SpacyModels(),
        samples=SampleStore(),
    )


def _reading(parts: _Parts) -> tuple[ProviderService, ReaderService]:
    """Return the providers and the text recognition: what reads a page, and with what."""
    providers = ProviderService(ollama=parts.ollama, credentials=parts.keys)
    reader = ReaderService(
        model_store=parts.model_store,
        component_factory=parts.component_factory,
        providers=providers,
    )
    return providers, reader


def _speed_estimates(parts: _Parts, providers: ProviderService) -> SpeedEstimateService:
    """Return the speed service, on the one sample store this process keeps."""
    # A run writes what it measured and the estimates read it; two stores would keep two locks
    # on one file.
    return SpeedEstimateService(
        effective_model=providers.effective_model,
        ollama=parts.ollama,
        client_factory=lambda url, cancel_event: OllamaClient(url, cancel_event=cancel_event),
        samples=parts.samples,
    )


def _addons(
    parts: _Parts,
    providers: ProviderService,
    reader: ReaderService,
    anonymization: AnonymizationService,
) -> AddonService:
    """Return the add-ons: what is installed, and the installs themselves."""
    return AddonService(
        model_store=parts.model_store,
        providers=providers,
        reader=reader,
        anonymization=anonymization,
        state=parts.addon_state,
    )


def _data_flow(providers: ProviderService, anonymization: AnonymizationService) -> DataFlowService:
    """Return the box that says where a step runs and what it would send."""
    return DataFlowService(
        provider_info=providers.get_provider_info,
        provider_region=providers.get_provider_data_region,
        anonymizer_models=anonymization.describe_anonymizer_models,
    )


def _recommendations(
    parts: _Parts, providers: ProviderService, speeds: SpeedEstimateService
) -> ModelRecommendationService:
    """Return the model advice, and have it forget a classification when the provider does."""
    recommendations = ModelRecommendationService(
        effective_model=providers.effective_model,
        invalidate_model_list=providers.forget_ollama_model_list,
        invalidate_speed_facts=speeds.forget_server_facts,
        machine_factor=lambda url, task: speeds.machine_factors(url).for_category(task),
        hardware=parts.hardware,
        ollama=parts.ollama,
    )
    # Refreshing a server's model list also drops what was classified from it.
    providers.watch_classifications(recommendations.invalidate_classification)
    return recommendations


def _setup(
    parts: _Parts,
    providers: ProviderService,
    reader: ReaderService,
    recommendations: ModelRecommendationService,
    anonymization: AnonymizationService,
    addons: AddonService,
) -> SetupService:
    """Return the service the Setup Assistant asks its questions of."""
    return SetupService(
        ollama=parts.ollama,
        providers=providers,
        recommendations=recommendations,
        anonymization=anonymization,
        reader=reader,
        addons=addons,
    )


def _documents(
    parts: _Parts,
    recommendations: ModelRecommendationService,
    data_flow: DataFlowService,
    anonymization: AnonymizationService,
    facts: KeyFacts,
) -> tuple[DocumentQueries, DocumentCommands]:
    """Return what reads a document and what writes it, on the factories the run uses too."""
    # One store per output folder, and the context windows a run uses: the queue, the Check tab
    # and the run all compute their cache keys with these two.
    state_store = functools.partial(state_store_for, repository=parts.state_repository_factory)
    windows = functools.partial(windows_for, recommendations=recommendations)
    documents = DocumentQueries(
        state_store_factory=state_store,
        work_store=parts.work_store,
        facts=facts,
        windows_for=windows,
        data_flow=data_flow,
        anonymization=anonymization,
    )
    commands = DocumentCommands(
        state_store_factory=state_store,
        windows_for=windows,
        facts=facts,
        work_store=parts.work_store,
    )
    return documents, commands


def _run(
    parts: _Parts,
    providers: ProviderService,
    reader: ReaderService,
    recommendations: ModelRecommendationService,
    anonymization: AnonymizationService,
    addons: AddonService,
    documents: DocumentQueries,
) -> RunService:
    """Return the run itself: the service the adapters start a batch on."""
    return RunService(
        parts=RunParts(
            discovery=parts.discovery,
            pipeline_factory=parts.pipeline_factory,
            component_factory=parts.component_factory,
            sleep_blocker=parts.sleep_blocker,
            model_store=parts.model_store,
            work_store=parts.work_store,
            samples=parts.samples,
        ),
        services=RunServices(
            ollama=parts.ollama,
            hardware=parts.hardware,
            providers=providers,
            reader=reader,
            recommendations=recommendations,
            anonymization=anonymization,
            addons=addons,
            documents=documents,
        ),
    )


def inject_addon_packages_dir() -> None:
    """Clean up the addon packages directory and prepend it to ``sys.path`` (idempotent).

    Every entry point calls this first, so packages installed under Add-ons are importable after
    a restart however the app was started, and retired add-ons and an interrupted install are
    dealt with once per process.  A folder left by an older app version is not added
    (see ``AddonService.addons_outdated``).
    """
    from ..infrastructure.addon_manager import (  # noqa: PLC0415
        inject_packages_dir,
        prepare_packages_dir_once,
    )

    prepare_packages_dir_once()
    inject_packages_dir()


def start_diagnostics(version: str) -> Diagnostics | None:
    """Start the opt-in diagnostics (``--diagnose``) and return them, or ``None`` when off."""
    from ..infrastructure.diagnostics import start_if_requested  # noqa: PLC0415

    return start_if_requested(version)


def diagnostics_phase(line: str) -> None:
    """Record that the start reached *line*; does nothing unless the diagnostics are on."""
    from ..infrastructure.diagnostics import phase  # noqa: PLC0415

    phase(line)


def start_logging() -> Path | None:
    """Start the log of this process (idempotent); return its path, or ``None`` when unwritable."""
    return logging_setup.start_logging()


def log_folder() -> Path:
    """Return the folder the log file is written to: what Help → Open Log Folder shows."""
    return logging_setup.log_folder()
