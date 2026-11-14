"""The typed records the writing-model tools read and write, and their versioned JSONL format.

The results were plain dicts whose keys depended on the kind of row, and they are published, so the
schema is explicit and carries a version.  ``schema_version: 1`` rows are written by
:func:`write_rows`; a line without a version is an older v0 row and is mapped on read.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, fields
import hashlib
import json
from pathlib import Path

from teachers_teammate.domain.correction_review import CorrectionReview

from . import languages
from .answer_mode import DEFAULT_MODE, AnswerMode, parse_mode
from .error_labels import ReferenceLabel
from .item_scores import item_scores
from .performance import Performance
from .quality import (
    EditScore,
    count_explained,
    score_edits,
    unchanged_sentences,
)
from .redaction import word_count

SCHEMA_VERSION = 1
"""Version of the JSONL lines this module writes.  Fields added later (``labels``, ``mode``…) have
defaults, so a line written before them is still version 1 and reads as before."""

_MARK_MARKS = ("**", "__", "~~")
"""Markdown marks some models add to "highlight" their changes; not part of the text."""


@dataclass(frozen=True)
class TestRecord:
    """One test case: a learner text and what the reference correction makes of it."""

    id: str
    language: str
    kind: str
    """``"errors"`` (a text with mistakes, with a reference correction) or ``"correct"``."""

    origin: str
    """Where the text comes from; kept so a licence question can be answered later."""

    source: str
    reference: str = ""
    """The reference correction; empty for a ``"correct"`` text."""

    sentences: tuple[str, ...] = ()
    """The already-correct sentences of a ``"correct"`` text, to check they stay untouched."""

    labels: tuple[ReferenceLabel, ...] = ()
    """The corpus label of every change the reference makes; empty when the text has none (the
    invented French texts, or a set built before the labels were kept)."""

    band: str = ""
    """The band the recipe drew the text for (``short``, ``page``, ``pages``); empty when the
    recipe has none, as for the invented French texts."""

    sentence_spans: tuple[tuple[int, int], ...] = ()
    """The token span of every sentence of the corpus's own segmentation, in text order; empty for
    a text without one (the invented French texts, or a set built before the spans were kept)."""

    unchanged_sentences: tuple[int, ...] = ()
    """Indices into :attr:`sentence_spans` of the sentences the reference leaves alone."""

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record, tagged with :data:`SCHEMA_VERSION`.

        A field that is empty is left out, so a record without a band or spans writes the line it
        wrote before those fields existed, and keeps its ``records_sha256``.
        """
        record = {"schema_version": SCHEMA_VERSION, **asdict(self)}
        for name in ("band", "sentence_spans", "unchanged_sentences"):
            if not record[name]:
                del record[name]
        return record

    @classmethod
    def from_dict(cls, record: dict) -> TestRecord:
        """Build a record from :meth:`to_dict` output (a missing version counts as v0)."""
        version = record.get("schema_version", 0)
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported test record schema_version {version!r}")
        values = {name: record[name] for name in _field_names(cls) if name in record}
        if "sentences" in values:
            values["sentences"] = tuple(values["sentences"])
        if "labels" in values:
            values["labels"] = tuple(ReferenceLabel.from_dict(label) for label in values["labels"])
        if "sentence_spans" in values:
            values["sentence_spans"] = tuple(tuple(span) for span in values["sentence_spans"])
        if "unchanged_sentences" in values:
            values["unchanged_sentences"] = tuple(values["unchanged_sentences"])
        return cls(**values)


