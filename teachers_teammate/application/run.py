"""The run itself: the files it is given, the summary it leaves, and the lock around it.

Whether a run may start is asked of :class:`~teachers_teammate.application.run_checks.RunChecks`;
this service runs the files, keeps the computer awake, and counts the runs so the stored results
are not deleted under one.  The context windows a cache key has to match are worked out here too,
so the queue, the Check tab and the run itself agree.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
import dataclasses
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any

from ..config import Config
from ..domain.context_budget import RunWindows
from ..domain.key_facts import KeyFacts
from ..domain.run_summary import KeepAwakeState, RunSummary
from ..infrastructure.file_discovery import FileDiscovery
from ..infrastructure.onnx.store import ModelStore
from ..infrastructure.pipeline import OCRPipeline, PipelineDependencies
from ..infrastructure.power import KEEP_AWAKE_REASON, NullSleepBlocker, on_battery
from ..infrastructure.speed_store import SampleStore
from ..infrastructure.stage_builder import PipelineComponentFactory
from ..infrastructure.state_repository import StateRepository
from ..infrastructure.storage_root import resolve_artifact_dir
from ..infrastructure.teacher_work_store import TeacherWorkStore
from ..interfaces import ProgressCallbacks, SleepBlocker
from ..run_rules import resolve_run_windows
from .anonymization import AnonymizationService
from .model_recommendations import ModelRecommendationService
from .ports import OllamaClientFactory


def state_store_for(
    config: Config, *, repository: Callable[[Path], StateRepository]
) -> StateRepository:
    """Return the store of *config*'s document results: one per output folder."""
    return repository(resolve_artifact_dir(config.output_dir) / "state")


def windows_for(config: Config, *, recommendations: ModelRecommendationService) -> RunWindows:
    """Return the context windows a run with *config* uses on this computer.

    The same resolution the run itself uses, so a cache key computed here matches the one the
    run writes — once this computer has been probed.  It never probes itself: the GUI asks for
    the expected cache keys on its own thread, and the probe runs ``nvidia-smi`` and the ROCm
    tools.  While the computer is still unknown the base window is returned; a caller that
    needs the card-derived window calls
    :meth:`ModelRecommendationService.detect_hardware` in the background first and asks again
    (``.cached_hardware_profile`` says whether that is needed).
    """
    return resolve_run_windows(
        config,
        recommendations.cached_hardware_profile(config.hardware_profile, config.ollama_url),
    )


@dataclass(frozen=True)
class RunParts:
    """The parts of this process a run starts from: the files, the pipeline, the stores."""

    discovery: FileDiscovery
    pipeline_factory: Callable[..., OCRPipeline]
    #: The stage factory a run starts from; the run swaps in the shared anonymizer.
    component_factory: PipelineComponentFactory
    #: Held around a run only (see run_selected): a fake in tests, the platform's own here.
    sleep_blocker: SleepBlocker
    model_store: ModelStore
    #: What this computer adds to a cache key: the queue and the run key a result the same way.
    key_facts: KeyFacts
    #: The teacher's work: one store for the window's writes and every run's reads, so both see
    #: the same folder.
    work_store: TeacherWorkStore
    #: The one sample store of this process: the speed estimates read what a run writes.
    samples: SampleStore
    #: Builds every Ollama client a run uses: the app's only factory, handed in by composition,
    #: so a run's clients carry its stop event and its statistics sink.
    client_factory: OllamaClientFactory[Any]


