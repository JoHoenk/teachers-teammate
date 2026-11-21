"""End-to-end tests for the teacher's work in the pipeline.

What the teacher changes lives in its own store, apart from the machine results in the cache.
A run reads it where it exists — the transcription it proofreads, the corrections it writes out
— and never writes it.  These tests run the real pipeline over the same cache two or three
times and check what the corrector receives, what the cache stores and what the outputs carry.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
import shutil
from unittest.mock import MagicMock, patch

from docx import Document
import pytest

from teachers_teammate.domain.evaluation import EvaluationOutcome
from teachers_teammate.domain.proofreading.result import CorrectionOutcome
from teachers_teammate.domain.teacher_work import (
    TeacherWork,
    current_texts,
    text_identity,
    with_checked,
    with_corrected,
    with_looked_at,
    with_transcription,
)
from teachers_teammate.infrastructure import teacher_work_store
from teachers_teammate.infrastructure.file_discovery import FileDiscovery
from teachers_teammate.infrastructure.pipeline import OCRPipeline
from teachers_teammate.infrastructure.stage_builder import StageBuilder
from teachers_teammate.infrastructure.state_repository import (
    DocumentState,
    StateRepository,
    compute_file_hash,
    machine_texts,
)
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from teachers_teammate.infrastructure.teacher_work_store import TeacherWorkStore
from tests.conftest import make_config


# ── Helpers ────────────────────────────────────────────────────────────────


def _state_for(cfg, source: Path) -> DocumentState | None:
    return StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(source)


def _machine(cfg, source: Path):
    state = _state_for(cfg, source)
    assert state is not None
    return machine_texts(state)


def _change(source: Path, change: Callable[[TeacherWork], TeacherWork]) -> TeacherWork:
    """Apply *change* to the work of *source*, as the window's commands do."""
    return TeacherWorkStore().update(compute_file_hash(source), source.name, change)


def _fix_transcription(cfg, source: Path, text: str) -> None:
    machine = _machine(cfg, source)
    _change(source, lambda work: with_transcription(work, text, machine.raw_text_format, machine))


def _fix_corrected(cfg, source: Path, text: str) -> None:
    machine = _machine(cfg, source)

    def change(work: TeacherWork) -> TeacherWork:
        return with_corrected(work, text, current_texts(machine, work))

    _change(source, change)


def _check(cfg, source: Path, checked: bool = True) -> None:
    machine = _machine(cfg, source)

    def change(work: TeacherWork) -> TeacherWork:
        return with_checked(work, checked, current_texts(machine, work))

    _change(source, change)


def _look_at(cfg, source: Path, keys: list[str]) -> None:
    machine = _machine(cfg, source)

    def change(work: TeacherWork) -> TeacherWork:
        return with_looked_at(work, keys, current_texts(machine, work))

    _change(source, change)


def _run(cfg, *, corrector: MagicMock | None = None, evaluator: MagicMock | None = None) -> int:
    """Run the pipeline with the LLM stages replaced by doubles."""
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "teachers_teammate.infrastructure.stage_builder.build_llm",
                return_value=MagicMock(),
            )
        )
        if corrector is not None:
            stack.enter_context(
                patch.object(StageBuilder, "build_corrector", return_value=corrector)
            )
        if evaluator is not None:
            stack.enter_context(
                patch(
                    "teachers_teammate.infrastructure.stage_builder.LangChainEvaluator",
                    return_value=evaluator,
                )
            )
        return OCRPipeline(cfg, FileDiscovery().collect_input_files(cfg)).run().exit_code


def _docx_text(path: Path) -> str:
    """Return every text of a Word file, in the body and in the tables."""
    document = Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.extend(paragraph.text for paragraph in cell.paragraphs)
    return "\n".join(parts)


def _proofreading_config(tmp_path: Path, **overrides: object):
    """Return a config that proofreads, with *overrides* applied on top."""
    settings: dict[str, object] = {
        "correction_enabled": True,
        "correction_provider": "openai",
        "docx_enabled": False,
    }
    settings.update(overrides)
    return make_config(tmp_path, **settings)


# ── The transcription is what is proofread ─────────────────────────────────


