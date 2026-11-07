"""The typed records the writing-model tools read and write, and their versioned JSONL format.

The results were plain dicts whose keys depended on the kind of row, and the model plan wants to
publish them, so the schema is explicit and carries a version.  ``schema_version: 1`` rows are
written by :func:`write_rows`; a line without a version is the ``plans/``-era v0 row and is
mapped on read.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field, fields
import json
from pathlib import Path

from teachers_teammate.domain.proofreading_quality import score_edits, unchanged_sentences

SCHEMA_VERSION = 1
"""Version of the JSONL lines this module writes."""

_MARK_MARKS = ("**", "__", "~~")
"""Markdown marks some models add to "highlight" their changes; not part of the text."""


@dataclass(frozen=True)
class TestRecord:
    """One test case: a learner text and what the reference correction makes of it."""

    id: str
    language: str
    kind: str
    """``"errors"`` (a text with mistakes, with a reference correction) or ``"correct"``."""

    origin: str
    """Where the text comes from; kept so a licence question can be answered later."""

    source: str
    reference: str = ""
    """The reference correction; empty for a ``"correct"`` text."""

    sentences: tuple[str, ...] = ()
    """The already-correct sentences of a ``"correct"`` text, to check they stay untouched."""

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record, tagged with :data:`SCHEMA_VERSION`."""
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, record: dict) -> TestRecord:
        """Build a record from :meth:`to_dict` output (a missing version counts as v0)."""
        version = record.get("schema_version", 0)
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported test record schema_version {version!r}")
        values = {name: record[name] for name in _field_names(cls) if name in record}
        if "sentences" in values:
            values["sentences"] = tuple(values["sentences"])
        return cls(**values)


@dataclass(frozen=True)
class ResultRow:
    """One model's answer for one test case, with everything needed to re-score it."""

    id: str
    model: str
    temperature: float
    language: str
    kind: str
    origin: str
    seconds: float
    failed: bool
    fallback: bool
    has_review: bool
    source: str
    output: str
    warning: str = ""
    reference: str = ""
    notes: tuple[dict, ...] = ()
    """The review's notes as plain dicts, for the spot-check sheet."""

    explained: int = 0
    needed: int = 0
    orphans: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    sentences: tuple[str, ...] = ()
    unchanged: int = 0
    raw_output: str = field(default="", compare=False)
    """The output as it was before re-scoring dropped added Markdown marks."""

    def to_dict(self) -> dict:
        """Return a JSON-serialisable row, tagged with :data:`SCHEMA_VERSION`."""
        record = asdict(self)
        record.pop("raw_output", None)
        return {"schema_version": SCHEMA_VERSION, **record}

    @classmethod
    def from_dict(cls, record: dict) -> ResultRow:
        """Build a row from a stored line, migrating a v0 line when it carries no version."""
        version = record.get("schema_version", 0)
        if version not in (0, SCHEMA_VERSION):
            raise ValueError(f"unsupported result schema_version {version!r}")
        values = {name: record[name] for name in _field_names(cls) if name in record}
        for name in ("notes", "sentences"):
            if name in values:
                values[name] = tuple(values[name])
        values.pop("raw_output", None)
        return cls(**values)


def _field_names(cls: type) -> tuple[str, ...]:
    """Return the names of the dataclass fields of *cls*."""
    return tuple(entry.name for entry in fields(cls))


def read_rows(path: Path) -> list[ResultRow]:
    """Return every row stored in *path* (JSONL), rejecting an unknown schema version."""
    with path.open(encoding="utf-8") as handle:
        return [ResultRow.from_dict(json.loads(line)) for line in handle if line.strip()]


def read_all(out: Path) -> list[ResultRow]:
    """Return the rows of every results file below *out*, in file-name order."""
    rows: list[ResultRow] = []
    for path in sorted(out.glob("*.jsonl")):
        rows.extend(read_rows(path))
    return rows


def write_rows(path: Path, rows: Iterable[ResultRow]) -> None:
    """Append *rows* to *path*, one JSON object per line, so an interrupted run resumes."""
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            _write(handle, row.to_dict())


def write_records(path: Path, records: Iterable[TestRecord]) -> None:
    """Write *records* to *path*, one JSON object per line, replacing the file."""
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            _write(handle, record.to_dict())


def read_records(path: Path) -> list[TestRecord]:
    """Return the test records stored in *path*."""
    with path.open(encoding="utf-8") as handle:
        return [TestRecord.from_dict(json.loads(line)) for line in handle if line.strip()]


def read_done_ids(path: Path) -> set[str]:
    """Return the ids already stored in *path*, so an interrupted run can resume."""
    if not path.exists():
        return set()
    return {row.id for row in read_rows(path)}


def _write(handle, record: dict) -> None:
    """Write one JSON object as a line."""
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def added_marks(row: ResultRow) -> bool:
    """Whether the output carries Markdown marks the source does not.

    A model that "highlights" its changes puts literal asterisks into the text a teacher
    receives; the structured-output check does not see it.
    """
    output = row.raw_output or row.output
    marks = (*_MARK_MARKS, "[unclear]")
    return any(mark in output and mark not in row.source for mark in marks)


def without_added_marks(row: ResultRow) -> str:
    """Return the output without Markdown marks the source does not contain.

    Scoring ignores that by default (``report --count-marks`` counts it): only the words matter.
    """
    output = row.output
    for mark in _MARK_MARKS:
        if mark not in row.source:
            output = output.replace(mark, "")
    return output


def rescore(row: ResultRow, *, count_marks: bool) -> ResultRow:
    """Return *row* with its scores recomputed from the stored texts.

    The results files keep source, reference and output, so a change of the scoring rules needs
    no new model run: the report always recomputes instead of trusting the stored numbers.
    """
    from dataclasses import replace  # noqa: PLC0415  # keeps the import list small

    output = row.output if count_marks else without_added_marks(row)
    updated = replace(row, output=output, raw_output=row.raw_output or row.output)
    if row.kind != "errors":
        return replace(updated, unchanged=unchanged_sentences(list(row.sentences), output))
    score = score_edits(row.source, output, row.reference)
    return replace(
        updated,
        true_positives=score.true_positives,
        false_positives=score.false_positives,
        false_negatives=score.false_negatives,
    )


def rows_by_model(rows: Iterable[ResultRow]) -> Iterator[tuple[tuple[str, float], list[ResultRow]]]:
    """Group *rows* by ``(model, temperature)``, keeping the results sorted."""
    groups: dict[tuple[str, float], list[ResultRow]] = {}
    for row in rows:
        groups.setdefault((row.model, row.temperature), []).append(row)
    yield from sorted(groups.items())
