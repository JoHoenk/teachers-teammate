"""The protocol table: which fingerprints a version accepts, and which version a run measured.

The old file recorded one fingerprint per version, so every additive change — a new prompt variant,
a new sampling knob — raised the version and re-stamped every run.  Here a version lists
the fingerprints it **accepts**, and a fingerprint belongs to exactly one version:

* an **additive** change (the same requests, a new variant beside them) adds its fingerprint to the
  *current* version, after the check that every existing request hashes as before;
* a **request-side** change raises the version by hand, as before.

Because the compare key uses ``instructions`` and not the fingerprint, neither kind of change
splits a group by itself.  The capture of the request side stays in ``run/protocol``; this module is
the table and its lookup, and it reads one JSON file it is given.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

TABLE_PATH = Path(__file__).with_name("protocol.json")
"""The version file, next to this module (``//tools/evaluation:core`` carries it as data)."""

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ProtocolTableError(ValueError):
    """The version file cannot be read, or breaks its own rules."""


def read_table(path: Path | None = None) -> dict[str, Any]:
    """Return the version file, refusing one that cannot be read or is not an object.

    The committed file is read once per process: every lookup of a fingerprint would otherwise
    re-read and re-parse it, and a report looks up one fingerprint per run.

    Raises:
        ProtocolTableError: if the file is missing, is not JSON, or does not hold a JSON object.
    """
    return dict(_read_cached(TABLE_PATH if path is None else path))


@lru_cache(maxsize=8)
def _read_cached(source: Path) -> dict[str, Any]:
    """Return the version file at *source*, parsed once per path."""
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolTableError(f"{source}: cannot be read as JSON ({error})") from error
    if not isinstance(data, dict):
        raise ProtocolTableError(f"{source}: the protocol table is not an object")
    return data


def _entries(table: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the version entries of *table*, skipping anything that is not an object."""
    versions = table.get("versions")
    if not isinstance(versions, list):
        return []
    return [entry for entry in versions if isinstance(entry, dict)]


def fingerprints_of(version: int, table: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    """Return the fingerprints *version* accepts, in the order the file lists them."""
    data = read_table() if table is None else table
    for entry in _entries(data):
        if entry.get("version") == version:
            found = entry.get("fingerprints")
            return tuple(str(value) for value in found) if isinstance(found, list) else ()
    return ()


def version_of(fingerprint: str, table: Mapping[str, Any] | None = None) -> int | None:
    """Return the version that accepts *fingerprint*, or ``None`` when no version does.

    A run made with a protocol the table does not record is evidence of nothing: nobody can say
    which prompts, window or scoring it used.
    """
    data = read_table() if table is None else table
    for entry in _entries(data):
        found = entry.get("fingerprints")
        if isinstance(found, list) and fingerprint in found:
            number = entry.get("version")
            return number if isinstance(number, int) and not isinstance(number, bool) else None
    return None


def current_version(table: Mapping[str, Any] | None = None) -> int:
    """Return the version a new run records.

    Raises:
        ProtocolTableError: if the file has no ``current``, or it is not one of the versions.
    """
    data = read_table() if table is None else table
    current = data.get("current")
    numbers = {entry.get("version") for entry in _entries(data)}
    if not isinstance(current, int) or isinstance(current, bool) or current not in numbers:
        raise ProtocolTableError(f"current {current!r} is not one of the versions")
    return current


def accepts(version: int, fingerprint: str, table: Mapping[str, Any] | None = None) -> bool:
    """Whether *version* accepts *fingerprint*."""
    return fingerprint in fingerprints_of(version, table)


def _fingerprint_problems(number: int, fingerprints: object, seen: dict[str, int]) -> list[str]:
    """Return what is wrong with one version's fingerprints, recording the ones it accepts."""
    if not isinstance(fingerprints, list) or not fingerprints:
        return [f"version {number} accepts no fingerprint"]
    found: list[str] = []
    for fingerprint in fingerprints:
        if not (isinstance(fingerprint, str) and _HEX64.match(fingerprint)):
            found.append(f"version {number} has a fingerprint that is not a SHA-256")
        elif fingerprint in seen:
            found.append(
                f"the fingerprint of version {number} is also accepted by "
                f"version {seen[fingerprint]}"
            )
        else:
            seen[fingerprint] = number
    return found


def _entry_problems(
    item: object, position: int, seen: dict[str, int]
) -> tuple[list[str], int | None]:
    """Return what is wrong with one version entry, and its number when it has one."""
    if not isinstance(item, dict):
        return [f"entry {position} is not an object"], None
    number = item.get("version")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return [f"entry {position} has no positive integer version"], None
    found = _fingerprint_problems(number, item.get("fingerprints"), seen)
    if not str(item.get("why", "")).strip():
        found.append(f"version {number} has no reason")
    if not str(item.get("date", "")).strip():
        found.append(f"version {number} has no date")
    return found, number


def problems(table: Mapping[str, Any] | None = None) -> list[str]:
    """Return what is wrong with the version file: its order, its entries, its ``current``.

    A version file is the record of what every committed run measured, so it is checked like a run
    file: ascending versions, no duplicate, a non-empty list of fingerprints and a reason per
    entry, a ``current`` that is one of them, and no fingerprint accepted by two versions.
    """
    data = read_table() if table is None else table
    found: list[str] = []
    if data.get("schema") != 1:
        found.append(f"schema is {data.get('schema')!r}, not 1")
    versions = data.get("versions")
    if not isinstance(versions, list) or not versions:
        return [*found, "versions is not a non-empty list"]
    numbers: list[int] = []
    seen: dict[str, int] = {}
    for position, item in enumerate(versions):
        here, number = _entry_problems(item, position, seen)
        found.extend(here)
        if number is not None:
            numbers.append(number)
    if numbers != sorted(numbers):
        found.append("the versions are not in ascending order")
    if len(set(numbers)) != len(numbers):
        found.append("a version appears twice")
    current = data.get("current")
    if not isinstance(current, int) or isinstance(current, bool) or current not in numbers:
        found.append(f"current {current!r} is not one of the versions")
    return found


def checked(table: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the version file after checking it, so a caller cannot use a broken one.

    Raises:
        ProtocolTableError: if :func:`problems` finds anything.
    """
    data = read_table() if table is None else table
    broken = problems(data)
    if broken:
        raise ProtocolTableError(f"{TABLE_PATH.name} is not valid: " + "; ".join(broken))
    return dict(data)


__all__ = [
    "TABLE_PATH",
    "ProtocolTableError",
    "accepts",
    "checked",
    "current_version",
    "fingerprints_of",
    "problems",
    "read_table",
    "version_of",
]
