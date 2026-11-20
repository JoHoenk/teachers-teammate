"""The shape every stored file of the evaluation tooling shares: a version, JSONL, sorted keys.

Three rules, and they are the whole module:

* **Every file names its format version**.  The number is the major version only: a reader
  refuses a version it does not know, and an added optional field does not move it.
* **A file is JSON Lines with sorted keys**, so ``git diff`` shows what changed and two writers
  produce the same bytes.
* **A reader keeps the fields it does not know** when it writes a file again.  That is what
  :func:`merge_unknown` is for: a newer writer's field survives a round trip through an older
  reader, so a tool that only knows part of a format cannot silently drop the rest.

Nothing here knows what a run or a test set is; the records live in :mod:`.records`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Literal, overload

RUN_FORMAT = 2
"""Format version of a run file (``.run.jsonl``): header line, then session and answer lines."""

TESTSET_FORMAT = 2
"""Format version of a test set's record file (``<language>.jsonl``)."""

MARKS_FORMAT = 3
"""Format version of a marks file (``store/marks/<marker>__<question>.jsonl``).

Version 3 gives a mark a :attr:`~.records.Mark.kind`: an edit mark and an explanation mark answer
two different questions and no longer share one value list.
"""

MANIFEST_FORMAT = 2
"""Format version of a test set's manifest (``testsets/<testset>/manifest.json``)."""


class FormatError(ValueError):
    """A stored file is not of a format this reader knows, or is not the file it claims to be."""


def check_format(record: Mapping[str, Any], expected: int, source: str | Path) -> None:
    """Refuse *record* unless its ``format`` is *expected*.

    Args:
        record: The first line of a stored file, or any line that carries a version.
        expected: The version this reader writes and understands.
        source: Where the record came from, for the message.

    Raises:
        FormatError: if the record carries no ``format``, or one this reader does not know.
    """
    found = record.get("format")
    if found == expected:
        return
    if found is None:
        raise FormatError(f"{source}: no format version; this reader knows format {expected}")
    raise FormatError(f"{source}: format {found!r}, this reader knows format {expected}")


@dataclass(frozen=True)
class TornTail:
    """The last line of a file that was cut off mid-write, and what it held.

    A killed process leaves the line it was appending without its newline and without its closing
    brace.  That is not a broken file: everything before it is complete, and the text it was about
    is simply sent again.  A torn line anywhere else is still a :class:`FormatError`.
    """

    path: Path
    line: int
    """The line number of the torn line, counting from 1."""
    bytes: int
    """How many bytes of the line were on disk."""

    def __str__(self) -> str:
        """Return the message a command prints when it drops the line."""
        return f"{self.path}:{self.line}: a torn last line of {self.bytes} bytes"


def write_text_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* so a reader sees the old file or the new one, never half of either.

    The marks, the accepted alternatives, the manifests and the index hold the only human data in
    the store, so they are written to a temporary file beside the target and renamed over it.  A
    write that fails leaves the old file as it was.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@overload
def read_jsonl(path: Path, *, torn_tail: Literal["refuse"] = "refuse") -> list[dict[str, Any]]: ...


@overload
def read_jsonl(
    path: Path, *, torn_tail: Literal["report"]
) -> tuple[list[dict[str, Any]], TornTail | None]: ...


def read_jsonl(
    path: Path, *, torn_tail: Literal["refuse", "report"] = "refuse"
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], TornTail | None]:
    """Return every JSON object of *path*, one per line, skipping blank lines.

    Args:
        path: The file to read.
        torn_tail: What to do with a last line that has no trailing newline and is not JSON, which
            is what a killed append leaves behind.  ``"refuse"`` raises, ``"report"`` leaves the
            line out and returns it beside the records.

    Returns:
        The records, and with ``torn_tail="report"`` also the torn line or ``None``.

    Raises:
        FormatError: if a line is not a JSON object, naming the line number.
    """
    records: list[dict[str, Any]] = []
    torn: TornTail | None = None
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                if torn_tail == "report" and not line.endswith("\n"):
                    torn = TornTail(path=path, line=number, bytes=len(line.encode("utf-8")))
                    break
                raise FormatError(f"{path}:{number}: not JSON ({error})") from error
            if not isinstance(record, dict):
                raise FormatError(f"{path}:{number}: not a JSON object")
            records.append(record)
    if torn_tail == "report":
        return records, torn
    return records


def read_first_json(path: Path) -> dict[str, Any]:
    """Return the first JSON object of *path*, reading one line and no more.

    A run file's first line is its header, so a lookup over many run files reads one line each
    instead of every answer of every run.

    Raises:
        FormatError: if the file is empty or its first line is not a JSON object.
    """
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise FormatError(f"{path}:{number}: not JSON ({error})") from error
            if not isinstance(record, dict):
                raise FormatError(f"{path}:{number}: not a JSON object")
            return record
    raise FormatError(f"{path}: an empty file")


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Write *records* to *path*, one JSON object per line, sorted keys, replacing the file.

    Sorted keys and one record per line keep ``git diff`` readable: a new run is one file, and a
    corrected one shows the lines that changed.  The file is written atomically, so a reader never
    sees half of it.
    """
    lines = [json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records]
    write_text_atomic(path, "\n".join(lines) + "\n")


def append_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Append *records* to *path*, one JSON object per line, so an interrupted run resumes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def extra_fields(record: Mapping[str, Any], known: Iterable[str]) -> dict[str, Any]:
    """Return the fields of *record* that *known* does not name, so a rewrite keeps them.

    The one copy of the rule that a reader keeps what it does not understand: every record of
    :mod:`.records`, the performance blocks and the redacted run use it, so a newer writer's field
    survives any round trip.
    """
    names = set(known)
    return {key: value for key, value in record.items() if key not in names}


def merge_unknown(original: Mapping[str, Any], known: Mapping[str, Any]) -> dict[str, Any]:
    """Return *known* with every field of *original* it does not name, so a rewrite keeps them.

    A reader that knows part of a format writes the fields it understands and passes the line it
    read as *original*: a field a newer writer added survives, and a field this reader dropped on
    purpose (a derived number, say) is gone because *known* does not name it either.

    Args:
        original: The line as it was read.
        known: The fields this reader writes.

    Returns:
        A new dict: *original*'s fields first, then *known*'s, which win on a shared name.
    """
    merged = dict(original)
    merged.update(known)
    return merged


__all__ = [
    "MANIFEST_FORMAT",
    "MARKS_FORMAT",
    "RUN_FORMAT",
    "TESTSET_FORMAT",
    "FormatError",
    "TornTail",
    "append_jsonl",
    "check_format",
    "extra_fields",
    "merge_unknown",
    "read_first_json",
    "read_jsonl",
    "write_jsonl",
    "write_text_atomic",
]
