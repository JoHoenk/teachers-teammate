"""Unit tests for teachers_teammate.infrastructure.workflow.file_processor."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from teachers_teammate.domain.correction_review import (
    ChangeNote,
    CorrectionOutcome,
    CorrectionReview,
    SentenceNote,
)
from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.performance_estimate import SpeedStep
from teachers_teammate.infrastructure.correction import ChunkingCorrector
from teachers_teammate.infrastructure.ollama_utils import ChatStats
from teachers_teammate.infrastructure.run_stats import RunStatsCollector
from teachers_teammate.infrastructure.speed_store import RunSample, SampleKey
from teachers_teammate.infrastructure.state_repository import DocumentState
from teachers_teammate.infrastructure.workflow.cache_service import CacheContext
from teachers_teammate.infrastructure.workflow.preprocess_service import PreparedInput
from teachers_teammate.exceptions import RunStoppedError
from teachers_teammate.infrastructure.workflow.file_processor import (
    STOPPED_BEFORE_PROOFREADING,
    STOPPED_BEFORE_RECOGNITION,
    STOPPED_BEFORE_REVIEW,
    FileProcessor,
    FileProcessorConfig,
)
from teachers_teammate.interfaces import Corrector


# ── Helpers ────────────────────────────────────────────────────────────────


@dataclass
class _StubReturns:
    """Return values for the mocked services built by `_make_processor`."""

    cache_ctx: CacheContext | None = None
    preprocess_return: (
        tuple[list[Path], list[str], Path | None, str | None] | PreparedInput | None
    ) = None
    ocr_return: tuple[list[str], str | None] = field(
        default_factory=lambda: (["extracted text"], None)
    )


def _make_state(
    *,
    ocr_done: bool = False,
    raw_text: str = "",
    correction_done: bool = False,
    correction_text: str = "",
    evaluation_done: bool = False,
    evaluation_text: str = "",
    preview_img: str = "",
    source_image: str = "",
) -> DocumentState:
    return DocumentState(
        schema_version=2,
        source_path="doc.png",
        source_hash="h",
        ocr_done=ocr_done,
        raw_text=raw_text,
        correction_done=correction_done,
        correction_text=correction_text,
        evaluation_done=evaluation_done,
        evaluation_text=evaluation_text,
        preview_img=preview_img,
        source_image=source_image,
    )


def _make_ctx(state: DocumentState) -> CacheContext:
    return CacheContext(
        state=state,
        source_hash="h",
        ocr_prompt="Transcribe.",
        ocr_config_hash="ocr_h",
        correction_config_hash="corr_h",
        eval_config_hash="eval_h",
        preview_config_hash="prev_h",
    )


class _ListReporter:
    """Reporter that records status/warning lines for assertions."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def status(self, line: str) -> None:
        self.lines.append(line.strip())

    def warn(self, line: str) -> None:
        self.lines.append(line.strip())


def _make_processor(
    tmp_path: Path,
    *,
    returns: _StubReturns | None = None,
    corrector: Corrector | MagicMock | None = None,
    evaluator: MagicMock | None = None,
    doc_creator: MagicMock | None = None,
    stop_event: threading.Event | None = None,
    on_ocr_done: MagicMock | None = None,
    **config_extras: Any,
) -> tuple[FileProcessor, MagicMock, MagicMock, MagicMock]:
    returns = returns or _StubReturns()
    cache_service = MagicMock()
    preprocess_service = MagicMock()
    ocr_stage_service = MagicMock()

    default_state = _make_state()
    cache_ctx = returns.cache_ctx or _make_ctx(default_state)
    cache_service.prepare.return_value = cache_ctx
    cache_service.persist_preview_artifact.return_value = ("preview.png", cache_ctx)
    cache_service.record_ocr.return_value = cache_ctx
    cache_service.record_correction.return_value = cache_ctx
    cache_service.record_evaluation.return_value = cache_ctx

    preprocess_return = returns.preprocess_return or (
        [tmp_path / "img.png"],
        [],
        tmp_path / "img.png",
        None,
    )
    if isinstance(preprocess_return, PreparedInput):
        prepared = preprocess_return
    else:
        ocr_inputs, steps, source_image, text_hint = preprocess_return
        prepared = PreparedInput(ocr_inputs, steps, source_image, text_hint, None)
    preprocess_service.preprocess_input.return_value = prepared

    ocr_stage_service.run_pages.return_value = returns.ocr_return

    cfg = FileProcessorConfig(
        output_dir=tmp_path / "output",
        language="English",
        cache_service=cache_service,
        preprocess_service=preprocess_service,
        ocr_stage_service=ocr_stage_service,
        correction=corrector,
        evaluation=evaluator,
        doc_creator=doc_creator or MagicMock(),
        stop_event=stop_event,
        on_ocr_done=on_ocr_done,
        **config_extras,  # on_stage_started, reporter
    )
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    fp = FileProcessor(cfg)
    return fp, cache_service, preprocess_service, ocr_stage_service


# ── Cache hit path ─────────────────────────────────────────────────────────


def test_cache_hit_restoring_preview_keeps_recorded_original_and_steps(tmp_path: Path) -> None:
    """
    Given  cached OCR whose preview image is missing but source_image, original_img and
           preprocess_steps are recorded
    When   process() re-persists the preview from source_image
    Then   the recorded original and steps are passed along instead of being reset
    """
    source_image = tmp_path / "page.png"
    source_image.write_bytes(b"png")
    state = dataclasses.replace(
        _make_state(ocr_done=True, raw_text="cached text", source_image=str(source_image)),
        original_img=str(tmp_path / "doc_original.png"),
        preprocess_steps=["grayscale"],
    )
    ctx = _make_ctx(state)
    file = tmp_path / "doc.png"
    file.write_bytes(b"data")

    fp, cache_svc, _, _ = _make_processor(tmp_path, returns=_StubReturns(cache_ctx=ctx))
    fp.process(file)

    cache_svc.persist_preview_artifact.assert_called_once_with(
        file,
        ctx,
        source_image,
        original_path=tmp_path / "doc_original.png",
        preprocess_steps=["grayscale"],
    )


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_cache_hit_skips_ocr_and_returns_cached_text(tmp_path: Path) -> None:
    """
    Given  a file whose OCR is already cached (ocr_done=True, raw_text non-empty)
    When   process() is called
    Then   OCR stage is skipped and the cached raw_text is returned in the result tuple
    """
    state = _make_state(ocr_done=True, raw_text="cached text", preview_img="p.png")
    ctx = _make_ctx(state)
    file = tmp_path / "doc.png"
    file.write_bytes(b"data")

    fp, _, preprocess_svc, ocr_svc = _make_processor(tmp_path, returns=_StubReturns(cache_ctx=ctx))
    result = fp.process(file)

    assert result.ok is True
    assert result.raw_text == "cached text"
    preprocess_svc.preprocess_input.assert_not_called()
    ocr_svc.run_pages.assert_not_called()


def test_cache_hit_invokes_on_ocr_done_callback(tmp_path: Path) -> None:
    """
    Given  a cached file and an on_ocr_done callback
    When   process() is called
    Then   the callback is invoked with (str(file.resolve()), file.name)
    """
    state = _make_state(ocr_done=True, raw_text="text", preview_img="p.png")
    ctx = _make_ctx(state)
    file = tmp_path / "doc.png"
    file.write_bytes(b"data")
    on_done = MagicMock()

    fp, _, _, _ = _make_processor(
        tmp_path, returns=_StubReturns(cache_ctx=ctx), on_ocr_done=on_done
    )
    fp.process(file)

    on_done.assert_called_once_with(str(file.resolve()), file.name)


# ── Error paths ────────────────────────────────────────────────────────────


def test_oserror_on_prepare_returns_false(tmp_path: Path) -> None:
    """
    Given  cache_service.prepare raises OSError
    When   process() is called
    Then   result is (False, 'Could not read input file: ...', '', '', '', '')
    """
    file = tmp_path / "doc.png"
    fp, cache_svc, _, _ = _make_processor(tmp_path)
    cache_svc.prepare.side_effect = OSError("file not found")

    result = fp.process(file)

    assert result.ok is False
    assert "Could not read input file" in result.message
    assert (
        result.preview_img
        == result.raw_text
        == result.correction_text
        == result.evaluation_text
        == ""
    )


def test_preprocessing_failure_returns_false(tmp_path: Path) -> None:
    """
    Given  preprocess_service.preprocess_input raises an exception
    When   process() is called
    Then   result is (False, 'Preprocessing failed: ...', '', '', '', '')
    """
    file = tmp_path / "doc.png"
    fp, _, preprocess_svc, _ = _make_processor(tmp_path)
    preprocess_svc.preprocess_input.side_effect = RuntimeError("corrupt image")

    result = fp.process(file)

    assert result.ok is False
    assert "Preprocessing failed" in result.message
    assert (
        result.preview_img
        == result.raw_text
        == result.correction_text
        == result.evaluation_text
        == ""
    )


