"""The reading stage: the pages of one file, prepared and read, or taken from the cache."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ...domain.formatted_text import TextFormat, join_labelled_pages
from ...domain.page_layout import ScanBox
from ...domain.performance_estimate import SpeedStep
from ...domain.stages import ProgressStage, Stage
from ...domain.teacher_work import TeacherWork, TranscriptionWork
from ...exceptions import (
    RECOGNITION_OFF_MESSAGE,
    FileSkipped,
    PipelineInputError,
    RecognitionSwitchedOffError,
    RunStoppedError,
    StageError,
    StageErrorKind,
)
from ...interfaces import OnOcrDoneFn, Reporter
from ...messages import (
    cache_save_failed,
    empty_ocr_answer,
    preprocessing_failed,
    preview_save_failed,
)
from ...texts import report as report_text
from .cache_service import CacheContext
from .ocr_service import OCRStageService
from .preprocess_service import PreparedInput, PreprocessService
from .processing_result import stopped_result
from .scan_places import ScanPlaces
from .stage_shared import StageShared, stage_error

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


@dataclass(frozen=True)
class Reading:
    """What the reading stage hands to the stages after it."""

    preview_img: str
    """The image the preview shows, or ``""`` when the file has none."""
    source_image: Path | None
    """The page the preview was made from, as it was scanned."""
    ctx: CacheContext
    """The cache state the reading left: the text it read is in it."""


def _teacher_reading(work: TeacherWork | None) -> TranscriptionWork | None:
    """Return the teacher's transcription of the file, or ``None`` when there is none to go by."""
    reading = work.transcription if work is not None else None
    if reading is None or not reading.text.strip():
        return None  # an emptied text is not a reading to go by
    return reading


