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

from collections.abc import Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
import threading
from typing import NamedTuple

from ..config import Config, OcrConfig
from ..domain.data_location import ENGINE_OLLAMA
from ..domain.key_facts import KeyFacts
from ..domain.model_catalog import HardwareDetection
from ..domain.ollama_discovery import WaitDetail
from ..domain.output_files import output_folder_problem
from ..domain.run_summary import KeepAwakeState, RunSummary
from ..exceptions import OllamaUnreachableError, ProviderNotAvailableError
from ..interfaces import ImageUnit, PreparedPreview, ProgressCallbacks, Reporter
from ..messages import preview_needs_images
from ..run_rules import uses_ollama
from ..texts import report as report_text
from ..texts.dialogs import folder_problem_text
from . import run_report
from .input_provider_factory import InputOptions, get_input_provider, supported_suffixes
from .key_facts import MachineKeyFacts
from .ollama_utils import OllamaClient
from .onnx.store import ModelStore
from .preview_image_store import PreviewImageStore
from .reporting import StdoutReporter
from .run_stats import RunStatsCollector
from .speed_store import SampleStore
from .stage_builder import (
    PipelineComponentFactory,
    PreprocessorBuilder,
    StageBuilder,
    default_build_preprocessor,
)
from .state_repository import StateRepository
from .storage_root import resolve_artifact_dir, resolve_storage_root
from .teacher_work_store import TeacherWorkStore
from .workflow.cache_service import CacheReconciliationService
from .workflow.coordinator import run_files as _run_files_coordinator
from .workflow.file_processor import FileProcessor, FileProcessorDeps
from .workflow.ocr_service import OCRStageService
from .workflow.preprocess_service import PreprocessService

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineDependencies:
    """What a run is given from outside; a run builds none of these itself."""

    # Where the speed samples of this run go.  One store per process writes the file, so the
    # application layer passes the store its speed estimates also write to; a second store would
    # be a second lock on one file.
    samples: SampleStore
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
    # The sentence the banner and the window warn with on battery power; the adapter passes it,
    # so the run's warning and the window say the same line.
    battery_warning: str = ""
    # Where the built-in reader's model files live; the application layer passes the store its
    # readiness check looks at, so the run reads the folder that was checked.
    model_store: ModelStore | None = None
    # What this computer adds to a cache key.  The application layer passes the one its queue and
    # commands compute keys with, so a run and the queue cannot key differently; ``None`` asks
    # the computer the run is on, with ``model_store``.
    key_facts: KeyFacts | None = None
    # Where the teacher's work is.  The application layer passes the store the window writes
    # to, so a run reads the corrections the teacher made; ``None`` builds the default one.
    work_store: TeacherWorkStore | None = None


# ── Public helpers ─────────────────────────────────────────────────────────