def test_persist_preview_oserror_returns_false(tmp_path: Path) -> None:
    """
    Given  cache_service.persist_preview_artifact raises OSError
    When   process() is called
    Then   result is (False, 'Could not persist preview image: ...', ...)
    """
    file = tmp_path / "doc.png"
    fp, cache_svc, _, _ = _make_processor(tmp_path)
    cache_svc.persist_preview_artifact.side_effect = OSError("disk full")

    result = fp.process(file)

    assert result.ok is False
    assert "Could not persist preview image" in result.message


# ── Stop event ─────────────────────────────────────────────────────────────


def test_stop_event_before_ocr_returns_stopped(tmp_path: Path) -> None:
    """
    Given  stop_event is already set before OCR begins
    When   process() is called
    Then   the file is stopped (not failed) before the text was read and OCR is not run
    """
    file = tmp_path / "doc.png"
    stop = threading.Event()
    stop.set()

    fp, _cache_svc, _, ocr_svc = _make_processor(tmp_path, stop_event=stop)
    result = fp.process(file)

    assert result.stopped is True
    assert result.ok is False
    assert result.message == STOPPED_BEFORE_RECOGNITION
    assert result.raw_text == ""
    assert result.correction_text == ""
    assert result.evaluation_text == ""
    ocr_svc.run_pages.assert_not_called()


def test_stop_during_ocr_request_returns_stopped(tmp_path: Path) -> None:
    """
    Given  the OCR stage raises RunStoppedError because "Stop now" cut the request off
    When   process() is called
    Then   the file is stopped before recognition, with the preview kept, and nothing is stored
    """
    fp, cache_svc, _, ocr_svc = _make_processor(tmp_path)
    ocr_svc.run_pages.side_effect = RunStoppedError

    result = fp.process(tmp_path / "doc.png")

    assert result.stopped is True
    assert result.message == STOPPED_BEFORE_RECOGNITION
    assert result.preview_img == "preview.png"
    cache_svc.record_ocr.assert_not_called()


def test_stop_after_ocr_without_proofreading_still_completes(tmp_path: Path) -> None:
    """
    Given  recognition only (no corrector) and stop_event set while OCR was running
    When   process() is called
    Then   the file is a success: every step the run asked for was done
    """
    file = tmp_path / "doc.png"
    stop = threading.Event()

    def set_stop_after_ocr(*_args: object, **_kwargs: object) -> CacheContext:
        stop.set()
        return _make_ctx(_make_state())

    fp, cache_svc, _, _ = _make_processor(tmp_path, stop_event=stop)
    cache_svc.record_ocr.side_effect = set_stop_after_ocr

    result = fp.process(file)

    assert result.ok is True
    assert result.stopped is False
    assert result.raw_text == "extracted text"


def test_stop_after_ocr_skips_proofreading_and_keeps_the_text(tmp_path: Path) -> None:
    """
    Given  a corrector and stop_event set after OCR finished
    When   process() is called
    Then   the file is stopped before proofreading, carries the stored text, and the corrector
           is never asked
    """
    stop = threading.Event()
    corrector = MagicMock()

    def set_stop_after_ocr(*_args: object, **_kwargs: object) -> CacheContext:
        stop.set()
        return _make_ctx(_make_state())

    fp, cache_svc, _, _ = _make_processor(tmp_path, stop_event=stop, corrector=corrector)
    cache_svc.record_ocr.side_effect = set_stop_after_ocr

    result = fp.process(tmp_path / "doc.png")

    assert result.stopped is True
    assert result.message == STOPPED_BEFORE_PROOFREADING
    assert result.raw_text == "extracted text"
    assert result.correction_text == ""
    corrector.correct.assert_not_called()


def test_a_failed_proofreading_is_a_failure_even_when_stop_was_pressed(tmp_path: Path) -> None:
    """
    Given  a corrector whose call fails for its own reason while "Stop now" was pressed
    When   process() is called
    Then   the failure is not turned into a stop (only RunStoppedError says the stop cut the
           request off), and the unchanged source is not stored as a correction
    """
    stop = threading.Event()
    corrector = MagicMock()

    def correct(*_args: object, **_kwargs: object) -> CorrectionOutcome:
        stop.set()
        return CorrectionOutcome("raw", None, "Correction failed", failed=True)

    corrector.correct.side_effect = correct
    fp, cache_svc, _, _ = _make_processor(tmp_path, stop_event=stop, corrector=corrector)

    result = fp.process(tmp_path / "doc.png")

    assert result.stopped is False
    assert result.correction_warning == "Correction failed"
    cache_svc.record_correction.assert_not_called()


def test_proofreading_raising_run_stopped_is_stopped(tmp_path: Path) -> None:
    """
    Given  a corrector that raises RunStoppedError
    When   process() is called
    Then   the file is stopped before proofreading
    """
    corrector = MagicMock()
    corrector.correct.side_effect = RunStoppedError
    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector)

    result = fp.process(tmp_path / "doc.png")

    assert result.stopped is True
    assert result.raw_text == "extracted text"
    cache_svc.record_correction.assert_not_called()


def test_stop_before_review_keeps_the_stored_proofreading(tmp_path: Path) -> None:
    """
    Given  proofreading finished and was stored, then "Stop now" is pressed
    When   process() reaches the review
    Then   the file is stopped before the review and still carries the proofread text
    """
    stop = threading.Event()
    evaluator = MagicMock()
    corrector = MagicMock()

    def correct(*_args: object, **_kwargs: object) -> CorrectionOutcome:
        stop.set()
        return CorrectionOutcome("proofread")

    corrector.correct.side_effect = correct
    fp, cache_svc, _, _ = _make_processor(
        tmp_path, stop_event=stop, corrector=corrector, evaluator=evaluator
    )
    cache_svc.record_correction.return_value = _make_ctx(
        _make_state(correction_done=True, correction_text="proofread")
    )

    result = fp.process(tmp_path / "doc.png")

    assert result.stopped is True
    assert result.message == STOPPED_BEFORE_REVIEW
    assert result.correction_text == "proofread"
    evaluator.evaluate.assert_not_called()


