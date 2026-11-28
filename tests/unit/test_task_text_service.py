"""The printed text of a queue: what a run leaves alone, and what it stores with each document."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_config

from teachers_teammate.application.task_text import TaskTextService
from teachers_teammate.domain.teacher_work import with_not_task
from teachers_teammate.infrastructure.state_repository import (
    OcrResultRecord,
    StateRepository,
    compute_file_hash,
)
from teachers_teammate.infrastructure.teacher_work_store import TeacherWorkStore

HEADER = "Gymnasium Musterstadt"
TASK = "Aufgabe 1 Lies den Text und beantworte die Fragen."
"""The two printed lines an invented worksheet carries above the student's answer."""

ANSWERS = (
    "Anna schreibt ihrer Freundin einen langen Brief über die Ferien.",
    "Am Montag war die Klasse im Museum und hat viel gelernt.",
    "Der Lehrer sammelt die Hefte ein und gibt sie morgen zurück.",
)


def _stored(tmp_path: Path, name: str, text: str, *, not_task: tuple[str, ...] = ()) -> Path:
    """Store *text* as the read document *name* and return its path."""
    source = tmp_path / name
    source.write_text(f"scan of {name}", encoding="utf-8")
    source_hash = compute_file_hash(source)
    repo = StateRepository(tmp_path / "state")
    state = repo.create(source, source_hash)
    state = repo.record_ocr_result(
        source,
        state,
        OcrResultRecord(
            source_hash=source_hash,
            raw_text=text,
            preview_img="",
            source_image="",
            ocr_config_hash="h1",
            preview_config_hash="",
        ),
    )
    if not_task:
        _work(tmp_path).update(source_hash, name, lambda work: with_not_task(work, not_task))
    return source


def _work(tmp_path: Path) -> TeacherWorkStore:
    """Return the store of the teacher's work in *tmp_path*."""
    return TeacherWorkStore(tmp_path / "work")


def _worksheet(tmp_path: Path, count: int = 3, *, not_task: tuple[str, ...] = ()) -> list[Path]:
    """Store *count* documents of one worksheet, each with its own answer.

    *not_task* is what the teacher took back in the first document.
    """
    return [
        _stored(
            tmp_path,
            f"sheet{index}.png",
            f"{HEADER}\n{TASK}\n{ANSWERS[index % len(ANSWERS)]}\n",
            not_task=not_task if index == 0 else (),
        )
        for index in range(count)
    ]


def _service(tmp_path: Path) -> TaskTextService:
    """Return the service over the store in *tmp_path*."""
    return TaskTextService(
        state_store_factory=lambda config: StateRepository(tmp_path / "state"),
        work=_work(tmp_path),
    )


def _marks(source: Path, tmp_path: Path) -> list[str]:
    """Return the texts the store holds as *source*'s printed text."""
    state = StateRepository(tmp_path / "state").load_valid(source)
    assert state is not None
    return list(state.task_marks)


def test_one_worksheet_is_marked_in_every_document(tmp_path: Path) -> None:
    """
    Given  three documents whose header and task line repeat above the student's own answer
    When   the queue is marked
    Then   each document holds the printed lines, and the line names one worksheet
    """
    files = _worksheet(tmp_path)

    found = _service(tmp_path).mark_a_queue(make_config(tmp_path), files)

    assert found.documents == 3
    assert found.read == 3
    assert found.worksheets == 1
    assert _marks(files[0], tmp_path) == [f"{HEADER}\n{TASK}"]


def test_a_line_the_teacher_called_not_task_is_left_to_the_proofreading(tmp_path: Path) -> None:
    """
    Given  three documents of one worksheet, the teacher's work on the first with "not task" on
           the header
    When   the queue is marked
    Then   the first does not keep the header, and the others do: the entry is that document's
    """
    files = _worksheet(tmp_path, not_task=(HEADER,))

    _service(tmp_path).mark_a_queue(make_config(tmp_path), files)

    assert _marks(files[0], tmp_path) == [TASK]
    assert all(_marks(file, tmp_path) == [f"{HEADER}\n{TASK}"] for file in files[1:])


def test_fewer_than_three_documents_detect_nothing(tmp_path: Path) -> None:
    """
    Given  two documents that carry the same header and task line
    When   the queue is marked
    Then   nothing is stored: one or two copies do not make printed text
    """
    files = _worksheet(tmp_path, count=2)

    found = _service(tmp_path).mark_a_queue(make_config(tmp_path), files)

    assert found.documents == 0
    assert all(_marks(file, tmp_path) == [] for file in files)


def test_a_passage_a_document_already_holds_is_kept_once(tmp_path: Path) -> None:
    """
    Given  a queue that was marked before, so every document already holds the passages
    When   the same queue is marked again
    Then   each document still holds one copy of them
    """
    files = _worksheet(tmp_path)
    service = _service(tmp_path)
    service.mark_a_queue(make_config(tmp_path), files)

    service.mark_a_queue(make_config(tmp_path), files)

    assert _marks(files[0], tmp_path) == [f"{HEADER}\n{TASK}"]
