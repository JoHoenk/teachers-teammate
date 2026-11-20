"""The marks workflow: the samples a teacher marks, the sheets, and the marks read back.

A model's numbers say whether it corrects; they do not say whether its explanations help a student,
and they cannot say whether a change the reference does not make is still a correct fix.  Before a
model takes a star place, a teacher marks a sample of both.  This module owns the three parts of
that: the **sample**, drawn from a stored run so every machine draws the same one; the **sheet**, the
markdown a teacher fills in; and the **marks**, read back from the filled-in sheet as the store's own
:class:`~..core.records.Mark` records, whose identity is ``(text, span, replacement)`` and which hold
no text.

Two sheets, two questions:

* the **explanation sheet** asks whether an explanation is true and fits the change.  Its sample is
  the notes of ``page``-band texts that explain an edit, at most two per text, drawn with
  ``random.Random(f"{run_key}/{language}")``; a text with fewer such notes contributes fewer, and the
  threshold then scales: at least 80 %, rounded up.
* the **extra-edits sheet** asks whether a change the reference does not make is a correct fix.  Its
  sample is the answer's edits minus the reference's, drawn the same way, and it is **blind**: it
  names the run by its key and never by the model, so a verdict is about the change and not about
  who made it.

The seed is the run **key**, not the file name: a renamed file draws the same sample, and the name is
only there for people to read.

The sheets need no local cache, unlike the old one: the store holds the test set, so the source
sentence and the change come from the record and the review, in **characters**, and no tokenizer is
involved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import math
import random
import re

from teachers_teammate.domain.proofreading.review import EditNote

from ..core.axes import run_key_sha256
from ..core.hashing import sha256_text
from ..core.records import MARK_VALUES, Mark, TestRecord, text_id
from ..metrics.quality import char_edits
from . import groups
from .load import LoadedItem, LoadedRun

MARKS: tuple[str, ...] = MARK_VALUES["explanation"]
"""What a teacher may write in an explanation sheet's ``Mark`` column; only ``correct`` counts."""

EDIT_MARKS: tuple[str, ...] = MARK_VALUES["edit"]
"""What a teacher may write in an extra-edits sheet's ``Value`` column; only ``valid`` counts."""

CORRECT_SHARE = 0.80
"""The share of a language's sample that has to be ``correct`` for the check to pass."""

PER_TEXT = 2
"""How many notes, or edits, of one text the sample takes."""

PER_LANGUAGE = 10
"""How many notes, or edits, the sample aims at per language: five page texts, two each."""

EXPLANATIONS = "explanations"
"""The explanation sheet's kind, and the name the gate's explanation checks are printed under."""

EXTRA_EDITS = "extra-edits"
"""The extra-edits sheet's kind."""

_RUN_LINE = re.compile(r"^Run: `(?P<run_id>[^`]+)`\s*$")
_RUN_KEY_LINE = re.compile(r"^Run key: `(?P<run_key>[^`]+)`\s*$")
_SHEET_LINE = re.compile(r"^Sheet: `?(?P<kind>[a-z-]+)`?\s*$")
_SPAN_CELL = re.compile(r"^(?P<start>\d+)-(?P<end>\d+)$")


# ── the explanation sample ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SampleNote:
    """One explanation of the sample: which note of which text, and what it explains."""

    text: str
    """The text's id, ``<language>/<record id>``, which is what a mark names."""

    note: int
    """The index of the note in the text's review."""

    language: str
    explanation_sha256: str
    """The hash of the explanation the teacher reads."""

    category: str
    edit: tuple[int, int, str]
    """The edit the note explains, as ``(start, end, replacement)`` in characters of the source."""

    @property
    def span(self) -> tuple[int, int]:
        """Return the character span of the edit, which is the mark's identity."""
        return (self.edit[0], self.edit[1])

    @property
    def replacement(self) -> str:
        """Return the text the edit puts there, which is the rest of the mark's identity."""
        return self.edit[2]


@dataclass(frozen=True)
class Sample:
    """The explanations of one run that a teacher marks."""

    run_id: str
    """The run's file name, for people: the sample is found by its key, not by this."""

    run_key: str
    """The hash of the run key: what the sample is drawn from, and what a sheet names."""

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

    def find(self, text: str, note: int) -> SampleNote | None:
        """Return the sample note of *text* and *note*, or ``None`` when it is not in the sample."""
        return next(
            (found for found in self.notes if found.text == text and found.note == note), None
        )


