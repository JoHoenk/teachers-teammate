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

:func:`preprocess_preview`
    Preprocess a single source file (image or PDF) and return the paths to
    the original image and the preprocessed result.  Used by the GUI preview
    dialog without spawning a full pipeline run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
import threading

from ..config import Config, OcrConfig
from ..domain.model_catalog import HardwareDetection
from ..domain.output_files import output_folder_refusal
from ..domain.run_summary import RunSummary
from ..exceptions import OllamaConnectionError, ProviderNotAvailableError
from ..interfaces import ImagePreprocessor
from . import run_report
from .input_provider_factory import InputOptions, get_input_provider, supported_suffixes
from .ollama_utils import OllamaClient, WaitDetail
from .onnx.store import ModelStore
from .preview_image_store import PreviewImageStore
from .reporting import KeepAwakeState, ProgressCallbacks, Reporter, StdoutReporter
from .run_stats import RunStatsCollector
from .speed_store import SampleStore
from .stage_builder import PipelineComponentFactory as PipelineComponentFactory  # noqa: PLC0414
from .stage_builder import StageBuilder, default_build_preprocessor
from .state_repository import StateRepository
from .storage_root import resolve_artifact_dir, resolve_storage_root
from .teacher_work_store import TeacherWorkStore
from .workflow.cache_service import CacheReconciliationService
from .workflow.coordinator import run_files as _run_files_coordinator
from .workflow.file_processor import FileProcessor, FileProcessorConfig
from .workflow.ocr_service import OCRStageService
from .workflow.preprocess_service import PreprocessService

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
    # Hardware of the computer that runs Ollama; sizes the context window of the local models.
    hardware: HardwareDetection | None = None
    # Whether the computer is kept awake for this run (the application layer holds it); the
    # banner says so, and warns when the run is on battery power.
    keep_awake: KeepAwakeState | None = None
    # Where the built-in reader's model files live; the application layer passes the store its
    # readiness check looks at, so the run reads the folder that was checked.
    model_store: ModelStore | None = None
    # Where the teacher's work is.  The application layer passes the store the window writes
    # to, so a run reads the corrections the teacher made; ``None`` builds the default one.
    work_store: TeacherWorkStore | None = None


# ── Public helpers ─────────────────────────────────────────────────────────


def preprocess_preview(
    source_path: Path,
    ocr: OcrConfig,
    tmp_dir: Path,
    build_preprocessor: Callable[
        [Path, bool, OcrConfig, ModelStore | None], ImagePreprocessor
    ] = default_build_preprocessor,
    model_store: ModelStore | None = None,
) -> tuple[Path, Path, list[str]]:
    """Preprocess *source_path* and return paths for both the original and processed image.

    Does not run OCR, correction, or DOCX generation.

    For PDF inputs the first page is rendered to ``tmp_dir`` (at ``ocr.pdf_render_dpi``)
    and used as the original image.  For image inputs the file is used directly.

    Builds the preprocessor via *build_preprocessor*, which defaults to the same
    constructor a real run uses (:func:`stage_builder.default_build_preprocessor`). Pass
    a :class:`PipelineComponentFactory`'s ``build_preprocessor`` (e.g.
    ``factory.build_preprocessor``) when the caller was configured with a non-default
    factory, so the preview matches what that run would actually apply.

    Args:
        source_path: Path to a supported input file (PDF or image).
        ocr:         The OCR config slice (method, PDF DPI) to preview.
        tmp_dir:     Temporary directory for any intermediate and output files.
        build_preprocessor: Preprocessor constructor; defaults to
            :func:`stage_builder.default_build_preprocessor`.
        model_store: Where the page models of the automatic preparation are; ``None`` = the
            default folder.

    Returns:
        A tuple of ``(original_image_path, preprocessed_image_path, steps_applied)``.
    """
    # A preview shows the preprocessed page image, so the PDF is always rendered.
    options = InputOptions(pdf_dpi=ocr.pdf_render_dpi, pdf_text_layer="never")
    provider = get_input_provider(source_path.suffix, tmp_dir=tmp_dir, options=options)
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

    preprocessor = build_preprocessor(tmp_dir, False, ocr, model_store)
    out_path, steps = preprocessor.preprocess(image_path)
    return image_path, out_path, steps


