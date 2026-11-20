"""The redacted run: the one file of the repository that holds scores, because its texts are cut.

A local run file holds the model's output, which is corpus text and may not be committed.  The
export turns it into a *redacted* run: the same shape as a run file (header, session and answer
lines), with ``scores`` in place of ``output``, the model's edits as character positions plus capped
replacements, and the review's explanations capped.  Everything a reader needs to check the run
without the corpora is there; nothing a licence forbids is.

The header repeats two facts the run file does not need: ``scorer_version``, because the numbers
were computed by that version of :func:`~..report.score.score`, and ``key_sha256``, because the
numbers are only meaningful against the key they were measured with.  A reader that finds either
missing refuses the file rather than showing numbers nobody can place.  Each answer names its own
text's ``key_part_sha256`` as well, so a key that moved for one text makes the runs that hold that
text stale and leaves the others alone.

This module owns the format and nothing else: :mod:`..publish` writes it, :mod:`..report` reads it,
and neither computes a number here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Any

from ..core.formats import (
    RUN_FORMAT,
    FormatError,
    check_format,
    extra_fields,
    read_jsonl,
    write_jsonl,
)
from ..core.performance import LoadedModel, RequestStats
from ..core.records import RunHeader, Session

REDACTED_FORMAT = 3
"""Format version of a redacted run file.

Version 3 names the scorer and the key as fields of the header instead of leaving them to the
header's unknown fields, and gives every answer the key part of its own text.
"""

SCORER_VERSION_KEY = "scorer_version"
"""The header field that names the scorer the numbers came from."""

KEY_SHA256_KEY = "key_sha256"
"""The header field that names the key the numbers were measured against."""

EXPORT_NAME = "export.json"
"""What the export's own manifest is called, beside the test sets and the runs it describes."""

EXPORT_FORMAT = 1
"""The version of :data:`EXPORT_NAME`'s shape."""


