"""The test set and the local run caches a report is built from.

A report reads two things: the texts of a test set (``<language>.jsonl``, one
:class:`~..results.TestRecord` per line) and the runs of one or more local caches, each a
``<name>.jsonl`` of result rows next to a ``<name>.header.json``.  Nothing here computes a number;
this module only reads, checks that a row belongs to the text it names, and says what it skipped
and why, so the page can show "23 / 31 texts" instead of pretending a run is complete.

A cache is written while a run is going, so a file may end in a half-written line: that line is
skipped with a reason and the rows before it are kept.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .. import results, session
from ..grouping import GroupKey
from ..redaction import word_count
from ..results import ResultRow, TestRecord
from ..run_format import RunHeader

HEADER_SUFFIX = ".header.json"
"""What a run's header file is called, next to its rows."""


@dataclass(frozen=True)
class Skipped:
    """One file or row a report could not use, and the reason it says so."""

    file: str
    reason: str


@dataclass(frozen=True)
class CacheRun:
    """One results file of a local cache: its header facts and the rows that could be read."""

    path: Path
    header: Mapping[str, Any]
    rows: tuple[ResultRow, ...]

    @property
    def name(self) -> str:
        """Return the file name, which is what a skip message and the footer show."""
        return self.path.name

    @property
    def repeat(self) -> int:
        """Return the repeat number the header records."""
        return int(self.header["repeat"])

    @property
    def meta(self) -> RunHeader:
        """Return the typed header, so a cache run reads like a committed one.

        Raises:
            KeyError: if the header lacks a field the readers need.
            ValueError: if a field has a value of the wrong kind.
        """
        return RunHeader.from_dict(self.header)


@dataclass(frozen=True)
class Cache:
    """One cache directory: the runs it holds, and what of it was skipped."""

    directory: Path
    runs: tuple[CacheRun, ...]
    skipped: tuple[Skipped, ...]

    @property
    def rows(self) -> int:
        """Return how many rows the cache's runs hold together."""
        return sum(len(run.rows) for run in self.runs)


@dataclass(frozen=True)
class TestSet:
    """The texts a report is about, keyed ``language/id`` as a result row names them."""

    directory: Path
    records: tuple[TestRecord, ...]
    by_key: Mapping[str, TestRecord]
    sha256: str = ""
    """The hash of every record of the set on disk, which a run's ``testset_sha256`` names."""

    known: frozenset[str] = frozenset()
    """The keys of every text of the set on disk, also of those a language filter left out.

    A row of a text that is known but not shown is not a problem of the cache, so it is left out
    without a skip message."""

    @property
    def languages(self) -> tuple[str, ...]:
        """Return the language codes the set holds, in the order of the table."""
        return tuple(dict.fromkeys(record.language for record in self.records))

    @property
    def words(self) -> int:
        """Return the words and marks of every text, as the manifest counts them."""
        return sum(word_count(record.source) for record in self.records)


def read_testset(directory: Path) -> TestSet:
    """Return the test set stored in *directory*.

    Raises:
        ValueError: if the directory holds no ``<language>.jsonl`` file.
    """
    codes = results.testset_languages(directory)
    if not codes:
        raise ValueError(f"{directory}: no <language>.jsonl test records")
    records = tuple(results.read_testset(directory, codes))
    by_key = {results.record_key(record): record for record in records}
    return TestSet(
        directory=directory,
        records=records,
        by_key=by_key,
        sha256=results.records_sha256(records),
        known=frozenset(by_key),
    )


def read_cache(
    directory: Path, testset: TestSet, progress: Callable[[str], None] | None = None
) -> Cache:
    """Return the runs of the cache in *directory*, checked against *testset*.

    A file is skipped whole when it has no header, when its header names another test set or the
    hash of other texts than the ones on disk, or when it holds no row that belongs to this one.
    A single row is skipped when the test set has no such text, when the row's text or reference
    is not the test set's, or when the row was asked in another mode than its header says.  Every
    row that is kept is scored again from its stored texts, as ``proofreading_store report`` does,
    so the report never shows a number the stored row computed under older rules.

    ``progress`` is called with each file's name before its rows are scored, which takes a while.

    Raises:
        ValueError: if *directory* is not a directory.
    """
    if not directory.is_dir():
        raise ValueError(f"{directory}: not a directory")
    skipped: list[Skipped] = []
    runs: list[CacheRun] = []
    for path in sorted(directory.glob("*.jsonl")):
        header = _read_header(path, skipped)
        if header is None:
            continue
        if str(header.get("testset", "")) != testset.directory.name:
            skipped.append(
                Skipped(
                    file=path.name,
                    reason=f"the run is of test set {header.get('testset')!r}, "
                    f"not {testset.directory.name!r}",
                )
            )
            continue
        problem = _header_problem(header)
        if problem:
            skipped.append(Skipped(file=path.name, reason=problem))
            continue
        stored_hash = str(header.get("testset_sha256", ""))
        if stored_hash and testset.sha256 and stored_hash != testset.sha256:
            skipped.append(
                Skipped(
                    file=path.name,
                    reason=f"the run was asked about other texts than {testset.directory.name!r} "
                    f"holds now (hash {stored_hash[:8]}, the set is {testset.sha256[:8]})",
                )
            )
            continue
        if progress is not None:
            progress(path.name)
        rows = _read_rows(path, header, testset, skipped)
        if not rows:
            skipped.append(Skipped(file=path.name, reason="no row of this test set could be read"))
            continue
        runs.append(CacheRun(path=path, header=header, rows=rows))
    return Cache(directory=directory, runs=tuple(runs), skipped=tuple(skipped))


