"""Where a run's numbers sit in the store, and how they are read back.

The store is the private repository ``teachers-teammate-evals``; this tool keeps its records in
``ocr/runs/`` below its root, one file per run.  A reading is never written here: it is the page's
text, and it stays in the local cache.

A run's file name says what it is, so a folder reads like a campaign:
``c1/reader--R--app--cpu--laptop--1a2b3c.ocr.jsonl``.  The name below ``ocr/runs/`` without the
suffix is what a report prints and what ``--runs`` and ``--baseline`` take, so a row can be pasted
back into a command.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from tools.evaluation.core.formats import FormatError, read_jsonl, write_jsonl
from tools.evaluation.core.hashing import sha256_json

from .records import Header, RunRecord, validate

OCR_DIR = "ocr"
"""The tool's own folder at the store's root: the proofreading tool's records are elsewhere."""

RUNS_DIR = "runs"
"""Below :data:`OCR_DIR`: one file per run."""

RUN_SUFFIX = ".ocr.jsonl"
"""A run's file: one object, naming itself, so a stray file is not read as a run."""

CPU = "cpu"
GPU = "gpu"
"""The device a run was measured on, as its name spells it; ``gpu`` never collides with a prompt."""

_NOT_IN_NAME = re.compile(r"[^A-Za-z0-9._-]+")
"""What a file name replaces with ``_``: everything but letters, digits, dot, dash, underscore."""
_NAME_HASH = 6
"""How many hex digits of the name's hash: two runs of one campaign, and no longer."""


@dataclass(frozen=True)
class Stored:
    """One run as the store holds it: its name below ``ocr/runs/``, and its numbers."""

    name: str
    record: RunRecord

    @property
    def header(self) -> Header:
        """The run's header, as a row reads it."""
        return self.record.header


def runs_dir(store: Path) -> Path:
    """Return the folder a store keeps this tool's runs in."""
    return store / OCR_DIR / RUNS_DIR


def run_name(header: Header) -> str:
    """Return the file name of one run: the model, the preset, the ask, the device, the machine.

    A short hash closes the name over what the rest does not spell out — the detector's numbers,
    the side of the set and the language — so two runs that differ only there are two files.
    """
    parts = [
        _NOT_IN_NAME.sub("_", header.model).strip("_"),
        header.preset,
        header.prompt,
        CPU if header.cpu_only else GPU,
        _NOT_IN_NAME.sub("_", header.machine).strip("_"),
        sha256_json([list(header.detector), header.split, header.language])[:_NAME_HASH],
    ]
    return "--".join(parts)


def run_path(store: Path, name: str, *, folder: str = "") -> Path:
    """Return the file of the run *name*; *folder* groups a campaign inside ``runs/``."""
    leaf = f"{_NOT_IN_NAME.sub('_', name)}{RUN_SUFFIX}"
    return runs_dir(store) / folder / leaf if folder else runs_dir(store) / leaf


def name_of(store: Path, path: Path) -> str:
    """Return the name of the run in *path*: its place below ``runs/``, without the suffix."""
    return str(path.relative_to(runs_dir(store)))[: -len(RUN_SUFFIX)]


def run_files(store: Path, *, folder: str | None = None) -> list[Path]:
    """Return the run files of *store*, of one *folder* of them, or of all of them."""
    root = runs_dir(store)
    where = root / folder if folder else root
    return sorted(path for path in where.rglob(f"*{RUN_SUFFIX}") if path.is_file())


def write(path: Path, record: RunRecord, *, replace: bool = False) -> Path:
    """Write *record* to *path*, once and whole, and return the path.

    Raises:
        FileExistsError: when a run of this name is there and *replace* is not asked for.  The
            store is a git repository, so a replaced run can still be recovered.
    """
    if path.exists() and not replace:
        raise FileExistsError(
            f"{path}: a run of this name is there; name another --folder, or pass --replace"
        )
    validate(record.to_dict())
    path.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(path, [record.to_dict()])
    return path


def read(path: Path) -> RunRecord:
    """Return the run *path* holds.

    Raises:
        FormatError: if the file is not one record of this tool's format.
    """
    records = read_jsonl(path)
    if len(records) != 1:
        raise FormatError(f"{path.name}: one run per file, this one holds {len(records)}")
    return RunRecord.from_dict(records[0])


def stored(store: Path, *, folder: str | None = None) -> list[Stored]:
    """Return every run of *store* with its name, in the order the files sort."""
    return [Stored(name_of(store, path), read(path)) for path in run_files(store, folder=folder)]


def pick(found: list[Stored], name: str) -> Stored:
    """Return the run *name* names among *found*, by its path or by its bare name.

    Raises:
        ValueError: when no run holds the name, or when two folders do.
    """
    by_name = {one.name: one for one in found}
    if name in by_name:
        return by_name[name]
    matches = [one for one in found if one.name.rsplit("/", 1)[-1] == name]
    if len(matches) > 1:
        raise ValueError(
            f"{name}: two runs hold that name, so name the folder too: "
            + ", ".join(one.name for one in matches)
        )
    if matches:
        return matches[0]
    raise ValueError(f"{name}: no such run here; the ones read hold: " + ", ".join(sorted(by_name)))


def records_of(store: Path, *names: str, folder: str | None = None) -> list[Stored]:
    """Return the runs *names* name, or every run when no name is given."""
    found = stored(store, folder=folder)
    return [pick(found, name) for name in names] if names else found