def test_review_cut_off_by_stop_is_stopped_and_not_stored(tmp_path: Path) -> None:
    """
    Given  the evaluator is cut off by "Stop now" (it raises RunStoppedError)
    When   process() is called
    Then   the file is stopped at the review and no evaluation is stored
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("proofread")
    evaluator = MagicMock()
    evaluator.evaluate.side_effect = RunStoppedError
    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector, evaluator=evaluator)
    cache_svc.record_correction.return_value = _make_ctx(
        _make_state(correction_done=True, correction_text="proofread")
    )

    result = fp.process(tmp_path / "doc.png")

    assert result.stopped is True
    assert result.message == STOPPED_BEFORE_REVIEW
    assert result.stopped_stage == "evaluation"
    cache_svc.record_evaluation.assert_not_called()


def test_a_file_stopped_at_the_review_keeps_the_stored_review_and_warning(
    tmp_path: Path,
) -> None:
    """
    Given  a stored proofreading with a review and a warning, then "Stop now" at the review
    When   process() is called
    Then   the stopped result carries the correction, its review and its warning, so the row
           keeps its badge and explanations
    """
    review = CorrectionReview(
        sentences=(SentenceNote("a", "b", (ChangeNote("a", "b", "grammar", "why"),)),)
    )
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("proofread", review, "only corrections")
    evaluator = MagicMock()
    evaluator.evaluate.side_effect = RunStoppedError
    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector, evaluator=evaluator)
    cache_svc.record_correction.return_value = _make_ctx(
        _make_state(correction_done=True, correction_text="proofread")
    )

    result = fp.process(tmp_path / "doc.png")

    assert result.stopped is True
    assert result.correction_text == "proofread"
    assert result.correction_review == review
    assert result.correction_warning == "only corrections"


def test_a_stop_before_proofreading_does_not_wait_for_the_anonymizer(tmp_path: Path) -> None:
    """
    Given  "Stop now" pressed while the text was being read, and anonymization on
    When   process() is called
    Then   the file is stopped before proofreading without running the (slow) anonymizer
    """
    stop = threading.Event()
    corrector = MagicMock()
    anonymizer = MagicMock()
    fp, _, _, ocr = _make_processor(
        tmp_path, stop_event=stop, corrector=corrector, anonymizer=anonymizer
    )

    def read(*_args: object, **_kwargs: object) -> tuple[list[str], None]:
        stop.set()
        return ["extracted text"], None

    ocr.run_pages.side_effect = read

    result = fp.process(tmp_path / "doc.png")

    assert result.stopped is True
    assert result.stopped_stage == "correction"
    anonymizer.anonymize.assert_not_called()
    corrector.correct.assert_not_called()


def test_a_stop_before_the_review_does_not_anonymize_for_it(tmp_path: Path) -> None:
    """
    Given  "Stop now" pressed while proofreading ran, and anonymization on
    When   process() reaches the review
    Then   the file is stopped before the review and the anonymizer ran only for proofreading
    """
    stop = threading.Event()
    corrector = MagicMock()
    corrector.correct.side_effect = lambda *_a, **_k: (stop.set(), CorrectionOutcome("done"))[1]
    evaluator = MagicMock()
    anonymizer = MagicMock()
    anonymizer.anonymize.side_effect = lambda text: (text, {})
    fp, cache_svc, _, _ = _make_processor(
        tmp_path, stop_event=stop, corrector=corrector, evaluator=evaluator, anonymizer=anonymizer
    )
    cache_svc.record_correction.return_value = _make_ctx(
        _make_state(correction_done=True, correction_text="done")
    )

    result = fp.process(tmp_path / "doc.png")

    assert result.message == STOPPED_BEFORE_REVIEW
    assert anonymizer.anonymize.call_count == 1
    evaluator.evaluate.assert_not_called()


def test_the_anonymizer_pre_pass_is_skipped_when_stopping(tmp_path: Path) -> None:
    """
    Given  no proofreading, anonymization on and "Stop now" pressed while the text was read
    When   process() is called
    Then   the pre-pass that only fills the preview cache does not run
    """
    stop = threading.Event()
    anonymizer = MagicMock()
    fp, _, _, ocr = _make_processor(tmp_path, stop_event=stop, anonymizer=anonymizer)
    ocr.run_pages.side_effect = lambda *_a, **_k: (stop.set(), (["text"], None))[1]

    fp.process(tmp_path / "doc.png")

    anonymizer.anonymize.assert_not_called()


# ── Text input path ────────────────────────────────────────────────────────


@pytest.mark.use_case("TXT_Ingestion_Without_OCR")
def test_text_input_skips_ocr_stage(tmp_path: Path) -> None:
    """
    Given  preprocess_service returns a raw_text_hint (text input, not image)
    When   process() is called
    Then   ocr_stage_service.run_pages is NOT called and the hint text is used
    """
    file = tmp_path / "doc.txt"
    preprocess_return = ([], [], None, "plain text content")
    fp, _, _, ocr_svc = _make_processor(
        tmp_path, returns=_StubReturns(preprocess_return=preprocess_return)
    )

    result = fp.process(file)

    assert result.ok is True
    assert result.raw_text == "plain text content"
    ocr_svc.run_pages.assert_not_called()


@pytest.mark.use_case("Batch_OCR_Processing")
def test_pdf_text_layer_skips_ocr_and_reports_embedded_text(tmp_path: Path) -> None:
    """
    Given  preprocess_service returns the embedded text of a PDF (text_origin="pdf_text_layer")
    When   process() is called
    Then   OCR is not run and the status line says embedded PDF text was used
    """
    reporter = _ListReporter()
    prepared = PreparedInput(
        [], [], tmp_path / "page.png", "typed worksheet text", "pdf_text_layer"
    )
    fp, _, _, ocr_svc = _make_processor(
        tmp_path, returns=_StubReturns(preprocess_return=prepared), reporter=reporter
    )

    result = fp.process(tmp_path / "doc.pdf")

    assert result.ok is True
    assert result.raw_text == "typed worksheet text"
    ocr_svc.run_pages.assert_not_called()
    assert "→ Embedded PDF text used (OCR skipped)" in reporter.lines


@pytest.mark.use_case("Batch_OCR_Processing")
def test_pdf_text_layer_fallback_reports_reason_before_ocr(tmp_path: Path) -> None:
    """
    Given  a PDF whose text layer was rejected (text_layer_note set)
    When   process() is called
    Then   the status line names the reason and OCR runs
    """
    reporter = _ListReporter()
    prepared = PreparedInput(
        [tmp_path / "page.png"],
        [],
        tmp_path / "page.png",
        None,
        None,
        "page 2: contains images (scanned or pasted)",
    )
    fp, _, _, ocr_svc = _make_processor(
        tmp_path,
        returns=_StubReturns(preprocess_return=prepared, ocr_return=(["scanned"], None)),
        reporter=reporter,
    )

    result = fp.process(tmp_path / "doc.pdf")

    assert result.ok is True
    ocr_svc.run_pages.assert_called_once()
    assert "→ PDF page 2: contains images (scanned or pasted) — using OCR" in reporter.lines


# ── Fresh OCR ─────────────────────────────────────────────────────────────


@pytest.mark.use_case("Batch_OCR_Processing")
def test_fresh_ocr_single_page(tmp_path: Path) -> None:
    """
    Given  a fresh file with a single OCR page result
    When   process() is called
    Then   raw_text equals the single-page text without any page prefix
    """
    file = tmp_path / "doc.png"
    fp, _, _, _ = _make_processor(
        tmp_path, returns=_StubReturns(ocr_return=(["hello world"], None))
    )

    result = fp.process(file)

    assert result.ok is True
    assert result.raw_text == "hello world"


@pytest.mark.use_case("Batch_OCR_Processing")
def test_fresh_ocr_multi_page_joins_with_prefix(tmp_path: Path) -> None:
    """
    Given  a fresh file with two OCR pages
    When   process() is called
    Then   raw_text contains 'Page 1:' and 'Page 2:' prefixes
    """
    file = tmp_path / "doc.png"
    fp, _, _, _ = _make_processor(
        tmp_path, returns=_StubReturns(ocr_return=(["first", "second"], None))
    )

    result = fp.process(file)

    assert result.ok is True
    assert "Page 1:" in result.raw_text
    assert "Page 2:" in result.raw_text
    assert "first" in result.raw_text
    assert "second" in result.raw_text


def test_fresh_ocr_error_returns_false(tmp_path: Path) -> None:
    """
    Given  ocr_stage_service.run_pages returns an error string
    When   process() is called
    Then   result is (False, error_msg, preview_img, '', '', '')
    """
    file = tmp_path / "doc.png"
    fp, _, _, _ = _make_processor(
        tmp_path, returns=_StubReturns(ocr_return=([], "OCR model timed out"))
    )

    result = fp.process(file)

    assert result.ok is False
    assert result.message == "OCR model timed out"
    assert result.raw_text == result.correction_text == result.evaluation_text == ""


def test_empty_ocr_response_returns_false(tmp_path: Path) -> None:
    """
    Given  ocr_stage_service.run_pages returns a list with an empty string
    When   process() is called
    Then   result is (False, 'Empty response from OCR model.', ...)
    """
    file = tmp_path / "doc.png"
    fp, _, _, _ = _make_processor(tmp_path, returns=_StubReturns(ocr_return=([""], None)))

    result = fp.process(file)

    assert result.ok is False
    assert result.message == "Empty response from OCR model."


# ── Correction ─────────────────────────────────────────────────────────────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_correction_cache_hit_skips_corrector(tmp_path: Path) -> None:
    """
    Given  correction_done=True and non-empty correction_text in cached state
    When   process() is called
    Then   corrector.correct is NOT called and cached text appears in the result
    """
    state = _make_state(
        ocr_done=True,
        raw_text="raw",
        correction_done=True,
        correction_text="corrected cached",
        preview_img="p.png",
    )
    ctx = _make_ctx(state)
    ctx_after_ocr = _make_ctx(state)

    corrector = MagicMock()
    file = tmp_path / "doc.png"
    fp, cache_svc, _, _ = _make_processor(
        tmp_path, returns=_StubReturns(cache_ctx=ctx), corrector=corrector
    )
    cache_svc.record_ocr.return_value = ctx_after_ocr

    result = fp.process(file)

    assert result.ok is True
    assert result.correction_text == "corrected cached"
    corrector.correct.assert_not_called()


@pytest.mark.use_case("AI_Proofreading_Correction")
def test_fresh_correction_calls_corrector_and_records(tmp_path: Path) -> None:
    """
    Given  correction_done=False and a corrector is provided
    When   process() is called
    Then   corrector.correct is called and the result appears as correction_text
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("freshly corrected")
    file = tmp_path / "doc.png"

    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector)
    updated_ctx = _make_ctx(_make_state(correction_done=True, correction_text="freshly corrected"))
    cache_svc.record_correction.return_value = updated_ctx

    result = fp.process(file)

    assert result.ok is True
    assert result.correction_text == "freshly corrected"
    corrector.correct.assert_called_once()


