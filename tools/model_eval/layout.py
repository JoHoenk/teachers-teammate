"""Where the parts of the repository format live: the directory, the names, the machine files.

A run sits at ``<root>/runs/<testset>/<run-id>.jsonl``, the manifest that pins it at
``<root>/testsets/<testset>/manifest.json`` and the machine it ran on at
``<root>/machines/<id>.json``.  This module owns that directory and the names in it: what a run is
called, which files belong to it, and what a machine file holds.  It must not import
:mod:`.validation`, :mod:`.export`, :mod:`.index`, :mod:`.report` or the command line.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

INDEX_NAME = "INDEX.md"
"""The generated list of all runs, next to ``runs/``."""

MACHINES_DIR = "machines"
TESTSETS_DIR = "testsets"
RUNS_DIR = "runs"
"""Where the parts of the repository format live, below ``evaluations/proofreading/``."""

# ── what a run is called ──────────────────────────────────────────────────────


def model_slug(tag: str) -> str:
    """Return a model tag in the form a run id uses: lower case, no registry prefix, no marks.

    ``hf.co/bartowski/Qwen3-8B-GGUF:Q4_K_M`` becomes ``bartowski-qwen3-8b-gguf-q4_k_m``, so the
    id survives being a file name on every platform.
    """
    slug = tag.strip().lower()
    for prefix in ("hf.co/", "https://hf.co/"):
        if slug.startswith(prefix):
            slug = slug[len(prefix) :]
    return slug.replace("/", "-").replace(":", "-")


def digest8(model_info: dict) -> str:
    """Return the first eight hex digits of a model digest, ``unknown`` when there is none."""
    digest = str(model_info.get("digest", "")).strip()
    if ":" in digest:  # Ollama writes "sha256:<hex>"
        digest = digest.split(":", 1)[1]
    return digest[:8] if digest else "unknown"


def date_of(iso: str) -> str:
    """Return the date part (``YYYY-MM-DD``) of a run's ISO timestamp, as id and index use it."""
    return iso.strip()[:10]


def run_id(  # noqa: PLR0913  # one part of the id per argument, as the scheme reads
    *,
    date: str,
    machine: str,
    model: str,
    model_info: dict,
    mode: str,
    temperature: float,
    repeat: int,
    explainer: str | None = None,
    label: str | None = None,
) -> str:
    """Return the id of a run, which is also its file name.

    ``<date>_<machine>_<model-slug>_<digest8>_<mode>[_<explainer>]_T<t>_r<n>``; a ``label``
    replaces the ``r<n>`` suffix when two runs of one configuration would otherwise collide.
    """
    parts = [date_of(date), machine, model_slug(model), digest8(model_info), mode]
    if explainer:
        parts.append(model_slug(explainer))
    parts.append(f"T{temperature:g}")
    parts.append(label or f"r{repeat}")
    return "_".join(parts)


def kind_of(header: dict) -> str:
    """Return ``speed`` for a run that was isolated and sampled, ``quality`` for everything else."""
    sampled = header.get("gpu_sampler") is not None
    return "speed" if header.get("isolated") and sampled else "quality"


# ── the machine file ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Machine:
    """A test machine, written by hand once."""

    id: str
    gpu: dict
    cpu: str
    ram_gib: float
    os: str
    accelerator_stack: str
    notes: str = ""

    @property
    def gpu_name(self) -> str:
        """Return the card's name, as a report or a sampler names it."""
        return str(self.gpu.get("name", ""))


MACHINE_SCHEMA = 1
"""Format version of ``machines/<id>.json``."""

PLACEHOLDER = "todo"
"""What a machine file says while a value is still missing; :func:`read_machine` refuses it."""


def _refuse_placeholder(path: Path, key: str, value: object) -> None:
    """Raise when a required text field of a machine file is empty or still the placeholder.

    A machine file is written by hand once, and a value nobody filled in would otherwise travel
    into every run header of that machine.
    """
    text = str(value).strip()
    if not text or text.casefold() == PLACEHOLDER:
        raise ValueError(f"{path}: {key!r} is still a placeholder")


def read_machine(path: Path) -> Machine:
    """Return the machine stored in *path*, checking that every required key is there.

    Raises:
        ValueError: if the file is not a machine file of this schema version, a required key is
            missing or of the wrong type, or a required text field is empty or still ``TODO``.
    """
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"{path}: a machine file is a JSON object")
    if record.get("schema") != MACHINE_SCHEMA:
        raise ValueError(f"{path}: unsupported machine schema {record.get('schema')!r}")
    for key in ("id", "gpu", "cpu", "ram_gib", "os", "accelerator_stack"):
        if key not in record:
            raise ValueError(f"{path}: the machine file has no {key!r}")
    if not isinstance(record["gpu"], dict) or "name" not in record["gpu"]:
        raise ValueError(f"{path}: 'gpu' needs a 'name'")
    if path.stem != record["id"]:
        raise ValueError(f"{path}: the file name and the id {record['id']!r} disagree")
    for key in ("cpu", "os", "accelerator_stack"):
        _refuse_placeholder(path, key, record[key])
    _refuse_placeholder(path, "gpu.name", record["gpu"]["name"])
    return Machine(
        id=str(record["id"]),
        gpu=dict(record["gpu"]),
        cpu=str(record["cpu"]),
        ram_gib=record["ram_gib"],
        os=str(record["os"]),
        accelerator_stack=str(record["accelerator_stack"]),
        notes=str(record.get("notes", "")),
    )


def machine_path(root: Path, machine: str) -> Path:
    """Return the file of machine *machine* in the repository directory *root*."""
    return root / MACHINES_DIR / f"{machine}.json"


def sampler_record(vendor: Any) -> dict | None:
    """Return the ``gpu_sampler`` field of a header from what the local run recorded.

    A local run stores only the vendor of the sampler that ran on its own machine (a remote
    agent would record a dict, which passes through unchanged).
    """
    if isinstance(vendor, dict):
        return vendor
    if isinstance(vendor, str) and vendor:
        return {"kind": "local", "vendor": vendor}
    return None


def layout_root(path: Path) -> Path:
    """Return the repository directory that holds ``runs/``, ``testsets/`` and ``machines/``.

    A run sits at ``<root>/runs/<testset>/<run-id>.jsonl``.
    """
    parent = path.parent
    if parent.parent.name == RUNS_DIR:
        return parent.parent.parent
    return parent.parent


def manifest_for(path: Path, header: dict | None = None) -> Path:
    """Return the manifest that pins the run in *path*: ``testsets/<testset>/manifest.json``."""
    root = layout_root(path)
    if header is not None and header.get("testset"):
        return root / TESTSETS_DIR / str(header["testset"]) / "manifest.json"
    return root / TESTSETS_DIR / path.parent.name / "manifest.json"


def run_files(out: Path) -> list[Path]:
    """Return every run file below *out*."""
    return sorted((out / RUNS_DIR).glob("*/*.jsonl"))
