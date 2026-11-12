"""The export: a local run becomes a committed run file, and the index is regenerated.

A local run writes result rows with the source, the reference and the model's output in them
(:mod:`.results`); the repository must not hold those.  So a run is *exported*: the model's edits
are stored as positions plus capped replacements, the notes as categories plus capped
explanations, and every outcome as numbers.  What stays is checkable (manifest hash, instructions
hash, digest, seed, machine) and re-scorable, without a corpus sentence in the file.  The export
runs strict mode and never writes a file it refuses: it cuts the field instead and counts the cuts
in the header.

This module owns the way from a cache to a file.  It must not import :mod:`.report` or the command
line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import protocol, session
from . import testset as testset_module
from .error_labels import ReferenceLabel
from .index import write_index
from .layout import (
    RUNS_DIR,
    TESTSETS_DIR,
    kind_of,
    machine_path,
    read_machine,
    run_id,
    sampler_record,
)
from .quality import match_edits, ordered_edits
from .redaction import Redactor, field_is_cut, sha256_text
from .results import DEFAULT_MODE, ResultRow, added_marks, read_rows, strip_added_marks
from .run_format import (
    RUN_SCHEMA,
    RUN_TYPE,
    apply_stored_edits,
    warning_code,
    write_run,
)
from .testset import Corpora, ResolvedItem
from .validation import validate_run

# ── what the caps keep, per item ──────────────────────────────────────────────


def edits_of(row: ResultRow) -> tuple[tuple[int, int, str], ...]:
    """Return the model's changes over the source tokens, as strings, in text order."""
    corrected = strip_added_marks(row.source, row.output)
    return tuple(
        (start, end, "".join(replacement))
        for start, end, replacement in ordered_edits(row.source, corrected)
    )


def output_sha256(row: ResultRow) -> str:
    """Return the hash of the source with the model's edits applied.

    Not the hash of the raw output: the scoring ignores whitespace, and so does a rebuild, so the
    hash of the edited source is what a reader can check without the output.
    """
    return sha256_text(apply_stored_edits(row.source, edits_of(row)))


def reference_outcomes(row: ResultRow, labels: Sequence[ReferenceLabel]) -> list[list[bool | None]]:
    """Return one ``[exact, located, category_given]`` per reference edit, in text order.

    ``exact`` and ``located`` come from the scoring the run used.  ``category_given`` says whether
    the note that explains the fix names the category the corpus label maps to, and is ``None``
    when it cannot be judged: the model did not fix this error exactly (only then it is certain
    which note belongs to it, as in :func:`~.error_labels.category_agreement`), or the label maps
    to no category.
    """
    if row.kind != "errors":
        return []
    corrected = strip_added_marks(row.source, row.output)
    by_span = {
        (int(change["edit"][0]), int(change["edit"][1])): str(change.get("category", ""))
        for change in row.changes
        if change.get("edit") is not None
    }
    labelled = {(label.start, label.end): label for label in labels}
    outcomes: list[list[bool | None]] = []
    for match in match_edits(row.source, corrected, row.reference):
        start, end = match.edit[0], match.edit[1]
        given: bool | None = None
        label = labelled.get((start, end))
        if label is not None and match.exact and label.category not in {"", "other"}:
            named = by_span.get((start, end))
            if named is not None:
                given = named == label.category
        outcomes.append([match.exact, match.located, given])
    return outcomes


def _note_records(
    row: ResultRow, edits: Sequence[tuple[int, int, str]]
) -> list[tuple[int | None, str, str, bool]]:
    """Return the row's notes as ``(edit index or None, category, explanation, orphan)``.

    A note that explains a change is linked to it by the index of that edit in the stored list;
    a note that matches no change keeps ``None`` and is flagged, because a reader of the run can
    then tell an unplaceable remark from an unlinked one (a row written before
    :attr:`~.results.ResultRow.changes` existed has none of these links).
    """
    index_of = {(start, end): position for position, (start, end, _) in enumerate(edits)}
    records = []
    for change in row.changes:
        span = change.get("edit")
        key = (int(span[0]), int(span[1])) if span is not None else None
        index = index_of.get(key) if key is not None else None
        records.append(
            (
                index,
                str(change.get("category", "")),
                str(change.get("explanation", "")),
                bool(change.get("orphan", False)) or index is None,
            )
        )
    return records