def test_fallback_warning_is_stored_with_the_correction(tmp_path: Path) -> None:
    """
    Given  a corrector that fell back to corrections only and says why
    When   process() is called
    Then   the warning is shown now and handed to the cache with the correction
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("fixed", None, "no explanations")
    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector)
    cache_svc.record_correction.return_value = _make_ctx(
        _make_state(correction_done=True, correction_text="fixed")
    )

    result = fp.process(tmp_path / "doc.png")

    assert result.correction_warning == "no explanations"
    assert cache_svc.record_correction.call_args.kwargs["correction_warning"] == "no explanations"


def test_cached_correction_replays_its_stored_warning(tmp_path: Path) -> None:
    """
    Given  a cached correction that was produced by the fallback and stored its warning
    When   process() is called again
    Then   the corrector is not asked again and the warning still reaches the result
    """
    state = dataclasses.replace(
        _make_state(
            ocr_done=True,
            raw_text="raw",
            correction_done=True,
            correction_text="fixed",
            preview_img="p.png",
        ),
        correction_warning="no explanations",
    )
    corrector = MagicMock()
    fp, cache_svc, _, _ = _make_processor(
        tmp_path, returns=_StubReturns(cache_ctx=_make_ctx(state)), corrector=corrector
    )
    cache_svc.record_ocr.return_value = _make_ctx(state)

    result = fp.process(tmp_path / "doc.png")

    corrector.correct.assert_not_called()
    assert result.correction_warning == "no explanations"


# ── Evaluation ─────────────────────────────────────────────────────────────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_evaluation_cache_hit_skips_evaluator(tmp_path: Path) -> None:
    """
    Given  evaluation_done=True and non-empty evaluation_text
    When   process() is called with correction and evaluation
    Then   evaluator.evaluate is NOT called
    """
    state = _make_state(
        ocr_done=True,
        raw_text="raw",
        correction_done=True,
        correction_text="corrected",
        evaluation_done=True,
        evaluation_text="eval cached",
        preview_img="p.png",
    )
    ctx = _make_ctx(state)

    evaluator = MagicMock()
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("corrected")
    file = tmp_path / "doc.png"

    fp, _, _, _ = _make_processor(
        tmp_path, returns=_StubReturns(cache_ctx=ctx), corrector=corrector, evaluator=evaluator
    )

    result = fp.process(file)

    assert result.ok is True
    assert result.evaluation_text == "eval cached"
    evaluator.evaluate.assert_not_called()


@pytest.mark.use_case("Automatic_Content_Evaluation")
def test_evaluation_never_sees_real_names_when_anonymizing(tmp_path: Path) -> None:
    """
    Given  anonymization is on and the proofread text contains a real name
    When   process() runs the content review
    Then   the evaluator receives the placeholder text, and the stored review has the
           real name restored
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("Anna Weber wrote this.")
    evaluator = MagicMock()
    evaluator.evaluate.return_value = ("[PERSON_1] writes clearly.", None)
    anonymizer = MagicMock()
    anonymizer.anonymize.side_effect = lambda text: (
        text.replace("Anna Weber", "[PERSON_1]"),
        {"[PERSON_1]": "Anna Weber"},
    )
    anonymizer.restore.side_effect = lambda text, mapping: text.replace(
        "[PERSON_1]", mapping["[PERSON_1]"]
    )
    fp, cache, _, _ = _make_processor(
        tmp_path, corrector=corrector, evaluator=evaluator, anonymizer=anonymizer
    )

    result = fp.process(tmp_path / "doc.png")

    sent_to_evaluator = evaluator.evaluate.call_args.args[0]
    assert "Anna Weber" not in sent_to_evaluator
    assert "[PERSON_1]" in sent_to_evaluator
    assert result.evaluation_text == "Anna Weber writes clearly."
    assert cache.record_evaluation.call_args.kwargs["evaluation_text"] == (
        "Anna Weber writes clearly."
    )


@pytest.mark.use_case("Automatic_Content_Evaluation")
def test_failed_restore_after_evaluation_is_a_clean_failure(tmp_path: Path) -> None:
    """
    Given  anonymization is on and restoring names into the review fails
    When   process() runs the content review
    Then   the file fails with a "PII restore failed" result (keeping the proofread text)
           instead of an unhandled exception
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("Anna Weber wrote this.")
    evaluator = MagicMock()
    evaluator.evaluate.return_value = ("[PERSON_1] writes clearly.", None)
    anonymizer = MagicMock()
    anonymizer.anonymize.side_effect = lambda text: (
        text.replace("Anna Weber", "[PERSON_1]"),
        {"[PERSON_1]": "Anna Weber"},
    )
    restored = iter(["Anna Weber wrote this.", ValueError("bad placeholder")])

    def restore(_text: str, _mapping: dict) -> str:
        outcome = next(restored)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    anonymizer.restore.side_effect = restore
    fp, _, _, _ = _make_processor(
        tmp_path, corrector=corrector, evaluator=evaluator, anonymizer=anonymizer
    )

    result = fp.process(tmp_path / "doc.png")

    assert result.ok is False
    assert "PII restore failed" in result.message
    assert result.correction_text == "Anna Weber wrote this."


@pytest.mark.use_case("Automatic_Content_Evaluation")
def test_evaluation_text_is_unchanged_without_anonymizer(tmp_path: Path) -> None:
    """
    Given  no anonymizer is configured
    When   process() runs the content review
    Then   the evaluator receives the proofread text as it is
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("Anna Weber wrote this.")
    evaluator = MagicMock()
    evaluator.evaluate.return_value = ("fine", None)
    fp, _, _, _ = _make_processor(tmp_path, corrector=corrector, evaluator=evaluator)

    fp.process(tmp_path / "doc.png")

    assert evaluator.evaluate.call_args.args[0] == "Anna Weber wrote this."


@pytest.mark.use_case("Automatic_Content_Evaluation")
def test_fresh_evaluation_calls_evaluator(tmp_path: Path) -> None:
    """
    Given  evaluation_done=False and an evaluator is provided with correction present
    When   process() is called
    Then   evaluator.evaluate is called and its result appears in the tuple
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("corrected text")
    evaluator = MagicMock()
    evaluator.evaluate.return_value = ("quality: high", None)
    file = tmp_path / "doc.png"

    fp, _, _, _ = _make_processor(tmp_path, corrector=corrector, evaluator=evaluator)

    result = fp.process(file)

    assert result.ok is True
    assert result.evaluation_text == "quality: high"
    evaluator.evaluate.assert_called_once()


@pytest.mark.use_case("Automatic_Content_Evaluation")
def test_evaluation_skipped_when_no_correction_text(tmp_path: Path) -> None:
    """
    Given  an evaluator is provided but correction_text ends up empty
    When   process() is called
    Then   evaluator.evaluate is NOT called
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("")  # empty → no correction_text
    evaluator = MagicMock()
    file = tmp_path / "doc.png"

    fp, _, _, _ = _make_processor(tmp_path, corrector=corrector, evaluator=evaluator)
    fp.process(file)

    evaluator.evaluate.assert_not_called()


def test_evaluation_skipped_when_no_evaluator(tmp_path: Path) -> None:
    """
    Given  evaluator=None
    When   process() is called even with correction_text present
    Then   no evaluation is performed and evaluation_text in result is empty
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("corrected")
    file = tmp_path / "doc.png"

    fp, _, _, _ = _make_processor(tmp_path, corrector=corrector, evaluator=None)

    fp2_cfg = FileProcessorConfig(
        output_dir=tmp_path / "output",
        language="English",
        cache_service=fp._cache_service,
        preprocess_service=fp._preprocess_service,
        ocr_stage_service=fp._ocr_stage_service,
        correction=corrector,
        evaluation=None,
        doc_creator=MagicMock(),
    )
    fp2 = FileProcessor(fp2_cfg)
    result = fp2.process(file)

    assert result.ok is True
    assert result.evaluation_text == ""


# ── DOCX ───────────────────────────────────────────────────────────────────


def test_docx_creation_error_returns_false(tmp_path: Path) -> None:
    """
    Given  doc_creator.create raises an exception
    When   process() is called
    Then   result is (False, 'DOCX creation failed: ...', ...)
    """
    doc_creator = MagicMock()
    doc_creator.create.side_effect = RuntimeError("template error")
    file = tmp_path / "doc.png"

    fp, _, _, _ = _make_processor(tmp_path, doc_creator=doc_creator)

    result = fp.process(file)

    assert result.ok is False
    assert "DOCX creation failed" in result.message


def test_record_ocr_oserror_returns_false(tmp_path: Path) -> None:
    """
    Given  cache_service.record_ocr raises OSError while persisting fresh OCR output
    When   process() is called
    Then   result is (False, 'Could not persist OCR cache state: ...', ...)
    """
    file = tmp_path / "doc.png"
    fp, cache_svc, _, _ = _make_processor(tmp_path)
    cache_svc.record_ocr.side_effect = OSError("disk full")

    result = fp.process(file)

    assert result.ok is False
    assert "persist OCR cache state" in result.message


def test_fresh_ocr_invokes_on_ocr_done_callback(tmp_path: Path) -> None:
    """
    Given  a fresh file (no cached OCR) and an on_ocr_done callback
    When   process() runs the OCR stage
    Then   the callback is invoked once with (str(file.resolve()), file.name)
    """
    file = tmp_path / "doc.png"
    on_done = MagicMock()
    fp, _, _, _ = _make_processor(tmp_path, on_ocr_done=on_done)

    fp.process(file)

    on_done.assert_called_once_with(str(file.resolve()), file.name)


def test_correction_record_oserror_is_nonfatal(tmp_path: Path) -> None:
    """
    Given  the corrector succeeds but cache_service.record_correction raises OSError
    When   process() is called
    Then   the file still succeeds (non-fatal) and the corrected text is returned
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("corrected")
    file = tmp_path / "doc.png"
    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector)
    cache_svc.record_correction.side_effect = OSError("disk full")

    result = fp.process(file)

    assert result.ok is True
    assert result.correction_text == "corrected"


