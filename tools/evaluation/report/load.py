"""The store as a report reads it: every answer scored, and what could not be read.

A report reads two things: the test sets of a store and its run files.  Nothing here computes a
number of its own — the numbers are :func:`~.score.score`'s — but this is the one place a run
file becomes a table: each answer is paired with the record it names, scored, and handed on as a
:class:`LoadedItem`.  A file or an answer that cannot be used is skipped with a reason, so a report
can say "23 / 31 texts" instead of pretending a run is complete.

Two files of one run key are one measurement written twice, and :func:`distinct_runs` keeps one of
them: the other is skipped with the reason, so no table counts a run twice.

**The score cache** is optional and disposable.  Scoring 10,664 answers takes about twenty minutes,
and a report is rebuilt far more often than a run is made, so the numbers are kept in
``<cache>/<run key hash>.scores.jsonl``, one line per answer.  A line is used only when its key
matches the answer, the record and the scorer exactly; anything else is scored again.  A report
never trusts a line it did not just verify, so a stale cache costs time and never a wrong number.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from ..core.axes import run_key_sha256
from ..core.constants import TextKind
from ..core.formats import FormatError
from ..core.hashing import sha256_json
from ..core.manifest import Manifest, ManifestItem
from ..core.performance import Performance, performance_of
from ..core.records import Answer, Protocol, RunFile, RunHeader, TestRecord, text_id
from ..store import layout, reader
from ..store.redacted import (
    EXPORT_NAME,
    ExportManifest,
    RedactedAnswer,
    RedactedRun,
    read_export_manifest,
    read_redacted_file,
)
from .pooled import mode_label
from .score import (
    SCORER_VERSION,
    Scored,
    ScoreOptions,
    score,
    scored_from_dict,
    strip_added_marks,
)

SCORES_SUFFIX = ".scores.jsonl"
"""What a run's score cache file is called, next to the cache directory's other files."""


@dataclass(frozen=True)
class Skipped:
    """One file or answer a report could not use, and the reason it says so."""

    file: str
    reason: str


@dataclass(frozen=True)
class ScoredFields:
    """The facts a table reads from one scored text, whether it came from a run file or an export.

    Every property here delegates to :attr:`scored` or :attr:`header`, so the two sources cannot
    disagree about a number or about what the text was measured under.  What each source adds is
    what it alone holds: a run file the answer and the record, an export the manifest entry.
    """

    scored: Scored
    header: RunHeader

    @property
    def protocol(self) -> Protocol:
        """The protocol the text was asked under."""
        return self.header.protocol

    @property
    def model(self) -> str:
        """The model tag the text was sent to."""
        return self.protocol.model

    @property
    def temperature(self) -> float:
        """The temperature it was asked at."""
        return self.protocol.temperature

    @property
    def mode(self) -> str:
        """How it was asked, as :func:`~.pooled.mode_label` writes it."""
        return mode_label(self.protocol)

    @property
    def scores(self) -> Mapping[str, float]:
        """The numbers of :func:`~..metrics.item_scores.item_scores` and the text-loss counts."""
        return self.scored.scores

    @property
    def true_positives(self) -> int:
        """Changes the text made that the reference makes too, or that the key accepts."""
        return self.scored.true_positives

    @property
    def false_positives(self) -> int:
        """Changes the text made that neither the reference nor the key accepts."""
        return self.scored.false_positives

    @property
    def found(self) -> int:
        """Reference changes the text found."""
        return self.scored.found

    @property
    def false_negatives(self) -> int:
        """Reference changes the text did not find."""
        return self.scored.false_negatives

    @property
    def unchanged(self) -> int:
        """Already-correct sentences that came back untouched."""
        return self.scored.unchanged

    @property
    def marks_added(self) -> bool:
        """The model added Markdown marks the source does not have."""
        return self.scored.marks_added

    @property
    def explained(self) -> int:
        """Changes that carry an explanation."""
        return self.scored.explained

    @property
    def needed(self) -> int:
        """Changes that need one."""
        return self.scored.needed

    @property
    def explanations_known(self) -> bool:
        """Whether the review could be read, so whether the explanation counts mean anything."""
        return self.scored.explanations_known

    @property
    def orphans(self) -> int:
        """Notes that explain no change."""
        return self.scored.orphans

    @property
    def category_checked(self) -> int:
        """Changes whose error category the reference labels."""
        return self.scored.category_checked

    @property
    def category_agreed(self) -> int:
        """Changes whose error category the model named as the reference does."""
        return self.scored.category_agreed

    @property
    def unclear(self) -> int:
        """Expressions the model said a reader cannot follow."""
        return self.scored.unclear

    @property
    def accepted(self) -> int:
        """Changes the key accepts although the reference does not make them."""
        return self.scored.accepted


@dataclass(frozen=True)
class LoadedItem(ScoredFields):
    """One scored answer: the answer, the record it names, and the numbers.

    This is what :class:`~.pooled.TextItem` describes, so a table that shows the text reads a run
    file and not an export.
    """

    answer: Answer
    record: TestRecord
    options: ScoreOptions = field(default_factory=ScoreOptions)
    """What the report asked the scorer differently, so a table reads the same text it scored."""

    @property
    def output(self) -> str:
        """The text the model produced, as the scorer read it: without the marks it added.

        A table that compares the model's changes with the reference's has to start from the same
        text the score did, or the two would disagree about which changes the model made.
        """
        if self.options.count_marks:
            return self.answer.output
        return strip_added_marks(self.record.source, self.answer.output)

    @property
    def language(self) -> str:
        """The language code of the text."""
        return self.record.language

    @property
    def item(self) -> str:
        """The record's id of the text (``de-01``)."""
        return self.record.id

    @property
    def text(self) -> str:
        """The text's id outside its own set file (``de/de-01``), which is what a mark names."""
        return text_id(self.record)

    @property
    def kind(self) -> str:
        """``errors`` (a text with mistakes) or ``correct`` (sentences that must stay untouched)."""
        return self.record.kind

    @property
    def band(self) -> str:
        """The band the recipe drew the text for, or ``""``."""
        return self.record.band

    @property
    def sentences_total(self) -> int:
        """The correct sentences of a ``correct`` text, else 0."""
        return len(self.record.sentences) if self.record.kind == TextKind.CORRECT else 0

    @property
    def failed(self) -> bool:
        """The text could not be proofread at all."""
        return self.answer.failed

    @property
    def fallback(self) -> bool:
        """The answer fell back to corrections only, so the explanations are missing."""
        return self.answer.fallback

    @property
    def seconds(self) -> float:
        """Seconds the text took."""
        return self.answer.seconds

    @property
    def words(self) -> int:
        """Words and marks of the source, as the manifest counts them."""
        return self.record.word_count

    @property
    def performance(self) -> Performance | None:
        """What the text cost on the server, or ``None`` when nothing was measured."""
        return performance_of(self.answer) if self.answer.requests else None


