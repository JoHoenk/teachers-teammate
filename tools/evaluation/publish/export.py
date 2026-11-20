"""The redacted export: a local run becomes a committed run file, and the index is regenerated.

A local run file holds the model's output, which is corpus text and may not be committed.  So a run
is *exported*: the model's edits are stored as character positions plus capped replacements, the
notes as categories plus capped explanations, and every outcome as numbers.  What stays is checkable
(question hash, key hash, scorer version, digest, seed, machine) and re-scorable, without a corpus
sentence in the file.

The numbers are :func:`~..report.score.score`'s, computed by the report loader, so the export and
a report of the local store cannot disagree.  The caps are :mod:`..store.redaction`'s, and the file
shape is :mod:`..store.redacted`'s; this module only decides what goes where.

An export is a **plan** first: one entry per run saying what would happen to it, and nothing is
written while any entry is refused.  A committed run file is written once and never merged, so a
second export of the same run says ``unchanged``; a run that grew since it was written is
``replace``; a run whose numbers were measured against another key or another scorer is ``stale``
until ``--rescore``.  The two exceptions are derived data: a copied manifest is replaced when the
set's ``key_version`` rises, and the export's own manifest is merged, so the sets of an earlier
export stay.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..core.axes import date_of, run_file_name, run_key_sha256
from ..core.hashing import file_sha256
from ..core.manifest import Manifest, read_manifest, write_manifest
from ..core.records import RunHeader, TestRecord, key_sha256, question_sha256, text_id
from ..input.corpora import adapter_class
from ..input.testset import Corpora
from ..metrics.quality import char_edits
from ..report.load import Loaded, LoadedItem, LoadedRun, load
from ..report.score import SCORER_VERSION, notes_of
from ..store import layout
from ..store.redacted import (
    EXPORT_NAME,
    ExportedSet,
    ExportManifest,
    RedactedAnswer,
    RedactedHeader,
    RedactedRun,
    merge_export_manifest,
    read_redacted_file,
    write_redacted,
)
from ..store.redaction import Redactor, field_is_cut
from .index import write_index

WRITE = "write"
"""The run file is not there yet."""

UNCHANGED = "unchanged"
"""The committed file holds exactly this run, so nothing is written."""

REPLACE = "replace"
"""The committed file is replaced: the run grew, or ``--rescore`` was given."""

STALE = "stale"
"""The committed file's numbers were measured against another key or another scorer."""

REFUSED = "refused"
"""Writing the run would lose something, so the whole export stops."""

SKIPPED = "skipped"
"""The run is incomplete and ``--partial`` was not given."""

ACTIONS: tuple[str, ...] = (WRITE, UNCHANGED, REPLACE, STALE, REFUSED, SKIPPED)
"""What an export can do with one run."""


@dataclass(frozen=True)
class PlanEntry:
    """What an export will do with one run, and why."""

    run: LoadedRun
    action: str
    name: str
    """The file name the run is written under."""
    reason: str = ""
    """Why the action is what it is, for the line a command prints."""
    redacted: RedactedRun | None = None
    """The file to write; ``None`` for a run the export leaves alone."""
    cut_fields: int = 0
    """Fields the caps had to cut (hashed replacements, withheld explanations)."""

    def line(self) -> str:
        """Return the entry as the one line a command prints."""
        detail = f" ({self.reason})" if self.reason else ""
        return f"{self.action}{detail}: {self.name}"


@dataclass(frozen=True)
class Plan:
    """What an export would do: one entry per run, and what pins each test set."""

    entries: tuple[PlanEntry, ...]
    sets: Mapping[str, ExportedSet]

    @property
    def refused(self) -> tuple[PlanEntry, ...]:
        """The entries the export cannot write, which stop the whole export."""
        return tuple(entry for entry in self.entries if entry.action == REFUSED)

    def lines(self) -> list[str]:
        """Return one line per entry, in run order."""
        return [entry.line() for entry in self.entries]