def preprocess_preview(
    source_path: Path,
    ocr: OcrConfig,
    tmp_dir: Path,
    build_preprocessor: PreprocessorBuilder = default_build_preprocessor,
    model_store: ModelStore | None = None,
) -> PreparedPreview:
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
        The original page, the prepared page, and the steps that were applied.
    """
    # A preview shows the preprocessed page image, so the PDF is always rendered.
    options = InputOptions(pdf_dpi=ocr.pdf_render_dpi, pdf_text_layer="never")
    provider = get_input_provider(source_path.suffix, tmp_dir=tmp_dir, options=options)
    payload = provider.load(source_path)
    image_path = next(
        (unit.path for unit in payload.units if isinstance(unit, ImageUnit)),
        None,
    )
    if image_path is None:
        raise ValueError(preview_needs_images(source_path.name))

    preprocessor = build_preprocessor(tmp_dir=tmp_dir, save_steps=False, ocr=ocr, store=model_store)
    out_path, steps = preprocessor.preprocess(image_path)
    return PreparedPreview(image_path, out_path, steps)


# ── Pipeline ───────────────────────────────────────────────────────────────


class _RunStart(NamedTuple):
    """What a run needs set up before it reads the first file."""

    tmp_dir: Path
    """Where the intermediate images, the previews and the state of the run go."""
    artifact_store: PreviewImageStore
    """The store the prepared pages and the step snapshots are written to."""
    state_repo: StateRepository
    """What is already done for each file, so a run continues where the last one stopped."""


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
        What the run is given from outside: ``samples`` (where the speed samples go, one store
        per process) is required; the rest are the injection seam for tests and adapters.
        Notably carries ``reporter``: the sink for the run narrative and advisory warnings,
        defaulting to :class:`~teachers_teammate.infrastructure.reporting.StdoutReporter`
        (CLI behaviour).  The GUI injects a reporter that feeds its log pane.
    progress:
        Optional per-file progress hooks; see
        :class:`~teachers_teammate.interfaces.ProgressCallbacks`.
    """

    def __init__(
        self,
        config: Config,
        files: Sequence[Path],
        stop_event: threading.Event | None = None,
        *,
        stop_after_file_event: threading.Event | None = None,
        config_file: Path | None = None,
        dependencies: PipelineDependencies,
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
        deps = dependencies
        self._keep_awake: KeepAwakeState | None = deps.keep_awake
        self._battery_warning = deps.battery_warning
        self._reporter: Reporter = deps.reporter or StdoutReporter()
        self._component_factory = deps.component_factory or PipelineComponentFactory()
        self._hardware: HardwareDetection | None = deps.hardware
        self._model_store: ModelStore | None = deps.model_store
        self._key_facts: KeyFacts | None = deps.key_facts
        self._work_store: TeacherWorkStore | None = deps.work_store
        self._samples: SampleStore = deps.samples
        self._artifact_store: PreviewImageStore | None = deps.artifact_store
        self._state_repo: StateRepository | None = deps.state_repo
        self._cache_service: CacheReconciliationService | None = deps.cache_service
        self._preprocess_service: PreprocessService | None = deps.preprocess_service
        self._ocr_stage_service: OCRStageService | None = deps.ocr_stage_service

    # ── public ────────────────────────────────────────────────────────────

    def run(self) -> RunSummary:
        """Execute the full pipeline and return what happened to each file.

        A stopped run is no failure: ``exit_code`` is ``1`` only when a file failed or the run
        could not start.
        """
        refusal = self._refuse_folders()
        if refusal:
            self._reporter.error(refusal)
            return RunSummary(error=refusal)
        start = self._prepare_run()
        unreadable = self._check_ocr_model()
        if unreadable:
            return RunSummary(error=unreadable)
        try:
            file_processor = self._setup_services(
                start.tmp_dir, start.artifact_store, start.state_repo
            )
        except (ValueError, RuntimeError, ProviderNotAvailableError) as exc:
            self._reporter.error(str(exc))
            return RunSummary(error=str(exc))
        files = self._files
        if not files:
            return self._report_no_files()
        self._reporter.status(report_text.files_to_process(len(files)))
        summary = self._run_files(files, file_processor, start.tmp_dir, start.artifact_store)
        run_report.summary(summary, self._reporter)
        return summary

    def _refuse_folders(self) -> str:
        """Return why a run may not write there, or ``""`` when both folders are fine."""
        cfg = self._config
        problem = output_folder_problem(
            writes_text_files=cfg.writes_text_files,
            output_dir=cfg.output_dir.resolve(),
            input_dir=cfg.input_dir.resolve(),
            recursive=cfg.recursive,
        )
        return folder_problem_text(problem) if problem is not None else ""

    def _prepare_run(self) -> _RunStart:
        """Create the run's folders and stores, and say where the teacher's work is kept."""
        cfg = self._config
        reporter = self._reporter
        tmp_dir = resolve_artifact_dir(cfg.output_dir)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        artifact_store = self._artifact_store or PreviewImageStore(tmp_dir, keep_debug=cfg.debug)
        state_repo = self._state_repo or StateRepository(tmp_dir / "state")
        storage = resolve_storage_root()
        reporter.status(report_text.storage_root(storage.path.resolve(), storage.source))
        run_report.banner(
            cfg,
            reporter,
            config_file=self._config_file,
            keep_awake=self._keep_awake,
            battery_warning=self._battery_warning,
        )
        return _RunStart(tmp_dir, artifact_store, state_repo)

    def _check_ocr_model(self) -> str:
        """Return why the model that reads the pages cannot be used, or ``""``."""
        cfg = self._config
        if not (cfg.recognition_enabled and cfg.ocr.engine == ENGINE_OLLAMA):
            return ""
        try:
            self._component_factory.check_ocr_model(
                url=cfg.ollama_url, model=cfg.ocr.model, reporter=self._reporter
            )
        except (RuntimeError, OllamaUnreachableError) as exc:
            self._reporter.error(str(exc))
            return str(exc)
        self._reporter.status(report_text.OLLAMA_CHECKED)
        return ""

    def _report_no_files(self) -> RunSummary:
        """Say which files a run reads; the queue holds none of them."""
        supported = ", ".join(s.lstrip(".").upper() for s in sorted(supported_suffixes()))
        self._reporter.status(report_text.no_supported_files(supported))
        return RunSummary()

    def run_preview_only(self) -> RunSummary:
        """Preprocess all input files and save images to the temp cache directory.

        No OCR, correction, or DOCX generation is performed.  Use this to
        inspect exactly what images the preprocessor would send to the OCR model.
        """
        reporter = self._reporter
        preprocess_service, files, tmp_dir = self._prepare_preview_run()
        if not files:
            return self._report_no_files()

        try:
            summary = self._run_preview_batch(preprocess_service, files)
        finally:
            self._cleanup_preview_intermediates(tmp_dir)

        run_report.preview_summary(summary, reporter, tmp_dir)
        return summary

    def _prepare_preview_run(self) -> tuple[PreprocessService, list[Path], Path]:
        cfg = self._config
        reporter = self._reporter
        tmp_dir = resolve_artifact_dir(cfg.output_dir)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        run_report.preview_banner(cfg, reporter, tmp_dir)

        builder = StageBuilder(
            cfg, self._component_factory, self._reporter, self._hardware, self._model_store
        )
        preprocess_service = builder.build_preprocessor_service(tmp_dir)
        return preprocess_service, self._files, tmp_dir

    def _run_preview_batch(
        self,
        preprocess_service: PreprocessService,
        files: list[Path],
    ) -> RunSummary:
        """Preprocess every file, and return the names sorted by what happened to them."""
        reporter = self._reporter
        total = len(files)
        reporter.status(report_text.files_to_preprocess(total))

        succeeded: list[str] = []
        failed: list[str] = []
        for idx, file in enumerate(files, start=1):
            if self._stop_event and self._stop_event.is_set():
                reporter.status(report_text.STOPPED_BY_USER)
                break
            reporter.status(report_text.processing(idx, total, file.name))
            try:
                self._preview_file(file, preprocess_service)
            except Exception as exc:  # noqa: BLE001  # preprocessing errors (OpenCV, PIL) skip the file; the pipeline must continue
                reporter.error(f"{file.name}: {exc}")
                failed.append(file.name)
            else:
                succeeded.append(file.name)
        return RunSummary(succeeded=tuple(succeeded), failed=tuple(failed))

    def _cleanup_preview_intermediates(self, tmp_dir: Path) -> None:
        """Delete what the preview run wrote besides its result, in the folder it wrote to.

        The stored previews of a file live in the same folder and are named after it, and the
        prepared pages are what the run was for, so only the step snapshots and the pages as
        they were rendered go.
        """
        store = PreviewImageStore(tmp_dir, keep_debug=self._config.debug)
        try:
            store.cleanup_intermediate_images(keep_prepared_pages=True)
        except OSError as exc:
            _logger.warning("Could not remove preview images: %s", exc)

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

    def _preview_file(self, file: Path, preprocess_service: PreprocessService) -> None:
        prepared = preprocess_service.preprocess_input(file)
        if prepared.raw_text_hint is not None:
            self._reporter.status(report_text.PREPROCESSING_SKIPPED_TEXT)
        else:
            for out_path, page_steps in zip(
                prepared.ocr_inputs, prepared.steps_per_page, strict=False
            ):
                self._reporter.status(
                    report_text.prepared_page(out_path.name, ", ".join(page_steps))
                )

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
        run_stats = RunStatsCollector(cfg.ollama_url, self._samples.add)
        ollama_client = (
            OllamaClient(cfg.ollama_url, cancel_event=self._stop_event, stats_sink=run_stats.record)
            if uses_ollama(cfg)
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
            reporter.status(report_text.CORRECTION_LLM_BUILT)

        evaluation = builder.build_evaluation(correction, ollama_client)
        if evaluation is not None:
            reporter.status(report_text.EVALUATION_LLM_BUILT)

        doc_creator = builder.build_doc_creator()
        text_anonymizer = builder.build_text_anonymizer(anonymizer)

        cache_service = self._cache_service or CacheReconciliationService(
            state_repo=state_repo,
            artifact_store=artifact_store,
            config=cfg,
            windows=builder.resolved_windows(),
            facts=self._key_facts or MachineKeyFacts(self._model_store),
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
            FileProcessorDeps(
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
            self._reporter.status(report_text.debug_files_kept(tmp_dir))
            return
        try:
            artifact_store.cleanup_intermediate_images()
        except OSError as exc:
            _logger.warning("Could not remove temporary images: %s", exc)