class RunBase:
    """What a report needs from one run, whether it came from a run file or an export.

    The two sources hold the same header and the same file name, so the facts a table reads about a
    run live here once.  What each adds is what it alone holds: a run file its answers, an export
    its manifest.
    """

    path: Path
    run: RunFile | RedactedRun
    items: tuple[Any, ...]
    header: RunHeader
    """The typed header of the run: what it was measured under, and its repeat number."""

    @property
    def name(self) -> str:
        """The file name of the run, which is its label and never read back."""
        return self.path.name

    @property
    def repeat(self) -> int:
        """The repeat number the header records."""
        return self.header.run.repeat

    @property
    def answers(self) -> int:
        """How many answers the run holds, scored or not."""
        return len(self.run.answers)


@dataclass(frozen=True)
class LoadedRun(RunBase):
    """One run of the store, with the answers that could be scored."""

    path: Path
    run: RunFile
    items: tuple[LoadedItem, ...]

    @property
    def header(self) -> RunHeader:
        """The typed header of the run: what it was measured under, and its repeat number."""
        return self.run.header

    @property
    def folder(self) -> str:
        """The folder the run file sits in, ``""`` for a file directly below ``runs/``."""
        return self.path.parent.name if self.path.parent.name != layout.RUNS_DIR else ""


