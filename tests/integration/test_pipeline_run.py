"""Integration tests for teachers_teammate.infrastructure.pipeline.OCRPipeline.

These exercise the composition root end to end: a real ``OCRPipeline`` wired to
real ``StateRepository`` / ``PreviewImageStore`` / cache services, with only the
stage constructors (OCR engine, LLM corrector/evaluator, DOCX builder) patched.
The focus is on observable run() behaviour — return codes, persisted cache
state, generated DOCX files, callback invocation, engine selection, the Ollama
reachability gate, and run_preview_only.

Per-file stage branching is unit-tested directly against ``FileProcessor`` in
``tests/unit/test_file_processor.py``; the OCR-page loop in
``tests/unit/test_ocr_stage_service.py``; and the cache/correction/evaluation
scenarios in ``tests/integration/test_pipeline_scenarios.py``.
"""
# pylint: disable=W0404,W0621,W0613  # reimported — monkeypatch/patch blocks locally reimport the patched symbol / redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.exceptions import OCRError
from teachers_teammate.infrastructure.pipeline import OCRPipeline, collect_files
from teachers_teammate.infrastructure.state_repository import StateRepository
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from tests.conftest import make_config

# ── Helpers ────────────────────────────────────────────────────────────────


def _place_image(input_dir: Path, name: str = "page.png") -> Path:
    """Write a test PNG into *input_dir* and return its path."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    img = Image.new("RGB", (400, 120), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=48)
    draw.text((20, 30), "Hello World", fill=(0, 0, 0), font=font)
    path = input_dir / name
    img.save(path)
    return path


# ── OCRPipeline.run() happy path ───────────────────────────────────────────


@pytest.mark.use_case("Batch_OCR_Processing")
def test_pipeline_run_processes_image_and_persists_ocr_state(tmp_path: Path) -> None:
    """
    Given  one PNG in input_dir and a mocked TesseractOCRProcessor returning "hello world"
    When   OCRPipeline.run() is called
    Then   returns 0 and JSON cache state stores the extracted OCR text
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "hello world"

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    source = cfg.input_dir / "page.png"
    state = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(source)
    assert state is not None
    assert state.raw_text == "hello world"


def test_pipeline_run_returns_0_when_no_input_files(tmp_path: Path) -> None:
    """
    Given  an empty input directory
    When   OCRPipeline.run() is called
    Then   returns 0 (nothing to do is a successful no-op)
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
    )
    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=MagicMock(),
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0


def test_pipeline_run_returns_1_on_ocr_failure(tmp_path: Path) -> None:
    """
    Given  one PNG and a mocked OCR processor that raises OCRError
    When   OCRPipeline.run() is called
    Then   returns 1 (failed file)
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    mock_proc = MagicMock()
    mock_proc.process_image.side_effect = OCRError("model crashed")

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 1


@pytest.mark.use_case("Batch_OCR_Processing")
def test_pipeline_run_processes_multiple_files(tmp_path: Path) -> None:
    """
    Given  three PNG files in input_dir and a mocked OCR processor
    When   OCRPipeline.run() is called
    Then   returns 0 and three state records contain OCR text
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    for name in ("a.png", "b.png", "c.png"):
        _place_image(cfg.input_dir, name)

    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "text"

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    repo = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state")
    assert repo.load(cfg.input_dir / "a.png") is not None
    assert repo.load(cfg.input_dir / "b.png") is not None
    assert repo.load(cfg.input_dir / "c.png") is not None


@pytest.mark.use_case("Selective_Queue_Execution")
def test_pipeline_run_processes_selected_sources_only(tmp_path: Path) -> None:
    """
    Given  three PNG files and selected_source_paths set to a single file path
    When   OCRPipeline.run() is called
    Then   only the selected file is processed and persisted in state
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    for name in ("a.png", "b.png", "c.png"):
        _place_image(cfg.input_dir, name)

    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "text"
    selected_path = str((cfg.input_dir / "b.png").resolve())

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg, selected_source_paths=[selected_path]).run()

    assert rc == 0
    repo = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state")
    assert repo.load(cfg.input_dir / "a.png") is None
    assert repo.load(cfg.input_dir / "b.png") is not None
    assert repo.load(cfg.input_dir / "c.png") is None


