"""Application-layer orchestration services used by GUI and CLI adapters."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
import dataclasses
from dataclasses import dataclass
from enum import StrEnum
import logging
from pathlib import Path
import threading
from typing import TYPE_CHECKING

from ..config import DEFAULTS, Config
from ..domain.context_budget import RunWindows, memory_at_context_gb
from ..domain.data_location import (
    ENGINE_PPOCR,
)
from ..domain.formatted_text import TextFormat, normalize_text_format
from ..domain.local_models import (
    PAGE_MODELS_SKIPPED_NOTICE,
    READER_NOT_DOWNLOADED,
    READER_SIZE_LABEL,
)
from ..domain.model_catalog import (
    RUN_MEMORY_MARGIN_GB,
    DetectionReason,
    HardwareDetection,
    OllamaModelSpec,
    find_spec,
    ram_needed_gb,
)
from ..domain.model_listing import ModelListing
from ..domain.output_files import (
    TEXT_SUFFIX,
    feeds_inputs,
    output_folder_refusal,
    output_path,
)
from ..domain.pdf_text_layer import PdfTextLayerMode, normalize_pdf_text_layer
from ..domain.run_summary import RunSummary
from ..infrastructure import power
from ..infrastructure.addon_manager import ADDON_PRIVACY as ADDON_PRIVACY  # noqa: PLC0414
from ..infrastructure.anonymizer import (
    DEFAULT_PATTERNS as DEFAULT_ANONYMIZER_PATTERNS,  # noqa: F401
)

# ── Re-exports for GUI/CLI use — keeps infrastructure types out of adapter layers ──
from ..infrastructure.anonymizer import AnonymizerConfig as AnonymizerConfig  # noqa: PLC0414
from ..infrastructure.anonymizer import (
    resolve_patterns as resolve_anonymizer_patterns,  # noqa: F401
)
from ..infrastructure.file_discovery import FileDiscovery
from ..infrastructure.gpu_detector import GpuInfo as GpuInfo  # noqa: PLC0414
from ..infrastructure.ollama_utils import WaitDetail
from ..infrastructure.onnx.store import ModelStore
from ..infrastructure.pipeline import OCRPipeline
from ..infrastructure.power import KEEP_AWAKE_REASON, NullSleepBlocker, on_battery
from ..infrastructure.reporting import KeepAwakeState
from ..infrastructure.reporting import ProgressCallbacks as ProgressCallbacks  # noqa: PLC0414
from ..infrastructure.stage_builder import (
    PipelineComponentFactory,
    resolve_run_windows,
    window_of_model,
)
from ..infrastructure.state_repository import (
    StateRepository,
    compute_file_hash_cached,
)
from ..infrastructure.storage_root import (
    resolve_artifact_dir,
)
from ..infrastructure.teacher_work_store import TeacherWorkStore
from ..interfaces import SUPPORTED_SUFFIXES, SleepBlocker
from .addons import AddonService
from .anonymization import AnonymizationService
from .commands import DocumentCommands
from .commands import WorkNotSavedError as WorkNotSavedError  # noqa: PLC0414
from .correction_options import proofreading_prompts
from .data_flow import DataFlowService, ocr_refusal
from .document_view import DocumentStateView
from .documents import DocumentQueries, WorkedOn
from .formatting import TextFormatter
from .model_recommendations import ModelRecommendationService
from .ollama_server import OllamaServerService
from .providers import ProviderService
from .reader import ReaderService
from .sent_text import Anonymize, SentTexts, describe_sent_texts
from .setup_plan import SetupService
from .speed_estimates import SpeedEstimateService

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


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
    worked_on: WorkedOn = dataclasses.field(default_factory=WorkedOn)
    #: Another model is in the way on the Ollama server, so the first file waits for it.  A
    #: note, never a refusal: the run works, it just waits.
    ollama_wait: WaitDetail = dataclasses.field(default_factory=WaitDetail)

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
class RecognitionPlan:
    """Which queued files a run can use while text recognition is switched off."""

    readable: tuple[Path, ...] = ()
    """Text files, PDFs with a usable text layer, and files whose recognised text is stored."""
    skipped: tuple[Path, ...] = ()
    """Scans: images and PDFs that have to be read from page images."""


#: Why an export has nothing to write: the document was never read.
NO_TEXT_MESSAGE = "No OCR text — run the pipeline first."


class ProcessingApplicationService:
    """Coordinates processing and state transitions behind a small app-facing API."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        discovery: FileDiscovery | None = None,
        pipeline_factory: Callable[..., OCRPipeline] | None = None,
        state_repository_factory: Callable[[Path], StateRepository] | None = None,
        component_factory: PipelineComponentFactory | None = None,
        sleep_blocker: SleepBlocker | None = None,
        model_store: ModelStore | None = None,
        work_store: TeacherWorkStore | None = None,
        ollama: OllamaServerService,
        providers: ProviderService,
        reader: ReaderService,
        recommendations: ModelRecommendationService,
        speeds: SpeedEstimateService,
        anonymization: AnonymizationService,
        addons: AddonService,
        data_flow: DataFlowService,
        setup: SetupService,
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
        # The services are given, never built here: see ``application/composition.py``.
        self._ollama = ollama
        self._providers = providers
        self._reader = reader
        self.anonymization = anonymization
        self.data_flow = data_flow
        self.addons = addons
        # Whether a PDF has a usable text layer, by (path, size, mtime, mode): the readiness
        # check asks again after every settings change, and opening a PDF is not free.
        self._pdf_text_cache: dict[tuple[str, int, int, str], bool] = {}
        self._speeds = speeds
        self._recommendations = recommendations
        # A model list that is forgotten takes the classifications derived from it with it.
        self._providers.watch_classifications(self._recommendations.invalidate_classification)
        self.setup = setup
        self.commands = DocumentCommands(
            state_store_factory=self._state_store,
            windows_for=self.resolved_windows,
            model_store=self._model_store,
            work_store=self._work_store,
        )
        # Read-only queries over the store; the adapters use this one directly.
        self.documents = DocumentQueries(
            state_store_factory=self._state_store,
            work_store=self._work_store,
            model_store=self._model_store,
            windows_for=self.resolved_windows,
        )

    def _state_store(self, config: Config) -> StateRepository:
        return self._state_repository_factory(resolve_artifact_dir(config.output_dir) / "state")

    # ── Workflow operations ───────────────────────────────────────────────

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
                    work_store=self._work_store,
                ),
                progress=progress,
            )
            return pipeline.run()

    def _run_component_factory(self) -> PipelineComponentFactory:
        """Return the stage factory of a run: its anonymizer shares this service's caches."""
        return dataclasses.replace(
            self._component_factory, build_anonymizer=self.anonymization.build_cached_anonymizer
        )

    def run_preview_only(self, config: Config, files: Sequence[Path]) -> RunSummary:
        """Preprocess *files* and return what happened to each; nothing is read or proofread."""
        return self._pipeline_factory(config, files).run_preview_only()

    # ── Queries ───────────────────────────────────────────────────────────

    def collect_input_files(self, config: Config) -> list[Path]:
        """Return supported source files for the current config."""
        return self._discovery.collect_input_files(config)

    # ── Context windows ───────────────────────────────────────────────────

    def resolved_windows(self, config: Config) -> RunWindows:
        """Return the context windows a run with *config* uses on this computer.

        The same resolution the run itself uses, so a cache key computed here matches the
        one the run writes — once this computer has been probed.  It never probes itself:
        the GUI asks for the expected cache keys on its own thread, and the probe runs
        ``nvidia-smi`` and the ROCm tools.  While the computer is still unknown the base
        window is returned; a caller that needs the card-derived window calls
        :meth:`ModelRecommendationService.detect_hardware` in the background first and asks
        again (``.cached_hardware_profile`` says whether that is needed).
        """
        return resolve_run_windows(
            config,
            self._recommendations.cached_hardware_profile(
                config.hardware_profile, config.ollama_url
            ),
        )

    def list_supported_suffixes(self) -> frozenset[str]:
        """Return input file suffixes accepted by the pipeline."""
        return SUPPORTED_SUFFIXES

    def collect_input_files_from_dir(self, input_dir: Path, recursive: bool) -> list[Path]:
        """Collect supported input files without a full Config (for partial GUI state)."""
        return self._discovery.collect_input_files_from_dir(input_dir, recursive)

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

        view = self.documents.load_view(config, source)
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
            text, _mapping = self.anonymization.anonymize_preview(text, language, anonymizer_config)
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
        view = self.documents.load_view(config, source)
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
            return self._providers.check_connection(provider, url, model=model)
        return False, False, "Nothing to check."

    def _check_engine(self, engine: str, url: str, *, model: str = "") -> tuple[bool, bool, str]:
        if engine == "ollama":
            return self._providers.check_connection("ollama", url, model=model)
        if engine == ENGINE_PPOCR:
            if self._reader.reader_installed():
                return True, True, "✓ built-in reader ready"
            return False, False, f"✗ built-in reader not downloaded ({READER_SIZE_LABEL})"
        return True, True, "✓ OK"

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
        cached = self._providers.get_cached_models("ollama", base_url=url)
        if cached is not None:
            return list(cached), ""
        installed, unreachable = self._ollama.listed_models(url)
        self._providers.remember_listing("ollama", ModelListing(installed), base_url=url)
        return installed, unreachable

    def _ollama_issue(self, url: str, message: str) -> RequirementIssue:
        """Return what is wrong with Ollama at *url*: not found, not running, or unreachable.

        A local address that does not answer is told apart by looking for the program: a
        teacher who has just installed the app needs to know whether to install Ollama or to
        start it.  An address on another computer can only be unreachable.
        """
        if not self._ollama.is_local(url, include_wsl_host=True):
            return RequirementIssue(message, IssueKind.OLLAMA_UNREACHABLE)
        if not self._ollama.program_found():
            return RequirementIssue(message, IssueKind.OLLAMA_NOT_FOUND)
        return RequirementIssue(
            message, IssueKind.OLLAMA_NOT_RUNNING, startable=self._ollama.can_start()
        )

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
        flow = self.data_flow.describe_data_flow(config)
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
        return lambda text: self.anonymization.anonymize_preview(text, language, anon_config)

    def _sent_correction_prompt(self, config: Config) -> str:
        """Return the instructions the corrector sends, or ``""`` when it sends none.

        One heading per step that runs, then the teacher's notes when there are any: the
        prompts are fixed, so this is the whole of what the model is told.
        """
        from ..domain.correction_prompts import notes_block  # noqa: PLC0415

        parts = [f"{title}\n\n{prompt}" for title, prompt in proofreading_prompts(config)]
        notes = notes_block(config.correction_notes).strip()
        if notes:
            parts.append(notes)
        return "\n\n".join(parts)

    def _sent_evaluation_prompt(self, config: Config, text_format: TextFormat) -> str:
        """Return the instructions the evaluator sends, or ``""`` when it sends none."""
        from ..domain.notation import with_notation_hint  # noqa: PLC0415

        prompt = config.evaluate_prompt or DEFAULTS["evaluate_prompt"]
        return with_notation_hint(prompt, text_format, task="evaluation")

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
            and not self._reader.page_models_installed()
        ):
            return PAGE_MODELS_SKIPPED_NOTICE
        return ""

    def reader_refusal_for_run(self, config: Config, files: Sequence[Path]) -> str:
        """Return why the built-in reader cannot start a run over *files*, or ``""`` when it can.

        Only a run that reads pages with the reader and has no model files is refused; the
        sentence names the command line flag that downloads them.  A run over files that need
        no reading (text files, text that is already recognised) is not: the model files are
        only opened when a page is read, as in the main window
        (see :meth:`needs_text_recognition`).
        """
        if not (
            config.recognition_enabled
            and config.ocr.engine == ENGINE_PPOCR
            and not self._reader.reader_installed()
        ):
            return ""
        if not self.needs_text_recognition(
            files, recognised=self.documents.recognised_files(config, files)
        ):
            return ""
        return READER_NOT_DOWNLOADED

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
                worked_on=self.documents.worked_on_documents(
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
                self._providers.forget_ollama_model_list(config.ollama_url)
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

    def ollama_wait_for_run(self, config: Config) -> WaitDetail:
        """Return why the first file waits for Ollama, or nothing.

        A note, not a refusal.  Only for a server on this computer, and only when the model the
        run needs is not in memory while another one is: Ollama then finishes that one and loads
        this one before the first answer.  Makes one request, so only the check that starts a
        run asks for it.
        """
        if not self._ollama_needed(config):
            return WaitDetail()
        if not self._ollama.is_local(config.ollama_url):
            return WaitDetail()
        models = self.configured_ollama_models(config)
        if not models:
            return WaitDetail()
        return self._ollama.wait_for(config.ollama_url, models[0])

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
        if not self._ollama.is_local(config.ollama_url):
            return ""
        available = self._recommendations.available_memory_gb()
        if available is None:
            return ""
        detection = self._recommendations.resolve_hardware_profile(
            config.hardware_profile, config.ollama_url
        )
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
        if engine == ENGINE_PPOCR and not self._reader.reader_installed():
            return [
                RequirementIssue(
                    "The built-in reader is not downloaded yet.", IssueKind.READER_MODEL_MISSING
                )
            ]
        return []

    # ── The built-in reader's model files ────────────────────────────────

    # ── The page models of the automatic image preparation ───────────────

    @property
    def model_store(self) -> ModelStore:
        """The store of the model files this service checks, downloads and runs from."""
        return self._model_store

    def _llm_stage_issues(self, config: Config, stage: str) -> list[RequirementIssue]:
        """Return what proofreading or the content review still needs (package, key, model)."""
        provider = config.correction_provider if stage == "correction" else config.evaluate_provider
        model = (
            (config.effective_correction_model or "")
            if stage == "correction"
            else (config.effective_evaluate_model or "")
        )
        name = str(self._providers.get_provider_info(provider).get("display_name", provider))
        missing_package = self._providers.missing_provider_package(provider)
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
        if self._providers.provider_key_missing(provider):
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
        installed_models = self.anonymization.list_installed_spacy_models()
        required = self.anonymization.spacy_model_for_language(language)
        if required in installed_models:
            return []
        if not installed_models and not self.addons.is_module_importable("spacy"):
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

    def system_language(self) -> str | None:
        """Return the language of this computer's system, or ``None`` when it is not supported."""
        from ..infrastructure.system_locale import system_language  # noqa: PLC0415

        return system_language()

    # ── Commands ──────────────────────────────────────────────────────────