@dataclass(frozen=True)
class Loaded:
    """The store as a report reads it: the runs, the test sets, and what was skipped."""

    root: Path
    runs: tuple[LoadedRun, ...]
    testsets: Mapping[str, tuple[TestRecord, ...]]
    skipped: tuple[Skipped, ...]

    @property
    def items(self) -> tuple[LoadedItem, ...]:
        """Every scored answer of every run, in run order."""
        return tuple(item for run in self.runs for item in run.items)

    @property
    def answers(self) -> int:
        """How many answers were scored."""
        return sum(len(run.items) for run in self.runs)

    def summary(self) -> str:
        """Return one line naming what was read and what was skipped."""
        return (
            f"{len(self.runs)} runs, {self.answers} answers, "
            f"{len(self.testsets)} test sets, {len(self.skipped)} skipped"
        )


def distinct_runs(
    runs: Iterable[LoadedRun],
) -> tuple[tuple[LoadedRun, ...], tuple[tuple[LoadedRun, LoadedRun], ...]]:
    """Return the runs without copies, and the copies with the run that replaced each.

    Two files that hold the same run key are one measurement written twice: a run copied to another
    folder, or started again.  The one with more answers stays, so a run still being written never
    replaces a finished one; of two equally far the newer one by its first session's date, then by
    its name.  The other is reported, so a reader can say why a file of the store does not count.
    """
    ordered = tuple(runs)
    chosen: dict[str, LoadedRun] = {}
    dropped: list[tuple[LoadedRun, LoadedRun]] = []
    for run in ordered:
        key = run_key_sha256(run.header)
        other = chosen.get(key)
        if other is None:
            chosen[key] = run
            continue
        keep, lose = (run, other) if _rank(run) > _rank(other) else (other, run)
        chosen[key] = keep
        dropped.append((lose, keep))
    kept = {id(run) for run in chosen.values()}
    return tuple(run for run in ordered if id(run) in kept), tuple(dropped)


def _rank(run: LoadedRun) -> tuple[int, str, str]:
    """Return what decides which of two files of one run stays: answers, date, name."""
    session = run.run.sessions[0] if run.run.sessions else None
    return (
        len(run.items),
        session.date if session is not None else "",
        run.name,
    )


def load(
    root: Path,
    *,
    testsets: Sequence[str] | None = None,
    folders: Sequence[str] | None = None,
    cache: Path | None = None,
    options: ScoreOptions | None = None,
    progress: Callable[[str], None] | None = None,
) -> Loaded:
    """Return the store at *root* as a report reads it.

    Args:
        root: The store's directory.
        testsets: The test sets to read; every set of the store when not given.
        folders: The run folders to read; every folder when not given.
        cache: Where the score cache lives, or ``None`` to score every answer again.
        options: What the report asks differently from the run's own rules.
        progress: Called with each run's name before its answers are scored, which takes a while.

    Raises:
        FormatError: if a test set or a run file is not of a format this reader knows.
    """
    chosen = options or ScoreOptions()
    wanted = list(testsets) if testsets is not None else _testsets_of(root)
    records: dict[str, dict[str, TestRecord]] = {}
    skipped: list[Skipped] = []
    for name in wanted:
        try:
            records[name] = reader.read_testset_by_id(root, name)
        except (FormatError, FileNotFoundError) as error:
            skipped.append(Skipped(file=name, reason=str(error)))
    runs: list[LoadedRun] = []
    for stored in _runs_of(root, folders, skipped):
        if progress is not None:
            progress(stored.name)
        by_id = records.get(stored.run.header.input.testset)
        if by_id is None:
            skipped.append(
                Skipped(
                    file=stored.name,
                    reason=f"the test set {stored.run.header.input.testset!r} was not read",
                )
            )
            continue
        items = _items_of(stored.run, by_id, skipped, cache=cache, options=chosen)
        if not items:
            skipped.append(
                Skipped(file=stored.name, reason="no answer of this run could be scored")
            )
            continue
        runs.append(LoadedRun(path=stored.path, run=stored.run, items=items))
    kept, copies = distinct_runs(runs)
    for lost, keep in copies:
        skipped.append(
            Skipped(file=lost.name, reason=f"the same run key as {keep.name}, which was kept")
        )
    return Loaded(
        root=root,
        runs=kept,
        testsets={name: tuple(by_id.values()) for name, by_id in records.items()},
        skipped=tuple(skipped),
    )


