"""Application-layer orchestration services used by GUI and CLI adapters."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
import dataclasses
from dataclasses import dataclass
from enum import StrEnum
import functools
import importlib.util
import os
from pathlib import Path
import sys
import threading
import time
from typing import TYPE_CHECKING, Any
import uuid

from ..config import DEFAULTS, INSTALLER_SETTING_KEYS, Config, OcrConfig, load_config_file
from ..domain.context_budget import RunWindows, memory_at_context_gb
from ..domain.data_location import (
    ENGINE_PPOCR,
    DataFlow,
    StageLocation,
    is_ollama_cloud_model,
)
from ..domain.formatted_text import TextFormat, normalize_text_format
from ..domain.local_models import (
    PAGE_MODELS,
    PAGE_MODELS_SKIPPED_NOTICE,
    READER_MODELS,
    READER_NOT_DOWNLOADED,
    READER_SIZE_LABEL,
)
from ..domain.model_catalog import (
    RUN_MEMORY_MARGIN_GB,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    ModelCategory,
    ModelRating,
    ModelUses,
    OllamaModelSpec,
    find_spec,
    ram_needed_gb,
)
from ..domain.model_listing import ModelListing
from ..domain.ollama_discovery import (
    DiscoveredServer,
    DiscoveryScope,
    OllamaState,
    ollama_state,
)
from ..domain.output_files import (
    TEXT_SUFFIX,
    feeds_inputs,
    output_folder_refusal,
    output_path,
)
from ..domain.pdf_text_layer import PdfTextLayerMode, normalize_pdf_text_layer
from ..domain.performance_estimate import MachineFactors, TimeRange
from ..domain.run_summary import RunSummary
from ..infrastructure import power
from ..infrastructure.addon_manager import ADDON_PRIVACY as ADDON_PRIVACY  # noqa: PLC0414
from ..infrastructure.anonymizer import (
    DEFAULT_PATTERNS as DEFAULT_ANONYMIZER_PATTERNS,  # noqa: F401
)
from ..infrastructure.anonymizer import AnonymizationResultCache, CachedAnonymizer

# ── Re-exports for GUI/CLI use — keeps infrastructure types out of adapter layers ──
from ..infrastructure.anonymizer import AnonymizerConfig as AnonymizerConfig  # noqa: PLC0414
from ..infrastructure.anonymizer import (
    resolve_patterns as resolve_anonymizer_patterns,  # noqa: F401
)
from ..infrastructure.file_discovery import FileDiscovery
from ..infrastructure.gpu_detector import GpuInfo as GpuInfo  # noqa: PLC0414
from ..infrastructure.llm_factory import (
    check_provider_connection,
    default_model_for_task,
    get_provider_data_region,
    get_provider_info,
    list_provider_models_checked,
    list_providers,
    provider_package,
    provider_package_required,
)
from ..infrastructure.ocr_processor import (
    SUPPORTED_OCR_ENGINES,
)
from ..infrastructure.ollama_utils import (
    ServerActivity,
    WaitDetail,
    warm_up_own_host_names,
)
from ..infrastructure.onnx.store import ModelStore
from ..infrastructure.pipeline import OCRPipeline, preprocess_preview
from ..infrastructure.power import KEEP_AWAKE_REASON, NullSleepBlocker, on_battery
from ..infrastructure.reporting import KeepAwakeState
from ..infrastructure.reporting import ProgressCallbacks as ProgressCallbacks  # noqa: PLC0414
from ..infrastructure.stage_builder import (
    PipelineComponentFactory,
    resolve_run_windows,
    window_of_model,
)
from ..infrastructure.state_repository import (
    DocumentState,
    DocumentStateView,
    StateRepository,
    compute_file_hash_cached,
    machine_texts,
)
from ..infrastructure.storage_root import (
    default_config_path as _infra_default_config_path,
)
from ..infrastructure.storage_root import (
    resolve_artifact_dir,
    resolve_storage_root,
)
from ..infrastructure.storage_root import (
    resolve_config_path as _infra_resolve_config_path,
)
from ..infrastructure.teacher_work_store import TeacherWorkStore
from ..infrastructure.workflow.cache_service import (
    ExpectedHashes,
    compute_correction_config_hash,
    expected_hashes,
    privacy_fingerprint,
    stage_hashes,
)
from ..interfaces import SUPPORTED_SUFFIXES, SleepBlocker
from .check_document import CheckDocument, check_document_of
from .commands import ApplicationCommands
from .commands import WorkNotSavedError as WorkNotSavedError  # noqa: PLC0414
from .context_choice import ContextChoice, context_choice
from .correction_options import prompt_step_title
from .data_flow import (
    describe_data_flow,
    llm_location,
    ocr_location,
    ocr_refusal,
    text_goes_online,
)
from .formatting import TextFormatter
from .merged_view import merged_view, reconciled_view
from .model_recommendations import ModelRecommendationService
from .queue import QueueRow, StoredStages, queue_row
from .sent_text import Anonymize, SentTexts, describe_sent_texts
from .setup_plan import (
    LocalModelChoices,
    ProofreadingPlace,
    SetupAnswers,
    SetupFacts,
    SetupPlan,
    build_model_choices,
    build_setup_plan,
)
from .speed_estimates import SpeedChoices, SpeedEstimateService, SpeedSummary

if TYPE_CHECKING:
    from ..infrastructure.anonymizer import SpacyAnonymizer
    from ..infrastructure.performance_probe import ProbeResult, ProgressFn
    from ..interfaces import Anonymizer


class IssueKind(StrEnum):
    """What is missing, so the GUI can offer the fix without reading the sentence."""

    OCR_NOT_ALLOWED = "ocr_not_allowed"
    #: The built-in reader's model files are not downloaded yet.
    READER_MODEL_MISSING = "reader_model_missing"
    #: An address on this computer that does not answer, and no Ollama program was found.
    OLLAMA_NOT_FOUND = "ollama_not_found"
    #: Ollama is installed on this computer but does not answer: it has to be started.
    OLLAMA_NOT_RUNNING = "ollama_not_running"
    #: An address that does not answer, and nothing can be started from here.
    OLLAMA_UNREACHABLE = "ollama_unreachable"
    NO_OLLAMA_MODEL = "no_ollama_model"
    MODEL_MISSING = "model_missing"
    PACKAGE_MISSING = "package_missing"
    KEY_MISSING = "key_missing"
    NAME_DETECTION_MISSING = "name_detection_missing"
    #: spaCy itself is not installed, so no name detection model can run.
    PRIVACY_ADDON_MISSING = "privacy_addon_missing"
    #: Text recognition is switched off and no queued file has text to use without it.
    NOTHING_TO_READ = "nothing_to_read"
    #: An app update changed packages the add-ons share with it: they stay off until updated.
    ADDONS_OUTDATED = "addons_outdated"
    #: An install could not replace a file the running app has loaded: a restart finishes it.
    ADDON_RESTART_NEEDED = "addon_restart_needed"


@dataclasses.dataclass(frozen=True)
class RequirementIssue:
    """A single unmet requirement for enabling a pipeline stage or starting a run."""

    #: The sentence for the dialog.
    message: str
    kind: IssueKind
    #: The provider whose package or key is missing (``PACKAGE_MISSING``, ``KEY_MISSING``).
    provider: str = ""
    #: Ollama tags that are not downloaded, or the spaCy model that is missing.
    models: tuple[str, ...] = ()
    #: The app can start Ollama itself (``OLLAMA_NOT_RUNNING``): the fix offers a button.
    startable: bool = False


@dataclass(frozen=True)
class DocxExport:
    """Presentation options for :meth:`ProcessingApplicationService.export_docx`."""

    docx_format: str = "table"
    """``"table"`` (three columns) or ``"comments"`` (diff hunks as Word comments)."""
    labels_language: str = ""
    """Language of the review headings and column titles (empty: English)."""


@dataclass(frozen=True)
class WorkedOn:
    """The documents of a run whose corrections or transcription the teacher worked on.

    Worked out before a run starts, so the window can ask what to do with them: a run keeps a
    proofreading the teacher worked on (it is not stale by its settings), and only an explicit
    answer replaces it.
    """

    kept_stale: tuple[Path, ...] = ()
    """The proofreading is stale only by its settings and was worked on: a run keeps it."""
    unanswered: tuple[Path, ...] = ()
    """Of those, the ones not yet answered for these settings: the run-start question lists them."""
    unbound: tuple[Path, ...] = ()
    """Worked-on corrections that belong to no stored proofreading: a new one will not have them."""
    transcribed: tuple[Path, ...] = ()
    """The teacher fixed or checked the transcription: reading the pages again replaces it."""
    worked_on: tuple[Path, ...] = ()
    """The corrections part applies and was worked on: an explicit re-run would drop it."""

    @property
    def empty(self) -> bool:
        """Whether the teacher worked on nothing this run touches."""
        return not (
            self.kept_stale or self.unanswered or self.unbound or self.transcribed or self.worked_on
        )


@dataclass(frozen=True)
class RunPreflight:
    """Outcome of the checks made before a run starts (first blocker wins)."""

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
    #: Why the configured Ollama models do not fit in the memory that is free now ("" when they
    #: do, or when Ollama runs on another computer).  A warning, not a refusal: free memory
    #: changes from run to run, so the user decides ("Run anyway" in the GUI, a flag in the CLI).
    memory_warning: str = ""
    #: Text recognition is switched off and not one queued file can be used without it.
    nothing_to_read: bool = False
    #: The queued files this run leaves out because text recognition is switched off (scans).
    #: Informational: they never block the run.
    skipped: tuple[Path, ...] = ()
    #: The documents whose corrections or transcription the teacher worked on, so the window can
    #: ask what to do with them before the run replaces anything.
    worked_on: WorkedOn = WorkedOn()
    #: Another model is in the way on the Ollama server, so the first file waits for it.  A
    #: note, never a refusal: the run works, it just waits.
    ollama_wait: WaitDetail = WaitDetail()

    @property
    def blocked(self) -> bool:
        """Whether a requirement of the run is unmet, so it must not start at all.

        The memory warning does not block: the user decides whether to run anyway.
        """
        return bool(
            self.nothing_to_read
            or self.ocr_not_allowed
            or self.ollama_unreachable
            or self.missing_models
            or self.stage_issues
        )

    @property
    def blocker(self) -> tuple[str, RequirementIssue] | None:
        """The first blocker as ``(stage, issue)``, or ``None`` when nothing blocks the run.

        The stage is ``"ocr"`` for a refused recognition and ``""`` for a server or a model
        that does not belong to one stage.  One shape for every blocker, so the GUI shows and
        fixes them all the same way.
        """
        if self.nothing_to_read:
            return "ocr", RequirementIssue(
                "Text recognition is switched off, and none of these files has text to use "
                "without it.",
                IssueKind.NOTHING_TO_READ,
            )
        if self.ocr_not_allowed:
            return "ocr", RequirementIssue(self.ocr_not_allowed, IssueKind.OCR_NOT_ALLOWED)
        if self.ollama_unreachable:
            return "", self.ollama_issue or RequirementIssue(
                self.ollama_unreachable, IssueKind.OLLAMA_UNREACHABLE
            )
        if self.missing_models:
            listed = ", ".join(f"'{model}'" for model in self.missing_models)
            noun = "Model" if len(self.missing_models) == 1 else "Models"
            verb = "is" if len(self.missing_models) == 1 else "are"
            return "", RequirementIssue(
                f"{noun} {listed} {verb} not downloaded.",
                IssueKind.MODEL_MISSING,
                models=self.missing_models,
            )
        if self.stage_issues:
            return self.stage, self.stage_issues[0]
        return None


@dataclass(frozen=True)
class OllamaStatus:
    """What the app knows about Ollama at an address, and what it can do about it."""

    state: OllamaState
    #: The server's version (``""`` when it did not answer).
    version: str = ""
    #: How many models it has downloaded (``None`` when it did not answer).
    model_count: int | None = None
    #: The app can start it from here (only ``NOT_RUNNING`` on Windows and macOS).
    can_start: bool = False


@dataclass(frozen=True)
class RecognitionPlan:
    """Which queued files a run can use while text recognition is switched off."""

    readable: tuple[Path, ...] = ()
    """Text files, PDFs with a usable text layer, and files whose recognised text is stored."""
    skipped: tuple[Path, ...] = ()
    """Scans: images and PDFs that have to be read from page images."""


#: Keys of :meth:`ProcessingApplicationService.get_installation_status` for the two model sets the
#: app can carry itself; the Add-ons page names its rows (and its install items) the same way.
READER_STATUS_KEY = "reader:ppocr"
PAGE_MODELS_STATUS_KEY = "page_models:all"
#: In front of a status key: whether the bundle itself carries that item (``bundled:reader:ppocr``).
BUNDLED_PREFIX = "bundled:"

#: Why an export has nothing to write: the document was never read.
NO_TEXT_MESSAGE = "No OCR text — run the pipeline first."


def bundled_status_key(key: str) -> str:
    """Return the status key that says whether the app carries *key*'s files itself."""
    return f"{BUNDLED_PREFIX}{key}"