def item_record(  # one item needs its row, its manifest entry and its caps
    row: ResultRow,
    entry: dict,
    *,
    redactor: Redactor,
    cut: list[bool],
    labels: Sequence[ReferenceLabel] = (),
) -> dict:
    """Return one item line of a run: the row's outcome, scores and capped edits and notes.

    Args:
        row: The local result row of this text, with the model's output still in it.
        entry: The manifest item this row belongs to (its id, kind and word count).
        redactor: The caps of the item: the runs its fields may not repeat, and whether it stores
            words at all.
        cut: Collects one flag per field that had to be cut, for the header's ``cut_fields``.
        labels: The corpus labels of the item's reference edits, for ``reference``.

    Returns:
        The item as it goes on its line of the run file.
    """
    edits = edits_of(row)
    stored_edits = []
    for start, end, replacement in edits:
        field = redactor.replacement(replacement)
        cut.append(field_is_cut(field))
        stored_edits.append([start, end, field])

    notes = []
    for index, category, explanation, orphan in _note_records(row, edits):
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

    performance = row.performance.to_dict() if row.performance is not None else None
    return {
        "item": entry["id"],
        "outcome": {
            "failed": row.failed,
            "fallback": row.fallback,
            "has_review": row.has_review,
            "warning_code": warning_code(row.warning),
            "pieces": row.pieces,
            "seconds": row.seconds,
        },
        "scores": {
            "tp": row.true_positives,
            "fp": row.false_positives,
            "fn": row.false_negatives,
            "unchanged": row.unchanged,
            "explained": row.explained,
            "needed": row.needed,
            "orphans": row.orphans,
            "category_checked": row.category_checked,
            "category_agreed": row.category_agreed,
            "marks_added": int(added_marks(row)),
            "notes_linked": bool(row.changes),
            # The numbers of the item: GLEU, recall by half, left alone and coverage.  They are
            # counts, so they carry no corpus text.
            **row.scores,
        },
        "reference": reference_outcomes(row, labels),
        "edits": stored_edits,
        "notes": notes,
        "unclear": row.unclear,
        "output_sha256": output_sha256(row),
        "performance": performance,
    }


# ── the export ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExportedRun:
    """What one export produced: where the file is, what it is called, and what it holds."""

    path: Path
    run_id: str
    items: int
    """Item lines written."""
    cut_fields: int
    """Fields the caps had to cut (hashed replacements, withheld explanations)."""
    eval_version: int
    """The protocol version the run measured; runs of different versions are never compared."""


def _cache_pairs(cache_dir: Path) -> list[tuple[Path, Path]]:
    """Return ``(rows, header)`` per run in the local cache, in file-name order."""
    pairs = []
    for rows in sorted(cache_dir.glob("*.jsonl")):
        header = rows.with_suffix(".header.json")
        if header.exists():
            pairs.append((rows, header))
    return pairs


def _session_header(path: Path) -> dict:
    """Return the first entry of a local ``.header.json``: the session the run started in.

    A resumed run appends one entry per session, so the entries are checked against each other
    first: a run resumed after a tool, Ollama or model change would otherwise export as one
    consistent run.  The *first* entry is returned, so the run id carries the date the run
    started.

    Raises:
        ValueError: if the list is empty, or two consecutive sessions disagree.
    """
    entries = session.read_sessions(path)
    conflicts = session.session_conflicts(entries)
    if conflicts:
        raise ValueError(
            f"{path}: the sessions of this run disagree, so it is not one run: "
            + "; ".join(conflicts)
        )
    return entries[0]


def _manifest_items_by_row(manifest: dict) -> dict[str, dict]:
    """Return the manifest's items keyed by the row id the local results use (``de/de-01``)."""
    return {f"{entry['language']}/{entry['id']}": entry for entry in manifest["items"]}


