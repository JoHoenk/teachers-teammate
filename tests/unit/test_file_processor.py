"""Unit tests for teachers_teammate.infrastructure.workflow.file_processor."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from teachers_teammate.infrastructure.state_repository import DocumentState
from teachers_teammate.infrastructure.workflow.cache_service import CacheContext
from teachers_teammate.infrastructure.workflow.file_processor import (
    FileProcessor,
    FileProcessorConfig,
)


# ── Helpers ────────────────────────────────────────────────────────────────


@dataclass
class _StubReturns:
    """Return values for the mocked services built by `_make_processor`."""

    cache_ctx: CacheContext | None = None
    preprocess_return: tuple | None = None
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
    corrector: MagicMock | None = None,
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
    preprocess_service.preprocess_input.return_value = preprocess_return

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
    Then   result is (False, 'Stopped by user.', preview_img, '', '', '')
    """
    file = tmp_path / "doc.png"
    stop = threading.Event()
    stop.set()

    fp, cache_svc, _, _ = _make_processor(tmp_path, stop_event=stop)
    result = fp.process(file)

    assert result.ok is False
    assert result.message == "Stopped by user."
    assert result.raw_text == ""
    assert result.correction_text == ""
    assert result.evaluation_text == ""


def test_stop_event_after_ocr_returns_partial(tmp_path: Path) -> None:
    """
    Given  stop_event is set after OCR completes but before correction
    When   process() is called
    Then   result is (False, 'Stopped by user.', preview_img, raw_text, '', '')
    """
    file = tmp_path / "doc.png"
    stop = threading.Event()

    def set_stop_after_ocr(*_args: object, **_kwargs: object) -> CacheContext:
        stop.set()
        return _make_ctx(_make_state())

    fp, cache_svc, _, _ = _make_processor(tmp_path, stop_event=stop)
    cache_svc.record_ocr.side_effect = set_stop_after_ocr

    result = fp.process(file)

    assert result.ok is False
    assert result.message == "Stopped by user."
    assert result.raw_text == "extracted text"
    assert result.correction_text == ""
    assert result.evaluation_text == ""


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
    corrector.correct.return_value = ("freshly corrected", None)
    file = tmp_path / "doc.png"

    fp, cache_svc, _, _ = _make_processor(tmp_path, corrector=corrector)
    updated_ctx = _make_ctx(_make_state(correction_done=True, correction_text="freshly corrected"))
    cache_svc.record_correction.return_value = updated_ctx

    result = fp.process(file)

    assert result.ok is True
    assert result.correction_text == "freshly corrected"
    corrector.correct.assert_called_once()


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
    corrector.correct.return_value = ("corrected", None)
    file = tmp_path / "doc.png"

    fp, _, _, _ = _make_processor(
        tmp_path, returns=_StubReturns(cache_ctx=ctx), corrector=corrector, evaluator=evaluator
    )

    result = fp.process(file)

    assert result.ok is True
    assert result.evaluation_text == "eval cached"
    evaluator.evaluate.assert_not_called()


@pytest.mark.use_case("Automatic_Content_Evaluation")
def test_fresh_evaluation_calls_evaluator(tmp_path: Path) -> None:
    """
    Given  evaluation_done=False and an evaluator is provided with correction present
    When   process() is called
    Then   evaluator.evaluate is called and its result appears in the tuple
    """
    corrector = MagicMock()
    corrector.correct.return_value = ("corrected text", None)
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
    corrector.correct.return_value = ("", None)  # empty → no correction_text
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
    corrector.correct.return_value = ("corrected", None)
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
    corrector.correct.return_value = ("corrected", None)
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
    corrector.correct.return_value = ("corrected", None)
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
    preprocess_svc.preprocess_input.return_value = (
        [tmp_path / "img.png"],
        [],
        tmp_path / "img.png",
        None,
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
    assert result.message == "results cached (no DOCX output)"


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

    def _run_pages(inputs, _language, on_page=None):
        for i in range(len(inputs)):
            if on_page is not None:
                on_page(i + 1, len(inputs))
        return ["a", "b"], None

    ocr_svc.run_pages.side_effect = _run_pages

    fp.process(tmp_path / "doc.pdf")

    assert "→ OCR page 1/2..." in reporter.lines
    assert "→ OCR page 2/2..." in reporter.lines
    assert "→ Preprocessing done: grayscale (2 page(s))" in reporter.lines


def test_process_single_page_passes_no_page_callback(tmp_path: Path) -> None:
    """
    Given  a single-page image
    When   process() is called
    Then   run_pages receives on_page=None so no per-page noise is logged
    """
    fp, _cache, _prep, ocr_svc = _make_processor(
        tmp_path, returns=_image_returns(tmp_path, ["grayscale"])
    )

    fp.process(tmp_path / "doc.png")

    assert ocr_svc.run_pages.call_args.kwargs == {"on_page": None}


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