def _header_problem(header: Mapping[str, Any]) -> str:
    """Return why the header cannot name a variant and a repeat, or ``""`` when it can.

    A cache written by an older tool lacks fields the group key is made of; such a run is skipped
    with this reason instead of stopping the report.
    """
    try:
        GroupKey.of_local(header)
        int(header["repeat"])
    except KeyError as error:
        return f"the header lacks {error}"
    except (TypeError, ValueError) as error:
        return f"the header has a value of the wrong kind: {error}"
    return ""


def _read_header(path: Path, skipped: list[Skipped]) -> dict | None:
    """Return the last session of *path*'s header file, or ``None`` when there is none to use.

    A run appends one header per session, so the file is a list; the last entry is the session
    that wrote the rows that are there now.  Sessions that disagree on what the texts mean
    (:func:`..session.session_conflicts`, the check the export makes) are not one run, so the
    file is skipped.  A single object is read as it is, because a cache written by hand or by an
    older tool has one.
    """
    header_path = path.with_suffix(HEADER_SUFFIX)
    if not header_path.is_file():
        skipped.append(Skipped(file=path.name, reason=f"no {header_path.name} next to it"))
        return None
    try:
        text = header_path.read_text(encoding="utf-8")
        stored = json.loads(text)
        entries = session.read_sessions(header_path) if isinstance(stored, list) else [stored]
    except (OSError, ValueError) as error:
        skipped.append(Skipped(file=path.name, reason=f"{header_path.name}: {error}"))
        return None
    if not all(isinstance(entry, dict) for entry in entries):
        skipped.append(Skipped(file=path.name, reason=f"{header_path.name} is not a header"))
        return None
    conflicts = session.session_conflicts(entries)
    if conflicts:
        skipped.append(
            Skipped(
                file=path.name,
                reason="the sessions of this run disagree, so it is not one run: "
                + "; ".join(conflicts),
            )
        )
        return None
    return entries[-1]


def _read_rows(
    path: Path, header: Mapping[str, Any], testset: TestSet, skipped: list[Skipped]
) -> tuple[ResultRow, ...]:
    """Return the rows of *path* that belong to *testset*, skipping the rest with a reason.

    A row of a text the set holds but a filter left out is dropped without a message.  The rows
    that are kept are scored again (:func:`~..results.rescore`).
    """
    rows: list[ResultRow] = []
    try:
        # Iterate the handle, which splits on newlines only: str.splitlines() would also cut a
        # JSON string at U+2028, U+0085 or a form feed, which the writer leaves unescaped.
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = ResultRow.from_dict(json.loads(line))
                except (ValueError, KeyError, TypeError) as error:
                    skipped.append(Skipped(file=path.name, reason=f"line {number}: {error}"))
                    continue
                if row.id in testset.known and row.id not in testset.by_key:
                    continue
                problem = _row_problem(row, header, testset)
                if problem:
                    skipped.append(Skipped(file=path.name, reason=f"line {number}: {problem}"))
                    continue
                rows.append(results.rescore(row, count_marks=False, record=testset.by_key[row.id]))
    except OSError as error:
        skipped.append(Skipped(file=path.name, reason=str(error)))
        return ()
    return tuple(rows)


def _row_problem(row: ResultRow, header: Mapping[str, Any], testset: TestSet) -> str:
    """Return why *row* cannot be shown against *testset*, or ``""`` when it can."""
    record = testset.by_key.get(row.id)
    if record is None:
        return f"the test set has no text {row.id!r}"
    if row.source != record.source:
        return f"the row's text is not the test set's {row.id!r}"
    if row.reference != record.reference:
        return f"the row's reference is not the test set's for {row.id!r}"
    if row.mode.value != str(header.get("mode", row.mode.value)):
        return f"the row was asked in {row.mode.value!r}, its header says {header.get('mode')!r}"
    return ""


def read_caches(directories: Sequence[Path], testset: TestSet) -> tuple[Cache, ...]:
    """Return the cache of each directory in *directories*, in the order given."""
    return tuple(read_cache(directory, testset) for directory in directories)


__all__ = [
    "HEADER_SUFFIX",
    "Cache",
    "CacheRun",
    "Skipped",
    "TestSet",
    "read_cache",
    "read_caches",
    "read_testset",
]
