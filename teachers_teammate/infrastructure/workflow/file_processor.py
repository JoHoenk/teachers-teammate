"""Per-file processing logic for the OCR pipeline."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
import logging
from pathlib import Path
import threading
from typing import TYPE_CHECKING

from ...domain.anonymization import leftover_placeholders, restore_with_offsets
from ...domain.formatted_text import TextFormat, join_labelled_pages
from ...domain.output_files import (
    ANONYMIZED_TEXT_SUFFIX,
    DOCX_SUFFIX,
    TEXT_SUFFIX,
    feeds_inputs,
    output_path,
)
from ...domain.performance_estimate import SpeedStep
from ...domain.proofreading.result import CorrectionOutcome
from ...domain.proofreading.review import CorrectionReview, fits, fitted_review
from ...domain.stages import ProgressStage, Stage
from ...domain.teacher_work import (
    CurrentTexts,
    TeacherWork,
    TranscriptionWork,
    current_texts,
    text_identity,
)
from ...exceptions import (
    FILE_GONE_MESSAGE,
    RECOGNITION_OFF_MESSAGE,
    AnonymizerError,
    FileSkipped,
    PipelineInputError,
    RecognitionSwitchedOffError,
    RunStoppedError,
    StageError,
    StageErrorKind,
    stop_requested,
)
from ...interfaces import (
    AnonymizationMap,
    Anonymizer,
    Corrector,
    DocumentCreator,
    Evaluator,
    OnOcrDoneFn,
    OnStageStartedFn,
    Reporter,
)
from ...messages import (
    Message,
    cache_save_failed,
    empty_ocr_answer,
    input_read_failed,
    message_of,
    output_overwrites_input,
    output_read_back_as_input,
    output_replaces_another,
    personal_info_removal_failed,
    personal_info_restore_failed,
    preprocessing_failed,
    preview_save_failed,
)
from ...texts import report as report_text
from ...texts.review import REVIEW_DOES_NOT_FIT, personal_info_still_hidden
from ...texts.run import RESULTS_CACHED, docx_failed, output_not_saved, text_files_failed
from ..ocr_formats import plain_text_of
from ..reporting import StdoutReporter
from ..run_stats import RunStatsCollector
from ..state_repository import machine_texts
from .cache_service import CacheContext, CacheReconciliationService
from .ocr_service import OCRStageService
from .preprocess_service import PreparedInput, PreprocessService

if TYPE_CHECKING:
    from ..ocr_formats import OcrOutputPostprocessor

_logger = logging.getLogger(__name__)


def _report_preparation(reporter: Reporter, prepared: PreparedInput) -> None:
    """Say what the automatic preparation did with the pages of the file."""
    if not prepared.ocr_inputs:
        reporter.status(report_text.PREPROCESSING_TEXT_INPUT)
    elif not prepared.steps:
        reporter.status(report_text.PREPROCESSING_NO_STEPS)
    else:
        applied = ", ".join(dict.fromkeys(prepared.steps))
        reporter.status(report_text.preprocessing_done(applied, len(prepared.ocr_inputs)))


def _teacher_reading(work: TeacherWork | None) -> TranscriptionWork | None:
    """Return the teacher's transcription of the file, or ``None`` when there is none to go by."""
    reading = work.transcription if work is not None else None
    if reading is None or not reading.text.strip():
        return None  # an emptied text is not a reading to go by
    return reading


def _stage_error(
    sentence: Callable[[Message], Message], exc: BaseException, kind: StageErrorKind
) -> StageError:
    """Return the :class:`StageError` that says *sentence* about *exc*, of *kind*."""
    return StageError(sentence(message_of(exc)), kind=kind)


@dataclass(frozen=True)
class ProcessingResult:
    """Result of processing a single input file through all pipeline stages.

    A saved file carries its texts (the run counts the changes of its proofreading); a file that
    failed, was left out or was stopped carries the sentence a teacher reads and nothing else.
    """

    ok: bool
    message: str
    preview_img: str = ""
    raw_text: str = ""
    correction_text: str = ""
    evaluation_text: str = ""
    warnings: tuple[str, ...] = ()
    text_format: TextFormat = "plain"
    """Notation of ``raw_text`` and ``correction_text`` (``"plain"`` or ``"markdown"``)."""
    correction_review: CorrectionReview | None = None
    """Explanations and unclear expressions of the proofreading, ``None`` without a review."""
    correction_warning: str = ""
    """Why this file has no review (e.g. the fallback to corrections-only); empty when none."""
    skipped: bool = False
    """The file was left out on purpose (a scan while text recognition is off): neither a
    success nor a failure.  ``ok`` is ``False`` and ``message`` says why."""
    stopped: bool = False
    """The run was stopped before this file was complete: neither a success nor a failure.
    ``ok`` is ``False`` and ``message`` says before which step; the steps that were done are
    stored and reused by the next run."""
    stopped_stage: str = ""
    """With ``stopped``: the :class:`~teachers_teammate.domain.stages.Stage` the stop cut short
    (``"ocr"``, ``"correction"`` or ``"evaluation"``); empty when the file did not get as far as
    telling."""

    @classmethod
    def failure(cls, message: str) -> ProcessingResult:
        """Return the result of a file that failed, with the sentence a teacher reads."""
        return cls(False, message)

    @classmethod
    def left_out(cls, message: str) -> ProcessingResult:
        """Return the result of a file skipped on purpose; *message* says why."""
        return cls(False, message, skipped=True)

    @classmethod
    def stopped_at(
        cls,
        stage: str = "",
        *,
        preview_img: str = "",
        raw_text: str = "",
        correction_text: str = "",
        text_format: TextFormat = "plain",
        correction_review: CorrectionReview | None = None,
        correction_warning: str = "",
    ) -> ProcessingResult:
        """Return the result of a file stopped at *stage*, carrying only what is stored.

        *stage* stays empty when no stage of this file turned the stop into a result.
        """
        return cls(
            False,
            "",
            preview_img=preview_img,
            raw_text=raw_text,
            correction_text=correction_text,
            text_format=text_format,
            correction_review=correction_review,
            correction_warning=correction_warning,
            stopped=True,
            stopped_stage=stage,
        )