class RunService:
    """The run: the files it is given, the summary it leaves, and the lock around it."""

    def __init__(
        self,
        *,
        parts: RunParts,
        recommendations: ModelRecommendationService,
        anonymization: AnonymizationService,
    ) -> None:
        self._discovery = parts.discovery
        self._pipeline_factory = parts.pipeline_factory
        self._component_factory = parts.component_factory
        self._sleep_blocker = parts.sleep_blocker
        self._model_store = parts.model_store
        self._key_facts = parts.key_facts
        self._work_store = parts.work_store
        self._samples = parts.samples
        self._client_factory = parts.client_factory
        self._recommendations = recommendations
        self._anonymization = anonymization
        # How many runs this process is running right now: 0 unless one is inside its call.
        self._runs_going = 0
        self._runs_lock = threading.Lock()

    def busy(self) -> bool:
        """Return whether a run is going in this process.

        The stored results and the settings that delete them ask this: a run is writing the
        files they would remove.  A comparison counts too (:meth:`running`).
        """
        with self._runs_lock:
            return self._runs_going > 0

    @contextmanager
    def running(self) -> Iterator[None]:
        """Count the caller as a run for as long as the block lasts.

        Whatever writes the stored results counts, not only :meth:`run_selected`: a
        comparison writes into the same folders.
        """
        with self._runs_lock:
            self._runs_going += 1
        try:
            yield
        finally:
            with self._runs_lock:
                self._runs_going -= 1

    def run_selected(
        self,
        config: Config,
        files: Sequence[Path],
        *,
        config_file: Path | None = None,
        stop_event: threading.Event | None = None,
        stop_after_file_event: threading.Event | None = None,
        reporter=None,
        progress: ProgressCallbacks | None = None,
        battery_warning: str = "",
    ) -> RunSummary:
        """Run *files* and return what happened to each.

        The run processes exactly the files it is given, in that order: the queue the teacher
        sees and the files that are read are one list.

        The computer is kept awake for the whole run unless ``config.keep_awake`` is off.
        *stop_event* stops at once (the Ollama request being made is cancelled);
        *stop_after_file_event* lets the file being processed finish and starts no other.
        """
        with self.running():
            blocker = self._sleep_blocker if config.keep_awake else NullSleepBlocker()
            with blocker.hold(KEEP_AWAKE_REASON) as held:
                awake = KeepAwakeState(
                    enabled=config.keep_awake,
                    held=held,
                    available=blocker.available,
                    on_battery=on_battery(),
                )
                if progress is not None and progress.on_keep_awake is not None:
                    progress.on_keep_awake(awake)
                pipeline = self._pipeline_factory(
                    config,
                    files,
                    stop_event=stop_event,
                    stop_after_file_event=stop_after_file_event,
                    config_file=config_file,
                    dependencies=PipelineDependencies(
                        reporter=reporter,
                        component_factory=self._run_component_factory(),
                        hardware=self._recommendations.resolve_hardware_profile(
                            config.hardware_profile, config.ollama_url
                        ),
                        keep_awake=awake,
                        battery_warning=battery_warning,
                        model_store=self._model_store,
                        key_facts=self._key_facts,
                        work_store=self._work_store,
                        samples=self._samples,
                        client_factory=self._client_factory,
                    ),
                    progress=progress,
                )
                return pipeline.run()

    def _run_component_factory(self) -> PipelineComponentFactory:
        """Return the stage factory of a run: its anonymizer shares this service's caches."""
        return dataclasses.replace(
            self._component_factory, build_anonymizer=self._anonymization.build_cached_anonymizer
        )

    def run_preview_only(self, config: Config, files: Sequence[Path]) -> RunSummary:
        """Preprocess *files* and return what happened to each; nothing is read or proofread."""
        with self.running():
            dependencies = PipelineDependencies(
                samples=self._samples,
                work_store=self._work_store,
                model_store=self._model_store,
                key_facts=self._key_facts,
                client_factory=self._client_factory,
            )
            return self._pipeline_factory(
                config, files, dependencies=dependencies
            ).run_preview_only()

    def collect_input_files(self, config: Config) -> list[Path]:
        """Return supported source files for the current config."""
        return self._discovery.collect_input_files(config)

    def list_supported_suffixes(self) -> frozenset[str]:
        """Return input file suffixes accepted by the pipeline."""
        return self._discovery.supported_suffixes()

    def collect_input_files_from_dir(self, input_dir: Path, recursive: bool) -> list[Path]:
        """Collect supported input files without a full Config (for partial GUI state)."""
        return self._discovery.collect_input_files_from_dir(input_dir, recursive)

    def page_counts(self, files: Collection[Path]) -> dict[str, int]:
        """Return how many pages each of *files* has, keyed by resolved path.

        An image or a text file is one page, a PDF as many as it has, and a file that cannot be
        read counts as one: the figure feeds a time estimate, which must not fail over a file
        the run itself will report.  Opens PDFs: call it from a background thread.
        """
        from ..infrastructure.input_providers import pdf_page_count  # noqa: PLC0415

        counts: dict[str, int] = {}
        for file in files:
            pages = 1
            if file.suffix.lower() == ".pdf":
                pages = pdf_page_count(file) or 1
            counts[str(file.resolve())] = pages
        return counts