def sample(run: LoadedRun) -> Sample:
    """Return the explanation sample of *run*, drawn from its scored items.

    Args:
        run: The run of repeat 1; its key seeds the draw, so the sample is the same everywhere and
            a renamed file draws the same one.
    """
    key = run_key_sha256(run.header)
    seed = f"{key}/"
    drawn: list[SampleNote] = []
    for language in groups.DECIDING_LANGUAGES:
        rng = random.Random(f"{seed}{language}")  # a reproducible sample, not a security decision
        picked: list[SampleNote] = []
        for item in run.items:
            if item.language != language or item.band != groups.PAGE_BAND:
                continue
            candidates = _candidates(item)
            picked.extend(rng.sample(candidates, min(PER_TEXT, len(candidates))))
        if len(picked) > PER_LANGUAGE:
            picked = rng.sample(picked, PER_LANGUAGE)
        drawn.extend(sorted(picked, key=lambda note: (note.text, note.note)))
    return Sample(run_id=run.name, run_key=key, seed=seed, notes=tuple(drawn))


def _candidates(item: LoadedItem) -> list[SampleNote]:
    """Return the notes of *item* the sample may take: they explain an edit, and say something."""
    review = item.answer.review
    if review is None:
        return []
    found: list[SampleNote] = []
    for number, note in enumerate(review.edits):
        if not note.parts or not note.explanation.strip():
            continue
        found.append(
            SampleNote(
                text=text_id(item.record),
                note=number,
                language=item.language,
                explanation_sha256=explanation_hash(note),
                category=note.category,
                edit=(note.raw_start, note.raw_end, note.corrected),
            )
        )
    return found


def explanation_hash(note: EditNote) -> str:
    """Return the hash of the explanation a teacher reads."""
    return sha256_text(note.explanation)


# ── the extra-edits sample ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SampleEdit:
    """One change of the sample: what the model did that the reference does not."""

    text: str
    """The text's id, ``<language>/<record id>``, which is what a mark names."""

    language: str
    edit: tuple[int, int, str]
    """The change, as ``(start, end, replacement)`` in characters of the source."""

    @property
    def span(self) -> tuple[int, int]:
        """Return the character span of the change, which is the mark's identity."""
        return (self.edit[0], self.edit[1])

    @property
    def replacement(self) -> str:
        """Return the text the change puts there, which is the rest of the mark's identity."""
        return self.edit[2]


@dataclass(frozen=True)
class EditSample:
    """The changes of one run that a teacher judges, blind to the model that made them."""

    run_id: str
    """The run's file name, for the tool: the sheet prints the key and never this."""

    run_key: str
    """The hash of the run key: what the sample is drawn from, and what a sheet names."""

    seed: str
    edits: tuple[SampleEdit, ...]

    def per_language(self) -> dict[str, tuple[SampleEdit, ...]]:
        """Return the sample edits grouped by language, in the order the languages appear."""
        grouped: dict[str, list[SampleEdit]] = {}
        for edit in self.edits:
            grouped.setdefault(edit.language, []).append(edit)
        return {code: tuple(edits) for code, edits in grouped.items()}

    def find(self, text: str, span: tuple[int, int]) -> SampleEdit | None:
        """Return the sample edit of *text* and *span*, or ``None`` when it is not in the sample."""
        return next(
            (found for found in self.edits if found.text == text and found.span == span), None
        )


def extra_edits_sample(
    run: LoadedRun, *, per_text: int = PER_TEXT, seed: str | None = None
) -> EditSample:
    """Return the changes of *run* the reference does not make, drawn per language.

    Args:
        run: The run of repeat 1; its key seeds the draw, so the sample is the same everywhere.
        per_text: How many changes of one text the sample takes.
        seed: What to draw with, or ``None`` for the run key, which is what a sheet names.
    """
    key = run_key_sha256(run.header)
    chosen = seed or key
    drawn: list[SampleEdit] = []
    for language in _languages_of(run):
        rng = random.Random(
            f"{chosen}/{language}"
        )  # a reproducible sample, not a security decision
        picked: list[SampleEdit] = []
        for item in run.items:
            if item.language != language:
                continue
            candidates = _extra_candidates(item)
            picked.extend(rng.sample(candidates, min(per_text, len(candidates))))
        if len(picked) > PER_LANGUAGE:
            picked = rng.sample(picked, PER_LANGUAGE)
        drawn.extend(sorted(picked, key=lambda edit: (edit.text, edit.span)))
    return EditSample(run_id=run.name, run_key=key, seed=chosen, edits=tuple(drawn))