def load_run(
    root: Path,
    stored: reader.StoredRun,
    *,
    cache: Path | None = None,
    options: ScoreOptions | None = None,
) -> LoadedRun | None:
    """Return one run of the store, scored against its own test set, or ``None`` when it cannot be.

    A command that needs one run — a spot-check sheet, the marks of a filled-in one — reads that run
    and its set only, instead of scoring every run of the store.

    Args:
        root: The store's directory.
        stored: The run file, as the reader found it.
        cache: Where the score cache lives, or ``None`` to score every answer again.
        options: What the report asks differently from the run's own rules.

    Returns:
        The run with its scored items, or ``None`` when its set is missing or no answer could be
        scored.
    """
    chosen = options or ScoreOptions()
    try:
        by_id = reader.read_testset_by_id(root, stored.run.header.input.testset)
    except FormatError, FileNotFoundError:
        return None
    items = _items_of(stored.run, by_id, [], cache=cache, options=chosen)
    if not items:
        return None
    return LoadedRun(path=stored.path, run=stored.run, items=items)


def _testsets_of(root: Path) -> list[str]:
    """Return the names of every test set the store holds."""
    return layout.testsets(root)


def _runs_of(
    root: Path, folders: Sequence[str] | None, skipped: list[Skipped]
) -> list[reader.StoredRun]:
    """Return the runs of *folders*, or of every folder when *folders* is ``None``.

    A file that cannot be read is left out and noted in *skipped*, so one broken file costs its own
    run and not the report.  A file whose last line was cut off mid-write is read without that line
    and noted too: the answers above it are whole.
    """
    paths = (
        layout.run_files(root)
        if folders is None
        else [path for folder in folders for path in layout.run_files(root, folder)]
    )
    found: list[reader.StoredRun] = []
    for path in paths:
        try:
            run = reader.read_run(path)
        except (FormatError, OSError) as error:
            skipped.append(Skipped(file=path.name, reason=str(error)))
            continue
        if run.torn is not None:
            skipped.append(
                Skipped(file=path.name, reason=f"{run.torn}; its answers above it count")
            )
        found.append(reader.StoredRun(path=path, run=run))
    return found


def _items_of(
    run: RunFile,
    by_id: Mapping[str, TestRecord],
    skipped: list[Skipped],
    *,
    cache: Path | None,
    options: ScoreOptions,
) -> tuple[LoadedItem, ...]:
    """Return the scored answers of *run*, skipping the ones that name no text of the set."""
    known = _ScoreCache(cache, run) if cache is not None else None
    items: list[LoadedItem] = []
    for answer in run.answers:
        record = by_id.get(answer.text)
        if record is None:
            skipped.append(
                Skipped(file=run.header.input.testset, reason=f"no text {answer.text!r}")
            )
            continue
        if answer.text_sha256 != record.text_sha256:
            skipped.append(
                Skipped(
                    file=answer.text,
                    reason="the answer was asked about another text than the set holds now",
                )
            )
            continue
        scored = known.get(answer, record, run.header, options) if known is not None else None
        if scored is None:
            scored = score(answer, record, run.header, options)
            if known is not None:
                known.put(answer, record, run.header, options, scored)
        items.append(
            LoadedItem(
                answer=answer,
                record=record,
                scored=scored,
                header=run.header,
                options=options,
            )
        )
    if known is not None:
        known.flush()
    _note_unreadable(run, skipped)
    return tuple(items)


