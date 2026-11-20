"""Writing the store: run files, test sets and marks, in the formats of :mod:`..core`.

Two rules shape this module.  A run file is **append-only**: the header is written once, and a
session or an answer is appended, so an interrupted run resumes and a copy cannot separate the
header from its answers.  Everything else is written whole, with sorted keys, so two writers of the
same data produce the same bytes.

Nothing here decides *what* to write: the records come from :mod:`..core.records`, and a caller that
wants to keep a field it does not know passes the line it read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..core.formats import append_jsonl, read_jsonl, write_jsonl
from ..core.manifest import Manifest, write_manifest
from ..core.records import AcceptedEdit, Answer, Mark, RunFile, Session, TestRecord
from . import layout
from .marks import identity


def write_run(path: Path, run: RunFile) -> None:
    """Write *run* to *path*, replacing the file: the header first, then sessions and answers."""
    write_jsonl(path, run.lines())


def start_run(path: Path, run: RunFile) -> None:
    """Write the header and the sessions of *run* to *path*, replacing the file.

    A run that starts writes its header once; every answer after it is appended, so a crash leaves
    a file that says what was asked and how far it got.
    """
    write_jsonl(path, [run.header.to_dict(), *(session.to_dict() for session in run.sessions)])


def append_session(path: Path, session: Session) -> None:
    """Append one session line to the run file at *path*, so a resume is recorded and not rewritten."""
    append_jsonl(path, [session.to_dict()])


def append_answer(path: Path, answer: Answer) -> None:
    """Append one answer line to the run file at *path*."""
    append_jsonl(path, [answer.to_dict()])


def append_answers(path: Path, answers: Iterable[Answer]) -> None:
    """Append several answer lines to the run file at *path*, in the order given."""
    append_jsonl(path, [answer.to_dict() for answer in answers])


def write_testset(
    root: Path,
    testset: str,
    records: Sequence[TestRecord],
    manifest: Manifest,
) -> None:
    """Write *testset* to the store: one record file per language, then the manifest.

    The records are grouped by their own ``language``, so a caller cannot put a German text in
    ``en.jsonl`` by passing the wrong list.  The manifest is written last: a set whose manifest is
    there is complete.
    """
    by_language: dict[str, list[TestRecord]] = {}
    for record in records:
        by_language.setdefault(record.language, []).append(record)
    for language, group in by_language.items():
        write_jsonl(
            layout.testset_file(root, testset, language), [item.to_dict() for item in group]
        )
    write_manifest(layout.manifest_path(root, testset), manifest)


def write_accepted(path: Path, by_text: Mapping[str, Sequence[AcceptedEdit]]) -> None:
    """Write the accepted alternatives of a test set, one line per edit, sorted by text and span.

    The order is fixed so two writers of the same marks produce the same bytes: the file is part of
    the key group, and a key hash that moved because a dict was iterated differently would be a
    false alarm.
    """
    lines = [
        {"text": text, **edit.to_dict()}
        for text in sorted(by_text)
        for edit in sorted(by_text[text], key=lambda edit: edit.span)
    ]
    write_jsonl(path, lines)


@dataclass(frozen=True)
class MarksChange:
    """What one ``marks`` call did to a marks file: what it added, what it replaced, what it kept."""

    path: Path
    added: int
    changed: int
    kept: int

    def lines(self) -> list[str]:
        """Return what the command prints: one line per count."""
        return [
            f"wrote {self.path} ({self.added} new marks, {self.changed} replaced, {self.kept} kept)"
        ]


def merge_marks(path: Path, marks: Iterable[Mark]) -> MarksChange:
    """Merge *marks* into the file at *path*, replacing a mark of the same identity and keeping the rest.

    A marks file holds one marker's verdicts on one set, and marking a second run's sheet must not
    erase the first: the file is merged, not replaced.  The lines are written sorted by kind and
    identity, so two writers of the same marks produce the same bytes, and the file is replaced
    atomically, so a reader never sees half of it.

    Raises:
        FormatError: if the file exists and a line is not a mark of a format this reader knows.
    """
    existing = _marks_of(path)
    before = {identity(mark): mark for mark in existing}
    merged = dict(before)
    added = changed = 0
    for mark in marks:
        key = identity(mark)
        if key not in merged:
            added += 1
        elif merged[key] != mark:
            changed += 1
        merged[key] = mark
    order = sorted(merged, key=lambda key: (merged[key].kind, key))
    write_jsonl(path, [merged[key].to_dict() for key in order])
    return MarksChange(path=path, added=added, changed=changed, kept=len(before) - changed)


def _marks_of(path: Path) -> list[Mark]:
    """Return the marks already stored at *path*, or none when the file is not there yet."""
    if not path.exists():
        return []
    return [Mark.from_dict(line) for line in read_jsonl(path)]


__all__ = [
    "MarksChange",
    "append_answer",
    "append_answers",
    "append_session",
    "merge_marks",
    "start_run",
    "write_accepted",
    "write_run",
    "write_testset",
]