def test_evaluation_record_oserror_is_nonfatal(tmp_path: Path) -> None:
    """
    Given  the evaluator succeeds but cache_service.record_evaluation raises OSError
    When   process() is called
    Then   the file still succeeds (non-fatal) and the evaluation text is returned
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("corrected")
    evaluator = MagicMock()
    evaluator.evaluate.return_value = ("quality: good", None)
    file = tmp_path / "doc.png"
    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector, evaluator=evaluator)
    cache_svc.record_evaluation.side_effect = OSError("disk full")

    result = fp.process(file)

    assert result.ok is True
    assert result.evaluation_text == "quality: good"


def test_no_doc_creator_returns_results_cached_message(tmp_path: Path) -> None:
    """
    Given  doc_creator=None
    When   process() is called
    Then   result message says the results were cached without DOCX output
    """
    state = _make_state()
    ctx = _make_ctx(state)

    cache_svc = MagicMock()
    cache_svc.prepare.return_value = ctx
    cache_svc.persist_preview_artifact.return_value = ("p.png", ctx)
    cache_svc.record_ocr.return_value = ctx

    preprocess_svc = MagicMock()
    preprocess_svc.preprocess_input.return_value = PreparedInput(
        [tmp_path / "img.png"], [], tmp_path / "img.png", None, None
    )

    ocr_svc = MagicMock()
    ocr_svc.run_pages.return_value = (["text"], None)

    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    cfg = FileProcessorConfig(
        output_dir=tmp_path / "output",
        language="English",
        cache_service=cache_svc,
        preprocess_service=preprocess_svc,
        ocr_stage_service=ocr_svc,
        correction=None,
        evaluation=None,
        doc_creator=None,
    )
    fp = FileProcessor(cfg)

    file = tmp_path / "doc.png"
    result = fp.process(file)

    assert result.ok is True
    assert result.message == "results cached (no output files)"


# ── Output paths & text files ──────────────────────────────────────────────


def test_docx_keeps_input_subfolder(tmp_path: Path) -> None:
    """
    Given  an input file in a subfolder of the input folder
    When   process() writes the DOCX
    Then   it goes into the same subfolder below the output folder
    """
    input_dir = tmp_path / "in"
    file = input_dir / "class1" / "essay.png"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"png")
    doc_creator = MagicMock()
    fp, _, _, _ = _make_processor(tmp_path, doc_creator=doc_creator, input_dir=input_dir)

    result = fp.process(file)

    assert result.ok is True
    out_path = doc_creator.create.call_args.args[2]
    assert out_path == tmp_path / "output" / "class1" / "essay.docx"
    assert out_path.parent.is_dir()


def test_same_named_files_in_subfolders_do_not_collide(tmp_path: Path) -> None:
    """
    Given  two inputs with the same name in different subfolders and text output on
    When   both are processed
    Then   each text file lands in its own subfolder, so neither replaces the other
    """
    input_dir = tmp_path / "in"
    files = [input_dir / name / "essay.png" for name in ("class1", "class2")]
    for file in files:
        file.parent.mkdir(parents=True)
        file.write_bytes(b"png")
    fp, _, _, _ = _make_processor(tmp_path, input_dir=input_dir, write_text=True)

    for file in files:
        assert fp.process(file).ok is True

    for name in ("class1", "class2"):
        assert (tmp_path / "output" / name / "essay.txt").read_text(encoding="utf-8") == (
            "extracted text"
        )


def test_text_files_plain_and_anonymized(tmp_path: Path) -> None:
    """
    Given  plain and anonymized text output enabled
    When   process() finishes
    Then   name.txt holds the OCR text, name_anonymized.txt the anonymized text,
           and the result message lists every written file
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("[PERSON_1] wrote this", {"[PERSON_1]": "Anna"})
    file = tmp_path / "essay.png"
    fp, _, _, _ = _make_processor(
        tmp_path,
        returns=_StubReturns(ocr_return=(["Anna wrote this"], None)),
        write_text=True,
        text_anonymizer=anonymizer,
    )

    result = fp.process(file)

    assert result.ok is True
    out = tmp_path / "output"
    assert (out / "essay.txt").read_text(encoding="utf-8") == "Anna wrote this"
    assert (out / "essay_anonymized.txt").read_text(encoding="utf-8") == ("[PERSON_1] wrote this")
    anonymizer.anonymize.assert_called_once_with("Anna wrote this")
    assert result.message == "essay.docx, essay.txt, essay_anonymized.txt"


def test_text_file_never_replaces_text_input(tmp_path: Path) -> None:
    """
    Given  a TXT input and text output into the same folder
    When   process() would write essay.txt over the input essay.txt
    Then   the file fails with a message and the input is untouched
    """
    out = tmp_path / "output"
    out.mkdir()
    file = out / "essay.txt"
    file.write_text("original", encoding="utf-8")
    fp, _, _, _ = _make_processor(tmp_path, write_text=True)

    result = fp.process(file)

    assert result.ok is False
    assert "Output files not saved" in result.message
    assert "essay.txt is the input document" in result.message
    assert file.read_text(encoding="utf-8") == "original"


def test_anonymizer_error_fails_the_file(tmp_path: Path) -> None:
    """
    Given  plain and anonymized text output whose anonymizer raises
    When   process() writes the outputs
    Then   the file fails with a text-file message instead of crashing the run, the message
           names the DOCX that was already saved, and no text file is left half-written
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.side_effect = OSError("spaCy model missing")
    fp, _, _, _ = _make_processor(tmp_path, write_text=True, text_anonymizer=anonymizer)

    result = fp.process(tmp_path / "essay.png")

    assert result.ok is False
    assert "spaCy model missing" in result.message
    assert "already saved: essay.docx" in result.message
    assert not (tmp_path / "output" / "essay.txt").exists()


def test_markdown_text_is_written_without_markup_after_anonymizing(tmp_path: Path) -> None:
    """
    Given  OCR text stored as Markdown and plain plus anonymized text output
    When   process() writes the text files
    Then   the anonymizer sees the Markdown as stored and both files hold it without markup
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("**[PERSON_1]** wrote", {})
    ctx = _markdown_ctx(ocr_done=True, raw_text="**Anna** wrote", preview_img="p.png")
    fp, _, _, _ = _make_processor(
        tmp_path,
        returns=_StubReturns(cache_ctx=ctx),
        write_text=True,
        text_anonymizer=anonymizer,
    )

    result = fp.process(tmp_path / "essay.png")

    assert result.ok is True
    anonymizer.anonymize.assert_called_once_with("**Anna** wrote")
    out = tmp_path / "output"
    assert (out / "essay.txt").read_text(encoding="utf-8") == "Anna wrote"
    assert (out / "essay_anonymized.txt").read_text(encoding="utf-8") == "[PERSON_1] wrote"


def test_text_file_where_the_run_reads_inputs_is_refused(tmp_path: Path) -> None:
    """
    Given  an input folder inside the output folder, with a subfolder of the same name
    When   process() would save a text file into the input folder
    Then   the file fails before anything is written, so no input is replaced or created
    """
    input_dir = tmp_path / "output" / "in"
    file = input_dir / "in" / "essay.png"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"png")
    doc_creator = MagicMock()
    fp, _, _, _ = _make_processor(
        tmp_path, doc_creator=doc_creator, input_dir=input_dir, write_text=True
    )

    result = fp.process(file)

    assert result.ok is False
    assert "Output files not saved" in result.message
    assert "output folder outside the input folder" in result.message
    assert not (input_dir / "essay.txt").exists()
    doc_creator.create.assert_not_called()


def test_text_file_below_input_subfolder_is_fine_when_not_recursive(tmp_path: Path) -> None:
    """
    Given  a run that does not read subfolders and text output in a folder below the inputs
    When   process() saves the text file
    Then   it is written, because the next run would not look there
    """
    input_dir = tmp_path
    file = input_dir / "essay.png"
    fp, _, _, _ = _make_processor(tmp_path, input_dir=input_dir, recursive=False, write_text=True)

    result = fp.process(file)

    assert result.ok is True
    assert (tmp_path / "output" / "essay.txt").read_text(encoding="utf-8") == "extracted text"


@pytest.mark.parametrize(
    ("first", "second", "clash"),
    [
        ("essay.png", "essay.pdf", "essay.docx"),
        ("essay.png", "essay_anonymized.png", "essay_anonymized.txt"),
    ],
)
def test_two_documents_writing_the_same_output_file_fail_the_second(
    tmp_path: Path, first: str, second: str, clash: str
) -> None:
    """
    Given  two inputs of one folder whose output files have the same name
    When   both are processed in one run
    Then   the first keeps its file and the second fails naming the clash, writing nothing
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("anonymized", {})
    doc_creator = MagicMock()
    fp, _, _, _ = _make_processor(
        tmp_path,
        doc_creator=doc_creator,
        input_dir=tmp_path / "in",
        write_text=True,
        text_anonymizer=anonymizer,
    )

    ok_first = fp.process(tmp_path / "in" / first)
    created_before = doc_creator.create.call_count
    result = fp.process(tmp_path / "in" / second)

    assert ok_first.ok is True
    assert result.ok is False
    assert f"{clash} would replace the output of {first}" in result.message
    assert doc_creator.create.call_count == created_before


def test_processing_the_same_document_again_is_not_a_collision(tmp_path: Path) -> None:
    """
    Given  a document that was already processed by this processor
    When   it is processed again (a re-run of the same file)
    Then   it may write its output files again
    """
    fp, _, _, _ = _make_processor(tmp_path, input_dir=tmp_path / "in", write_text=True)

    assert fp.process(tmp_path / "in" / "essay.png").ok is True
    assert fp.process(tmp_path / "in" / "essay.png").ok is True


def test_same_text_is_anonymized_once_for_correction_and_text_file(tmp_path: Path) -> None:
    """
    Given  anonymization before correction and an anonymized text file from the same anonymizer
    When   process() runs
    Then   the text is analysed once and both uses get that result
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("fixed")
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("[PERSON_1] wrote", {"[PERSON_1]": "Anna"})
    anonymizer.restore.side_effect = lambda text, _mapping: text
    fp, _, _, _ = _make_processor(
        tmp_path, corrector=corrector, anonymizer=anonymizer, text_anonymizer=anonymizer
    )

    result = fp.process(tmp_path / "essay.png")

    assert result.ok is True
    anonymizer.anonymize.assert_called_once_with("extracted text")
    assert (tmp_path / "output" / "essay_anonymized.txt").read_text(encoding="utf-8") == (
        "[PERSON_1] wrote"
    )


