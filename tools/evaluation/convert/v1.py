"""The one-off converter: the old ``results-*`` folders and ``set-2026-12`` read as they are.

The old store is a folder of ``<run>.jsonl`` rows beside a ``<run>.header.json`` list of sessions, and
a test set of ``<language>.jsonl`` records whose positions are **token indices**.  The new store is
one run file per run and one record file per language whose positions are **character offsets**.
This module reads the old JSON itself and writes the new formats, so it depends on nothing
but the formats it writes; only the acceptance tests read the old folders beside it.

What it does not do: it never edits or deletes an old folder, and it never re-runs
``prepare_gec_testset``.  The corpora are deterministic, but a conversion must not depend on them.

Three things are checked rather than assumed, because a silent difference here would be a wrong
number in every report afterwards:

* the old ``testset_sha256`` of a header is recomputed from the old records and compared;
* the old ``instructions_sha256`` is recomputed per language and combined, and compared;
* every ``text_sha256`` of a converted record is compared with the committed manifest's.

What cannot be recovered is written down instead: a run whose ``machine`` is empty keeps it empty
unless ``--assume-machine`` names a machine whose evidence (the server version and the sampler's
hash) agrees, and then the assumption is recorded in ``environment.machine_source``.  A run whose
requests carry no step tag gets its steps inferred only where the count is unambiguous, and the
answer says ``step_inferred``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote

from ..core.axes import run_file_name
from ..core.constants import OLLAMA, TextKind
from ..core.formats import FormatError, read_jsonl
from ..core.hashing import sha256_text
from ..core.labels import ReferenceLabel
from ..core.languages import is_language_code, ordered
from ..core.manifest import Manifest, ManifestItem
from ..core.modes import Exchange, Step, exchange_and_steps, parse_mode
from ..core.performance import LoadedModel, RequestStats
from ..core.records import (
    TASK_PROOFREAD,
    Answer,
    Environment,
    Input,
    Protocol,
    RunFile,
    RunHeader,
    RunSettings,
    Session,
    TestRecord,
    key_sha256,
    question_sha256,
    text_id,
)
from ..input.manifest import manifest_item
from ..metrics.offsets import token_span_to_chars
from ..store import layout, writer

TESTSET_NAMES: dict[str, str] = {
    "set-2026-12": "gec-2026-12",
    "set-2026-12-heldout": "gec-2026-12-heldout",
}
"""The old local name of a test set and the committed manifest's id, which is what a run records."""

IN_SCOPE: tuple[str, ...] = (
    "results-correct-only",
    "results-exchange",
    "results-prompt",
    "results-probe-penalty",
    "results-probe-repeat",
    "results-text",
    "results-text-isolated",
)
"""The folders the conversion covers: 10,664 rows, every run of the main test set that is a run."""

OUT_OF_SCOPE: dict[str, str] = {
    "results": "the old store, superseded by the folders above",
    "results-v1": "the old store, superseded by the folders above",
    "results-r": "POC rows without a header",
    "results-r-sweep": "POC rows without a header",
    "results-text-isolated-invalid": "an invalid run, kept as evidence",
    "results-w5": "review mode B on another test set",
    "results-v1-server": "review mode B, which no in-scope run uses",
    "results-prompt-heldout": "the held-out set; convert it with its own test set",
}
"""The folders the conversion leaves alone, and why, so a report can say it rather than omit them."""

HEADER_SUFFIX = ".header.json"
"""What the old header file is called after the run's name."""

InstructionsHash = Callable[[Mapping[str, Any], Sequence[str]], str]
"""How the converter asks for the hash of the instructions a run sent, per language.

The hash is made of the prompts of the steps and of the parameter lines of the languages, which is
``run``-layer knowledge (``one_request/correction.instruction_parts``).  The converter takes it as a
hook instead of importing it, so ``store`` stays below ``run`` in the import graph and the
acceptance test can supply the old package's own function.
"""


