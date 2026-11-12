"""The explanation check: a sample of a run's explanations, the marks, and the sheet.

A model's numbers say whether it corrects; they do not say whether its explanations help a
student.  Before a model takes a star place, a teacher marks a sample of them.  This module owns
the three parts of that: the **sample**, drawn from a committed run so every machine draws the
same one; the **marks**, a file without any text, checked against the run it belongs to; and the
**sheet**, the markdown a teacher fills in, whose source sentences come from the local cache
because the repository holds no corpus text.

The sample is defined by the run, not by the cache: from the run of repeat 1, per deciding
language, the notes linked to an edit with a non-blank explanation, from ``page``-band texts only,
at most two per text, drawn with ``random.Random(f"{run_id}/{language}")``.  A text with fewer
such notes contributes fewer, and the threshold then scales: at least 80 %, rounded up.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import random
import re
from typing import Any

from teachers_teammate.domain.correction_review import tokenize

from . import grouping
from .redaction import sha256_text
from .results import ResultRow
from .run_format import StoredItem, StoredRun

SCHEMA = 1
"""The layout of a marks file.  A reader refuses a file it does not know."""

KIND = "marks"
"""What the file holds, so a wrong file is refused by name and not by a missing key."""

MARKS: tuple[str, ...] = ("correct", "wrong", "vague")
"""What a teacher may write in the ``Mark`` column; only ``correct`` counts."""

CORRECT_SHARE = 0.80
"""The share of a language's sample that has to be ``correct`` for the check to pass."""

PER_TEXT = 2
"""How many notes of one text the sample takes."""

PER_LANGUAGE = 10
"""How many notes the sample aims at per language: five page texts, two notes each."""

MARKS_DIR = "marks"
"""Where the marks files live, below the repository directory."""

_RUN_LINE = re.compile(r"^Run: `(?P<run_id>[^`]+)`\s*$")


@dataclass(frozen=True)
class SampleNote:
    """One explanation of the sample: which note of which item, and what it explains."""

    item: str
    note: int
    """The index of the note in the item's stored notes."""
    language: str
    explanation_sha256: str
    """The hash of the explanation the teacher reads: the stored one, or the one it was cut from."""
    category: str
    edit: tuple[int, int, str] | None
    """The stored edit the note explains, as ``(start, end, replacement)``."""


@dataclass(frozen=True)
class Sample:
    """The explanations of one run that a teacher marks."""

    run_id: str
    seed: str
    notes: tuple[SampleNote, ...]

    def per_language(self) -> dict[str, tuple[SampleNote, ...]]:
        """Return the sample notes grouped by language, in the order the languages appear."""
        grouped: dict[str, list[SampleNote]] = {}
        for note in self.notes:
            grouped.setdefault(note.language, []).append(note)
        return {code: tuple(notes) for code, notes in grouped.items()}

    def threshold(self, language: str) -> int:
        """Return how many of a language's notes have to be ``correct``: 80 %, rounded up."""
        return math.ceil(CORRECT_SHARE * len(self.per_language().get(language, ())))

    def find(self, item: str, note: int) -> SampleNote | None:
        """Return the sample note of *item* and *note*, or ``None`` when it is not in the sample."""
        return next(
            (found for found in self.notes if found.item == item and found.note == note), None
        )


def sample(run: StoredRun, items: Sequence[StoredItem]) -> Sample:
    """Return the explanation sample of *run*, drawn from its items.

    Args:
        run: The run of repeat 1; its id seeds the draw, so the sample is the same everywhere.
        items: The typed items of that run.
    """
    seed = f"{run.run_id}/"
    drawn: list[SampleNote] = []
    for language in grouping.DECIDING_LANGUAGES:
        rng = random.Random(f"{seed}{language}")  # a reproducible sample, not a security decision
        picked: list[SampleNote] = []
        for item in items:
            if item.language != language or item.band != grouping.PAGE_BAND:
                continue
            candidates = _candidates(item)
            picked.extend(rng.sample(candidates, min(PER_TEXT, len(candidates))))
        if len(picked) > PER_LANGUAGE:
            picked = rng.sample(picked, PER_LANGUAGE)
        drawn.extend(sorted(picked, key=lambda note: (note.item, note.note)))
    return Sample(run_id=run.run_id, seed=seed, notes=tuple(drawn))


