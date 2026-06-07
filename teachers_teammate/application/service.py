"""Application-layer orchestration services used by GUI and CLI adapters."""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
import importlib.util
from pathlib import Path
import shutil
import threading
import time
import uuid

from ..config import Config
from ..infrastructure.addon_manager import ADDON_AMD as ADDON_AMD  # noqa: PLC0414
from ..infrastructure.addon_manager import ADDON_NVIDIA as ADDON_NVIDIA  # noqa: PLC0414
from ..infrastructure.addon_manager import ADDON_PADDLE as ADDON_PADDLE  # noqa: PLC0414
from ..infrastructure.addon_manager import ADDON_PRIVACY as ADDON_PRIVACY  # noqa: PLC0414
from ..infrastructure.anonymizer import (
    DEFAULT_PATTERNS as DEFAULT_ANONYMIZER_PATTERNS,  # noqa: F401
)

# ── Re-exports for GUI/CLI use — keeps infrastructure types out of adapter layers ──
from ..infrastructure.anonymizer import AnonymizerConfig as AnonymizerConfig  # noqa: PLC0414
from ..infrastructure.file_discovery import FileDiscovery
from ..infrastructure.gpu_detector import GpuInfo as GpuInfo  # noqa: PLC0414
from ..infrastructure.llm_factory import (
    check_provider_connection,
    default_model_for_task,
    get_provider_info,
    list_provider_models,
    list_providers,
)
from ..infrastructure.ocr_processor import (
    SUPPORTED_OCR_ENGINES,
)
from ..infrastructure.ocr_processor import (
    default_preprocess_for_engine as _infra_default_preprocess_for_engine,
)
from ..infrastructure.pipeline import OCRPipeline, preprocess_preview
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


@dataclasses.dataclass(frozen=True)
class RequirementIssue:
    """A single unmet requirement for enabling a pipeline stage."""

    message: str
    fixable_via_downloads: bool