@dataclass(frozen=True)
class ResultRow:
    """One model's answer for one test case, with everything needed to re-score it."""

    id: str
    model: str
    temperature: float
    language: str
    kind: str
    origin: str
    seconds: float
    failed: bool
    fallback: bool
    has_review: bool
    source: str
    output: str
    warning: str = ""
    reference: str = ""
    notes: tuple[dict, ...] = ()
    """The review's notes as plain dicts, for the spot-check sheet."""

    changes: tuple[dict, ...] = ()
    """The model's changes with the note that explains each, in text order.

    Each entry holds ``edit`` (``[start, end, replacement]`` over the source tokens, ``None``
    for a note that explains no change), ``category``, ``explanation`` and, for such an orphan,
    ``orphan: True``.  This is what lets an exported run store the model's edits *and* link them
    to their notes; a row written before the field existed has none.
    """

    unclear: int = 0
    """How many unclear expressions the review listed."""

    explained: int = 0
    needed: int = 0
    orphans: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    sentences: tuple[str, ...] = ()
    unchanged: int = 0
    band: str = ""
    """The band the recipe drew the text for (``short``, ``page``, ``pages``); empty when the
    recipe has none, as for the invented French texts."""

    scores: dict[str, int] = field(default_factory=dict)
    """The numbers of :func:`~.item_scores.item_scores`: GLEU for the model and the source, the
    recall by half, and left alone and coverage when the set kept sentence spans.  Empty for a
    ``correct`` text and for a row written before they were stored."""

    mode: AnswerMode = DEFAULT_MODE
    """How the model was asked: ``review`` (the app's JSON answer) or ``text`` (corrected text
    only).  A row written before the mode was recorded is a ``review`` row."""

    pieces: int = 1
    """How many non-blank pieces the text was sent in: one when it fitted one request.

    The app splits a text longer than the corrector's budget
    (:class:`~teachers_teammate.infrastructure.correction.ChunkingCorrector`), so a long text
    costs several requests and can lose the context across a cut.  A row written before the
    field existed is a one-piece row.
    """

    category_checked: int = 0
    """Errors the model fixed exactly with a note, whose corpus label maps to a category."""

    category_agreed: int = 0
    """Of those, how many notes carry the category the label maps to (``error_labels``)."""

    performance: Performance | None = None
    """Requests, tokens, durations and memory of this text; ``None`` for a row written before
    they were recorded."""

    raw_output: str = field(default="", compare=False)
    """The output as it was before re-scoring dropped added Markdown marks."""

    @property
    def item(self) -> str:
        """The manifest's id of the text: the part of :attr:`id` after the language."""
        return self.id.split("/", 1)[-1]

    @property
    def sentences_total(self) -> int:
        """The correct sentences of a ``correct`` text, else 0."""
        return len(self.sentences)

    @property
    def marks_added(self) -> bool:
        """Whether the model added Markdown marks the source does not have."""
        return added_marks(self)

    @property
    def words(self) -> int:
        """Words and marks of the source, as the manifest counts them."""
        return word_count(self.source)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable row, tagged with :data:`SCHEMA_VERSION`."""
        record = asdict(self)
        record.pop("raw_output", None)
        return {"schema_version": SCHEMA_VERSION, **record}

    @classmethod
    def from_dict(cls, record: dict) -> ResultRow:
        """Build a row from a stored line, migrating a v0 line when it carries no version."""
        version = record.get("schema_version", 0)
        if version not in (0, SCHEMA_VERSION):
            raise ValueError(f"unsupported result schema_version {version!r}")
        values = {name: record[name] for name in _field_names(cls) if name in record}
        for name in ("notes", "sentences", "changes"):
            if name in values:
                values[name] = tuple(values[name])
        values.pop("raw_output", None)
        if "mode" in values:
            values["mode"] = parse_mode(values["mode"])
        if isinstance(values.get("performance"), dict):
            values["performance"] = Performance.from_dict(values["performance"])
        return cls(**values)


def _field_names(cls: type) -> tuple[str, ...]:
    """Return the names of the dataclass fields of *cls*."""
    return tuple(entry.name for entry in fields(cls))


def read_rows(path: Path) -> list[ResultRow]:
    """Return every row stored in *path* (JSONL), rejecting an unknown schema version."""
    with path.open(encoding="utf-8") as handle:
        return [ResultRow.from_dict(json.loads(line)) for line in handle if line.strip()]


def read_all(out: Path) -> list[ResultRow]:
    """Return the rows of every results file below *out*, in file-name order."""
    rows: list[ResultRow] = []
    for path in sorted(out.glob("*.jsonl")):
        rows.extend(read_rows(path))
    return rows


def write_rows(path: Path, rows: Iterable[ResultRow]) -> None:
    """Append *rows* to *path*, one JSON object per line, so an interrupted run resumes."""
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            _write(handle, row.to_dict())


def write_records(path: Path, records: Iterable[TestRecord]) -> None:
    """Write *records* to *path*, one JSON object per line, replacing the file."""
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            _write(handle, record.to_dict())


def read_records(path: Path) -> list[TestRecord]:
    """Return the test records stored in *path*."""
    with path.open(encoding="utf-8") as handle:
        return [TestRecord.from_dict(json.loads(line)) for line in handle if line.strip()]


def testset_languages(testset: Path) -> list[str]:
    """Return the languages a built test set holds: the stems of its ``<language>.jsonl`` files.

    A set need not hold every language the tool can ask in (a German-only set has no
    ``en.jsonl``), so the files decide what a run covers, not the table of names.  Only a stem
    that looks like a language code (two or three lower-case letters) counts, so ``notes.jsonl``
    next to the texts is not read as a language.
    """
    return languages.ordered(
        path.stem for path in testset.glob("*.jsonl") if is_language_code(path.stem)
    )


def is_language_code(text: str) -> bool:
    """Return whether *text* looks like a language code: two or three lower-case ASCII letters."""
    return 2 <= len(text) <= 3 and text.isascii() and text.isalpha() and text.islower()


def read_testset(testset: Path, codes: Sequence[str]) -> list[TestRecord]:
    """Return the test records of the languages *codes*, in file order."""
    records: list[TestRecord] = []
    for code in codes:
        records.extend(read_records(testset / f"{code}.jsonl"))
    return records


def read_records_by_id(testset: Path) -> dict[str, TestRecord]:
    """Return every test record below *testset*, keyed ``language/id``.

    A report needs the record of a row for what the row does not carry: the labels, the band and
    the sentence spans the numbers are computed from.
    """
    records: dict[str, TestRecord] = {}
    for path in sorted(testset.glob("*.jsonl")):
        for record in read_records(path):
            records[record_key(record)] = record
    return records


def read_labels(testset: Path) -> dict[str, tuple[ReferenceLabel, ...]]:
    """Return the corpus labels of every test record below *testset*, keyed ``language/id``.

    The result rows do not carry the labels, so a report can group an old results file by error
    type as soon as the test set is rebuilt with labels.
    """
    return {key: record.labels for key, record in read_records_by_id(testset).items()}


def result_file_name(
    model: str, temperature: float, mode: AnswerMode = DEFAULT_MODE, repeat: int | None = None
) -> str:
    """Return the name of the results file of one model, temperature, answer mode and repeat.

    The default mode and a single run keep the names earlier runs used, so those files still
    resume; only a second answer mode and the repeats of a repeated run get a suffix.
    """
    suffix = ("" if mode == DEFAULT_MODE else f"__{mode.value}") + (
        "" if repeat is None else f"__r{repeat}"
    )
    return f"{model.replace('/', '_').replace(':', '_')}__T{temperature:g}{suffix}.jsonl"


def read_done_ids(path: Path) -> set[str]:
    """Return the ids already stored in *path*, so an interrupted run can resume."""
    if not path.exists():
        return set()
    return {row.id for row in read_rows(path)}


def records_sha256(records: Sequence[TestRecord]) -> str:
    """Return the SHA-256 of the test records, in order: which texts a run was asked about.

    A test set rebuilt under the same name holds other texts; a resumed run compares this hash,
    not the name.
    """
    text = json.dumps([record.to_dict() for record in records], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_key(record: TestRecord) -> str:
    """Return the id a result row of *record* is stored under: ``<language>/<record id>``."""
    return f"{record.language}/{record.id}"


def missing_ids(records: Sequence[TestRecord], done: set[str]) -> list[str]:
    """Return the keys of the *records* that have no row among the *done* ids.

    A results file is named after the model and the settings, not the languages, so it may hold
    the rows of other languages too: what is done has to be compared by id, not by count.
    """
    return [key for key in map(record_key, records) if key not in done]


def _write(handle, record: dict) -> None:
    """Write one JSON object as a line."""
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def added_marks(row: ResultRow) -> bool:
    """Whether the output carries Markdown marks the source does not.

    A model that "highlights" its changes puts literal asterisks into the text a teacher
    receives; the structured-output check does not see it.
    """
    output = row.raw_output or row.output
    marks = (*_MARK_MARKS, "[unclear]")
    return any(mark in output and mark not in row.source for mark in marks)


def strip_added_marks(source: str, output: str) -> str:
    """Return *output* without the Markdown marks that *source* does not contain.

    Scoring ignores them by default (``report --count-marks`` counts them): only the words
    matter.
    """
    for mark in _MARK_MARKS:
        if mark not in source:
            output = output.replace(mark, "")
    return output


def without_added_marks(row: ResultRow) -> str:
    """Return the output of *row* without Markdown marks the source does not contain."""
    return strip_added_marks(row.source, row.output)


def edit_score(kind: str, source: str, corrected: str, reference: str) -> EditScore:
    """Return the edit counts of *corrected* against *reference*; zero for a ``correct`` text.

    The one place a row's true and false positives are decided, for a text that was just
    proofread and for a stored row that is scored again.
    """
    if kind != "errors":
        return EditScore(true_positives=0, false_positives=0, false_negatives=0)
    return score_edits(source, corrected, reference)


def review_for_counting(
    mode: AnswerMode, review: CorrectionReview | None, source: str, corrected: str
) -> CorrectionReview | None:
    """Return the review the explanations are counted against.

    A review-mode text that fell back to corrections only gives the teacher the corrections but
    nothing that explains them, so its changes count as unexplained: it is counted against an
    empty review.  The rule is applied when a text is proofread and again when a stored row is
    re-scored, so a report of an older run says the same.
    """
    if review is None and mode in (AnswerMode.REVIEW, AnswerMode.EXCHANGE) and corrected != source:
        return CorrectionReview()
    return review


def rescore(row: ResultRow, *, count_marks: bool, record: TestRecord | None = None) -> ResultRow:
    """Return *row* with its scores recomputed from the stored texts.

    The results files keep source, reference and output, so a change of the scoring rules needs
    no new model run: the report always recomputes instead of trusting the stored numbers.

    Args:
        row: The stored row.
        count_marks: Whether Markdown marks the model added count as changes.
        record: The test record of the row, when the caller has the test set.  With it the band
            and the numbers of :func:`~.item_scores.item_scores` are recomputed from the record's
            spans; without it the stored ones are kept, because a row alone does not know the
            sentence spans of its text (but dropped when *count_marks* is set, as they were
            computed on the text without the marks).
    """
    from dataclasses import replace  # noqa: PLC0415  # keeps the import list small

    output = row.output if count_marks else without_added_marks(row)
    updated = replace(row, output=output, raw_output=row.raw_output or row.output)
    if row.kind != "errors":
        return replace(updated, unchanged=unchanged_sentences(list(row.sentences), output))
    score = edit_score(row.kind, row.source, output, row.reference)
    updated = replace(
        updated,
        true_positives=score.true_positives,
        false_positives=score.false_positives,
        false_negatives=score.false_negatives,
    )
    if (
        not row.has_review
        and (fallback := review_for_counting(row.mode, None, row.source, output)) is not None
    ):
        explained = count_explained(row.source, output, fallback)
        updated = replace(
            updated,
            explained=explained.explained,
            needed=explained.needed,
            orphans=explained.orphans,
        )
    if record is None:
        if count_marks:
            # The stored numbers were computed on the text without the marks; next to scores
            # that count them they would contradict each other, so they are not shown.
            return replace(updated, scores={})
        return updated
    return replace(
        updated,
        band=record.band,
        scores=item_scores(
            row.source,
            output,
            row.reference,
            sentence_spans=record.sentence_spans,
            unchanged_sentences=record.unchanged_sentences,
        ),
    )