REVIEW_UNMATCHED = (
    "The explanations could not be matched to the names in the text and were left out."
)
"""Warning when the review of an anonymized text could not be carried over to the real names."""


@dataclass(frozen=True)
class _OutputTexts:
    """The three texts one output pass writes: source, proofread and evaluation."""

    raw: str
    corrected: str
    evaluation: str


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


class _Stopped(Exception):
    """Ends this file because the teacher pressed "Stop now"; it carries the result to report.

    Not a failure and not an error: the steps that were done are stored, and the next run picks
    them up.
    """

    def __init__(self, result: ProcessingResult) -> None:
        super().__init__()
        self.result = result


def _stopped_result(
    stage: Stage,
    *,
    preview_img: str = "",
    raw_text: str = "",
    correction_text: str = "",
    text_format: TextFormat = "plain",
    correction_review: CorrectionReview | None = None,
    correction_warning: str = "",
) -> _Stopped:
    """Return the stop that ends a file at *stage*.

    It carries only what is already stored (the texts, and the review and warning that belong
    to a stored correction), so the row shows what the next run reuses.
    """
    return _Stopped(
        ProcessingResult.stopped_at(
            stage,
            preview_img=preview_img,
            raw_text=raw_text,
            correction_text=correction_text,
            text_format=text_format,
            correction_review=correction_review,
            correction_warning=correction_warning,
        )
    )


class _LazyAnonymization:
    """The anonymized extracted text of one file: computed on the first call, then reused."""

    def __init__(self, compute: Callable[[str], tuple[str, AnonymizationMap]]) -> None:
        self._compute = compute
        #: ``(text, mapping)`` once it was computed, else ``None``.
        self.result: tuple[str, AnonymizationMap] | None = None

    def __call__(self, stage: Stage | str = "") -> tuple[str, AnonymizationMap]:
        if self.result is None:
            self.result = self._compute(stage)
        return self.result


@dataclass(frozen=True)
class _Sent:
    """What went out for one proofreading: the text and the mask it was sent with."""

    text: str
    mask: AnonymizationMap


def _served_review(
    ctx: CacheContext, texts: CurrentTexts, file: Path
) -> tuple[CorrectionReview | None, list[str]]:
    """Return the stored review and the warnings its serving adds.

    A review whose words are no longer at the offsets it points at is dropped, by the same rule
    every reader of a stored document applies: the corrected text stays, the explanations do not,
    and the stored review is left as it was written — this run does not write it.
    """
    stored = CorrectionReview.from_dict(ctx.state.correction_review)
    review = fitted_review(stored, plain_text_of(texts.transcription, texts.transcription_format))
    if stored is not None and review is None:
        _logger.warning(
            "Dropped the review of %s: it does not match the text it was made for", file.name
        )
        return None, [REVIEW_DOES_NOT_FIT]
    return review, []


