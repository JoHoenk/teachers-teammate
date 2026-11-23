"""End-to-end scenario tests for the OCR pipeline.

These exercise stages that the other integration modules don't cover:
correction → DOCX (both formats), automatic evaluation, anonymization, the
resume/freshness cache path, and multi-page PDF OCR. LLM-backed stages are
mocked at the stage-builder import sites; the DOCX builder runs for real.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from docx import Document

from teachers_teammate.domain.anonymization import restore
from teachers_teammate.domain.evaluation import EvaluationOutcome
from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.result import CorrectionOutcome
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote, fits
from teachers_teammate.domain.proofreading.word_diff import word_diff
from teachers_teammate.exceptions import OCRError
from teachers_teammate.infrastructure.ocr_formats import plain_text_of
from teachers_teammate.infrastructure.file_discovery import FileDiscovery
from teachers_teammate.infrastructure.pipeline import OCRPipeline
from teachers_teammate.infrastructure.proofreading.chunking import ChunkingCorrector
from teachers_teammate.infrastructure.stage_builder import StageBuilder
from teachers_teammate.infrastructure.state_repository import StateRepository
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from teachers_teammate.interfaces import Corrector
from tests.conftest import (
    make_config,
    pipeline_dependencies,
    reader_store,
    skip_no_reader_models,
)


def _state_for(cfg, source: Path):
    return StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(source)


def _patch_correction(corrector: Corrector | MagicMock):
    """Patch the correction stage so the stage builder returns a double."""
    return (
        patch("teachers_teammate.infrastructure.stage_builder.build_llm", return_value=MagicMock()),
        patch.object(StageBuilder, "build_corrector", return_value=corrector),
    )


# ── correction → DOCX (both formats) ─────────────────────────────────────────


@pytest.mark.use_case("DOCX_Report_Export")
@pytest.mark.parametrize("fmt", ["table", "comments"])
def test_correction_produces_openable_docx(tmp_path: Path, fmt: str) -> None:
    """
    Given  correction enabled and docx_enabled with the given format
    When   OCRPipeline.run() processes a TXT file
    Then   a .docx is written for that file and is openable by python-docx
    """
    cfg = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        docx_enabled=True,
        docx_format=fmt,
    )
    source = cfg.input_dir / "essay.txt"
    source.write_text("teh quik broWn fox", encoding="utf-8")

    corrector = MagicMock()
    corrector.pieces.return_value = None
    corrector.correct.return_value = CorrectionOutcome("the quick brown fox")
    build_llm_patch, corrector_patch = _patch_correction(corrector)

    with build_llm_patch, corrector_patch:
        rc = (
            OCRPipeline(
                cfg,
                FileDiscovery().collect_input_files(cfg),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
        )

    assert rc == 0
    docx_file = cfg.output_dir / "essay.docx"
    assert docx_file.is_file()
    # Opening confirms it is a structurally valid .docx, not just bytes on disk.
    assert Document(str(docx_file)) is not None


# ── automatic evaluation ─────────────────────────────────────────────────────


@pytest.mark.use_case("Automatic_Content_Evaluation")
def test_evaluation_stage_runs_and_persists(tmp_path: Path) -> None:
    """
    Given  correction and evaluation enabled for a batch run
    When   OCRPipeline.run() processes a TXT file
    Then   the evaluator is invoked with the corrected text and the result is cached
    """
    cfg = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        evaluation_enabled=True,
        evaluate_provider="openai",
        docx_enabled=False,
    )
    source = cfg.input_dir / "graded.txt"
    source.write_text("student answer", encoding="utf-8")

    corrector = MagicMock()
    corrector.pieces.return_value = None
    corrector.correct.return_value = CorrectionOutcome("corrected answer")
    evaluator = MagicMock()
    evaluator.evaluate.return_value = EvaluationOutcome("Grade: A. Well done.")
    build_llm_patch, corrector_patch = _patch_correction(corrector)

    with (
        build_llm_patch,
        corrector_patch,
        patch(
            "teachers_teammate.infrastructure.stage_builder.LangChainEvaluator",
            return_value=evaluator,
        ),
    ):
        rc = (
            OCRPipeline(
                cfg,
                FileDiscovery().collect_input_files(cfg),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
        )

    assert rc == 0
    assert evaluator.evaluate.call_args.args[0] == "corrected answer"
    state = _state_for(cfg, source)
    assert state is not None
    assert state.evaluation_done is True
    assert state.evaluation_text == "Grade: A. Well done."


# ── anonymization ────────────────────────────────────────────────────────────


@pytest.mark.use_case("PII_Anonymization_Before_Correction")
def test_anonymizer_runs_before_correction_and_restores(tmp_path: Path) -> None:
    """
    Given  correction + anonymization enabled
    When   OCRPipeline.run() processes a TXT file
    Then   the text is anonymized before correction and the PII is restored afterwards
    """
    cfg = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        anonymization_enabled=True,
        docx_enabled=False,
    )
    source = cfg.input_dir / "letter.txt"
    source.write_text("Dear Alice", encoding="utf-8")

    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("Dear <NAME_0>", {"<NAME_0>": "Alice"})
    anonymizer.restore.return_value = "Dear Alice (corrected)"
    corrector = MagicMock()
    corrector.pieces.return_value = None
    corrector.correct.return_value = CorrectionOutcome("Dear <NAME_0> (corrected)")
    build_llm_patch, corrector_patch = _patch_correction(corrector)

    with (
        build_llm_patch,
        corrector_patch,
        patch(
            "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer",
            return_value=anonymizer,
        ),
    ):
        rc = (
            OCRPipeline(
                cfg,
                FileDiscovery().collect_input_files(cfg),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
        )

    assert rc == 0
    # Correction must see the anonymized text, not the raw PII.
    assert corrector.correct.call_args.args[0] == "Dear <NAME_0>"
    anonymizer.restore.assert_called_once()
    state = _state_for(cfg, source)
    assert state is not None
    assert state.correction_text == "Dear Alice (corrected)"


# ── resume / freshness ───────────────────────────────────────────────────────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_second_run_reuses_cached_correction(tmp_path: Path) -> None:
    """
    Given  a file already corrected and cached by a first run
    When   OCRPipeline.run() runs again with identical config
    Then   the correction LLM is not invoked again (cached output is reused)
    """
    cfg = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        docx_enabled=False,
    )
    source = cfg.input_dir / "doc.txt"
    source.write_text("original text", encoding="utf-8")

    corrector = MagicMock()
    corrector.pieces.return_value = None
    corrector.correct.return_value = CorrectionOutcome("corrected text")
    build_llm_patch, corrector_patch = _patch_correction(corrector)

    with build_llm_patch, corrector_patch:
        assert (
            OCRPipeline(
                cfg,
                FileDiscovery().collect_input_files(cfg),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
            == 0
        )
        assert corrector.correct.call_count == 1
        # Second run with unchanged config should hit the cache.
        assert (
            OCRPipeline(
                cfg,
                FileDiscovery().collect_input_files(cfg),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
            == 0
        )
        assert corrector.correct.call_count == 1


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_changed_correction_config_recomputes(tmp_path: Path) -> None:
    """
    Given  a file corrected and cached with one set of notes for the AI
    When   OCRPipeline.run() runs again with different notes
    Then   the correction stage is re-executed (cache is stale)
    """
    source_text = "original text"
    corrector = MagicMock()
    corrector.pieces.return_value = None
    corrector.correct.return_value = CorrectionOutcome("corrected text")

    cfg1 = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        correction_notes="Fix spelling.",
        docx_enabled=False,
    )
    source = cfg1.input_dir / "doc.txt"
    source.write_text(source_text, encoding="utf-8")

    build_llm_patch, corrector_patch = _patch_correction(corrector)
    with build_llm_patch, corrector_patch:
        assert (
            OCRPipeline(
                cfg1,
                FileDiscovery().collect_input_files(cfg1),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
            == 0
        )
    assert corrector.correct.call_count == 1

    # Same input/output dirs, different notes → stale correction stage.
    cfg2 = make_config(
        tmp_path,
        input_dir=cfg1.input_dir,
        output_dir=cfg1.output_dir,
        correction_enabled=True,
        correction_provider="openai",
        correction_notes="Rewrite formally.",
        docx_enabled=False,
    )
    build_llm_patch2, corrector_patch2 = _patch_correction(corrector)
    with build_llm_patch2, corrector_patch2:
        assert (
            OCRPipeline(
                cfg2,
                FileDiscovery().collect_input_files(cfg2),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
            == 0
        )
    assert corrector.correct.call_count == 2


# ── multi-page PDF OCR (the real built-in reader) ────────────────────────────


@pytest.mark.needs_reader_models
@skip_no_reader_models
@pytest.mark.use_case("Batch_OCR_Processing")
def test_multipage_pdf_ocr_concatenates_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a two-page PDF with legible text on each page
    When   OCRPipeline.run() processes it with the built-in reader and its real models
    Then   it succeeds and the cached raw text is labelled per page and holds the words
    """
    from teachers_teammate.infrastructure.onnx.store import MODELS_DIR_ENV  # noqa: PLC0415

    monkeypatch.setenv(MODELS_DIR_ENV, str(reader_store(tmp_path).root))
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    in_dir = tmp_path / "input"
    in_dir.mkdir()
    pages = []
    for words in ("Hello World", "Second Page"):
        img = Image.new("RGB", (600, 200), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=48)
        draw.text((40, 80), words, fill=(0, 0, 0), font=font)
        pages.append(img)
    pdf_path = in_dir / "scan.pdf"
    pages[0].save(str(pdf_path), "PDF", save_all=True, append_images=pages[1:])

    cfg = make_config(
        tmp_path,
        input_dir=in_dir,
        ocr_engine="ppocr",
        correction_enabled=False,
        docx_enabled=False,
    )
    rc = (
        OCRPipeline(
            cfg,
            FileDiscovery().collect_input_files(cfg),
            dependencies=pipeline_dependencies(tmp_path),
        )
        .run()
        .exit_code
    )

    assert rc == 0
    state = _state_for(cfg, pdf_path)
    assert state is not None
    assert "Page 1:" in state.raw_text
    assert "Page 2:" in state.raw_text
    assert "Hello World" in state.raw_text
    assert "Second Page" in state.raw_text