@pytest.mark.use_case("Selective_Queue_Execution")
def test_pipeline_selected_sources_disambiguate_duplicate_basenames(tmp_path: Path) -> None:
    """
    Given  two files with the same basename in different subdirectories
    When   OCRPipeline.run() is called with one selected source path
    Then   only that exact source path is processed
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
        recursive=True,
    )
    left = cfg.input_dir / "left"
    right = cfg.input_dir / "right"
    left.mkdir(parents=True, exist_ok=True)
    right.mkdir(parents=True, exist_ok=True)
    left_page = _place_image(left, "page.png")
    right_page = _place_image(right, "page.png")

    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "text"

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg, selected_source_paths=[str(right_page.resolve())]).run()

    assert rc == 0
    repo = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state")
    assert repo.load(left_page) is None
    assert repo.load(right_page) is not None


def test_pipeline_run_returns_1_when_any_file_fails(tmp_path: Path) -> None:
    """
    Given  two files and OCR that fails on the second call
    When   OCRPipeline.run() is called
    Then   returns 1 (at least one failure)
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "first.png")
    _place_image(cfg.input_dir, "second.png")

    mock_proc = MagicMock()
    mock_proc.process_image.side_effect = ["success text", OCRError("fail")]

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 1


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_pipeline_cache_reuses_ocr_when_input_hash_unchanged(tmp_path: Path) -> None:
    """
    Given  one PNG already processed once
    When   OCRPipeline.run() is called again with unchanged input
    Then   cached OCR output is reused and OCR processor is not called
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "page.png")

    first_ocr = MagicMock()
    first_ocr.process_image.return_value = "first-run"
    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=first_ocr,
    ):
        first_rc = OCRPipeline(cfg).run()

    assert first_rc == 0

    second_ocr = MagicMock()
    second_ocr.process_image.side_effect = OCRError("should not be called")
    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=second_ocr,
    ):
        second_rc = OCRPipeline(cfg).run()

    assert second_rc == 0
    second_ocr.process_image.assert_not_called()


@pytest.mark.use_case("Preview_Preprocessing")
def test_pipeline_persists_preview_artifact_for_none_preprocess(tmp_path: Path) -> None:
    """
    Given  preprocess_method='none' and one image input
    When   OCRPipeline.run() completes
    Then   preview image is persisted at the canonical preprocessed artifact path
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "page.png")

    mock_ocr = MagicMock()
    mock_ocr.process_image.return_value = "text"
    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_ocr,
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    state = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(
        cfg.input_dir / "page.png"
    )
    assert state is not None
    assert state.preview_img.endswith("page_preprocessed.png")
    assert Path(state.preview_img).exists()


@pytest.mark.use_case("Stage_Specific_Rerun")
def test_pipeline_reruns_ocr_when_preprocess_method_changes(tmp_path: Path) -> None:
    """
    Given  a file processed once with preprocess_method='none'
    When   preprocess_method changes to 'grayscale' and pipeline runs again
    Then   OCR stage is invalidated and OCR processor is called again
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "page.png")

    first_ocr = MagicMock()
    first_ocr.process_image.return_value = "first"
    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=first_ocr,
    ):
        first_rc = OCRPipeline(cfg).run()

    assert first_rc == 0

    cfg.ocr.preprocess_method = "grayscale"
    second_ocr = MagicMock()
    second_ocr.process_image.return_value = "second"
    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=second_ocr,
    ):
        second_rc = OCRPipeline(cfg).run()

    assert second_rc == 0
    second_ocr.process_image.assert_called_once()


@pytest.mark.use_case("DOCX_Report_Export")
def test_pipeline_keeps_output_dir_docx_only(tmp_path: Path) -> None:
    """
    Given  a normal run with DOCX output enabled
    When   OCRPipeline.run() completes successfully
    Then   output_dir contains only the DOCX file and no preview/preprocess artifacts
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=True,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "page.png")

    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "hello world"

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    output_entries = sorted(path.name for path in cfg.output_dir.iterdir())
    assert output_entries == ["page.docx"]


# ── Callbacks ──────────────────────────────────────────────────────────────


def test_pipeline_callbacks_are_invoked(tmp_path: Path) -> None:
    """
    Given  on_file_started, on_ocr_done, on_file_done callbacks and one image
    When   OCRPipeline.run() is called
    Then   each callback is called once with arguments that include the filename
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "page.png")

    started, ocr_done, file_done = [], [], []

    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "extracted"

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        OCRPipeline(
            cfg,
            on_file_started=lambda _source_id, name, _idx, _total: started.append(name),
            on_ocr_done=lambda _source_id, name: ocr_done.append(name),
            on_file_done=lambda _source_id, name, ok, msg, pi, rt, ct, et: file_done.append(
                (name, ok)
            ),
        ).run()

    assert started == ["page.png"]
    assert ocr_done == ["page.png"]
    assert file_done == [("page.png", True)]


# ── Stop event ─────────────────────────────────────────────────────────────


def test_pipeline_stops_when_stop_event_is_set_before_first_file(tmp_path: Path) -> None:
    """
    Given  a stop_event that is already set and two images in input_dir
    When   OCRPipeline.run() is called
    Then   returns 0 and no OCR is performed
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "first.png")
    _place_image(cfg.input_dir, "second.png")

    stop = threading.Event()
    stop.set()

    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "text"

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg, stop_event=stop).run()

    assert rc == 0
    mock_proc.process_image.assert_not_called()