def export(  # one argument per fact the run file needs
    cache_dir: Path,
    *,
    testset: str,
    manifest: Path,
    corpora: Corpora,
    machine: str,
    out: Path,
    label: str | None = None,
) -> list[ExportedRun]:
    """Turn every run in *cache_dir* into a committed run file below *out*; regenerate the index.

    Args:
        cache_dir: The local results directory (rows plus ``*.header.json``); never committed.
        testset: Name of the test set, which has to be the manifest's.
        manifest: The manifest the builder wrote; it is copied to ``out`` and pins every run.
        corpora: The corpora, so strict mode can check the caps against the real text.
        machine: Id of the machine file in ``out/machines/``.
        out: The repository directory (``evaluations/proofreading``).
        label: Replaces the ``r<n>`` suffix of the run id, for two runs of one configuration.

    Returns:
        One entry per run file written.

    Raises:
        ValueError: if the manifest is not the test set's, the corpora are not the ones the
            manifest pinned, a row belongs to another test set, a text of the set has no row, or a
            run file of that id exists already (files are never edited after an export).
    """
    pinned = testset_module.read_manifest(manifest)
    if pinned["testset"] != testset:
        raise ValueError(
            f"{manifest}: the manifest is the test set {pinned['testset']!r}, not {testset!r}"
        )
    if problems := testset_module.corpus_problems(pinned, corpora):
        raise ValueError("the corpora are not the ones the manifest pinned: " + "; ".join(problems))
    machine_file = machine_path(out, machine)
    if not machine_file.exists():
        raise ValueError(f"{machine_file}: no such machine file; write it by hand first")
    read_machine(machine_file)

    stored = _store_manifest(out, testset, manifest, pinned)
    entries = _manifest_items_by_row(pinned)
    resolved = {entry["id"]: testset_module.resolve(entry, corpora) for entry in pinned["items"]}
    written: list[ExportedRun] = []
    for rows_path, header_path in _cache_pairs(cache_dir):
        rows = read_rows(rows_path)
        if not rows:
            continue
        written.append(
            _export_one(
                rows,
                _session_header(header_path),
                rows_path=rows_path,
                entries=entries,
                resolved=resolved,
                corpora=corpora,
                manifest=stored,
                testset=testset,
                machine=machine,
                out=out,
                label=label,
            )
        )
    write_index(out)
    return written


def _store_manifest(out: Path, testset: str, source: Path, pinned: dict) -> Path:
    """Copy the manifest into the repository, or check that the one there is the same.

    A manifest is immutable: another selection is another test-set id.  So an export that finds a
    different manifest under the same id refuses instead of overwriting the pin of earlier runs.
    """
    target = out / TESTSETS_DIR / testset / "manifest.json"
    if target.exists():
        if testset_module.manifest_sha256(target) != testset_module.manifest_sha256(source):
            raise ValueError(
                f"{target}: the test set {testset!r} is already pinned by another manifest; "
                "another selection is another test-set id"
            )
        return target
    testset_module.write_manifest(target, pinned)
    return target


PROTOCOL_FIELDS: tuple[str, ...] = (
    "eval_version",
    "fingerprint",
    "instructions_sha256",
    "answer_format_version",
)
"""What a local header carries once it records the protocol as it was at run time."""


def _protocol_of(local: dict, rows_path: Path) -> tuple[int, str, str, int]:
    """Return the protocol a local run recorded: version, fingerprint, instructions, format.

    The four facts are *copied* from the run's header, never recomputed: a prompt changed since
    the run would otherwise give a hash that describes today's code, not the run.

    Raises:
        ValueError: if the header lacks one of them (a run made before the protocol was recorded), or if the version
            pair is not an entry of ``protocol.json`` (a run made with an unrecorded protocol).
    """
    missing = [key for key in PROTOCOL_FIELDS if key not in local]
    if missing:
        raise ValueError(
            f"{rows_path.name}: the local header has no {', '.join(missing)}; the run was made "
            "before the protocol was recorded — run it again"
        )
    version = local["eval_version"]
    fingerprint = str(local["fingerprint"])
    if not protocol.is_recorded(version, fingerprint):
        raise ValueError(
            f"{rows_path.name}: eval version {version!r} with fingerprint {fingerprint[:12]} is "
            f"not in {protocol.PROTOCOL_PATH.name}; a run made with an unrecorded protocol "
            "cannot be committed"
        )
    return (
        int(version),
        fingerprint,
        str(local["instructions_sha256"]),
        int(local["answer_format_version"]),
    )