def plan(
    loaded: Loaded,
    out: Path,
    *,
    corpora: Corpora | None = None,
    label: str = "",
    rescore: bool = False,
    partial: bool = False,
) -> Plan:
    """Return what an export of *loaded* into *out* would do, writing nothing.

    Args:
        loaded: The store as :func:`~..report.load.load` read it.
        out: The repository directory (``evaluations/proofreading``).
        corpora: The corpora, needed only when a restricted corpus is in the set: its items store
            no words, and its key is what their hashes are made with.
        label: Appended to the file name, for two runs of one configuration.
        rescore: Replace a committed run file whose numbers were measured against another key or
            another scorer; without it, such a run is listed and left alone.
        partial: Export a run that holds fewer answers than its test set has texts.

    Raises:
        ValueError: if a copied manifest exists already with another set or a key that moved
            without a version, or a restricted corpus is in the set and *corpora* does not hold it.
    """
    sets = _check_testsets(loaded.root, out, loaded.testsets)
    redactors: dict[str, Redactor] = {}
    entries: list[PlanEntry] = []
    named: dict[str, LoadedRun] = {}
    for run in loaded.runs:
        name = _file_name(run.header, run, label)
        other = named.get(name)
        if other is not None:
            entries.append(
                PlanEntry(
                    run=run,
                    action=REFUSED,
                    name=name,
                    reason=f"the same name as {other.name}; use --label to tell two runs apart",
                )
            )
            continue
        named[name] = run
        entries.append(
            _plan_run(
                run,
                out,
                loaded,
                sets,
                corpora,
                redactors,
                name,
                rescore=rescore,
                partial=partial,
            )
        )
    return Plan(entries=tuple(entries), sets=sets)


