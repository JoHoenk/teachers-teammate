"""The one-off importer of the blind sheet's key: its token positions become characters.

The blind sheet of 2026-11-17 was drawn before the store stored character offsets, so its key names
each change as a **token** span of the source plus the tokens that replace it::

    {"id": "en/en-06", "edit": [64, 65, [" ", "Traveling"]], "where": "kept", ...}

The teacher's verdicts are in the filled-in sheet, one row per key entry, marked ``A`` (a correct
fix), ``B`` (acceptable but not needed), ``C`` (wrong or unnecessary) or ``?`` (cannot tell).  This
module maps each entry once, with the tokenizer of the day, and writes the result as the store's own
edit marks, so the 60 changes waiting for a verdict need no re-draw.

The mapping is checked, not assumed: the text under the mapped span has to be the text the sheet
shows in brackets, which is the source as the old sheet rendered it.  A tokenizer change moves the
span and the check fails, and an entry that fails is **listed**, never dropped.

``B`` and ``?`` become ``vague``: neither accepts the change nor refuses it, which is what the two
answers have in common.  Only ``A`` becomes ``valid``, and only a ``valid`` edit mark can become an
accepted alternative.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from ..core.records import Mark, TestRecord, question_sha256, text_id
from ..metrics.offsets import text_under, token_span_to_chars
from ..store import layout, reader, writer

VERDICTS: dict[str, str] = {"A": "valid", "B": "vague", "C": "wrong", "?": "vague"}
"""What the old sheet's four marks mean in the store's vocabulary."""

EMPTY = "∅"
"""What the old sheet printed where a change inserts text and the source has none."""

_ROW = re.compile(r"^\|")
_BRACKET = re.compile(r"\[(?P<text>[^\]]*)\]")


@dataclass(frozen=True)
class BlindKeyImport:
    """What one import wrote, and which entries it could not map."""

    path: Path
    """The marks file the entries were merged into."""

    added: int
    changed: int
    kept: int
    failed: tuple[str, ...] = ()
    """One line per entry that could not be mapped, so a person can look at it."""

    def lines(self) -> list[str]:
        """Return what the command prints: what was written, and what needs a person."""
        found = [
            f"wrote {self.path} ({self.added} new marks, {self.changed} replaced, {self.kept} kept)"
        ]
        if self.failed:
            found.append(f"{len(self.failed)} entries could not be mapped:")
            found.extend(f"  {line}" for line in self.failed)
        return found


def import_blind_key(
    root: Path, testset: str, key_path: Path, sheet_path: Path, marker: str
) -> BlindKeyImport:
    """Write the blind sheet's verdicts as edit marks of *marker* on *testset*.

    Args:
        root: The store's directory.
        testset: The test set the sheet was drawn from; its records hold the sources.
        key_path: The sheet's key file, with the token positions of the changes.
        sheet_path: The filled-in sheet, one row per key entry.
        marker: The marker the verdicts belong to.

    Returns:
        The marks file written, what the merge did to it, and the entries that could not be mapped.

    Raises:
        FormatError: if a record file or a marks file is not of a format this reader knows.
        ValueError: if the key file is not a JSON object of entries.
    """
    records = reader.read_testset(root, testset)
    by_text = {text_id(record): record for record in records}
    key = _key_of(key_path)
    marks: list[Mark] = []
    failed: list[str] = []
    for number, (shown, verdict) in _rows_of(sheet_path).items():
        entry = key.get(number)
        if entry is None:
            failed.append(f"{number}: the key has no entry for this row")
            continue
        try:
            marks.append(_mark_of(entry, shown, verdict, by_text))
        except ValueError as error:
            failed.append(f"{number}: {error}")
    path = layout.marks_path(root, marker, question_sha256(records))
    change = writer.merge_marks(path, marks)
    return BlindKeyImport(
        path=path,
        added=change.added,
        changed=change.changed,
        kept=change.kept,
        failed=tuple(failed),
    )


def _key_of(path: Path) -> dict[str, Mapping[str, Any]]:
    """Return the key file's entries, keyed by the row number the sheet prints.

    Raises:
        ValueError: if the file is not a JSON object of objects.
    """
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not all(
        isinstance(entry, dict) for entry in loaded.values()
    ):
        raise ValueError(f"{path} is not a key of one entry per row")
    return loaded


def _rows_of(path: Path) -> dict[str, tuple[str, str]]:
    """Return ``{row number: (the source text the sheet shows in brackets, the verdict)}``.

    A row with an empty verdict is left out: the teacher did not judge it, and a mark is a verdict.
    A verdict that is not one of :data:`VERDICTS` is kept, so the caller can list it.
    """
    found: dict[str, tuple[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not _ROW.match(line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6 or not cells[0].isdigit():
            continue
        verdict = cells[-2]
        if not verdict:
            continue
        shown = _BRACKET.search(cells[2])
        found[cells[0]] = (shown.group("text") if shown else "", verdict)
    return found


def _mark_of(
    entry: Mapping[str, Any],
    shown: str,
    verdict: str,
    by_text: Mapping[str, TestRecord],
) -> Mark:
    """Return the mark one key entry stands for.

    Raises:
        ValueError: if the entry cannot be mapped, naming why.
    """
    text = str(entry.get("id", ""))
    if verdict not in VERDICTS:
        raise ValueError(f"{text}: {verdict!r} is not one of {', '.join(VERDICTS)}")
    record = by_text.get(text)
    if record is None:
        raise ValueError(f"{text}: the test set has no such text")
    edit = entry.get("edit")
    if not isinstance(edit, list) or len(edit) != 3:
        raise ValueError(f"{text}: the entry's edit is not a span and its tokens")
    start, end, tokens = edit
    if not isinstance(start, int) or not isinstance(end, int) or not isinstance(tokens, list):
        raise ValueError(f"{text}: the entry's edit is not a span and its tokens")
    try:
        span = token_span_to_chars(record.source, (start, end))
    except IndexError:
        raise ValueError(f"{text}: tokens {start} to {end} are not in the text any more") from None
    under = text_under(record.source, span)
    if under.strip() != ("" if shown == EMPTY else shown).strip():
        raise ValueError(
            f"{text}: tokens {start} to {end} are {under!r}, the sheet shows {shown!r}; "
            "the tokenizer moved"
        )
    return Mark(
        kind="edit",
        text=text,
        span=span,
        replacement="".join(str(token) for token in tokens),
        value=VERDICTS[verdict],
    )


__all__ = ["EMPTY", "VERDICTS", "BlindKeyImport", "import_blind_key"]