def instructions_hook(hash_of: Callable[..., str]) -> InstructionsHash:
    """Return the converter's instructions hook, from the run layer's own hash function.

    The hook reads the old header's mode and request profile — which is what the converter knows —
    and asks *hash_of* for the hash of the exchange and steps they stand for.  The command line
    passes :func:`~..run.protocol.instructions_sha256`, so the converter never imports ``run``.
    """

    def hook(first: Mapping[str, Any], languages: Sequence[str]) -> str:
        """Return the hash of the instructions the old header's run sent, for *languages*."""
        mode = parse_mode(str(first.get("mode", "")))
        profile = dict(first.get("request_profile") or {})
        exchange, steps = exchange_and_steps(
            mode, unclear_check=profile.get("unclear_check") is not False
        )
        return hash_of(
            languages,
            exchange=exchange,
            steps=steps,
            compact_json=bool(profile.get("compact_json", False)),
            prompt=str(profile.get("prompt") or ""),
        )

    return hook


_OLD_FIELDS: tuple[str, ...] = (
    "id",
    "language",
    "kind",
    "origin",
    "source",
    "reference",
    "sentences",
    "labels",
    "band",
    "sentence_spans",
    "unchanged_sentences",
    "level",
)
"""The fields the old test record's ``to_dict`` wrote, in the order its dataclass declared them."""

_OLD_EMPTY_DROPPED: tuple[str, ...] = ("band", "sentence_spans", "unchanged_sentences", "level")
"""The fields the old ``to_dict`` left out when they were empty, so its hash can be recomputed."""


@dataclass(frozen=True)
class ConvertedTestset:
    """One old test set, converted, with the hash the old headers recorded for it."""

    testset: str
    records: tuple[TestRecord, ...]
    manifest: Manifest
    old_sha256: str
    """The old ``testset_sha256``: the hash of the old records, which every header of the set names."""


@dataclass(frozen=True)
class ConvertedRun:
    """One old run file, converted, with the name it is written under and what was assumed."""

    run: RunFile
    folder: str
    old_name: str
    new_name: str
    notes: tuple[str, ...] = ()

    @property
    def answers(self) -> int:
        """Return how many answers the run holds, which is the number of rows it was read from."""
        return len(self.run.answers)

    @property
    def sessions(self) -> int:
        """Return how many sessions the run recorded, one per start or resume."""
        return len(self.run.sessions)


