"""Checking a store before anything reads it: the test sets, the run files and the marks.

Validation is a layer of its own because the three kinds of file fail in different ways and a
report that silently reads a broken one is worse than no report.  :func:`check_store` walks a whole
store and says what each file is; the ``problems_of_*`` functions are the rules it applies.  The
checks are the ones the format promises:

* a test set's ids are unique, its labels are still edits of its own reference, and its spans lie on
  token boundaries — a label whose edit moved (a tokenizer or differ change) is **listed**, never
  dropped;
* a run file's answers belong to the set it names, by id and by ``text_sha256``, and no text is
  answered twice;
* a run's protocol is one the table records, so nobody can say which prompts it used when it is not;
* a mark's text exists and its span lies on token boundaries; a mark whose edit no answer makes any
  more is listed too.

Nothing here repairs a file.  A problem is a string a person reads, and the caller decides whether
to refuse or to report.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from teachers_teammate.domain.proofreading.edit_agreement import char_edits, char_to_token, edits

from ..core import protocol_table
from ..core.constants import OLLAMA, TextKind
from ..core.formats import FormatError, read_jsonl
from ..core.hashing import sha256_text
from ..core.manifest import Manifest
from ..core.records import (
    MARK_VALUES,
    TASKS,
    Answer,
    Mark,
    RunFile,
    RunHeader,
    TestRecord,
    question_sha256,
    read_run_file,
)
from . import layout, reader
from . import marks as marks_module

KINDS: tuple[str, ...] = (TextKind.ERRORS, TextKind.CORRECT)
"""What a test record can be: a text with mistakes, or one that is already correct."""


class ValidationError(ValueError):
    """A stored file breaks a rule of its format, and the caller asked to be refused."""


def _exact_edits(source: str, reference: str) -> set[tuple[int, int, str]]:
    """Return the reference's edits as ``(token start, token end, replacement text)``."""
    return {
        (start, end, "".join(replacement)) for start, end, replacement in edits(source, reference)
    }


def _label_problems(record: TestRecord, exact: set[tuple[int, int, str]]) -> list[str]:
    """Return what is wrong with the labels of *record*, given its reference's exact edits."""
    found: list[str] = []
    for label in record.labels:
        found.extend(_span_problems(record, label.span, "label"))
        start = char_to_token(record.source, label.start)
        end = char_to_token(record.source, label.end)
        if start is None or end is None:
            continue
        if (start, end, label.replacement) not in exact:
            found.append(
                f"{record.id}: the label {label.code or 'unlabelled'} at {label.span} is no longer "
                f"an edit of the reference"
            )
    return found


def _span_problems(record: TestRecord, span: tuple[int, int], what: str) -> list[str]:
    """Return what is wrong with one character span of *record*: its bounds and its boundaries."""
    start, end = span
    if not 0 <= start <= end <= len(record.source):
        return [f"{record.id}: the {what} span {span} is outside the text of {len(record.source)}"]
    if char_to_token(record.source, start) is None or char_to_token(record.source, end) is None:
        return [f"{record.id}: the {what} span {span} does not lie on token boundaries"]
    return []


def problems_of_record(record: TestRecord) -> list[str]:
    """Return what is wrong with one test record, on its own."""
    found: list[str] = []
    if record.kind not in KINDS:
        found.append(f"{record.id}: kind {record.kind!r} is not one of {', '.join(KINDS)}")
    if record.kind == TextKind.ERRORS and not record.reference.strip():
        found.append(f"{record.id}: an error text needs a reference")
    if record.kind == TextKind.CORRECT and not record.sentences:
        found.append(f"{record.id}: a correct text needs its sentences")
    for span in record.sentence_spans:
        found.extend(_span_problems(record, span, "sentence"))
    for index in record.unchanged_sentences:
        if not 0 <= index < len(record.sentence_spans):
            found.append(f"{record.id}: unchanged sentence {index} is not one of the spans")
    for edit in record.accepted:
        found.extend(_span_problems(record, edit.span, "accepted"))
    if record.kind == TextKind.ERRORS:
        found.extend(_label_problems(record, _exact_edits(record.source, record.reference)))
    return found


def problems_of_testset(
    records: Sequence[TestRecord], manifest: Manifest | None = None
) -> list[str]:
    """Return what is wrong with a whole test set, and with its manifest when one is given."""
    found: list[str] = []
    seen: set[str] = set()
    for record in records:
        if record.id in seen:
            found.append(f"{record.id}: the id appears twice")
        seen.add(record.id)
        found.extend(problems_of_record(record))
    if manifest is not None:
        found.extend(_manifest_problems(records, manifest))
    return found


