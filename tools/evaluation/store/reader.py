"""Reading the store: run files, test sets and marks, as the typed records of :mod:`..core`.

Every reader here is thin on purpose.  The formats and their identities live in
:mod:`..core.records`; this module only knows *where* the files are (:mod:`.layout`) and in which
order a set's languages are read.  A reader never scores, never groups and never compares — that is
:mod:`..report`'s work, and keeping it out is what lets a report be rebuilt from the files alone.

A run file is read whole: its header, its sessions and its answers.  A test set is read per
language and returned in the table's order, so two readers of the same set see the same sequence
and a hash over it is stable.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, replace
from pathlib import Path

from ..core import axes
from ..core.formats import FormatError, read_first_json, read_jsonl
from ..core.manifest import Manifest
from ..core.manifest import read_manifest as read_manifest_file
from ..core.records import (
    AcceptedEdit,
    Mark,
    RunFile,
    RunHeader,
    TestRecord,
    header_from_line,
    read_run_file,
    text_id,
)
from . import layout


@dataclass(frozen=True)
class StoredRun:
    """One run file of the store, with the path it was read from.

    The path is not part of the format: it says which folder the run came from, which is what a
    conversion report and a "where is this run" question need.
    """

    path: Path
    run: RunFile

    @property
    def folder(self) -> str:
        """Return the folder the run file sits in, ``""`` for a file directly below ``runs/``."""
        return self.path.parent.name if self.path.parent.name != layout.RUNS_DIR else ""

    @property
    def name(self) -> str:
        """Return the file name of the run, which is its label and never read back."""
        return self.path.name


def read_run(path: Path) -> RunFile:
    """Return the run file stored in *path*.

    A last line that was cut off mid-write is left out and kept on the run as
    :attr:`~..core.records.RunFile.torn`, so a reader can say that one answer is missing instead of
    refusing the whole run.

    Raises:
        FormatError: if the file is empty, its first line is not a header, or a later line is
            neither a session nor an answer.
    """
    records, torn = read_jsonl(path, torn_tail="report")
    return read_run_file(records, source=str(path), torn=torn)


def read_header(path: Path) -> RunHeader:
    """Return the header of a run file, from its first line only.

    Raises:
        FormatError: if the file is empty or its first line is not a header.
    """
    return header_from_line(read_first_json(path), str(path))


def run_index(root: Path) -> dict[str, Path]:
    """Return the store's run files by run key, so a run's file is found without reading them all.

    A run key names one file: the repeats of one setup on one machine are different runs, and the
    same setup on another machine is another run too.  A file that cannot be read is left out; the
    caller that needs it reads it again and refuses with the reason.

    Raises:
        ValueError: if two files carry one run key, naming both, because one of them is a copy.
    """
    found: dict[str, Path] = {}
    for path in layout.run_files(root):
        key: str | None = None
        with contextlib.suppress(FormatError, OSError):
            key = axes.run_key_sha256(read_header(path))
        if key is None:
            continue
        if key in found:
            raise ValueError(
                f"two run files carry one run key, so one of them is a copy: "
                f"{found[key]} and {path}"
            )
        found[key] = path
    return found


def read_runs(root: Path, folder: str | None = None) -> list[StoredRun]:
    """Return every run of the store, in path order; *folder* narrows it to one folder."""
    return [StoredRun(path=path, run=read_run(path)) for path in layout.run_files(root, folder)]


def read_manifest(root: Path, testset: str) -> Manifest:
    """Return the manifest that pins *testset*.

    Raises:
        FormatError: if the file is missing, is not a JSON object, or is of another format.
    """
    return read_manifest_file(layout.manifest_path(root, testset))


def read_testset(root: Path, testset: str) -> list[TestRecord]:
    """Return every record of *testset*, language by language in the table's order.

    The accepted alternatives of the set are merged into the records they belong to, because they
    are part of the key group: a set read without them would score against another key.

    Raises:
        FormatError: if a record file is missing or holds a record of a format this reader does not
            know.
    """
    accepted = read_accepted(root, testset)
    records: list[TestRecord] = []
    for language in layout.languages_of(root, testset):
        path = layout.testset_file(root, testset, language)
        for record in (TestRecord.from_dict(line) for line in read_jsonl(path)):
            edits = accepted.get(text_id(record))
            records.append(replace(record, accepted=edits) if edits else record)
    return records


def read_accepted(root: Path, testset: str) -> dict[str, tuple[AcceptedEdit, ...]]:
    """Return the accepted alternatives of *testset*, keyed by the text each belongs to.

    The file is optional: a set whose marks nobody has accepted yet has none, and reads as a set
    whose key is the annotators' alone.

    Raises:
        FormatError: if a line is not an accepted edit of a format this reader knows.
    """
    path = layout.accepted_path(root, testset)
    if not path.is_file():
        return {}
    found: dict[str, list[AcceptedEdit]] = {}
    for record in read_jsonl(path):
        found.setdefault(str(record["text"]), []).append(AcceptedEdit.from_dict(record))
    return {text: tuple(edits) for text, edits in found.items()}


def read_testset_by_id(root: Path, testset: str) -> dict[str, TestRecord]:
    """Return the records of *testset* keyed ``<language>/<id>``, which is how an answer finds its text.

    The order is the read order, so :func:`~..core.records.question_sha256` of the values is the
    set's own hash.
    """
    return {text_id(record): record for record in read_testset(root, testset)}


def read_marks(path: Path) -> list[Mark]:
    """Return every mark stored in *path*.

    Raises:
        FormatError: if a line is not a mark of a format this reader knows.
        ValueError: if a mark's value is not one a marker can give.
    """
    return [Mark.from_dict(record) for record in read_jsonl(path)]


def read_all_marks(root: Path) -> dict[str, list[Mark]]:
    """Return every marks file of the store, keyed by its marker."""
    found: dict[str, list[Mark]] = {}
    for path in layout.marks_files(root):
        found.setdefault(layout.marker_of(path), []).extend(read_marks(path))
    return found


__all__ = [
    "StoredRun",
    "read_accepted",
    "read_all_marks",
    "read_header",
    "read_manifest",
    "read_marks",
    "read_run",
    "read_runs",
    "read_testset",
    "read_testset_by_id",
    "run_index",
]