def export(  # noqa: PLR0913  # one argument per fact the export needs
    root: Path,
    *,
    out: Path,
    testsets: Sequence[str] | None = None,
    folders: Sequence[str] | None = None,
    cache: Path | None = None,
    corpora: Corpora | None = None,
    label: str = "",
    rescore: bool = False,
    partial: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Plan:
    """Write every run of the store at *root* as a redacted run file below *out*; write the index.

    Args:
        root: The local store's directory.
        out: The repository directory (``evaluations/proofreading``).
        testsets: The test sets to export; every set of the store when not given.
        folders: The run folders to export; every folder when not given.
        cache: Where the score cache lives, or ``None`` to score every answer again.
        corpora: The corpora, needed only when a restricted corpus is in the set: its items store
            no words, and its key is what their hashes are made with.
        label: Appended to the file name, for two runs of one configuration.
        rescore: Replace a committed run file whose numbers were measured against another key or
            another scorer; without it, such a run is listed and left alone.
        partial: Export a run that holds fewer answers than its test set has texts.
        progress: Called with each run's name before its answers are scored, which takes a while.

    Returns:
        The plan that was carried out: one entry per run, with the action taken.

    Raises:
        ValueError: if any entry is refused, naming every one of them; nothing is written then.
    """
    loaded = load(root, testsets=testsets, folders=folders, cache=cache, progress=progress)
    found = plan(loaded, out, corpora=corpora, label=label, rescore=rescore, partial=partial)
    if found.refused:
        raise ValueError("; ".join(f"{entry.name}: {entry.reason}" for entry in found.refused))
    _write_testsets(root, out, loaded.testsets)
    try:
        for entry in found.entries:
            if entry.redacted is not None:
                _write_run(entry.name, entry.redacted, out)
    finally:
        # The index lists what is on disk, so it is written even when a run file failed: a run
        # file without an index entry is a file nobody finds.
        merge_export_manifest(
            out / EXPORT_NAME,
            ExportManifest(
                exported=_today(),
                scorer_version=SCORER_VERSION,
                testsets=dict(found.sets),
            ),
        )
        write_index(out)
    return found


def _today() -> str:
    """Return today's date, which is the day the export was made and not the day of the run."""
    from datetime import date  # noqa: PLC0415  # only the export needs the clock

    return date.today().isoformat()


def _plan_run(  # noqa: PLR0913  # one argument per fact the decision needs
    run: LoadedRun,
    out: Path,
    loaded: Loaded,
    sets: Mapping[str, ExportedSet],
    corpora: Corpora | None,
    redactors: dict[str, Redactor],
    name: str,
    *,
    rescore: bool,
    partial: bool,
) -> PlanEntry:
    """Return what the export does with one run, and the file it would write."""
    testset = run.header.input.testset
    records = loaded.testsets.get(testset)
    entry = sets.get(testset)
    if records is None or entry is None:
        return PlanEntry(
            run=run,
            action=REFUSED,
            name=name,
            reason=f"the test set {testset!r} was not read",
        )
    if not partial and run.answers < len(records):
        return PlanEntry(
            run=run,
            action=SKIPPED,
            name=name,
            reason=f"incomplete ({run.answers} of {len(records)} texts); use --partial",
        )
    redacted, cut = _redacted_run(run, entry, corpora, redactors)
    target = layout.run_path(out, name)
    if not target.is_file():
        return PlanEntry(run=run, action=WRITE, name=name, redacted=redacted, cut_fields=cut)
    committed = read_redacted_file(target)
    if run_key_sha256(committed.header.header) != run_key_sha256(run.header):
        return PlanEntry(
            run=run,
            action=REFUSED,
            name=name,
            reason="the run exists already; use --label to tell two runs apart",
        )
    if _stale(committed, redacted):
        if not rescore:
            return PlanEntry(
                run=run,
                action=STALE,
                name=name,
                reason="another key or another scorer; use --rescore",
            )
        return PlanEntry(
            run=run,
            action=REPLACE,
            name=name,
            reason="rescore",
            redacted=redacted,
            cut_fields=cut,
        )
    if committed.lines() == redacted.lines():
        return PlanEntry(run=run, action=UNCHANGED, name=name)
    if _is_prefix(committed.answers, redacted.answers):
        return PlanEntry(
            run=run,
            action=REPLACE,
            name=name,
            reason="grew",
            redacted=redacted,
            cut_fields=cut,
        )
    return PlanEntry(
        run=run,
        action=REFUSED,
        name=name,
        reason="the committed file holds answers this run does not",
    )


def _stale(committed: RedactedRun, redacted: RedactedRun) -> bool:
    """Whether the committed numbers were measured against another key or another scorer.

    The key is compared per text, so a key that moved for one text makes the runs that hold that
    text stale and leaves the others alone.
    """
    if committed.scorer_version != redacted.scorer_version:
        return True
    local = {answer.text: answer.key_part_sha256 for answer in redacted.answers}
    return any(answer.key_part_sha256 != local.get(answer.text) for answer in committed.answers)


def _is_prefix(committed: Sequence[RedactedAnswer], new: Sequence[RedactedAnswer]) -> bool:
    """Whether *committed* is the first answers of *new*, so the run only grew."""
    if len(committed) >= len(new):
        return False
    return all(one.to_dict() == other.to_dict() for one, other in zip(committed, new, strict=False))


def _write_run(name: str, redacted: RedactedRun, out: Path) -> None:
    """Write one run file, creating the folder it sits in."""
    target = layout.run_path(out, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_redacted(target, redacted)


def _check_testsets(
    root: Path, out: Path, testsets: Mapping[str, Sequence[TestRecord]]
) -> dict[str, ExportedSet]:
    """Return what pins each set, refusing a committed manifest that pins another set.

    The two manifests are compared by what they pin, not by their bytes: another ``question_sha256``
    is another set and is refused (a committed set is never edited, so a rebuilt set needs a new
    id); the same question with a higher ``key_version`` replaces the committed manifest, because
    the same runs are scored against a better key; a lower or equal ``key_version`` with another
    ``key_sha256`` is refused, because the key moved without saying so.
    """
    copied: dict[str, ExportedSet] = {}
    for name, records in sorted(testsets.items()):
        source = layout.manifest_path(root, name)
        target = layout.manifest_path(out, name)
        if target.exists():
            _check_manifest_change(target, read_manifest(source))
        copied[name] = ExportedSet(
            testset=name,
            manifest_sha256=file_sha256(source),
            question_sha256=question_sha256(list(records)),
            key_sha256=key_sha256(list(records)),
            texts=len(records),
        )
    return copied


def _write_testsets(root: Path, out: Path, testsets: Mapping[str, Sequence[TestRecord]]) -> None:
    """Copy each set's manifest to *out*, byte for byte, so a run header still matches it."""
    for name in sorted(testsets):
        write_manifest(
            layout.manifest_path(out, name), read_manifest(layout.manifest_path(root, name))
        )


def _check_manifest_change(target: Path, source: Manifest) -> None:
    """Refuse a committed manifest that pins another set, or a key that moved without a version.

    Raises:
        ValueError: if the committed manifest is another set, or its key moved without a higher
            ``key_version``.
    """
    committed = read_manifest(target)
    if committed.question_sha256 != source.question_sha256:
        raise ValueError(
            f"{target}: the export holds another manifest of {source.testset!r}; a committed set is "
            "never edited, so give the rebuilt set a new id"
        )
    if source.key_version > committed.key_version:
        return
    if source.key_sha256 != committed.key_sha256:
        raise ValueError(
            f"{target}: the key of {source.testset!r} moved but key_version did not "
            f"({committed.key_version}); raise it in the manifest"
        )


def _redacted_run(
    run: LoadedRun,
    entry: ExportedSet,
    corpora: Corpora | None,
    redactors: dict[str, Redactor],
) -> tuple[RedactedRun, int]:
    """Return the file *run* is exported as, and how many fields the caps had to cut."""
    cut: list[bool] = []
    answers = tuple(
        _redacted_answer(item, _redactor_of(item.record, corpora, redactors), cut)
        for item in run.items
    )
    header = RedactedHeader(
        header=run.header,
        scorer_version=SCORER_VERSION,
        key_sha256=entry.key_sha256,
    )
    return (
        RedactedRun(header=header, sessions=run.run.sessions, answers=answers),
        sum(cut),
    )


def _file_name(header: RunHeader, run: LoadedRun, label: str) -> str:
    """Return the file name of the exported run: its axes' label, plus *label* when one is given."""
    date = date_of(run.run.sessions[0].date) if run.run.sessions else ""
    name = run_file_name(header, date)
    if not label:
        return name
    stem = name[: -len(layout.RUN_SUFFIX)]
    return f"{stem}__{label}{layout.RUN_SUFFIX}"


def _redactor_of(
    record: TestRecord, corpora: Corpora | None, redactors: dict[str, Redactor]
) -> Redactor:
    """Return the caps of one text, built once per text and shared by every run of it.

    Raises:
        ValueError: if the text belongs to a restricted corpus and *corpora* does not hold it, so
            the export would store words the licence forbids passing on.
    """
    key = f"{record.corpus}/{text_id(record)}"
    if key not in redactors:
        redactors[key] = _build_redactor(record, corpora)
    return redactors[key]


def redactor_of(record: TestRecord, corpora: Corpora | None = None) -> Redactor:
    """Return the caps of *record*: its own text is what a stored field may not repeat.

    Raises:
        ValueError: if the text belongs to a restricted corpus and *corpora* does not hold it.
    """
    return _build_redactor(record, corpora)


def _build_redactor(record: TestRecord, corpora: Corpora | None) -> Redactor:
    """Return the caps of *record*: its own text is what a stored field may not repeat."""
    cls = adapter_class(record.corpus)
    if not cls.RESTRICTED:
        return Redactor.for_item(record.source, record.reference)
    if corpora is None:
        raise ValueError(
            f"{record.corpus}: a restricted corpus, so the export needs it to hash what it cuts "
            f"(--corpus {record.corpus}={cls.HINT})"
        )
    return Redactor.for_item(
        record.source,
        record.reference,
        restricted=True,
        key=corpora.adapter(record.corpus).redaction_key(),
    )


def _redacted_answer(item: LoadedItem, redactor: Redactor, cut: list[bool]) -> RedactedAnswer:
    """Return one answer as it goes on its line: the numbers, the edits and the capped notes."""
    answer = item.answer
    source = item.record.source
    output = item.output
    edits: list[tuple[int, int, Mapping[str, object]]] = []
    for (start, end), replacement in char_edits(source, output):
        field = redactor.replacement(replacement)
        cut.append(field_is_cut(field))
        edits.append((start, end, field))
    notes: list[Mapping[str, object]] = []
    for index, category, explanation, orphan in notes_of(source, output, answer.review):
        field = redactor.explanation(explanation)
        cut.append(field_is_cut(field))
        notes.append(
            {
                "edit": index,
                "category": category,
                "explanation": field["explanation"],
                "explanation_sha256": field["explanation_sha256"],
                "orphan": orphan,
            }
        )
    return RedactedAnswer(
        text=answer.text,
        text_sha256=answer.text_sha256,
        key_part_sha256=item.record.key_sha256,
        scores=item.scored.to_dict(),
        edits=tuple(edits),
        notes=tuple(notes),
        failed=answer.failed,
        fallback=answer.fallback,
        warning=answer.warning,
        unclear_count=answer.unclear_count,
        requests=answer.requests,
        loaded=answer.loaded,
        shared=answer.shared,
        vram_baseline_mb=answer.vram_baseline_mb,
        vram_peak_mb=answer.vram_peak_mb,
        pieces=answer.pieces,
        seconds=answer.seconds,
    )


__all__ = [
    "ACTIONS",
    "REFUSED",
    "REPLACE",
    "SKIPPED",
    "STALE",
    "UNCHANGED",
    "WRITE",
    "Plan",
    "PlanEntry",
    "export",
    "plan",
    "redactor_of",
]