class ProcessingApplicationService:
    """Coordinates processing and state transitions behind a small app-facing API."""

    _MODEL_CACHE_TTL_S: float = 300.0

    def __init__(
        self,
        *,
        discovery: FileDiscovery | None = None,
        pipeline_factory: Callable[..., OCRPipeline] | None = None,
        state_repository_factory: Callable[[Path], StateRepository] | None = None,
        component_factory: PipelineComponentFactory | None = None,
        sleep_blocker: SleepBlocker | None = None,
        model_store: ModelStore | None = None,
        work_store: TeacherWorkStore | None = None,
    ) -> None:
        self._discovery = discovery or FileDiscovery()
        # The teacher's work: one store for the window's writes and every run's reads, so both
        # see the same folder.
        self._work_store = work_store or TeacherWorkStore()
        # The built-in reader's model files; tests give it a store under a temporary folder.
        self._model_store = model_store or ModelStore()
        # Held around a run only (see run_selected); a fake in tests, the platform's own here.
        self._sleep_blocker = sleep_blocker or power.default_sleep_blocker()
        self._pipeline_factory = pipeline_factory or OCRPipeline
        self._state_repository_factory = state_repository_factory or StateRepository
        # Used only to keep preprocess_preview() in sync with a non-default factory a
        # caller's OCRPipeline was built with; pipeline runs configure their own copy.
        self._component_factory = component_factory or PipelineComponentFactory()
        self._anonymizer_cache: dict[tuple, SpacyAnonymizer] = {}
        # Anonymized texts of this session, so a run, a preview and an export analyse the same
        # text once.  In memory only: nothing that was hidden is written to disk.
        self._anonymization_cache = AnonymizationResultCache()
        # A spaCy pipeline is not safe to share between threads (a privacy preview and a
        # background text export may run at once), so building and running are serialized.
        self._anonymizer_lock = threading.RLock()
        # Listing the installed spaCy models imports spaCy; the answer only changes when the
        # user downloads one, so it is remembered until forget_installed_models().
        self._installed_models: list[str] | None = None
        # Warnings already shown: resolving the settings happens on every preview.
        self._warned: set[str] = set()
        self._model_cache: dict[str, tuple[float, ModelListing]] = {}
        # Whether a PDF has a usable text layer, by (path, size, mtime, mode): the readiness
        # check asks again after every settings change, and opening a PDF is not free.
        self._pdf_text_cache: dict[tuple[str, int, int, str], bool] = {}
        # Owns hardware detection and model classification (and their caches).
        self._recommendations = ModelRecommendationService(
            invalidate_model_list=self._evict_ollama_model_list
        )
        # Owns the speed test, the stored measurements and the server facts behind them.
        self._speeds = SpeedEstimateService()
        self._commands = ApplicationCommands(
            discovery=self._discovery,
            state_store_factory=self._state_store,
            windows_for=self.resolved_windows,
            model_store=self._model_store,
            work_store=self._work_store,
        )

    def _state_store(self, config: Config) -> StateRepository:
        return self._state_repository_factory(resolve_artifact_dir(config.output_dir) / "state")

    # ── Workflow operations ───────────────────────────────────────────────

    def run_selected(
        self,
        config: Config,
        *,
        config_file: Path | None = None,
        selected_source_paths: list[str] | None = None,
        stop_event: threading.Event | None = None,
        stop_after_file_event: threading.Event | None = None,
        reporter=None,
        progress: ProgressCallbacks | None = None,
    ) -> RunSummary:
        """Run the selected files (all when none are selected) and return what happened.

        The computer is kept awake for the whole run unless ``config.keep_awake`` is off.
        *stop_event* stops at once (the Ollama request being made is cancelled);
        *stop_after_file_event* lets the file being processed finish and starts no other.
        """
        from ..infrastructure.pipeline import PipelineDependencies  # noqa: PLC0415

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
                stop_event=stop_event,
                stop_after_file_event=stop_after_file_event,
                config_file=config_file,
                selected_source_paths=selected_source_paths,
                dependencies=PipelineDependencies(
                    reporter=reporter,
                    component_factory=self._run_component_factory(),
                    hardware=self.resolve_hardware_profile(
                        config.hardware_profile, config.ollama_url
                    ),
                    keep_awake=awake,
                    model_store=self._model_store,
                    work_store=self._work_store,
                ),
                progress=progress,
            )
            return pipeline.run()

    def _run_component_factory(self) -> PipelineComponentFactory:
        """Return the stage factory of a run: its anonymizer shares this service's caches."""
        return dataclasses.replace(
            self._component_factory, build_anonymizer=self.build_cached_anonymizer
        )

    def run_preview_only(self, config: Config) -> int:
        return self._pipeline_factory(config).run_preview_only()

    # ── Queries ───────────────────────────────────────────────────────────

    def collect_input_files(self, config: Config) -> list[Path]:
        """Return supported source files for the current config."""
        return self._discovery.collect_input_files(config)

    def load_views(self, config: Config, files: list[Path]) -> dict[str, DocumentStateView]:
        """Return the views of *files*, with the texts each document has now."""
        store = self._state_store(config)
        current = functools.cache(lambda: privacy_fingerprint(config))
        views: dict[str, DocumentStateView] = {}
        for file in files:
            state = store.load_valid(file)
            if state is None:
                continue
            view = self._merged(store.to_view(file, state), state)
            views[view.source_id] = self._sent_verdict(view, current)
        return views

    def load_views_reconciled(
        self, config: Config, files: list[Path]
    ) -> dict[str, DocumentStateView]:
        """Like load_views but marks stages not-done when their config hash has changed.

        The flags are worked out exactly as the run works them out
        (:func:`~teachers_teammate.infrastructure.workflow.cache_service.stage_hashes`), so the
        queue and the next run agree on what is stale and what the teacher's work keeps.
        """
        store = self._state_store(config)
        windows = self.resolved_windows(config)

        current_privacy = functools.cache(lambda: privacy_fingerprint(config))
        views: dict[str, DocumentStateView] = {}
        for file in files:
            state = store.load_valid(file)
            if state is None:
                continue
            view = reconciled_view(
                store,
                file,
                state,
                self._work_store.load(state.source_hash),
                expected_hashes(config, file, windows, self._model_store),
            )
            views[view.source_id] = self._sent_verdict(view, current_privacy)
        return views

    def queue_rows(self, config: Config, files: Sequence[Path]) -> list[QueueRow]:
        """Return what the queue shows for *files*, read from the store.

        One row per file, in the order given: the stage states the next run would find, and
        what the document has.  The texts themselves are not part of a row; an action that
        needs them reads the document (:meth:`load_view`), so the queue holds no copy that
        could drift from the store.
        """
        store = self._state_store(config)
        windows = self.resolved_windows(config)
        current_privacy = functools.cache(lambda: privacy_fingerprint(config))
        rows: list[QueueRow] = []
        for file in files:
            state = store.load_valid(file)
            if state is None:
                rows.append(QueueRow.empty(file))
                continue
            view = reconciled_view(
                store,
                file,
                state,
                self._work_store.load(state.source_hash),
                expected_hashes(config, file, windows, self._model_store),
            )
            stored = StoredStages(
                ocr=state.ocr_done,
                correction=state.correction_done,
                evaluation=state.evaluation_done,
            )
            rows.append(queue_row(self._sent_verdict(view, current_privacy), stored))
        return rows

    def queue_row(self, config: Config, source: Path) -> QueueRow:
        """Return what the queue shows for one file, read from the store."""
        return self.queue_rows(config, [source])[0]

    def worked_on_documents(
        self, config: Config, files: Collection[Path], *, run_proofreads: bool
    ) -> WorkedOn:
        """Return the documents of a run whose corrections or transcription the teacher worked on.

        Reads every file that was not read since it last changed, so call it from a background
        thread.  *run_proofreads* says whether the run makes a proofreading at all: without
        one it replaces nothing, so no stored proofreading needs a question and corrections that
        belong to no stored answer are not worth one.
        """
        from ..domain.freshness import correction_kept_over_settings  # noqa: PLC0415
        from ..domain.teacher_work import current_texts  # noqa: PLC0415

        store = self._state_store(config)
        expected_correction_hash = compute_correction_config_hash(
            config, self.resolved_windows(config)
        )

        kept_stale: list[Path] = []
        unanswered: list[Path] = []
        unbound: list[Path] = []
        transcribed: list[Path] = []
        worked_on_documents: list[Path] = []
        for file in files:
            try:
                work = self._work_store.load(compute_file_hash_cached(file))
            except OSError:
                continue
            if work is None:
                continue
            if work.transcription is not None:
                transcribed.append(file)
            corrections = work.corrections
            worked_on = corrections is not None and corrections.worked_on
            state = store.load_valid(file)
            if state is None:
                # No stored result: the corrections part applies to nothing.
                if run_proofreads and worked_on:
                    unbound.append(file)
                continue
            # Only the proofreading's freshness is asked about, so the other two hashes are
            # the stored ones: they are never different, and the file is not read for them.
            flags = stage_hashes(
                state,
                work,
                ExpectedHashes(
                    ocr_config_hash=state.ocr_config_hash,
                    correction_config_hash=expected_correction_hash,
                    eval_config_hash=state.eval_config_hash,
                    correction_enabled=config.correction_enabled,
                    evaluation_enabled=config.evaluation_enabled,
                ),
            )
            applies = current_texts(machine_texts(state), work).corrections_work is not None
            if applies and worked_on:
                worked_on_documents.append(file)
            if run_proofreads and correction_kept_over_settings(flags):
                kept_stale.append(file)
                if corrections is not None and corrections.kept_under != expected_correction_hash:
                    unanswered.append(file)
            elif run_proofreads and worked_on and not applies:
                unbound.append(file)
        return WorkedOn(
            kept_stale=tuple(kept_stale),
            unanswered=tuple(unanswered),
            unbound=tuple(unbound),
            transcribed=tuple(transcribed),
            worked_on=tuple(worked_on_documents),
        )

    def load_view(self, config: Config, source: Path) -> DocumentStateView | None:
        """Return the view of *source*, with the texts the document has now."""
        store = self._state_store(config)
        state = store.load_valid(source)
        if state is None:
            return None
        view = self._merged(store.to_view(source, state), state)
        return self._sent_verdict(view, lambda: privacy_fingerprint(config))

    def _merged(
        self, view: DocumentStateView, state: DocumentState, *, kept_over_settings: bool = False
    ) -> DocumentStateView:
        """Return *view* with the teacher's texts over the machine's."""
        work = self._work_store.load(state.source_hash)
        return merged_view(view, state, work, kept_over_settings=kept_over_settings)

    def check_document(self, config: Config, source: Path) -> CheckDocument | None:
        """Return everything the Check tab shows about *source*, or ``None`` without state.

        One query for the whole tab: the texts, the review aligned with them, the items the list
        numbers, the pages of the scan and how the results were sent.  The window asks for it
        once per document and once per stored edit, so nothing else assembles that state.
        """
        view = self.load_view(config, source)
        if view is None:
            return None
        return check_document_of(view)

    @staticmethod
    def _sent_verdict(
        view: DocumentStateView, current_privacy: Callable[[], str]
    ) -> DocumentStateView:
        """Add whether each stored result was made with other privacy settings than today's.

        The comparison is by settings, not by text: working out the text would need a spaCy
        pass per document.  So a changed pattern that hides nothing new in a document still
        says "other privacy settings" — the wording says *settings*, not *text*.

        *current_privacy* is called only when the view has a stored result, so a file without
        one costs no fingerprint.
        """
        if not view.sent:
            return view
        current = current_privacy()
        return dataclasses.replace(
            view,
            sent={
                stage: dataclasses.replace(
                    status, other_privacy_settings=status.record.privacy != current
                )
                for stage, status in view.sent.items()
            },
        )

    def list_providers(self) -> list[str]:
        """Return available LLM providers."""
        return list_providers()

    def get_provider_info(self, provider: str) -> dict:
        """Return provider metadata used for model picker defaults."""
        return get_provider_info(provider)

    def provider_key_missing(self, provider: str) -> bool:
        """Return whether *provider* needs an API key that is not set in the environment.

        Without it the service cannot be asked for its models, so the GUI says "no key"
        instead of starting a request that is certain to fail.
        """
        info = self.get_provider_info(provider)
        return bool(info.get("needs_api_key")) and not os.environ.get(str(info.get("env_key", "")))

    @staticmethod
    def _model_cache_key(provider: str, base_url: str = "") -> str:
        """Return the cache key for *provider*'s model list.

        Ollama URLs are normalised so ``"http://host:11434/"`` and
        ``"http://host:11434"`` — and an omitted URL, which means the default
        server — share one entry; otherwise an invalidation could miss the entry
        that was cached under the other spelling.
        """
        if provider == "ollama":
            from ..infrastructure.ollama_utils import normalize_ollama_url  # noqa: PLC0415

            return f"ollama:{normalize_ollama_url(base_url)}"
        return f"{provider}:{base_url}" if base_url else provider

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        """Return available models for a provider (result is TTL-cached per instance)."""
        return self.list_provider_models_checked(provider, base_url=base_url).models

    def list_provider_models_checked(self, provider: str, *, base_url: str = "") -> ModelListing:
        """Return the models of a provider and whether the list is live (TTL-cached).

        Only a live or by-design built-in answer is remembered; a failed one (no key, key
        refused, no connection) is asked again next time, so a key entered a moment later
        is noticed.
        """
        cached = self.get_cached_listing(provider, base_url=base_url)
        if cached is not None:
            return cached
        kwargs: dict[str, str] = {"base_url": base_url} if base_url else {}
        listing = list_provider_models_checked(provider, **kwargs)
        self._store_listing(provider, base_url, listing)
        return listing

    def _store_listing(self, provider: str, base_url: str, listing: ModelListing) -> None:
        """Cache *listing* when it is :attr:`ModelListing.cacheable`.

        An empty list means "unreachable" (or nothing downloaded yet): never cache it,
        or a server that was started a moment later would keep being reported as down.
        """
        if listing.cacheable:
            self._model_cache[self._model_cache_key(provider, base_url)] = (
                time.monotonic(),
                listing,
            )

    def get_cached_listing(self, provider: str, *, base_url: str = "") -> ModelListing | None:
        """Return the cached listing for *provider* if still within TTL, else ``None``."""
        entry = self._model_cache.get(self._model_cache_key(provider, base_url))
        if entry and (time.monotonic() - entry[0]) < self._MODEL_CACHE_TTL_S:
            return entry[1]
        return None

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        """Return cached model list for *provider* if still within TTL, else ``None``."""
        listing = self.get_cached_listing(provider, base_url=base_url)
        return listing.models if listing is not None else None

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        """Evict the cached model list for *provider* (forces re-fetch on next call).

        For Ollama this also forgets the model classifications of that server.
        """
        self._model_cache.pop(self._model_cache_key(provider, base_url), None)
        if provider == "ollama":
            self._recommendations.invalidate_classification(base_url)

    def _evict_ollama_model_list(self, normalized_url: str) -> None:
        """Drop only the cached Ollama model list (the recommender evicts the rest)."""
        self._model_cache.pop(self._model_cache_key("ollama", normalized_url), None)

    # ── Hardware profiles & model recommendations (delegated) ─────────────

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        """Return the hardware detection for the machine that runs Ollama."""
        return self._recommendations.detect_hardware(ollama_url, refresh=refresh)

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection:
        """Resolve the ``hardware_profile`` *setting* into a concrete detection."""
        return self._recommendations.resolve_hardware_profile(setting, ollama_url)

    def available_memory_gb(self) -> float | None:
        """Return the main memory free right now in GB, or ``None`` when it cannot be read.

        Read when the "My computer" line is drawn, never stored: it changes all the time and no
        model suggestion depends on it.  It is a quick query, but the GUI still asks for it on a
        background task, like every other probe of this computer.
        """
        from ..infrastructure import gpu_detector  # noqa: PLC0415

        return gpu_detector.detect_available_memory_gb()

    def cached_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection | None:
        """Resolve *setting* without probing this computer; ``None`` while it is unknown.

        The graphics probe runs ``nvidia-smi`` and the ROCm tools, so it must never happen on
        the GUI thread: a caller that gets ``None`` asks a background task for
        :meth:`detect_hardware` and asks again.
        """
        return self._recommendations.cached_hardware_profile(setting, ollama_url)

    def resolved_windows(self, config: Config) -> RunWindows:
        """Return the context windows a run with *config* uses on this computer.

        The same resolution the run itself uses, so a cache key computed here matches the
        one the run writes — once this computer has been probed.  It never probes itself:
        the GUI asks for the expected cache keys on its own thread, and the probe runs
        ``nvidia-smi`` and the ROCm tools.  While the computer is still unknown the base
        window is returned; a caller that needs the card-derived window calls
        :meth:`detect_hardware` in the background first and asks again
        (:meth:`cached_hardware_profile` says whether that is needed).
        """
        return resolve_run_windows(
            config, self.cached_hardware_profile(config.hardware_profile, config.ollama_url)
        )

    def context_choice(
        self, settings: Mapping[str, object], card_gb: float | None
    ) -> ContextChoice:
        """Return what the context setting changes on this computer, and what ``"auto"`` gives.

        The same rule the run uses (:func:`~teachers_teammate.domain.context_budget.run_windows`),
        so the Settings page cannot promise a window the run does not use.  *card_gb* comes from
        a detection the caller already has: this does no I/O, so it is safe on the GUI thread.
        """
        proofreading_provider = str(
            settings.get("correction_provider") or DEFAULTS["correction_provider"]
        )
        review_provider = str(settings.get("evaluate_provider") or DEFAULTS["evaluate_provider"])
        return context_choice(
            proofreading_provider=proofreading_provider,
            proofreading_model=self.effective_model(
                proofreading_provider, str(settings.get("correction_model") or ""), "correction"
            ),
            review_provider=review_provider,
            review_model=self.effective_model(
                review_provider, str(settings.get("evaluate_model") or ""), "evaluation"
            ),
            card_gb=card_gb,
        )

    def effective_model(self, provider: str, model: str, task: str) -> str:
        """Return the model a run uses for *task*: an empty *model* means the provider's default."""
        return model.strip() or self.default_model_for_task(provider, task)

    def sends_text_online(self, settings: Mapping[str, object]) -> bool:
        """Return whether a run with *settings* sends text to an online service.

        Only the steps that run count (proofreading on, and the content review on top of it),
        each with the model it really uses; the same steps and the same classification as the
        data-flow box (:meth:`describe_data_flow`), but from settings, so a page can ask
        before anything is saved.
        """
        return text_goes_online(settings, self.describe_llm_location, self.effective_model)

    def ollama_catalog(self) -> tuple[OllamaModelSpec, ...]:
        """Return the curated Ollama model catalog."""
        return self._recommendations.ollama_catalog()

    def best_ollama_model(
        self,
        category: ModelCategory,
        profile: HardwareProfile,
        *,
        system_memory_gb: float | None = None,
        card_gb: float | None = None,
    ) -> str:
        """Return the BEST Ollama model tag for *category* on this computer."""
        return self._recommendations.best_ollama_model(
            category, profile, system_memory_gb=system_memory_gb, card_gb=card_gb
        )

    def classify_ollama_models(
        self, tags: list[str], ollama_url: str, *, cached_only: bool = False
    ) -> dict[str, ModelUses | None]:
        """Return ``{tag: "ocr" | "general" | None}``; ``cached_only`` never contacts Ollama."""
        return self._recommendations.classify_ollama_models(
            tags, ollama_url, cached_only=cached_only
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
    ) -> list[ModelRating]:
        """Return a :class:`ModelRating` per tag for *task* on *profile*.

        The ratings carry the speed band a model is expected to reach here, worked out from
        the models this server has measured; a model that was never measured anywhere gets
        none.
        """
        return self._recommendations.rate_ollama_models(
            tags,
            task,
            profile,
            ollama_url,
            cached_only=cached_only,
            system_memory_gb=system_memory_gb,
            card_gb=card_gb,
            machine_factor=self._speeds.machine_factors(ollama_url).for_category(task),
        )

    def first_run_model_settings(
        self,
        profile: HardwareProfile,
        *,
        system_memory_gb: float | None = None,
        card_gb: float | None = None,
    ) -> dict[str, str]:
        """Return the first-start model settings (BEST picks) for *profile*."""
        return self._recommendations.first_run_model_settings(
            profile, system_memory_gb=system_memory_gb, card_gb=card_gb
        )

    def on_ollama_models_changed(self, ollama_url: str) -> None:
        """Evict the model list and classifications of *ollama_url* (call after a pull)."""
        self._recommendations.on_ollama_models_changed(ollama_url)
        # A pull changes the digest, which is what a stored measurement is keyed by.
        self._speeds.forget_server_facts(ollama_url)

    # ── Speed test and time estimates (delegated) ─────────────────────────

    def speed_choices(self, settings: Mapping[str, object]) -> SpeedChoices:
        """Return the models *settings* name, as the speed test and the estimate need them.

        The models are resolved the way a run resolves them, so the page tests what a run would
        use rather than what an empty field happens to mean.
        """
        return SpeedChoices.from_settings(settings, effective_model=self.effective_model)

    def speed_choices_for_run(self, config: Config) -> SpeedChoices:
        """Return the models a run with *config* uses: only the steps that run, on Ollama.

        What the time left of a run is worked out from, so a run that only reads the text, or
        proofreads without a content review, is not charged for the steps it skips.
        """
        return SpeedChoices.from_config(config, effective_model=self.effective_model)

    def refresh_speed_server_facts(self, ollama_url: str) -> None:
        """Fetch the server's version and model digests (background thread only)."""
        self._speeds.refresh_server_facts(ollama_url)

    def test_speed(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cancel_event: threading.Event | None = None,
        on_progress: ProgressFn | None = None,
    ) -> ProbeResult:
        """Run the speed test for *choices* and store the result (background thread only).

        *on_progress* hears of each step as it starts (from the test's thread).
        """
        return self._speeds.test_speed(
            choices, hardware=hardware, cancel_event=cancel_event, on_progress=on_progress
        )

    def speed_summary(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cached_only: bool = True,
    ) -> SpeedSummary:
        """Return what the speed panel shows for *choices*; ``cached_only`` never waits."""
        return self._speeds.speed_summary(choices, hardware=hardware, cached_only=cached_only)

    def speed_machine_factors(self, ollama_url: str) -> MachineFactors:
        """Return how much slower this computer is than the reference machine, per rate."""
        return self._speeds.machine_factors(ollama_url)

    def speed_page_times(
        self,
        tags: Sequence[str],
        category: ModelCategory,
        ollama_url: str,
        *,
        hardware: HardwareDetection | None = None,
    ) -> dict[str, TimeRange]:
        """Return the measured seconds per page of *tags*, for the model lists' tooltips."""
        return self._speeds.page_times(tags, category, ollama_url, hardware=hardware)

    def default_model_for_task(self, provider: str, task: str) -> str:
        """Resolve default model for provider and task."""
        return default_model_for_task(provider, task)

    def list_ocr_engines(self) -> list[str]:
        """Return supported OCR engine identifiers."""
        return list(SUPPORTED_OCR_ENGINES)

    def list_supported_suffixes(self) -> frozenset[str]:
        """Return input file suffixes accepted by the pipeline."""
        return SUPPORTED_SUFFIXES

    def proofreading_prompts(self, config: Config) -> tuple[tuple[str, str], ...]:
        """Return ``(step title, system prompt)`` for the steps this config sends.

        Taken from the exchange itself, so the *Options…* dialog and *What is sent* show the
        requests a run really makes and cannot drift from them.  *Corrections only* leaves
        step ① alone; a model without the unclear check leaves step ③ out.
        """
        from ..infrastructure.proofreading.corrector import exchange_prompts  # noqa: PLC0415

        steps = exchange_prompts(
            config.correction_profile,
            corrections_only=config.correction_output == "text",
        )
        return tuple((prompt_step_title(step), text) for step, text in steps)

    def evaluation_fixed_instructions(self, text_format: TextFormat = "plain") -> str:
        """Return what the app appends to an evaluation prompt: the notation hint, if any.

        Markdown is the only format with a hint, so for plain text this is ``""``.
        """
        from ..domain.notation import with_notation_hint  # noqa: PLC0415

        return with_notation_hint("", text_format, task="evaluation").strip()

    def collect_input_files_from_dir(self, input_dir: Path, recursive: bool) -> list[Path]:
        """Collect supported input files without a full Config (for partial GUI state)."""
        return self._discovery.collect_input_files_from_dir(input_dir, recursive)

    def preprocess_preview(
        self,
        source_path: Path,
        ocr: OcrConfig,
        tmp_dir: Path,
    ) -> tuple[Path, Path, list[str]]:
        """Run preprocessing preview for one source file."""
        return preprocess_preview(
            source_path,
            ocr,
            tmp_dir,
            self._component_factory.build_preprocessor,
            self._model_store,
        )

    def warm_up_anonymizer(self, language: str, config: object | None = None) -> None:
        """Load (and cache) the anonymizer for *language* so a missing model shows up early.

        Raises:
            ImportError: spacy is not installed.
            OSError: a required spacy language model is not downloaded.
        """
        with self._anonymizer_lock:
            self._anonymizer(language, config)

    def _anonymizer(
        self, language: str, config: object | None
    ) -> tuple[SpacyAnonymizer, AnonymizerConfig]:
        """Return the cached anonymizer and its resolved settings; callers hold the lock."""
        from ..infrastructure.anonymizer import SpacyAnonymizer  # noqa: PLC0415

        anon_config = self.resolve_anonymizer_config(language, config)
        cache_key = (language, anon_config)
        cached = self._anonymizer_cache.get(cache_key)
        if cached is None:
            cached = SpacyAnonymizer(language, anon_config)
            self._anonymizer_cache[cache_key] = cached
        return cached, anon_config

    def build_cached_anonymizer(self, language: str, config: AnonymizerConfig) -> Anonymizer:
        """Return an anonymizer backed by this service's model and result caches.

        Passed into a run as ``build_anonymizer``, so a pipeline, a privacy preview and a text
        export share one result: the same text costs one spaCy pass per session and settings.

        Args:
            language: Document language — selects the primary spaCy model.
            config: The anonymizer settings; ``"auto"`` models are resolved here.
        """
        with self._anonymizer_lock:
            inner, anon_config = self._anonymizer(language, config)
        return CachedAnonymizer(
            inner,
            self._anonymization_cache,
            (language, anon_config),
            self._anonymizer_lock,
        )

    def _anonymize(
        self, text: str, language: str, config: object | None
    ) -> tuple[str, dict[str, str]]:
        with self._anonymizer_lock:
            inner, anon_config = self._anonymizer(language, config)
            cached = CachedAnonymizer(
                inner,
                self._anonymization_cache,
                (language, anon_config),
                self._anonymizer_lock,
            )
            return cached.anonymize(text)

    def anonymize_preview(
        self,
        text: str,
        language: str,
        config: object | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Anonymize *text* using NER + configured regex patterns.

        Args:
            text: Raw text to anonymize.
            language: Document language — selects the primary spaCy model.
            config: :class:`AnonymizerConfig` controlling the optional secondary
                NER model and regex patterns.  ``None`` uses the default config.

        Raises:
            ImportError: spacy is not installed.
            OSError: a required spacy language model is not downloaded.
        """
        return self._anonymize(text, language, config)

    def clear_anonymizer_cache(self) -> None:
        """Evict the cached anonymizers and with them every anonymized text."""
        with self._anonymizer_lock:
            self._anonymizer_cache.clear()
            self._anonymization_cache.clear()
        self.forget_installed_models()

    def reread_installed_models(self) -> None:
        """Ask spaCy again which models are installed, without forgetting what was warned."""
        self._installed_models = None

    def forget_installed_models(self) -> None:
        """Ask spaCy again which models are installed (after a download or an install).

        Also lets a warning be printed again: what it said may have changed with the install.
        """
        self.reread_installed_models()
        self._warned.clear()

    def export_docx(
        self,
        config: Config,
        source: Path,
        out_path: Path,
        *,
        options: DocxExport | None = None,
    ) -> None:
        """Write the Word file of *source* to *out_path*, from the texts it has now.

        The texts, their notation and the review come from the view, so an export carries what
        the teacher sees — their transcription and their corrections — and never the machine's
        texts behind them.  *options* selects the layout and the language of the headings.

        Raises:
            ValueError: the document has no text to export.
        """
        from ..infrastructure.docx_builder import DocxDocumentCreator  # noqa: PLC0415

        view = self.load_view(config, source)
        if view is None or not view.raw_text.strip():
            raise ValueError(NO_TEXT_MESSAGE)
        chosen = options or DocxExport()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        creator = DocxDocumentCreator(
            fmt=chosen.docx_format, labels_language=chosen.labels_language
        )
        creator.create(
            view.raw_text,
            view.correction_text,
            out_path,
            source.stem,
            source_image=view.preview_img or None,
            text_format=TextFormatter.normalize(view.raw_text_format),
            review=view.correction_review,
        )

    def text_for_copy(
        self,
        view: DocumentStateView,
        *,
        corrected: bool = False,
        anonymize: bool = False,
        language: str = "English",
        anonymizer_config: object | None = None,
    ) -> str:
        """Return the text of *view* as plain text, the way a text export writes it.

        One function for the clipboard and for the saved file, so a copy and an export of the
        same document never differ.  *corrected* takes the proofread text instead of the
        transcription.  With *anonymize* the personal data is replaced by placeholders
        (``[PERSON_1]``, ...) on the text in its stored notation, as the pipeline and the
        privacy preview do.

        Raises:
            ImportError: *anonymize* is set and spacy is not installed.
            OSError: a required spacy model is not downloaded.
        """
        text = view.correction_text if corrected else view.raw_text
        if anonymize:
            text, _mapping = self.anonymize_preview(text, language, anonymizer_config)
        return TextFormatter.plain_text(text, view.raw_text_format)

    def export_text(
        self,
        config: Config,
        source: Path,
        out_path: Path,
        *,
        anonymize: bool = False,
        language: str = "English",
        anonymizer_config: object | None = None,
        protected: Collection[Path] = (),
    ) -> None:
        """Write the transcription of *source* to *out_path* as a UTF-8 text file.

        The text and its notation come from the view, so the file carries the teacher's
        transcription where they fixed or checked it.  Markdown is written as plain text.  With
        *anonymize* the personal data is replaced by placeholders (``[PERSON_1]``, ...) using
        the local anonymizer, on the text in its stored notation (as the pipeline and the
        privacy preview do); the placeholder mapping is dropped, so the original names cannot
        be recovered from the file.

        Args:
            protected: Further documents that must not be overwritten (the other inputs of
                the same export); *source* itself is always protected.

        Raises:
            ValueError: the document has no text, or *out_path* is *source* or one of
                *protected* (an input document).
            ImportError: *anonymize* is set and spacy is not installed.
            OSError: the file cannot be written, or a required spacy model is not downloaded.
        """
        target = out_path.resolve()
        if target == source.resolve() or target in {p.resolve() for p in protected}:
            raise ValueError(
                f"{out_path.name} is an input document and would be overwritten; "
                "choose another name or folder."
            )
        view = self.load_view(config, source)
        if view is None or not view.raw_text.strip():
            raise ValueError(NO_TEXT_MESSAGE)
        text = self.text_for_copy(
            view,
            anonymize=anonymize,
            language=language,
            anonymizer_config=anonymizer_config,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")

    @staticmethod
    def is_in_input_folder(folder: Path, input_dir: Path, *, recursive: bool) -> bool:
        """Return whether files written to *folder* would be found as inputs of *input_dir*."""
        return feeds_inputs(folder.resolve(), input_dir.resolve(), recursive=recursive)

    @staticmethod
    def export_target(source: Path, input_dir: Path | None, folder: Path, suffix: str) -> Path:
        """Return where a folder export saves *source*: the subfolder it has below
        *input_dir*, recreated below *folder* (so same-named files in different subfolders
        do not replace each other).
        """
        return output_path(
            source.resolve(), input_dir.resolve() if input_dir else None, folder, suffix
        )

    def resolve_preview_tmp_dir(self) -> Path:
        """Return a fresh unique temporary directory for preview preprocessing."""
        root = resolve_storage_root().path
        tmp = root / "preview_tmp" / f"pp_preview_{uuid.uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        return tmp

    def check_connection(
        self,
        *,
        engine: str = "",
        provider: str = "",
        url: str = "http://127.0.0.1:11434",
        model: str = "",
    ) -> tuple[bool, bool, str]:
        """Check whether an OCR engine or LLM provider is reachable.

        Returns ``(connected, model_ok, message)``.
        For non-Ollama services the tuple is ``(ok, ok, message)`` because
        the model-level distinction does not apply.
        """
        if engine:
            return self._check_engine(engine, url, model=model)
        if provider:
            return self._check_provider(provider, url, model=model)
        return False, False, "Nothing to check."

    def discover_ollama_servers(
        self,
        scope: DiscoveryScope = DiscoveryScope.THIS_COMPUTER,
        *,
        stop_event: threading.Event | None = None,
        on_found: Callable[[DiscoveredServer], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[DiscoveredServer]:
        """Return the Ollama servers that answer, searching as far as *scope* says.

        ``THIS_COMPUTER`` asks this computer and, inside WSL, the Windows computer behind it:
        a few addresses, quick and quiet.  ``NETWORK`` also asks the private network around
        this computer (up to 254 addresses per network card): the caller must only ask for it
        when the person did.  Blocking network calls: run it on a background thread.  Changes
        no setting; *on_found* / *on_progress* are called from worker threads.
        """
        from ..infrastructure import ollama_discovery  # noqa: PLC0415

        found = ollama_discovery.discover_this_computer()
        if on_found is not None:
            for server in found:
                on_found(server)
        if scope is DiscoveryScope.NETWORK:
            known = {server.url for server in found}
            found += [
                server
                for server in ollama_discovery.scan_network(
                    stop_event=stop_event, on_found=on_found, on_progress=on_progress
                )
                if server.url not in known
            ]
        return found

    def _check_ollama(self, url: str, model: str = "") -> tuple[bool, bool, str]:
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        return OllamaClient(url).check_connection(model)

    def _check_engine(self, engine: str, url: str, *, model: str = "") -> tuple[bool, bool, str]:
        if engine == "ollama":
            return self._check_ollama(url, model)
        if engine == ENGINE_PPOCR:
            if self.reader_installed():
                return True, True, "✓ built-in reader ready"
            return False, False, f"✗ built-in reader not downloaded ({READER_SIZE_LABEL})"
        return True, True, "✓ OK"

    def _check_provider(
        self, provider: str, url: str, *, model: str = ""
    ) -> tuple[bool, bool, str]:
        if provider == "ollama":
            return self._check_ollama(url, model)
        # Unknown providers are not blocked — assume OK by default.
        if provider not in list_providers():
            return True, True, "✓ OK"
        # Delegate to the provider module's own health check (env-key presence
        # for API providers). The provider declares its own env_key, so no
        # provider→env-var mapping is duplicated here.
        ok, message = check_provider_connection(provider)
        return ok, ok, message

    def list_ollama_model_sizes(self, url: str = "") -> dict[str, int]:
        """Return ``{model_name: size_bytes}`` for models installed in Ollama."""
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        return dict(OllamaClient(url or "http://127.0.0.1:11434").list_models_with_size())

    @staticmethod
    def _ollama_needed(config: Config) -> bool:
        """Return whether any enabled stage of *config* talks to Ollama."""
        return (
            (config.recognition_enabled and config.ocr.engine == "ollama")
            or (config.correction_enabled and config.correction_provider == "ollama")
            or (config.evaluation_enabled and config.evaluate_provider == "ollama")
        )

    @staticmethod
    def configured_ollama_models(config: Config) -> list[str]:
        """Return the deduplicated Ollama models the enabled stages of *config* use."""
        models: list[str] = []
        if config.recognition_enabled and config.ocr.engine == "ollama" and config.ocr.model:
            models.append(config.ocr.model)
        if config.correction_enabled and config.correction_provider == "ollama":
            model = config.effective_correction_model or ""
            if model and model not in models:
                models.append(model)
        if config.evaluation_enabled and config.evaluate_provider == "ollama":
            model = config.effective_evaluate_model or ""
            if model and model not in models:
                models.append(model)
        return models

    def _installed_ollama_models(self, url: str) -> tuple[list[str], str]:
        """Return ``(installed, unreachable)`` for the Ollama server at *url*.

        *unreachable* is ``""`` when the server answered — also when it has no model
        downloaded yet — and the connection message otherwise.  Makes at most one
        request (none within the model-cache TTL), so an unreachable server costs a
        single timeout instead of one per question asked of it.
        """
        cached = self.get_cached_models("ollama", base_url=url)
        if cached is not None:
            return list(cached), ""
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        installed, unreachable = OllamaClient(url).list_models_or_error()
        self._store_listing("ollama", url, ModelListing(installed))
        return installed, unreachable

    def _ollama_issue(self, url: str, message: str) -> RequirementIssue:
        """Return what is wrong with Ollama at *url*: not found, not running, or unreachable.

        A local address that does not answer is told apart by looking for the program: a
        teacher who has just installed the app needs to know whether to install Ollama or to
        start it.  An address on another computer can only be unreachable.
        """
        from ..infrastructure.ollama_install import (  # noqa: PLC0415
            can_start_ollama,
            find_ollama_program,
        )
        from ..infrastructure.ollama_utils import is_local_ollama_url  # noqa: PLC0415

        if not is_local_ollama_url(url, include_wsl_host=True):
            return RequirementIssue(message, IssueKind.OLLAMA_UNREACHABLE)
        if find_ollama_program() is None:
            return RequirementIssue(message, IssueKind.OLLAMA_NOT_FOUND)
        return RequirementIssue(message, IssueKind.OLLAMA_NOT_RUNNING, startable=can_start_ollama())

    def ollama_status(self, url: str) -> OllamaStatus:
        """Return whether Ollama answers at *url*, and what the app can do about it.

        One probe; the program is looked for only when nothing answered.  Blocking network
        call: run it on a background thread.
        """
        from ..infrastructure import ollama_discovery  # noqa: PLC0415
        from ..infrastructure.ollama_install import (  # noqa: PLC0415
            can_start_ollama,
            find_ollama_program,
        )
        from ..infrastructure.ollama_utils import (  # noqa: PLC0415
            is_local_ollama_url,
            normalize_ollama_url,
        )

        normalized = normalize_ollama_url(url)
        info = ollama_discovery.probe_server(normalized)
        program = None if info is not None else find_ollama_program()
        state = ollama_state(
            answered=info is not None,
            local_address=is_local_ollama_url(normalized, include_wsl_host=True),
            program_found=program is not None,
        )
        return OllamaStatus(
            state=state,
            version=info.version if info is not None else "",
            model_count=info.model_count if info is not None else None,
            can_start=state is OllamaState.NOT_RUNNING and can_start_ollama(),
        )

    def start_ollama(self) -> None:
        """Start the Ollama program on this computer (background thread only).

        Does nothing when no program was found, or when the platform has no way to start it
        (on Linux the server is a system service).
        """
        from ..infrastructure.ollama_install import (  # noqa: PLC0415
            can_start_ollama,
            find_ollama_program,
            start_ollama,
        )

        program = find_ollama_program()
        if program is None or not can_start_ollama():
            return
        start_ollama(program)

    # ── Setup Assistant ───────────────────────────────────────────────────────

    def setup_facts(self, answers: SetupAnswers, current: Mapping[str, Any]) -> SetupFacts:
        """Gather what the setup plan needs to know about this computer.

        Blocking: it probes Ollama, reads the disk and asks spaCy.  Run it on a background
        thread.  *current* is the settings as they are now, including the main window's
        ``anonymization_enabled``.
        """
        from ..infrastructure.ollama_install import (  # noqa: PLC0415
            free_disk_gb,
            ollama_models_dir,
        )
        from ..infrastructure.ollama_utils import is_local_ollama_url  # noqa: PLC0415

        url = answers.ollama_url
        running = self.ollama_status(url).state is OllamaState.RUNNING
        hardware = self.resolve_hardware_profile(answers.hardware_profile, url)
        local = is_local_ollama_url(url, include_wsl_host=True)
        return SetupFacts(
            profile=hardware.profile,
            system_memory_gb=hardware.system_memory_gb,
            card_gb=hardware.card_gb,
            ollama_running=running,
            installed_tags=(
                frozenset(self.list_provider_models("ollama", base_url=url))
                if running
                else frozenset()
            ),
            free_disk_gb=free_disk_gb(ollama_models_dir()) if local else None,
            removal_on=bool(current.get("anonymization_enabled")),
            name_detection=self.anonymizer_available(answers.language),
            spacy_installed=self.is_module_importable("spacy"),
            spacy_model=self.spacy_model_for_language(answers.language),
            installed_spacy_models=frozenset(self.list_installed_spacy_models()),
            reader_installed=self.reader_installed(),
            page_models_installed=self.page_models_installed(),
            missing_package=(
                self.missing_provider_package(answers.online_choice[0]) or ""
                if answers.proofreading is ProofreadingPlace.ONLINE
                else ""
            ),
        )

    def plan_setup(self, answers: SetupAnswers, current: Mapping[str, Any]) -> SetupPlan:
        """Return the setup plan for *answers*: the settings to write and the downloads to run.

        Blocking, like :meth:`setup_facts`: run it on a background thread.
        """
        facts = self.setup_facts(answers, current)
        return build_setup_plan(answers, current, facts, **self._plan_tools(answers, facts))

    def plan_with_model_choices(
        self, answers: SetupAnswers, current: Mapping[str, Any]
    ) -> tuple[SetupPlan, LocalModelChoices]:
        """Return the setup plan and the models the assistant offers for the proofreading.

        The models are the installed ones and the catalog ones that can work on the computer
        Ollama runs on, best first, with the one the setup plan uses when the teacher leaves the
        choice to the assistant.  One call measures the facts once for both.  Blocking, like
        :meth:`plan_setup`: run it on a background thread.
        """
        facts = self.setup_facts(answers, current)
        tools = self._plan_tools(answers, facts)
        plan = build_setup_plan(answers, current, facts, **tools)
        step = next((row for row in plan.steps if row.stage == "correction"), None)
        choices = build_model_choices(
            facts, suggested=step.model if step is not None else "", rate=tools["rate"]
        )
        return plan, choices

    def _plan_tools(self, answers: SetupAnswers, facts: SetupFacts) -> dict[str, Any]:
        """Return the rating, recommendation and default-model functions the setup plan is built with."""

        def rate(tag: str, task: ModelCategory) -> ModelRating:
            return self.rate_ollama_models(
                [tag],
                task,
                facts.profile,
                answers.ollama_url,
                # Blocking is fine here (a background thread): an installed model outside the
                # catalog is classified by asking the server, and one that stays unrated would
                # be replaced although it works.
                cached_only=False,
                system_memory_gb=facts.system_memory_gb,
                card_gb=facts.card_gb,
            )[0]

        def best(task: ModelCategory) -> str:
            return self.best_ollama_model(
                task,
                facts.profile,
                system_memory_gb=facts.system_memory_gb,
                card_gb=facts.card_gb,
            )

        return {"rate": rate, "best": best, "default_model": self.default_model_for_task}

    def describe_data_flow(self, config: Config) -> DataFlow:
        """Return where each enabled step of *config* runs (this computer, another, online)."""
        return describe_data_flow(
            config,
            self.get_provider_info,
            self.get_provider_data_region,
            self.describe_anonymizer_models(config),
        )

    def describe_steps(self, config: Config) -> DataFlow:
        """Return where every step would run if it were ticked, whatever *config* ticks.

        The main window shows the model and place of each step even while it is unticked, so
        the teacher sees what ticking it would use.  Asking here keeps the GUI from deciding
        a place itself.
        """
        return self.describe_data_flow(
            dataclasses.replace(
                config,
                recognition_enabled=True,
                correction_enabled=True,
                evaluation_enabled=True,
            )
        )

    def get_provider_data_region(self, provider: str) -> str:
        """Return where *provider* processes the data: ``"eu"``, ``"non_eu"`` or ``"unknown"``."""
        return get_provider_data_region(provider)

    def anonymizer_models(self, language: str, config: object | None = None) -> list[str]:
        """Return the spaCy models personal-info removal loads for *language* and *config*.

        Resolved the same way a run resolves them, so a chosen primary model counts and an
        automatic extra model that is not downloaded is left out.  No warning: this answers
        "would it work?", and the run itself warns when a model is missing.
        """
        from ..infrastructure.anonymizer import resolve_anonymizer_config  # noqa: PLC0415
        from ..infrastructure.spacy_models import spacy_model_for  # noqa: PLC0415
        from ..infrastructure.system_locale import system_language  # noqa: PLC0415

        anon_config: AnonymizerConfig = (
            config if isinstance(config, AnonymizerConfig) else AnonymizerConfig()
        )
        resolved = resolve_anonymizer_config(
            anon_config,
            document_language=language,
            system_language=system_language(),
            installed_models=self._remembered_installed_models(),
        )
        models = [resolved.primary_model or spacy_model_for(language)]
        if resolved.secondary_model:
            models.append(resolved.secondary_model)
        return models

    def describe_anonymizer_models(self, config: Config) -> list[str]:
        """Return the spaCy models personal-info removal loads for *config*, or ``[]`` when off.

        Resolved the same way a run resolves them, so the status box names the models a run
        would really use.  A missing automatic model is not reported here: the box only
        says what runs, and the run itself warns.
        """
        if not config.anonymization_enabled:
            return []
        from ..infrastructure.anonymizer import anonymizer_config_from  # noqa: PLC0415

        return self.anonymizer_models(config.language, anonymizer_config_from(config))

    def describe_sent_texts(self, config: Config, view: DocumentStateView) -> SentTexts:
        """Return what every step that leaves this computer receives, exactly as sent.

        Uses the shared anonymizer (and so its cache), so opening the dialog costs one spaCy
        pass per text and settings for the whole session — the same result a run sends.  The
        prompts are built with the code the stages use, so the dialog cannot drift from them.

        Args:
            config: The live settings; decides the steps and the prompts.
            view: The document as it is now: the texts a run would send, and how the results
                that are already stored were sent, so the dialog can say what really went out
                next to what would go next.
        """
        flow = self.describe_data_flow(config)
        return describe_sent_texts(
            flow,
            raw_text=view.raw_text,
            correction_text=view.correction_text,
            anonymize=self._sent_text_anonymizer(config),
            preview_extracted=config.anonymization_enabled,
            correction_prompt=self._sent_correction_prompt(config),
            evaluation_prompt=self._sent_evaluation_prompt(
                config, normalize_text_format(view.raw_text_format)
            ),
            stored=view.sent,
        )

    def _sent_text_anonymizer(self, config: Config) -> Anonymize | None:
        """Return the anonymizer for this config — the shared, caching one — or ``None``.

        ``None`` when personal-info removal is off: nothing is hidden then, so the dialog
        must show the text as written.
        """
        from ..infrastructure.anonymizer import anonymizer_config_from  # noqa: PLC0415

        if not config.anonymization_enabled:
            return None
        anon_config = anonymizer_config_from(config)
        language = config.language
        return lambda text: self.anonymize_preview(text, language, anon_config)

    def _sent_correction_prompt(self, config: Config) -> str:
        """Return the instructions the corrector sends, or ``""`` when it sends none.

        One heading per step that runs, then the teacher's notes when there are any: the
        prompts are fixed, so this is the whole of what the model is told.
        """
        from ..domain.correction_prompts import notes_block  # noqa: PLC0415

        parts = [f"{title}\n\n{prompt}" for title, prompt in self.proofreading_prompts(config)]
        notes = notes_block(config.correction_notes).strip()
        if notes:
            parts.append(notes)
        return "\n\n".join(parts)

    def _sent_evaluation_prompt(self, config: Config, text_format: TextFormat) -> str:
        """Return the instructions the evaluator sends, or ``""`` when it sends none."""
        from ..domain.notation import with_notation_hint  # noqa: PLC0415

        prompt = config.evaluate_prompt or DEFAULTS["evaluate_prompt"]
        return with_notation_hint(prompt, text_format, task="evaluation")

    def describe_ocr_location(self, engine: str, model: str, ollama_url: str) -> StageLocation:
        """Return where text recognition with these settings runs."""
        return ocr_location(engine, model, ollama_url)

    def describe_ocr_refusal(self, engine: str, model: str, ollama_url: str) -> str:
        """Return why text recognition with these settings is refused, or ``""`` when allowed."""
        return ocr_refusal(engine, model, ollama_url)

    def list_ocr_models(self, base_url: str = "") -> list[str]:
        """Return the Ollama models usable for text recognition (cloud models are left out)."""
        return [
            model
            for model in self.list_provider_models("ollama", base_url=base_url)
            if not is_ollama_cloud_model(model)
        ]

    def describe_llm_location(self, provider: str, model: str, ollama_url: str) -> StageLocation:
        """Return where proofreading / content review with *provider* and *model* runs."""
        return llm_location(provider, model, ollama_url, self.get_provider_info)

    @staticmethod
    def ocr_refusal_for_run(config: Config) -> str:
        """Return why this run must not start, or ``""`` when text recognition is allowed.

        Needs no network access.  Settings that would read pages online are refused for
        every run, whatever files it holds: the fix is one model choice, and it keeps the
        rule independent of which files happen to need recognition.
        """
        if not config.recognition_enabled:
            return ""  # no page is read, so what would read it does not matter
        return ocr_refusal(config.ocr.engine, config.ocr.model, config.ollama_url)

    def page_models_notice_for_run(self, config: Config) -> str:
        """Return the one line a run prints when it skips turning and flattening, else ``""``.

        That is a run that reads pages with the automatic preparation while the page models
        are not downloaded.  It is a notice, never a refusal: the run does not wait for them.
        """
        if (
            config.recognition_enabled
            and config.ocr.preprocess_method == "auto"
            and not self.page_models_installed()
        ):
            return PAGE_MODELS_SKIPPED_NOTICE
        return ""

    def reader_refusal_for_run(self, config: Config) -> str:
        """Return why the built-in reader cannot start this run, or ``""`` when it can.

        Only a run that reads pages with the reader and has no model files is refused; the
        sentence names the command line flag that downloads them.  A run over files that need
        no reading (text files, text that is already recognised) is not: the model files are
        only opened when a page is read, as in the main window
        (see :meth:`needs_text_recognition`).
        """
        if not (
            config.recognition_enabled
            and config.ocr.engine == ENGINE_PPOCR
            and not self.reader_installed()
        ):
            return ""
        files = self.collect_input_files(config)
        if not self.needs_text_recognition(files, recognised=self._recognised_files(config, files)):
            return ""
        return READER_NOT_DOWNLOADED

    def _recognised_files(self, config: Config, files: list[Path]) -> set[Path]:
        """Return the *files* whose recognised text is already stored (best effort)."""
        try:
            views = self.load_views(config, files)
        except Exception:  # noqa: BLE001  # a lookup that fails only means "read it again"
            return set()
        return {
            file for file in files if (view := views.get(str(file.resolve()))) and view.ocr_done
        }

    @staticmethod
    def output_refusal_for_run(config: Config) -> str:
        """Return why this run's output folder must not be used, or ``""`` when it is fine.

        Text files saved where the run looks for inputs would be read as new documents
        the next time.
        """
        return output_folder_refusal(
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

    def plan_recognition(
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
        memory: bool = True,
        recognition: bool = True,
        fresh: bool = False,
        files: Collection[Path] | None = None,
        recognised: Collection[Path] = (),
        ask_about_work: bool = False,
    ) -> RunPreflight:
        """Return the unmet requirements before a run, or an all-clear result.

        With text recognition switched off and *files* given, the result also lists the files
        the run leaves out (``skipped``), and is blocked (``nothing_to_read``) when none of
        them can be used.  Such a check opens PDFs, see :meth:`plan_recognition`.  A document
        whose transcription the teacher stored is read from that, so it is not left out.

        *ask_about_work* adds ``worked_on``: the documents whose corrections or transcription the
        teacher worked on, for the question before a run.  It reads every file that was not
        read since it last changed, so only the check that starts a run asks for it.
        """
        skipped: tuple[Path, ...] = ()
        if not config.recognition_enabled and files:
            plan = self.plan_recognition(
                files,
                recognised={*recognised, *self._with_stored_transcription(files)},
                pdf_text_layer=config.ocr.pdf_text_layer,
            )
            if not plan.readable:
                return RunPreflight(nothing_to_read=True, skipped=plan.skipped)
            skipped = plan.skipped
        result = self._check_requirements(
            config, memory=memory, recognition=recognition, fresh=fresh
        )
        if skipped:
            result = dataclasses.replace(result, skipped=skipped)
        if ask_about_work and files and not result.blocked:
            # The question about the teacher's work belongs to a run that will start.
            result = dataclasses.replace(
                result,
                worked_on=self.worked_on_documents(
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
        first).  ``memory=False`` skips the memory warning: the readiness line only asks
        whether a run can start, and the question about free memory belongs to Run.
        ``recognition=False`` skips the check of the text recognition engine for a run that
        reads no page (only text files, or text that is already recognised): a reader that is
        not downloaded must not stop it (see :meth:`needs_text_recognition`).  ``fresh=True``
        asks the Ollama server again instead of using the list it gave within the last minutes:
        for a teacher who just ran ``ollama pull`` in a terminal and pressed "Check again".
        """
        ocr_reason = self.ocr_refusal_for_run(config)
        if ocr_reason:
            return RunPreflight(ocr_not_allowed=ocr_reason)
        if self._ollama_needed(config):
            from ..infrastructure.ollama_utils import models_include  # noqa: PLC0415

            if fresh:
                self._evict_ollama_model_list(config.ollama_url)
            installed, unreachable = self._installed_ollama_models(config.ollama_url)
            if unreachable:
                return RunPreflight(
                    ollama_unreachable=unreachable,
                    ollama_issue=self._ollama_issue(config.ollama_url, unreachable),
                )
            configured = self.configured_ollama_models(config)
            missing = [m for m in configured if not models_include(m, installed)]
            if missing:
                return RunPreflight(missing_models=tuple(missing))

        stages: list[str] = ["ocr"] if recognition and config.recognition_enabled else []
        if config.anonymization_enabled or config.text_anonymized_enabled:
            stages.append("anonymizer")
        if config.correction_enabled:
            stages.append("correction")
        if config.evaluation_enabled:
            stages.append("evaluation")
        for stage in stages:
            issues = self.check_stage_requirements(config, stage)
            if issues:
                return RunPreflight(stage=stage, stage_issues=tuple(issues))
        return RunPreflight(
            memory_warning=self.memory_warning_for_run(config) if memory else "",
            ollama_wait=self.ollama_wait_for_run(config) if memory else WaitDetail(),
        )

    def ollama_activity(self, url: str, model: str = "") -> ServerActivity:
        """Return what the Ollama server at *url* holds in memory, and whether *model* is there.

        Never raises: an unreachable server reports nothing loaded and nothing busy.
        """
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        return OllamaClient(url).activity(model)

    def ollama_wait_for_run(self, config: Config) -> WaitDetail:
        """Return why the first file waits for Ollama, or nothing.

        A note, not a refusal.  Only for a server on this computer, and only when the model the
        run needs is not in memory while another one is: Ollama then finishes that one and loads
        this one before the first answer.  Makes one request, so only the check that starts a
        run asks for it.
        """
        if not self._ollama_needed(config):
            return WaitDetail()
        from ..infrastructure.ollama_utils import (  # noqa: PLC0415
            WaitKind,
            is_local_ollama_url,
            normalize_ollama_url,
        )

        if not is_local_ollama_url(normalize_ollama_url(config.ollama_url)):
            return WaitDetail()
        models = self.configured_ollama_models(config)
        if not models:
            return WaitDetail()
        model = models[0]
        activity = self.ollama_activity(config.ollama_url, model)
        if activity.has(model):
            return WaitDetail()
        other = activity.busy_other_than(model) or next(iter(activity.loaded_names()), "")
        if not other:
            return WaitDetail()
        return WaitDetail(WaitKind.QUEUED, model, other)

    def memory_warning_for_run(self, config: Config) -> str:
        """Return why the configured Ollama models do not fit in memory now, or ``""``.

        A warning for the user, not a refusal: free memory changes from run to run, so the
        caller offers to run anyway.  For an Ollama on this computer: the part of each catalogued
        model that does not fit on the graphics card has to fit in the main memory that is free
        *right now* (keeping
        :data:`~teachers_teammate.domain.model_catalog.RUN_MEMORY_MARGIN_GB`), because a model
        that spills over would make the computer swap to disk and become unusable.  A model that
        is not catalogued, a server on another computer and an unreadable memory give ``""``.

        A model Ollama already has loaded is not credited as already counted: whether the
        processor-side part of a loaded model leaves the "available" figure depends on the kernel
        (anonymous memory vs. reclaimable memory-mapped weights) and has not been measured.  So
        the check behaves as it would after Ollama unloaded everything — which is the case it has
        to warn about anyway, since Ollama unloads idle models to make room.  If a measurement
        ever shows the memory is taken out of "available", the fix is to count the RAM part of
        other loaded models as free; see docs/development.md.
        """
        if not self._ollama_needed(config):
            return ""
        from ..infrastructure import gpu_detector  # noqa: PLC0415
        from ..infrastructure.ollama_utils import (  # noqa: PLC0415
            is_local_ollama_url,
            normalize_ollama_url,
        )

        if not is_local_ollama_url(normalize_ollama_url(config.ollama_url)):
            return ""
        available = gpu_detector.detect_available_memory_gb()
        if available is None:
            return ""
        detection = self.resolve_hardware_profile(config.hardware_profile, config.ollama_url)
        unified = self._runs_on_apple_silicon(detection)
        for tag in self.configured_ollama_models(config):
            spec = find_spec(tag)
            if spec is None:
                continue
            memory = self._memory_gb_at_resolved_window(config, tag, spec, detection)
            needed = ram_needed_gb(memory, detection.profile, detection.card_gb, unified=unified)
            if needed > available - RUN_MEMORY_MARGIN_GB:
                return (
                    f"{tag} needs about {needed:.0f} GB of free memory, but only "
                    f"{available:.0f} GB are free on this computer right now. Close other "
                    "programs or choose a smaller model; otherwise the computer would have to "
                    "use its disk as memory and become extremely slow."
                )
        return ""

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

    @staticmethod
    def _runs_on_apple_silicon(detection: HardwareDetection) -> bool:
        """Whether the models share one pool of memory with the graphics side (Apple Silicon).

        Read from the computer, not from the detection reason: a profile chosen by hand is a
        MANUAL detection, but on Apple Silicon the whole model still comes out of that memory.
        """
        from ..infrastructure import gpu_detector  # noqa: PLC0415

        return (
            detection.reason is DetectionReason.APPLE_UNIFIED_MEMORY
            or gpu_detector.detect_apple_unified_memory_gb() is not None
        )

    def config_for_stage_run(self, config: Config, stage: str) -> Config:
        """Return *config* for running one stage by hand: that stage on, the later ones off.

        Running a stage from the right-click menu is an explicit, per-entry action: it runs
        exactly that stage regardless of the enable checkboxes (which only govern the main
        *Run*), and nothing after it.  The cache of the later stages was just cleared, so
        leaving them enabled would silently run the whole chain again.  Evaluation also needs
        correction text (cached, or computed first).
        """
        if stage == "ocr":
            return dataclasses.replace(
                config,
                # Asking for this stage by hand overrides the "Recognize text" tick.
                recognition_enabled=True,
                correction_enabled=False,
                evaluation_enabled=False,
                anonymization_enabled=False,
            )
        if stage == "correction":
            return dataclasses.replace(config, correction_enabled=True, evaluation_enabled=False)
        if stage == "evaluation":
            return dataclasses.replace(config, evaluation_enabled=True, correction_enabled=True)
        return config

    def check_stage_requirements(self, config: Config, stage: str) -> list[RequirementIssue]:
        """Return unmet requirements for enabling *stage*.

        *stage* is 'ocr', 'correction', 'evaluation' or 'anonymizer'.
        Empty list means all requirements are satisfied.
        """
        if stage == "ocr":
            return self._ocr_engine_issues(config.ocr.engine)
        if stage in ("correction", "evaluation"):
            return self._llm_stage_issues(config, stage)
        if stage == "anonymizer":
            return self._name_detection_issues(config.language)
        return []

    def _ocr_engine_issues(self, engine: str) -> list[RequirementIssue]:
        """Return an issue when the text-recognition *engine* is not ready.

        Ollama is checked by the preflight before the stages.
        """
        if engine == ENGINE_PPOCR and not self.reader_installed():
            return [
                RequirementIssue(
                    "The built-in reader is not downloaded yet.", IssueKind.READER_MODEL_MISSING
                )
            ]
        return []

    # ── The built-in reader's model files ────────────────────────────────

    def reader_installed(self) -> bool:
        """Return whether the built-in reader's model files are downloaded."""
        return not self._model_store.missing(READER_MODELS)

    def download_reader(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """Download the built-in reader's model files (only ever on the teacher's request).

        Raises:
            ModelDownloadError: with a sentence a teacher can act on.
            ModelDownloadCancelledError: when *cancel* was set.
        """
        self._model_store.download(READER_MODELS, on_progress=on_progress, cancel=cancel)

    # ── The page models of the automatic image preparation ───────────────

    def page_models_installed(self) -> bool:
        """Return whether the page models (turn, flatten) are downloaded."""
        return not self._model_store.missing(PAGE_MODELS)

    def download_page_models(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """Download the page models (only ever on the teacher's request).

        Raises:
            ModelDownloadError: with a sentence a teacher can act on.
            ModelDownloadCancelledError: when *cancel* was set.
        """
        self._model_store.download(PAGE_MODELS, on_progress=on_progress, cancel=cancel)

    @property
    def model_store(self) -> ModelStore:
        """The store of the model files this service checks, downloads and runs from."""
        return self._model_store

    def models_dir(self) -> Path:
        """Return the folder the downloaded model files live in."""
        return self._model_store.root

    def _llm_stage_issues(self, config: Config, stage: str) -> list[RequirementIssue]:
        """Return what proofreading or the content review still needs (package, key, model)."""
        provider = config.correction_provider if stage == "correction" else config.evaluate_provider
        model = (
            (config.effective_correction_model or "")
            if stage == "correction"
            else (config.effective_evaluate_model or "")
        )
        name = str(get_provider_info(provider).get("display_name", provider))
        missing_package = self.missing_provider_package(provider)
        if missing_package and self._addon_waits_for_restart(missing_package):
            return [self._addon_restart_issue(f"{name} support")]
        if missing_package and self._addon_needs_update(missing_package):
            return [self._addons_outdated_issue()]
        if missing_package:
            return [
                RequirementIssue(
                    f"{name} support is not installed yet.",
                    IssueKind.PACKAGE_MISSING,
                    provider=provider,
                )
            ]
        if self.provider_key_missing(provider):
            return [
                RequirementIssue(
                    f"No API key for {name} is set.", IssueKind.KEY_MISSING, provider=provider
                )
            ]
        if provider != "ollama":
            return []
        from ..infrastructure.ollama_utils import models_include  # noqa: PLC0415

        installed, unreachable = self._installed_ollama_models(config.ollama_url)
        models = (model,) if model else ()
        if unreachable:
            return [
                RequirementIssue(
                    "Ollama is not running. Start it with `ollama serve`.",
                    IssueKind.OLLAMA_UNREACHABLE,
                )
            ]
        if not installed:
            return [
                RequirementIssue(
                    "No model is downloaded in Ollama yet.",
                    IssueKind.NO_OLLAMA_MODEL,
                    models=models,
                )
            ]
        if model and not models_include(model, installed):
            return [
                RequirementIssue(
                    f"Model '{model}' is not downloaded.", IssueKind.MODEL_MISSING, models=models
                )
            ]
        return []

    @staticmethod
    def _addons_outdated_issue() -> RequirementIssue:
        return RequirementIssue(
            "The add-ons need an update after the app update.", IssueKind.ADDONS_OUTDATED
        )

    @staticmethod
    def _addon_restart_issue(what: str) -> RequirementIssue:
        return RequirementIssue(
            f"Restart the app to finish installing {what}.", IssueKind.ADDON_RESTART_NEEDED
        )

    def _addon_needs_update(self, distribution: str) -> bool:
        """Return whether *distribution* is installed as an add-on but kept off by an app update."""
        from ..infrastructure.addon_manager import addon_in_folder, addons_outdated  # noqa: PLC0415

        return addons_outdated() and addon_in_folder(distribution)

    @staticmethod
    def _addon_waits_for_restart(distribution: str) -> bool:
        """Return whether *distribution* is installed but waits for the next start."""
        from ..infrastructure.addon_manager import addon_waits_for_restart  # noqa: PLC0415

        return addon_waits_for_restart(distribution)

    def _name_detection_issues(self, language: str) -> list[RequirementIssue]:
        """Return what removing names still needs: spaCy itself or the model of *language*."""
        if self._addon_waits_for_restart("spacy"):
            return [self._addon_restart_issue("name detection")]
        if self._addon_needs_update("spacy"):
            return [self._addons_outdated_issue()]
        installed_models = self.list_installed_spacy_models()
        required = self.spacy_model_for_language(language)
        if required in installed_models:
            return []
        if not installed_models and not self.is_module_importable("spacy"):
            return [
                RequirementIssue(
                    "spaCy is not installed. Install it under Add-ons in the app.",
                    IssueKind.PRIVACY_ADDON_MISSING,
                    models=(required,),
                )
            ]
        return [
            RequirementIssue(
                f"spaCy NER model '{required}' is not downloaded.",
                IssueKind.NAME_DETECTION_MISSING,
                models=(required,),
            )
        ]

    def pull_ollama_model(
        self,
        url: str,
        model_name: str,
        progress_cb=None,
        abort_event=None,
    ) -> None:
        """Pull (download) *model_name* from Ollama at *url*."""
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        OllamaClient(url).pull(model_name, progress_cb, abort_event)

    # ── Anonymizer helpers ────────────────────────────────────────────────

    def spacy_model_for_language(self, language: str) -> str:
        """Return the spaCy model name for *language*, falling back to the multilingual model."""
        from ..infrastructure.spacy_models import spacy_model_for  # noqa: PLC0415

        return spacy_model_for(language)

    def list_installed_spacy_models(self) -> list[str]:
        """Return sorted names of installed spaCy model packages, or ``[]`` if spaCy is absent."""
        from ..infrastructure.spacy_models import installed_spacy_models  # noqa: PLC0415

        return installed_spacy_models()

    def _remembered_installed_models(self) -> list[str]:
        """Return the installed spaCy models, asking spaCy only once until it is forgotten."""
        if self._installed_models is None:
            self._installed_models = self.list_installed_spacy_models()
        return list(self._installed_models)

    def _warn_once(self, message: str) -> None:
        """Print *message* to stderr unless it was printed before for the same settings."""
        if message not in self._warned:
            self._warned.add(message)
            print(message, file=sys.stderr)

    def anonymizer_available(self, language: str, config: object | None = None) -> bool:
        """Return whether personal information can be hidden for *language* right now.

        The models are resolved the way a run resolves them, so a chosen primary model counts:
        the answer is "yes" exactly when a run would find every model it loads.  Cheap enough
        for the GUI to ask whenever it builds a panel: spaCy must be importable and the models
        downloaded.  The GUI uses this to disable "Hide personal information in texts" instead
        of failing when it is used.
        """
        from importlib.util import find_spec as find_module_spec  # noqa: PLC0415

        if find_module_spec("spacy") is None:
            return False
        installed = self._remembered_installed_models()
        return all(model in installed for model in self.anonymizer_models(language, config))

    def extra_name_models(self, language: str) -> list[str]:
        """Return the spaCy models that can be the extra language next to *language*'s own.

        The model of the document language itself is left out: a second pass with it would only
        repeat the first.
        """
        from ..infrastructure.spacy_models import (  # noqa: PLC0415
            FALLBACK_SPACY_MODEL,
            SPACY_MODELS,
            spacy_model_for,
        )

        own = spacy_model_for(language)
        models = dict.fromkeys([*SPACY_MODELS.values(), FALLBACK_SPACY_MODEL])
        return [model for model in models if model != own]

    def system_spacy_model(self) -> str | None:
        """Return the spaCy model for the language this computer is set to, if it is known."""
        from ..infrastructure.spacy_models import spacy_model_for  # noqa: PLC0415
        from ..infrastructure.system_locale import system_language  # noqa: PLC0415

        language = system_language()
        return spacy_model_for(language) if language else None

    def resolve_anonymizer_config(
        self, language: str, config: object | None = None
    ) -> AnonymizerConfig:
        """Return *config* with the spaCy models that will actually be loaded.

        ``"auto:system"`` becomes the model of the system language — unless that is the same
        model as the primary pass or the model is not downloaded (then a warning says so and
        no extra pass runs).  An explicitly chosen model is kept as it is: if it is missing,
        building the anonymizer fails as before.
        """
        anon_config: AnonymizerConfig = (
            config if isinstance(config, AnonymizerConfig) else AnonymizerConfig()
        )
        from ..infrastructure.anonymizer import resolve_anonymizer_config  # noqa: PLC0415
        from ..infrastructure.system_locale import system_language  # noqa: PLC0415

        return resolve_anonymizer_config(
            anon_config,
            document_language=language,
            system_language=system_language(),
            installed_models=self._remembered_installed_models(),
            warn=self._warn_once,
        )

    def get_installation_status(
        self,
        *,
        provider_modules: dict[str, str] | None = None,
        spacy_models: list[str] | None = None,
        ollama_models: list[str] | None = None,
        ollama_url: str = "",
        langchain_packages: dict[str, str] | None = None,
    ) -> dict[str, bool]:
        """Probe availability of optional components and return a status map.

        Keys produced (all optional — only those whose *inputs* are supplied):
        - ``"reader:ppocr"`` — always checked (the built-in reader's model files)
        - ``"page_models:all"`` — always checked (the page models of the automatic preparation)
        - ``"bundled:reader:ppocr"``, ``"bundled:page_models:all"`` — always checked: whether
          the app carries those files itself, so the Add-ons rows say *included* not *downloaded*
        - ``"provider:{name}"`` — for each entry in *provider_modules*
        - ``"spacy:{model_id}"`` — for each id in *spacy_models*
        - ``"ollama:{model}"`` — for each model in *ollama_models*
        - ``"langchain:{key}"`` — for each entry in *langchain_packages*
        """
        result: dict[str, bool] = {}
        result[READER_STATUS_KEY] = self.reader_installed()
        result[PAGE_MODELS_STATUS_KEY] = self.page_models_installed()
        result[bundled_status_key(READER_STATUS_KEY)] = self._model_store.is_bundled(READER_MODELS)
        result[bundled_status_key(PAGE_MODELS_STATUS_KEY)] = self._model_store.is_bundled(
            PAGE_MODELS
        )

        if provider_modules:
            for name, module_name in provider_modules.items():
                result[f"provider:{name}"] = importlib.util.find_spec(module_name) is not None

        if spacy_models:
            installed_spacy = set(self.list_installed_spacy_models())
            for model_id in spacy_models:
                result[f"spacy:{model_id}"] = model_id in installed_spacy

        if ollama_models:
            from ..domain.model_catalog import normalize_tag  # noqa: PLC0415

            try:
                # Compare normalised tags: "llama3.2" and "llama3.2:latest" are one model.
                pulled = {
                    normalize_tag(name)
                    for name in self.list_provider_models("ollama", base_url=ollama_url)
                }
            except Exception:  # noqa: BLE001
                # Ollama may be unreachable; treat as no models pulled
                pulled = set()
            for model in ollama_models:
                result[f"ollama:{model}"] = normalize_tag(model) in pulled

        if langchain_packages:
            for key, module_name in langchain_packages.items():
                result[f"langchain:{key}"] = importlib.util.find_spec(module_name) is not None

        return result

    def is_addon_available(self, addon: str) -> bool:
        """Return whether the named addon package is importable."""
        from ..infrastructure.addon_manager import is_addon_importable  # noqa: PLC0415

        return is_addon_importable(addon)

    def is_module_importable(self, module: str) -> bool:
        """Return True if *module* can be imported (the package is installed).

        The import caches are refreshed first: a package installed while the app runs
        (Add-ons) is otherwise reported missing until the next start.
        """
        importlib.invalidate_caches()
        return importlib.util.find_spec(module) is not None

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        """Return ``{provider: (importable module, pip name)}`` for every provider package.

        Providers that share one package (Mistral global and EU) appear once, under the
        first of them, so an install list does not offer the same package twice.
        """
        packages: dict[str, tuple[str, str]] = {}
        seen: set[str] = set()
        for provider in list_providers():
            package = provider_package(provider)
            if package is None or package[1] in seen:
                continue
            seen.add(package[1])
            packages[provider] = package
        return packages

    def missing_provider_package(self, provider: str) -> str | None:
        """Return the pip name of the package *provider* needs but is not installed, else ``None``."""
        package = provider_package(provider)
        if package is None or not provider_package_required(provider):
            return None
        if self.is_module_importable(package[0]):
            return None
        return package[1]

    # ── GPU / addon installer helpers ─────────────────────────────────────

    def detect_gpus(self, *, refresh: bool = False) -> list[GpuInfo]:
        """Return all detected NVIDIA and AMD GPUs.

        Served from the process-wide hardware probe, so the "Detect GPU" button
        and the "My computer" detection never probe the machine twice;
        ``refresh=True`` probes again.
        """
        return self._recommendations.detected_gpus(refresh=refresh)

    def addon_packages_dir(self) -> Path:
        """Return the persistent user-writable directory for addon packages."""
        from ..infrastructure.addon_manager import get_packages_dir  # noqa: PLC0415

        return get_packages_dir()

    def install_addon(
        self,
        addon: str,
        target: Path,
        progress_cb: Callable[[str], None],
    ) -> None:
        """Install *addon* packages into *target* using the bundled pip."""
        from ..infrastructure.addon_manager import install_addon_subprocess  # noqa: PLC0415

        install_addon_subprocess(addon, target, progress_cb)

    def addons_restart_needed(self) -> bool:
        """Return whether an install waits for the next start (``packages.pending/``).

        Windows locks a file the running app has loaded, so a package that could not replace
        its old copy waits there; :func:`prepare_packages_dir` applies it at the next start.
        """
        from ..infrastructure.addon_manager import addons_restart_needed  # noqa: PLC0415

        return addons_restart_needed()

    def pending_addon_packages(self) -> list[str]:
        """Return the normalised names of the packages that wait for the next start."""
        from ..infrastructure.addon_manager import pending_addon_packages  # noqa: PLC0415

        return pending_addon_packages()

    def addons_outdated(self) -> bool:
        """Return whether an app update left add-ons that wait for *Update add-ons*.

        They stay off (the app runs on its own bundle) until :meth:`update_addons` ran.  Reads the
        folder names and one small ``WHEEL`` file per package; ``False`` outside the installed
        app.
        """
        from ..infrastructure.addon_manager import addons_outdated  # noqa: PLC0415

        return addons_outdated()

    def update_addons(self, progress_cb: Callable[[str], None]) -> None:
        """Install the add-ons in the packages directory again with the app's package versions.

        Slow (downloads): call it from a background thread, and only after the teacher asked.

        Raises:
            OSError: if the packages directory cannot be written.
            RuntimeError: if pip fails for an add-on, or this is not the installed app.
        """
        from ..infrastructure.addon_manager import update_installed_addons  # noqa: PLC0415

        update_installed_addons(progress_cb)

    def install_packages(
        self,
        packages: list[str],
        progress_cb: Callable[[str], None],
    ) -> None:
        """Install arbitrary pip *packages* into the addon packages directory."""
        from ..infrastructure.addon_manager import (  # noqa: PLC0415
            get_packages_dir,
            install_packages_subprocess,
        )

        install_packages_subprocess(packages, get_packages_dir(), progress_cb)

    def download_spacy_model(
        self,
        model: str,
        target: Path,
        progress_cb: Callable[[str], None],
    ) -> None:
        """Download a spaCy model into *target* using spaCy's own download mechanism."""
        from ..infrastructure.addon_manager import download_spacy_model_subprocess  # noqa: PLC0415

        download_spacy_model_subprocess(model, target, progress_cb)

    def clear_stale_addon_modules(self, addon: str) -> None:
        """Evict cached sys.modules entries left over from a failed addon import."""
        from ..infrastructure.addon_manager import clear_stale_modules  # noqa: PLC0415

        clear_stale_modules(addon)

    @staticmethod
    def warm_up_locality_checks() -> None:
        """Start, in the background, the lookup that tells whether an Ollama URL is this computer.

        Call it at start-up so classifying where a step runs never waits for the network
        on the GUI thread.
        """
        warm_up_own_host_names()

    @staticmethod
    def inject_addon_packages_dir() -> None:
        """Prepend the addon packages directory to sys.path (idempotent).

        Every entry point calls this first, so packages installed under Add-ons are importable
        after a restart however the app was started.  A folder left by an older app version is
        not added (see :meth:`addons_outdated`).
        """
        from ..infrastructure.addon_manager import inject_packages_dir  # noqa: PLC0415

        inject_packages_dir()

    # ── Storage root / config path helpers ───────────────────────────────

    def default_config_path(self) -> Path:
        """Return the default ``ocr.toml`` path inside the config root."""
        return _infra_default_config_path()

    def resolve_config_path(self, explicit: str | None) -> Path | None:
        """Return the config file path to load, or ``None`` when none applies."""
        return _infra_resolve_config_path(explicit)

    def first_start_settings(self, path: Path | None) -> dict | None:
        """Return what the installer chose when *path* is no saved setup yet, else ``None``.

        A first start is a missing settings file, or one that holds only the keys the installer
        writes (``INSTALLER_SETTING_KEYS``): the teacher has not set the app up then, and the
        Setup Assistant must still run.  The dict carries those keys, so the first save does not
        undo them.  A file that cannot be read is a saved setup, so the caller reports it.
        """
        if path is None or not path.is_file():
            return {}
        try:
            values = load_config_file(path).values
        except Exception:  # noqa: BLE001  # a broken file is the caller's warning, not a first start
            return None
        if set(values) <= INSTALLER_SETTING_KEYS:
            return values
        return None

    # ── Commands ──────────────────────────────────────────────────────────

    def invalidate_from_stage(
        self,
        config: Config,
        *,
        selected_source_ids: list[str],
        stage: str,
    ) -> tuple[list[str], list[str], bool]:
        return self._commands.invalidate_from_stage(
            config, selected_source_ids=selected_source_ids, stage=stage
        )

    def edit_transcription(
        self, config: Config, *, source: Path, text: str, text_format: str = "plain"
    ) -> DocumentStateView | None:
        """Store the teacher's transcription of *source*; ``None`` when it has no state.

        Raises:
            WorkNotSavedError: the work could not be stored.
        """
        return self._commands.edit_transcription(
            config, source=source, text=text, text_format=text_format
        )

    def edit_corrected_text(
        self, config: Config, *, source: Path, text: str
    ) -> DocumentStateView | None:
        """Store the teacher's corrections of *source*; ``None`` when there is nothing to bind.

        Raises:
            WorkNotSavedError: the work could not be stored.
        """
        return self._commands.edit_corrected_text(config, source=source, text=text)

    def set_checked(
        self, config: Config, *, source: Path, checked: bool
    ) -> DocumentStateView | None:
        """Mark *source* as checked, or take the mark back.

        Raises:
            WorkNotSavedError: the work could not be stored.
        """
        return self._commands.set_checked(config, source=source, checked=checked)

    def mark_looked_at(self, config: Config, *, source: Path, keys: Collection[str]) -> None:
        """Record that the teacher looked at the items *keys* of *source*."""
        self._commands.mark_looked_at(config, source=source, keys=keys)

    def keep_teacher_work(self, config: Config, *, files: Collection[Path]) -> None:
        """Record that the teacher keeps their corrections over the settings of this run."""
        self._commands.keep_teacher_work(config, files=files)

    def drop_unbound_corrections(self, config: Config, *, files: Collection[Path]) -> None:
        """Drop the corrections of *files* that belong to no stored proofreading."""
        self._commands.drop_unbound_corrections(config, files=files)