# ── Progress narrative & stage callbacks ───────────────────────────────────


def _image_returns(
    tmp_path: Path, steps: list[str], pages: int = 1, ocr_pages: list[str] | None = None
) -> _StubReturns:
    page_paths = [tmp_path / f"img{i}.png" for i in range(pages)]
    return _StubReturns(
        preprocess_return=(page_paths, steps, tmp_path / "raw.png", None),
        ocr_return=(ocr_pages or ["text"] * pages, None),
    )


def test_process_reports_preprocessing_and_ocr_progress_in_order(tmp_path: Path) -> None:
    """
    Given  a single-page image with grayscale preprocessing
    When   process() is called
    Then   the log announces preprocessing, its completion, OCR start and OCR completion in order
    """
    reporter = _ListReporter()
    fp, *_ = _make_processor(
        tmp_path,
        returns=_image_returns(tmp_path, ["grayscale"]),
        reporter=reporter,
    )

    fp.process(tmp_path / "doc.png")

    assert reporter.lines == [
        "→ Preprocessing...",
        "→ Preprocessing done: grayscale (1 page(s))",
        "→ Running OCR...",
        "→ OCR done (1 page(s))",
    ]


def test_process_reports_preprocessing_skipped_for_method_none(tmp_path: Path) -> None:
    """
    Given  an image input where no preprocessing steps were applied (method none)
    When   process() is called
    Then   the log says preprocessing was skipped instead of staying silent
    """
    reporter = _ListReporter()
    fp, *_ = _make_processor(tmp_path, returns=_image_returns(tmp_path, []), reporter=reporter)

    fp.process(tmp_path / "doc.png")

    assert "→ Preprocessing applied no steps" in reporter.lines
    assert not any(line.startswith("→ Preprocessing done") for line in reporter.lines)


def test_process_text_input_reports_skip_without_preprocessing_start(tmp_path: Path) -> None:
    """
    Given  a .txt input (text hint, no images)
    When   process() is called
    Then   no "Preprocessing..." start line or stage callback is emitted and the log notes
           that preprocessing was skipped for text input
    """
    reporter = _ListReporter()
    stages = MagicMock()
    fp, *_ = _make_processor(
        tmp_path,
        returns=_StubReturns(preprocess_return=([], [], None, "hello")),
        on_stage_started=stages,
        reporter=reporter,
    )

    fp.process(tmp_path / "doc.txt")

    assert "→ Preprocessing..." not in reporter.lines
    assert "→ Preprocessing skipped (text input)" in reporter.lines
    assert not any("Running OCR" in line for line in reporter.lines)
    stages.assert_not_called()


def test_process_notifies_stage_started_for_preprocessing_then_ocr(tmp_path: Path) -> None:
    """
    Given  an image input and an on_stage_started callback
    When   process() is called
    Then   the callback receives (source_id, "preprocessing") then (source_id, "ocr")
    """
    stages = MagicMock()
    fp, *_ = _make_processor(
        tmp_path, returns=_image_returns(tmp_path, ["grayscale"]), on_stage_started=stages
    )
    file = tmp_path / "doc.png"

    fp.process(file)

    source_id = str(file.resolve())
    assert stages.call_args_list == [call(source_id, "preprocessing"), call(source_id, "ocr")]


def test_process_cache_hit_does_not_notify_stage_started(tmp_path: Path) -> None:
    """
    Given  a file whose OCR output is already cached
    When   process() is called
    Then   on_stage_started is never invoked (no preprocessing/OCR runs)
    """
    state = _make_state(ocr_done=True, raw_text="cached", preview_img="p.png")
    stages = MagicMock()
    fp, *_ = _make_processor(
        tmp_path, returns=_StubReturns(cache_ctx=_make_ctx(state)), on_stage_started=stages
    )

    fp.process(tmp_path / "doc.png")

    stages.assert_not_called()


def test_process_multi_page_passes_page_progress_callback(tmp_path: Path) -> None:
    """
    Given  a two-page PDF
    When   process() is called and the OCR service reports each page
    Then   the log contains "OCR page 1/2..." and "OCR page 2/2..."
    """
    reporter = _ListReporter()
    fp, _cache, _prep, ocr_svc = _make_processor(
        tmp_path, returns=_image_returns(tmp_path, ["grayscale"], pages=2), reporter=reporter
    )

    def _run_pages(inputs, _language, on_page=None, on_page_done=None):
        for i in range(len(inputs)):
            if on_page is not None:
                on_page(i + 1, len(inputs))
            if on_page_done is not None:
                on_page_done("page text")
        return ["a", "b"], None

    ocr_svc.run_pages.side_effect = _run_pages

    fp.process(tmp_path / "doc.pdf")

    assert "→ OCR page 1/2..." in reporter.lines
    assert "→ OCR page 2/2..." in reporter.lines
    assert "→ Preprocessing done: grayscale (2 page(s))" in reporter.lines


def test_process_single_page_logs_no_page_number(tmp_path: Path) -> None:
    """
    Given  a single-page image
    When   process() is called and the OCR service reports the page
    Then   no per-page line is logged: "page 1/1" would only be noise
    """
    reporter = _ListReporter()
    fp, _cache, _prep, ocr_svc = _make_processor(
        tmp_path, returns=_image_returns(tmp_path, ["grayscale"]), reporter=reporter
    )

    def _run_pages(inputs, _language, on_page=None, on_page_done=None):
        for i in range(len(inputs)):
            if on_page is not None:
                on_page(i + 1, len(inputs))
            if on_page_done is not None:
                on_page_done("page text")
        return ["a"], None

    ocr_svc.run_pages.side_effect = _run_pages

    fp.process(tmp_path / "doc.png")

    assert not [line for line in reporter.lines if "OCR page" in line]


def test_process_persists_original_when_preprocessing_changed_the_page(tmp_path: Path) -> None:
    """
    Given  an image whose raw page exists and differs from the preprocessed output
    When   process() is called with applied steps
    Then   persist_preview_artifact receives the raw page as original_path and the steps
    """
    raw = tmp_path / "raw.png"
    raw.write_bytes(b"raw")
    fp, cache_svc, *_ = _make_processor(
        tmp_path, returns=_image_returns(tmp_path, ["grayscale", "deskew"])
    )

    fp.process(tmp_path / "doc.png")

    kwargs = cache_svc.persist_preview_artifact.call_args.kwargs
    assert kwargs["original_path"] == raw
    assert kwargs["preprocess_steps"] == ["grayscale", "deskew"]


def test_process_keeps_no_original_when_no_steps_applied(tmp_path: Path) -> None:
    """
    Given  an image input with preprocessing method none (no steps)
    When   process() is called
    Then   no original copy is requested and the recorded steps are an empty list
    """
    (tmp_path / "raw.png").write_bytes(b"raw")
    fp, cache_svc, *_ = _make_processor(tmp_path, returns=_image_returns(tmp_path, []))

    fp.process(tmp_path / "doc.png")

    kwargs = cache_svc.persist_preview_artifact.call_args.kwargs
    assert kwargs["original_path"] is None
    assert kwargs["preprocess_steps"] == []


# ── Text notation (plain / markdown) ──────────────────────────────────────


def _markdown_ctx(**state_kwargs: Any) -> CacheContext:
    """A cache context whose OCR text is stored as Markdown."""
    state = dataclasses.replace(_make_state(**state_kwargs), raw_text_format="markdown")
    return _make_ctx(state)


def test_postprocessed_ocr_text_and_format_are_recorded(tmp_path: Path) -> None:
    """
    Given  a postprocessor that turns the page texts into Markdown
    When   process() runs OCR
    Then   the Markdown is what gets recorded and returned, with its format
    """
    postprocessor = MagicMock()
    postprocessor.process_pages.return_value = MagicMock(text="~~a~~ b", text_format="markdown")
    ctx = _markdown_ctx()
    fp, cache_svc, _, _ = _make_processor(
        tmp_path,
        returns=_StubReturns(cache_ctx=ctx, ocr_return=(["<del>a</del> b"], None)),
        postprocessor=postprocessor,
    )

    result = fp.process(tmp_path / "doc.png")

    assert result.ok is True
    assert result.raw_text == "~~a~~ b"
    assert result.text_format == "markdown"
    postprocessor.process_pages.assert_called_once_with(["<del>a</del> b"])
    record_kwargs = cache_svc.record_ocr.call_args.kwargs
    assert record_kwargs["raw_text"] == "~~a~~ b"
    assert record_kwargs["raw_text_format"] == "markdown"


