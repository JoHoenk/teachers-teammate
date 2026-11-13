"""Application-layer orchestration services used by GUI and CLI adapters."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
import dataclasses
from dataclasses import dataclass
from enum import StrEnum
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from typing import TYPE_CHECKING
import uuid

from ..config import DEFAULTS, Config, OcrConfig
from ..domain.context_budget import RunWindows, memory_at_context_gb
from ..domain.correction_policy import DEFAULT_LEVEL, normalize_output_mode
from ..domain.correction_review import CorrectionReview
from ..domain.data_location import DataFlow, StageLocation, is_ollama_cloud_model
from ..domain.formatted_text import TextFormat
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
from ..domain.ollama_discovery import DiscoveredServer, DiscoveryScope
from ..domain.output_files import (
    TEXT_SUFFIX,
    feeds_inputs,
    output_folder_refusal,
    output_path,
)
from ..domain.pdf_text_layer import PdfTextLayerMode, normalize_pdf_text_layer
from ..infrastructure.addon_manager import ADDON_AMD as ADDON_AMD  # noqa: PLC0414
from ..infrastructure.addon_manager import ADDON_NVIDIA as ADDON_NVIDIA  # noqa: PLC0414
from ..infrastructure.addon_manager import ADDON_PADDLE as ADDON_PADDLE  # noqa: PLC0414
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
from ..infrastructure.ocr_processor import (
    default_preprocess_for_engine as _infra_default_preprocess_for_engine,
)
from ..infrastructure.ollama_utils import warm_up_own_host_names
from ..infrastructure.pipeline import OCRPipeline, preprocess_preview
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
from ..infrastructure.workflow.cache_service import (
    compute_correction_config_hash,
    compute_eval_config_hash,
    compute_ocr_config_hash,
)
from ..interfaces import SUPPORTED_SUFFIXES
from .commands import ApplicationCommands
from .context_choice import ContextChoice, context_choice
from .data_flow import (
    describe_data_flow,
    llm_location,
    ocr_location,
    ocr_refusal,
    text_goes_online,
)
from .formatting import TextFormatter
from .model_recommendations import ModelRecommendationService
from .sent_text import Anonymize, SentTexts, describe_sent_texts
from .text_size import TextSizeEstimate, proofreading_pieces

if TYPE_CHECKING:
    from ..infrastructure.anonymizer import SpacyAnonymizer
    from ..interfaces import Anonymizer


class IssueKind(StrEnum):
    """What is missing, so the GUI can offer the fix without reading the sentence."""

    OCR_NOT_ALLOWED = "ocr_not_allowed"
    ENGINE_MISSING = "engine_missing"
    OLLAMA_UNREACHABLE = "ollama_unreachable"
    NO_OLLAMA_MODEL = "no_ollama_model"
    MODEL_MISSING = "model_missing"
    PACKAGE_MISSING = "package_missing"
    KEY_MISSING = "key_missing"
    NAME_DETECTION_MISSING = "name_detection_missing"
    #: Text recognition is switched off and no queued file has text to use without it.
    NOTHING_TO_READ = "nothing_to_read"


@dataclasses.dataclass(frozen=True)
class RequirementIssue:
    """A single unmet requirement for enabling a pipeline stage or starting a run."""

    #: The sentence for the dialog.
    message: str
    kind: IssueKind
    #: The provider whose package or key is missing (``PACKAGE_MISSING``, ``KEY_MISSING``).
    provider: str = ""
    #: The text-recognition engine that is not installed (``ENGINE_MISSING``).
    engine: str = ""
    #: Ollama tags that are not downloaded, or the spaCy model that is missing.
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocxExport:
    """Presentation options for :meth:`ProcessingApplicationService.export_docx`."""

    docx_format: str = "table"
    """``"table"`` (three columns) or ``"comments"`` (diff hunks as Word comments)."""
    text_format: str = "plain"
    """Notation of the exported texts (``"markdown"`` keeps bold, strikethrough, ...)."""
    review: CorrectionReview | None = None
    """Adds the explanations and the table of unclear expressions."""
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
            return "", RequirementIssue(self.ollama_unreachable, IssueKind.OLLAMA_UNREACHABLE)
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
    ) -> None:
        self._discovery = discovery or FileDiscovery()
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
        self._commands = ApplicationCommands(
            discovery=self._discovery,
            state_store_factory=self._state_store,
            windows_for=self.resolved_windows,
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
        reporter=None,
        progress: ProgressCallbacks | None = None,
    ) -> int:
        from ..infrastructure.pipeline import PipelineDependencies  # noqa: PLC0415

        pipeline = self._pipeline_factory(
            config,
            stop_event=stop_event,
            config_file=config_file,
            selected_source_paths=selected_source_paths,
            dependencies=PipelineDependencies(
                reporter=reporter,
                component_factory=self._run_component_factory(),
                hardware=self.resolve_hardware_profile(config.hardware_profile, config.ollama_url),
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
        return self._state_store(config).load_views(files)

    def get_document_state(self, config: Config, source: Path) -> DocumentState | None:
        """Return the raw persisted state for *source*, or None if not found."""
        return self._state_store(config).load(source)

    def load_views_reconciled(
        self, config: Config, files: list[Path]
    ) -> dict[str, DocumentStateView]:
        """Like load_views but marks stages not-done when their config hash has changed."""
        from ..infrastructure.ocr_processor import get_ocr_prompt  # noqa: PLC0415

        store = self._state_store(config)

        ocr_prompt = get_ocr_prompt(config.language)
        windows = self.resolved_windows(config)
        expected_corr_hash = compute_correction_config_hash(config, windows)
        expected_eval_hash = compute_eval_config_hash(config, windows)

        from ..domain.freshness import StageHashes, first_stale_stage_for_hashes  # noqa: PLC0415

        views: dict[str, DocumentStateView] = {}
        for file in files:
            state = store.load_valid(file)
            if state is None:
                continue
            stale_stage = first_stale_stage_for_hashes(
                StageHashes(
                    ocr_done=state.ocr_done,
                    ocr_config_hash=state.ocr_config_hash,
                    expected_ocr_config_hash=compute_ocr_config_hash(config, ocr_prompt, file),
                    correction_done=state.correction_done,
                    correction_config_hash=state.correction_config_hash,
                    expected_correction_config_hash=expected_corr_hash,
                    evaluation_done=state.evaluation_done,
                    eval_config_hash=state.eval_config_hash,
                    expected_eval_config_hash=expected_eval_hash,
                )
            )
            if stale_stage == "ocr":
                state = dataclasses.replace(
                    state, ocr_done=False, correction_done=False, evaluation_done=False
                )
            elif stale_stage == "correction":
                state = dataclasses.replace(state, correction_done=False, evaluation_done=False)
            elif stale_stage == "evaluation":
                state = dataclasses.replace(state, evaluation_done=False)
            view = store.to_view(file, state)
            views[view.source_id] = view
        return views

    def load_view(self, config: Config, source: Path) -> DocumentStateView | None:
        return self._state_store(config).load_view(source)

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

    def best_ollama_model(self, category: ModelCategory, profile: HardwareProfile) -> str:
        """Return the BEST Ollama model tag for *category* on *profile*."""
        return self._recommendations.best_ollama_model(category, profile)

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
        """Return a :class:`ModelRating` per tag for *task* on *profile*."""
        return self._recommendations.rate_ollama_models(
            tags,
            task,
            profile,
            ollama_url,
            cached_only=cached_only,
            system_memory_gb=system_memory_gb,
            card_gb=card_gb,
        )

    def first_run_model_settings(self, profile: HardwareProfile) -> dict[str, str]:
        """Return the first-start model settings (BEST picks) for *profile*."""
        return self._recommendations.first_run_model_settings(profile)

    def on_ollama_models_changed(self, ollama_url: str) -> None:
        """Evict the model list and classifications of *ollama_url* (call after a pull)."""
        self._recommendations.on_ollama_models_changed(ollama_url)

    def default_model_for_task(self, provider: str, task: str) -> str:
        """Resolve default model for provider and task."""
        return default_model_for_task(provider, task)

    def list_ocr_engines(self) -> list[str]:
        """Return supported OCR engine identifiers."""
        return list(SUPPORTED_OCR_ENGINES)

    def default_preprocess_for_engine(self, engine: str) -> str:
        """Return the recommended preprocessing method for *engine*."""
        return _infra_default_preprocess_for_engine(engine)

    def list_supported_suffixes(self) -> frozenset[str]:
        """Return input file suffixes accepted by the pipeline."""
        return SUPPORTED_SUFFIXES

    def list_correction_prompt_presets(self) -> dict[str, str]:
        """Return language-name → prompt mapping for the built-in correction presets."""
        from ..infrastructure.correction import PRESET_PROMPTS  # noqa: PLC0415

        return dict(PRESET_PROMPTS)

    def default_correction_prompt(self) -> str:
        """Return the English (fallback) correction prompt."""
        from ..infrastructure.correction import ENGLISH_PROMPT  # noqa: PLC0415

        return ENGLISH_PROMPT

    def correction_instructions(
        self,
        *,
        prompt: str,
        language: str,
        level: str = DEFAULT_LEVEL,
        output_mode: str = "review",
        text_format: TextFormat = "plain",
        answer_format: str = "",
        compact_json: bool = False,
    ) -> tuple[str, str]:
        """Return ``(policy, fixed)``: the teacher's instructions and the answer format.

        Built from the same code path the corrector uses, so what a dialog shows is exactly
        what is sent.  *text_format* defaults to plain text; for Markdown texts the caller
        passes ``"markdown"`` and the fixed part gains the notation hint.  *answer_format*
        is the teacher's own text for *output_mode* (``""`` = the built-in one).
        *compact_json* is the chosen model's request-profile choice, so the dialog shows the
        request of that model and not another one's.
        """
        from ..infrastructure.correction import instruction_parts  # noqa: PLC0415

        return instruction_parts(
            prompt,
            language,
            level=level,
            text_format=text_format,
            mode=normalize_output_mode(output_mode),
            answer_format=answer_format,
            compact_json=compact_json,
        )

    def default_answer_formats(self) -> tuple[str, str]:
        """Return the built-in answer formats ``(with explanations, corrections only)``."""
        from ..domain.answer_format import REVIEW_FORMAT, TEXT_FORMAT  # noqa: PLC0415

        return REVIEW_FORMAT, TEXT_FORMAT

    def correction_notation_hint(self) -> str:
        """Return what the app adds to the proofreading instructions for Markdown texts."""
        from ..domain.notation import with_notation_hint  # noqa: PLC0415

        return with_notation_hint("", "markdown", task="correction").strip()

    def evaluation_fixed_instructions(self, text_format: TextFormat = "plain") -> str:
        """Return what the app appends to an evaluation prompt: the notation hint, if any.

        Markdown is the only format with a hint, so for plain text this is ``""``.
        """
        from ..domain.notation import with_notation_hint  # noqa: PLC0415

        return with_notation_hint("", text_format, task="evaluation").strip()

    def collect_input_files_from_dir(self, input_dir: Path, recursive: bool) -> list[Path]:
        """Collect supported input files without a full Config (for partial GUI state)."""
        return self._discovery.collect_input_files_from_dir(input_dir, recursive)

    def build_lookup_config(self, input_dir: Path, output_dir: Path) -> Config:
        """Return a minimal Config for reading cached state before output_dir is confirmed."""
        return Config(
            input_dir=input_dir,
            output_dir=output_dir,
            recursive=DEFAULTS["recursive"],
            debug=DEFAULTS["debug"],
            ocr=OcrConfig(
                engine=DEFAULTS["ocr_engine"],
                model=DEFAULTS["ocr_model"],
                preprocess_method=DEFAULTS["preprocess_method"],
            ),
            language=DEFAULTS["language"],
            ollama_url=DEFAULTS["ollama_url"],
            correction_enabled=DEFAULTS["correction_enabled"],
            correction_provider=DEFAULTS["correction_provider"],
            correction_model=DEFAULTS["correction_model"],
        )

    def preprocess_preview(
        self,
        source_path: Path,
        ocr: OcrConfig,
        tmp_dir: Path,
    ) -> tuple[Path, Path, list[str]]:
        """Run preprocessing preview for one source file."""
        return preprocess_preview(
            source_path, ocr, tmp_dir, self._component_factory.build_preprocessor
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

    def forget_installed_models(self) -> None:
        """Ask spaCy again which models are installed (after a download or an install)."""
        self._installed_models = None
        self._warned.clear()

    def export_docx(
        self,
        source: Path,
        raw_text: str,
        correction_text: str,
        preview_img: str,
        out_path: Path,
        *,
        options: DocxExport | None = None,
    ) -> None:
        """Generate a DOCX for *source* from cached text and write it to *out_path*.

        *options* selects the layout, the notation of the texts, the review to quote and the
        language of its headings.
        """
        from ..infrastructure.docx_builder import DocxDocumentCreator  # noqa: PLC0415

        chosen = options or DocxExport()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        creator = DocxDocumentCreator(
            fmt=chosen.docx_format, labels_language=chosen.labels_language
        )
        creator.create(
            raw_text,
            correction_text,
            out_path,
            source.stem,
            source_image=preview_img or None,
            text_format=TextFormatter.normalize(chosen.text_format),
            review=chosen.review,
        )

    def export_text(
        self,
        source: Path,
        raw_text: str,
        out_path: Path,
        *,
        text_format: str = "plain",
        anonymize: bool = False,
        language: str = "English",
        anonymizer_config: object | None = None,
        protected: Collection[Path] = (),
    ) -> None:
        """Write the extracted text of *source* to *out_path* as a UTF-8 text file.

        Markdown is written as plain text.  With *anonymize* the personal data is replaced
        by placeholders (``[PERSON_1]``, ...) using the local anonymizer, on the text in its
        stored notation (as the pipeline and the privacy preview do); the placeholder mapping
        is dropped, so the original names cannot be recovered from the file.

        Args:
            protected: Further documents that must not be overwritten (the other inputs of
                the same export); *source* itself is always protected.

        Raises:
            ValueError: *out_path* is *source* or one of *protected* (an input document).
            ImportError: *anonymize* is set and spacy is not installed.
            OSError: the file cannot be written, or a required spacy model is not downloaded.
        """
        target = out_path.resolve()
        if target == source.resolve() or target in {p.resolve() for p in protected}:
            raise ValueError(
                f"{out_path.name} is an input document and would be overwritten; "
                "choose another name or folder."
            )
        text = raw_text
        if anonymize:
            text, _mapping = self.anonymize_preview(text, language, anonymizer_config)
        text = TextFormatter.plain_text(text, text_format)
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
        if engine == "tesseract":
            if self._tesseract_installed():
                return True, True, "✓ tesseract binary found"
            return False, False, "✗ tesseract not found — install Tesseract OCR"
        if engine == "paddleocr":
            if not self.is_module_importable("paddleocr"):
                return (
                    False,
                    False,
                    "✗ paddleocr not installed — run: pip install paddlepaddle paddleocr",
                )
            from ..infrastructure.ocr_processor import paddle_gpu_available  # noqa: PLC0415

            accel = "GPU" if paddle_gpu_available() else "CPU only"
            return True, True, f"✓ paddleocr available · {accel}"
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

    def estimate_text_size(
        self,
        config: Config,
        raw_text: str,
        text_format: TextFormat = "plain",
        hardware: HardwareDetection | None = None,
    ) -> TextSizeEstimate:
        """Return how long *raw_text* is and how many proofreading requests it takes.

        The count comes from the corrector the run builds and from
        :meth:`~teachers_teammate.infrastructure.correction.ChunkingCorrector.split`, so the
        estimate and the run cannot disagree.  Building the corrector is cheap: neither the
        Ollama client nor the corrector contacts the server.

        It is an estimate: the text really sent is anonymized when personal-info removal is
        on, and the content review reads the proofread text, not the OCR text.

        An online proofreading provider brings its own request limit, which the app does not
        set, so there is nothing to count: the answer is the word count alone.  That also keeps
        the GUI thread from importing LangChain, reading the keychain and building a client
        every few hundred milliseconds.

        Args:
            hardware: The detection the caller already has.  ``None`` keeps the base window.
                The caller resolves it, because resolving it here would run the graphics probe
                on the GUI thread.
        """
        from ..infrastructure.evaluation import NativeOllamaEvaluator  # noqa: PLC0415
        from ..infrastructure.reporting import CallbackReporter  # noqa: PLC0415
        from ..infrastructure.stage_builder import StageBuilder  # noqa: PLC0415

        words = len(TextFormatter.plain_text(raw_text, text_format).split())
        if config.correction_provider != "ollama":
            return TextSizeEstimate(
                words=words, pieces=None, words_per_piece=None, content_review_fits=None
            )
        builder = StageBuilder(
            config,
            self._component_factory,
            CallbackReporter(lambda _line: None),  # an estimate says nothing on the console
            hardware,
        )
        corrector = builder.build_corrector()
        pieces: int | None = None
        words_per_piece: int | None = None
        if corrector is not None:
            pieces, words_per_piece = proofreading_pieces(
                corrector, raw_text, config.language, text_format=text_format
            )
        content_review_fits: bool | None = None
        if (
            corrector is not None
            and config.evaluation_enabled
            and config.evaluate_provider == "ollama"
        ):
            # Only a NativeOllamaEvaluator answers ``fits``; an online one brings its own limit.
            evaluator = builder.build_evaluation(corrector)
            if isinstance(evaluator, NativeOllamaEvaluator):
                content_review_fits = evaluator.fits(
                    raw_text, config.language, text_format=text_format
                )
        return TextSizeEstimate(
            words=words,
            pieces=pieces,
            words_per_piece=words_per_piece,
            content_review_fits=content_review_fits,
        )

    def get_provider_data_region(self, provider: str) -> str:
        """Return where *provider* processes the data: ``"eu"``, ``"non_eu"`` or ``"unknown"``."""
        return get_provider_data_region(provider)

    def describe_anonymizer_models(self, config: Config) -> list[str]:
        """Return the spaCy models personal-info removal loads for *config*, or ``[]`` when off.

        Resolved the same way a run resolves them, so the status box names the models a run
        would really use.  A missing automatic model is not reported here: the box only
        says what runs, and the run itself warns.
        """
        if not config.anonymization_enabled:
            return []
        from ..infrastructure.anonymizer import (  # noqa: PLC0415
            anonymizer_config_from,
            resolve_anonymizer_config,
        )
        from ..infrastructure.spacy_models import spacy_model_for  # noqa: PLC0415
        from ..infrastructure.system_locale import system_language  # noqa: PLC0415

        resolved = resolve_anonymizer_config(
            anonymizer_config_from(config),
            document_language=config.language,
            system_language=system_language(),
            installed_models=self._remembered_installed_models(),
        )
        models = [resolved.primary_model or spacy_model_for(config.language)]
        if resolved.secondary_model:
            models.append(resolved.secondary_model)
        return models

    def describe_sent_texts(
        self,
        config: Config,
        raw_text: str,
        correction_text: str = "",
        *,
        text_format: TextFormat = "plain",
    ) -> SentTexts:
        """Return what every step that leaves this computer receives, exactly as sent.

        Uses the shared anonymizer (and so its cache), so opening the dialog costs one spaCy
        pass per text and settings for the whole session — the same result a run sends.  The
        prompts are built with the code the stages use, so the dialog cannot drift from them.

        Args:
            config: The live settings; decides the steps and the prompts.
            raw_text: The extracted text of the file.
            correction_text: Its proofread text (empty when there is none yet).
            text_format: Notation of the texts (``"plain"`` or ``"markdown"``).
        """
        flow = self.describe_data_flow(config)
        return describe_sent_texts(
            flow,
            raw_text=raw_text,
            correction_text=correction_text,
            anonymize=self._sent_text_anonymizer(config),
            preview_extracted=config.anonymization_enabled,
            correction_prompt=self._sent_correction_prompt(config, text_format),
            evaluation_prompt=self._sent_evaluation_prompt(config, text_format),
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

    def _sent_correction_prompt(self, config: Config, text_format: TextFormat) -> str:
        """Return the instructions the corrector sends, or ``""`` when it sends none."""
        mode = normalize_output_mode(config.correction_output)
        policy, fixed = self.correction_instructions(
            prompt=config.correction_prompt,
            language=config.language,
            level=config.correction_level,
            output_mode=mode,
            text_format=text_format,
            answer_format=(
                config.answer_format_review if mode == "review" else config.answer_format_text
            ),
            compact_json=config.correction_profile.compact_json,
        )
        return f"{policy}\n\n{fixed}"

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
    ) -> RunPreflight:
        """Return the unmet requirements before a run, or an all-clear result.

        With text recognition switched off and *files* given, the result also lists the files
        the run leaves out (``skipped``), and is blocked (``nothing_to_read``) when none of
        them can be used.  Such a check opens PDFs, see :meth:`plan_recognition`.
        """
        skipped: tuple[Path, ...] = ()
        if not config.recognition_enabled and files:
            plan = self.plan_recognition(
                files, recognised=recognised, pdf_text_layer=config.ocr.pdf_text_layer
            )
            if not plan.readable:
                return RunPreflight(nothing_to_read=True, skipped=plan.skipped)
            skipped = plan.skipped
        result = self._check_requirements(
            config, memory=memory, recognition=recognition, fresh=fresh
        )
        return dataclasses.replace(result, skipped=skipped) if skipped else result

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
        reads no page (only text files, or text that is already recognised): a missing
        Tesseract must not stop it (see :meth:`needs_text_recognition`).  ``fresh=True`` asks the
        Ollama server again instead of using the list it gave within the last minutes: for a
        teacher who just ran ``ollama pull`` in a terminal and pressed "Check again".
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
                return RunPreflight(ollama_unreachable=unreachable)
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
        return RunPreflight(memory_warning=self.memory_warning_for_run(config) if memory else "")

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

    @staticmethod
    def _tesseract_installed() -> bool:
        """Return whether the Tesseract program is on the PATH."""
        return shutil.which("tesseract") is not None

    def _ocr_engine_issues(self, engine: str) -> list[RequirementIssue]:
        """Return an issue when the text-recognition *engine* is not installed.

        The same two checks as the connection check, without its graphics-card probe (that
        one imports Paddle).  Ollama is checked by the preflight before the stages.
        """
        if engine == "tesseract" and not self._tesseract_installed():
            return [
                RequirementIssue(
                    "Tesseract is not installed.", IssueKind.ENGINE_MISSING, engine=engine
                )
            ]
        if engine == "paddleocr" and not self.is_module_importable("paddleocr"):
            return [
                RequirementIssue(
                    "PaddleOCR is not installed.", IssueKind.ENGINE_MISSING, engine=engine
                )
            ]
        return []

    def _llm_stage_issues(self, config: Config, stage: str) -> list[RequirementIssue]:
        """Return what proofreading or the content review still needs (package, key, model)."""
        provider = config.correction_provider if stage == "correction" else config.evaluate_provider
        model = (
            (config.effective_correction_model or "")
            if stage == "correction"
            else (config.effective_evaluate_model or "")
        )
        name = str(get_provider_info(provider).get("display_name", provider))
        if self.missing_provider_package(provider):
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

    def _name_detection_issues(self, language: str) -> list[RequirementIssue]:
        """Return what removing names still needs: spaCy itself or the model of *language*."""
        installed_models = self.list_installed_spacy_models()
        required = self.spacy_model_for_language(language)
        if installed_models:
            if required in installed_models:
                return []
            message = f"spaCy NER model '{required}' is not downloaded."
        else:
            try:
                import spacy  # noqa: PLC0415, F401
            except ImportError:
                message = "spaCy is not installed. Install it under Add-ons in the app."
            else:
                message = f"spaCy NER model '{required}' is not downloaded."
        return [RequirementIssue(message, IssueKind.NAME_DETECTION_MISSING, models=(required,))]

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

    def anonymizer_available(self, language: str) -> bool:
        """Return whether personal information can be hidden for *language* right now.

        Cheap enough for the GUI to ask when it builds a menu or a panel: spaCy must be
        importable and the model of *language* must be downloaded.  The GUI uses this to
        disable "Hide personal information in texts" instead of failing when it is used.
        """
        from importlib.util import find_spec as find_module_spec  # noqa: PLC0415

        if find_module_spec("spacy") is None:
            return False
        return self.spacy_model_for_language(language) in self.list_installed_spacy_models()

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
        gpu_packages: dict[str, str] | None = None,
        ollama_models: list[str] | None = None,
        ollama_url: str = "",
        langchain_packages: dict[str, str] | None = None,
    ) -> dict[str, bool]:
        """Probe availability of optional components and return a status map.

        Keys produced (all optional — only those whose *inputs* are supplied):
        - ``"tesseract"`` — always checked
        - ``"paddleocr"`` — always checked
        - ``"provider:{name}"`` — for each entry in *provider_modules*
        - ``"spacy:{model_id}"`` — for each id in *spacy_models*
        - ``"gpu:nvidia"`` — when *gpu_packages* contains ``"gpu:nvidia"``
        - ``"gpu:amd"`` — when *gpu_packages* contains ``"gpu:amd"``
        - ``"ollama:{model}"`` — for each model in *ollama_models*
        - ``"langchain:{key}"`` — for each entry in *langchain_packages*
        """
        result: dict[str, bool] = {}
        result["tesseract"] = shutil.which("tesseract") is not None
        result["pytesseract"] = importlib.util.find_spec("pytesseract") is not None
        result["paddleocr"] = importlib.util.find_spec("paddleocr") is not None

        if provider_modules:
            for name, module_name in provider_modules.items():
                result[f"provider:{name}"] = importlib.util.find_spec(module_name) is not None

        if spacy_models:
            installed_spacy = set(self.list_installed_spacy_models())
            for model_id in spacy_models:
                result[f"spacy:{model_id}"] = model_id in installed_spacy

        if gpu_packages:
            for key, pkg_module in gpu_packages.items():
                result[key] = importlib.util.find_spec(pkg_module) is not None

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

    def pending_gpu_monitoring_addon(self) -> str | None:
        """Return the live-GPU-monitoring add-on this computer needs but lacks, or None.

        ``None`` unless the app can install add-ons (frozen build), a supported GPU is
        present and its package is missing.  Installs nothing.  Slow (hardware probe):
        call from a background thread.

        Returns:
            ``"nvidia"`` or ``"amd"``, or ``None``.
        """
        from ..infrastructure.addon_manager import (  # noqa: PLC0415
            can_install_addons,
            gpu_monitoring_addon,
            is_addon_importable,
        )

        if not can_install_addons():
            return None
        addon = gpu_monitoring_addon(gpu.vendor for gpu in self.detect_gpus())
        if addon is None or is_addon_importable(addon):
            return None
        return addon

    def install_gpu_monitoring(self, addon: str) -> None:
        """Install the GPU-monitoring *addon* unattended and make it importable.

        Uses a short network timeout so a network that drops traffic fails instead of
        hanging.  Slow: call from a background thread.

        Raises:
            OSError: if the packages directory cannot be written.
            RuntimeError: if pip fails (offline, blocked, …).
        """
        from ..infrastructure.addon_manager import (  # noqa: PLC0415
            BACKGROUND_PIP_TIMEOUT_S,
            clear_stale_modules,
            get_packages_dir,
            inject_packages_dir,
            install_addon_subprocess,
        )

        install_addon_subprocess(
            addon,
            get_packages_dir(),
            lambda _line: None,
            network_timeout=BACKGROUND_PIP_TIMEOUT_S,
        )
        inject_packages_dir()
        clear_stale_modules(addon)

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

    def inject_addon_packages_dir(self) -> None:
        """Prepend the addon packages directory to sys.path (idempotent)."""
        from ..infrastructure.addon_manager import inject_packages_dir  # noqa: PLC0415

        inject_packages_dir()

    # ── Storage root / config path helpers ───────────────────────────────

    def default_config_path(self) -> Path:
        """Return the default ``ocr.toml`` path inside the platform storage root."""
        return _infra_default_config_path()

    def resolve_config_path(self, explicit: str | None) -> Path | None:
        """Return the config file path to load, or ``None`` when none applies."""
        return _infra_resolve_config_path(explicit)

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

    def record_manual_ocr_edit(
        self,
        config: Config,
        *,
        source: Path,
        edited_text: str,
        preview_img: str,
        raw_text_format: str | None = None,
    ) -> DocumentState | None:
        return self._commands.record_manual_ocr_edit(
            config,
            source=source,
            edited_text=edited_text,
            preview_img=preview_img,
            raw_text_format=raw_text_format,
        )

    def record_manual_correction_edit(
        self,
        config: Config,
        *,
        source: Path,
        raw_text: str,
        preview_img: str,
        edited_text: str,
        raw_text_format: str = "plain",
    ) -> DocumentState | None:
        return self._commands.record_manual_correction_edit(
            config,
            source=source,
            raw_text=raw_text,
            preview_img=preview_img,
            edited_text=edited_text,
            raw_text_format=raw_text_format,
        )
