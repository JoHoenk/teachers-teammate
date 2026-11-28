"""One file through all pipeline stages: the stages in order, and the one way to report it.

The work of a stage is in its own module — :mod:`reading_stage`, :mod:`correction_stage`,
:mod:`review_stage` — and what they write is :mod:`output_writer`.  This module builds them from
:class:`FileProcessorDeps` and turns every way out into a
:class:`~teachers_teammate.infrastructure.workflow.processing_result.ProcessingResult`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
import logging
from pathlib import Path
import threading

from ...domain.document_state import machine_texts
from ...domain.formatted_text import TextFormat
from ...domain.task_text import Segment, locate
from ...domain.teacher_work import CurrentTexts, current_texts
from ...exceptions import (
    FILE_GONE_MESSAGE,
    FileSkipped,
    RunStoppedError,
    StageError,
    StageErrorKind,
)
from ...interfaces import (
    Anonymizer,
    Corrector,
    DocumentCreator,
    Evaluator,
    OnOcrDoneFn,
    OnStageStartedFn,
    Reporter,
)
from ...messages import input_read_failed
from ...texts import report as report_text
from ..ocr_formats import OcrOutputPostprocessor, plain_text_of
from ..reporting import StdoutReporter
from ..run_stats import RunStatsCollector, StepSampler
from .cache_service import CacheContext, CacheReconciliationService
from .correction_stage import CorrectionStage
from .hidden_names import NameHiding
from .ocr_service import OCRStageService
from .output_writer import OutputTexts, OutputWriter
from .preprocess_service import PreprocessService
from .processing_result import ProcessingResult, Stopped
from .reading_stage import ReadingStage
from .review_stage import ReviewStage
from .scan_places import ScanPlaces
from .stage_shared import StageShared, stage_error

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileProcessorDeps:
    """Construction-time dependencies for :class:`FileProcessor`."""

    output_dir: Path
    language: str
    cache_service: CacheReconciliationService
    preprocess_service: PreprocessService
    ocr_stage_service: OCRStageService
    correction: Corrector | None
    evaluation: Evaluator | None
    doc_creator: DocumentCreator | None
    anonymizer: Anonymizer | None = None
    scan_places: ScanPlaces | None = None
    """Makes where the lines of a scan stand in the text read from it; ``None`` when the run
    keeps no places (switched off, or the built-in reader's models are not there)."""
    input_dir: Path | None = None
    """Root of the inputs; outputs keep the subfolders below it (``None`` = flat output)."""
    recursive: bool = True
    """Whether the run reads subfolders of *input_dir* (text files must not land in them)."""
    write_text: bool = False
    """Save the extracted text as ``<name>.txt``."""
    text_anonymizer: Anonymizer | None = None
    """Set to save the extracted text with personal data replaced as ``<name>_anonymized.txt``."""
    postprocessor: OcrOutputPostprocessor | None = None
    recognize: bool = True
    """Whether scans are read.  Off, a file that needs text recognition is skipped; text files,
    PDFs with a text layer and files whose recognised text is already stored are not."""
    stop_event: threading.Event | None = None
    on_ocr_done: OnOcrDoneFn | None = None
    on_stage_started: OnStageStartedFn | None = None
    reporter: Reporter = field(default_factory=StdoutReporter)
    stats: RunStatsCollector | None = None
    """Turns the requests of each step into a sample of how fast this machine is; ``None``
    when nothing measures the run (a test, a tool)."""


class FileProcessor:
    """Executes all pipeline stages for a single input file.

    Receives pre-built services and optional components; returns a
    :class:`ProcessingResult` to the coordinator for dispatch.
    """

    def __init__(self, config: FileProcessorDeps) -> None:
        shared = StageShared(
            reporter=config.reporter,
            sampler=StepSampler(config.stats),
            cache_service=config.cache_service,
            language=config.language,
            stop_event=config.stop_event,
            on_stage_started=config.on_stage_started,
        )
        self._shared = shared
        self._text_anonymizer = config.text_anonymizer
        self._correction = config.correction
        self._names = NameHiding(anonymizer=config.anonymizer, reporter=config.reporter)
        self._reading = ReadingStage(
            shared,
            preprocess_service=config.preprocess_service,
            ocr_stage_service=config.ocr_stage_service,
            scan_places=config.scan_places,
            postprocessor=config.postprocessor,
            recognize=config.recognize,
            on_ocr_done=config.on_ocr_done,
        )
        self._proofreading = CorrectionStage(
            shared, correction=config.correction, names=self._names
        )
        self._review = ReviewStage(shared, evaluation=config.evaluation, names=self._names)
        # One writer per processor, i.e. per run: the claims of its outputs span every file.
        self._outputs = OutputWriter(
            output_dir=config.output_dir,
            input_dir=config.input_dir,
            recursive=config.recursive,
            write_text=config.write_text,
            text_anonymizer=config.text_anonymizer,
            doc_creator=config.doc_creator,
        )

    def process(self, file: Path) -> ProcessingResult:
        """Process *file* through all pipeline stages.

        Every stage reads the texts the document has *now*: the teacher's transcription where
        they fixed or checked it, their corrections where they apply to the stored answer.
        The machine's own texts stay in the cache untouched.

        One failure contract: a stage that could not do its work raises
        :exc:`~teachers_teammate.exceptions.StageError`, which ends this file with the sentence a
        teacher reads — the stages before it keep what they stored, and the next file runs.  A
        file that is left out (:exc:`~teachers_teammate.exceptions.FileSkipped`) and one the
        teacher stopped
        (:class:`~teachers_teammate.infrastructure.workflow.processing_result.Stopped`) are
        neither a success nor a failure.
        """
        return self._guarded(lambda: self._process(file))

    def read(self, file: Path) -> ProcessingResult | None:
        """Read *file* up to its transcription, and keep it; ``None`` when it was read.

        The reading pass of a run: nothing is proofread, and the reading is not announced as
        done, because the file's turn reads it from what this one stored and reports all of it.
        Otherwise the result is what its turn reports without reading it again: a failure, a
        file left out, or a stop.
        """
        return self._guarded(lambda: self._read_only(file))

    def _read_only(self, file: Path) -> None:
        """Read *file* up to its transcription; a way out of it raises."""
        self._reading.run(file, self._prepare_cache(file), announce=False)

    def _process(self, file: Path) -> ProcessingResult:
        """Run every stage of *file*; a way out of it raises (see :meth:`process`)."""
        reading = self._reading.run(file, self._prepare_cache(file))
        texts = self._current_texts(reading.ctx)

        # The extracted text is anonymized once, and only when something asks for it: a
        # proofreading that is not served from the cache.  Without proofreading nothing
        # else would ask, so it runs here to fill the session cache the previews read.
        anonymized = self._names.lazy(texts.transcription)
        if self._correction is None and self._names.enabled and not self._shared.stopping():
            anonymized("")  # the anonymized text file, not one stage's request

        proofread = self._proofreading.run(
            file, texts, reading.preview_img, reading.ctx, anonymized
        )
        # Again: a new proofreading makes the teacher's corrections part not apply.
        texts = self._current_texts(proofread.ctx)
        reviewed = self._review.run(file, texts, reading.preview_img, proofread=proofread)
        result = self._outputs.write(
            file,
            OutputTexts(
                texts.transcription,
                texts.corrected,
                reviewed.text,
                task=self._task_segments(
                    proofread.ctx, texts.corrected, texts.transcription_format
                ),
            ),
            reading.preview_img,
            reading.source_image,
            proofread.warnings + reviewed.warnings,
            text_format=texts.transcription_format,
            review=proofread.review,
            anonymized_raw=self._names.reusable_text(anonymized, self._text_anonymizer),
        )
        return replace(result, correction_warning=proofread.stored_warning)

    def _guarded[T](self, work: Callable[[], T]) -> T | ProcessingResult:
        """Return what *work* returns, or the result each way out of a file ends in."""
        try:
            return work()
        except RunStoppedError:
            # A cancelled request that no stage turned into a result itself: nothing of that
            # step was stored, and the steps before it already are.
            return ProcessingResult.stopped_at()
        except Stopped as stopped:
            return stopped.result
        except FileSkipped as left_out:
            return ProcessingResult.left_out(str(left_out))
        except StageError as exc:
            self._shared.sampler.finish_sample(usable=False)  # a failed step's requests say nothing
            _logger.error("%s stage failed (%s): %s", exc.stage or "pipeline", exc.kind, exc)
            return ProcessingResult.failure(str(exc))

    # ── Stage helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _task_segments(
        ctx: CacheContext, corrected: str, text_format: TextFormat
    ) -> tuple[Segment, ...]:
        """Return where the printed text stands in the plain *corrected* text, for the Word file.

        The corrected text has had the student's stretches changed, so the marks are matched in
        it again; nothing when the run leaves no text alone.
        """
        if not corrected or not (ctx.task_marks or ctx.not_task):
            return ()
        return locate(plain_text_of(corrected, text_format), ctx.task_marks, ctx.not_task)

    @staticmethod
    def _current_texts(ctx: CacheContext) -> CurrentTexts:
        """Return the texts the document has now: the teacher's where they exist."""
        return current_texts(machine_texts(ctx.state), ctx.work)

    def _prepare_cache(self, file: Path) -> CacheContext:
        try:
            return self._shared.cache_service.prepare(file)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError) and not file.exists():
                self._shared.reporter.status(report_text.file_skipped(FILE_GONE_MESSAGE))
                raise FileSkipped(FILE_GONE_MESSAGE) from exc
            raise stage_error(input_read_failed, exc, StageErrorKind.STORAGE) from exc
