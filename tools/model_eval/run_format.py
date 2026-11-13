"""One run file: its schema, its typed items, and how it is read and written.

A committed run is one JSON object per line (JSONL), sorted keys, so ``git diff`` shows what
changed: line 1 is the header (:data:`HEADER_KEYS`, ``type`` is ``"run"`` and ``run_id`` is the
file name), line 2… one item per text of the test set (:data:`ITEM_KEYS`, ``item`` is the
manifest's id).  The corpora are licensed and are not part of this project, so an item holds the
model's edits as positions plus capped replacements, its notes as categories plus capped
explanations, and every outcome as numbers.

This module owns the format of one file and nothing else: no directory, no validation, no export.
It must not import :mod:`.testset` (the manifest is passed in as a dict), :mod:`.layout`,
:mod:`.validation`, :mod:`.export`, :mod:`.index`, :mod:`.report` or the command line.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from teachers_teammate.domain.correction_review import tokenize

from .answer_mode import AnswerMode, parse_mode
from .performance import Performance
from .redaction import MAX_REPLACEMENT_WORDS

RUN_SCHEMA = 1
"""Format version of a run file; a reader refuses any other."""

RUN_TYPE = "run"
"""What line 1 says it is, so a file of items alone is not mistaken for a run."""

REPLACEMENT_WORDS = MAX_REPLACEMENT_WORDS
"""Re-exported for the validator and the docs: a stored replacement keeps at most this many."""

HEADER_KEYS: tuple[str, ...] = (
    "schema",
    "type",
    "run_id",
    "testset",
    "manifest_sha256",
    "provider",
    "machine",
    "kind",
    "date",
    "tool_revision",
    "eval_version",
    "fingerprint",
    "ollama_version",
    "model",
    "model_info",
    "mode",
    "explainer",
    "input",
    "temperature",
    "request_profile",
    "num_ctx",
    "seed",
    "repeat",
    "instructions_sha256",
    "answer_format_version",
    "isolated",
    "loaded_before",
    "gpu_sampler",
    "vram_baseline_mb",
    "cut_fields",
    "texts",
)
"""Every field the header of a run carries."""

ITEM_KEYS: tuple[str, ...] = (
    "item",
    "outcome",
    "scores",
    "reference",
    "edits",
    "notes",
    "unclear",
    "output_sha256",
    "performance",
)
"""Every field one item line carries."""

SCORE_KEYS: tuple[str, ...] = (
    "tp",
    "fp",
    "fn",
    "unchanged",
    "explained",
    "needed",
    "orphans",
    "category_checked",
    "category_agreed",
    "marks_added",
    "notes_linked",
)
"""The numbers of one item; ``notes_linked`` says whether its notes point at its edits."""

OUTCOME_KEYS: tuple[str, ...] = (
    "failed",
    "fallback",
    "has_review",
    "warning_code",
    "pieces",
    "seconds",
)
"""The outcome of one item; ``pieces`` is how many requests the text was sent in."""

NOTE_KEYS: tuple[str, ...] = ("edit", "category", "explanation", "explanation_sha256", "orphan")
"""The fields of one note."""

KINDS = frozenset({"quality", "speed"})
"""``speed`` only for an isolated, sampled run; anything else measured something else too."""

INPUTS = frozenset({"model", "reference"})
"""What the explaining model was given: the model's own correction, or the reference one."""

WARNING_CODES = frozenset({"", "fallback", "review_partial", "unplaced", "other"})
"""What ``outcome.warning_code`` may say; the text of a warning is not stored."""

TEXT_FIELD_NAMES = frozenset({"source", "reference", "output"})
"""Field names that hold a text; a *string* under one of them must never appear in a run."""

_MODEL_KEYS = frozenset({"digest", "quantization", "parameter_size", "family"})
"""The facts of a model tag, as ``ServerProbe.model_info`` reports them."""

_PERFORMANCE_KEYS = frozenset(
    {
        "requests",
        "loaded",
        "shared",
        "vram_peak_mb",
        "vram_baseline_mb",
        "seed",
        "repeat",
        "model",
        "prompt_tokens",
        "answer_tokens",
        "load_s",
        "prompt_eval_s",
        "generation_s",
        "total_s",
        "wall_s",
        "done_reason",
        "failed",
        "name",
        "size_bytes",
        "size_vram_bytes",
        "context_length",
    }
)
"""The parts of a performance record (``Performance.to_dict``, unchanged)."""

_FIELD_KEYS = frozenset(
    {
        # a stored replacement, and the sampler record of a header
        "r",
        "h",
        "n",
        "kind",
        "source",
        "vendor",
        "compact_json",
        "interval_s",
        "cards",
        "agent_sha256",
    }
)
"""The other field names a run uses."""

KNOWN_KEYS: frozenset[str] = (
    frozenset((*HEADER_KEYS, *ITEM_KEYS, *SCORE_KEYS, *OUTCOME_KEYS, *NOTE_KEYS))
    | _MODEL_KEYS
    | _PERFORMANCE_KEYS
    | _FIELD_KEYS
)
"""Every field name the format uses; a string under any other name must be short (see
:func:`~.validation.validate_run`), so a corpus sentence has nowhere to hide."""