class ProcessingApplicationService:
    """Coordinates processing and state transitions behind a small app-facing API."""

    _MODEL_CACHE_TTL_S: float = 300.0

    def __init__(
        self,
        *,
        discovery: FileDiscovery | None = None,
        pipeline_factory: Callable[..., OCRPipeline] | None = None,
        state_repository_factory: Callable[[Path], StateRepository] | None = None,
    ) -> None:
        self._discovery = discovery or FileDiscovery()
        self._pipeline_factory = pipeline_factory or OCRPipeline
        self._state_repository_factory = state_repository_factory or StateRepository
        self._anonymizer_cache: dict[tuple, object] = {}
        self._model_cache: dict[str, tuple[float, list[str]]] = {}
        self._commands = ApplicationCommands(
            discovery=self._discovery,
            state_store_factory=self._state_store,
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
        on_file_started=None,
        on_ocr_done=None,
        on_file_done=None,
    ) -> int:
        from ..infrastructure.pipeline import PipelineDependencies  # noqa: PLC0415

        pipeline = self._pipeline_factory(
            config,
            stop_event=stop_event,
            config_file=config_file,
            selected_source_paths=selected_source_paths,
            dependencies=PipelineDependencies(reporter=reporter) if reporter is not None else None,
            on_file_started=on_file_started,
            on_ocr_done=on_ocr_done,
            on_file_done=on_file_done,
        )
        return pipeline.run()

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
        expected_ocr_hash = compute_ocr_config_hash(config, ocr_prompt)
        expected_corr_hash = compute_correction_config_hash(config)
        expected_eval_hash = compute_eval_config_hash(config)

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
                    expected_ocr_config_hash=expected_ocr_hash,
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

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        """Return available models for a provider (result is TTL-cached per instance)."""
        cached = self.get_cached_models(provider, base_url=base_url)
        if cached is not None:
            return cached
        kwargs: dict[str, str] = {"base_url": base_url} if base_url else {}
        result = list_provider_models(provider, **kwargs)
        key = f"{provider}:{base_url}" if base_url else provider
        self._model_cache[key] = (time.monotonic(), result)
        return result

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        """Return cached model list for *provider* if still within TTL, else ``None``."""
        key = f"{provider}:{base_url}" if base_url else provider
        entry = self._model_cache.get(key)
        if entry and (time.monotonic() - entry[0]) < self._MODEL_CACHE_TTL_S:
            return entry[1]
        return None

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        """Evict the cached model list for *provider* (forces re-fetch on next call)."""
        key = f"{provider}:{base_url}" if base_url else provider
        self._model_cache.pop(key, None)

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

    def collect_input_files_from_dir(self, input_dir: Path, recursive: bool) -> list[Path]:
        """Collect supported input files without a full Config (for partial GUI state)."""
        return self._discovery.collect_input_files_from_dir(input_dir, recursive)

    def build_lookup_config(self, input_dir: Path, output_dir: Path) -> Config:
        """Return a minimal Config for reading cached state before output_dir is confirmed."""
        from ..config import DEFAULTS, OcrConfig  # noqa: PLC0415

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
        method: str,
        tmp_dir: Path,
    ) -> tuple[Path, Path, list[str]]:
        """Run preprocessing preview for one source file."""
        return preprocess_preview(source_path, method, tmp_dir)

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
        from ..infrastructure.anonymizer import SpacyAnonymizer  # noqa: PLC0415

        anon_config: AnonymizerConfig = (
            config if isinstance(config, AnonymizerConfig) else AnonymizerConfig()
        )
        cache_key = (language, anon_config)
        cached = self._anonymizer_cache.get(cache_key)
        if cached is None:
            cached = SpacyAnonymizer(language, anon_config)
            self._anonymizer_cache[cache_key] = cached
        assert isinstance(cached, SpacyAnonymizer)
        return cached.anonymize(text)

    def clear_anonymizer_cache(self) -> None:
        """Evict all cached SpacyAnonymizer instances."""
        self._anonymizer_cache.clear()

    def export_docx(
        self,
        source: Path,
        raw_text: str,
        correction_text: str,
        preview_img: str,
        out_path: Path,
        *,
        docx_format: str = "table",
    ) -> None:
        """Generate a DOCX for *source* from cached text and write it to *out_path*."""
        from ..infrastructure.docx_builder import DocxDocumentCreator  # noqa: PLC0415

        out_path.parent.mkdir(parents=True, exist_ok=True)
        creator = DocxDocumentCreator(fmt=docx_format)
        creator.create(
            raw_text,
            correction_text,
            out_path,
            source.stem,
            source_image=preview_img or None,
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

    def _check_ollama(self, url: str, model: str = "") -> tuple[bool, bool, str]:
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        return OllamaClient(url).check_connection(model)

    def _check_engine(self, engine: str, url: str, *, model: str = "") -> tuple[bool, bool, str]:
        if engine == "ollama":
            return self._check_ollama(url, model)
        if engine == "tesseract":
            if shutil.which("tesseract") is not None:
                return True, True, "✓ tesseract binary found"
            return False, False, "✗ tesseract not found — install Tesseract OCR"
        if engine == "paddleocr":
            if importlib.util.find_spec("paddleocr") is None:
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

    def check_ollama_models_ready(self, config: Config) -> list[str]:
        """Return names of Ollama models that are configured but not installed.

        Checks OCR, correction, and evaluation stages.  Uses the in-process
        model cache (5-minute TTL) to avoid redundant network calls.
        """
        needs_ollama = (
            config.ocr.engine == "ollama"
            or (config.correction_enabled and config.correction_provider == "ollama")
            or (config.evaluation_enabled and config.evaluate_provider == "ollama")
        )
        if not needs_ollama:
            return []
        from ..infrastructure.ollama_utils import models_include  # noqa: PLC0415

        url = config.ollama_url
        installed = list(self.list_provider_models("ollama", base_url=url))
        if not installed:  # Ollama unreachable — don't block the run
            return []
        missing: list[str] = []
        if config.ocr.engine == "ollama":
            model = config.ocr.model or ""
            if model and not models_include(model, installed):
                missing.append(model)
        if config.correction_enabled and config.correction_provider == "ollama":
            model = config.effective_correction_model or ""
            if model and model not in missing and not models_include(model, installed):
                missing.append(model)
        if config.evaluation_enabled and config.evaluate_provider == "ollama":
            model = config.effective_evaluate_model or ""
            if model and model not in missing and not models_include(model, installed):
                missing.append(model)
        return missing

    def check_stage_requirements(self, config: Config, stage: str) -> list[RequirementIssue]:
        """Return unmet requirements for enabling *stage* ('correction', 'evaluation', 'anonymizer').

        Empty list means all requirements are satisfied.
        """
        issues: list[RequirementIssue] = []
        if stage in ("correction", "evaluation"):
            provider = (
                config.correction_provider if stage == "correction" else config.evaluate_provider
            )
            model = (
                (config.effective_correction_model or "")
                if stage == "correction"
                else (config.effective_evaluate_model or "")
            )
            if provider == "ollama":
                from ..infrastructure.ollama_utils import models_include  # noqa: PLC0415

                installed = self.list_provider_models("ollama", base_url=config.ollama_url)
                if not installed:
                    issues.append(
                        RequirementIssue(
                            "Ollama is not running. Start it with `ollama serve`.",
                            fixable_via_downloads=False,
                        )
                    )
                elif model and not models_include(model, installed):
                    issues.append(
                        RequirementIssue(
                            f"Model '{model}' is not downloaded.",
                            fixable_via_downloads=True,
                        )
                    )
        elif stage == "anonymizer":
            installed_models = self.list_installed_spacy_models()
            if not installed_models:
                try:
                    import spacy  # noqa: PLC0415, F401

                    required = self.spacy_model_for_language(config.language)
                    issues.append(
                        RequirementIssue(
                            f"spaCy NER model '{required}' is not downloaded.",
                            fixable_via_downloads=True,
                        )
                    )
                except ImportError:
                    issues.append(
                        RequirementIssue(
                            "spaCy is not installed. Use the Downloads dialog to install it.",
                            fixable_via_downloads=True,
                        )
                    )
            else:
                required = self.spacy_model_for_language(config.language)
                if required not in installed_models:
                    issues.append(
                        RequirementIssue(
                            f"spaCy NER model '{required}' is not downloaded.",
                            fixable_via_downloads=True,
                        )
                    )
        return issues

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
        from ..infrastructure.anonymizer import _spacy_model_for  # noqa: PLC0415

        return _spacy_model_for(language)

    def list_installed_spacy_models(self) -> list[str]:
        """Return sorted names of installed spaCy model packages, or ``[]`` if spaCy is absent."""
        try:
            import spacy.util  # noqa: PLC0415

            return sorted(spacy.util.get_installed_models())
        except Exception:  # noqa: BLE001  # spaCy may be absent or its model registry may fail; degrade to an empty list
            return []

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
            try:
                pulled = set(self.list_provider_models("ollama", base_url=ollama_url))
            except Exception:  # noqa: BLE001  # Ollama may be unreachable; treat as no models pulled
                pulled = set()
            for model in ollama_models:
                result[f"ollama:{model}"] = model in pulled

        if langchain_packages:
            for key, module_name in langchain_packages.items():
                result[f"langchain:{key}"] = importlib.util.find_spec(module_name) is not None

        return result

    def is_addon_available(self, addon: str) -> bool:
        """Return whether the named addon package is importable."""
        from ..infrastructure.addon_manager import is_addon_importable  # noqa: PLC0415

        return is_addon_importable(addon)

    def is_module_importable(self, module: str) -> bool:
        """Return True if *module* can be imported (the package is installed)."""
        return importlib.util.find_spec(module) is not None

    # ── GPU / addon installer helpers ─────────────────────────────────────

    def detect_gpus(self) -> list[GpuInfo]:
        """Return all detected NVIDIA and AMD GPUs."""
        from ..infrastructure.gpu_detector import detect_gpus as _detect  # noqa: PLC0415

        return _detect()

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
    ) -> DocumentState | None:
        return self._commands.record_manual_ocr_edit(
            config, source=source, edited_text=edited_text, preview_img=preview_img
        )

    def record_manual_correction_edit(
        self,
        config: Config,
        *,
        source: Path,
        raw_text: str,
        preview_img: str,
        edited_text: str,
    ) -> DocumentState | None:
        return self._commands.record_manual_correction_edit(
            config,
            source=source,
            raw_text=raw_text,
            preview_img=preview_img,
            edited_text=edited_text,
        )