def _manifest_problems(records: Sequence[TestRecord], manifest: Manifest) -> list[str]:
    """Return what is wrong with a manifest against the records it pins."""
    found: list[str] = []
    wanted = question_sha256(records)
    if manifest.question_sha256 != wanted:
        found.append(
            f"the manifest's question_sha256 is {manifest.question_sha256[:8] or 'missing'}, "
            f"the records hash to {wanted[:8]}"
        )
    by_id = {record.id: record for record in records}
    listed = {item.id for item in manifest.items}
    for missing in sorted(set(by_id) - listed):
        found.append(f"{missing}: the record is not in the manifest")
    for extra in sorted(listed - set(by_id)):
        found.append(f"{extra}: the manifest lists a text the set does not have")
    for item in manifest.items:
        record = by_id.get(item.id)
        if record is None:
            continue
        if item.text_sha256 and item.text_sha256 != record.text_sha256:
            found.append(f"{record.id}: the manifest's text hash is not the record's")
        stored_source = str(item.source.get("sha256", ""))
        if stored_source and stored_source != sha256_text(record.source):
            found.append(f"{record.id}: the manifest's source hash is not the record's")
    return found


def problems_of_run(
    header: RunHeader,
    answers: Iterable[Answer],
    records_by_id: Mapping[str, TestRecord],
    table: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return what is wrong with one run file against the test set it names.

    Without the set's records only the format is checked: an answer's id and text hash can only be
    compared with texts the caller has.  :func:`notes_of_run` says what was left out.
    """
    found: list[str] = []
    if header.task not in TASKS:
        found.append(f"the run's task {header.task!r} is not one of {', '.join(TASKS)}")
    if not header.input.testset:
        found.append("the run names no test set")
    if records_by_id:
        wanted = question_sha256(list(records_by_id.values()))
        if header.input.question_sha256 != wanted:
            found.append(
                f"the run was asked about another set: its question hash is "
                f"{header.input.question_sha256[:8]}, the set hashes to {wanted[:8]}"
            )
    if header.protocol.provider == OLLAMA and not header.environment.machine:
        if not header.environment.machine_source:
            found.append(
                "an Ollama run names the machine it ran on, or says the machine was assumed"
            )
    found.extend(_protocol_problems(header.protocol.fingerprint, table))
    seen: set[str] = set()
    for answer in answers:
        if answer.text in seen:
            found.append(f"{answer.text}: the text is answered twice")
        seen.add(answer.text)
        record = records_by_id.get(answer.text)
        if record is None:
            if records_by_id:
                found.append(f"{answer.text}: the answer is not a text of the set")
            continue
        if answer.text_sha256 != record.text_sha256:
            found.append(f"{answer.text}: the answer belongs to another question")
    return found


def notes_of_run(testset: str, records_by_id: Mapping[str, TestRecord]) -> list[str]:
    """Return what a run's check could not look at, so a reader knows what was not checked."""
    if not testset or records_by_id:
        return []
    return [f"test set {testset} is not in the store; texts not checked"]


def _protocol_problems(fingerprint: str, table: Mapping[str, Any] | None) -> list[str]:
    """Return what is wrong with a run's protocol fingerprint, if the table does not record it."""
    if not fingerprint:
        return ["the run records no protocol fingerprint"]
    if protocol_table.version_of(fingerprint, table) is None:
        return [
            f"the protocol {fingerprint[:8]} is not in the table, so nobody can say what it used"
        ]
    return []


def problems_of_marks(marks: Iterable[Mark], records_by_id: Mapping[str, TestRecord]) -> list[str]:
    """Return what is wrong with a marks file against the test set it belongs to."""
    found: list[str] = []
    for mark in marks:
        found.extend(_mark_problems(mark))
        record = records_by_id.get(mark.text)
        if record is None:
            found.append(f"{mark.text}: the mark names a text the set does not have")
            continue
        found.extend(_span_problems(record, mark.span, "mark"))
    return found


def _mark_problems(mark: Mark) -> list[str]:
    """Return what is wrong with one mark on its own: its kind, its value and its hash."""
    values = MARK_VALUES.get(mark.kind)
    if values is None:
        return [
            f"{mark.text}: the mark's kind {mark.kind!r} is not one of {', '.join(MARK_VALUES)}"
        ]
    found: list[str] = []
    if mark.value not in values:
        found.append(
            f"{mark.text}: a {mark.kind} mark cannot be {mark.value!r}; "
            f"it is one of {', '.join(values)}"
        )
    if mark.kind == "explanation" and not mark.explanation_sha256:
        found.append(f"{mark.text}: an explanation mark names the explanation it is about")
    if mark.kind == "edit" and mark.explanation_sha256:
        found.append(f"{mark.text}: an edit mark is not about an explanation")
    return found


def unmatched_marks(
    marks: Iterable[Mark],
    answers_by_text: Mapping[str, Sequence[Answer]],
    records_by_id: Mapping[str, TestRecord],
) -> list[Mark]:
    """Return the marks whose edit no stored answer makes any more, so a reader can list them.

    A mark is identified by ``(text, span, replacement)`` in characters, so it survives a tokenizer
    change and only a moved hunk boundary can invalidate it.  The check is what makes that second
    case visible instead of silent.  A spot-check mark also names the explanation it is about, and
    it is matched only when some answer wrote that explanation on that edit.
    """
    found: list[Mark] = []
    for mark in marks:
        record = records_by_id.get(mark.text)
        if record is None:
            found.append(mark)
            continue
        answers = answers_by_text.get(mark.text, ())
        if mark.explanation_sha256:
            if not any(_explains(answer, mark) for answer in answers):
                found.append(mark)
            continue
        made = {
            (span, replacement)
            for answer in answers
            for span, replacement in char_edits(record.source, answer.output)
        }
        if (mark.span, mark.replacement) not in made:
            found.append(mark)
    return found


def _explains(answer: Answer, mark: Mark) -> bool:
    """Return whether *answer*'s review holds *mark*'s explanation on *mark*'s edit."""
    review = answer.review
    if review is None:
        return False
    return any(
        (note.raw_start, note.raw_end, note.corrected)
        == (mark.span[0], mark.span[1], mark.replacement)
        and sha256_text(note.explanation) == mark.explanation_sha256
        for note in review.edits
    )


def checked_testset(records: Sequence[TestRecord], manifest: Manifest | None = None) -> None:
    """Refuse a test set that breaks a rule of its format.

    Raises:
        ValidationError: if :func:`problems_of_testset` finds anything.
    """
    _refuse(problems_of_testset(records, manifest), "the test set")


def checked_run(
    run: RunFile,
    records_by_id: Mapping[str, TestRecord],
    table: Mapping[str, Any] | None = None,
) -> None:
    """Refuse a run file that breaks a rule of its format.

    Raises:
        ValidationError: if :func:`problems_of_run` finds anything.
    """
    _refuse(problems_of_run(run.header, run.answers, records_by_id, table), "the run file")


def checked_marks(marks: Iterable[Mark], records_by_id: Mapping[str, TestRecord]) -> None:
    """Refuse a marks file that breaks a rule of its format.

    Raises:
        ValidationError: if :func:`problems_of_marks` finds anything.
    """
    _refuse(problems_of_marks(marks, records_by_id), "the marks file")


def _refuse(problems: Sequence[str], what: str) -> None:
    """Raise when *problems* is not empty, naming the first few."""
    if problems:
        shown = "; ".join(problems[:5])
        more = f" (and {len(problems) - 5} more)" if len(problems) > 5 else ""
        raise ValidationError(f"{what} is not valid: {shown}{more}")


# ── the whole store ─────────────────────────────────────────────────────────


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


def check_store(root: Path) -> list[Checked]:
    """Return what checking every file of the store at *root* found.

    The corpora are not read here: a caller that named them adds the five-word rule and the hashes
    against the real text, which is :mod:`..input`'s work and not this layer's.
    """
    found: list[Checked] = []
    for name in layout.testsets(root):
        found.append(_check_testset(root, name))
    for stored in reader.read_runs(root):
        found.append(_check_run(stored, root))
    for path in layout.marks_files(root):
        found.append(_check_marks(path, root))
    return found


def check_files(paths: Sequence[Path], root: Path) -> list[Checked]:
    """Return what checking the run files *paths* found, against the store at *root*."""
    return [_check_file(path, root) for path in paths]


def _check_testset(root: Path, name: str) -> Checked:
    """Return what checking one test set of the store found."""
    try:
        records = reader.read_testset(root, name)
        manifest = reader.read_manifest(root, name)
    except (FormatError, OSError) as error:
        return Checked(name=name, problems=(str(error),))
    return Checked(
        name=name,
        problems=tuple(problems_of_testset(records, manifest)),
        counted=f"{len(records)} texts",
    )


def _check_run(stored: reader.StoredRun, root: Path) -> Checked:
    """Return what checking one run of the store found, against its test set."""
    name = stored.run.header.input.testset
    try:
        records = reader.read_testset_by_id(root, name)
    except (FormatError, OSError) as error:
        return Checked(name=stored.name, problems=(str(error),))
    return Checked(
        name=stored.name,
        problems=tuple(problems_of_run(stored.run.header, stored.run.answers, records)),
        counted=f"{len(stored.run.answers)} answers",
        notes=tuple(notes_of_run(name, records)),
    )


def _check_file(path: Path, root: Path) -> Checked:
    """Return what checking one run file named on the command line found."""
    try:
        run = read_run_file(read_jsonl(path), source=str(path))
    except (FormatError, OSError, KeyError, ValueError) as error:
        return Checked(name=path.name, problems=(str(error),))
    records = _records_of(root, run.header.input.testset)
    return Checked(
        name=path.name,
        problems=tuple(problems_of_run(run.header, run.answers, records)),
        counted=f"{len(run.answers)} answers",
        notes=tuple(notes_of_run(run.header.input.testset, records)),
    )


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
        problems=tuple(problems_of_marks(found, records)),
        counted=f"{len(found)} marks",
    )


__all__ = [
    "KINDS",
    "OLLAMA",
    "Checked",
    "ValidationError",
    "check_files",
    "check_store",
    "checked_marks",
    "checked_run",
    "checked_testset",
    "notes_of_run",
    "problems_of_marks",
    "problems_of_record",
    "problems_of_run",
    "problems_of_testset",
    "unmatched_marks",
]