# ── Pipeline ───────────────────────────────────────────────────────────────


class OCRPipeline:
    """Orchestrates the OCR → correction → DOCX pipeline.

    Parameters
    ----------
    config:
        Full runtime configuration.
    files:
        The files this run processes, in the order the adapter listed them.  The run does not
        look for files itself: the queue the teacher sees and the files that are read are the
        same list.
    config_file:
        Path to the loaded config file, shown in the startup banner.
    stop_event:
        Optional threading event ("Stop now"); when set the pipeline cancels the Ollama request
        being made, stops the file being processed before its next step and starts no further
        file.  What was already done stays stored.
    stop_after_file_event:
        Optional threading event ("Stop after this file"); when set the file being processed
        is finished completely and no further file is started.
    dependencies:
        Optional injection seam (tests/adapters).  Notably carries ``reporter``:
        the sink for the run narrative and advisory warnings, defaulting to
        :class:`~teachers_teammate.infrastructure.reporting.StdoutReporter`
        (CLI behaviour).  The GUI injects a reporter that feeds its log pane.
    progress:
        Optional per-file progress hooks; see
        :class:`~teachers_teammate.infrastructure.reporting.ProgressCallbacks`.
    """

    def __init__(
        self,
        config: Config,
        files: Sequence[Path],
        stop_event: threading.Event | None = None,
        *,
        stop_after_file_event: threading.Event | None = None,
        config_file: Path | None = None,
        dependencies: PipelineDependencies | None = None,
        progress: ProgressCallbacks | None = None,
    ) -> None:
        self._config = config
        self._files = list(files)
        self._config_file = config_file
        self._stop_event = stop_event
        self._stop_after_file_event = stop_after_file_event
        self._progress = progress or ProgressCallbacks()
        #: The file the run is on, so a note from the Ollama client can name it.
        self._current_source_id = ""
        deps = dependencies or PipelineDependencies()
        self._keep_awake: KeepAwakeState | None = deps.keep_awake
        self._reporter: Reporter = deps.reporter or StdoutReporter()
        self._component_factory = deps.component_factory or PipelineComponentFactory()
        self._hardware: HardwareDetection | None = deps.hardware
        self._model_store: ModelStore | None = deps.model_store
        self._work_store: TeacherWorkStore | None = deps.work_store
        self._artifact_store: PreviewImageStore | None = deps.artifact_store
        self._state_repo: StateRepository | None = deps.state_repo
        self._cache_service: CacheReconciliationService | None = deps.cache_service
        self._preprocess_service: PreprocessService | None = deps.preprocess_service
        self._ocr_stage_service: OCRStageService | None = deps.ocr_stage_service

    # ── public ────────────────────────────────────────────────────────────

    def run(self) -> RunSummary:
        """Execute the full pipeline and return what happened to each file.

        A run that was stopped is not a failure: ``summary.exit_code`` is ``1`` only when a
        file failed or the run could not start.
        """
        cfg = self._config
        reporter = self._reporter
        refusal = output_folder_refusal(
            writes_text_files=cfg.writes_text_files,
            output_dir=cfg.output_dir.resolve(),
            input_dir=cfg.input_dir.resolve(),
            recursive=cfg.recursive,
        )
        if refusal:
            reporter.error(refusal)
            return RunSummary(error=refusal)
        tmp_dir = resolve_artifact_dir(cfg.output_dir)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        artifact_store = self._artifact_store or PreviewImageStore(tmp_dir)
        state_repo = self._state_repo or StateRepository(tmp_dir / "state")

        storage = resolve_storage_root()
        reporter.status(f"Storage root: {storage.path.resolve()} ({storage.source})")

        run_report.banner(cfg, reporter, config_file=self._config_file, keep_awake=self._keep_awake)
        if cfg.recognition_enabled and cfg.ocr.engine == "ollama":
            try:
                self._component_factory.check_ocr_model(cfg.ollama_url, cfg.ocr.model, reporter)
                reporter.status("Checking Ollama connection... OK")
            except (RuntimeError, OllamaConnectionError) as exc:
                reporter.error(str(exc))
                return RunSummary(error=str(exc))
        try:
            file_processor = self._setup_services(tmp_dir, artifact_store, state_repo)
        except (ValueError, RuntimeError, ProviderNotAvailableError) as exc:
            reporter.error(str(exc))
            return RunSummary(error=str(exc))

        files = self._files
        if not files:
            supported = ", ".join(s.lstrip(".").upper() for s in sorted(supported_suffixes()))
            reporter.status(f"No supported files found ({supported}). Nothing to do.")
            return RunSummary()

        reporter.status(f"Found {len(files)} file(s) to process.\n")
        summary = self._run_files(files, file_processor, tmp_dir, artifact_store)
        run_report.summary(summary, reporter)
        return summary

    def run_preview_only(self) -> RunSummary:
        """Preprocess all input files and save images to the temp cache directory.

        No OCR, correction, or DOCX generation is performed.  Use this to
        inspect exactly what images the preprocessor would send to the OCR model.
        """
        reporter = self._reporter
        _cfg, preprocess_service, files, tmp_dir = self._prepare_preview_run()
        if not files:
            supported = ", ".join(s.lstrip(".").upper() for s in sorted(supported_suffixes()))
            reporter.status(f"No supported files found ({supported}). Nothing to do.")
            return RunSummary()

        try:
            succeeded, failed = self._run_preview_batch(preprocess_service, files)
        finally:
            self._cleanup_preview_intermediates(tmp_dir)

        summary = RunSummary(succeeded=tuple(succeeded), failed=tuple(failed))
        run_report.preview_summary(summary, reporter, tmp_dir)
        return summary

    def _prepare_preview_run(self) -> tuple[Config, PreprocessService, list[Path], Path]:
        cfg = self._config
        reporter = self._reporter
        tmp_dir = resolve_artifact_dir(cfg.output_dir)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        reporter.status(run_report.LINE)
        reporter.status("Teacher's Teammate — Preview (preprocess only)")
        reporter.status(run_report.LINE)
        reporter.status(f"  Preprocess    : {cfg.ocr.preprocess_method}")
        reporter.status(f"  Input         : {cfg.input_dir.resolve()}")
        reporter.status(f"  Preview dir   : {tmp_dir.resolve()}")
        reporter.status(run_report.LINE)

        builder = StageBuilder(
            cfg, self._component_factory, self._reporter, self._hardware, self._model_store
        )
        preprocess_service = builder.build_preprocessor_service(tmp_dir)
        return cfg, preprocess_service, self._files, tmp_dir

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
                reporter.error(f"{file.name}: {exc}")
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
    ) -> RunSummary:
        return _run_files_coordinator(
            files,
            process_file=file_processor.process,
            cleanup_tmp=lambda: self._cleanup_tmp(tmp_dir, artifact_store),
            stop_event=self._stop_event,
            stop_after_file_event=self._stop_after_file_event,
            on_file_started=self._on_file_started,
            on_file_done=self._progress.on_file_done,
            reporter=self._reporter,
        )

    def _on_file_started(self, source_id: str, name: str, idx: int, total: int) -> None:
        """Remember which file the run is on, then pass the event on."""
        self._current_source_id = source_id
        if self._progress.on_file_started is not None:
            self._progress.on_file_started(source_id, name, idx, total)

    def _on_ollama_detail(self, detail: WaitDetail) -> None:
        """Pass what the server is doing with the request to the run's progress hooks."""
        if self._progress.on_stage_detail is not None:
            self._progress.on_stage_detail(self._current_source_id, detail)

    def _on_ollama_first_token(self) -> None:
        """Say that the model began answering: the run's clock starts here, not at the request."""
        if self._progress.on_first_token is not None:
            self._progress.on_first_token(self._current_source_id)

    def _preview_file(self, file: Path, preprocess_service: PreprocessService) -> bool:
        prepared = preprocess_service.preprocess_input(file)
        if prepared.raw_text_hint is not None:
            self._reporter.status("       → skipped preprocessing (text input)")
        else:
            for out_path in prepared.ocr_inputs:
                self._reporter.status(f"       → {out_path.name}  ({', '.join(prepared.steps)})")
        return True

    def _setup_services(
        self,
        tmp_dir: Path,
        artifact_store: PreviewImageStore,
        state_repo: StateRepository,
    ) -> FileProcessor:
        """Build all pipeline services and return a ready-to-use FileProcessor."""
        cfg = self._config
        reporter = self._reporter
        builder = StageBuilder(
            cfg, self._component_factory, self._reporter, self._hardware, self._model_store
        )

        # Build one shared OllamaClient for all Ollama-backed stages so URL
        # normalisation happens once and injection is explicit.
        # The same client reports every request's statistics to the collector, so one sink sees
        # the whole run and can turn each step into a sample of how fast this machine is.
        run_stats = RunStatsCollector(cfg.ollama_url, SampleStore().add)
        ollama_client = (
            OllamaClient(cfg.ollama_url, cancel_event=self._stop_event, stats_sink=run_stats.record)
            if (cfg.recognition_enabled and cfg.ocr.engine == "ollama")
            or (cfg.correction_enabled and cfg.correction_provider == "ollama")
            or (cfg.evaluation_enabled and cfg.evaluate_provider == "ollama")
            else None
        )
        if ollama_client is not None:
            # The client is the only place that sees a request waiting for the server, so it is
            # where the run learns what the server is doing and when the answer really began.
            ollama_client.detail_sink = self._on_ollama_detail
            ollama_client.first_token_sink = self._on_ollama_first_token

        ocr = builder.build_ocr(ollama_client, self._stop_event)

        correction, anonymizer = builder.build_correction_and_anonymizer(ollama_client)
        if cfg.correction_enabled:
            reporter.status("Building correction LLM... OK")

        evaluation = builder.build_evaluation(correction, ollama_client)
        if evaluation is not None:
            reporter.status("Building evaluation LLM... OK")

        doc_creator = builder.build_doc_creator()
        text_anonymizer = builder.build_text_anonymizer(anonymizer)

        cache_service = self._cache_service or CacheReconciliationService(
            state_repo=state_repo,
            artifact_store=artifact_store,
            config=cfg,
            windows=builder.resolved_windows(),
            model_store=self._model_store,
            # A run always reads the teacher's work: the window's writes and the run's reads
            # must see the same folder, so the default store is the one the window uses.
            work_store=self._work_store or TeacherWorkStore(),
        )
        preprocess_service = self._preprocess_service or builder.build_preprocessor_service(tmp_dir)
        ocr_stage_service = self._ocr_stage_service or OCRStageService(
            processor=ocr,
            stop_event=self._stop_event,
        )
        return FileProcessor(
            FileProcessorConfig(
                output_dir=cfg.output_dir,
                input_dir=cfg.input_dir,
                recursive=cfg.recursive,
                write_text=cfg.text_enabled,
                text_anonymizer=text_anonymizer,
                language=cfg.language,
                cache_service=cache_service,
                preprocess_service=preprocess_service,
                ocr_stage_service=ocr_stage_service,
                correction=correction,
                evaluation=evaluation,
                doc_creator=doc_creator,
                anonymizer=anonymizer,
                postprocessor=builder.build_ocr_postprocessor(),
                recognize=cfg.recognition_enabled,
                stop_event=self._stop_event,
                on_ocr_done=self._progress.on_ocr_done,
                on_stage_started=self._progress.on_stage_started,
                reporter=self._reporter,
                stats=run_stats,
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