def is_number(value: Any) -> bool:
    """Whether *value* is a number that is not a boolean."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ── the run file ──────────────────────────────────────────────────────────────


def profile_items(profile: Mapping[str, Any] | None) -> tuple[tuple[str, object], ...]:
    """Return a header's request profile as sorted items, so two profiles compare by value."""
    return tuple(sorted((str(key), value) for key, value in (profile or {}).items()))


def host_of(header: Mapping[str, Any]) -> str:
    """Return where the run of *header* ran: its machine, else its provider, else ``""``.

    Reads the dict as it is, so the index can show a run the validator has not judged.
    """
    return str(header.get("machine") or header.get("provider") or "")


@dataclass(frozen=True)
class RunHeader:
    """The facts of a run's header that a reader groups, selects and orders runs by, typed.

    The header stays a dict in :class:`StoredRun` because the validator has to judge malformed
    ones; everything that reads a run that passed validation asks :attr:`StoredRun.meta` instead
    of indexing the dict, so a renamed key is found in one place.
    """

    run_id: str
    testset: str
    manifest_sha256: str
    eval_version: int
    provider: str
    """Who ran the model: ``ollama`` for a model on an Ollama server of ours, else the name of
    the provider module (see :func:`.layout.provider_names`)."""
    machine: str
    """The machine an Ollama run ran on; ``""`` for a provider that ran on none of ours."""
    date: str
    model: str
    digest: str
    """The digest of the weights, ``""`` when the header records none."""
    mode: AnswerMode
    explainer: str | None
    temperature: float
    num_ctx: int
    repeat: int
    request_profile: tuple[tuple[str, object], ...]

    @property
    def host(self) -> str:
        """Return where the run ran: its machine, or its provider when no machine of ours ran it.

        The name the index shows and ``--machine`` takes, so a run listed under ``openai`` is
        selected with ``--machine openai``.
        """
        return self.machine or self.provider

    @classmethod
    def from_dict(cls, header: Mapping[str, Any]) -> RunHeader:
        """Return the typed header of *header*.

        Raises:
            KeyError: if a field the readers need is missing.
            ValueError: if a field has a value of the wrong kind.
        """
        explainer = header.get("explainer")
        return cls(
            run_id=str(header["run_id"]),
            testset=str(header["testset"]),
            manifest_sha256=str(header["manifest_sha256"]),
            eval_version=int(header["eval_version"]),
            provider=str(header["provider"]),
            machine="" if header["machine"] is None else str(header["machine"]),
            date=str(header.get("date", "")),
            model=str(header["model"]),
            digest=str((header.get("model_info") or {}).get("digest", "")),
            mode=parse_mode(str(header["mode"])),
            explainer=None if explainer is None else str(explainer),
            temperature=float(header["temperature"]),
            num_ctx=int(header["num_ctx"]),
            repeat=int(header["repeat"]),
            request_profile=profile_items(header.get("request_profile")),
        )


@dataclass(frozen=True)
class StoredRun:
    """One run file as it was read back: its header and its item lines, still untyped.

    The lines stay dicts because the validator has to judge malformed ones; :meth:`items` types
    them for the reports, and is meant for a run that passed validation.
    """

    path: Path
    header: dict
    records: tuple[dict, ...]

    @property
    def run_id(self) -> str:
        """Return the run id, which the file name and the header agree on."""
        return str(self.header["run_id"])

    @property
    def meta(self) -> RunHeader:
        """Return the typed header, for a run that passed validation."""
        return RunHeader.from_dict(self.header)

    def items(self, manifest: dict) -> tuple[StoredItem, ...]:
        """Return the item lines as typed items, with the manifest entry of each.

        Raises:
            ValueError: if the manifest has no entry for a line, or a line lacks a field.
        """
        entries = {str(entry["id"]): entry for entry in manifest.get("items", ())}
        typed = []
        for record in self.records:
            item = str(record.get("item", ""))
            entry = entries.get(item)
            if entry is None:
                raise ValueError(f"{self.path}: the manifest has no item {item!r}")
            typed.append(StoredItem.from_record(record, self.header, entry))
        return tuple(typed)