def _languages_of(run: LoadedRun) -> tuple[str, ...]:
    """Return the languages of *run*, in the order its texts first appear."""
    return tuple(dict.fromkeys(item.language for item in run.items))


def _extra_candidates(item: LoadedItem) -> list[SampleEdit]:
    """Return the changes of *item* the reference does not make: the ones a teacher has to judge."""
    reference = set(char_edits(item.record.source, item.record.reference))
    return [
        SampleEdit(
            text=text_id(item.record),
            language=item.language,
            edit=(span[0], span[1], replacement),
        )
        for span, replacement in char_edits(item.record.source, item.output)
        if (span, replacement) not in reference
    ]


# ── the sheets ────────────────────────────────────────────────────────────────


def sheet(drawn: Sample, items: Mapping[str, LoadedItem]) -> str:
    """Return the explanation sheet a teacher fills in, with the source sentences from the test set.

    Args:
        drawn: The sample of the stored run.
        items: The run's scored items, keyed by text id; they hold the record and the review.

    Raises:
        ValueError: if a sampled text is not in the run, or its explanation changed since the
            sample was drawn, because then the sheet would show another text than the run.
    """
    lines = [
        f"# Spot-check of explanations: run `{drawn.run_id}`",
        "",
        "Mark each explanation `correct`, `wrong` or `vague`.  `correct` means the explanation is "
        "true and fits the change; `wrong` means it is false, or explains a change that is itself "
        "wrong; `vague` means it is true but too general to help a student.  Only `correct` "
        "counts.  The text is from public learner corpora; do not publish this sheet.",
        "",
        f"Sheet: {EXPLANATIONS}",
        f"Run: `{drawn.run_id}`",
        f"Run key: `{drawn.run_key}`",
        f"Sample seed: `{drawn.seed}`",
        "",
    ]
    for language, notes in drawn.per_language().items():
        lines += [
            f"## {language}",
            "",
            "| # | Text | Note | Source sentence | Change | Category | Explanation | Mark |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for number, note in enumerate(notes, start=1):
            item = items.get(note.text)
            if item is None:
                raise ValueError(f"{note.text}: the run has no item for it")
            _check_item(note, item)
            lines.append(
                f"| {number} | `{note.text}` | {note.note} | {_cell(_sentence(item.record, note.span))} "
                f"| {_cell(_change(item.record, note.span, note.replacement))} "
                f"| {_cell(note.category)} "
                f"| {_cell(_explanation(item, note))} |  |"
            )
        lines.append("")
    return "\n".join(lines)


def extra_edits_sheet(run: LoadedRun, *, per_text: int = PER_TEXT, seed: str | None = None) -> str:
    """Return the blind sheet of the changes *run* makes that the reference does not.

    The sheet names the run by its key and not by its file, so the teacher judges the change and
    not the model.  Each row shows the source sentence with the change in brackets, which is also
    what :func:`read_extra_sheet` checks a filled-in row against.

    Args:
        run: The run of repeat 1.
        per_text: How many changes of one text the sample takes.
        seed: What to draw with, or ``None`` for the run key.
    """
    drawn = extra_edits_sample(run, per_text=per_text, seed=seed)
    items = {text_id(item.record): item for item in run.items}
    lines = [
        f"# Spot-check of edits: run key `{drawn.run_key}`",
        "",
        "Mark each change `valid`, `wrong` or `vague`.  `valid` means it is a correct fix a teacher "
        "would accept, whether or not the reference makes it; `wrong` means it is not; `vague` "
        "means you cannot tell.  Only `valid` counts, and an accepted change becomes part of the "
        "test set's key.  The text is from public learner corpora; do not publish this sheet.",
        "",
        f"Sheet: {EXTRA_EDITS}",
        f"Run key: `{drawn.run_key}`",
        f"Sample seed: `{drawn.seed}`",
        "",
    ]
    for language, edits in drawn.per_language().items():
        lines += [
            f"## {language}",
            "",
            "| # | Text | Span | Source sentence | Change | Value |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for number, edit in enumerate(edits, start=1):
            item = items.get(edit.text)
            if item is None:
                raise ValueError(f"{edit.text}: the run has no item for it")
            lines.append(
                f"| {number} | `{edit.text}` | {edit.span[0]}-{edit.span[1]} "
                f"| {_cell(_marked_sentence(item.record, edit.span))} "
                f"| {_cell(_change(item.record, edit.span, edit.replacement))} |  |"
            )
        lines.append("")
    return "\n".join(lines)


def _check_item(note: SampleNote, item: LoadedItem) -> None:
    """Refuse an item whose explanation is not the one the sample was drawn from.

    Raises:
        ValueError: if the note's explanation hash differs from the sample's.
    """
    if explanation_hash(_note_of(item, note)) != note.explanation_sha256:
        raise ValueError(
            f"{note.text}: the explanation of note {note.note} changed since the sample; "
            "the sheet would show another explanation"
        )


def _note_of(item: LoadedItem, note: SampleNote) -> EditNote:
    """Return the review's note that *note* names.

    Raises:
        ValueError: if the item has no such note.
    """
    review = item.answer.review
    if review is None or note.note >= len(review.edits):
        raise ValueError(f"{note.text}: the run has no note {note.note}")
    return review.edits[note.note]


def _explanation(item: LoadedItem, note: SampleNote) -> str:
    """Return the explanation the review holds for *note*."""
    return _note_of(item, note).explanation


def _sentence_span(record: TestRecord, position: int) -> tuple[int, int]:
    """Return the character span of the sentence *position* sits in, or the whole text."""
    for low, high in record.sentence_spans:
        if low <= position < high:
            return low, high
    return 0, len(record.source)


def _sentence(record: TestRecord, span: tuple[int, int]) -> str:
    """Return the source sentence the change sits in, from the record's character spans."""
    low, high = _sentence_span(record, span[0])
    return record.source[low:high].strip()


def _marked_sentence(record: TestRecord, span: tuple[int, int]) -> str:
    """Return the source sentence with the change in brackets, as the blind sheet shows it."""
    low, high = _sentence_span(record, span[0])
    marked = record.source[span[0] : span[1]] or "∅"
    return f"{record.source[low : span[0]]}[{marked}]{record.source[span[1] : high]}"


def _change(record: TestRecord, span: tuple[int, int], replacement: str) -> str:
    """Return the change as ``original → corrected``, from the record's source."""
    original = record.source[span[0] : span[1]].strip() or "∅"
    corrected = replacement.strip() or "∅"
    return f"{original} → {corrected}"


def _cell(text: str) -> str:
    """Return *text* as one markdown table cell: pipes escaped, newlines flattened."""
    return " ".join(text.split()).replace("|", "\\|")


# ── reading a filled-in sheet ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SheetRow:
    """One row of a filled-in explanation sheet: which note, and what the teacher wrote."""

    text: str
    note: int
    mark: str


@dataclass(frozen=True)
class Sheet:
    """A filled-in explanation sheet: which run it is of, and what the teacher wrote."""

    run_key: str
    """The hash of the run key the sheet names, which is what the run is found by."""

    run_id: str
    """The run's file name, as the sheet prints it for people to read."""

    rows: tuple[SheetRow, ...]


@dataclass(frozen=True)
class EditRow:
    """One row of a filled-in extra-edits sheet: which change, and what the teacher wrote."""

    text: str
    span: tuple[int, int]
    mark: str


@dataclass(frozen=True)
class EditSheet:
    """A filled-in extra-edits sheet: which run it is of, and what the teacher wrote."""

    run_key: str
    rows: tuple[EditRow, ...]


@dataclass(frozen=True)
class SheetHeader:
    """What every sheet says about itself: what it is of, and which run it is of."""

    kind: str
    """``explanations`` or ``extra-edits``."""

    run_id: str
    """The run's file name, which only an explanation sheet prints."""

    run_key: str
    """The hash of the run key, which is what the run is found by."""


def header_of(text: str) -> SheetHeader:
    """Return what a sheet says about itself, before any row is read.

    Raises:
        ValueError: if the sheet does not say what it is, or names no run key.
    """
    return _header_of(text)[0]


def read_sheet(text: str) -> Sheet:
    """Return the run and the marked rows of a filled-in explanation sheet.

    Raises:
        ValueError: if the sheet is of another kind, names no run or no run key, or a row's mark
            is not one of :data:`MARKS`.
    """
    header, rows = _header_of(text)
    if header.kind != EXPLANATIONS:
        raise ValueError(f"the sheet is of {header.kind!r}, not of {EXPLANATIONS!r}")
    if not header.run_id:
        raise ValueError("the sheet names no run: the line `Run: `<run-id>`` is missing")
    found: list[SheetRow] = []
    for cells in rows:
        if not cells[1].startswith("`"):
            continue
        mark = cells[-1]
        if not mark:
            continue
        if mark not in MARKS:
            raise ValueError(f"{cells[1]}: {mark!r} is not one of {', '.join(MARKS)}")
        found.append(SheetRow(text=cells[1].strip("`"), note=int(cells[2]), mark=mark))
    return Sheet(run_key=header.run_key, run_id=header.run_id, rows=tuple(found))


def read_extra_sheet(text: str) -> EditSheet:
    """Return the run and the marked rows of a filled-in extra-edits sheet.

    Raises:
        ValueError: if the sheet is of another kind, names no run key, or a row's value is not one
            of :data:`EDIT_MARKS` or its span is not of the form ``start-end``.
    """
    header, rows = _header_of(text)
    if header.kind != EXTRA_EDITS:
        raise ValueError(f"the sheet is of {header.kind!r}, not of {EXTRA_EDITS!r}")
    found: list[EditRow] = []
    for cells in rows:
        if not cells[1].startswith("`"):
            continue
        mark = cells[-1]
        if not mark:
            continue
        if mark not in EDIT_MARKS:
            raise ValueError(f"{cells[1]}: {mark!r} is not one of {', '.join(EDIT_MARKS)}")
        span = _SPAN_CELL.match(cells[2])
        if span is None:
            raise ValueError(f"{cells[1]}: {cells[2]!r} is not a span of the form `start-end`")
        found.append(
            EditRow(
                text=cells[1].strip("`"),
                span=(int(span["start"]), int(span["end"])),
                mark=mark,
            )
        )
    return EditSheet(run_key=header.run_key, rows=tuple(found))


def _header_of(text: str) -> tuple[SheetHeader, list[list[str]]]:
    """Return a sheet's header and the cells of its table rows.

    Raises:
        ValueError: if the sheet does not say what it is, or names no run key.
    """
    kind = run_id = run_key = ""
    rows: list[list[str]] = []
    for line in text.splitlines():
        if found := _SHEET_LINE.match(line):
            kind = found.group("kind")
            continue
        if found := _RUN_LINE.match(line):
            run_id = found.group("run_id")
            continue
        if found := _RUN_KEY_LINE.match(line):
            run_key = found.group("run_key")
            continue
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 4:
            rows.append(cells)
    if not kind:
        raise ValueError("the sheet does not say what it is: the line `Sheet: `<kind>`` is missing")
    if not run_key:
        raise ValueError("the sheet names no run key: the line `Run key: `<hash>`` is missing")
    return SheetHeader(kind=kind, run_id=run_id, run_key=run_key), rows


def marks_from_sheet(text: str, drawn: Sample) -> list[Mark]:
    """Return the marks a filled-in explanation sheet holds, checked against *drawn*.

    The marks are the store's own records: their identity is the edit the explanation is about and
    the explanation's hash, so they hold no text and survive a tokenizer change.

    Raises:
        ValueError: if a row names a note that is not in the sample, or the sheet is of another
            run than *drawn*.
    """
    sheet_of = read_sheet(text)
    if sheet_of.run_key != drawn.run_key:
        raise ValueError(
            f"the sheet is of run key {sheet_of.run_key!r}, the sample of {drawn.run_key!r}"
        )
    marks: list[Mark] = []
    for row in sheet_of.rows:
        note = drawn.find(row.text, row.note)
        if note is None:
            raise ValueError(
                f"{row.text}: note {row.note} is not in the sample of {drawn.run_id}; "
                "the sheet and the run disagree"
            )
        marks.append(
            Mark(
                kind="explanation",
                text=note.text,
                span=note.span,
                replacement=note.replacement,
                value=row.mark,
                explanation_sha256=note.explanation_sha256,
            )
        )
    return marks


def marks_from_extra_sheet(text: str, drawn: EditSample) -> list[Mark]:
    """Return the marks a filled-in extra-edits sheet holds, checked against *drawn*.

    An edit mark is about the change and not about a text, so it holds no explanation hash and is
    the one kind of mark that can become an accepted alternative.

    Raises:
        ValueError: if a row names a change that is not in the sample, or the sheet is of another
            run than *drawn*.
    """
    sheet_of = read_extra_sheet(text)
    if sheet_of.run_key != drawn.run_key:
        raise ValueError(
            f"the sheet is of run key {sheet_of.run_key!r}, the sample of {drawn.run_key!r}"
        )
    marks: list[Mark] = []
    for row in sheet_of.rows:
        edit = drawn.find(row.text, row.span)
        if edit is None:
            raise ValueError(
                f"{row.text} {row.span[0]}:{row.span[1]} is not in the sample of {drawn.run_id}; "
                "the sheet and the run disagree"
            )
        marks.append(
            Mark(
                kind="edit",
                text=edit.text,
                span=edit.span,
                replacement=edit.replacement,
                value=row.mark,
            )
        )
    return marks


def unmarked(drawn: Sample, marks: Iterable[Mark]) -> list[SampleNote]:
    """Return the sample notes *marks* does not mark, so a half-filled sheet is visible.

    A mark is matched on the whole identity, the explanation's hash included: a verdict on another
    model's explanation of the same edit does not mark this one.
    """
    marked = {_identity(mark) for mark in marks}
    return [note for note in drawn.notes if _note_identity(note) not in marked]


def correct_share(drawn: Sample, marks: Iterable[Mark]) -> dict[str, tuple[int, int]]:
    """Return ``{language: (correct, total)}`` over *drawn*, counting only ``correct``.

    A mark is matched on the whole identity, the explanation's hash included, so one model is not
    credited with a verdict on another model's explanation.
    """
    verdicts = {_identity(mark): mark.value for mark in marks}
    counted: dict[str, tuple[int, int]] = {}
    for language, notes in drawn.per_language().items():
        found = sum(1 for note in notes if verdicts.get(_note_identity(note)) == "correct")
        counted[language] = (found, len(notes))
    return counted


def _identity(mark: Mark) -> tuple[str, tuple[int, int], str, str]:
    """Return what *mark* is: the text, the span, the replacement and the explanation's hash."""
    return (mark.text, mark.span, mark.replacement, mark.explanation_sha256)


def _note_identity(note: SampleNote) -> tuple[str, tuple[int, int], str, str]:
    """Return what *note* is, in the same shape as :func:`_identity`."""
    return (note.text, note.span, note.replacement, note.explanation_sha256)


def languages_of(notes: Iterable[SampleNote]) -> tuple[str, ...]:
    """Return the languages of *notes*, in the order they first appear."""
    return tuple(dict.fromkeys(note.language for note in notes))


def today() -> str:
    """Return today's date, as a sheet records it."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


__all__ = [
    "CORRECT_SHARE",
    "EDIT_MARKS",
    "EXPLANATIONS",
    "EXTRA_EDITS",
    "MARKS",
    "PER_LANGUAGE",
    "PER_TEXT",
    "EditRow",
    "EditSample",
    "EditSheet",
    "Mark",
    "Sample",
    "SampleEdit",
    "SampleNote",
    "Sheet",
    "SheetHeader",
    "SheetRow",
    "correct_share",
    "explanation_hash",
    "extra_edits_sample",
    "extra_edits_sheet",
    "header_of",
    "languages_of",
    "marks_from_extra_sheet",
    "marks_from_sheet",
    "read_extra_sheet",
    "read_sheet",
    "sample",
    "sheet",
    "today",
    "unmarked",
]