@pytest.mark.use_case("AI_Proofreading_Correction")
def test_a_proofreading_is_made_from_the_teachers_transcription(tmp_path: Path) -> None:
    """
    Given  a run that only reads the texts, then the teacher fixes both transcriptions and
           marks one of them as checked
    When   the next run proofreads
    Then   the corrector receives the teacher's texts, the stored proofreading remembers the
           transcription it was made from, and the check of the transcription stays
    """
    reading = make_config(tmp_path, correction_enabled=False, docx_enabled=False)
    first = reading.input_dir / "first.txt"
    first.write_text("teh first essay", encoding="utf-8")
    second = reading.input_dir / "second.txt"
    second.write_text("teh second essay", encoding="utf-8")
    assert _run(reading) == 0

    _fix_transcription(reading, first, "the first essay")
    _fix_transcription(reading, second, "the second essay")
    _check(reading, second)

    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("proofread")
    assert _run(_proofreading_config(tmp_path), corrector=corrector) == 0

    assert sorted(call.args[0] for call in corrector.correct.call_args_list) == [
        "the first essay",
        "the second essay",
    ]
    state = _state_for(_proofreading_config(tmp_path), first)
    assert state is not None
    assert state.correction_source_hash == text_identity("the first essay")

    work = TeacherWorkStore().load(compute_file_hash(second))
    assert work is not None
    assert work.transcription is not None
    assert work.transcription.checked is True
    # A new proofreading starts unchecked: the check named the transcription, not the answer.
    assert work.corrections is None


# ── The reading is frozen while the teacher's transcription is there ───────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_a_changed_reading_setting_does_not_read_a_fixed_transcription_again(
    tmp_path: Path,
) -> None:
    """
    Given  a proofread document whose transcription the teacher fixed, and one they did not
    When   a run with a changed reading setting comes
    Then   the fixed one is not read again and keeps its proofreading, while the other is read
           again and proofread again
    """
    reading = make_config(tmp_path, correction_enabled=False, docx_enabled=False)
    fixed = reading.input_dir / "fixed.txt"
    fixed.write_text("teh fixed essay", encoding="utf-8")
    plain = reading.input_dir / "plain.txt"
    plain.write_text("teh plain essay", encoding="utf-8")
    assert _run(reading) == 0

    _fix_transcription(reading, fixed, "the fixed essay")
    frozen_hash = _state_for(reading, fixed).ocr_config_hash

    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("proofread")
    assert _run(_proofreading_config(tmp_path), corrector=corrector) == 0
    assert corrector.correct.call_count == 2

    corrector.reset_mock()
    reread = _proofreading_config(tmp_path, preprocess_method="grayscale")
    assert _run(reread, corrector=corrector) == 0

    assert corrector.correct.call_count == 1
    assert corrector.correct.call_args.args[0] == "teh plain essay"
    assert _state_for(reread, fixed).ocr_config_hash == frozen_hash
    assert _state_for(reread, plain).ocr_config_hash != frozen_hash