def _note_unreadable(run: RunFile, skipped: list[Skipped]) -> None:
    """Add one entry per review version this tool could not read, so a report says why it is short.

    The answers are kept and scored: their corrections count, and only their explanations are
    unknown.  Saying so once per run is enough; a line per answer would bury the other reasons.
    """
    versions: dict[str, int] = {}
    for answer in run.answers:
        if answer.review_unreadable:
            versions[answer.review_unreadable] = versions.get(answer.review_unreadable, 0) + 1
    for version, count in sorted(versions.items()):
        skipped.append(
            Skipped(
                file=run.header.input.testset,
                reason=(
                    f"{count} reviews of version {version or 'unknown'}, which this tool does not "
                    "read: their explanations are not counted"
                ),
            )
        )


# ── the score cache ──────────────────────────────────────────────────────────


def answer_sha256(answer: Answer, header: RunHeader, options: ScoreOptions) -> str:
    """Return the hash of everything about *answer* that its numbers depend on.

    The output, the review and the outcome flags are the answer's own; the run's steps and the
    report's options are in it too, because the same answer scored under other rules is another
    number.  The text's hash is in it as well: two texts can be answered with the same output, and
    a line keyed by the output alone would serve one text's numbers for the other.  A line whose
    hash does not match is scored again, so a stale cache costs time only.
    """
    return sha256_json(
        {
            "text_sha256": answer.text_sha256,
            "output": answer.output,
            "review": answer.review.to_dict() if answer.review is not None else None,
            "failed": answer.failed,
            "fallback": answer.fallback,
            "unclear_count": answer.unclear_count,
            "left_out": answer.left_out,
            "steps": [step.value for step in header.protocol.steps],
            "count_marks": options.count_marks,
            "accepted": options.accepted,
        }
    )


def key_part_sha256(record: TestRecord) -> str:
    """Return the hash of everything about *record* that an answer's numbers depend on.

    That is the record's key group and nothing else: a corrected reference, a new label or an
    accepted alternative changes the numbers, while the text's own identity does not.
    """
    return record.key_sha256


class _ScoreCache:
    """The score cache of one run: the lines it had, and the lines it will write.

    A cache is read whole and written whole.  A line is kept only when its key matches, so a cache
    written by another scorer, or before a text changed, is replaced rather than trusted.  The lines
    of this scorer are written back with the new ones: a report that scores one more answer must not
    drop the numbers it did not have to compute.
    """

    def __init__(self, directory: Path, run: RunFile) -> None:
        self._path = directory / f"{_run_id(run)}{SCORES_SUFFIX}"
        self._lines: dict[str, dict[str, Any]] = {}
        self._written: dict[str, dict[str, Any]] = {}
        self._dirty = False
        for line in _read_lines(self._path):
            key = str(line.get("answer_sha256", ""))
            if not key:
                continue
            self._lines[key] = line
            if line.get("scorer_version") == SCORER_VERSION:
                self._written[key] = line

    def get(
        self, answer: Answer, record: TestRecord, header: RunHeader, options: ScoreOptions
    ) -> Scored | None:
        """Return the cached numbers of *answer*, or ``None`` when the cache has none to trust."""
        key = answer_sha256(answer, header, options)
        line = self._lines.get(key)
        if line is None:
            return None
        if line.get("key_part_sha256") != key_part_sha256(record):
            return None
        if line.get("scorer_version") != SCORER_VERSION:
            return None
        try:
            return _scored_of(line)
        except KeyError, TypeError, ValueError:
            return None

    def put(
        self,
        answer: Answer,
        record: TestRecord,
        header: RunHeader,
        options: ScoreOptions,
        scored: Scored,
    ) -> None:
        """Remember the numbers of *answer*, so the next report need not compute them again."""
        key = answer_sha256(answer, header, options)
        self._written[key] = {
            "answer_sha256": key,
            "key_part_sha256": key_part_sha256(record),
            "scorer_version": SCORER_VERSION,
            "text": answer.text,
            "scores": scored.to_dict(),
        }
        self._dirty = True

    def flush(self) -> None:
        """Write the cache: the lines it served and the lines it added, in one step."""
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f"{self._path.name}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for key in sorted(self._written):
                handle.write(json.dumps(self._written[key], sort_keys=True, ensure_ascii=False))
                handle.write("\n")
        temporary.replace(self._path)
        self._dirty = False