# ── Correction path ────────────────────────────────────────────────────────


@pytest.mark.use_case("AI_Proofreading_Correction")
def test_pipeline_run_with_correction_persists_correction_state(tmp_path: Path) -> None:
    """
    Given  correction enabled and a mocked corrector returning "corrected text"
    When   OCRPipeline.run() is called
    Then   returns 0 and corrected text is stored in JSON cache state
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=True,
        docx_enabled=False,
        correction_provider="openai",
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    mock_ocr = MagicMock()
    mock_ocr.process_image.return_value = "raw text"

    mock_corrector = MagicMock()
    mock_corrector.correct.return_value = ("corrected text", None)

    with (
        patch(
            "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
            return_value=mock_ocr,
        ),
        patch("teachers_teammate.infrastructure.stage_builder.build_llm", return_value=MagicMock()),
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainCorrector",
            return_value=mock_corrector,
        ),
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    state = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(
        cfg.input_dir / "page.png"
    )
    assert state is not None
    assert state.correction_text == "corrected text"


# ── DOCX path ──────────────────────────────────────────────────────────────


@pytest.mark.use_case("DOCX_Report_Export")
def test_pipeline_run_with_docx_creates_docx_file(tmp_path: Path) -> None:
    """
    Given  DOCX output enabled (docx_enabled=True) and a mocked OCR processor
    When   OCRPipeline.run() is called
    Then   returns 0 and a .docx file is created in output_dir
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=True,
        preprocess_method="none",
        docx_format="table",
    )
    _place_image(cfg.input_dir, "page.png")

    mock_ocr = MagicMock()
    mock_ocr.process_image.return_value = "extracted text"

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_ocr,
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    docx_files = list(cfg.output_dir.glob("*.docx"))
    assert len(docx_files) == 1


# ── Ollama reachability check ──────────────────────────────────────────────