class FileProcessor:
    """Executes all pipeline stages for a single input file.

    Receives pre-built services and optional components; returns a
    :class:`ProcessingResult` to the coordinator for dispatch.
    """

    def __init__(self, config: FileProcessorDeps) -> None:
        self._output_dir = config.output_dir
        self._input_dir = config.input_dir.resolve() if config.input_dir is not None else None
        self._recursive = config.recursive
        self._write_text = config.write_text
        self._text_anonymizer = config.text_anonymizer
        # Output file -> the input document that owns it, for the whole run.
        self._claimed: dict[Path, Path] = {}
        self._claim_lock = threading.Lock()
        self._language = config.language
        self._cache_service = config.cache_service
        self._preprocess_service = config.preprocess_service
        self._ocr_stage_service = config.ocr_stage_service
        self._correction = config.correction
        self._anonymizer = config.anonymizer
        self._postprocessor = config.postprocessor
        self._recognize = config.recognize
        self._evaluation = config.evaluation
        self._doc_creator = config.doc_creator
        self._stop_event = config.stop_event
        self._on_ocr_done = config.on_ocr_done
        self._on_stage_started = config.on_stage_started
        self._reporter = config.reporter
        self._stats = config.stats

    def process(self, file: Path) -> ProcessingResult:
        """Process *file* through all pipeline stages.

        Every stage reads the texts the document has *now*: the teacher's transcription where
        they fixed or checked it, their corrections where they apply to the stored answer.
        The machine's own texts stay in the cache untouched.

        One failure contract: a stage that could not do its work raises
        :exc:`~teachers_teammate.exceptions.StageError`, which ends this file with the sentence a
        teacher reads — the stages before it keep what they stored, and the next file runs.  A
        file that is left out (:exc:`~teachers_teammate.exceptions.FileSkipped`) and one the
        teacher stopped (:class:`_Stopped`) are neither a success nor a failure.
        """
        try:
            ctx = self._prepare_cache(file)
            _read, preview_img, source_image, ctx = self._run_preprocess_or_cache(file, ctx)
            texts = self._current_texts(ctx)

            # The extracted text is anonymized once, and only when something asks for it: a
            # proofreading that is not served from the cache.  Without proofreading nothing
            # else would ask, so it runs here to fill the session cache the previews read.
            anonymized = self._lazy_personal_info_removal(texts.transcription)
            if self._correction is None and self._anonymizer is not None and not self._stopping():
                anonymized()

            review, ctx, correction_warnings = self._run_correction_or_cache(
                file, texts, preview_img, ctx, anonymized
            )
            # Again: a new proofreading makes the teacher's corrections part not apply.
            texts = self._current_texts(ctx)
            evaluation_text, eval_warnings = self._run_evaluation_or_cache(
                file,
                texts,
                preview_img,
                ctx,
                stored_review=(review, "; ".join(correction_warnings)),
            )
            all_warnings = tuple(correction_warnings + eval_warnings)

            # Only an anonymizer that also writes the anonymized text file can reuse the pre-pass.
            done = anonymized.result
            reused = (
                done[0] if done is not None and self._text_anonymizer is self._anonymizer else None
            )
            result = self._write_outputs(
                file,
                _OutputTexts(texts.transcription, texts.corrected, evaluation_text),
                preview_img,
                source_image,
                all_warnings,
                text_format=texts.transcription_format,
                review=review,
                anonymized_raw=reused,
            )
        except RunStoppedError:
            # A cancelled request that no stage turned into a result itself: nothing of that
            # step was stored, and the steps before it already are.
            return ProcessingResult.stopped_at()
        except _Stopped as stopped:
            return stopped.result
        except FileSkipped as left_out:
            return ProcessingResult.left_out(str(left_out))
        except StageError as exc:
            self._finish_sample(usable=False)  # the requests of a failed step say nothing
            _logger.error("%s stage failed (%s): %s", exc.stage or "pipeline", exc.kind, exc)
            return ProcessingResult.failure(str(exc))
        return replace(result, correction_warning="; ".join(correction_warnings))

    # ── Stage helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _current_texts(ctx: CacheContext) -> CurrentTexts:
        """Return the texts the document has now: the teacher's where they exist."""
        return current_texts(machine_texts(ctx.state), ctx.work)

    def _prepare_cache(self, file: Path) -> CacheContext:
        try:
            return self._cache_service.prepare(file)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError) and not file.exists():
                self._reporter.status(report_text.file_skipped(FILE_GONE_MESSAGE))
                raise FileSkipped(FILE_GONE_MESSAGE) from exc
            raise _stage_error(input_read_failed, exc, StageErrorKind.STORAGE) from exc

    def _run_preprocess_or_cache(
        self, file: Path, ctx: CacheContext
    ) -> tuple[str, str, Path | None, CacheContext]:
        """Handle cache-hit or full preprocess+OCR path.

        Returns (raw_text, preview_img, source_image, ctx) on success.
        """
        cache_state = ctx.state

        if cache_state.ocr_done and cache_state.raw_text:
            return self._load_ocr_from_cache(file, ctx)

        return self._run_preprocess_and_ocr(file, ctx)

    def _load_ocr_from_cache(
        self, file: Path, ctx: CacheContext
    ) -> tuple[str, str, Path | None, CacheContext]:
        cache_state = ctx.state
        raw_text = cache_state.raw_text
        preview_img = cache_state.preview_img
        source_image_str = cache_state.source_image or preview_img
        source_image = Path(source_image_str) if source_image_str else None
        if not preview_img and source_image is not None and source_image.exists():
            try:
                # Re-persisting the preview must not drop what is already recorded about it.
                kept_original = Path(ctx.state.original_img) if ctx.state.original_img else None
                preview_img, ctx = self._cache_service.persist_preview_artifact(
                    file,
                    ctx,
                    source_image,
                    original_paths=[kept_original] if kept_original is not None else (),
                    preprocess_steps=ctx.state.preprocess_steps,
                )
            except OSError as exc:
                _logger.warning("Cannot store the preview image of %s: %s", file.name, exc)
        self._reporter.status(report_text.CACHED_OCR)
        if self._on_ocr_done:
            self._on_ocr_done(str(file.resolve()), file.name)
        return raw_text, preview_img, source_image, ctx

    def _notify_stage(self, file: Path, stage: ProgressStage) -> None:
        if self._on_stage_started:
            self._on_stage_started(str(file.resolve()), stage)

    def _run_preprocess_and_ocr(
        self, file: Path, ctx: CacheContext
    ) -> tuple[str, str, Path | None, CacheContext]:
        """Prepare the pages of *file* and read them, or take the text the file already has."""
        reading = _teacher_reading(ctx.work)
        prepared = self._prepared(file, reading)
        pages: Sequence[Path] = prepared.ocr_inputs or prepared.page_images
        preview_img, ctx = self._persist_preview(file, ctx, prepared, pages)
        _report_preparation(self._reporter, prepared)
        reading = reading if prepared.raw_text_hint is None else None
        if reading is not None:
            prepared = prepared._replace(raw_text_hint=reading.text)
        if self._stopping() and prepared.raw_text_hint is None:
            raise _stopped_result(Stage.OCR, preview_img=preview_img)
        try:
            raw_text, ctx = self._run_ocr(
                file,
                prepared.ocr_inputs,
                prepared,
                preview_img,
                prepared.source_image,
                ctx,
                reading=reading,
            )
        except RunStoppedError as exc:
            raise _stopped_result(Stage.OCR, preview_img=preview_img) from exc
        return raw_text, preview_img, prepared.source_image, ctx

    def _prepared(self, file: Path, reading: TranscriptionWork | None) -> PreparedInput:
        """Return the pages of *file*, prepared; raises when the file cannot be read."""
        if file.suffix.lower() != ".txt":
            self._reporter.status(report_text.PREPROCESSING)
            self._notify_stage(file, ProgressStage.PREPROCESSING)
        try:
            if self._recognize or reading is not None:
                return self._preprocess_service.preprocess_input(file)
            return self._preprocess_service.preprocess_input(file, allow_images=False)
        except RecognitionSwitchedOffError as exc:
            self._reporter.status(report_text.file_skipped(RECOGNITION_OFF_MESSAGE))
            raise FileSkipped(str(exc)) from exc
        except (OSError, ValueError, RuntimeError, PipelineInputError) as exc:
            raise _stage_error(preprocessing_failed, exc, StageErrorKind.PREPROCESSING) from exc

    def _persist_preview(
        self,
        file: Path,
        ctx: CacheContext,
        prepared: PreparedInput,
        pages: Sequence[Path],
    ) -> tuple[str, CacheContext]:
        """Store the page the preview shows, with the pages as they were scanned."""
        # The steps of the first page: the preview shows that page.  A PDF read through its text
        # layer has no image units, but its pages are what the teacher compares the text with.
        steps = prepared.steps_per_page[0] if prepared.steps_per_page else ()
        preview_source = pages[0] if pages else prepared.source_image
        if preview_source is None:
            return "", ctx
        # Keep the pages as they were scanned too, but only when preprocessing changed them:
        # without a step the original is the prepared page.
        originals = prepared.original_pages
        changed = (
            bool(steps)
            and bool(originals)
            and originals[0] != preview_source
            and originals[0].exists()
        )
        try:
            return self._cache_service.persist_preview_artifact(
                file,
                ctx,
                preview_source,
                original_paths=originals if changed else (),
                preprocess_steps=steps if prepared.ocr_inputs else None,
                pages=pages,
            )
        except OSError as exc:
            raise _stage_error(preview_save_failed, exc, StageErrorKind.STORAGE) from exc

    def _stopping(self) -> bool:
        """Whether the teacher pressed "Stop now" (the other Stop only keeps new files away)."""
        return stop_requested(self._stop_event)

    def _report_ocr_page(self, page: int, total: int) -> None:
        self._reporter.status(report_text.ocr_page(page, total))

    def _begin_sample(self, step: SpeedStep) -> None:
        """Mark *step* as running, so the requests it sends become one sample."""
        if self._stats is not None:
            self._stats.begin(step)

    def _finish_sample(self, *, words: int = 0, mode: str = "", usable: bool = True) -> None:
        """Turn the requests of the step that just ended into one sample.

        *words* is the text that went through, *mode* the proofreading mode really used.  A step
        that sent no request (a result from the cache) gives no sample, and neither does one
        that is not *usable*: a failed step, or a review that fell back to corrections only,
        whose requests would be counted into the answer length of the mode that finally answered.
        """
        if self._stats is None:
            return
        if usable:
            self._stats.finish(words=words, mode=mode)
        else:
            self._stats.discard()

    def _run_ocr(
        self,
        file: Path,
        ocr_inputs: list[Path],
        prepared: PreparedInput,
        preview_img: str,
        source_image: Path | None,
        ctx: CacheContext,
        *,
        reading: TranscriptionWork | None = None,
    ) -> tuple[str, CacheContext]:
        raw_text_hint = prepared.raw_text_hint
        page_count = 0
        raw_text_format: TextFormat = "plain"
        if raw_text_hint is not None:
            raw_text = raw_text_hint
            if reading is not None:
                raw_text_format = reading.text_format
                self._reporter.status(report_text.USED_TRANSCRIPTION)
            elif prepared.text_origin == "pdf_text_layer":
                self._reporter.status(report_text.USED_PDF_TEXT)
            else:
                self._reporter.status(report_text.USED_TEXT_INPUT)
        else:
            if prepared.text_layer_note:
                self._reporter.status(report_text.pdf_text_layer_note(prepared.text_layer_note))
            self._reporter.status(report_text.RUNNING_OCR)
            self._notify_stage(file, ProgressStage.OCR)

            def page_started(page: int, total: int) -> None:
                if total > 1:
                    self._report_ocr_page(page, total)
                self._begin_sample(SpeedStep.READING)

            def page_done(text: str) -> None:
                self._finish_sample(words=len(text.split()))

            page_texts = self._ocr_stage_service.run_pages(
                ocr_inputs, self._language, on_page=page_started, on_page_done=page_done
            )
            page_count = len(page_texts)
            if self._postprocessor is not None:
                processed = self._postprocessor.process_pages(page_texts)
                raw_text = processed.text
                raw_text_format = processed.text_format
            else:
                raw_text = join_labelled_pages(page_texts)

        if not raw_text:
            raise StageError(empty_ocr_answer(), kind=StageErrorKind.ENGINE, stage=Stage.OCR)

        try:
            ctx = self._cache_service.record_ocr(
                file,
                ctx,
                raw_text=raw_text,
                preview_img=preview_img,
                source_image=preview_img or (str(source_image) if source_image else ""),
                raw_text_format=raw_text_format,
            )
        except OSError as exc:
            raise _stage_error(cache_save_failed, exc, StageErrorKind.STORAGE) from exc

        if raw_text_hint is None:
            self._reporter.status(report_text.ocr_done(page_count))
            if self._on_ocr_done:
                self._on_ocr_done(str(file.resolve()), file.name)

        return raw_text, ctx

    def _lazy_personal_info_removal(self, text: str) -> _LazyAnonymization:
        """Return a callable that hides personal data in the extracted *text* once.

        Nothing runs until it is called.  A failing anonymizer (spaCy missing, a text over its
        length limit, …) ends this file with a message instead of the whole run.
        """
        return _LazyAnonymization(lambda stage: self._hide_personal_info(text, stage=stage))

    def _hide_personal_info(
        self, text: str, *, stage: Stage | str = "", before_model: bool = False
    ) -> tuple[str, AnonymizationMap]:
        """Hide personal data in *text* before a model sees it (no-op without an anonymizer).

        *stage* names the stage a failure belongs to; *before_model* is the pass made for one
        request of its own (the review) rather than the one the proofreading shares.

        The one place that calls the anonymizer, so every call — the pass the proofreading uses,
        the one before the content review and the one before the anonymized text file — ends a
        failing file the same way instead of ending the run.
        """
        if self._anonymizer is None:
            return text, {}
        try:
            anonymized, anon_map = self._anonymizer.anonymize(text)
        except Exception as exc:  # spaCy and name-model errors vary; report them per file
            raise AnonymizerError(
                personal_info_removal_failed(message_of(exc)), stage=stage
            ) from exc
        if before_model:
            self._reporter.status(report_text.anonymized_before(stage))
        else:
            self._reporter.status(report_text.personal_info_removed(len(anon_map)))
        return anonymized, anon_map

    def _restore_pii(
        self, text: str, anon_map: AnonymizationMap, *, stage: Stage | str = ""
    ) -> str:
        """Put the real personal data back into *text*; end the file when that fails."""
        if not (anon_map and text and self._anonymizer is not None):
            return text
        try:
            return self._anonymizer.restore(text, anon_map)
        except (ValueError, KeyError) as exc:
            raise AnonymizerError(
                personal_info_restore_failed(message_of(exc)), stage=stage
            ) from exc

    @staticmethod
    def _left_hidden(text: str, anon_map: AnonymizationMap) -> str:
        """Return the warning about personal data still hidden in *text*, or ``""``."""
        left = leftover_placeholders(text, anon_map)
        return personal_info_still_hidden(left) if left else ""

    def _run_correction_or_cache(
        self,
        file: Path,
        texts: CurrentTexts,
        preview_img: str,
        ctx: CacheContext,
        anonymized: _LazyAnonymization,
    ) -> tuple[CorrectionReview | None, CacheContext, list[str]]:
        """Proofread the current transcription, or return the stored answer.

        The teacher's transcription is what is sent: it is what the student wrote, as they read
        it.  A stored answer is served as it is, and the teacher's corrections of it are applied
        by the caller, which reads the current texts again.
        """
        if self._correction is None:
            return None, ctx, []
        if ctx.state.correction_done and ctx.state.correction_text:
            return self._stored_correction(file, texts, ctx)

        # Before the anonymization: it is a full spaCy pass that a stop should not wait for.
        if self._stopping():
            raise self._stop_correction(preview_img, texts)
        self._reporter.status(report_text.RUNNING_CORRECTION)
        self._notify_stage(file, ProgressStage.CORRECTION)
        sent = _Sent(*anonymized(Stage.CORRECTION))
        # A text that goes out in several pieces cannot be changed across a piece boundary;
        # say so before the requests start, so the teacher knows what to expect.
        pieces = self._correction.pieces(
            sent.text, self._language, text_format=texts.transcription_format
        )
        if pieces is not None and pieces > 1:
            self._reporter.status(report_text.proofreading_in_parts(file.name, pieces))
        if self._stopping():  # the anonymization may have taken a while
            raise self._stop_correction(preview_img, texts)
        outcome = self._proofread(self._correction, sent.text, texts, preview_img)
        return self._keep_correction(file, texts, ctx, outcome, sent)

    def _stored_correction(
        self, file: Path, texts: CurrentTexts, ctx: CacheContext
    ) -> tuple[CorrectionReview | None, CacheContext, list[str]]:
        """Return the answer of an earlier run, with the note that says where it comes from."""
        if ctx.proofreading_kept:
            self._reporter.status(report_text.KEPT_PROOFREADING)
        else:
            self._reporter.status(report_text.CACHED_CORRECTION)
        review, dropped = _served_review(ctx, texts, file)
        warnings = [ctx.state.correction_warning] if ctx.state.correction_warning else []
        return review, ctx, [*warnings, *dropped]

    def _stop_correction(self, preview_img: str, texts: CurrentTexts) -> _Stopped:
        """Return the stop that ends a file at the proofreading."""
        return _stopped_result(
            Stage.CORRECTION,
            preview_img=preview_img,
            raw_text=texts.transcription,
            text_format=texts.transcription_format,
        )

    def _proofread(
        self, corrector: Corrector, text: str, texts: CurrentTexts, preview_img: str
    ) -> CorrectionOutcome:
        """Return the corrector's answer to *text*, with the sample of the run recorded."""
        self._begin_sample(SpeedStep.PROOFREADING)
        try:
            outcome = corrector.correct(
                text, self._language, text_format=texts.transcription_format
            )
        except RunStoppedError as exc:
            raise self._stop_correction(preview_img, texts) from exc
        # The mode the answer was really written in: a run that came back without a review
        # answered in text mode, and its answer length is the text-mode one.
        self._finish_sample(
            words=len(text.split()),
            mode="exchange" if outcome.review is not None else "text",
            usable=not (outcome.failed or outcome.fell_back),
        )
        return outcome

    def _keep_correction(
        self,
        file: Path,
        texts: CurrentTexts,
        ctx: CacheContext,
        outcome: CorrectionOutcome,
        sent: _Sent,
    ) -> tuple[CorrectionReview | None, CacheContext, list[str]]:
        """Restore the personal data of the answer, store it, and say what is worth a warning."""
        corrected_text = self._restore_pii(outcome.text or "", sent.mask, stage=Stage.CORRECTION)
        review, unmatched = self._restore_review_pii(
            outcome.review,
            sent.mask,
            sent_text=sent.text,
            raw_text=texts.transcription,
            text_format=texts.transcription_format,
            file_name=file.name,
        )
        warnings: list[str] = [outcome.warning] if outcome.warning else []
        if unmatched:
            warnings.append(REVIEW_UNMATCHED)
        left_hidden = self._left_hidden(corrected_text, sent.mask)
        if left_hidden:
            warnings.append(left_hidden)
        stored_warning = "; ".join(w for w in (outcome.warning, left_hidden) if w)
        # A failed outcome is the unchanged source; caching it would present that as a finished
        # correction and the next run would never try again.  (A corrections-only fallback
        # *is* stored: the model answered, and asking it for explanations again would repeat
        # the failed requests.)
        if corrected_text and not outcome.failed:
            ctx = self._store_correction(
                file, texts, ctx, stored_warning, corrected_text, review, sent
            )
        return review, ctx, warnings

    def _store_correction(
        self,
        file: Path,
        texts: CurrentTexts,
        ctx: CacheContext,
        warning: str,
        corrected_text: str,
        review: CorrectionReview | None,
        sent: _Sent,
    ) -> CacheContext:
        """Store the answer with what was sent; a write that fails leaves it this run's only."""
        try:
            return self._cache_service.record_correction(
                file,
                ctx,
                correction_text=corrected_text,
                correction_review=review.to_dict() if review is not None else None,
                correction_warning=warning,
                # The text the proofreading was made from, stored with its result so the window
                # can show what really went out.  With hiding off it is the extracted text
                # itself, so nothing is duplicated.
                sent_text=sent.text if sent.mask else "",
                sent_map=dict(sent.mask) if sent.mask else None,
                # The transcription the answer was made from, so a proofreading made from a
                # text the document no longer has is stale.
                source_text=texts.transcription,
            )
        except OSError as exc:
            _logger.warning("Could not save correction state for %s: %s", file.name, exc)
            # The answer is still this run's: the outputs and the review are made from it, even
            # though the next run will not find it in the cache.
            return replace(
                ctx,
                state=replace(
                    ctx.state,
                    correction_done=True,
                    correction_text=corrected_text,
                    correction_review=review.to_dict() if review is not None else None,
                    correction_warning=warning,
                    correction_source_hash=text_identity(texts.transcription),
                ),
            )

    def _restore_review_pii(
        self,
        review: CorrectionReview | None,
        anon_map: AnonymizationMap,
        *,
        sent_text: str,
        raw_text: str,
        text_format: TextFormat,
        file_name: str,
    ) -> tuple[CorrectionReview | None, bool]:
        """Put the real personal data back into *review* and keep its offsets right.

        The corrector answers about the anonymized text, so the review's offsets are in the plain
        text of what was sent and its words quote placeholders.  A name is rarely as long as its
        placeholder, so every edit after a name would point at the wrong words: the review is
        remapped through the offsets of the restore and then checked against the plain text of
        the raw text.  A review that does not fit is dropped, which shows the plain diff and
        keeps the correction itself; the caller says so in the warnings.

        Returns:
            The restored review (or ``None``) and whether it had to be dropped.
        """
        anonymizer = self._anonymizer
        if review is None or not anon_map or anonymizer is None:
            return review, False
        try:
            sent_plain = plain_text_of(sent_text, text_format)
            restored_plain, offsets = restore_with_offsets(sent_plain, anon_map)
            restored = review.map_text(lambda value: anonymizer.restore(value, anon_map))
        except (ValueError, KeyError) as exc:
            _logger.warning("Could not restore PII in the review of %s: %s", file_name, exc)
            return None, True
        raw_plain = plain_text_of(raw_text, text_format)
        remapped = restored.remapped(offsets)
        if restored_plain != raw_plain or not fits(remapped, raw_plain):
            _logger.warning("Could not match the review of %s to the names in the text", file_name)
            return None, True
        return remapped, False

    def _run_evaluation_or_cache(
        self,
        file: Path,
        texts: CurrentTexts,
        preview_img: str,
        ctx: CacheContext,
        *,
        stored_review: tuple[CorrectionReview | None, str] = (None, ""),
    ) -> tuple[str, list[str]]:
        """Run the content review, or return the cached one.

        The review is made from the current corrected text — the teacher's where they changed
        it: it judges the student's content, which their corrections hardly change.

        *stored_review* is the ``(review, warning)`` of the proofreading; a file stopped here
        hands them back with the stored correction, so its row keeps them.
        """
        correction_text = texts.corrected
        if self._evaluation is None or not correction_text:
            return "", []

        cache_state = ctx.state
        if cache_state.evaluation_done and cache_state.evaluation_text:
            self._reporter.status(report_text.CACHED_EVALUATION)
            warnings = [cache_state.evaluation_warning] if cache_state.evaluation_warning else []
            return cache_state.evaluation_text, warnings

        stored = ctx.state.correction_done
        review, review_warning = stored_review

        def stopped() -> _Stopped:
            return _stopped_result(
                Stage.EVALUATION,
                preview_img=preview_img,
                raw_text=texts.transcription,
                correction_text=correction_text if stored else "",
                text_format=texts.transcription_format,
                correction_review=review if stored else None,
                correction_warning=review_warning if stored else "",
            )

        # Before the anonymization: it is a spaCy pass that a stop should not wait for.
        if self._stopping():
            raise stopped()
        self._reporter.status(report_text.RUNNING_EVALUATION)
        # The proofread text has its real names back, so hide them again: the review may
        # run on an online service just like proofreading.
        text_for_evaluation, anon_map = self._hide_personal_info(
            correction_text, stage=Stage.EVALUATION, before_model=True
        )
        if self._stopping():
            raise stopped()
        self._begin_sample(SpeedStep.REVIEW)
        try:
            evaluation = self._evaluation.evaluate(
                text_for_evaluation, self._language, text_format=texts.transcription_format
            )
        except RunStoppedError as exc:
            raise stopped() from exc
        self._finish_sample(
            words=len(text_for_evaluation.split()), usable=evaluation.warning is None
        )
        eval_text = self._restore_pii(evaluation.text or "", anon_map, stage=Stage.EVALUATION)
        eval_warnings: list[str] = [evaluation.warning] if evaluation.warning else []
        if left_hidden := self._left_hidden(eval_text, anon_map):
            eval_warnings.append(left_hidden)
        if eval_text:
            try:
                self._cache_service.record_evaluation(
                    file,
                    ctx,
                    evaluation_text=eval_text,
                    evaluation_warning="; ".join(eval_warnings),
                    names_hidden=len(anon_map),
                )
            except OSError as exc:
                _logger.warning("Could not save evaluation state for %s: %s", file.name, exc)
        return eval_text or "", eval_warnings

    def _output_file(self, file: Path, suffix: str) -> Path:
        return output_path(file.resolve(), self._input_dir, self._output_dir, suffix)

    def _planned_outputs(self, file: Path) -> dict[str, Path]:
        """Return ``{suffix: path}`` for every output file this run writes for *file*."""
        suffixes: list[str] = []
        if self._doc_creator is not None:
            suffixes.append(DOCX_SUFFIX)
        if self._write_text:
            suffixes.append(TEXT_SUFFIX)
        if self._text_anonymizer is not None:
            suffixes.append(ANONYMIZED_TEXT_SUFFIX)
        return {suffix: self._output_file(file, suffix) for suffix in suffixes}

    def _shown(self, target: Path) -> str:
        """Name an output file by its path below the output folder (subfolders can repeat names)."""
        return target.relative_to(self._output_dir).as_posix()

    def _claim_outputs(self, file: Path, targets: dict[str, Path]) -> None:
        """Reserve *targets* for *file*; refuse targets that would harm inputs or other outputs.

        Nothing is written for a refused file, and nothing is reserved.

        Raises:
            ValueError: a text file would replace an input document or land where the run reads
                inputs, or another document of this run already writes the same output file.
        """
        source = file.resolve()
        for suffix, target in targets.items():
            if suffix == DOCX_SUFFIX:  # Word documents are not an input type
                continue
            if target.resolve() == source:
                raise ValueError(output_overwrites_input(target.name))
            if self._input_dir is not None and feeds_inputs(
                target.parent.resolve(), self._input_dir, recursive=self._recursive
            ):
                raise ValueError(output_read_back_as_input(self._shown(target)))
        with self._claim_lock:
            for target in targets.values():
                owner = self._claimed.get(target.resolve())
                if owner is not None and owner != source:
                    raise ValueError(output_replaces_another(self._shown(target), owner.name))
            for target in targets.values():
                self._claimed[target.resolve()] = source

    def _write_outputs(
        self,
        file: Path,
        texts: _OutputTexts,
        preview_img: str,
        source_image: Path | None,
        warnings: tuple[str, ...] = (),
        *,
        text_format: TextFormat = "plain",
        review: CorrectionReview | None = None,
        anonymized_raw: str | None = None,
    ) -> ProcessingResult:
        def failed(message: str) -> StageError:
            return StageError(message, kind=StageErrorKind.OUTPUT)

        targets = self._planned_outputs(file)
        try:
            self._claim_outputs(file, targets)
        except ValueError as exc:
            raise failed(output_not_saved(exc)) from exc

        saved: list[str] = []
        try:
            if self._doc_creator is not None:
                docx_file = targets[DOCX_SUFFIX]
                docx_file.parent.mkdir(parents=True, exist_ok=True)
                self._doc_creator.create(
                    texts.raw,
                    texts.corrected,
                    docx_file,
                    docx_file.stem,
                    source_image=source_image,
                    text_format=text_format,
                    review=review,
                )
                saved.append(self._shown(docx_file))
        except Exception as exc:  # python-docx / PIL errors vary widely; surface the message
            raise failed(docx_failed(exc)) from exc
        try:
            self._write_text_files(targets, texts.raw, text_format, saved, anonymized_raw)
        except Exception as exc:  # NER / file-system errors vary; surface the message
            raise failed(text_files_failed(exc, saved)) from exc
        return ProcessingResult(
            True,
            ", ".join(saved) or RESULTS_CACHED,
            preview_img=preview_img,
            raw_text=texts.raw,
            correction_text=texts.corrected,
            evaluation_text=texts.evaluation,
            warnings=warnings,
            text_format=text_format,
            correction_review=review,
        )

    def _write_text_files(
        self,
        targets: dict[str, Path],
        raw_text: str,
        text_format: TextFormat,
        saved: list[str],
        anonymized_raw: str | None = None,
    ) -> None:
        """Save the extracted text (plain and/or anonymized) as UTF-8 text.

        Like the Export menu: the OCR text without markup; anonymizing runs on the text in
        its stored notation, and the placeholder mapping is dropped. Every file is appended
        to *saved* as soon as it is written, so a failure still reports what already exists.

        *anonymized_raw* is the anonymized *raw_text* of the pre-pass, when the same anonymizer
        produced it; otherwise the text file anonymizes on its own.
        """
        jobs: list[tuple[Path, str]] = []
        if TEXT_SUFFIX in targets:
            jobs.append((targets[TEXT_SUFFIX], raw_text))
        if ANONYMIZED_TEXT_SUFFIX in targets and self._text_anonymizer is not None:
            if anonymized_raw is None:
                anonymized_raw, _mapping = self._text_anonymizer.anonymize(raw_text)
            jobs.append((targets[ANONYMIZED_TEXT_SUFFIX], anonymized_raw))
        if jobs and text_format == "markdown":
            # Only Markdown needs the parser (and its dependencies); plain text is written as is.
            jobs = [(target, plain_text_of(text, text_format)) for target, text in jobs]
        for target, text in jobs:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            saved.append(self._shown(target))
