"""The run: what it needs before it starts, the files it reads, and the summary it leaves.

One service answers everything a run asks — which files the queue holds, whether text
recognition may read the pages, whether the server answers, whether the models are downloaded,
whether the stages are ready and whether the models fit the free memory — and runs the files it
is given.  The context windows a cache key has to match are worked out here too, so the queue,
the Check tab and the run itself agree.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
import dataclasses
from dataclasses import dataclass
import logging
from pathlib import Path
import threading

from ..config import Config
from ..domain.context_budget import RunWindows, memory_at_context_gb
from ..domain.data_location import ENGINE_PPOCR, PROVIDER_OLLAMA
from ..domain.key_facts import KeyFacts
from ..domain.model_catalog import (
    RUN_MEMORY_MARGIN_GB,
    DetectionReason,
    HardwareDetection,
    OllamaModelSpec,
    find_spec,
    ram_needed_gb,
)
from ..domain.model_listing import ModelListing
from ..domain.ollama_address import models_include
from ..domain.ollama_discovery import WaitDetail
from ..domain.output_files import TEXT_SUFFIX, FolderProblem, output_folder_problem
from ..domain.pdf_text_layer import PdfTextLayerMode, normalize_pdf_text_layer
from ..domain.requirements import IssueKind, MemoryShortfall, RequirementIssue
from ..domain.run_summary import KeepAwakeState, RunSummary
from ..domain.stages import Stage
from ..infrastructure.file_discovery import FileDiscovery
from ..infrastructure.onnx.store import ModelStore
from ..infrastructure.pipeline import OCRPipeline, PipelineDependencies
from ..infrastructure.power import KEEP_AWAKE_REASON, NullSleepBlocker, on_battery
from ..infrastructure.speed_store import SampleStore
from ..infrastructure.stage_builder import PipelineComponentFactory
from ..infrastructure.state_repository import StateRepository, compute_file_hash_cached
from ..infrastructure.storage_root import resolve_artifact_dir
from ..infrastructure.teacher_work_store import TeacherWorkStore
from ..interfaces import ProgressCallbacks, SleepBlocker
from ..run_rules import ollama_models, resolve_run_windows, uses_ollama, window_of_model
from .addons import AddonService
from .anonymization import AnonymizationService
from .data_flow import ocr_refusal
from .documents import DocumentQueries, WorkedOn
from .model_recommendations import ModelRecommendationService
from .ports import HardwareProbe, OllamaServer
from .providers import ProviderService
from .reader import ReaderService

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunPreflight:
    """Outcome of the checks made before a run starts (first blocker wins)."""

    #: Why the output folder must not be used (``None`` when it is fine), e.g. text files
    #: saved where the run looks for inputs.  The first thing a teacher can fix.
    output_problem: FolderProblem | None = None
    #: Why text recognition may not run with these settings ("" when it may), e.g. an
    #: Ollama cloud model: pages must be read on a computer the user controls.
    ocr_not_allowed: str = ""
    #: Why Ollama cannot be used ("" when it is reachable or not needed).
    ollama_unreachable: str = ""
    #: What is wrong with Ollama when it does not answer: not found, not running, or
    #: unreachable (``None`` when it answered or is not needed).
    ollama_issue: RequirementIssue | None = None
    #: Configured Ollama models that are not downloaded.
    missing_models: tuple[str, ...] = ()
    #: Stage whose requirements are unmet ("" when all are met) and the unmet issues.
    stage: str = ""
    stage_issues: tuple[RequirementIssue, ...] = ()
    #: Why the configured Ollama models do not fit in the memory that is free now (``None`` when
    #: they do, or when Ollama runs on another computer).  A warning, not a refusal: free memory
    #: changes from run to run, so the user decides ("Run anyway" in the GUI, a flag in the CLI).
    memory_shortfall: MemoryShortfall | None = None
    #: The run reads pages with the automatic preparation while the page models are not
    #: downloaded, so it skips turning and flattening them.  A notice, never a refusal.
    page_models_skipped: bool = False
    #: Text recognition is switched off and not one queued file can be used without it.
    nothing_to_read: bool = False
    #: The queued files this run leaves out because text recognition is switched off (scans).
    #: Informational: they never block the run.
    skipped: tuple[Path, ...] = ()
    #: The documents whose corrections or transcription the teacher worked on, so the window can
    #: ask what to do with them before the run replaces anything.
    worked_on: WorkedOn = dataclasses.field(default_factory=WorkedOn)
    #: Another model is in the way on the Ollama server, so the first file waits for it.  A
    #: note, never a refusal: the run works, it just waits.
    ollama_wait: WaitDetail = dataclasses.field(default_factory=WaitDetail)

    @property
    def blocked(self) -> bool:
        """Whether a requirement of the run is unmet, so it must not start at all.

        A memory shortfall does not block: the user decides whether to run anyway.
        """
        return bool(
            self.output_problem is not None
            or self.nothing_to_read
            or self.ocr_not_allowed
            or self.ollama_unreachable
            or self.missing_models
            or self.stage_issues
        )

    @property
    def blocker(self) -> tuple[str, RequirementIssue] | None:
        """The first blocker as ``(stage, issue)``, or ``None`` when nothing blocks the run.

        The stage is :attr:`~teachers_teammate.domain.stages.Stage.OCR` for a refused recognition
        and ``""`` for a server or a model that does not belong to one stage.  One shape for
        every blocker, so the GUI shows and fixes them all the same way.
        """
        if self.output_problem is not None:
            return "", RequirementIssue(IssueKind.OUTPUT_IN_INPUT_FOLDER)
        if self.nothing_to_read:
            return Stage.OCR, RequirementIssue(IssueKind.NOTHING_TO_READ)
        if self.ocr_not_allowed:
            return Stage.OCR, RequirementIssue(
                IssueKind.OCR_NOT_ALLOWED, reason=self.ocr_not_allowed
            )
        if self.ollama_unreachable:
            return "", self.ollama_issue or RequirementIssue(
                IssueKind.OLLAMA_UNREACHABLE, reason=self.ollama_unreachable
            )
        if self.missing_models:
            return "", RequirementIssue(IssueKind.MODEL_MISSING, models=self.missing_models)
        if self.stage_issues:
            return self.stage, self.stage_issues[0]
        return None


@dataclass(frozen=True)
class RecognitionPlan:
    """Which queued files a run can use while text recognition is switched off."""

    readable: tuple[Path, ...] = ()
    """Text files, PDFs with a usable text layer, and files whose recognised text is stored."""
    skipped: tuple[Path, ...] = ()
    """Scans: images and PDFs that have to be read from page images."""


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


@dataclass(frozen=True)
class RunServices:
    """The services a run asks, one per topic."""

    ollama: OllamaServer
    #: The cheap question of this computer a run asks; the full probe stays with the
    #: recommendations.
    hardware: HardwareProbe
    providers: ProviderService
    reader: ReaderService
    recommendations: ModelRecommendationService
    anonymization: AnonymizationService
    addons: AddonService
    #: What the store says about a file: what a run would replace, and what needs no reading.
    documents: DocumentQueries


class RunService:
    """The run and everything it asks before it starts: files, requirements, windows, summary."""

    def __init__(self, *, parts: RunParts, services: RunServices) -> None:
        self._discovery = parts.discovery
        self._pipeline_factory = parts.pipeline_factory
        self._component_factory = parts.component_factory
        self._sleep_blocker = parts.sleep_blocker
        self._model_store = parts.model_store
        self._key_facts = parts.key_facts
        self._work_store = parts.work_store
        self._samples = parts.samples
        self._ollama = services.ollama
        self._hardware = services.hardware
        self._providers = services.providers
        self._reader = services.reader
        self._recommendations = services.recommendations
        self._anonymization = services.anonymization
        self._addons = services.addons
        self._documents = services.documents
        # How many runs this process is running right now: 0 unless one is inside its call.
        self._runs_going = 0
        self._runs_lock = threading.Lock()
        # Whether a PDF has a usable text layer, by (path, size, mtime, mode): the readiness
        # check asks again after every settings change, and opening a PDF is not free.
        self._pdf_text_cache: dict[tuple[str, int, int, str], bool] = {}

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

    def _page_models_skipped(self, config: Config, *, recognition: bool) -> bool:
        """Return whether a run skips turning and flattening the pages.

        That is a run that reads pages (*recognition*) with the automatic preparation while the
        page models are not downloaded.  It is a notice, never a refusal: the run does not wait
        for them.
        """
        return (
            recognition
            and config.recognition_enabled
            and config.ocr.preprocess_method == "auto"
            and not self._reader.page_models_installed()
        )

    @staticmethod
    def output_problem_for_run(config: Config) -> FolderProblem | None:
        """Return why this run's output folder must not be used, or ``None`` when it is fine.

        Text files saved where the run looks for inputs would be read as new documents
        the next time.
        """
        return output_folder_problem(
            writes_text_files=config.writes_text_files,
            output_dir=config.output_dir.resolve(),
            input_dir=config.input_dir.resolve(),
            recursive=config.recursive,
        )

    @staticmethod
    def needs_text_recognition(files: Collection[Path], *, recognised: Collection[Path]) -> bool:
        """Return whether a run over *files* has to read pages with the recognition engine.

        Text files bypass recognition, and a file whose recognised text is already stored
        (*recognised*) is not read again.  Anything else may need the engine — a PDF is counted
        as needing it even when it turns out to carry a text layer, because that is only known
        by opening it.  No files at all also counts as "needs it": nothing says otherwise yet.
        """
        if not files:
            return True
        return any(file.suffix.lower() != TEXT_SUFFIX and file not in recognised for file in files)

    def _plan_recognition(
        self,
        files: Collection[Path],
        *,
        recognised: Collection[Path] = (),
        pdf_text_layer: str = "auto",
    ) -> RecognitionPlan:
        """Return which of *files* a run can use while text recognition is switched off.

        A text file, a file whose recognised text is already stored (*recognised*) and a PDF
        with a usable text layer (in the *pdf_text_layer* mode) need no recognition.  Images
        and the other PDFs do: they are the scans the run leaves out.  A PDF that cannot be
        opened counts as readable, so the run reports the real problem instead of this
        guess.  Opens PDFs: call it from a background thread, never from the GUI thread.
        """
        mode = normalize_pdf_text_layer(pdf_text_layer)
        readable: list[Path] = []
        skipped: list[Path] = []
        for file in files:
            suffix = file.suffix.lower()
            if suffix == TEXT_SUFFIX or file in recognised:
                readable.append(file)
            elif suffix == ".pdf" and self._pdf_has_text_layer(file, mode):
                readable.append(file)
            else:
                skipped.append(file)
        return RecognitionPlan(tuple(readable), tuple(skipped))

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

    def _pdf_has_text_layer(self, file: Path, mode: PdfTextLayerMode) -> bool:
        """Return whether *file* would be read from its embedded text (``True`` if unknown)."""
        try:
            stat = file.stat()
        except OSError:
            _logger.debug("Cannot stat %s: it is taken to carry a text layer", file.name)
            return True
        key = (str(file), stat.st_size, stat.st_mtime_ns, mode)
        cached = self._pdf_text_cache.get(key)
        if cached is not None:
            return cached
        from ..infrastructure.input_providers import pdf_has_text_layer  # noqa: PLC0415

        try:
            answer = pdf_has_text_layer(file, mode)
        except Exception:  # noqa: BLE001  # an unreadable PDF is reported by the run itself
            return True
        self._pdf_text_cache[key] = answer
        return answer

    def check_run_preflight(
        self,
        config: Config,
        *,
        files: Collection[Path] | None = None,
        recognised: Collection[Path] = (),
        recognition: bool | None = None,
        for_run: bool = True,
        fresh: bool = False,
        ask_about_work: bool = False,
    ) -> RunPreflight:
        """Return the unmet requirements before a run, or an all-clear result.

        This is the one answer to "can this run start?" for the window and the command line.

        With text recognition switched off and *files* given, the result also lists the files
        the run leaves out (``skipped``), and is blocked (``nothing_to_read``) when none of
        them can be used.  Such a check opens PDFs, see :meth:`_plan_recognition`.  A document
        whose transcription the teacher stored is read from that, so it is not left out.

        *recognition* says whether the run reads pages at all; without it, *files* and
        *recognised* answer that (:meth:`needs_text_recognition`), so a caller need not ask.
        Documents whose recognised text an earlier run stored count as *recognised* too.

        *for_run* ``False`` is the bare question whether a run could start, as the readiness line
        asks it: free memory, the wait for Ollama and the output folder belong to Run, and the
        line shows the output folder on its own, so a folder not chosen yet must not stop it.

        *ask_about_work* adds ``worked_on``: the documents whose corrections or transcription the
        teacher worked on, for the question before a run.  It reads every file that was not
        read since it last changed, so only the check that starts a run asks for it.
        """
        problem = self.output_problem_for_run(config) if for_run else None
        if problem is not None:
            return RunPreflight(output_problem=problem)
        if files and (recognition is None or not config.recognition_enabled):
            recognised = {*recognised, *self._documents.recognised_files(config, files)}
        if recognition is None:
            recognition = (
                self.needs_text_recognition(files, recognised=recognised)
                if files is not None
                else True
            )
        skipped: tuple[Path, ...] = ()
        if not config.recognition_enabled and files:
            plan = self._plan_recognition(
                files,
                recognised={*recognised, *self._with_stored_transcription(files)},
                pdf_text_layer=config.ocr.pdf_text_layer,
            )
            if not plan.readable:
                return RunPreflight(nothing_to_read=True, skipped=plan.skipped)
            skipped = plan.skipped
        result = self._check_requirements(
            config, memory=for_run, recognition=recognition, fresh=fresh
        )
        if skipped:
            result = dataclasses.replace(result, skipped=skipped)
        if ask_about_work and files and not result.blocked:
            # The question about the teacher's work belongs to a run that will start.
            result = dataclasses.replace(
                result,
                worked_on=self._documents.worked_on_documents(
                    config, files, run_proofreads=config.correction_enabled
                ),
            )
        return result

    def _with_stored_transcription(self, files: Collection[Path]) -> set[Path]:
        """Return the *files* whose transcription the teacher stored: they need no reading."""
        found: set[Path] = set()
        for file in files:
            try:
                work = self._work_store.load(compute_file_hash_cached(file))
            except OSError:
                _logger.debug("Cannot read %s: no stored transcription to find", file.name)
                continue
            if work is not None and work.transcription is not None:
                found.add(file)
        return found

    def _check_requirements(
        self,
        config: Config,
        *,
        memory: bool,
        recognition: bool,
        fresh: bool,
    ) -> RunPreflight:
        """Return the unmet requirements before a run (the checks behind :meth:`check_run_preflight`).

        Makes blocking network calls: call it from a background thread, never from
        the GUI thread.  Order: text recognition allowed → Ollama reachable → models
        downloaded → stage requirements (text recognition engine, anonymizer, correction,
        evaluation) → memory (a warning, so a blocked run still reports its stage issues
        first).  ``memory=False`` skips the memory shortfall: the readiness line only asks
        whether a run can start, and the question about free memory belongs to Run.
        ``recognition=False`` skips the check of the text recognition engine for a run that
        reads no page (only text files, or text that is already recognised): a reader that is
        not downloaded must not stop it (see :meth:`needs_text_recognition`).  ``fresh=True``
        asks the Ollama server again instead of using the list it gave within the last minutes:
        for a teacher who just ran ``ollama pull`` in a terminal and pressed "Check again".
        """
        # Settings that would read pages online are refused with every file list: the fix is
        # one model choice, so the rule holds whatever files the run happens to hold.
        if config.recognition_enabled:
            ocr_reason = ocr_refusal(config.ocr.engine, config.ocr.model, config.ollama_url)
            if ocr_reason:
                return RunPreflight(ocr_not_allowed=ocr_reason)
        if uses_ollama(config):
            if fresh:
                self._providers.forget_ollama_model_list(config.ollama_url)
            installed, unreachable = self._installed_ollama_models(config.ollama_url)
            if unreachable:
                return RunPreflight(
                    ollama_unreachable=unreachable,
                    ollama_issue=self._ollama_issue(config.ollama_url, unreachable),
                )
            configured = ollama_models(config)
            missing = [m for m in configured if not models_include(m, installed)]
            if missing:
                return RunPreflight(missing_models=tuple(missing))

        stages: list[str] = [Stage.OCR] if recognition and config.recognition_enabled else []
        if config.anonymization_enabled or config.text_anonymized_enabled:
            stages.append("anonymizer")
        if config.correction_enabled:
            stages.append(Stage.CORRECTION)
        if config.evaluation_enabled:
            stages.append(Stage.EVALUATION)
        for stage in stages:
            issues = self.check_stage_requirements(config, stage)
            if issues:
                return RunPreflight(stage=stage, stage_issues=tuple(issues))
        return RunPreflight(
            memory_shortfall=self._memory_shortfall(config) if memory else None,
            page_models_skipped=self._page_models_skipped(config, recognition=recognition),
            ollama_wait=self._ollama_wait(config) if memory else WaitDetail(),
        )

    def _installed_ollama_models(self, url: str) -> tuple[list[str], str]:
        """Return ``(installed, unreachable)`` for the Ollama server at *url*.

        *unreachable* is ``""`` when the server answered — also when it has no model
        downloaded yet — and the connection message otherwise.  Makes at most one
        request (none within the model-cache TTL), so an unreachable server costs a
        single timeout instead of one per question asked of it.
        """
        cached = self._providers.get_cached_models(PROVIDER_OLLAMA, base_url=url)
        if cached is not None:
            return list(cached), ""
        installed, unreachable = self._ollama.listed_models(url)
        self._providers.remember_listing(PROVIDER_OLLAMA, ModelListing(installed), base_url=url)
        return installed, unreachable

    def _ollama_issue(self, url: str, message: str) -> RequirementIssue:
        """Return what is wrong with Ollama at *url*: not found, not running, or unreachable.

        A local address that does not answer is told apart by looking for the program: a
        teacher who has just installed the app needs to know whether to install Ollama or to
        start it.  An address on another computer can only be unreachable.  *message* is what
        the server answered, which the dialog shows.
        """
        if not self._ollama.is_local(url, include_wsl_host=True):
            return RequirementIssue(IssueKind.OLLAMA_UNREACHABLE, reason=message)
        if not self._ollama.program_found():
            return RequirementIssue(IssueKind.OLLAMA_NOT_FOUND, reason=message)
        return RequirementIssue(
            IssueKind.OLLAMA_NOT_RUNNING, reason=message, startable=self._ollama.can_start()
        )

    def _ollama_wait(self, config: Config) -> WaitDetail:
        """Return why the first file waits for Ollama, or nothing.

        A note, not a refusal.  Only for a server on this computer, and only when the model the
        run needs is not in memory while another one is: Ollama then finishes that one and loads
        this one before the first answer.  Makes one request, so only the check that starts a
        run asks for it.
        """
        if not uses_ollama(config):
            return WaitDetail()
        if not self._ollama.is_local(config.ollama_url):
            return WaitDetail()
        models = ollama_models(config)
        if not models:
            return WaitDetail()
        return self._ollama.wait_for(config.ollama_url, models[0])

    def _memory_shortfall(self, config: Config) -> MemoryShortfall | None:
        """Return the configured model that does not fit in memory now, or ``None``.

        A warning for the user, not a refusal: free memory changes from run to run, so the
        caller offers to run anyway.  For an Ollama on this computer: the part of each catalogued
        model that does not fit on the graphics card has to fit in the main memory that is free
        *right now* (keeping
        :data:`~teachers_teammate.domain.model_catalog.RUN_MEMORY_MARGIN_GB`), because a model
        that spills over would make the computer swap to disk and become unusable.  A model that
        is not catalogued, a server on another computer and an unreadable memory give ``None``.

        A model Ollama already has loaded is not credited as already counted: whether the
        processor-side part of a loaded model leaves the "available" figure depends on the kernel
        (anonymous memory vs. reclaimable memory-mapped weights) and has not been measured.  So
        the check behaves as it would after Ollama unloaded everything — which is the case it has
        to warn about anyway, since Ollama unloads idle models to make room.  If a measurement
        ever shows the memory is taken out of "available", the fix is to count the RAM part of
        other loaded models as free; see docs/development.md, *Rating a model against the computer's memory*.
        """
        if not uses_ollama(config):
            return None
        if not self._ollama.is_local(config.ollama_url):
            return None
        available = self._recommendations.available_memory_gb()
        if available is None:
            return None
        detection = self._recommendations.resolve_hardware_profile(
            config.hardware_profile, config.ollama_url
        )
        unified = self._runs_on_apple_silicon(detection)
        for tag in ollama_models(config):
            spec = find_spec(tag)
            if spec is None:
                continue
            memory = self._memory_gb_at_resolved_window(config, tag, spec, detection)
            needed = ram_needed_gb(memory, detection.profile, detection.card_gb, unified=unified)
            if needed > available - RUN_MEMORY_MARGIN_GB:
                return MemoryShortfall(model=tag, needed_gb=needed, free_gb=available)
        return None

    @staticmethod
    def _memory_gb_at_resolved_window(
        config: Config, tag: str, spec: OllamaModelSpec, detection: HardwareDetection
    ) -> float:
        """Return the memory (GB) the model *tag* needs at the window this run will give it.

        A model rated "Works" at the base window must be checked at the window it really
        gets: the larger window costs memory, and the run would swap to disk.  The window is
        the one of the stage that uses the model (the same resolution the run itself uses), and
        the base window for a model that only reads scans.
        """
        windows = resolve_run_windows(config, detection)
        window = window_of_model(config, windows, tag)
        return memory_at_context_gb(spec, window) or spec.memory_gb

    def _runs_on_apple_silicon(self, detection: HardwareDetection) -> bool:
        """Whether the models share one pool of memory with the graphics side (Apple Silicon).

        Read from the computer, not from the detection reason: a profile chosen by hand is a
        MANUAL detection, but on Apple Silicon the whole model still comes out of that memory.
        """
        return (
            detection.reason is DetectionReason.APPLE_UNIFIED_MEMORY
            or self._hardware.apple_unified_memory_gb() is not None
        )

    def config_for_stage_run(self, config: Config, stage: str) -> Config:
        """Return *config* for running one stage by hand: that stage on, the later ones off.

        Running a stage from the right-click menu is an explicit, per-entry action: it runs
        exactly that stage regardless of the enable checkboxes (which only govern the main
        *Run*), and nothing after it.  The cache of the later stages was just cleared, so
        leaving them enabled would silently run the whole chain again.  Evaluation also needs
        correction text (cached, or computed first).
        """
        if stage == Stage.OCR:
            return dataclasses.replace(
                config,
                # Asking for this stage by hand overrides the "Recognize text" tick.
                recognition_enabled=True,
                correction_enabled=False,
                evaluation_enabled=False,
                anonymization_enabled=False,
            )
        if stage == Stage.CORRECTION:
            return dataclasses.replace(config, correction_enabled=True, evaluation_enabled=False)
        if stage == Stage.EVALUATION:
            return dataclasses.replace(config, evaluation_enabled=True, correction_enabled=True)
        return config

    def check_stage_requirements(self, config: Config, stage: str) -> list[RequirementIssue]:
        """Return unmet requirements for enabling *stage*.

        *stage* is a :class:`~teachers_teammate.domain.stages.Stage` or ``"anonymizer"``.
        Empty list means all requirements are satisfied.
        """
        if stage == Stage.OCR:
            return self._ocr_engine_issues(config.ocr.engine)
        if stage in (Stage.CORRECTION, Stage.EVALUATION):
            return self._llm_stage_issues(config, stage)
        if stage == "anonymizer":
            return self._name_detection_issues(config.language)
        return []

    def _ocr_engine_issues(self, engine: str) -> list[RequirementIssue]:
        """Return an issue when the text-recognition *engine* is not ready.

        Ollama is checked by the preflight before the stages.
        """
        if engine == ENGINE_PPOCR and not self._reader.reader_installed():
            return [RequirementIssue(IssueKind.READER_MODEL_MISSING)]
        return []

    def _llm_stage_issues(self, config: Config, stage: str) -> list[RequirementIssue]:
        """Return what proofreading or the content review still needs (package, key, model)."""
        provider = (
            config.correction_provider if stage == Stage.CORRECTION else config.evaluate_provider
        )
        model = (
            (config.effective_correction_model or "")
            if stage == Stage.CORRECTION
            else (config.effective_evaluate_model or "")
        )
        name = self._providers.display_name(provider)
        missing_package = self._providers.missing_provider_package(provider)
        if missing_package and self._addon_waits_for_restart(missing_package):
            return [self._addon_restart_issue(f"{name} support")]
        if missing_package and self._addon_needs_update(missing_package):
            return [self._addons_outdated_issue()]
        if missing_package:
            return [RequirementIssue(IssueKind.PACKAGE_MISSING, provider=provider, name=name)]
        if self._providers.provider_key_missing(provider):
            return [RequirementIssue(IssueKind.KEY_MISSING, provider=provider, name=name)]
        if provider != PROVIDER_OLLAMA:
            return []
        installed, unreachable = self._installed_ollama_models(config.ollama_url)
        models = (model,) if model else ()
        if unreachable:
            return [RequirementIssue(IssueKind.OLLAMA_UNREACHABLE, reason=unreachable)]
        if not installed:
            return [RequirementIssue(IssueKind.NO_OLLAMA_MODEL, models=models)]
        if model and not models_include(model, installed):
            return [RequirementIssue(IssueKind.MODEL_MISSING, models=models)]
        return []

    @staticmethod
    def _addons_outdated_issue() -> RequirementIssue:
        return RequirementIssue(IssueKind.ADDONS_OUTDATED)

    @staticmethod
    def _addon_restart_issue(what: str) -> RequirementIssue:
        return RequirementIssue(IssueKind.ADDON_RESTART_NEEDED, name=what)

    def _addon_needs_update(self, distribution: str) -> bool:
        """Return whether *distribution* is installed as an add-on but kept off by an app update."""
        return self._addons.addons_outdated() and self._addons.addon_in_folder(distribution)

    def _addon_waits_for_restart(self, distribution: str) -> bool:
        """Return whether *distribution* is installed but waits for the next start."""
        return self._addons.addon_waits_for_restart(distribution)

    def _name_detection_issues(self, language: str) -> list[RequirementIssue]:
        """Return what removing names still needs: spaCy itself or the model of *language*."""
        if self._addon_waits_for_restart("spacy"):
            return [self._addon_restart_issue("name detection")]
        if self._addon_needs_update("spacy"):
            return [self._addons_outdated_issue()]
        installed_models = self._anonymization.list_installed_spacy_models()
        required = self._anonymization.spacy_model_for_language(language)
        if required in installed_models:
            return []
        if not installed_models and not self._addons.is_module_importable("spacy"):
            return [RequirementIssue(IssueKind.PRIVACY_ADDON_MISSING, models=(required,))]
        return [RequirementIssue(IssueKind.NAME_DETECTION_MISSING, models=(required,))]