def test_text_input_is_recorded_as_plain_without_postprocessing(tmp_path: Path) -> None:
    """
    Given  a .txt input (raw_text_hint) and a postprocessor
    When   process() is called
    Then   the postprocessor is not consulted and the text is recorded as plain
    """
    postprocessor = MagicMock()
    fp, cache_svc, _, _ = _make_processor(
        tmp_path,
        returns=_StubReturns(preprocess_return=([], [], None, "**not markdown**")),
        postprocessor=postprocessor,
    )

    result = fp.process(tmp_path / "doc.txt")

    assert result.raw_text == "**not markdown**"
    postprocessor.process_pages.assert_not_called()
    assert cache_svc.record_ocr.call_args.kwargs["raw_text_format"] == "plain"


def test_without_postprocessor_pages_are_joined_as_before(tmp_path: Path) -> None:
    """
    Given  no postprocessor and two OCR pages
    When   process() is called
    Then   the pages are joined with "Page N:" labels and recorded as plain text
    """
    fp, cache_svc, _, _ = _make_processor(
        tmp_path, returns=_StubReturns(ocr_return=(["one", "two"], None))
    )

    result = fp.process(tmp_path / "doc.pdf")

    assert result.raw_text == "Page 1:\none\nPage 2:\ntwo"
    assert cache_svc.record_ocr.call_args.kwargs["raw_text_format"] == "plain"


def test_markdown_text_format_reaches_corrector_evaluator_and_docx(tmp_path: Path) -> None:
    """
    Given  cached OCR text stored as Markdown, a corrector, an evaluator and a doc creator
    When   process() is called
    Then   each of them is told the text is Markdown and the result carries the format
    """
    ctx = _markdown_ctx(ocr_done=True, raw_text="~~a~~ b", preview_img="p.png")
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("~~a~~ b!")
    evaluator = MagicMock()
    evaluator.evaluate.return_value = ("fine", None)
    doc_creator = MagicMock()
    fp, cache_svc, _, _ = _make_processor(
        tmp_path,
        returns=_StubReturns(cache_ctx=ctx),
        corrector=corrector,
        evaluator=evaluator,
        doc_creator=doc_creator,
    )

    result = fp.process(tmp_path / "doc.png")

    assert corrector.correct.call_args.kwargs == {"text_format": "markdown"}
    assert evaluator.evaluate.call_args.kwargs == {"text_format": "markdown"}
    assert doc_creator.create.call_args.kwargs["text_format"] == "markdown"
    assert result.text_format == "markdown"
    assert cache_svc.record_correction.call_args.kwargs == {
        "correction_text": "~~a~~ b!",
        "correction_review": None,
        "correction_warning": "",
    }


def test_plain_text_is_corrected_with_plain_format(tmp_path: Path) -> None:
    """
    Given  OCR text stored as plain text and a corrector
    When   process() is called
    Then   the corrector is told text_format="plain" (so its prompt stays unchanged)
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("fixed")
    fp, _, _, _ = _make_processor(tmp_path, corrector=corrector)

    fp.process(tmp_path / "doc.png")

    assert corrector.correct.call_args.kwargs == {"text_format": "plain"}


def _anonymizing_review_processor(tmp_path: Path, restore):
    """Return a processor whose corrector answers with a review quoting a placeholder name."""
    review = CorrectionReview(
        sentences=(
            SentenceNote(
                "[PERSON_1] have gone.",
                "[PERSON_1] has gone.",
                (ChangeNote("have", "has", "grammar", "[PERSON_1] is third person."),),
            ),
        )
    )
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("[PERSON_1] has gone.", review)
    anonymizer = MagicMock()
    anonymizer.anonymize.side_effect = lambda text: (
        text.replace("Anna Weber", "[PERSON_1]"),
        {"[PERSON_1]": "Anna Weber"},
    )
    anonymizer.restore.side_effect = restore
    return _make_processor(tmp_path, corrector=corrector, anonymizer=anonymizer)


def test_names_are_restored_in_every_text_field_of_the_review(tmp_path: Path) -> None:
    """
    Given  anonymization is on and the review quotes the placeholder in a sentence and a note
    When   process() runs the proofreading
    Then   the review stored in the cache and returned has the real name in all of them
    """
    fp, cache, _, _ = _anonymizing_review_processor(
        tmp_path, lambda text, mapping: text.replace("[PERSON_1]", mapping["[PERSON_1]"])
    )

    result = fp.process(tmp_path / "doc.png")

    assert result.correction_review is not None
    sentence = result.correction_review.sentences[0]
    assert sentence.original == "Anna Weber have gone."
    assert sentence.changes[0].explanation == "Anna Weber is third person."
    stored = cache.record_correction.call_args.kwargs["correction_review"]
    assert stored["sentences"][0]["corrected"] == "Anna Weber has gone."


def test_failed_review_restore_drops_the_review_and_never_logs_the_text(
    tmp_path: Path, caplog
) -> None:
    """
    Given  restoring names into the review fails after the proofread text was restored
    When   process() runs the proofreading
    Then   the file still succeeds without a review, and the log names the file but does not
           contain the student's text
    """
    calls = {"count": 0}

    def restore(text: str, mapping: dict) -> str:
        calls["count"] += 1
        if calls["count"] > 1:
            raise ValueError("bad placeholder")
        return text.replace("[PERSON_1]", mapping["[PERSON_1]"])

    fp, _, _, _ = _anonymizing_review_processor(tmp_path, restore)

    with caplog.at_level("WARNING"):
        result = fp.process(tmp_path / "doc.png")

    assert result.ok is True
    assert result.correction_review is None
    assert result.correction_text == "Anna Weber has gone."
    assert "doc.png" in caplog.text
    assert "Anna Weber" not in caplog.text


def test_failed_correction_is_reported_but_not_stored_as_done(tmp_path: Path) -> None:
    """
    Given  a corrector whose provider call failed and returned the unchanged source
    When   process() is called
    Then   the warning is shown, but nothing is recorded as a finished correction, so the
           next run asks the model again
    """
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome(
        "raw", None, "Correction failed", failed=True
    )
    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector)

    result = fp.process(tmp_path / "doc.png")

    assert result.correction_warning == "Correction failed"
    cache_svc.record_correction.assert_not_called()


def test_anonymizer_runs_without_proofreading(tmp_path: Path) -> None:
    """
    Given  "Remove personal info" on and no corrector at all (recognition only)
    When   process() runs
    Then   the extracted text is still anonymized and the log names how many items were hidden
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("[PERSON_1] wrote", {"[PERSON_1]": "Anna"})
    reporter = _ListReporter()
    fp, *_ = _make_processor(tmp_path, anonymizer=anonymizer, reporter=reporter)

    result = fp.process(tmp_path / "essay.png")

    assert result.ok is True
    anonymizer.anonymize.assert_called_once_with("extracted text")
    assert "→ Personal info removed (1 items)" in reporter.lines
    assert not any("requires" in line.lower() for line in reporter.lines)