@pytest.mark.use_case("Service_Availability_Check")
def test_pipeline_run_returns_1_when_ollama_unreachable(tmp_path: Path) -> None:
    """
    Given  ocr_engine='ollama' and Ollama refuses the connection
    When   OCRPipeline.run() is called
    Then   returns 1 (Ollama connection check fails before processing begins)
    """
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415
    from teachers_teammate.exceptions import OllamaConnectionError  # noqa: PLC0415

    cfg = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="llama3:latest",
        correction_enabled=False,
        docx_enabled=False,
    )
    _place_image(cfg.input_dir)

    with patch.object(
        OllamaClient,
        "check_model",
        side_effect=OllamaConnectionError("Cannot connect to Ollama at http://127.0.0.1:11434"),
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 1


@pytest.mark.use_case("Service_Availability_Check")
def test_pipeline_run_returns_1_when_ocr_model_not_in_ollama(tmp_path: Path) -> None:
    """
    Given  ocr_engine='ollama', model='my-model', but Ollama only has 'other-model'
    When   OCRPipeline.run() is called
    Then   returns 1 (model not found)
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="my-model",
        correction_enabled=False,
        docx_enabled=False,
    )
    _place_image(cfg.input_dir)

    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415
    from teachers_teammate.exceptions import OllamaConnectionError  # noqa: PLC0415

    with patch.object(
        OllamaClient,
        "check_model",
        side_effect=OllamaConnectionError("Model 'my-model' not found in Ollama"),
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 1


@pytest.mark.use_case("Service_Availability_Check")
def test_pipeline_run_proceeds_when_ollama_model_found(tmp_path: Path) -> None:
    """
    Given  ocr_engine='ollama', model 'my-model' is available, OCR mock returns text
    When   OCRPipeline.run() is called
    Then   returns 0 (connection check passes and file is processed)
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="my-model",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    mock_ocr = MagicMock()
    mock_ocr.process_image.return_value = "text"

    with (
        patch.object(OllamaClient, "check_model"),  # no-op: model found
        patch(
            "teachers_teammate.infrastructure.stage_builder.OllamaOCRProcessor",
            return_value=mock_ocr,
        ),
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0


# ── Engine selection ───────────────────────────────────────────────────────


def test_pipeline_setup_raises_error_for_langchain_without_provider(tmp_path: Path) -> None:
    """
    Given  ocr_engine='langchain' with no ocr_provider set
    When   OCRPipeline.run() is called
    Then   returns 1 (ValueError from _setup_services propagates as failure)
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="langchain",
        ocr_provider="",
        correction_enabled=False,
        docx_enabled=False,
    )
    _place_image(cfg.input_dir)

    rc = OCRPipeline(cfg).run()

    assert rc == 1


@pytest.mark.use_case("Multi_Provider_Stage_Configuration")
def test_pipeline_setup_creates_langchain_ocr_processor(tmp_path: Path) -> None:
    """
    Given  ocr_engine='langchain' with ocr_provider='openai' and a mocked build_llm
    When   OCRPipeline.run() is called
    Then   build_llm is called for the OCR LLM and the file is processed
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="langchain",
        ocr_provider="openai",
        ocr_model="gpt-4o",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    mock_llm = MagicMock()
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = "extracted text"
    mock_llm.__or__ = MagicMock(return_value=mock_chain)

    with patch(
        "teachers_teammate.infrastructure.stage_builder.build_llm", return_value=mock_llm
    ) as mock_build:
        OCRPipeline(cfg).run()

    mock_build.assert_called()


def test_pipeline_setup_creates_paddleocr_processor(tmp_path: Path) -> None:
    """
    Given  ocr_engine='paddleocr' and a mocked PaddleOCRProcessor
    When   OCRPipeline.run() is called
    Then   PaddleOCRProcessor is instantiated and process_image is called
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="paddleocr",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    mock_ocr = MagicMock()
    mock_ocr.process_image.return_value = "paddle text"

    with patch(
        "teachers_teammate.infrastructure.stage_builder.PaddleOCRProcessor", return_value=mock_ocr
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    mock_ocr.process_image.assert_called_once()


# ── run_preview_only ───────────────────────────────────────────────────────


@pytest.mark.use_case("Preview_Preprocessing")
def test_pipeline_run_preview_only_saves_preprocessed_images(tmp_path: Path) -> None:
    """
    Given  one PNG in input_dir and method='grayscale'
    When   OCRPipeline.run_preview_only() is called
    Then   returns 0 and a preprocessed PNG file exists in tmp_dir
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="grayscale",
    )
    _place_image(cfg.input_dir, "scan.png")

    rc = OCRPipeline(cfg).run_preview_only()

    assert rc == 0
    preprocessed = list(resolve_artifact_dir(cfg.output_dir).glob("*_preprocessed.png"))
    assert len(preprocessed) == 1


def test_pipeline_run_preview_only_returns_0_with_no_files(tmp_path: Path) -> None:
    """
    Given  an empty input directory
    When   OCRPipeline.run_preview_only() is called
    Then   returns 0 (nothing to do)
    """
    cfg = make_config(tmp_path, correction_enabled=False, docx_enabled=False)
    rc = OCRPipeline(cfg).run_preview_only()
    assert rc == 0


def test_pipeline_run_preview_only_stopped_by_event(tmp_path: Path) -> None:
    """
    Given  a stop_event that is set before the run and images in input_dir
    When   OCRPipeline.run_preview_only() is called
    Then   returns 0 and no preprocessed files are written
    """
    cfg = make_config(
        tmp_path,
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="grayscale",
    )
    _place_image(cfg.input_dir, "scan.png")

    stop = threading.Event()
    stop.set()

    rc = OCRPipeline(cfg, stop_event=stop).run_preview_only()

    assert rc == 0
    assert list(cfg.output_dir.glob("*_preprocessed.png")) == []


# ── _setup_services hints ─────────────────────────────────────────────────


def test_setup_services_tesseract_with_adaptive_threshold_prints_hint(
    tmp_path: Path,
    capsys,
) -> None:
    """
    Given  ocr_engine='tesseract' and preprocess_method='adaptive_threshold'
    When   OCRPipeline.run() is called
    Then   a HINT about CLAHE preprocessing is printed
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="adaptive_threshold",
    )
    _place_image(cfg.input_dir)
    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "text"
    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        OCRPipeline(cfg).run()
    captured = capsys.readouterr()
    assert "HINT" in captured.out or "HINT" in captured.err or "clahe" in captured.out.lower()


def test_setup_services_paddleocr_with_clahe_prints_hint(
    tmp_path: Path,
    capsys,
) -> None:
    """
    Given  ocr_engine='paddleocr' and preprocess_method='clahe' (binarisation)
    When   OCRPipeline.run() is called
    Then   a HINT about using 'none' or 'grayscale' preprocessing is printed
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="paddleocr",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="clahe",
    )
    _place_image(cfg.input_dir)
    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "paddle text"
    with patch(
        "teachers_teammate.infrastructure.stage_builder.PaddleOCRProcessor", return_value=mock_proc
    ):
        OCRPipeline(cfg).run()
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "HINT" in combined or "grayscale" in combined.lower()


