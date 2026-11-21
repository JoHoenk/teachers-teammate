"""The machines the evaluations ran on, read from the store's registry.

A run names the machine it was made on, and a model-fit record names the machine and the device.
Both are ids of this registry, so a typo is refused instead of becoming a second machine that
nobody can compare with the first.  The registry is data: one JSON file per machine under
``<store>/machines/``, and this module only reads it.

A machine is not a device.  ``rx9070xt-server`` is one machine, measured on its card and on its
processor; the two are two devices of one machine, and a size measured on the card says nothing
about the processor.  The records keep them apart, and so does every report.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .formats import FormatError, check_format

FORMAT = 1
"""The version of a machine file; a file of another version is refused, not guessed at."""

MACHINES_DIR = Path("machines")
"""Where the registry lives, below the store root."""

DEVICES: tuple[str, ...] = ("gpu", "cpu")
"""The devices a measurement can be made on."""


@dataclass(frozen=True)
class Machine:
    """One machine of the registry: what it is, and what it was measured on."""

    id: str
    """The file name, and every record's ``machine``."""

    gpu_vendor: str
    gpu_name: str
    vram_mb: int
    cpu: str
    ram_gib: int
    os: str
    accelerator_stack: str
    """The driver stack; Ollama cannot report it, and two backends differ by it."""

    notes: str = ""
    """What a reader needs to compare two machines."""
    aliases: tuple[str, ...] = ()
    """The labels the records used before the registry.  Documentation, not ids: a command line
    that names one is refused like any other unknown machine."""

    def line(self) -> str:
        """Return the machine as one line: its id, its card and its processor."""
        return f"{self.id}: {self.gpu_name} ({self.vram_mb} MB), {self.cpu}"


def read_machine(path: Path) -> Machine:
    """Return the machine *path* holds.

    Raises:
        FormatError: the file is not a machine of this format, or a field it needs is missing.
    """
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FormatError(f"{path}: not JSON ({error})") from error
    if not isinstance(data, dict):
        raise FormatError(f"{path}: not a JSON object")
    check_format(data, FORMAT, path)
    gpu = data.get("gpu")
    if not isinstance(gpu, dict):
        raise FormatError(f"{path}: no gpu object")
    try:
        return Machine(
            id=str(data["id"]),
            gpu_vendor=str(gpu["vendor"]),
            gpu_name=str(gpu["name"]),
            vram_mb=int(gpu["vram_mb"]),
            cpu=str(data["cpu"]),
            ram_gib=int(data["ram_gib"]),
            os=str(data["os"]),
            accelerator_stack=str(data["accelerator_stack"]),
            notes=str(data.get("notes") or ""),
            aliases=tuple(str(alias) for alias in data.get("aliases") or ()),
        )
    except KeyError as missing:
        raise FormatError(f"{path}: no {missing.args[0]!r}") from missing


def known_machines(root: Path) -> dict[str, Machine]:
    """Return the registry of the store at *root*, keyed by id.

    Raises:
        ValueError: the store has no registry, naming the path that is missing.
        FormatError: a file is not a machine of this format, or two files name one id.
    """
    directory = root / MACHINES_DIR
    if not directory.is_dir():
        raise ValueError(f"{root}: no store of evaluation records, there is no {MACHINES_DIR}/")
    found: dict[str, Machine] = {}
    for path in sorted(directory.glob("*.json")):
        machine = read_machine(path)
        if machine.id in found:
            raise FormatError(f"{path.name}: the id {machine.id!r} is already in the registry")
        found[machine.id] = machine
    return found


def require_machine(root: Path, machine: str) -> Machine:
    """Return the machine *machine* names, from the registry of the store at *root*.

    Raises:
        ValueError: the store has no registry, or it does not hold the machine, naming the ones it
            does.
    """
    known = known_machines(root)
    if machine not in known:
        raise ValueError(
            f"unknown machine {machine!r}; the registry holds: " + ", ".join(sorted(known))
        )
    return known[machine]


__all__ = [
    "DEVICES",
    "FORMAT",
    "MACHINES_DIR",
    "Machine",
    "known_machines",
    "read_machine",
    "require_machine",
]