def test_anonymized_text_file_reuses_the_pre_pass_result(tmp_path: Path) -> None:
    """
    Given  recognition only, with the same anonymizer for the stage and the text file
    When   process() runs
    Then   the anonymized text file holds the pre-pass result and spaCy ran once
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("[PERSON_1] wrote", {"[PERSON_1]": "Anna"})
    fp, *_ = _make_processor(
        tmp_path, anonymizer=anonymizer, text_anonymizer=anonymizer, write_text=True
    )

    result = fp.process(tmp_path / "essay.png")

    assert result.ok is True
    anonymizer.anonymize.assert_called_once_with("extracted text")
    assert (tmp_path / "output" / "essay_anonymized.txt").read_text(encoding="utf-8") == (
        "[PERSON_1] wrote"
    )


def test_cached_proofreading_does_not_run_the_anonymizer(tmp_path: Path) -> None:
    """
    Given  a corrector and an anonymizer, and a proofreading that is already cached
    When   process() runs
    Then   no spaCy pass happens: nothing is sent, so nothing needs hiding
    """
    anonymizer = MagicMock()
    corrector = MagicMock()
    state = _make_state(ocr_done=True, raw_text="raw", correction_done=True, correction_text="ok")
    fp, *_ = _make_processor(
        tmp_path,
        returns=_StubReturns(cache_ctx=_make_ctx(state)),
        corrector=corrector,
        anonymizer=anonymizer,
    )

    result = fp.process(tmp_path / "essay.png")

    assert result.ok is True
    anonymizer.anonymize.assert_not_called()
    corrector.correct.assert_not_called()


def test_anonymizer_failure_fails_only_this_file(tmp_path: Path) -> None:
    """
    Given  proofreading on and an anonymizer that raises (text over spaCy's length limit)
    When   process() runs
    Then   the file fails with a message instead of the exception ending the run, and
           the corrector never saw the text
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.side_effect = ValueError("[E088] Text of length 2000000 exceeds")
    corrector = MagicMock()
    fp, *_ = _make_processor(tmp_path, corrector=corrector, anonymizer=anonymizer)

    result = fp.process(tmp_path / "essay.png")

    assert result.ok is False
    assert "Personal info removal failed" in result.message
    assert "E088" in result.message
    corrector.correct.assert_not_called()


# ── The pieces counter on the console ─────────────────────────────────────


class _BudgetCorrector(Corrector):
    """Corrector with a fixed request budget, so a text can be made to need several parts."""

    def __init__(self, max_chars: int) -> None:
        self._max_chars = max_chars

    def max_text_chars(self, language: str, *, text_format: TextFormat = "plain") -> int | None:
        """Return the fixed budget."""
        del language, text_format
        return self._max_chars

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the text unchanged."""
        del language, text_format
        return CorrectionOutcome(text=raw_text)


def test_a_text_in_several_parts_is_announced_before_proofreading(tmp_path: Path) -> None:
    """
    Given  a text that needs more than one proofreading request
    When   process() proofreads it
    Then   the console says how many parts, and the count matches the requests made
    """
    reporter = _ListReporter()
    corrector = ChunkingCorrector(_BudgetCorrector(max_chars=100))
    state = _make_state(ocr_done=True, raw_text="Ein Satz. " * 60, preview_img="p.png")
    fp, cache_svc, _, _ = _make_processor(
        tmp_path,
        returns=_StubReturns(cache_ctx=_make_ctx(state)),
        corrector=corrector,
        reporter=reporter,
    )
    cache_svc.record_correction.return_value = _make_ctx(
        _make_state(correction_done=True, correction_text="x")
    )

    fp.process(tmp_path / "doc.png")

    announced = [line for line in reporter.lines if line.startswith("→ Proofreading doc.png in")]
    assert len(announced) == 1
    assert announced[0].endswith("parts...")
    assert announced[0] != "→ Proofreading doc.png in 1 parts..."


def test_a_text_that_goes_out_whole_is_not_announced(tmp_path: Path) -> None:
    """
    Given  a text that fits into one proofreading request
    When   process() proofreads it
    Then   the console says nothing about parts: there is no boundary to warn about
    """
    reporter = _ListReporter()
    corrector = ChunkingCorrector(_BudgetCorrector(max_chars=100_000))
    state = _make_state(ocr_done=True, raw_text="Ein Satz. " * 60, preview_img="p.png")
    fp, cache_svc, _, _ = _make_processor(
        tmp_path,
        returns=_StubReturns(cache_ctx=_make_ctx(state)),
        corrector=corrector,
        reporter=reporter,
    )
    cache_svc.record_correction.return_value = _make_ctx(
        _make_state(correction_done=True, correction_text="x")
    )

    fp.process(tmp_path / "doc.png")

    assert not [line for line in reporter.lines if "Proofreading" in line]


# ── Text recognition switched off ──────────────────────────────────────────


def test_scan_is_skipped_when_recognition_is_off(tmp_path: Path) -> None:
    """
    Given  a processor with recognition off whose preprocess service refuses page images
    When   process() is called for an image
    Then   the result is skipped (not ok, with the reason), no OCR ran and nothing was cached
    """
    from teachers_teammate.exceptions import RecognitionSwitchedOffError  # noqa: PLC0415

    file = tmp_path / "scan.png"
    file.write_bytes(b"data")
    fp, cache_svc, preprocess_svc, ocr_svc = _make_processor(tmp_path, recognize=False)
    preprocess_svc.preprocess_input.side_effect = RecognitionSwitchedOffError(file.name)

    result = fp.process(file)

    assert result.skipped
    assert not result.ok
    assert "switched off" in result.message
    preprocess_svc.preprocess_input.assert_called_once_with(file, allow_images=False)
    ocr_svc.run_pages.assert_not_called()
    cache_svc.record_ocr.assert_not_called()


def test_cached_scan_is_still_processed_when_recognition_is_off(tmp_path: Path) -> None:
    """
    Given  a scan whose recognised text is already stored, and recognition switched off
    When   process() is called
    Then   the stored text is used: nothing needs reading, so the file is not skipped
    """
    state = _make_state(ocr_done=True, raw_text="cached text", preview_img="p.png")
    file = tmp_path / "scan.png"
    file.write_bytes(b"data")
    fp, _, preprocess_svc, _ = _make_processor(
        tmp_path, returns=_StubReturns(cache_ctx=_make_ctx(state)), recognize=False
    )

    result = fp.process(file)

    assert not result.skipped
    assert result.raw_text == "cached text"
    preprocess_svc.preprocess_input.assert_not_called()


# ── What a run measures ────────────────────────────────────────────────────

_URL = "http://127.0.0.1:11434"


def _stats() -> ChatStats:
    """Return the statistics of one complete request: a second reading, a second writing."""
    return ChatStats(
        done_reason="stop",
        prompt_eval_count=1000,
        eval_count=500,
        total_duration=2_000_000_000,
        load_duration=0,
        prompt_eval_duration=1_000_000_000,
        eval_duration=1_000_000_000,
    )


def _collector(samples: list[tuple[SampleKey, RunSample]]) -> RunStatsCollector:
    """Return a collector that keeps its samples in *samples*."""
    return RunStatsCollector(_URL, lambda key, sample: samples.append((key, sample)))


def test_a_cached_correction_gives_no_sample(tmp_path: Path) -> None:
    """
    Given  a cached correction and a collector that would take a sample
    When   process() is called
    Then   nothing is stored, because the step sent no request to measure
    """
    state = _make_state(
        ocr_done=True,
        raw_text="raw",
        correction_done=True,
        correction_text="cached",
        preview_img="p.png",
    )
    samples: list[tuple[SampleKey, RunSample]] = []
    corrector = MagicMock()
    fp, cache_svc, _, _ = _make_processor(
        tmp_path,
        returns=_StubReturns(cache_ctx=_make_ctx(state)),
        corrector=corrector,
        stats=_collector(samples),
    )
    cache_svc.record_ocr.return_value = _make_ctx(state)

    fp.process(tmp_path / "doc.png")

    corrector.correct.assert_not_called()
    assert samples == []


def test_a_fallback_to_text_mode_gives_no_sample(tmp_path: Path) -> None:
    """
    Given  a corrector that asked for a review, got an unusable answer and fell back to text mode
    When   process() is called
    Then   no sample is stored: the failed review requests would be counted into the text-mode
           answer length
    """
    samples: list[tuple[SampleKey, RunSample]] = []
    collector = _collector(samples)
    corrector = MagicMock()

    def correct(_text: str, _language: str, *, text_format: TextFormat = "plain") -> object:
        collector.record("gemma4:e4b", _stats())
        return CorrectionOutcome("fixed", None, "no explanations", fell_back=True)

    corrector.correct.side_effect = correct
    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector, stats=collector)
    cache_svc.record_correction.return_value = _make_ctx(
        _make_state(correction_done=True, correction_text="fixed")
    )

    fp.process(tmp_path / "doc.png")

    assert samples == []


def test_a_text_mode_correction_is_stored_as_text_mode(tmp_path: Path) -> None:
    """
    Given  a corrector configured for corrections only, and a collector
    When   process() is called
    Then   the sample is stored under the text mode, because that is the answer it wrote
    """
    samples: list[tuple[SampleKey, RunSample]] = []
    collector = _collector(samples)
    corrector = MagicMock()

    def correct(_text: str, _language: str, *, text_format: TextFormat = "plain") -> object:
        collector.record("gemma4:e4b", _stats())
        return CorrectionOutcome("fixed", None, None)

    corrector.correct.side_effect = correct
    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector, stats=collector)
    cache_svc.record_correction.return_value = _make_ctx(
        _make_state(correction_done=True, correction_text="fixed")
    )

    fp.process(tmp_path / "doc.png")

    assert [key.mode for key, _sample in samples] == ["text"]
    assert [key.step for key, _sample in samples] == [SpeedStep.PROOFREADING]


def test_a_failed_correction_gives_no_sample(tmp_path: Path) -> None:
    """
    Given  a corrector whose provider call failed
    When   process() is called
    Then   no sample is stored: the request of a failed call measures nothing
    """
    samples: list[tuple[SampleKey, RunSample]] = []
    collector = _collector(samples)
    corrector = MagicMock()

    def correct(_text: str, _language: str, *, text_format: TextFormat = "plain") -> object:
        collector.record("gemma4:e4b", _stats())
        return CorrectionOutcome("raw", None, "boom", failed=True)

    corrector.correct.side_effect = correct
    fp, _cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector, stats=collector)

    fp.process(tmp_path / "doc.png")

    assert samples == []


def test_a_sample_that_cannot_be_saved_does_not_fail_the_file(tmp_path: Path) -> None:
    """
    Given  a collector whose sink fails with an OSError (a full disk)
    When   process() is called
    Then   the file is still processed successfully
    """

    def broken(_key: SampleKey, _sample: RunSample) -> None:
        raise OSError("disk full")

    collector = RunStatsCollector(_URL, broken)
    corrector = MagicMock()

    def correct(_text: str, _language: str, *, text_format: TextFormat = "plain") -> object:
        collector.record("gemma4:e4b", _stats())
        return CorrectionOutcome("fixed", None, None)

    corrector.correct.side_effect = correct
    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector, stats=collector)
    cache_svc.record_correction.return_value = _make_ctx(
        _make_state(correction_done=True, correction_text="fixed")
    )

    result = fp.process(tmp_path / "doc.png")

    assert result.ok