@dataclass(frozen=True)
class RedactedAnswer:
    """One text's numbers, its edits and its capped notes: an answer without its text.

    The counts are the ones :meth:`~..report.score.Scored.to_dict` writes, so a reader gets the
    same numbers a report of the local store would compute, and no text.
    """

    text: str
    """The text's id, ``<language>/<record id>``."""

    text_sha256: str
    """The hash of the question, so the numbers can be checked against the set they belong to."""

    key_part_sha256: str = ""
    """The hash of the key group of the text, so a key that moved for it makes this run stale."""

    scores: Mapping[str, float] = field(default_factory=dict)
    """Every number of the answer, flat, as the score cache stores them."""

    edits: tuple[tuple[int, int, Mapping[str, Any]], ...] = ()
    """The model's changes: character span, and the replacement as text or as hash and length."""

    notes: tuple[Mapping[str, Any], ...] = ()
    """The review's notes: which edit each explains, its category and its capped explanation."""

    failed: bool = False
    fallback: bool = False
    warning: str = ""
    unclear_count: int = 0
    requests: tuple[RequestStats, ...] = ()
    loaded: tuple[LoadedModel, ...] = ()
    shared: bool = False
    vram_baseline_mb: int | None = None
    vram_peak_mb: int | None = None
    pieces: int = 1
    seconds: float = 0.0
    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    _KNOWN: tuple[str, ...] = (
        "format",
        "type",
        "text",
        "text_sha256",
        "key_part_sha256",
        "scores",
        "edits",
        "notes",
        "failed",
        "fallback",
        "warning",
        "unclear_count",
        "requests",
        "loaded",
        "shared",
        "vram_baseline_mb",
        "vram_peak_mb",
        "pieces",
        "seconds",
    )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record; an empty optional field is left out."""
        record: dict[str, Any] = {
            "format": REDACTED_FORMAT,
            "type": "answer",
            "text": self.text,
            "text_sha256": self.text_sha256,
            "key_part_sha256": self.key_part_sha256,
            "scores": dict(self.scores),
            "failed": self.failed,
            "fallback": self.fallback,
        }
        if self.edits:
            record["edits"] = [[start, end, dict(field_of)] for start, end, field_of in self.edits]
        if self.notes:
            record["notes"] = [dict(note) for note in self.notes]
        if self.warning:
            record["warning"] = self.warning
        if self.unclear_count:
            record["unclear_count"] = self.unclear_count
        if self.requests:
            record["requests"] = [request.to_dict() for request in self.requests]
        if self.loaded:
            record["loaded"] = [model.to_dict() for model in self.loaded]
        if self.shared:
            record["shared"] = True
        if self.vram_baseline_mb is not None:
            record["vram_baseline_mb"] = self.vram_baseline_mb
        if self.vram_peak_mb is not None:
            record["vram_peak_mb"] = self.vram_peak_mb
        if self.pieces != 1:
            record["pieces"] = self.pieces
        if self.seconds:
            record["seconds"] = self.seconds
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> RedactedAnswer:
        """Build a redacted answer from :meth:`to_dict` output.

        Raises:
            FormatError: if the line is not a redacted answer of a format this reader knows.
        """
        check_format(record, REDACTED_FORMAT, "redacted answer line")
        if "output" in record:
            raise FormatError(
                f"{record.get('text', '?')}: the line holds an output, so it is a run file and not "
                "a redacted export"
            )
        if "scores" not in record:
            raise FormatError(f"{record.get('text', '?')}: the line holds no scores")
        return cls(
            text=str(record["text"]),
            text_sha256=str(record["text_sha256"]),
            key_part_sha256=str(record["key_part_sha256"]),
            scores={str(key): value for key, value in record["scores"].items()},
            edits=tuple(
                (int(start), int(end), dict(field_of))
                for start, end, field_of in record.get("edits", ())
            ),
            notes=tuple(dict(note) for note in record.get("notes", ())),
            failed=bool(record.get("failed", False)),
            fallback=bool(record.get("fallback", False)),
            warning=str(record.get("warning", "")),
            unclear_count=int(record.get("unclear_count", 0)),
            requests=tuple(RequestStats.from_dict(item) for item in record.get("requests", ())),
            loaded=tuple(LoadedModel.from_dict(item) for item in record.get("loaded", ())),
            shared=bool(record.get("shared", False)),
            vram_baseline_mb=record.get("vram_baseline_mb"),
            vram_peak_mb=record.get("vram_peak_mb"),
            pieces=int(record.get("pieces", 1)),
            seconds=float(record.get("seconds", 0.0)),
            extra=extra_fields(record, cls._KNOWN),
        )


@dataclass(frozen=True)
class RedactedHeader:
    """Line 1 of a redacted run: the run's own header, and the two facts its numbers need.

    The two facts are fields and not unknown header fields, so a reader that finds either missing
    refuses the file instead of showing numbers nobody can place.
    """

    header: RunHeader
    """The header the run file holds: what was asked, of which model, and where."""

    scorer_version: int
    """The version of :func:`~..report.score.score` that computed the numbers."""

    key_sha256: str
    """The hash of the set's key the numbers were measured against."""

    def to_dict(self) -> dict:
        """Return a JSON-serialisable header, the two facts beside the run's own fields."""
        return {
            **self.header.to_dict(),
            "format": REDACTED_FORMAT,
            SCORER_VERSION_KEY: self.scorer_version,
            KEY_SHA256_KEY: self.key_sha256,
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> RedactedHeader:
        """Build a redacted header from :meth:`to_dict` output.

        Raises:
            FormatError: if the line is not a redacted header of a format this reader knows, or it
                names no scorer or no key.
        """
        check_format(record, REDACTED_FORMAT, "redacted run header")
        for key in (SCORER_VERSION_KEY, KEY_SHA256_KEY):
            if not record.get(key):
                raise FormatError(f"the header names no {key}, so the numbers cannot be placed")
        rest = {
            key: value
            for key, value in record.items()
            if key not in (SCORER_VERSION_KEY, KEY_SHA256_KEY)
        }
        return cls(
            header=RunHeader.from_dict({**rest, "format": RUN_FORMAT}),
            scorer_version=int(record[SCORER_VERSION_KEY]),
            key_sha256=str(record[KEY_SHA256_KEY]),
        )


@dataclass(frozen=True)
class RedactedRun:
    """One redacted run file: a header, the sessions it took, and one answer per text."""

    header: RedactedHeader
    sessions: tuple[Session, ...] = ()
    answers: tuple[RedactedAnswer, ...] = ()

    @property
    def scorer_version(self) -> int:
        """Return the scorer the numbers came from."""
        return self.header.scorer_version

    @property
    def key_sha256(self) -> str:
        """Return the key the numbers were measured against."""
        return self.header.key_sha256

    def lines(self) -> list[dict]:
        """Return every line of the file, header first, as the dicts a writer stores."""
        return [
            self.header.to_dict(),
            *(session.to_dict() for session in self.sessions),
            *(answer.to_dict() for answer in self.answers),
        ]

    def answer(self, text: str) -> RedactedAnswer | None:
        """Return the answer of *text*, or ``None`` when the run has none."""
        return next((answer for answer in self.answers if answer.text == text), None)


def read_redacted(
    records: Iterable[Mapping[str, Any]], source: str = "redacted run"
) -> RedactedRun:
    """Return the redacted run made of *records*, the lines of one file in order.

    Raises:
        FormatError: if the file is empty, its first line is not a header, or a later line is
            neither a session nor a redacted answer.
    """
    lines = list(records)
    if not lines:
        raise FormatError(f"{source}: an empty run file")
    header = RedactedHeader.from_dict(lines[0])
    sessions: list[Session] = []
    answers: list[RedactedAnswer] = []
    for number, record in enumerate(lines[1:], start=2):
        kind = record.get("type")
        if kind == "session":
            sessions.append(Session.from_dict(record))
        elif kind == "answer":
            answers.append(RedactedAnswer.from_dict(record))
        else:
            raise FormatError(f"{source}:{number}: neither a session nor an answer (type {kind!r})")
    return RedactedRun(header=header, sessions=tuple(sessions), answers=tuple(answers))


def read_redacted_file(path: Any) -> RedactedRun:
    """Return the redacted run stored in *path*.

    Raises:
        FormatError: if the file is not a redacted run of a format this reader knows.
    """
    return read_redacted(read_jsonl(path), source=str(path))


def write_redacted(path: Any, run: RedactedRun) -> None:
    """Write *run* to *path*, replacing the file: the header first, then sessions and answers."""
    write_jsonl(path, run.lines())


@dataclass(frozen=True)
class ExportedSet:
    """One test set of an export: what pins it, and what its numbers were measured against."""

    testset: str
    manifest_sha256: str
    """The hash of the copied ``manifest.json``, so a reader knows which set it is reading."""

    question_sha256: str
    """The hash of the set's question: a rebuilt set is another set, and its runs are about others."""

    key_sha256: str
    """The hash of the set's key: the numbers of a run are only meaningful against this one."""

    texts: int

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record."""
        return {
            "manifest_sha256": self.manifest_sha256,
            "question_sha256": self.question_sha256,
            "key_sha256": self.key_sha256,
            "texts": self.texts,
        }

    @classmethod
    def from_dict(cls, testset: str, record: Mapping[str, Any]) -> ExportedSet:
        """Build a set record from :meth:`to_dict` output."""
        return cls(
            testset=testset,
            manifest_sha256=str(record["manifest_sha256"]),
            question_sha256=str(record["question_sha256"]),
            key_sha256=str(record["key_sha256"]),
            texts=int(record["texts"]),
        )


@dataclass(frozen=True)
class ExportManifest:
    """What an export holds: its sets, the scorer that made its numbers, and the day it was made.

    The manifest is the export's own, not the test set's: the set's ``manifest.json`` is copied
    byte for byte, so its hash still matches what a run header records, and this file says which
    sets were copied and what their numbers were measured against.
    """

    exported: str
    scorer_version: int
    testsets: Mapping[str, ExportedSet] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record."""
        return {
            "format": EXPORT_FORMAT,
            "exported": self.exported,
            "scorer_version": self.scorer_version,
            "testsets": {name: entry.to_dict() for name, entry in sorted(self.testsets.items())},
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> ExportManifest:
        """Build an export manifest from :meth:`to_dict` output.

        Raises:
            FormatError: if the record is not an export manifest of a format this reader knows.
        """
        check_format(record, EXPORT_FORMAT, "export manifest")
        return cls(
            exported=str(record["exported"]),
            scorer_version=int(record["scorer_version"]),
            testsets={
                str(name): ExportedSet.from_dict(str(name), entry)
                for name, entry in (record.get("testsets") or {}).items()
            },
        )

    def check(self, run: RedactedRun) -> str:
        """Return why *run* does not belong to this export, or ``""`` when it does.

        A run is placed by three facts: the set it was asked about, the key its numbers were
        measured against, and the scorer that computed them.  A run that disagrees with the export
        on any of them is not shown, because its numbers would be read as if they belonged here.
        """
        header = run.header.header
        name = header.input.testset
        entry = self.testsets.get(name)
        if entry is None:
            return f"the export holds no test set {name!r}"
        if header.input.question_sha256 != entry.question_sha256:
            return f"the run was asked about another {name!r} than the export holds"
        if run.key_sha256 != entry.key_sha256:
            return (
                f"the run was scored against key {run.key_sha256[:12]}, the export holds "
                f"{entry.key_sha256[:12]}; run export --rescore"
            )
        if run.scorer_version != self.scorer_version:
            return (
                f"the run was scored by scorer {run.scorer_version}, the export holds "
                f"{self.scorer_version}; run export --rescore"
            )
        return ""


def read_export_manifest(path: Path) -> ExportManifest:
    """Return the export manifest stored in *path*.

    Raises:
        FormatError: if the file is not an export manifest of a format this reader knows.
        FileNotFoundError: if there is no such file.
    """
    return ExportManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def write_export_manifest(path: Path, manifest: ExportManifest) -> None:
    """Write *manifest* to *path*, sorted, so two exports of one store differ only in their runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def merge_export_manifest(path: Path, manifest: ExportManifest) -> ExportManifest:
    """Write *manifest* to *path*, keeping the sets an earlier export committed and this one lacks.

    An export of one test set must not drop the sets another export wrote: the runs of those sets
    are still in the tree, and their numbers are still measured against them.  The sets of
    *manifest* win, because they are the ones this export just copied.

    Returns:
        The manifest that was written.
    """
    try:
        earlier = read_export_manifest(path)
    except FileNotFoundError:
        merged = manifest
    else:
        merged = replace(manifest, testsets={**earlier.testsets, **manifest.testsets})
    write_export_manifest(path, merged)
    return merged


__all__ = [
    "EXPORT_FORMAT",
    "EXPORT_NAME",
    "KEY_SHA256_KEY",
    "REDACTED_FORMAT",
    "SCORER_VERSION_KEY",
    "ExportManifest",
    "ExportedSet",
    "RedactedAnswer",
    "RedactedHeader",
    "RedactedRun",
    "merge_export_manifest",
    "read_export_manifest",
    "read_redacted",
    "read_redacted_file",
    "write_export_manifest",
    "write_redacted",
]
