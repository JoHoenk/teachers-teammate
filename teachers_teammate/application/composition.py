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

from ..infrastructure import power
from ..infrastructure.file_discovery import FileDiscovery
from ..infrastructure.onnx.store import ModelStore
from ..infrastructure.pipeline import OCRPipeline
from ..infrastructure.stage_builder import PipelineComponentFactory
from ..infrastructure.state_repository import StateRepository
from ..infrastructure.teacher_work_store import TeacherWorkStore
from ..interfaces import SleepBlocker
from .service import ProcessingApplicationService


@dataclass(frozen=True)
class AppServices:
    """The application's services, one instance per process.

    ``processing`` is the facade the adapters call today; the focused services take its place
    one topic at a time.  The rest are the parts that must not exist twice: the teacher's work,
    the reader's model files, and the sleep blocker held around a run.
    """

    processing: ProcessingApplicationService
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
    processing = ProcessingApplicationService(
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