def test_setup_services_evaluation_with_no_correction_warns(
    tmp_path: Path,
    capsys,
) -> None:
    """
    Given  evaluation_enabled=True but correction_enabled=False
    When   OCRPipeline.run() is called
    Then   a WARNING about evaluation requiring correction is printed
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        evaluation_enabled=True,
        evaluate_provider="openai",
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)
    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "text"
    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        OCRPipeline(cfg).run()
    captured = capsys.readouterr()
    assert "WARNING" in captured.err or "evaluation" in (captured.out + captured.err).lower()


# ── _cleanup_tmp ──────────────────────────────────────────────────────────


def test_cleanup_tmp_preserves_files_in_debug_mode(
    tmp_path: Path,
    capsys,
) -> None:
    """
    Given  debug=True and intermediate step files in tmp_dir
    When   OCRPipeline.run() processes a file
    Then   _step*.jpg files are not removed (debug keeps them)
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
        debug=True,
    )
    _place_image(cfg.input_dir)

    # Create a fake step file to verify it is not removed
    resolve_artifact_dir(cfg.output_dir).mkdir(parents=True, exist_ok=True)
    step_file = resolve_artifact_dir(cfg.output_dir) / "page_step0.jpg"
    step_file.write_bytes(b"\xff\xd8\xff")  # dummy JPEG header

    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "text"
    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        OCRPipeline(cfg).run()

    assert step_file.exists()


# ── Ollama model-check warnings (OllamaClient.check_model, via run()) ──────


@pytest.mark.use_case("Service_Availability_Check")
def test_check_ollama_prints_warning_for_non_vision_model(
    tmp_path: Path,
    capsys,
) -> None:
    """
    Given  ocr_engine='ollama', model available, but families contains no clip/vision
    When   OCRPipeline.run() is called
    Then   a WARNING about vision capability is printed to stderr
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="llama3",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    import sys  # noqa: PLC0415

    mock_model = MagicMock()
    mock_model.model = "llama3"
    mock_list_resp = MagicMock()
    mock_list_resp.models = [mock_model]
    mock_show_details = MagicMock()
    mock_show_details.families = ["llama"]
    mock_show_resp = MagicMock()
    mock_show_resp.details = mock_show_details
    mock_ps_resp = MagicMock()
    mock_ps_resp.models = []
    mock_client = MagicMock()
    mock_client.list.return_value = mock_list_resp
    mock_client.show.return_value = mock_show_resp
    mock_client.ps.return_value = mock_ps_resp
    mock_ollama_mod = MagicMock()
    mock_ollama_mod.Client.return_value = mock_client

    mock_ocr = MagicMock()
    mock_ocr.process_image.return_value = "text"

    with (
        patch.dict(sys.modules, {"ollama": mock_ollama_mod}),
        patch(
            "teachers_teammate.infrastructure.stage_builder.OllamaOCRProcessor",
            return_value=mock_ocr,
        ),
    ):
        OCRPipeline(cfg).run()

    captured = capsys.readouterr()
    assert "WARNING" in captured.err or "vision" in captured.err.lower()


def test_check_ollama_warns_when_model_not_loaded(
    tmp_path: Path,
    capsys,
) -> None:
    """
    Given  ocr_engine='ollama', model is available in Ollama but /api/ps shows
           a different model currently loaded in memory
    When   OCRPipeline.run() is called
    Then   a NOTE about slow first request is printed to stderr
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="llava:latest",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    import sys  # noqa: PLC0415

    mock_model = MagicMock()
    mock_model.model = "llava:latest"
    mock_list_resp = MagicMock()
    mock_list_resp.models = [mock_model]
    mock_show_details = MagicMock()
    mock_show_details.families = ["clip"]
    mock_show_resp = MagicMock()
    mock_show_resp.details = mock_show_details
    mock_ps_model = MagicMock()
    mock_ps_model.model = "some-other-model:latest"
    mock_ps_resp = MagicMock()
    mock_ps_resp.models = [mock_ps_model]
    mock_client = MagicMock()
    mock_client.list.return_value = mock_list_resp
    mock_client.show.return_value = mock_show_resp
    mock_client.ps.return_value = mock_ps_resp
    mock_ollama_mod = MagicMock()
    mock_ollama_mod.Client.return_value = mock_client

    mock_ocr = MagicMock()
    mock_ocr.process_image.return_value = "text"

    with (
        patch.dict(sys.modules, {"ollama": mock_ollama_mod}),
        patch(
            "teachers_teammate.infrastructure.stage_builder.OllamaOCRProcessor",
            return_value=mock_ocr,
        ),
    ):
        OCRPipeline(cfg).run()

    captured = capsys.readouterr()
    assert "NOTE" in captured.err or "not currently loaded" in captured.err


