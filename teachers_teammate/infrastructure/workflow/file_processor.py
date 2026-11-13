"""Per-file processing logic for the OCR pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
import logging
from pathlib import Path
import threading
from typing import TYPE_CHECKING

from ...domain.correction_review import CorrectionReview
from ...domain.formatted_text import TextFormat, join_labelled_pages, normalize_text_format
from ...domain.output_files import (
    ANONYMIZED_TEXT_SUFFIX,
    DOCX_SUFFIX,
    TEXT_SUFFIX,
    feeds_inputs,
    output_path,
)
from ...domain.stages import PROGRESS_STAGE_OCR, PROGRESS_STAGE_PREPROCESSING, StageName
from ...exceptions import (
    RECOGNITION_OFF_MESSAGE,
    PipelineInputError,
    RecognitionSwitchedOffError,
    RunStoppedError,
    stop_requested,
)
from ...interfaces import AnonymizationMap, Anonymizer, Corrector, DocumentCreator, Evaluator
from ..correction import proofreading_pieces
from ..reporting import OnOcrDoneFn, OnStageStartedFn, Reporter, StdoutReporter
from .cache_service import CacheContext, CacheReconciliationService
from .ocr_service import OCRStageService
from .preprocess_service import PreparedInput, PreprocessService

if TYPE_CHECKING:
    from ..ocr_formats import OcrOutputPostprocessor

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingResult:
    """Result of processing a single input file through all pipeline stages."""

    ok: bool
    message: str
    preview_img: str
    raw_text: str
    correction_text: str
    evaluation_text: str
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
    """With ``stopped``: the step the stop cut short (``"ocr"``, ``"correction"`` or
    ``"evaluation"``); empty when the file did not get as far as telling."""


#: What a stopped file says, by the step it did not get to.
STOPPED_BEFORE_RECOGNITION = "Stopped before the text was read."
STOPPED_BEFORE_PROOFREADING = "Stopped before proofreading."
STOPPED_BEFORE_REVIEW = "Stopped before the review."
STOPPED_GENERIC = "Stopped by user."


@dataclass(frozen=True)
class _OutputTexts:
    """The three texts one output pass writes: source, proofread and evaluation."""

    raw: str
    corrected: str
    evaluation: str


@dataclass(frozen=True)
class FileProcessorConfig:
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


class _StageAbort(Exception):
    """Raised by stage helpers to short-circuit the pipeline with a ready result."""

    def __init__(self, result: ProcessingResult) -> None:
        super().__init__()
        self.result = result


def _stopped_abort(
    message: str,
    stage: StageName,
    *,
    preview_img: str = "",
    raw_text: str = "",
    correction_text: str = "",
    text_format: TextFormat = "plain",
    correction_review: CorrectionReview | None = None,
    correction_warning: str = "",
) -> _StageAbort:
    """Return the abort that ends a file because the run was stopped.

    It carries only what is already stored (the texts, and the review and warning that belong
    to a stored correction), so the row shows what the next run reuses.
    """
    return _StageAbort(
        ProcessingResult(
            False,
            message,
            preview_img,
            raw_text,
            correction_text,
            "",
            text_format=text_format,
            correction_review=correction_review,
            correction_warning=correction_warning,
            stopped=True,
            stopped_stage=stage,
        )
    )


class _LazyAnonymization:
    """The anonymized extracted text of one file: computed on the first call, then reused."""

    def __init__(self, compute: Callable[[], tuple[str, AnonymizationMap]]) -> None:
        self._compute = compute
        #: ``(text, mapping)`` once it was computed, else ``None``.
        self.result: tuple[str, AnonymizationMap] | None = None

    def __call__(self) -> tuple[str, AnonymizationMap]:
        if self.result is None:
            self.result = self._compute()
        return self.result


class FileProcessor:
    """Executes all pipeline stages for a single input file.

    Receives pre-built services and optional components; returns a
    :class:`ProcessingResult` to the coordinator for dispatch.
    """

    def __init__(self, config: FileProcessorConfig) -> None:
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

    def process(self, file: Path) -> ProcessingResult:
        """Process *file* through all pipeline stages."""
        try:
            ctx = self._prepare_cache(file)
            raw_text, preview_img, source_image, ctx = self._run_preprocess_or_cache(file, ctx)
            text_format = self._text_format(ctx)

            # The extracted text is anonymized once, and only when something asks for it: a
            # proofreading that is not served from the cache.  Without proofreading nothing
            # else would ask, so it runs here to fill the session cache the previews read.
            anonymized = self._lazy_personal_info_removal(raw_text, preview_img)
            if self._correction is None and self._anonymizer is not None and not self._stopping():
                anonymized()

            correction_text, review, ctx, correction_warnings = self._run_correction_or_cache(
                file, raw_text, preview_img, ctx, anonymized
            )
            evaluation_text, eval_warnings = self._run_evaluation_or_cache(
                file,
                raw_text,
                correction_text,
                preview_img,
                ctx,
                stored_review=(review, "; ".join(correction_warnings)),
            )
            all_warnings = tuple(correction_warnings + eval_warnings)
        except RunStoppedError:
            # A cancelled request that no stage turned into a result itself: nothing of that
            # step was stored, and the steps before it already are.
            return ProcessingResult(False, STOPPED_GENERIC, "", "", "", "", stopped=True)
        except _StageAbort as abort:
            return abort.result

        # Only an anonymizer that also writes the anonymized text file can reuse the pre-pass.
        done = anonymized.result
        reused = done[0] if done is not None and self._text_anonymizer is self._anonymizer else None
        result = self._write_outputs(
            file,
            _OutputTexts(raw_text, correction_text, evaluation_text),
            preview_img,
            source_image,
            all_warnings,
            text_format=text_format,
            review=review,
            anonymized_raw=reused,
        )
        return replace(result, correction_warning="; ".join(correction_warnings))

    # ── Stage helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _text_format(ctx: CacheContext) -> TextFormat:
        """Notation of the document's OCR text (and so of its correction)."""
        return normalize_text_format(ctx.state.raw_text_format)

    def _prepare_cache(self, file: Path) -> CacheContext:
        try:
            return self._cache_service.prepare(file)
        except OSError as exc:
            raise _StageAbort(
                ProcessingResult(False, f"Could not read input file: {exc}", "", "", "", "")
            ) from exc

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
                    original_path=kept_original,
                    preprocess_steps=ctx.state.preprocess_steps,
                )
            except OSError:
                pass
        self._reporter.status("       → Using cached OCR output")
        if self._on_ocr_done:
            self._on_ocr_done(str(file.resolve()), file.name)
        return raw_text, preview_img, source_image, ctx

    def _notify_stage(self, file: Path, stage: str) -> None:
        if self._on_stage_started:
            self._on_stage_started(str(file.resolve()), stage)

    def _run_preprocess_and_ocr(
        self, file: Path, ctx: CacheContext
    ) -> tuple[str, str, Path | None, CacheContext]:
        is_text_input = file.suffix.lower() == ".txt"
        if not is_text_input:
            self._reporter.status("       → Preprocessing...")
            self._notify_stage(file, PROGRESS_STAGE_PREPROCESSING)
        try:
            if self._recognize:
                prepared = self._preprocess_service.preprocess_input(file)
            else:
                prepared = self._preprocess_service.preprocess_input(file, allow_images=False)
        except RecognitionSwitchedOffError as exc:
            self._reporter.status(f"       → Skipped: {RECOGNITION_OFF_MESSAGE}")
            raise _StageAbort(
                ProcessingResult(False, str(exc), "", "", "", "", skipped=True)
            ) from exc
        except (OSError, ValueError, RuntimeError, PipelineInputError) as exc:
            raise _StageAbort(
                ProcessingResult(False, f"Preprocessing failed: {exc}", "", "", "", "")
            ) from exc

        ocr_inputs, prep_steps, source_image = (
            prepared.ocr_inputs,
            prepared.steps,
            prepared.source_image,
        )
        preview_img = ""
        preview_source = ocr_inputs[0] if ocr_inputs else source_image
        if preview_source is not None:
            # Keep the un-preprocessed page too, but only when preprocessing changed it.
            original_path = (
                source_image
                if prep_steps
                and source_image is not None
                and source_image != preview_source
                and source_image.exists()
                else None
            )
            try:
                preview_img, ctx = self._cache_service.persist_preview_artifact(
                    file,
                    ctx,
                    preview_source,
                    original_path=original_path,
                    preprocess_steps=prep_steps if ocr_inputs else None,
                )
            except OSError as exc:
                raise _StageAbort(
                    ProcessingResult(
                        False, f"Could not persist preview image: {exc}", "", "", "", ""
                    )
                ) from exc

        if not ocr_inputs:
            self._reporter.status("       → Preprocessing skipped (text input)")
        elif not prep_steps:
            self._reporter.status("       → Preprocessing applied no steps")
        else:
            self._reporter.status(
                f"       → Preprocessing done: {', '.join(prep_steps)} ({len(ocr_inputs)} page(s))"
            )

        if self._stopping() and prepared.raw_text_hint is None:
            raise _stopped_abort(STOPPED_BEFORE_RECOGNITION, "ocr", preview_img=preview_img)

        try:
            raw_text, ctx = self._run_ocr(
                file, ocr_inputs, prepared, preview_img, source_image, ctx
            )
        except RunStoppedError as exc:
            raise _stopped_abort(
                STOPPED_BEFORE_RECOGNITION, "ocr", preview_img=preview_img
            ) from exc
        return raw_text, preview_img, source_image, ctx

    def _stopping(self) -> bool:
        """Whether the teacher pressed "Stop now" (the other Stop only keeps new files away)."""
        return stop_requested(self._stop_event)

    def _report_ocr_page(self, page: int, total: int) -> None:
        self._reporter.status(f"       → OCR page {page}/{total}...")

    def _run_ocr(
        self,
        file: Path,
        ocr_inputs: list[Path],
        prepared: PreparedInput,
        preview_img: str,
        source_image: Path | None,
        ctx: CacheContext,
    ) -> tuple[str, CacheContext]:
        raw_text_hint = prepared.raw_text_hint
        page_count = 0
        raw_text_format: TextFormat = "plain"
        if raw_text_hint is not None:
            raw_text = raw_text_hint
            if prepared.text_origin == "pdf_text_layer":
                self._reporter.status("       → Embedded PDF text used (OCR skipped)")
            else:
                self._reporter.status("       → Text input loaded (OCR skipped)")
        else:
            if prepared.text_layer_note:
                self._reporter.status(f"       → PDF {prepared.text_layer_note} — using OCR")
            self._reporter.status("       → Running OCR...")
            self._notify_stage(file, PROGRESS_STAGE_OCR)
            on_page = self._report_ocr_page if len(ocr_inputs) > 1 else None
            page_texts, ocr_err = self._ocr_stage_service.run_pages(
                ocr_inputs, self._language, on_page=on_page
            )
            if ocr_err:
                raise _StageAbort(ProcessingResult(False, ocr_err, preview_img, "", "", ""))
            page_count = len(page_texts)
            if self._postprocessor is not None:
                processed = self._postprocessor.process_pages(page_texts)
                raw_text = processed.text
                raw_text_format = processed.text_format
            else:
                raw_text = join_labelled_pages(page_texts)

        if not raw_text:
            raise _StageAbort(
                ProcessingResult(False, "Empty response from OCR model.", preview_img, "", "", "")
            )

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
            raise _StageAbort(
                ProcessingResult(
                    False, f"Could not persist OCR cache state: {exc}", preview_img, "", "", ""
                )
            ) from exc

        if raw_text_hint is None:
            self._reporter.status(f"       → OCR done ({page_count} page(s))")
            if self._on_ocr_done:
                self._on_ocr_done(str(file.resolve()), file.name)

        return raw_text, ctx

    def _lazy_personal_info_removal(self, text: str, preview_img: str) -> _LazyAnonymization:
        """Return a callable that hides personal data in the extracted *text* once.

        Nothing runs until it is called.  A failing anonymizer (spaCy missing, a text over its
        length limit, …) ends this file with a message instead of the whole run.
        """

        def remove() -> tuple[str, AnonymizationMap]:
            if self._anonymizer is None:
                return text, {}
            try:
                anonymized, anon_map = self._anonymizer.anonymize(text)
            except Exception as exc:  # spaCy errors vary; report them per file
                raise _StageAbort(
                    ProcessingResult(
                        False,
                        f"Personal info removal failed: {exc}",
                        preview_img,
                        text,
                        "",
                        "",
                    )
                ) from exc
            self._reporter.status(f"       → Personal info removed ({len(anon_map)} items)")
            return anonymized, anon_map

        return _LazyAnonymization(remove)

    def _anonymize_for(self, stage: str, text: str) -> tuple[str, AnonymizationMap]:
        """Hide personal data in *text* before an LLM *stage* sees it (no-op without anonymiser)."""
        if self._anonymizer is None:
            return text, {}
        anonymized, anon_map = self._anonymizer.anonymize(text)
        self._reporter.status(f"       → PII anonymized before {stage}")
        return anonymized, anon_map

    def _restore_pii(
        self,
        text: str,
        anon_map: AnonymizationMap,
        *,
        preview_img: str,
        raw_text: str,
        correction_text: str = "",
    ) -> str:
        """Put the real personal data back into *text*; abort the file when that fails."""
        if not (anon_map and text and self._anonymizer is not None):
            return text
        try:
            return self._anonymizer.restore(text, anon_map)
        except (ValueError, KeyError) as exc:
            raise _StageAbort(
                ProcessingResult(
                    False,
                    f"PII restore failed: {exc}",
                    preview_img,
                    raw_text,
                    correction_text,
                    "",
                )
            ) from exc

    def _run_correction_or_cache(
        self,
        file: Path,
        raw_text: str,
        preview_img: str,
        ctx: CacheContext,
        anonymized: _LazyAnonymization,
    ) -> tuple[str, CorrectionReview | None, CacheContext, list[str]]:
        if self._correction is None:
            return "", None, ctx, []

        cache_state = ctx.state
        if cache_state.correction_done and cache_state.correction_text:
            self._reporter.status("       → Using cached correction output")
            review = CorrectionReview.from_dict(cache_state.correction_review)
            warnings = [cache_state.correction_warning] if cache_state.correction_warning else []
            return cache_state.correction_text, review, ctx, warnings

        text_format = self._text_format(ctx)

        def stopped() -> _StageAbort:
            return _stopped_abort(
                STOPPED_BEFORE_PROOFREADING,
                "correction",
                preview_img=preview_img,
                raw_text=raw_text,
                text_format=text_format,
            )

        # Before the anonymization: it is a full spaCy pass that a stop should not wait for.
        if self._stopping():
            raise stopped()
        self._reporter.status("       → Running correction...")
        text_for_correction, anon_map = anonymized()

        # A text that goes out in several pieces cannot be changed across a piece boundary;
        # say so before the requests start, so the teacher knows what to expect.
        pieces, _max_chars = proofreading_pieces(
            self._correction, text_for_correction, self._language, text_format=text_format
        )
        if pieces is not None and pieces > 1:
            self._reporter.status(f"       → Proofreading {file.name} in {pieces} parts...")
        if self._stopping():  # the anonymization may have taken a while
            raise stopped()
        try:
            outcome = self._correction.correct(
                text_for_correction, self._language, text_format=text_format
            )
        except RunStoppedError as exc:
            raise stopped() from exc
        corrected_text = self._restore_pii(
            outcome.text or "", anon_map, preview_img=preview_img, raw_text=raw_text
        )
        review = self._restore_review_pii(outcome.review, anon_map, file.name)
        corr_warnings: list[str] = [outcome.warning] if outcome.warning else []

        # A failed call returns the unchanged source; caching it would present that as a finished
        # correction and the next run would never try again.  (A corrections-only fallback
        # *is* stored: the model answered, and asking it for explanations again would repeat
        # the failed requests.)
        if corrected_text and not outcome.failed:
            try:
                ctx = self._cache_service.record_correction(
                    file,
                    ctx,
                    correction_text=corrected_text,
                    correction_review=review.to_dict() if review is not None else None,
                    correction_warning=outcome.warning or "",
                )
            except OSError as exc:
                _logger.warning("Could not save correction state for %s: %s", file.name, exc)

        return corrected_text, review, ctx, corr_warnings

    def _restore_review_pii(
        self,
        review: CorrectionReview | None,
        anon_map: AnonymizationMap,
        file_name: str,
    ) -> CorrectionReview | None:
        """Put the real personal data back into every text field of *review*.

        The corrector answers about the anonymized text, so anchors and explanations quote
        placeholders.  Failure is not fatal here: a review that cannot be restored is dropped,
        which shows the plain diff and keeps the correction itself.
        """
        anonymizer = self._anonymizer
        if review is None or not anon_map or anonymizer is None:
            return review
        try:
            return review.map_text(lambda value: anonymizer.restore(value, anon_map))
        except (ValueError, KeyError) as exc:
            _logger.warning("Could not restore PII in the review of %s: %s", file_name, exc)
            return None

    def _run_evaluation_or_cache(
        self,
        file: Path,
        raw_text: str,
        correction_text: str,
        preview_img: str,
        ctx: CacheContext,
        *,
        stored_review: tuple[CorrectionReview | None, str] = (None, ""),
    ) -> tuple[str, list[str]]:
        """Run the content review, or return the cached one.

        *stored_review* is the ``(review, warning)`` of the proofreading; a file stopped here
        hands them back with the stored correction, so its row keeps them.
        """
        if self._evaluation is None or not correction_text:
            return "", []

        cache_state = ctx.state
        if cache_state.evaluation_done and cache_state.evaluation_text:
            self._reporter.status("       → Using cached evaluation output")
            return cache_state.evaluation_text, []

        stored = ctx.state.correction_done
        review, review_warning = stored_review

        def stopped() -> _StageAbort:
            return _stopped_abort(
                STOPPED_BEFORE_REVIEW,
                "evaluation",
                preview_img=preview_img,
                raw_text=raw_text,
                correction_text=correction_text if stored else "",
                text_format=self._text_format(ctx),
                correction_review=review if stored else None,
                correction_warning=review_warning if stored else "",
            )

        # Before the anonymization: it is a spaCy pass that a stop should not wait for.
        if self._stopping():
            raise stopped()
        self._reporter.status("       → Running evaluation...")
        # The proofread text has its real names back, so hide them again: the review may
        # run on an online service just like proofreading.
        text_for_evaluation, anon_map = self._anonymize_for("evaluation", correction_text)
        if self._stopping():
            raise stopped()
        try:
            eval_text, eval_warning = self._evaluation.evaluate(
                text_for_evaluation, self._language, text_format=self._text_format(ctx)
            )
        except RunStoppedError as exc:
            raise stopped() from exc
        eval_text = self._restore_pii(
            eval_text or "",
            anon_map,
            preview_img=preview_img,
            raw_text=raw_text,
            correction_text=correction_text,
        )
        eval_warnings: list[str] = [eval_warning] if eval_warning else []
        if eval_text:
            try:
                self._cache_service.record_evaluation(file, ctx, evaluation_text=eval_text)
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
                raise ValueError(f"{target.name} is the input document and would be overwritten")
            if self._input_dir is not None and feeds_inputs(
                target.parent.resolve(), self._input_dir, recursive=self._recursive
            ):
                raise ValueError(
                    f"{self._shown(target)} would be read as an input document by the next run; "
                    "choose an output folder outside the input folder."
                )
        with self._claim_lock:
            for target in targets.values():
                owner = self._claimed.get(target.resolve())
                if owner is not None and owner != source:
                    raise ValueError(
                        f"{self._shown(target)} would replace the output of {owner.name}; "
                        "rename one of the two documents."
                    )
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
        def failed(message: str) -> ProcessingResult:
            return ProcessingResult(
                False,
                message,
                preview_img,
                texts.raw,
                texts.corrected,
                texts.evaluation,
                text_format=text_format,
                correction_review=review,
            )

        targets = self._planned_outputs(file)
        try:
            self._claim_outputs(file, targets)
        except ValueError as exc:
            return failed(f"Output files not saved: {exc}")

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
        except Exception as exc:  # noqa: BLE001  # python-docx / PIL errors vary widely; surface message without crashing
            return failed(f"DOCX creation failed: {exc}")
        try:
            self._write_text_files(targets, texts.raw, text_format, saved, anonymized_raw)
        except Exception as exc:  # noqa: BLE001  # NER / file-system errors vary; surface message without crashing
            already = f" (already saved: {', '.join(saved)})" if saved else ""
            return failed(f"Text file creation failed: {exc}{already}")
        return ProcessingResult(
            True,
            ", ".join(saved) or "results cached (no output files)",
            preview_img,
            texts.raw,
            texts.corrected,
            texts.evaluation,
            warnings,
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
            from ..ocr_formats import plain_text_of  # noqa: PLC0415

            jobs = [(target, plain_text_of(text, text_format)) for target, text in jobs]
        for target, text in jobs:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            saved.append(self._shown(target))