class ReadingStage:
    """Prepares the pages of one file and reads them, or returns the text the cache holds."""

    def __init__(
        self,
        shared: StageShared,
        *,
        preprocess_service: PreprocessService,
        ocr_stage_service: OCRStageService,
        scan_places: ScanPlaces | None = None,
        postprocessor: OcrOutputPostprocessor | None = None,
        recognize: bool = True,
        on_ocr_done: OnOcrDoneFn | None = None,
    ) -> None:
        self._shared = shared
        self._preprocess_service = preprocess_service
        self._ocr_stage_service = ocr_stage_service
        self._scan_places = scan_places
        self._postprocessor = postprocessor
        self._recognize = recognize
        self._on_ocr_done = on_ocr_done

    def run(self, file: Path, ctx: CacheContext) -> Reading:
        """Return what *file* was read as, from the cache or by reading its pages."""
        cache_state = ctx.state
        if cache_state.ocr_done and cache_state.raw_text:
            return self._load_ocr_from_cache(file, ctx)
        return self._run_preprocess_and_ocr(file, ctx)

    def _load_ocr_from_cache(self, file: Path, ctx: CacheContext) -> Reading:
        cache_state = ctx.state
        preview_img = cache_state.preview_img
        source_image_str = cache_state.source_image or preview_img
        source_image = Path(source_image_str) if source_image_str else None
        if not preview_img and source_image is not None and source_image.exists():
            try:
                # Re-persisting the preview must not drop what is already recorded about it.
                kept_original = Path(ctx.state.original_img) if ctx.state.original_img else None
                preview_img, ctx = self._shared.cache_service.persist_preview_artifact(
                    file,
                    ctx,
                    source_image,
                    original_paths=[kept_original] if kept_original is not None else (),
                    preprocess_steps=ctx.state.preprocess_steps,
                )
            except OSError as exc:
                _logger.warning("Cannot store the preview image of %s: %s", file.name, exc)
        self._shared.reporter.status(report_text.CACHED_OCR)
        if self._on_ocr_done:
            self._on_ocr_done(str(file.resolve()), file.name)
        return Reading(preview_img=preview_img, source_image=source_image, ctx=ctx)

    def _run_preprocess_and_ocr(self, file: Path, ctx: CacheContext) -> Reading:
        """Prepare the pages of *file* and read them, or take the text the file already has."""
        reading = _teacher_reading(ctx.work)
        prepared = self._prepared(file, reading)
        pages: Sequence[Path] = prepared.ocr_inputs or prepared.page_images
        preview_img, ctx = self._persist_preview(file, ctx, prepared, pages)
        _report_preparation(self._shared.reporter, prepared)
        reading = reading if prepared.raw_text_hint is None else None
        if reading is not None:
            prepared = prepared._replace(raw_text_hint=reading.text)
        if self._shared.stopping() and prepared.raw_text_hint is None:
            raise stopped_result(Stage.OCR, preview_img=preview_img)
        try:
            ctx = self._run_ocr(file, prepared, preview_img, ctx, reading=reading)
        except RunStoppedError as exc:
            raise stopped_result(Stage.OCR, preview_img=preview_img) from exc
        return Reading(preview_img=preview_img, source_image=prepared.source_image, ctx=ctx)

    def _prepared(self, file: Path, reading: TranscriptionWork | None) -> PreparedInput:
        """Return the pages of *file*, prepared; raises when the file cannot be read."""
        if file.suffix.lower() != ".txt":
            self._shared.reporter.status(report_text.PREPROCESSING)
            self._shared.notify(file, ProgressStage.PREPROCESSING)
        try:
            if self._recognize or reading is not None:
                return self._preprocess_service.preprocess_input(file)
            return self._preprocess_service.preprocess_input(file, allow_images=False)
        except RecognitionSwitchedOffError as exc:
            self._shared.reporter.status(report_text.file_skipped(RECOGNITION_OFF_MESSAGE))
            raise FileSkipped(str(exc)) from exc
        except (OSError, ValueError, RuntimeError, PipelineInputError) as exc:
            raise stage_error(preprocessing_failed, exc, StageErrorKind.PREPROCESSING) from exc

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
            return self._shared.cache_service.persist_preview_artifact(
                file,
                ctx,
                preview_source,
                original_paths=originals if changed else (),
                preprocess_steps=steps if prepared.ocr_inputs else None,
                pages=pages,
            )
        except OSError as exc:
            raise stage_error(preview_save_failed, exc, StageErrorKind.STORAGE) from exc

    def _report_ocr_page(self, page: int, total: int) -> None:
        self._shared.reporter.status(report_text.ocr_page(page, total))

    def _run_ocr(
        self,
        file: Path,
        prepared: PreparedInput,
        preview_img: str,
        ctx: CacheContext,
        *,
        reading: TranscriptionWork | None = None,
    ) -> CacheContext:
        """Read the pages of *prepared*, or take the text it already carries; record the text."""
        raw_text_hint = prepared.raw_text_hint
        page_count = 0
        raw_text_format: TextFormat = "plain"
        layout: tuple[ScanBox, ...] = ()
        if raw_text_hint is not None:
            raw_text = raw_text_hint
            if reading is not None:
                raw_text_format = reading.text_format
                self._shared.reporter.status(report_text.USED_TRANSCRIPTION)
            elif prepared.text_origin == "pdf_text_layer":
                self._shared.reporter.status(report_text.USED_PDF_TEXT)
            else:
                self._shared.reporter.status(report_text.USED_TEXT_INPUT)
        else:
            raw_text, raw_text_format, page_count, layout = self._read_pages(file, prepared)

        if not raw_text:
            raise StageError(empty_ocr_answer(), kind=StageErrorKind.ENGINE, stage=Stage.OCR)

        try:
            ctx = self._shared.cache_service.record_ocr(
                file,
                ctx,
                raw_text=raw_text,
                preview_img=preview_img,
                source_image=preview_img
                or (str(prepared.source_image) if prepared.source_image else ""),
                raw_text_format=raw_text_format,
                layout=layout,
            )
        except OSError as exc:
            raise stage_error(cache_save_failed, exc, StageErrorKind.STORAGE) from exc

        if raw_text_hint is None:
            self._shared.reporter.status(report_text.ocr_done(page_count))
            if self._on_ocr_done:
                self._on_ocr_done(str(file.resolve()), file.name)

        return ctx

    def _read_pages(
        self, file: Path, prepared: PreparedInput
    ) -> tuple[str, TextFormat, int, tuple[ScanBox, ...]]:
        """Read the pages with the run's engine; return the text, its format, the page count and
        where the lines of the pages stand in that text."""
        if prepared.text_layer_note:
            self._shared.reporter.status(report_text.pdf_text_layer_note(prepared.text_layer_note))
        self._shared.reporter.status(report_text.RUNNING_OCR)
        self._shared.notify(file, ProgressStage.OCR)

        def page_started(page: int, total: int) -> None:
            if total > 1:
                self._report_ocr_page(page, total)
            self._shared.sampler.begin_sample(SpeedStep.READING)

        def page_done(text: str) -> None:
            self._shared.sampler.finish_sample(words=len(text.split()))

        pending = (
            self._scan_places.begin(prepared.ocr_inputs, self._shared.language)
            if self._scan_places is not None
            else None
        )
        try:
            readings = self._ocr_stage_service.read_pages(
                prepared.ocr_inputs,
                self._shared.language,
                on_page=page_started,
                on_page_done=page_done,
            )
            page_texts = [page.text for page in readings]
            raw_text_format: TextFormat = "plain"
            if self._postprocessor is not None:
                processed = self._postprocessor.process_pages(page_texts)
                raw_text, raw_text_format = processed.text, processed.text_format
            else:
                raw_text = join_labelled_pages(page_texts)
            layout: tuple[ScanBox, ...] = ()
            if pending is not None and raw_text:
                layout = pending.places(
                    readings,
                    raw_text,
                    raw_text_format,
                    name=file.name,
                    on_wait=lambda: self._shared.reporter.status(report_text.PLACING_LINES),
                )
        finally:
            if pending is not None:
                pending.close()
        return raw_text, raw_text_format, len(page_texts), layout