@dataclass(frozen=True)
class StoredItem:
    """One item line of a run, typed: what a report needs, and nothing that holds a text.

    It satisfies :class:`~.scoring.ScoredItem`, so :func:`~.report.summarise` builds the same
    table from a committed run as from a local cache.  ``scores`` keeps every stored number, so a
    metric added later needs no new field here, and ``record`` keeps the line itself, for the
    views that need the edits and notes.
    """

    model: str
    temperature: float
    mode: AnswerMode
    language: str
    item: str
    kind: str
    band: str
    """The band the recipe drew the text for, from the manifest entry; empty when it has none."""
    words: int
    sentences_total: int
    true_positives: int
    false_positives: int
    false_negatives: int
    unchanged: int
    explained: int
    needed: int
    orphans: int
    failed: bool
    fallback: bool
    marks_added: bool
    seconds: float
    performance: Performance | None
    scores: Mapping[str, float]
    """Every number of the line, the required ones included (``notes_linked`` as 0 or 1)."""
    record: Mapping[str, Any]
    """The line as it was stored, for the views that need the edits and the notes."""

    @classmethod
    def from_record(cls, record: dict, header: dict, entry: dict) -> StoredItem:
        """Return the typed item of one line, its run header and its manifest entry.

        Raises:
            ValueError: if the line lacks a field this needs, naming the item.
        """
        item = str(record.get("item", ""))
        try:
            scores = record["scores"]
            outcome = record["outcome"]
            performance = record.get("performance")
            return cls(
                model=str(header["model"]),
                temperature=float(header["temperature"]),
                mode=parse_mode(str(header["mode"])),
                language=str(entry["language"]),
                item=item,
                kind=str(entry["kind"]),
                band=str(entry.get("band", "")),
                words=int(entry["source"]["words"]),
                sentences_total=len(entry.get("sentence_sha256") or ()),
                true_positives=int(scores["tp"]),
                false_positives=int(scores["fp"]),
                false_negatives=int(scores["fn"]),
                unchanged=int(scores["unchanged"]),
                explained=int(scores["explained"]),
                needed=int(scores["needed"]),
                orphans=int(scores["orphans"]),
                failed=bool(outcome["failed"]),
                fallback=bool(outcome["fallback"]),
                marks_added=bool(scores["marks_added"]),
                seconds=float(outcome["seconds"]),
                performance=Performance.from_dict(performance) if performance else None,
                scores={
                    key: int(value) if isinstance(value, bool) else value
                    for key, value in scores.items()
                },
                record=record,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"item {item!r} has no {error}") from error


def read_run(path: Path) -> StoredRun:
    """Return the run stored in *path*, refusing an unknown schema and a stray file name.

    Raises:
        ValueError: if line 1 is not a header of this schema version, or its ``run_id`` is not the
            file name (a renamed file is not evidence of anything).
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{path}: an empty run file")
    header = json.loads(lines[0])
    if not isinstance(header, dict):
        raise ValueError(f"{path}: line 1 is not a header object")
    version = header.get("schema")
    if version != RUN_SCHEMA:
        raise ValueError(f"{path}: unsupported run schema {version!r}")
    if header.get("type") != RUN_TYPE:
        raise ValueError(f"{path}: line 1 is not a run header (type {header.get('type')!r})")
    if header.get("run_id") != path.stem:
        raise ValueError(f"{path}: the header names the run {header.get('run_id')!r}")
    records = []
    for number, line in enumerate(lines[1:], start=2):
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{number}: an item line is not an object")
        records.append(record)
    return StoredRun(path=path, header=header, records=tuple(records))


def write_run(path: Path, header: dict, items: Sequence[dict]) -> None:
    """Write *header* and *items* to *path* as JSONL with sorted keys, replacing the file.

    Sorted keys and one item per line keep ``git diff`` readable: a new run is one file, and a
    corrected one shows the lines that changed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(header, ensure_ascii=False, sort_keys=True)]
    lines.extend(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def warning_code(warning: str) -> str:
    """Return the short code of a warning, because its text is not stored.

    The warnings the corrector produces are named; anything else is ``other``.  A run then
    records *that* something went wrong without carrying a sentence of the answer into the
    repository.

    A text that was sent in pieces joins the warnings of its pieces
    (:func:`~teachers_teammate.infrastructure.correction._join_warnings`), so the codes are
    looked for inside the warning, and the more specific code wins.  A review that covers only
    part of the text (``review_partial``: some pieces fell back, the others kept their
    explanations) says more than ``fallback`` (the whole text fell back), because the chunking
    wrapper adds that warning only when some pieces did get a review.  Then comes a correction
    the model could not place.  A failed text stays ``failed`` whatever its warning says.
    """
    from teachers_teammate.infrastructure.correction import (  # noqa: PLC0415
        FALLBACK_WARNING,
        MISSING_REVIEW_WARNING,
    )

    if not warning:
        return ""
    if MISSING_REVIEW_WARNING in warning:
        return "review_partial"
    if FALLBACK_WARNING in warning:
        return "fallback"
    if "could not be placed in the text." in warning:
        return "unplaced"
    return "other"


# ── what the caps keep, per item ──────────────────────────────────────────────


def apply_stored_edits(text: str, edits: Sequence[Sequence]) -> str:
    """Return *text* with ``[start, end, replacement]`` edits applied, tail first.

    The positions are over :func:`~teachers_teammate.domain.correction_review.tokenize` of *text*,
    whitespace tokens included, exactly as :func:`~.quality.ordered_edits` numbers
    them, so the edits are spliced into the token list, not the characters.  The result then
    differs from the model's raw output only in whitespace, which the scoring ignores too.

    The stored replacements are texts; an edit stored as ``{"h", "n"}`` cannot be applied, which is
    exactly why a reader of such an item stays at level 1.
    """
    tokens = tokenize(text)
    for start, end, replacement in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
        tokens[start:end] = [replacement]
    return "".join(tokens)