# ── Worked-on corrections are kept by default ──────────────────────────────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_a_changed_proofreading_setting_keeps_corrections_the_teacher_worked_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a proofread document whose corrections the teacher changed, and one they only
           looked at
    When   a run with changed proofreading settings comes
    Then   the changed one keeps its proofreading — the corrector is not asked again and the
           run says so — while the other is proofread again
    """
    first = _proofreading_config(tmp_path)
    worked = first.input_dir / "worked.txt"
    worked.write_text("teh worked essay", encoding="utf-8")
    looked = first.input_dir / "looked.txt"
    looked.write_text("teh looked essay", encoding="utf-8")

    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("proofread")
    assert _run(first, corrector=corrector) == 0

    _fix_corrected(first, worked, "the worked essay, proofread")
    _look_at(first, looked, ["edit:1"])

    corrector.reset_mock()
    capsys.readouterr()
    changed = _proofreading_config(tmp_path, correction_model="other-model")
    assert _run(changed, corrector=corrector) == 0

    assert corrector.correct.call_count == 1
    assert corrector.correct.call_args.args[0] == "teh looked essay"
    assert (
        "Proofreading kept: you worked on it (made with other settings)" in capsys.readouterr().out
    )
    assert _state_for(changed, worked).correction_text == "proofread"


# ── A proofreading remembers the transcription it was made from ────────────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_a_proofreading_made_from_a_transcription_that_is_gone_is_stale(
    tmp_path: Path,
) -> None:
    """
    Given  a proofreading made from the teacher's transcription, and then the work folder is
           emptied
    When   the next run comes
    Then   the stored proofreading is stale and the text is proofread again from the reading
    """
    reading = make_config(tmp_path, correction_enabled=False, docx_enabled=False)
    source = reading.input_dir / "essay.txt"
    source.write_text("teh essay", encoding="utf-8")
    assert _run(reading) == 0
    _fix_transcription(reading, source, "the essay")

    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("proofread")
    assert _run(_proofreading_config(tmp_path), corrector=corrector) == 0
    assert corrector.correct.call_args.args[0] == "the essay"
    assert _state_for(_proofreading_config(tmp_path), source).correction_source_hash == (
        text_identity("the essay")
    )

    shutil.rmtree(teacher_work_store.default_work_root())

    corrector.reset_mock()
    assert _run(_proofreading_config(tmp_path), corrector=corrector) == 0
    assert corrector.correct.call_args.args[0] == "teh essay"


# ── Outputs are snapshots of the current texts ─────────────────────────────


@pytest.mark.use_case("DOCX_Report_Export")
def test_the_outputs_carry_the_teachers_texts(tmp_path: Path) -> None:
    """
    Given  a proofread document whose transcription and corrections the teacher changed
    When   the next run writes the outputs
    Then   the Word file and the text file carry the teacher's texts, and the content review
           is made from their corrected text
    """
    reading = make_config(tmp_path, correction_enabled=False, docx_enabled=False)
    source = reading.input_dir / "essay.txt"
    source.write_text("teh essay", encoding="utf-8")
    assert _run(reading) == 0
    _fix_transcription(reading, source, "the essay, as read")

    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("the essay")
    assert _run(_proofreading_config(tmp_path), corrector=corrector) == 0
    _fix_corrected(_proofreading_config(tmp_path), source, "the essay, corrected by the teacher")

    corrector.reset_mock()
    evaluator = MagicMock()
    evaluator.evaluate.return_value = EvaluationOutcome("Grade: A")
    written = _proofreading_config(
        tmp_path,
        docx_enabled=True,
        text_enabled=True,
        evaluation_enabled=True,
        evaluate_provider="openai",
        evaluate_prompt="Another prompt",
    )
    assert _run(written, corrector=corrector, evaluator=evaluator) == 0

    assert corrector.correct.call_count == 0
    assert evaluator.evaluate.call_args.args[0] == "the essay, corrected by the teacher"
    assert (written.output_dir / "essay.txt").read_text(encoding="utf-8") == "the essay, as read"
    word = _docx_text(written.output_dir / "essay.docx")
    assert "the essay, as read" in word
    assert "the essay, corrected by the teacher" in word


# ── An emptied cache keeps the transcription, not the corrections ──────────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_an_emptied_cache_proofreads_the_teachers_transcription(tmp_path: Path) -> None:
    """
    Given  a proofreading the teacher corrected, and then the results cache is emptied
    When   the next run proofreads again
    Then   the teacher's transcription is what is sent, and their corrections of the old
           answer do not apply to the new one
    """
    reading = make_config(tmp_path, correction_enabled=False, docx_enabled=False)
    source = reading.input_dir / "essay.txt"
    source.write_text("teh essay", encoding="utf-8")
    assert _run(reading) == 0
    _fix_transcription(reading, source, "the essay, as read")

    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("the essay")
    assert _run(_proofreading_config(tmp_path), corrector=corrector) == 0
    _fix_corrected(_proofreading_config(tmp_path), source, "the essay, corrected by the teacher")

    shutil.rmtree(resolve_artifact_dir(reading.output_dir))

    corrector.reset_mock()
    corrector.correct.return_value = CorrectionOutcome("another answer")
    assert _run(_proofreading_config(tmp_path), corrector=corrector) == 0

    assert corrector.correct.call_args.args[0] == "the essay, as read"
    state = _state_for(_proofreading_config(tmp_path), source)
    assert state is not None
    texts = current_texts(machine_texts(state), TeacherWorkStore().load(compute_file_hash(source)))
    assert texts.corrected == "another answer"
    assert texts.corrections_work is None


# ── A scan whose text the teacher has is not read again ────────────────────


def _scan(input_dir: Path, name: str = "page.png") -> Path:
    """Write a page image into *input_dir*: a document that has to be read from its pixels."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    input_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (400, 120), color=(255, 255, 255))
    ImageDraw.Draw(img).text((20, 30), "Hello World", fill=(0, 0, 0))
    path = input_dir / name
    img.save(path)
    return path