def _candidates(item: StoredItem) -> list[SampleNote]:
    """Return the notes of *item* the sample may take: linked to an edit, with an explanation."""
    edits = list(item.record.get("edits") or ())
    found: list[SampleNote] = []
    for number, note in enumerate(item.record.get("notes") or ()):
        index = note.get("edit")
        explanation = str(note.get("explanation", ""))
        if index is None or note.get("orphan") or not explanation.strip():
            continue
        if not 0 <= int(index) < len(edits):
            continue
        start, end, replacement = edits[int(index)]
        found.append(
            SampleNote(
                item=item.item,
                note=number,
                language=item.language,
                explanation_sha256=explanation_hash(note),
                category=str(note.get("category", "")),
                edit=(int(start), int(end), str(replacement)),
            )
        )
    return found


def explanation_hash(note: Mapping[str, Any]) -> str:
    """Return the hash of the explanation a teacher reads: the stored one, or the one it was cut from.

    A stored explanation that had to be cut keeps the hash of the original text
    (:func:`~.redaction.explanation_field`), so the hash names the explanation the model gave in
    both cases.
    """
    stored = note.get("explanation_sha256")
    return str(stored) if stored else sha256_text(str(note.get("explanation", "")))


# ── the marks ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Mark:
    """One teacher's judgement of one explanation."""

    item: str
    note: int
    explanation_sha256: str
    mark: str

    def to_json(self) -> dict[str, Any]:
        """Return the mark as one entry of the marks file."""
        return {
            "item": self.item,
            "note": self.note,
            "explanation_sha256": self.explanation_sha256,
            "mark": self.mark,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Mark:
        """Return the mark *data* describes.

        Raises:
            ValueError: if a field is missing or the mark is not one of :data:`MARKS`.
        """
        mark = str(data["mark"])
        if mark not in MARKS:
            raise ValueError(f"mark {mark!r} is not one of {', '.join(MARKS)}")
        return cls(
            item=str(data["item"]),
            note=int(data["note"]),
            explanation_sha256=str(data["explanation_sha256"]),
            mark=mark,
        )


@dataclass(frozen=True)
class Marks:
    """The marks of one run: who marked it, when, and what was marked.  No text."""

    run_id: str
    date: str
    marker: str
    sample_seed: str
    marks: tuple[Mark, ...]

    def to_json(self) -> dict[str, Any]:
        """Return the marks as the file's JSON object."""
        return {
            "schema": SCHEMA,
            "type": KIND,
            "run_id": self.run_id,
            "date": self.date,
            "marker": self.marker,
            "sample_seed": self.sample_seed,
            "marks": [mark.to_json() for mark in self.marks],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Marks:
        """Return the marks *data* describes.

        Raises:
            ValueError: if *data* is not a marks file of this schema and kind, or a mark is not
                one of :data:`MARKS`.
        """
        if data.get("schema") != SCHEMA or data.get("type") != KIND:
            raise ValueError(
                f"not a {KIND} file of schema {SCHEMA} "
                f"(schema {data.get('schema')!r}, type {data.get('type')!r})"
            )
        return cls(
            run_id=str(data["run_id"]),
            date=str(data["date"]),
            marker=str(data["marker"]),
            sample_seed=str(data["sample_seed"]),
            marks=tuple(Mark.from_json(entry) for entry in data["marks"]),
        )

    def correct(self, drawn: Sample) -> dict[str, tuple[int, int]]:
        """Return ``{language: (correct, total)}`` over *drawn*, counting only ``correct``."""
        counted: dict[str, tuple[int, int]] = {}
        for language, notes in drawn.per_language().items():
            found = sum(
                1
                for note in notes
                if (mark := self._of(note)) is not None and mark.mark == "correct"
            )
            counted[language] = (found, len(notes))
        return counted

    def _of(self, note: SampleNote) -> Mark | None:
        """Return the mark of *note*, or ``None`` when it was not marked."""
        return next(
            (mark for mark in self.marks if mark.item == note.item and mark.note == note.note),
            None,
        )


def marks_path(out: Path, run_id: str) -> Path:
    """Return the file the marks of *run_id* live in."""
    return out / MARKS_DIR / f"{run_id}.json"


def read_marks(path: Path) -> Marks:
    """Return the marks stored in *path*.

    Raises:
        ValueError: if the file is not a marks file of this schema.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: a marks file is a JSON object")
    return Marks.from_json(data)


def write_marks(path: Path, marks: Marks) -> None:
    """Write *marks* to *path*, creating the directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(marks.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def today() -> str:
    """Return today's date, as a marks file records it."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


# ── the sheet ─────────────────────────────────────────────────────────────────


def rows_of_run(rows: Iterable[ResultRow], items: Sequence[StoredItem]) -> dict[str, ResultRow]:
    """Return the cache rows that belong to the run of *items*, keyed by item id.

    A cache holds several runs side by side — the repeats of one model, and other models — and
    their rows share the item ids, so the last file would win.  The run's own rows are the ones of
    its model, temperature and mode whose output is the one the run stored; identical answers of
    two repeats are therefore interchangeable, and a row of another run is never taken.

    An item without such a row is left out, and :func:`sheet` refuses it.
    """
    from . import export  # noqa: PLC0415

    if not items:
        return {}
    head = items[0]
    stored = {item.item: str(item.record.get("output_sha256", "")) for item in items}
    found: dict[str, ResultRow] = {}
    for row in rows:
        if (row.model, row.temperature, row.mode) != (head.model, head.temperature, head.mode):
            continue
        if row.item not in stored or row.item in found:
            continue
        if export.output_sha256(row) != stored[row.item]:
            continue
        found[row.item] = row
    return found


def sheet(
    drawn: Sample,
    rows: Mapping[str, ResultRow],
    items: Mapping[str, StoredItem],
    entries: Mapping[str, Mapping[str, Any]],
) -> str:
    """Return the markdown a teacher fills in, with the source sentences from the local cache.

    Args:
        drawn: The sample of the committed run.
        rows: The local result rows of that run, keyed by item id (:func:`rows_of_run`); they hold
            the source text.
        items: The committed run's items, keyed by item id; they hold the stored hashes.
        entries: The manifest's entries, keyed by item id; they hold the sentence spans, which the
            repository keeps and the cache's rows do not.

    Raises:
        ValueError: if a sampled item is not in the cache or the run, or its output or an
            explanation differs from the committed run, because then the sheet would show another
            text than the run.
    """
    lines = [
        f"# Spot-check of explanations: run `{drawn.run_id}`",
        "",
        "Mark each explanation `correct`, `wrong` or `vague`.  `correct` means the explanation is "
        "true and fits the change; `wrong` means it is false, or explains a change that is itself "
        "wrong; `vague` means it is true but too general to help a student.  Only `correct` "
        "counts.  The text is from public learner corpora; do not publish this sheet.",
        "",
        f"Run: `{drawn.run_id}`",
        f"Sample seed: `{drawn.seed}`",
        "",
    ]
    for language, notes in drawn.per_language().items():
        lines += [
            f"## {language}",
            "",
            "| # | Item | Note | Source sentence | Change | Category | Explanation | Mark |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for number, note in enumerate(notes, start=1):
            row = rows.get(note.item)
            if row is None:
                raise ValueError(f"{note.item}: the cache has no row for it, so no source sentence")
            item = items.get(note.item)
            if item is None:
                raise ValueError(f"{note.item}: the run has no item for it")
            entry = entries.get(note.item)
            if entry is None:
                raise ValueError(f"{note.item}: the manifest has no entry for it")
            _check_row(note, row, item)
            lines.append(
                f"| {number} | `{note.item}` | {note.note} | {_cell(_sentence(row, note, entry))} "
                f"| {_cell(_change(row, note))} | {_cell(note.category)} "
                f"| {_cell(_explanation(row, note))} |  |"
            )
        lines.append("")
    return "\n".join(lines)


def _check_row(note: SampleNote, row: ResultRow, item: StoredItem) -> None:
    """Refuse a cache row whose output or explanation is not the one the run stored.

    Raises:
        ValueError: if the row's output hash or the note's explanation hash differs from the run's.
    """
    from . import export  # noqa: PLC0415

    if export.output_sha256(row) != str(item.record.get("output_sha256", "")):
        raise ValueError(
            f"{note.item}: the cache's output is not the one the run stored; "
            "the sheet would show another text"
        )
    if sha256_text(_explanation(row, note)) != note.explanation_sha256:
        raise ValueError(
            f"{note.item}: the explanation of note {note.note} changed since the run; "
            "the sheet would show another explanation"
        )


def _change_of(row: ResultRow, note: SampleNote) -> dict | None:
    """Return the cache's change that the sample note explains, or ``None``."""
    if note.edit is None:
        return None
    start, end = note.edit[0], note.edit[1]
    return next(
        (
            change
            for change in row.changes
            if change.get("edit") is not None
            and int(change["edit"][0]) == start
            and int(change["edit"][1]) == end
        ),
        None,
    )


def _explanation(row: ResultRow, note: SampleNote) -> str:
    """Return the explanation the cache holds for *note*."""
    change = _change_of(row, note)
    return "" if change is None else str(change.get("explanation", ""))


def _sentence(row: ResultRow, note: SampleNote, entry: Mapping[str, Any]) -> str:
    """Return the source sentence the note's edit sits in, from the cache's row and the manifest."""
    if note.edit is None:
        return ""
    tokens = tokenize(row.source)
    start = note.edit[0]
    for low, high in entry.get("sentence_spans") or ():
        if int(low) <= start < int(high):
            return "".join(tokens[int(low) : int(high)]).strip()
    return ""


def _change(row: ResultRow, note: SampleNote) -> str:
    """Return the change as ``original → corrected``, from the cache's source and the run's edit."""
    if note.edit is None:
        return ""
    tokens = tokenize(row.source)
    original = "".join(tokens[note.edit[0] : note.edit[1]]).strip() or "∅"
    corrected = note.edit[2].strip() or "∅"
    return f"{original} → {corrected}"


def _cell(text: str) -> str:
    """Return *text* as one markdown table cell: pipes escaped, newlines flattened."""
    return " ".join(text.split()).replace("|", "\\|")


# ── reading a filled-in sheet ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SheetRow:
    """One row of a filled-in sheet: which note, and what the teacher wrote."""

    item: str
    note: int
    mark: str


def read_sheet(text: str) -> tuple[str, list[SheetRow]]:
    """Return the run id and the marked rows of a filled-in sheet.

    Raises:
        ValueError: if the sheet names no run, or a row's mark is not one of :data:`MARKS`.
    """
    run_id = ""
    rows: list[SheetRow] = []
    for line in text.splitlines():
        if found := _RUN_LINE.match(line):
            run_id = found.group("run_id")
            continue
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not cells[1].startswith("`"):
            continue
        mark = cells[-1]
        if not mark:
            continue
        if mark not in MARKS:
            raise ValueError(f"{cells[1]}: {mark!r} is not one of {', '.join(MARKS)}")
        rows.append(SheetRow(item=cells[1].strip("`"), note=int(cells[2]), mark=mark))
    if not run_id:
        raise ValueError("the sheet names no run: the line `Run: `<run-id>`` is missing")
    return run_id, rows


def marks_from_sheet(text: str, drawn: Sample, *, marker: str, date: str | None = None) -> Marks:
    """Return the marks a filled-in sheet holds, checked against *drawn*.

    Raises:
        ValueError: if a row names a note that is not in the sample, or the sheet is of another
            run than *drawn*.
    """
    run_id, rows = read_sheet(text)
    if run_id != drawn.run_id:
        raise ValueError(f"the sheet is of run {run_id!r}, the sample of {drawn.run_id!r}")
    marks: list[Mark] = []
    for row in rows:
        note = drawn.find(row.item, row.note)
        if note is None:
            raise ValueError(
                f"{row.item}: note {row.note} is not in the sample of {drawn.run_id}; "
                "the sheet and the run disagree"
            )
        marks.append(
            Mark(
                item=note.item,
                note=note.note,
                explanation_sha256=note.explanation_sha256,
                mark=row.mark,
            )
        )
    return Marks(
        run_id=drawn.run_id,
        date=date or today(),
        marker=marker,
        sample_seed=drawn.seed,
        marks=tuple(marks),
    )


def unmarked(drawn: Sample, marks: Marks) -> list[SampleNote]:
    """Return the sample notes *marks* does not mark, so a half-filled sheet is visible."""
    marked = {(mark.item, mark.note) for mark in marks.marks}
    return [note for note in drawn.notes if (note.item, note.note) not in marked]


def languages_of(notes: Iterable[SampleNote]) -> tuple[str, ...]:
    """Return the languages of *notes*, in the order they first appear."""
    return tuple(dict.fromkeys(note.language for note in notes))


__all__ = [
    "CORRECT_SHARE",
    "KIND",
    "MARKS",
    "MARKS_DIR",
    "PER_LANGUAGE",
    "PER_TEXT",
    "SCHEMA",
    "Mark",
    "Marks",
    "Sample",
    "SampleNote",
    "SheetRow",
    "explanation_hash",
    "languages_of",
    "marks_from_sheet",
    "marks_path",
    "read_marks",
    "read_sheet",
    "rows_of_run",
    "sample",
    "sheet",
    "today",
    "unmarked",
    "write_marks",
]