def _run_id(run: RunFile) -> str:
    """Return the name a run's cache file is called: the hash of its run key."""
    return run_key_sha256(run.header)[:16]


def _read_lines(path: Path) -> list[dict[str, Any]]:
    """Return the lines of a cache file, or nothing when it is not there or not readable."""
    if not path.is_file():
        return []
    lines: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    found = json.loads(line)
                except ValueError:
                    continue
                if isinstance(found, dict):
                    lines.append(found)
    except OSError:
        return []
    return lines


def _scored_of(line: Mapping[str, Any]) -> Scored:
    """Return the numbers a cache line holds.

    Raises:
        KeyError: if the line lacks a count.
        TypeError: if a count is not a number.
        ValueError: if a count is not a whole number.
    """
    return scored_from_dict(line["scores"])


# ── the redacted export ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExportedItem(ScoredFields):
    """One scored text of a redacted export: the numbers, and the manifest entry that places it.

    An export holds no text, so the facts a table needs about the text itself — its kind, its band
    and how many words it has — come from the manifest the export copied, and the numbers from the
    redacted answer.  This is what :class:`~.pooled.ItemFacts` describes, and not what
    :class:`~.pooled.TextItem` does: a table that shows the text cannot be built from an export.
    """

    answer: RedactedAnswer
    entry: ManifestItem

    @property
    def language(self) -> str:
        """The language code of the text."""
        return self.entry.language

    @property
    def item(self) -> str:
        """The record's id of the text (``de-01``)."""
        return self.entry.id

    @property
    def kind(self) -> str:
        """``errors`` (a text with mistakes) or ``correct`` (sentences that must stay untouched)."""
        return self.entry.kind

    @property
    def band(self) -> str:
        """The band the recipe drew the text for, or ``""``."""
        return self.entry.band

    @property
    def sentences_total(self) -> int:
        """The correct sentences of a ``correct`` text, else 0."""
        return len(self.entry.sentence_sha256) if self.kind == TextKind.CORRECT else 0

    @property
    def failed(self) -> bool:
        """The text could not be proofread at all."""
        return self.answer.failed

    @property
    def fallback(self) -> bool:
        """The answer fell back to corrections only, so the explanations are missing."""
        return self.answer.fallback

    @property
    def seconds(self) -> float:
        """Seconds the text took."""
        return self.answer.seconds

    @property
    def words(self) -> int:
        """Words and marks of the source, as the manifest counts them."""
        return int(self.entry.source.get("words", 0))

    @property
    def performance(self) -> Performance | None:
        """What the text cost on the server, or ``None`` when nothing was measured."""
        return performance_of(self.answer) if self.answer.requests else None


@dataclass(frozen=True)
class ExportedRun(RunBase):
    """One redacted run of an export, with the answers the manifest could place."""

    path: Path
    run: RedactedRun
    items: tuple[ExportedItem, ...]

    @property
    def header(self) -> RunHeader:
        """The run's own header; the export's two extra facts are on :attr:`run`."""
        return self.run.header.header