# ── corrupt inputs ────────────────────────────────────────────────────────────


@pytest.mark.use_case("Batch_OCR_Processing")
def test_pipeline_reports_nonzero_exit_when_all_inputs_corrupt(tmp_path: Path) -> None:
    """
    Given  an input directory containing only a PNG file with corrupt/empty content
    When   OCRPipeline.run() is called with a mocked OCR engine that raises on every image
    Then   the pipeline returns a non-zero exit code
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
    )
    corrupt_png = cfg.input_dir / "corrupt.png"
    corrupt_png.write_bytes(b"not a valid png")

    mock_ocr = MagicMock()
    mock_ocr.process_image.side_effect = OCRError("corrupt image data")

    with patch(
        "teachers_teammate.infrastructure.stage_builder.PpOcrProcessor",
        return_value=mock_ocr,
    ):
        rc = (
            OCRPipeline(
                cfg,
                FileDiscovery().collect_input_files(cfg),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
        )

    assert rc != 0


# ── the stored review of a text with names hidden, in pieces ─────────────────


class _ScriptedReviewCorrector(Corrector):
    """A corrector that answers with a review built from the diff of what it was sent."""

    def __init__(self, budget: int) -> None:
        self._budget = budget
        self.sent: list[str] = []

    def max_text_chars(self, language: str, *, text_format: str = "plain") -> int:
        del language, text_format
        return self._budget

    def correct(
        self, raw_text: str, language: str, *, text_format: str = "plain"
    ) -> CorrectionOutcome:
        del language, text_format
        self.sent.append(raw_text)
        corrected = raw_text.replace("have", "has")
        return CorrectionOutcome(text=corrected, review=_review_of(raw_text, corrected))


def _review_of(raw: str, corrected: str) -> CorrectionReview:
    """Return a review whose edits are the hunks of *raw* → *corrected*, each explained."""
    return CorrectionReview(
        edits=tuple(
            EditNote(
                id=index,
                parts=(EditPart(hunk.raw_start, hunk.raw_end, hunk.original, hunk.corrected),),
                sentences=(),
                category="grammar",
                explanation="Third person singular.",
            )
            for index, hunk in enumerate(word_diff(raw, corrected), start=1)
        )
    )


@pytest.mark.use_case("PII_Anonymization_Before_Correction")
def test_a_text_with_names_hidden_in_pieces_stores_a_review_that_fits(tmp_path: Path) -> None:
    """
    Given  a text with two names, hiding on, and a corrector that takes three pieces
    When   OCRPipeline.run() completes
    Then   the stored review quotes the words at its offsets in the plain stored text
    """
    cfg = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        anonymization_enabled=True,
        docx_enabled=False,
    )
    source = cfg.input_dir / "letter.txt"
    source.write_text(
        "Dear Alice, she have a dog.\nBob have a cat too.\nAlice have a bird as well.\n",
        encoding="utf-8",
    )
    mapping = {"[PERSON_1]": "Alice", "[PERSON_2]": "Bob"}
    anonymizer = MagicMock()
    anonymizer.anonymize.side_effect = lambda text: (
        text.replace("Alice", "[PERSON_1]").replace("Bob", "[PERSON_2]"),
        mapping,
    )
    anonymizer.restore.side_effect = lambda text, table: restore(text, table)
    corrector = ChunkingCorrector(_ScriptedReviewCorrector(budget=40))
    build_llm_patch, corrector_patch = _patch_correction(corrector)

    with (
        build_llm_patch,
        corrector_patch,
        patch(
            "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer",
            return_value=anonymizer,
        ),
    ):
        rc = (
            OCRPipeline(
                cfg,
                FileDiscovery().collect_input_files(cfg),
                dependencies=pipeline_dependencies(tmp_path),
            )
            .run()
            .exit_code
        )

    assert rc == 0
    assert len(corrector.inner.sent) == 3
    state = _state_for(cfg, source)
    assert state is not None
    assert state.correction_review is not None
    review = CorrectionReview.from_dict(state.correction_review)
    assert review is not None
    assert fits(review, plain_text_of(state.raw_text, state.raw_text_format))
    assert [note.original for note in review.edits] == ["have", "have", "have"]