def _export_one(  # noqa: PLR0913  # the facts of one run file
    rows: list[ResultRow],
    local: dict,
    *,
    rows_path: Path,
    entries: dict[str, dict],
    resolved: dict[str, ResolvedItem],
    corpora: Corpora,
    manifest: Path,
    testset: str,
    machine: str,
    out: Path,
    label: str | None,
) -> ExportedRun:
    """Write one local run as a committed run file, refusing anything a rebuild could not check."""
    unknown = [row.id for row in rows if row.id not in entries]
    if unknown:
        raise ValueError(
            f"{rows_path.name}: {', '.join(sorted(unknown)[:5])} belong to another test set "
            f"(the manifest {testset!r} has no such items)"
        )
    missing = sorted(set(entries) - {row.id for row in rows})
    if missing:
        raise ValueError(
            f"{rows_path.name}: no row for {', '.join(missing[:5])}"
            + (" …" if len(missing) > 5 else "")
            + "; a committed run covers the whole test set"
        )
    ordered = sorted(rows, key=lambda row: list(entries).index(row.id))
    eval_version, fingerprint, instructions, answer_format = _protocol_of(local, rows_path)
    revision = str(local.get("tool_revision", "unknown"))
    header = {
        "schema": RUN_SCHEMA,
        "type": RUN_TYPE,
        "run_id": run_id(
            date=str(local.get("date", "")),
            machine=machine,
            model=str(local.get("model", "")),
            model_info=local.get("model_info") or {},
            mode=str(local.get("mode", DEFAULT_MODE)),
            temperature=float(local.get("temperature", 0.0)),
            repeat=int(local.get("repeat", 1)),
            explainer=local.get("explainer"),
            label=label,
        ),
        "testset": testset,
        "manifest_sha256": testset_module.manifest_sha256(manifest),
        "machine": machine,
        "kind": kind_of({**local, "gpu_sampler": sampler_record(local.get("gpu_sampler"))}),
        "date": str(local.get("date", "")),
        "tool_revision": revision,
        "eval_version": eval_version,
        "fingerprint": fingerprint,
        "ollama_version": str(local.get("ollama_version", "")),
        "model": str(local.get("model", "")),
        "model_info": dict(local.get("model_info") or {}),
        "mode": str(local.get("mode", DEFAULT_MODE)),
        "explainer": local.get("explainer"),
        "input": "model",
        "temperature": float(local.get("temperature", 0.0)),
        "request_profile": dict(local.get("request_profile") or {}),
        "num_ctx": local.get("num_ctx"),
        "seed": local.get("seed"),
        "repeat": int(local.get("repeat", 1)),
        "instructions_sha256": instructions,
        "answer_format_version": answer_format,
        "isolated": bool(local.get("isolated", False)),
        "loaded_before": list(local.get("loaded_before") or ()),
        "gpu_sampler": sampler_record(local.get("gpu_sampler")),
        "vram_baseline_mb": local.get("vram_baseline_mb"),
        "cut_fields": 0,
        "texts": len(ordered),
    }
    cut: list[bool] = []
    items = []
    for row in ordered:
        entry = entries[row.id]
        resolved_item = resolved[entry["id"]]
        items.append(
            item_record(
                row,
                entry,
                redactor=_redactor_of(resolved_item, corpora),
                cut=cut,
                labels=resolved_item.labels,
            )
        )
    header["cut_fields"] = sum(cut)

    target = out / RUNS_DIR / testset / f"{header['run_id']}.jsonl"
    if target.exists():
        raise ValueError(f"{target}: the run {header['run_id']} exists already; use --label")
    write_run(target, header, items)
    if problems := validate_run(target, corpora, manifest_path=manifest):
        target.unlink()
        raise ValueError("strict validation refused the run: " + "; ".join(problems))
    return ExportedRun(
        path=target,
        run_id=header["run_id"],
        items=len(items),
        cut_fields=header["cut_fields"],
        eval_version=header["eval_version"],
    )


def _redactor_of(resolved: ResolvedItem, corpora: Corpora) -> Redactor:
    """Return the caps of one resolved item: its own text is what a field may not repeat.

    An item of a restricted corpus hashes with the key made from that corpus.
    """
    key = corpora.adapter(resolved.corpus).redaction_key() if resolved.restricted else b""
    return Redactor.for_item(
        resolved.source, resolved.reference, restricted=resolved.restricted, key=key
    )
