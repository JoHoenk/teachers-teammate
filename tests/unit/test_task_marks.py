"""What a document's task text really covers, as its proofreading key counts it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from teachers_teammate.application.formatting import TextFormatter
from teachers_teammate.domain.document_state import DocumentState, task_text_of
from teachers_teammate.domain.task_text import Segment
from teachers_teammate.domain.teacher_work import TeacherWork, TranscriptionWork
from teachers_teammate.infrastructure.state_repository import (
    StateRepository,
    compute_file_hash,
)
from teachers_teammate.infrastructure.workflow.cache_service import CacheContext
from teachers_teammate.infrastructure.workflow.file_processor import FileProcessor

TASK = "Aufgabe 1 Lies den Text!"
"""The printed line an invented worksheet carries above the student's answer."""

ANSWER = "Anna schreibt ihrer Freundin einen Brief."


def _state(
    text: str = f"{TASK}\n{ANSWER}\n",
    *,
    marks: Sequence[str] = (),
    text_format: str = "plain",
) -> DocumentState:
    """Return a read document with *text* and the marks given."""
    return replace(
        DocumentState(schema_version=3, source_path="doc.png", source_hash="h"),
        raw_text=text,
        raw_text_format=text_format,
        ocr_done=True,
        task_passages=list(marks),
    )


def _work(*, not_task: Sequence[str] = (), transcription: str | None = None) -> TeacherWork:
    """Return the teacher's work on the document: their entries and their transcription."""
    return TeacherWork(
        source_hash="h",
        name="doc.png",
        transcription=(
            None if transcription is None else TranscriptionWork(transcription, "plain")
        ),
        not_task=tuple(not_task),
    )


def _task_text(state: DocumentState, work: TeacherWork | None = None) -> tuple[str, ...]:
    """Return the task text of *state* as the queue keys it."""
    return task_text_of(state, work, TextFormatter.plain_text)


def _segments(state: DocumentState, text: str, work: TeacherWork | None = None) -> tuple:
    """Return the segments a run writes into the Word file of *state* for the corrected *text*."""
    ctx = CacheContext(
        state=state,
        source_hash="h",
        ocr_prompt="",
        ocr_config_hash="",
        correction_config_hash="",
        eval_config_hash="",
        preview_config_hash="",
        task_marks=state.task_marks,
        not_task=work.not_task if work is not None else (),
    )
    return FileProcessor._task_segments(ctx, text, "plain")


def test_a_passage_the_text_holds_counts() -> None:
    """
    Given  a document whose text carries the marked passage
    When   its task text is asked for
    Then   the passage counts, as the text has it
    """
    assert _task_text(_state(marks=[TASK])) == (TASK,)


def test_a_passage_the_text_no_longer_holds_counts_for_nothing() -> None:
    """
    Given  a document marked with a passage its text does not carry
    When   its task text is asked for
    Then   nothing counts, so the text is proofread as it stands
    """
    assert _task_text(_state("Nur ein Satz des Schülers.", marks=[TASK])) == ()


def test_a_line_the_teacher_proofreads_after_all_counts_for_nothing() -> None:
    """
    Given  a marked passage, and the teacher's work that proofreads exactly that line
    When   its task text is asked for
    Then   nothing counts: the teacher's decision beats the marking it undoes
    """
    assert _task_text(_state(marks=[TASK]), _work(not_task=[TASK])) == ()


def test_the_marks_are_looked_for_in_the_teachers_transcription() -> None:
    """
    Given  a marked passage, and a transcription the teacher fixed so that it no longer has the
           printed line
    When   its task text is asked for
    Then   nothing counts: the proofreading cuts the teacher's text, and so does the key
    """
    work = _work(transcription=f"{ANSWER}\n")

    assert _task_text(_state(marks=[TASK]), work) == ()


def test_a_markdown_document_counts_the_lines_its_plain_text_has() -> None:
    """
    Given  a document stored as Markdown, marked with the line as the proofreading reads it
    When   its task text is asked for
    Then   the line counts although the stored text spells it with markup around it
    """
    state = _state(f"## {TASK}\n\n{ANSWER}\n", marks=[TASK], text_format="markdown")

    assert _task_text(state) == (TASK,)


def test_a_document_without_marks_counts_nothing() -> None:
    """
    Given  a document the batch marked nowhere
    When   its task text is asked for
    Then   nothing counts, so its proofreading key stays the one its settings make
    """
    assert _task_text(_state()) == ()


def test_the_marks_of_a_document_survive_a_stored_record(tmp_path: Path) -> None:
    """
    Given  a stored document with a marked passage
    When   it is loaded again
    Then   the mark is there
    """
    source = tmp_path / "doc.png"
    source.write_bytes(b"data")
    repository = StateRepository(tmp_path / "state")
    state = replace(
        repository.create(source, compute_file_hash(source)),
        raw_text=f"{TASK}\n{ANSWER}\n",
        ocr_done=True,
        task_passages=[TASK],
    )
    repository.save(source, state)

    loaded = repository.load_valid(source)

    assert loaded is not None
    assert loaded.task_marks == (TASK,)


def test_the_segments_of_a_proofread_text_are_the_places_the_marks_stand() -> None:
    """
    Given  a document marked with a printed line, and a proofread text whose student part changed
    When   the segments of that proofread text are asked for
    Then   the printed line is task text and the rest is the student's
    """
    proofread = f"{TASK}\nAnna schreibt ihrer Freundin einen langen Brief.\n"

    segments = _segments(_state(marks=[TASK]), proofread)

    assert [segment.kind for segment in segments] == ["task", "student"]
    assert proofread[segments[0].start : segments[0].end] == TASK


def test_a_document_without_marks_has_no_segments() -> None:
    """
    Given  a document the batch found nothing in
    When   the segments of its text are asked for
    Then   there is none, so a display shows the text as one student stretch
    """
    assert _segments(_state(), f"{TASK}\n{ANSWER}\n") == ()


def test_a_text_with_no_lines_is_one_student_stretch() -> None:
    """
    Given  a document marked with a printed line, whose pages all read empty
    When   the segments of its text are asked for
    Then   the whole text is one student stretch, and nothing fails
    """
    text = "Page 1:\nPage 2:\n"

    assert _segments(_state(text, marks=[TASK]), text) == (Segment(0, len(text), "student"),)