def _run_scans(cfg, reader: MagicMock, *, corrector: MagicMock | None = None) -> int:
    """Run the pipeline with the built-in reader replaced by *reader*."""
    with patch(
        "teachers_teammate.infrastructure.stage_builder.PpOcrProcessor", return_value=reader
    ):
        return _run(cfg, corrector=corrector)


def _reading_config(tmp_path: Path, **overrides: object):
    """Return a config that reads pages with the built-in reader and proofreads nothing."""
    settings: dict[str, object] = {
        "ocr_engine": "ppocr",
        "correction_enabled": False,
        "docx_enabled": False,
        "preprocess_method": "none",
    }
    settings.update(overrides)
    return make_config(tmp_path, **settings)


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
@pytest.mark.parametrize("recognition_enabled", [True, False])
def test_an_emptied_cache_does_not_read_a_scan_the_teacher_transcribed(
    tmp_path: Path, recognition_enabled: bool
) -> None:
    """
    Given  a scan the teacher transcribed, and then the results cache is emptied
    When   the next run comes, with text recognition on or switched off
    Then   the pages are not read again, the file is not left out, and its text is the
           teacher's — the pages are still prepared for the scan
    """
    reading = _reading_config(tmp_path)
    scan = _scan(reading.input_dir)
    reader = MagicMock()
    reader.process_image.return_value = "teh essay"
    assert _run_scans(reading, reader) == 0
    _fix_transcription(reading, scan, "the essay")
    shutil.rmtree(resolve_artifact_dir(reading.output_dir))
    reader.reset_mock()

    again = _reading_config(tmp_path, recognition_enabled=recognition_enabled)
    assert _run_scans(again, reader) == 0

    reader.process_image.assert_not_called()
    state = _state_for(again, scan)
    assert state is not None
    assert state.ocr_done is True
    assert state.raw_text == "the essay"
    assert state.preview_img
    texts = current_texts(machine_texts(state), TeacherWorkStore().load(compute_file_hash(scan)))
    assert texts.transcription == "the essay"


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_a_changed_reading_setting_does_not_read_a_scan_whose_corrections_were_worked_on(
    tmp_path: Path,
) -> None:
    """
    Given  a proofread scan whose corrections the teacher changed (they fixed no transcription)
    When   a run with a changed reading setting comes
    Then   the pages are not read again, so the proofreading — and the corrections written on
           it — stay what they were
    """
    first = _reading_config(tmp_path, correction_enabled=True, correction_provider="openai")
    scan = _scan(first.input_dir)
    reader = MagicMock()
    reader.process_image.return_value = "teh essay"
    corrector = MagicMock()
    corrector.correct.return_value = CorrectionOutcome("the essay")
    assert _run_scans(first, reader, corrector=corrector) == 0
    _fix_corrected(first, scan, "the essay, corrected by the teacher")
    frozen_hash = _state_for(first, scan).ocr_config_hash
    reader.reset_mock()
    corrector.reset_mock()

    changed = _reading_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        preprocess_method="grayscale",
    )
    assert _run_scans(changed, reader, corrector=corrector) == 0

    reader.process_image.assert_not_called()
    corrector.correct.assert_not_called()
    state = _state_for(changed, scan)
    assert state is not None
    assert state.ocr_config_hash == frozen_hash
    texts = current_texts(machine_texts(state), TeacherWorkStore().load(compute_file_hash(scan)))
    assert texts.corrected == "the essay, corrected by the teacher"
