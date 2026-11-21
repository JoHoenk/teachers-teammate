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
from pathlib import Path
from typing import TYPE_CHECKING

from ..infrastructure import power
from ..infrastructure.file_discovery import FileDiscovery
from ..infrastructure.onnx.store import ModelStore
from ..infrastructure.pipeline import OCRPipeline
from ..infrastructure.stage_builder import PipelineComponentFactory
from ..infrastructure.state_repository import StateRepository
from ..infrastructure.teacher_work_store import TeacherWorkStore
from ..interfaces import SleepBlocker
from .addons import AddonService
from .anonymization import AnonymizationService
from .commands import DocumentCommands
from .data_flow import DataFlowService
from .documents import DocumentQueries
from .model_recommendations import ModelRecommendationService
from .ollama_server import OllamaServerService
from .providers import ProviderService
from .reader import ReaderService
from .service import ProcessingApplicationService
from .setup_plan import SetupService
from .speed_estimates import SpeedEstimateService

if TYPE_CHECKING:
    from ..infrastructure.diagnostics import Diagnostics


@dataclass(frozen=True)
class AppServices:
    """The application's services, one instance per process.

    ``processing`` is the facade the adapters call today; the focused services take its place
    one topic at a time.  The rest are the parts that must not exist twice: the teacher's work,
    the reader's model files, and the sleep blocker held around a run.
    """

    processing: ProcessingApplicationService
    """The facade the adapters still call for the topics that have not moved yet."""
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
    commands: DocumentCommands
    """The writes: the teacher's texts and decisions."""
    work_store: TeacherWorkStore
    model_store: ModelStore
    sleep_blocker: SleepBlocker


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
    work_store = work_store or TeacherWorkStore()
    model_store = model_store or ModelStore()
    sleep_blocker = sleep_blocker or power.default_sleep_blocker()
    ollama = OllamaServerService()
    providers = ProviderService()
    component_factory = component_factory or PipelineComponentFactory()
    reader = ReaderService(
        model_store=model_store, component_factory=component_factory, providers=providers
    )
    speeds = SpeedEstimateService(effective_model=providers.effective_model)
    anonymization = AnonymizationService()
    addons = AddonService(
        model_store=model_store,
        providers=providers,
        reader=reader,
        anonymization=anonymization,
    )
    data_flow = DataFlowService(
        provider_info=providers.get_provider_info,
        provider_region=providers.get_provider_data_region,
        anonymizer_models=anonymization.describe_anonymizer_models,
    )
    recommendations = ModelRecommendationService(
        effective_model=providers.effective_model,
        invalidate_model_list=providers.forget_ollama_model_list,
        invalidate_speed_facts=speeds.forget_server_facts,
        machine_factor=lambda url, task: speeds.machine_factors(url).for_category(task),
    )
    setup = SetupService(
        ollama=ollama,
        providers=providers,
        recommendations=recommendations,
        anonymization=anonymization,
        reader=reader,
        addons=addons,
    )
    processing = ProcessingApplicationService(
        ollama=ollama,
        providers=providers,
        reader=reader,
        recommendations=recommendations,
        speeds=speeds,
        anonymization=anonymization,
        addons=addons,
        data_flow=data_flow,
        setup=setup,
        discovery=discovery,
        pipeline_factory=pipeline_factory,
        state_repository_factory=state_repository_factory,
        component_factory=component_factory,
        sleep_blocker=sleep_blocker,
        model_store=model_store,
        work_store=work_store,
    )
    return AppServices(
        processing=processing,
        ollama=ollama,
        providers=providers,
        reader=reader,
        recommendations=recommendations,
        speeds=speeds,
        anonymization=anonymization,
        addons=addons,
        data_flow=data_flow,
        setup=setup,
        documents=processing.documents,
        commands=processing.commands,
        work_store=work_store,
        model_store=model_store,
        sleep_blocker=sleep_blocker,
    )


def inject_addon_packages_dir() -> None:
    """Prepend the addon packages directory to ``sys.path`` (idempotent).

    Every entry point calls this first, so packages installed under Add-ons are importable after
    a restart however the app was started.  A folder left by an older app version is not added
    (see ``AddonService.addons_outdated``).
    """
    from ..infrastructure.addon_manager import inject_packages_dir  # noqa: PLC0415

    inject_packages_dir()


def start_diagnostics(version: str) -> Diagnostics | None:
    """Start the opt-in diagnostics (``--diagnose``) and return them, or ``None`` when off."""
    from ..infrastructure.diagnostics import start_if_requested  # noqa: PLC0415

    return start_if_requested(version)


def diagnostics_phase(line: str) -> None:
    """Record that the start reached *line*; does nothing unless the diagnostics are on."""
    from ..infrastructure.diagnostics import phase  # noqa: PLC0415

    phase(line)