@dataclass(frozen=True)
class Exported:
    """A redacted export as a report reads it: the runs, the export's manifest, and what was skipped."""

    root: Path
    manifest: ExportManifest
    runs: tuple[ExportedRun, ...]
    skipped: tuple[Skipped, ...]

    @property
    def items(self) -> tuple[ExportedItem, ...]:
        """Every scored answer of every run, in run order."""
        return tuple(item for run in self.runs for item in run.items)

    @property
    def answers(self) -> int:
        """How many answers were placed."""
        return sum(len(run.items) for run in self.runs)

    def summary(self) -> str:
        """Return one line naming what was read and what was skipped."""
        return (
            f"{len(self.runs)} runs, {self.answers} answers, "
            f"{len(self.manifest.testsets)} test sets, {len(self.skipped)} skipped"
        )


def load_export(root: Path, *, testsets: Sequence[str] | None = None) -> Exported:
    """Return the redacted export at *root* as a report reads it.

    Nothing is scored here: the numbers are the ones the export stored, and the only work is
    placing each answer in the manifest the export copied.  A run whose set, key or scorer the
    export does not hold is skipped with a reason, so a report never mixes numbers that were
    measured against different keys.

    Args:
        root: The export's directory, the one that holds ``export.json``.
        testsets: The test sets to read; every set of the export when not given.

    Raises:
        FormatError: if the directory is not an export, or a manifest or run file is unreadable.
    """
    try:
        manifest = read_export_manifest(root / EXPORT_NAME)
    except FileNotFoundError as error:
        raise FormatError(f"{root}: not an export, there is no {EXPORT_NAME}") from error
    wanted = list(testsets) if testsets is not None else sorted(manifest.testsets)
    entries: dict[str, dict[str, ManifestItem]] = {}
    skipped: list[Skipped] = []
    for name in wanted:
        try:
            entries[name] = _entries_of(reader.read_manifest(root, name))
        except FormatError as error:
            skipped.append(Skipped(file=name, reason=str(error)))
    runs: list[ExportedRun] = []
    for path in layout.run_files(root):
        try:
            run = read_redacted_file(path)
        except FormatError as error:
            skipped.append(Skipped(file=path.name, reason=str(error)))
            continue
        reason = manifest.check(run)
        if reason:
            skipped.append(Skipped(file=path.name, reason=reason))
            continue
        by_text = entries.get(run.header.header.input.testset)
        if by_text is None:
            skipped.append(
                Skipped(
                    file=path.name,
                    reason=f"the test set {run.header.header.input.testset!r} was not read",
                )
            )
            continue
        items = _exported_items(run, by_text, skipped)
        if not items:
            skipped.append(Skipped(file=path.name, reason="no answer of this run could be placed"))
            continue
        runs.append(ExportedRun(path=path, run=run, items=items))
    return Exported(root=root, manifest=manifest, runs=tuple(runs), skipped=tuple(skipped))


def _entries_of(manifest: Manifest) -> dict[str, ManifestItem]:
    """Return a manifest's items keyed ``<language>/<id>``, which is how an answer finds its text."""
    return manifest.by_text()


def _exported_items(
    run: RedactedRun, by_text: Mapping[str, ManifestItem], skipped: list[Skipped]
) -> tuple[ExportedItem, ...]:
    """Return the placed answers of *run*, skipping the ones the manifest does not hold."""
    items: list[ExportedItem] = []
    for answer in run.answers:
        entry = by_text.get(answer.text)
        if entry is None:
            skipped.append(
                Skipped(file=run.header.header.input.testset, reason=f"no text {answer.text!r}")
            )
            continue
        try:
            scored = scored_from_dict(answer.scores)
        except (KeyError, TypeError, ValueError) as error:
            skipped.append(
                Skipped(file=answer.text, reason=f"the numbers are incomplete ({error})")
            )
            continue
        items.append(
            ExportedItem(
                answer=answer,
                entry=entry,
                scored=scored,
                header=run.header.header,
            )
        )
    return tuple(items)


__all__ = [
    "SCORES_SUFFIX",
    "Exported",
    "ExportedItem",
    "ExportedRun",
    "Loaded",
    "LoadedItem",
    "LoadedRun",
    "Skipped",
    "answer_sha256",
    "distinct_runs",
    "key_part_sha256",
    "load",
    "load_export",
    "load_run",
]
