"""The reading pass: the queue is read once, so the batch can find the print it shares."""

from __future__ import annotations

from pathlib import Path

from collections.abc import Sequence

from teachers_teammate.domain.task_text import TaskTextFound
from teachers_teammate.infrastructure.workflow.processing_result import ProcessingResult
from teachers_teammate.infrastructure.workflow.task_text_pass import mark_task_text


class _Reporter:
    """A reporter that only records the lines it was given."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def status(self, line: str) -> None:
        self.lines.append(line)

    def warn(self, line: str) -> None:  # pragma: no cover - the pass warns about nothing
        self.lines.append(line)

    def error(self, line: str) -> None:  # pragma: no cover - the pass reports no error
        self.lines.append(line)


def _files(tmp_path: Path, count: int) -> list[Path]:
    """Return *count* invented input files."""
    return [tmp_path / f"sheet{index}.png" for index in range(count)]


def test_the_pass_reads_every_file_before_the_first_is_proofread(tmp_path: Path) -> None:
    """
    Given  three files, a reader that records them, and a detection that finds one worksheet
    When   the reading pass runs
    Then   every file was read, the detection saw the whole queue, and the line says what it found
    """
    files = _files(tmp_path, 3)
    read: list[str] = []
    seen: list[Path] = []
    reporter = _Reporter()

    def mark(queued: Sequence[Path]) -> TaskTextFound:
        seen.extend(queued)
        return TaskTextFound(read=3, documents=3, worksheets=1)

    started: list[tuple[str, int, int]] = []

    reading = mark_task_text(
        files,
        read=lambda file: read.append(file.name),
        mark=mark,
        stopping=lambda: False,
        reporter=reporter,
        started=lambda file, index, total: started.append((file.name, index, total)),
    )

    assert read == ["sheet0.png", "sheet1.png", "sheet2.png"]
    assert started == [("sheet0.png", 1, 3), ("sheet1.png", 2, 3), ("sheet2.png", 3, 3)]
    assert seen == files
    assert reading.found is not None and reading.found.documents == 3
    assert reading.unread == {}
    assert any("3 of 3 documents (1 worksheet)" in line for line in reporter.lines)


def test_a_stop_keeps_what_was_read_and_marks_nothing(tmp_path: Path) -> None:
    """
    Given  a run the teacher stopped while the reading pass was going
    When   the pass runs
    Then   it finds nothing and the queue is not compared at all
    """
    files = _files(tmp_path, 3)
    read: list[str] = []

    reading = mark_task_text(
        files,
        read=lambda file: read.append(file.name),
        mark=lambda queued: TaskTextFound(documents=3),
        stopping=lambda: len(read) == 2,
        reporter=_Reporter(),
    )

    assert reading.found is None
    assert read == ["sheet0.png", "sheet1.png"]


def test_a_file_stopped_while_it_is_read_ends_the_pass(tmp_path: Path) -> None:
    """
    Given  a reader that the teacher's "Stop now" ends while it reads the second file
    When   the pass runs
    Then   the third file is not read and nothing is marked
    """
    files = _files(tmp_path, 3)
    read: list[str] = []
    marked: list[Sequence[Path]] = []

    def reader(file: Path) -> ProcessingResult | None:
        read.append(file.name)
        return ProcessingResult.stopped_at() if file.name == "sheet1.png" else None

    reading = mark_task_text(
        files,
        read=reader,
        mark=lambda queued: marked.append(queued) or TaskTextFound(),
        stopping=lambda: False,
        reporter=_Reporter(),
    )

    assert reading.found is None
    assert read == ["sheet0.png", "sheet1.png"]
    assert marked == []


def test_a_file_that_cannot_be_read_is_left_to_the_second_pass(tmp_path: Path) -> None:
    """
    Given  a file whose reading fails, between two files that read fine
    When   the pass runs
    Then   the pass is not stopped, the other two are read, the queue is compared, and the
           failure is kept for the file's turn, which does not read it again
    """
    files = _files(tmp_path, 3)
    read: list[str] = []
    marked: list[Sequence[Path]] = []

    failed = ProcessingResult.failure("the scan could not be opened")

    def reader(file: Path) -> ProcessingResult | None:
        if file.name == "sheet1.png":
            return failed
        read.append(file.name)
        return None

    reading = mark_task_text(
        files,
        read=reader,
        mark=lambda queued: marked.append(queued) or TaskTextFound(documents=0),
        stopping=lambda: False,
        reporter=_Reporter(),
    )

    assert read == ["sheet0.png", "sheet2.png"]
    assert marked == [files]
    assert reading.found is not None
    assert reading.unread == {files[1]: failed}


def test_an_unexpected_error_fails_that_file_alone(tmp_path: Path) -> None:
    """
    Given  a reader that raises an error no stage expected on the first file
    When   the pass runs
    Then   the other files are read, and the first keeps a failure for its turn
    """
    files = _files(tmp_path, 3)
    read: list[str] = []

    def reader(file: Path) -> None:
        if file.name == "sheet0.png":
            raise KeyError("a bug")
        read.append(file.name)

    reading = mark_task_text(
        files,
        read=reader,
        mark=lambda queued: TaskTextFound(),
        stopping=lambda: False,
        reporter=_Reporter(),
    )

    assert read == ["sheet1.png", "sheet2.png"]
    assert reading.unread[files[0]].ok is False