def test_check_ollama_no_warning_when_model_already_running(
    tmp_path: Path,
    capsys,
) -> None:
    """
    Given  ocr_engine='ollama', model is available and /api/ps confirms it is
           currently loaded in memory
    When   OCRPipeline.run() is called
    Then   no NOTE about slow first request is printed to stderr
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="llava:latest",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    import sys  # noqa: PLC0415

    mock_model = MagicMock()
    mock_model.model = "llava:latest"
    mock_list_resp = MagicMock()
    mock_list_resp.models = [mock_model]
    mock_show_details = MagicMock()
    mock_show_details.families = ["clip"]
    mock_show_resp = MagicMock()
    mock_show_resp.details = mock_show_details
    mock_ps_model = MagicMock()
    mock_ps_model.model = "llava:latest"  # same model already running
    mock_ps_resp = MagicMock()
    mock_ps_resp.models = [mock_ps_model]
    mock_client = MagicMock()
    mock_client.list.return_value = mock_list_resp
    mock_client.show.return_value = mock_show_resp
    mock_client.ps.return_value = mock_ps_resp
    mock_ollama_mod = MagicMock()
    mock_ollama_mod.Client.return_value = mock_client

    mock_ocr = MagicMock()
    mock_ocr.process_image.return_value = "text"

    with (
        patch.dict(sys.modules, {"ollama": mock_ollama_mod}),
        patch(
            "teachers_teammate.infrastructure.stage_builder.OllamaOCRProcessor",
            return_value=mock_ocr,
        ),
    ):
        OCRPipeline(cfg).run()

    captured = capsys.readouterr()
    assert "not currently loaded" not in captured.err


# ── preprocess_preview ────────────────────────────────────────────────────


def test_preprocess_preview_raises_for_text_only_input(tmp_path: Path) -> None:
    """
    Given  a .txt file (text-only input, no image unit)
    When   preprocess_preview() is called
    Then   a ValueError is raised indicating preview is unavailable for text inputs
    """
    import pytest  # noqa: PLC0415
    from teachers_teammate.infrastructure.pipeline import preprocess_preview  # noqa: PLC0415

    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("some handwriting text")
    with pytest.raises(ValueError, match="image-based"):
        preprocess_preview(txt_file, "none", tmp_path)


# ── _print_banner code paths ──────────────────────────────────────────────


def test_pipeline_banner_printed_with_config_file_path(
    tmp_path: Path,
    capsys,
) -> None:
    """
    Given  config_file is passed to OCRPipeline
    When   OCRPipeline.run() is called
    Then   the config file path appears in the printed banner
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)
    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "text"
    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        OCRPipeline(cfg, config_file=tmp_path / "ocr.toml").run()
    captured = capsys.readouterr()
    assert "ocr.toml" in captured.out


# ── collect_files OSError ─────────────────────────────────────────────────


def test_collect_files_returns_empty_list_on_os_error(tmp_path: Path) -> None:
    """
    Given  input_dir.glob raises OSError (e.g. permission denied)
    When   collect_files is called
    Then   an empty list is returned without raising
    """
    cfg = make_config(tmp_path)

    with patch("pathlib.Path.glob", side_effect=OSError("permission denied")):
        result = collect_files(cfg)
    assert result == []


# ── run_preview_only failed-file listing ─────────────────────────────────