@dataclass(frozen=True)
class ConversionReport:
    """What a conversion did: the sets, the runs, what it skipped and what it had to assume."""

    testsets: tuple[tuple[str, int], ...] = ()
    """Each converted test set and how many records it holds."""

    runs: tuple[ConvertedRun, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    """Every file or folder left alone, with the reason, so nothing disappears silently."""

    notes: tuple[str, ...] = ()

    @property
    def answers(self) -> int:
        """Return how many answers the conversion wrote, which is the number of rows it read."""
        return sum(run.answers for run in self.runs)

    def summary(self) -> str:
        """Return a few lines a person reads after a conversion, one fact per line."""
        lines = [f"{testset}: {count} records" for testset, count in self.testsets]
        lines.append(f"{len(self.runs)} runs, {self.answers} answers")
        lines.extend(f"skipped {where}: {why}" for where, why in self.skipped)
        lines.extend(f"note: {note}" for note in self.notes)
        return "\n".join(lines)


# ── the old test set ─────────────────────────────────────────────────────────


def old_testset_lines(directory: Path) -> list[dict[str, Any]]:
    """Return every old test record of *directory*, language by language in the table's order.

    Raises:
        FormatError: if the directory holds no record file.
    """
    languages = ordered(
        path.stem for path in directory.glob("*.jsonl") if is_language_code(path.stem)
    )
    if not languages:
        raise FormatError(f"{directory}: no <language>.jsonl test records")
    lines: list[dict[str, Any]] = []
    for language in languages:
        lines.extend(read_jsonl(directory / f"{language}.jsonl"))
    return lines


def old_records_sha256(lines: Sequence[Mapping[str, Any]]) -> str:
    """Return the hash the old headers recorded as ``testset_sha256``, recomputed from *lines*.

    The old hash covered the old records' ``to_dict`` output: the dataclass fields only, with the
    empty optional ones left out.  Recomputing it is what proves the converted set is the set the
    runs were asked about, and not a set rebuilt under the same name.
    """
    records: list[dict[str, Any]] = []
    for line in lines:
        record: dict[str, Any] = {"schema_version": 1}
        for name in _OLD_FIELDS:
            if name in line:
                record[name] = line[name]
        for name in _OLD_EMPTY_DROPPED:
            if not record.get(name):
                record.pop(name, None)
        records.append(record)
    return sha256_text(json.dumps(records, sort_keys=True, ensure_ascii=False))


def convert_testset(directory: Path, manifest_path: Path, testset: str) -> ConvertedTestset:
    """Return the old test set of *directory* as the new records and the new manifest.

    Args:
        directory: The old set's folder, holding ``<language>.jsonl``.
        manifest_path: The committed manifest that pins the set, whose positions are tokens too.
        testset: The committed manifest's id, which is what the new files are named after.

    Raises:
        FormatError: if a record or the manifest is not of a format this converter knows, or if a
            text's hash does not match the manifest's.
    """
    lines = old_testset_lines(directory)
    records = tuple(_record_of(line) for line in lines)
    manifest = _manifest_of(manifest_path, testset, records)
    return ConvertedTestset(
        testset=testset,
        records=records,
        manifest=manifest,
        old_sha256=old_records_sha256(lines),
    )


def _record_of(line: Mapping[str, Any]) -> TestRecord:
    """Return one old test record as a new one, its token positions mapped to characters."""
    source = str(line["source"])
    labels: list[ReferenceLabel] = []
    for label in line.get("labels") or ():
        start, end = token_span_to_chars(source, (int(label["start"]), int(label["end"])))
        labels.append(
            ReferenceLabel(
                start=start,
                end=end,
                replacement="".join(str(token) for token in label.get("replacement") or ()),
                code=str(label.get("code", "")),
            )
        )
    return TestRecord(
        id=str(line["id"]),
        language=str(line["language"]),
        source=source,
        kind=str(line.get("kind", TextKind.ERRORS)),
        reference=str(line.get("reference", "")),
        sentences=tuple(str(sentence) for sentence in line.get("sentences") or ()),
        labels=tuple(labels),
        sentence_spans=tuple(
            token_span_to_chars(source, (int(start), int(end)))
            for start, end in line.get("sentence_spans") or ()
        ),
        unchanged_sentences=tuple(int(index) for index in line.get("unchanged_sentences") or ()),
        origin=str(line.get("origin", "")),
        band=str(line.get("band", "")),
        corpus=str(line.get("corpus", "")),
        pointer=dict(line.get("pointer") or {}),
    )


def _manifest_of(manifest_path: Path, testset: str, records: Sequence[TestRecord]) -> Manifest:
    """Return the new manifest of *testset*: the committed one's meta, its positions in characters.

    The items are built by the one builder every writer uses, so a converted set and a rebuilt one
    are the same file.  The committed manifest's positions are not read: the records already carry
    characters, and the builder derives the edits from the reference.

    Raises:
        FormatError: if the manifest is of another format, or a text's hash is not the record's.
    """
    old = Manifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
    by_id = {record.id: record for record in records}
    items: list[ManifestItem] = []
    for item in old.items:
        record = by_id.get(item.id)
        if record is None:
            raise FormatError(f"{manifest_path}: the manifest lists {item.id!r}, the set has not")
        _check_text_hashes(manifest_path, item, record)
        items.append(
            manifest_item(
                record,
                {
                    "corpus": item.corpus,
                    "band": item.band,
                    "level": item.level,
                    "pointer": dict(item.pointer or {}),
                },
            )
        )
    return Manifest(
        testset=testset,
        built=old.built,
        selection_seed=old.selection_seed,
        builder_revision=old.builder_revision,
        key_version=old.key_version,
        corpora=dict(old.corpora),
        question_sha256=question_sha256(records),
        key_sha256=key_sha256(records),
        items=tuple(items),
    )


def _check_text_hashes(manifest_path: Path, item: ManifestItem, record: TestRecord) -> None:
    """Refuse a manifest whose text or reference hash is not the record's.

    Raises:
        FormatError: if either hash differs.
    """
    stored = str(item.source.get("sha256", ""))
    if stored and stored != sha256_text(record.source):
        raise FormatError(
            f"{manifest_path}: {record.id}: the manifest's text hash is not the record's"
        )
    stored = str(item.reference.get("sha256", ""))
    if stored and stored != sha256_text(record.reference):
        raise FormatError(
            f"{manifest_path}: {record.id}: the manifest's reference hash is not the record's"
        )


# ── the old run files ───────────────────────────────────────────────────────


def machine_evidence(headers: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    """Return what identifies each machine: its server version and its sampler's hash.

    A run whose ``machine`` is empty can only be given one when its evidence matches a machine that
    other runs name, which is what this table is for.
    """
    found: dict[str, tuple[str, str]] = {}
    for header in headers:
        machine = str(header.get("machine", ""))
        if machine:
            found.setdefault(machine, _evidence_of(header))
    return found


def _evidence_of(header: Mapping[str, Any]) -> tuple[str, str]:
    """Return the server version and the sampler's hash of *header*, which identify a machine."""
    sampler = header.get("gpu_sampler") or {}
    return (str(header.get("ollama_version", "")), str(sampler.get("agent_sha256", "")))


def convert_run(
    header_path: Path,
    rows_path: Path,
    testsets: Mapping[str, Sequence[TestRecord]],
    *,
    instructions: InstructionsHash | None = None,
    assume_machine: str = "",
    evidence: Mapping[str, tuple[str, str]] | None = None,
    old_sha256: str = "",
) -> ConvertedRun:
    """Return the old run of *header_path* and *rows_path* as one new run file.

    Args:
        header_path: The old ``<run>.header.json``, a JSON list of sessions.
        rows_path: The old ``<run>.jsonl``, one row per text.
        testsets: The converted test sets, keyed by the id a run records.
        instructions: How to hash the instructions a run sent, per language; ``None`` leaves the
            per-language hashes empty and says so in the report.
        assume_machine: A machine to give a run whose own ``machine`` is empty, when the evidence
            agrees.
        evidence: What identifies each machine, from :func:`machine_evidence`.
        old_sha256: The old ``testset_sha256`` of the set, checked against the header's.

    Raises:
        FormatError: if the header is not a list of sessions, a row is not a text of the set, the
            header names another test set, or a recorded hash is not the one recomputed here.
    """
    sessions = _read_sessions(header_path)
    first = sessions[0]
    testset = TESTSET_NAMES.get(str(first.get("testset", "")), str(first.get("testset", "")))
    records = testsets.get(testset)
    if records is None:
        raise FormatError(f"{header_path}: the test set {testset!r} was not converted")
    _check_testset_sha256(header_path, first, old_sha256)
    notes: list[str] = []
    header = _header_of(
        first,
        records,
        testset,
        instructions,
        assume_machine,
        evidence or {},
        notes,
    )
    by_text = {text_id(record): record for record in records}
    answers = tuple(_answer_of(row, by_text, header, rows_path) for row in read_jsonl(rows_path))
    run = RunFile(
        header=header,
        sessions=tuple(_session_of(session) for session in sessions),
        answers=answers,
    )
    return ConvertedRun(
        run=run,
        folder=header_path.parent.name,
        old_name=header_path.name[: -len(HEADER_SUFFIX)],
        new_name=run_file_name(header, str(first.get("date", ""))[:10]),
        notes=tuple(notes),
    )


def _read_sessions(header_path: Path) -> list[dict[str, Any]]:
    """Return the sessions of an old header file.

    Raises:
        FormatError: if the file is not a non-empty JSON list of objects.
    """
    try:
        sessions = json.loads(header_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FormatError(f"{header_path}: cannot be read as JSON ({error})") from error
    if not isinstance(sessions, list) or not sessions or not isinstance(sessions[0], dict):
        raise FormatError(f"{header_path}: an old header is a non-empty list of sessions")
    return sessions


def _check_testset_sha256(header_path: Path, first: Mapping[str, Any], old_sha256: str) -> None:
    """Refuse a header whose recorded test set hash is not the one recomputed from the records.

    Raises:
        FormatError: if the two differ.
    """
    stored = str(first.get("testset_sha256", ""))
    if old_sha256 and stored and stored != old_sha256:
        raise FormatError(
            f"{header_path}: the run was asked about another set: its testset_sha256 is "
            f"{stored[:8]}, the records hash to {old_sha256[:8]}"
        )


def _header_of(
    first: Mapping[str, Any],
    records: Sequence[TestRecord],
    testset: str,
    instructions: InstructionsHash | None,
    assume_machine: str,
    evidence: Mapping[str, tuple[str, str]],
    notes: list[str],
) -> RunHeader:
    """Return the header line of a converted run, from the old header's first session."""
    mode = parse_mode(str(first.get("mode", "")))
    profile = dict(first.get("request_profile") or {})
    exchange, steps = exchange_and_steps(
        mode, unclear_check=profile.get("unclear_check") is not False
    )
    temperature = float(first.get("temperature", 0.0))
    if "temperature" in profile and float(profile["temperature"]) != temperature:
        raise FormatError(
            f"the request profile's temperature {profile['temperature']} is not the run's "
            f"{temperature}"
        )
    languages = ordered({record.language for record in records})
    info = dict(first.get("model_info") or {})
    protocol = Protocol(
        model=str(first["model"]),
        model_digest=str(info.pop("digest", "")),
        exchange=exchange,
        steps=steps,
        step_one_prompt=str(profile.get("prompt") or "standard"),
        temperature=temperature,
        sampling=tuple(
            sorted(
                (str(name), float(value)) for name, value in (profile.get("options") or {}).items()
            )
        ),
        compact_json=bool(profile.get("compact_json", False)),
        num_ctx=int(first.get("num_ctx", 8192)),
        provider=str(first.get("provider", OLLAMA)),
        answer_format_version=int(first.get("answer_format_version", 1)),
        uses=(),
        instructions=_instructions_of(first, languages, instructions, notes),
        fingerprint=str(first.get("fingerprint", "")),
        explainer=str(first.get("explainer", "")),
    )
    return RunHeader(
        input=Input(
            testset=testset,
            question_sha256=question_sha256(records),
            texts=len(records),
            languages=tuple(languages),
        ),
        protocol=protocol,
        run=RunSettings(
            seed=int(first["seed"]),
            repeat=int(first.get("repeat", 1)),
            timeout=float(first.get("timeout", 900.0)),
        ),
        task=TASK_PROOFREAD,
        model_info=info,
        environment=_environment_of(first, assume_machine, evidence, notes),
    )


def _instructions_of(
    first: Mapping[str, Any],
    languages: Sequence[str],
    instructions: InstructionsHash | None,
    notes: list[str],
) -> tuple[tuple[str, str], ...]:
    """Return the per-language instruction hashes, checked to combine to the one the header stored.

    Raises:
        FormatError: if the hook's hash of all the languages is not the stored one.
    """
    if instructions is None:
        notes.append(
            "the per-language instruction hashes are empty: no hook was given, so the stored "
            "instructions_sha256 could not be split"
        )
        return ()
    found = tuple(sorted((code, instructions(first, [code])) for code in languages))
    combined = instructions(first, languages)
    stored = str(first.get("instructions_sha256", ""))
    if stored and combined != stored:
        raise FormatError(
            f"the instructions hash {stored[:8]} is not what the hook computes ({combined[:8]})"
        )
    return found


def _environment_of(
    first: Mapping[str, Any],
    assume_machine: str,
    evidence: Mapping[str, tuple[str, str]],
    notes: list[str],
) -> Environment:
    """Return where a run ran, filling an empty machine only when the evidence agrees."""
    machine = str(first.get("machine", ""))
    source = ""
    if not machine and assume_machine:
        wanted = evidence.get(assume_machine)
        mine = _evidence_of(first)
        if wanted is not None and wanted == mine:
            machine = assume_machine
            source = f"assumed by convert_v1: ollama {mine[0]}, sampler {mine[1][:8]}"
            notes.append(f"the machine {assume_machine} was assumed from the evidence")
        else:
            notes.append(
                f"the machine {assume_machine} was not assumed: the evidence "
                f"(ollama {mine[0]}, sampler {mine[1][:8]}) does not match"
            )
    return Environment(
        machine=machine,
        ollama_version=str(first.get("ollama_version", "")),
        isolated=bool(first.get("isolated", False)),
        gpu_sampler=dict(first.get("gpu_sampler") or {}),
        machine_source=source,
    )


def _session_of(session: Mapping[str, Any]) -> Session:
    """Return one old header session as a session line.

    The old ``loaded_before`` is a list of model names, so each becomes a mapping with the one field
    the old data has; a session that recorded more keeps it.
    """
    return Session(
        date=str(session.get("date", "")),
        tool_revision=str(session.get("tool_revision", "")),
        vram_baseline_mb=session.get("vram_baseline_mb"),
        loaded_before=tuple(_loaded_before_of(item) for item in session.get("loaded_before") or ()),
    )


def _loaded_before_of(item: Any) -> dict[str, Any]:
    """Return one ``loaded_before`` entry as a mapping: a name becomes ``{"name": name}``."""
    if isinstance(item, Mapping):
        return dict(item)
    return {"name": str(item)}


def _answer_of(
    row: Mapping[str, Any],
    by_text: Mapping[str, TestRecord],
    header: RunHeader,
    rows_path: Path,
) -> Answer:
    """Return one old row as an answer line.

    Raises:
        FormatError: if the row's id is not a text of the set.
    """
    text = str(row.get("id", ""))
    record = by_text.get(text)
    if record is None:
        raise FormatError(f"{rows_path}: {text!r} is not a text of the set")
    performance = dict(row.get("performance") or {})
    pieces = int(row.get("pieces", 1) or 1)
    requests, inferred = _requests_of(performance, header.protocol, pieces)
    return Answer(
        text=text,
        text_sha256=record.text_sha256,
        output=str(row.get("output", "")),
        failed=bool(row.get("failed", False)),
        fallback=bool(row.get("fallback", False)),
        warning=str(row.get("warning", "")),
        review=_review_of(row.get("changes") or (), record.source, header.protocol),
        unclear_count=int(row.get("unclear", 0) or 0),
        requests=requests,
        loaded=tuple(_loaded_of(item) for item in performance.get("loaded") or ()),
        shared=bool(performance.get("shared", False)),
        vram_baseline_mb=performance.get("vram_baseline_mb"),
        vram_peak_mb=performance.get("vram_peak_mb"),
        pieces=pieces,
        seconds=float(row.get("seconds", 0.0) or 0.0),
        step_inferred=inferred,
    )


def _review_of(
    changes: Sequence[Mapping[str, Any]], source: str, protocol: Protocol
) -> CorrectionReview | None:
    """Return the review a row's ``changes`` stand for, or ``None`` when the run asked for none.

    One note per entry, with one part whose character span is the entry's token span mapped to the
    source and whose ``corrected`` is the joined replacement; an entry that explains no change (an
    orphan) becomes a note with no parts, which is how ``align()`` reports it.
    """
    if Step.EXPLAIN not in protocol.steps:
        return None
    notes: list[EditNote] = []
    for number, entry in enumerate(changes, start=1):
        edit = entry.get("edit")
        parts: tuple[EditPart, ...] = ()
        if edit is not None:
            start, end = token_span_to_chars(source, (int(edit[0]), int(edit[1])))
            parts = (
                EditPart(
                    raw_start=start,
                    raw_end=end,
                    original=source[start:end],
                    corrected=str(edit[2]),
                ),
            )
        notes.append(
            EditNote(
                id=number,
                parts=parts,
                sentences=(),
                category=str(entry.get("category", "other")),
                explanation=str(entry.get("explanation", "")),
            )
        )
    return CorrectionReview(edits=tuple(notes))


def _requests_of(
    performance: Mapping[str, Any], protocol: Protocol, pieces: int
) -> tuple[tuple[RequestStats, ...], bool]:
    """Return the requests of one text, with their steps, and whether the steps were inferred.

    The old rows of the exchange carry no step tag, so the steps are inferred only where the count
    is unambiguous: as many requests as steps times pieces.  Everywhere else the step stays
    empty and the cost view shows ``-``, which is honest about what was not recorded.
    """
    raw = [dict(item) for item in performance.get("requests") or ()]
    if not raw:
        return (), False
    if all(str(item.get("step", "")) for item in raw):
        return tuple(_request_of(item) for item in raw), False
    steps = [step.value for step in protocol.steps]
    if steps and len(raw) == len(steps) * pieces:
        return (
            tuple(
                _request_of({**item, "step": steps[number % len(steps)]})
                for number, item in enumerate(raw)
            ),
            True,
        )
    if protocol.exchange is Exchange.ONE_REQUEST_TEXT:
        return tuple(_request_of({**item, "step": "text"}) for item in raw), False
    return tuple(_request_of(item) for item in raw), False


def _request_of(item: Mapping[str, Any]) -> RequestStats:
    """Return one old request as a request record, keeping the field names it had."""
    return RequestStats(
        model=str(item.get("model", "")),
        prompt_tokens=int(item.get("prompt_tokens", 0)),
        answer_tokens=int(item.get("answer_tokens", 0)),
        load_s=float(item.get("load_s", 0.0)),
        prompt_eval_s=float(item.get("prompt_eval_s", 0.0)),
        generation_s=float(item.get("generation_s", 0.0)),
        total_s=float(item.get("total_s", 0.0)),
        wall_s=float(item.get("wall_s", 0.0)),
        done_reason=str(item.get("done_reason", "")),
        failed=bool(item.get("failed", False)),
        step=str(item.get("step", "")),
    )


def _loaded_of(item: Mapping[str, Any]) -> LoadedModel:
    """Return one old loaded-model entry as a loaded-model record."""
    return LoadedModel(
        name=str(item.get("name", "")),
        size_bytes=int(item.get("size_bytes", 0)),
        size_vram_bytes=int(item.get("size_vram_bytes", 0)),
        context_length=int(item.get("context_length", 0)),
    )


# ── the whole conversion ────────────────────────────────────────────────────


def convert(
    old_root: Path,
    root: Path,
    *,
    manifests: Mapping[str, Path],
    folders: Sequence[str] = IN_SCOPE,
    instructions: InstructionsHash | None = None,
    assume_machine: str = "",
    skip: Sequence[str] = (),
) -> ConversionReport:
    """Convert the test sets and the run folders of *old_root* into the store at *root*.

    Everything is converted in memory first and written afterwards, so a refusal leaves the store
    as it was.  Two old runs can carry the same facts and so ask for one new name; that is refused
    before anything is written, naming both files, because the second would replace the first.

    Args:
        old_root: The old store's directory (``~/gec-testset``).
        root: The new store's root, below which ``testsets/`` and ``runs/`` are written.
        manifests: The committed manifest of each test set to convert, keyed by its id.
        folders: The old run folders to convert.
        instructions: How to hash the instructions a run sent, per language.
        assume_machine: A machine to give a run whose own ``machine`` is empty, when the evidence
            agrees.
        skip: Old run files to leave out, by name or by a path below *old_root*, to resolve a
            name collision.

    Returns:
        What was converted, what was skipped and what had to be assumed.

    Raises:
        ValueError: if two old runs ask for one new name.
    """
    planned_testsets: list[tuple[str, ConvertedTestset]] = []
    converted: dict[str, Sequence[TestRecord]] = {}
    old_hashes: dict[str, str] = {}
    for testset, manifest_path in sorted(manifests.items()):
        result = convert_testset(old_root / _old_name_of(testset), manifest_path, testset)
        planned_testsets.append((testset, result))
        converted[testset] = result.records
        old_hashes[testset] = result.old_sha256
    evidence = machine_evidence(_headers_of(old_root, folders))
    planned: list[ConvertedRun] = []
    skipped: list[tuple[str, str]] = []
    for folder in folders:
        directory = old_root / folder
        if not directory.is_dir():
            skipped.append((folder, "the folder is not there"))
            continue
        for header_path in sorted(directory.glob(f"*{HEADER_SUFFIX}")):
            if _left_out(header_path, old_root, skip):
                skipped.append((str(header_path), "left out on request"))
                continue
            rows_path = header_path.with_name(header_path.name[: -len(HEADER_SUFFIX)] + ".jsonl")
            if not rows_path.is_file():
                skipped.append((str(header_path), "no rows file beside the header"))
                continue
            old_testset = str(_read_sessions(header_path)[0].get("testset", ""))
            testset = TESTSET_NAMES.get(old_testset, old_testset)
            if testset not in converted:
                skipped.append((str(header_path), f"the test set {testset!r} was not converted"))
                continue
            planned.append(
                convert_run(
                    header_path,
                    rows_path,
                    converted,
                    instructions=instructions,
                    assume_machine=assume_machine,
                    evidence=evidence,
                    old_sha256=old_hashes[testset],
                )
            )
    _refuse_collisions(planned)
    for testset, result in planned_testsets:
        writer.write_testset(root, testset, result.records, result.manifest)
    notes: list[str] = []
    for result in planned:
        writer.write_run(layout.run_path(root, result.new_name, result.folder), result.run)
        notes.extend(f"{result.folder}/{result.old_name}: {note}" for note in result.notes)
    return ConversionReport(
        testsets=tuple((testset, len(result.records)) for testset, result in planned_testsets),
        runs=tuple(planned),
        skipped=tuple(skipped),
        notes=tuple(notes),
    )


def _left_out(header_path: Path, old_root: Path, skip: Sequence[str]) -> bool:
    """Return whether *header_path* is one of the runs to leave out.

    A run is named by its file name, by its path below *old_root*, or by its full path, so a
    caller can write whichever it has at hand.
    """
    if not skip:
        return False
    names = {header_path.name, str(header_path)}
    try:
        names.add(str(header_path.relative_to(old_root)))
    except ValueError:
        pass
    return not names.isdisjoint(skip)


def _refuse_collisions(planned: Sequence[ConvertedRun]) -> None:
    """Refuse when two old runs ask for one new name, naming both.

    Raises:
        ValueError: if two old runs of one folder and date carry the same facts.
    """
    by_name: dict[str, list[str]] = {}
    for result in planned:
        old_file = f"{result.folder}/{result.old_name}{HEADER_SUFFIX}"
        by_name.setdefault(result.new_name, []).append(old_file)
    clashes = {name: old for name, old in by_name.items() if len(old) > 1}
    if not clashes:
        return
    lines = [f"  {name}: {', '.join(sorted(old))}" for name, old in sorted(clashes.items())]
    raise ValueError(
        "two old runs ask for one new name, so the second would replace the first:\n"
        + "\n".join(lines)
        + "\nLeave one out with skip=…"
    )


def _old_name_of(testset: str) -> str:
    """Return the old folder name of a test set, from the committed manifest's id.

    Raises:
        KeyError: if no old name maps to *testset*.
    """
    for old, new in TESTSET_NAMES.items():
        if new == testset:
            return old
    known = ", ".join(sorted(TESTSET_NAMES.values()))
    raise KeyError(f"no old folder for the test set {testset!r}; the converter knows: {known}")


def _headers_of(old_root: Path, folders: Sequence[str]) -> list[dict[str, Any]]:
    """Return the first session of every old header of *folders*, for the machine evidence."""
    found: list[dict[str, Any]] = []
    for folder in folders:
        for header_path in sorted((old_root / folder).glob(f"*{HEADER_SUFFIX}")):
            found.append(_read_sessions(header_path)[0])
    return found


__all__ = [
    "HEADER_SUFFIX",
    "IN_SCOPE",
    "OUT_OF_SCOPE",
    "TESTSET_NAMES",
    "ConversionReport",
    "ConvertedRun",
    "ConvertedTestset",
    "InstructionsHash",
    "convert",
    "convert_run",
    "convert_testset",
    "instructions_hook",
    "machine_evidence",
    "old_records_sha256",
    "old_testset_lines",
]
