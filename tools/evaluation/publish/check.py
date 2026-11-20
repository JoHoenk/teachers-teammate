"""Checking a store, and the committed tree it was exported to, file by file.

The rules are :mod:`..store.validate`'s; this module walks the files and says what each one is.  It
sits in ``publish`` because the last check needs the export's own caps: a stored replacement or
explanation may not keep words of its text, and the caps are rebuilt from the store's texts with
:func:`~.export.redactor_of`, which is the function the export applied.

Nothing here repairs a file and nothing raises: a problem is a string a person reads, and the
command line decides what an exit code means.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..core.formats import FormatError, read_jsonl
from ..core.records import Answer, RunHeader, TestRecord, read_run_file
from ..input import testset as testset_module
from ..input.testset import Corpora
from ..store import layout, reader, redacted, validate
from ..store import marks as marks_module
from ..store.redacted import RedactedAnswer, read_redacted
from .export import redactor_of


@dataclass(frozen=True)
class Checked:
    """One file of the store, and what checking it found."""

    name: str
    problems: tuple[str, ...] = ()
    counted: str = ""
    """What was checked, in words: ``12 texts``, ``40 answers``, ``7 marks``."""
    notes: tuple[str, ...] = ()
    """What the check could not look at, so a reader knows what was not checked."""

    @property
    def ok(self) -> bool:
        """Whether the file passed every check."""
        return not self.problems


def check_store(
    root: Path, *, out: Path | None = None, corpora: Corpora | None = None
) -> list[Checked]:
    """Return what checking every file of the store at *root* found.

    Args:
        root: The store's directory.
        out: The committed tree, when one was exported; its run files are checked too.
        corpora: The corpora, when the caller named them; with them the five-word rule and the
            hashes are checked against the real text, and without them only the format is.
    """
    found: list[Checked] = []
    for name in layout.testsets(root):
        found.append(_check_testset(root, name, corpora))
    for stored in reader.read_runs(root):
        found.append(_check_run(stored, root))
    for path in layout.marks_files(root):
        found.append(_check_marks(path, root))
    for path in _committed_runs(out):
        found.append(_check_file(path, root, corpora))
    return found


def check_files(paths: Sequence[Path], root: Path, corpora: Corpora | None = None) -> list[Checked]:
    """Return what checking the run files *paths* found, against the store at *root*."""
    return [_check_file(path, root, corpora) for path in paths]


def _committed_runs(out: Path | None) -> list[Path]:
    """Return the committed run files below *out*, or none when nothing was exported there."""
    if out is None or not (out / redacted.EXPORT_NAME).is_file():
        return []
    return layout.run_files(out)


def _check_testset(root: Path, name: str, corpora: Corpora | None) -> Checked:
    """Return what checking one test set of the store found."""
    try:
        records = reader.read_testset(root, name)
        manifest = reader.read_manifest(root, name)
    except (FormatError, OSError) as error:
        return Checked(name=name, problems=(str(error),))
    problems = validate.problems_of_testset(records, manifest)
    if corpora is not None:
        problems += testset_module.corpus_problems(manifest, corpora)
        problems += testset_module.check_items(manifest, corpora)
    return Checked(name=name, problems=tuple(problems), counted=f"{len(records)} texts")


def _check_run(stored: reader.StoredRun, root: Path) -> Checked:
    """Return what checking one run of the store found, against its test set."""
    name = stored.run.header.input.testset
    try:
        records = reader.read_testset_by_id(root, name)
    except (FormatError, OSError) as error:
        return Checked(name=stored.name, problems=(str(error),))
    problems = validate.problems_of_run(stored.run.header, stored.run.answers, records)
    notes = validate.notes_of_run(name, records)
    return Checked(
        name=stored.name,
        problems=tuple(problems),
        counted=f"{len(stored.run.answers)} answers",
        notes=tuple(notes),
    )


def _check_file(path: Path, root: Path, corpora: Corpora | None) -> Checked:
    """Return what checking one run file named on the command line found.

    The file may be a run of the store, which holds the model's output, or a committed one, which
    holds the scores instead; the caps are checked against the store's texts when it has them.
    """
    try:
        lines = read_jsonl(path)
    except (FormatError, OSError) as error:
        return Checked(name=path.name, problems=(str(error),))
    stored = any("output" in line for line in lines)
    try:
        if stored:
            run = read_run_file(lines, source=str(path))
            header, answers = run.header, run.answers
            problems = _stored_problems(header, answers, root)
        else:
            committed = read_redacted(lines, str(path))
            header, answers = committed.header.header, committed.answers
            problems = _committed_problems(header, answers, root, corpora)
    except (FormatError, KeyError, ValueError) as error:
        return Checked(name=path.name, problems=(str(error),))
    notes = validate.notes_of_run(header.input.testset, _records_of(root, header.input.testset))
    return Checked(
        name=path.name,
        problems=tuple(problems),
        counted=f"{len(answers)} answers",
        notes=tuple(notes),
    )


def _stored_problems(header: RunHeader, answers: Sequence[Answer], root: Path) -> list[str]:
    """Return what is wrong with a run of the store against the store's texts."""
    return validate.problems_of_run(header, answers, _records_of(root, header.input.testset))


def _committed_problems(
    header: RunHeader,
    answers: Sequence[RedactedAnswer],
    root: Path,
    corpora: Corpora | None,
) -> list[str]:
    """Return what is wrong with a committed run against the store's texts, and against its caps."""
    records = _records_of(root, header.input.testset)
    problems = validate.problems_of_run(header, answers, records)
    if corpora is not None:
        problems += _corpus_problems_of(answers, records, corpora)
    return problems


def _corpus_problems_of(
    answers: Sequence[RedactedAnswer], records: Mapping[str, TestRecord], corpora: Corpora
) -> list[str]:
    """Return what the caps of *answers*' texts say about their stored fields.

    The caps are the ones the export applied, rebuilt from the store's texts: a field that keeps
    words of its source or reference, or of a restricted corpus at all, is refused.
    """
    found: list[str] = []
    for answer in answers:
        record = records.get(answer.text)
        if record is None:
            continue
        try:
            caps = redactor_of(record, corpora)
        except ValueError as error:
            found.append(f"{answer.text}: {error}")
            continue
        for start, end, field in answer.edits:
            if not caps.accepts(field):
                found.append(f"{answer.text}: the replacement at {start}:{end} keeps corpus words")
        for note in answer.notes:
            if not caps.accepts_explanation(str(note.get("explanation", ""))):
                found.append(f"{answer.text}: a note keeps corpus words")
    return found


def _records_of(root: Path, testset: str) -> dict[str, TestRecord]:
    """Return the store's records of *testset*, or an empty mapping when the store has none."""
    try:
        return reader.read_testset_by_id(root, testset)
    except FormatError, OSError:
        return {}


def _check_marks(path: Path, root: Path) -> Checked:
    """Return what checking one marks file found, against its test set."""
    question = layout.question_of(path)
    name = next(
        (
            name
            for name in layout.testsets(root)
            if marks_module.question_of(root, name) == question
        ),
        "",
    )
    if not name:
        return Checked(
            name=path.name,
            problems=("no test set of the store hashes to this marks file's question",),
        )
    try:
        found = reader.read_marks(path)
        records = reader.read_testset_by_id(root, name)
    except (FormatError, ValueError, OSError) as error:
        return Checked(name=path.name, problems=(str(error),))
    return Checked(
        name=path.name,
        problems=tuple(validate.problems_of_marks(found, records)),
        counted=f"{len(found)} marks",
    )


__all__ = ["Checked", "check_files", "check_store"]