def test_pipeline_run_preview_only_prints_failed_files(
    tmp_path: Path,
    capsys,
) -> None:
    """
    Given  run_preview_only has a file that fails
    When   run_preview_only() completes
    Then   the failed file name is printed in the output
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "broken.png")

    with patch(
        "teachers_teammate.infrastructure.pipeline.OCRPipeline._preview_file",
        side_effect=RuntimeError("broken image"),
    ):
        OCRPipeline(cfg).run_preview_only()

    captured = capsys.readouterr()
    assert "broken.png" in captured.err or "broken.png" in captured.out


# ── Ollama model-check error tolerance (OllamaClient.check_model, via run()) ─


@pytest.mark.use_case("Service_Availability_Check")
def test_check_ollama_request_exception_is_silent(tmp_path: Path) -> None:
    """
    Given  the ollama client's show()/ps() calls raise during the model check
    When   OllamaClient.check_model runs (via OCRPipeline.run())
    Then   the exception is swallowed and the pipeline continues normally
    """
    import sys  # noqa: PLC0415

    cfg = make_config(
        tmp_path,
        ocr_engine="ollama",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
        ocr_model="llava",
    )
    _place_image(cfg.input_dir)

    mock_model = MagicMock()
    mock_model.model = "llava"
    mock_list_resp = MagicMock()
    mock_list_resp.models = [mock_model]
    mock_client = MagicMock()
    mock_client.list.return_value = mock_list_resp
    mock_client.show.side_effect = RuntimeError("connection refused")
    mock_client.ps.side_effect = RuntimeError("connection refused")
    mock_ollama_mod = MagicMock()
    mock_ollama_mod.Client.return_value = mock_client

    mock_ocr = MagicMock()
    mock_ocr.process_image.return_value = "text"

    with (
        patch.dict(sys.modules, {"ollama": mock_ollama_mod}),
        patch(
            "teachers_teammate.infrastructure.stage_builder.OllamaOCRProcessor",
            return_value=mock_ocr,
        ),
    ):
        result = OCRPipeline(cfg).run()

    assert result == 0  # pipeline still succeeded despite the warning being skipped


# ── _preview_file text-input helper ───────────────────────────────────────


def test_preview_file_text_unit_prints_and_returns_true(tmp_path: Path) -> None:
    """
    Given  preprocess_service.preprocess_input signals a text-only input (non-None raw_text_hint)
    When   _preview_file is called
    Then   returns True and prints the skip message
    """
    from teachers_teammate.infrastructure.reporting import StdoutReporter  # noqa: PLC0415
    from teachers_teammate.infrastructure.workflow.preprocess_service import PreprocessService  # noqa: PLC0415

    cfg = make_config(
        tmp_path, ocr_engine="tesseract", correction_enabled=False, docx_enabled=False
    )
    pipeline = OCRPipeline.__new__(OCRPipeline)
    pipeline._config = cfg
    pipeline._reporter = StdoutReporter()

    mock_service = MagicMock(spec=PreprocessService)
    mock_service.preprocess_input.return_value = ([], [], None, "some text")
    result = pipeline._preview_file(tmp_path / "doc.txt", mock_service)

    assert result is True


# ── Batch mixed-type processing (UC1) ────────────────────────────────────


@pytest.mark.use_case("Batch_OCR_Processing")
def test_pipeline_batch_mixed_types_all_processed(tmp_path: Path) -> None:
    """
    Given  a PNG file and a TXT file in the same input directory
    When   OCRPipeline.run() is called
    Then   returns 0 and both source files have state records
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "page.png")
    (cfg.input_dir / "note.txt").write_text("plain text content", encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.process_image.return_value = "ocr text"

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    repo = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state")
    png_state = repo.load(cfg.input_dir / "page.png")
    txt_state = repo.load(cfg.input_dir / "note.txt")
    assert png_state is not None
    assert png_state.raw_text == "ocr text"
    assert txt_state is not None
    assert txt_state.raw_text == "plain text content"


@pytest.mark.use_case("Batch_OCR_Processing")
def test_pipeline_batch_failure_does_not_stop_remaining_files(tmp_path: Path) -> None:
    """
    Given  two PNG files where OCR fails on the first and succeeds on the second
    When   OCRPipeline.run() is called
    Then   returns 1 (failure) AND the second file still has a valid state record
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "first.png")
    _place_image(cfg.input_dir, "second.png")

    mock_proc = MagicMock()
    mock_proc.process_image.side_effect = [OCRError("fail on first"), "success text"]

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 1
    repo = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state")
    second_state = repo.load(cfg.input_dir / "second.png")
    assert second_state is not None
    assert second_state.raw_text == "success text"


# ── Selective queue (UC4) ────────────────────────────────────────────────


@pytest.mark.use_case("Selective_Queue_Execution")
def test_pipeline_selected_source_not_in_input_dir_is_skipped(tmp_path: Path) -> None:
    """
    Given  one PNG in input_dir and selected_source_paths pointing to a non-existent path
    When   OCRPipeline.run() is called
    Then   returns 0 and no OCR is performed (nothing matched the selection)
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    _place_image(cfg.input_dir, "page.png")
    outside_path = str((tmp_path / "elsewhere" / "other.png").resolve())

    mock_proc = MagicMock()

    with patch(
        "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
        return_value=mock_proc,
    ):
        rc = OCRPipeline(cfg, selected_source_paths=[outside_path]).run()

    assert rc == 0
    mock_proc.process_image.assert_not_called()


# ── Config hash isolation (UC6) ──────────────────────────────────────────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_pipeline_ocr_and_correction_not_rerun_when_only_evaluation_config_changes(
    tmp_path: Path,
) -> None:
    """
    Given  a file with OCR, correction, and evaluation all cached
    When   only the evaluate_prompt changes and the pipeline runs again
    Then   OCR and correction are reused from cache; only evaluation is re-run
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=True,
        docx_enabled=False,
        evaluation_enabled=True,
        correction_provider="openai",
        evaluate_provider="openai",
        evaluate_prompt="prompt-v1",
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    first_ocr = MagicMock()
    first_ocr.process_image.return_value = "raw text"
    first_corrector = MagicMock()
    first_corrector.correct.return_value = ("corrected text", None)
    first_evaluator = MagicMock()
    first_evaluator.evaluate.return_value = ("eval-v1", None)

    with (
        patch(
            "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
            return_value=first_ocr,
        ),
        patch("teachers_teammate.infrastructure.stage_builder.build_llm", return_value=MagicMock()),
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainCorrector",
            return_value=first_corrector,
        ),
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainEvaluator",
            return_value=first_evaluator,
        ),
    ):
        first_rc = OCRPipeline(cfg).run()

    assert first_rc == 0

    cfg.evaluate_prompt = "prompt-v2"
    second_ocr = MagicMock()
    second_ocr.process_image.side_effect = OCRError("OCR should be reused from cache")
    second_corrector = MagicMock()
    second_corrector.correct.side_effect = AssertionError("correction should be reused from cache")
    second_evaluator = MagicMock()
    second_evaluator.evaluate.return_value = ("eval-v2", None)

    with (
        patch(
            "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
            return_value=second_ocr,
        ),
        patch("teachers_teammate.infrastructure.stage_builder.build_llm", return_value=MagicMock()),
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainCorrector",
            return_value=second_corrector,
        ),
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainEvaluator",
            return_value=second_evaluator,
        ),
    ):
        second_rc = OCRPipeline(cfg).run()

    assert second_rc == 0
    second_ocr.process_image.assert_not_called()
    second_corrector.correct.assert_not_called()
    second_evaluator.evaluate.assert_called_once()


# ── Multi-provider (UC8) ──────────────────────────────────────────────────


@pytest.mark.use_case("Multi_Provider_Stage_Configuration")
def test_pipeline_run_with_different_providers_per_stage_produces_full_state(
    tmp_path: Path,
) -> None:
    """
    Given  correction_provider='openai' and evaluate_provider='anthropic' with mocked LLMs
    When   OCRPipeline.run() is called with evaluation enabled
    Then   all three stages complete and state.ocr_done, correction_done, evaluation_done are True
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=True,
        docx_enabled=False,
        evaluation_enabled=True,
        correction_provider="openai",
        evaluate_provider="anthropic",
        preprocess_method="none",
    )
    _place_image(cfg.input_dir)

    mock_ocr = MagicMock()
    mock_ocr.process_image.return_value = "raw text"
    mock_corrector = MagicMock()
    mock_corrector.correct.return_value = ("corrected text", None)
    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = ("quality: good", None)

    with (
        patch(
            "teachers_teammate.infrastructure.stage_builder.TesseractOCRProcessor",
            return_value=mock_ocr,
        ),
        patch("teachers_teammate.infrastructure.stage_builder.build_llm", return_value=MagicMock()),
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainCorrector",
            return_value=mock_corrector,
        ),
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainEvaluator",
            return_value=mock_evaluator,
        ),
    ):
        rc = OCRPipeline(cfg).run()

    assert rc == 0
    state = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(
        cfg.input_dir / "page.png"
    )
    assert state is not None
    assert state.ocr_done is True
    assert state.correction_done is True
    assert state.evaluation_done is True
