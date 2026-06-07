"""OCR pipeline orchestration.

This module is the composition root for a single processing run.
It wires concrete infrastructure services together into a runnable pipeline
and delegates all actual work to the stage services in `infrastructure/workflow/`.

Responsibilities
----------------
- `OCRPipeline` — wires `CacheReconciliationService`, the stage services
  (`PreprocessService`, `OCRStageService`), and the workflow coordinator
  function into a runnable unit.
- `PipelineDependencies` / `PipelineComponentFactory` — injection seams for
  tests and adapters that need to substitute specific services or constructors.
- `collect_files` — thin helper that returns supported input files for a config;
  delegates to `FileDiscovery`.
- `preprocess_preview` — standalone single-file preprocessing helper used by
  the GUI preview dialog without running OCR or correction.

What this module does NOT do
-----------------------------
- It does not implement any stage logic — all stage work lives in `workflow/`.
- It does not define cache or freshness policy — that is `domain/freshness.py`.
- It does not persist state — that is `StateRepository`.
- It does not format user-facing results — that is the application layer.
- It does not choose where its narrative goes — the injected `Reporter` does.

Public API
----------
:class:`OCRPipeline`
    Runs the full preprocess → OCR → correction → DOCX pipeline.

:func:`collect_files`
    List supported input files without running any processing.

:func:`preprocess_preview`
    Preprocess a single source file (image or PDF) and return the paths to
    the original image and the preprocessed result.  Used by the GUI preview
    dialog without spawning a full pipeline run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
import threading

from ..config import Config
from ..exceptions import OllamaConnectionError, ProviderNotAvailableError
from .file_discovery import FileDiscovery
from .image_preprocessor import HandwritingPreprocessor
from .input_provider_factory import get_input_provider, supported_suffixes
from .ollama_utils import OllamaClient
from .preview_image_store import PreviewImageStore
from .reporting import Reporter, StdoutReporter
from .stage_builder import PipelineComponentFactory as PipelineComponentFactory  # noqa: PLC0414
from .stage_builder import StageBuilder
from .state_repository import StateRepository
from .storage_root import resolve_artifact_dir, resolve_storage_root
from .workflow.cache_service import CacheReconciliationService
from .workflow.coordinator import run_files as _run_files_coordinator
from .workflow.file_processor import FileProcessor, FileProcessorConfig
from .workflow.ocr_service import OCRStageService
from .workflow.preprocess_service import PreprocessService

_LINE = "=" * 64
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineDependencies:
    """Optional runtime dependencies for explicit injection in tests/adapters."""

    artifact_store: PreviewImageStore | None = None
    state_repo: StateRepository | None = None
    cache_service: CacheReconciliationService | None = None
    preprocess_service: PreprocessService | None = None
    ocr_stage_service: OCRStageService | None = None
    component_factory: PipelineComponentFactory | None = None
    # Sink for the run narrative/warnings; defaults to stdout (CLI behaviour).
    reporter: Reporter | None = None


# ── Public helpers ─────────────────────────────────────────────────────────


def collect_files(config: Config) -> list[Path]:
    """Return all supported input files under ``config.input_dir``.

    Args:
        config: Pipeline configuration.

    Returns:
        Sorted list of :class:`~pathlib.Path` objects matching
        :data:`~teachers_teammate.config.SUPPORTED_SUFFIXES`.
    """
    return FileDiscovery().collect_input_files(config)


def preprocess_preview(
    source_path: Path,
    method: str,
    tmp_dir: Path,
) -> tuple[Path, Path, list[str]]:
    """Preprocess *source_path* and return paths for both the original and processed image.

    Does not run OCR, correction, or DOCX generation.

    For PDF inputs the first page is rendered to ``tmp_dir`` and used as the
    original image.  For image inputs the file is used directly.

    Args:
        source_path: Path to a supported input file (PDF or image).
        method:      Preprocessing method name (see :class:`HandwritingPreprocessor`).
        tmp_dir:     Temporary directory for any intermediate and output files.

    Returns:
        A tuple of ``(original_image_path, preprocessed_image_path, steps_applied)``.
    """
    provider = get_input_provider(source_path.suffix, tmp_dir=tmp_dir)
    payload = provider.load(source_path)
    image_path = next(
        (
            unit.image_path
            for unit in payload.units
            if unit.kind == "image" and unit.image_path is not None
        ),
        None,
    )
    if image_path is None:
        raise ValueError(f"Preview is only available for image-based inputs: '{source_path.name}'.")

    preprocessor = HandwritingPreprocessor(tmp_dir=tmp_dir, save_steps=False, method=method)
    out_path, steps = preprocessor.preprocess(image_path)
    return image_path, out_path, steps


# ── Pipeline ───────────────────────────────────────────────────────────────


class OCRPipeline:
    """Orchestrates the OCR → correction → DOCX pipeline.

    Parameters
    ----------
    config:
        Full runtime configuration.
    config_file:
        Path to the loaded config file, shown in the startup banner.
    stop_event:
        Optional threading event; when set the pipeline finishes the current
        phase and stops before starting the next file.
    dependencies:
        Optional injection seam (tests/adapters).  Notably carries ``reporter``:
        the sink for the run narrative and advisory warnings, defaulting to
        :class:`~teachers_teammate.infrastructure.reporting.StdoutReporter`
        (CLI behaviour).  The GUI injects a reporter that feeds its log pane.
    on_file_started:
        Called at the start of each file: ``(source_id, name, idx, total)``.
    on_ocr_done:
        Called after OCR (before correction) for each file: ``(source_id, name)``.
    on_file_done:
        Called after a file completes (success or failure):
        ``(source_id, name, ok, message, preview_img_path, raw_text, correction_text, evaluation_text)``.
        Text strings are empty when the associated stage output was not produced.
    """

    def __init__(
        self,
        config: Config,
        stop_event: threading.Event | None = None,
        *,
        config_file: Path | None = None,
        selected_source_paths: list[str] | None = None,
        dependencies: PipelineDependencies | None = None,
        on_file_started: Callable[[str, str, int, int], None] | None = None,
        on_ocr_done: Callable[[str, str], None] | None = None,
        on_file_done: Callable[[str, str, bool, str, str, str, str, str], None] | None = None,
    ) -> None:
        self._config = config
        self._config_file = config_file
        self._stop_event = stop_event
        self._selected_source_paths = {
            str(Path(path).resolve()) for path in (selected_source_paths or [])
        }
        self._on_file_started = on_file_started
        self._on_ocr_done = on_ocr_done
        self._on_file_done = on_file_done
        deps = dependencies or PipelineDependencies()
        self._reporter: Reporter = deps.reporter or StdoutReporter()
        self._component_factory = deps.component_factory or PipelineComponentFactory()
        self._artifact_store: PreviewImageStore | None = deps.artifact_store
        self._state_repo: StateRepository | None = deps.state_repo
        self._cache_service: CacheReconciliationService | None = deps.cache_service
        self._preprocess_service: PreprocessService | None = deps.preprocess_service
        self._ocr_stage_service: OCRStageService | None = deps.ocr_stage_service

    # ── public ────────────────────────────────────────────────────────────

    def run(self) -> int:
        """Execute the full pipeline. Returns 0 on success, 1 if any file failed."""
        cfg = self._config
        reporter = self._reporter
        tmp_dir = resolve_artifact_dir(cfg.output_dir)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        artifact_store = self._artifact_store or PreviewImageStore(tmp_dir)
        state_repo = self._state_repo or StateRepository(tmp_dir / "state")

        storage = resolve_storage_root()
        reporter.status(f"Storage root: {storage.path.resolve()} ({storage.source})")

        self._print_banner()
        if cfg.ocr.engine == "ollama":
            try:
                OllamaClient(cfg.ollama_url).check_model(cfg.ocr.model, reporter)
                reporter.status("Checking Ollama connection... OK")
            except (RuntimeError, OllamaConnectionError) as exc:
                reporter.warn(f"\nERROR: {exc}")
                return 1
        try:
            file_processor = self._setup_services(tmp_dir, artifact_store, state_repo)
        except (ValueError, RuntimeError, ProviderNotAvailableError) as exc:
            reporter.warn(f"ERROR: {exc}")
            return 1

        files = collect_files(cfg)
        if self._selected_source_paths:
            files = [file for file in files if str(file.resolve()) in self._selected_source_paths]
        if not files:
            supported = ", ".join(s.lstrip(".").upper() for s in sorted(supported_suffixes()))
            reporter.status(f"No supported files found ({supported}). Nothing to do.")
            return 0

        reporter.status(f"Found {len(files)} file(s) to process.\n")
        succeeded, failed = self._run_files(files, file_processor, tmp_dir, artifact_store)

        reporter.status("")
        reporter.status(_LINE)
        reporter.status(f"Done: {len(succeeded)} succeeded, {len(failed)} failed.")
        if failed:
            reporter.status("Failed files:")
            for name in failed:
                reporter.status(f"  - {name}")
        reporter.status(_LINE)

        return 1 if failed else 0

    def run_preview_only(self) -> int:
        """Preprocess all input files and save images to the temp cache directory.

        No OCR, correction, or DOCX generation is performed.  Use this to
        inspect exactly what images the preprocessor would send to the OCR model.
        """
        reporter = self._reporter
        _cfg, preprocess_service, files, tmp_dir = self._prepare_preview_run()
        if not files:
            supported = ", ".join(s.lstrip(".").upper() for s in sorted(supported_suffixes()))
            reporter.status(f"No supported files found ({supported}). Nothing to do.")
            return 0

        try:
            succeeded, failed = self._run_preview_batch(preprocess_service, files)
        finally:
            self._cleanup_preview_intermediates(tmp_dir)

        reporter.status("")
        reporter.status(_LINE)
        reporter.status(f"Done: {len(succeeded)} preprocessed, {len(failed)} failed.")
        reporter.status(f"Images saved to: {tmp_dir.resolve()}")
        if failed:
            reporter.status("Failed files:")
            for name in failed:
                reporter.status(f"  - {name}")
        reporter.status(_LINE)

        return 1 if failed else 0

    def _prepare_preview_run(self) -> tuple[Config, PreprocessService, list[Path], Path]:
        cfg = self._config
        reporter = self._reporter
        tmp_dir = resolve_artifact_dir(cfg.output_dir)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        reporter.status(_LINE)
        reporter.status("Teacher's Teammate — Preview (preprocess only)")
        reporter.status(_LINE)
        reporter.status(f"  Preprocess    : {cfg.ocr.preprocess_method}")
        reporter.status(f"  Input         : {cfg.input_dir.resolve()}")
        reporter.status(f"  Preview dir   : {tmp_dir.resolve()}")
        reporter.status(_LINE)

        builder = StageBuilder(cfg, self._component_factory, self._reporter)
        preprocess_service = builder.build_preprocessor_service(tmp_dir)
        files = collect_files(cfg)
        return cfg, preprocess_service, files, tmp_dir

    def _run_preview_batch(
        self,
        preprocess_service: PreprocessService,
        files: list[Path],
    ) -> tuple[list[str], list[str]]:
        reporter = self._reporter
        total = len(files)
        reporter.status(f"Found {total} file(s) to preprocess.\n")

        succeeded: list[str] = []
        failed: list[str] = []
        for idx, file in enumerate(files, start=1):
            if self._stop_event and self._stop_event.is_set():
                reporter.status("\nStopped by user.")
                break
            reporter.status(f"[{idx}/{total}] Processing: {file.name}")
            try:
                ok = self._preview_file(file, preprocess_service)
                if ok:
                    succeeded.append(file.name)
                else:
                    failed.append(file.name)
            except Exception as exc:  # noqa: BLE001  # preprocessing errors (OpenCV, PIL) skip the file; the pipeline must continue
                reporter.warn(f"       → ERROR: {exc}")
                failed.append(file.name)
        return succeeded, failed

    @staticmethod
    def _cleanup_preview_intermediates(tmp_dir: Path) -> None:
        for pattern in ("*_page*.png", "*_step*.jpg"):
            for path in tmp_dir.glob(pattern):
                try:
                    path.unlink()
                except OSError as exc:
                    _logger.warning("Could not remove %s: %s", path.name, exc)

    # ── private ───────────────────────────────────────────────────────────

    def _run_files(
        self,
        files: list[Path],
        file_processor: FileProcessor,
        tmp_dir: Path,
        artifact_store: PreviewImageStore,
    ) -> tuple[list[str], list[str]]:
        return _run_files_coordinator(
            files,
            process_file=file_processor.process,
            cleanup_tmp=lambda: self._cleanup_tmp(tmp_dir, artifact_store),
            stop_event=self._stop_event,
            on_file_started=self._on_file_started,
            on_file_done=self._on_file_done,
            reporter=self._reporter,
        )

    def _preview_file(self, file: Path, preprocess_service: PreprocessService) -> bool:
        preprocessed, steps, _, raw_text_hint = preprocess_service.preprocess_input(file)
        if raw_text_hint is not None:
            self._reporter.status("       → skipped preprocessing (text input)")
        else:
            for out_path in preprocessed:
                self._reporter.status(f"       → {out_path.name}  ({', '.join(steps)})")
        return True

    def _print_banner(self) -> None:
        cfg = self._config
        reporter = self._reporter
        reporter.status(_LINE)
        reporter.status("Teacher's Teammate")
        reporter.status(_LINE)
        if self._config_file is not None:
            reporter.status(f"  Config file   : {self._config_file}")
        reporter.status(f"  OCR engine    : {cfg.ocr.engine}")
        if cfg.ocr.engine == "ollama":
            reporter.status(f"  OCR model     : {cfg.ocr.model}")
        elif cfg.ocr.engine == "langchain":
            reporter.status(f"  OCR provider  : {cfg.ocr.provider}")
            reporter.status(f"  OCR model     : {cfg.ocr.effective_model}")
        reporter.status(f"  Preprocess    : {cfg.ocr.preprocess_method}")
        reporter.status(f"  Language      : {cfg.language}")
        if (
            cfg.ocr.engine in ("ollama",)
            or (cfg.ocr.engine == "langchain" and cfg.ocr.provider == "ollama")
            or (cfg.correction_enabled and cfg.correction_provider == "ollama")
            or (cfg.evaluation_enabled and cfg.evaluate_provider == "ollama")
        ):
            reporter.status(f"  Ollama URL    : {cfg.ollama_url}")
        reporter.status(f"  Input         : {cfg.input_dir.resolve()}")
        reporter.status(f"  Output        : {cfg.output_dir.resolve()}")
        reporter.status(f"  Recursive     : {cfg.recursive}")
        reporter.status(f"  Debug         : {cfg.debug}")
        if cfg.ocr.engine == "tesseract" and cfg.ocr.preprocess_method == "adaptive_threshold":
            reporter.status(
                "  HINT: Tesseract works best with CLAHE preprocessing. "
                "Consider --preprocess-method clahe."
            )
        elif cfg.ocr.engine == "paddleocr" and cfg.ocr.preprocess_method in (
            "adaptive_threshold",
            "clahe",
        ):
            reporter.status(
                "  HINT: PaddleOCR works on full-colour images; binarisation degrades accuracy. "
                "Consider --preprocess-method none or grayscale."
            )
        if not cfg.correction_enabled:
            reporter.status("  Correction    : disabled")
        else:
            reporter.status(
                f"  Correction    : {cfg.correction_provider} / {cfg.effective_correction_model}"
            )
            if cfg.anonymization_enabled:
                reporter.status("  Anonymization : enabled (PII removed before correction)")
        if not cfg.evaluation_enabled:
            reporter.status("  Evaluation    : disabled")
        else:
            reporter.status(
                f"  Evaluation    : {cfg.evaluate_provider} / {cfg.effective_evaluate_model}"
            )
        if not cfg.docx_enabled:
            reporter.status("  DOCX output   : disabled")
        else:
            reporter.status(f"  DOCX format   : {cfg.docx_format}")
        reporter.status(_LINE)

    def _setup_services(
        self,
        tmp_dir: Path,
        artifact_store: PreviewImageStore,
        state_repo: StateRepository,
    ) -> FileProcessor:
        """Build all pipeline services and return a ready-to-use FileProcessor."""
        cfg = self._config
        reporter = self._reporter
        builder = StageBuilder(cfg, self._component_factory, self._reporter)

        # Build one shared OllamaClient for all Ollama-backed stages so URL
        # normalisation happens once and injection is explicit.
        ollama_client = (
            OllamaClient(cfg.ollama_url)
            if cfg.ocr.engine == "ollama"
            or (cfg.correction_enabled and cfg.correction_provider == "ollama")
            or (cfg.evaluation_enabled and cfg.evaluate_provider == "ollama")
            else None
        )

        ocr = builder.build_ocr(ollama_client)
        if cfg.ocr.engine == "langchain":
            reporter.status("Building OCR LLM... OK")

        correction, anonymizer = builder.build_correction_and_anonymizer(ollama_client)
        if cfg.correction_enabled:
            reporter.status("Building correction LLM... OK")

        evaluation = builder.build_evaluation(correction, ollama_client)
        if evaluation is not None:
            reporter.status("Building evaluation LLM... OK")

        doc_creator = builder.build_doc_creator()

        cache_service = self._cache_service or CacheReconciliationService(
            state_repo=state_repo,
            artifact_store=artifact_store,
            config=cfg,
        )
        preprocess_service = self._preprocess_service or builder.build_preprocessor_service(tmp_dir)
        ocr_stage_service = self._ocr_stage_service or OCRStageService(
            processor=ocr,
            stop_event=self._stop_event,
        )
        return FileProcessor(
            FileProcessorConfig(
                output_dir=cfg.output_dir,
                language=cfg.language,
                cache_service=cache_service,
                preprocess_service=preprocess_service,
                ocr_stage_service=ocr_stage_service,
                correction=correction,
                evaluation=evaluation,
                doc_creator=doc_creator,
                anonymizer=anonymizer,
                stop_event=self._stop_event,
                on_ocr_done=self._on_ocr_done,
                reporter=self._reporter,
            )
        )

    def _cleanup_tmp(self, tmp_dir: Path, artifact_store: PreviewImageStore) -> None:
        if not tmp_dir.exists():
            return
        if self._config.debug:
            self._reporter.status(f"       → DEBUG: preprocessing files kept in {tmp_dir}")
            return
        try:
            artifact_store.cleanup_intermediate_images(keep_debug=self._config.debug)
        except OSError as exc:
            _logger.warning("Could not remove temporary images: %s", exc)
