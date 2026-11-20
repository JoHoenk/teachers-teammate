"""The whole report as one model: the texts, the variants, every version's edits, the numbers.

This is what the page receives, and it is built once, in Python, so that the page has nothing to
compute.  Three things are assembled here that the store does not hold:

* **The rows of a text.**  A text is shown sentence by sentence, and every version of it in the
  same rows, so an edit that runs past a sentence boundary merges the sentences it covers
  (:func:`~.outcomes.sentence_rows`).
* **The complete sentence of every version.**  A list of diff fragments is hard to judge: a reader
  needs the whole corrected sentence, with the changes marked on top.  So each row carries the text
  of the source, of the reference and of every version, and the character offsets of the changes
  inside it.
* **The numbers.**  From :mod:`.numbers`, which reads the same functions the report prints from.

Nothing here is a second implementation of a metric: the outcomes come from :mod:`.outcomes`, the
scorecard from :mod:`.numbers`, and the variant identity from :mod:`.variants`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from teachers_teammate.domain.proofreading.word_diff import tokenize

from ...core import languages, protocol_table
from ...core.labels import UNLABELLED
from ...core.records import text_id
from ...metrics import quality
from ...metrics.offsets import char_to_token
from ...metrics.quality import DEFAULT_BETA
from .. import pooled
from ..load import Loaded, LoadedItem, LoadedRun, Skipped
from . import numbers, outcomes, prompts, variants
from .outcomes import ModelEdit, Orphan, Outcomes, SentenceRow
from .variants import Variant

Span4 = tuple[int, int, tuple[str, ...], int]
"""One edit as the assembler needs it: start, end, replacement tokens, and its index."""


@dataclass(frozen=True)
class Mark:
    """Where one change sits in a row's text, as character offsets into it."""

    start: int
    end: int
    """One past the last character; equal to :attr:`start` for a deletion."""

    index: int
    """The change's index: into the version's edits, or into the text's reference edits."""

    missed: bool = False
    """Whether this marks a reference error the version left alone; :attr:`index` is then an index
    into the text's reference edits, and the marked words are the student's own."""


@dataclass(frozen=True)
class RowText:
    """One row of one version: the complete sentence, and the changes marked in it."""

    text: str
    marks: tuple[Mark, ...]


@dataclass(frozen=True)
class TextRow:
    """One row of a text, shared by every version: the student's sentence and the reference's."""

    start: int
    """First token of the row, an index into :attr:`Text.tokens`."""

    end: int
    source: RowText
    """The student's sentence, with a mark on each reference error."""

    reference: RowText
    """The reference's sentence, with a mark on each of its changes."""


@dataclass(frozen=True)
class RefEdit:
    """One change the reference correction makes, with its corpus label."""

    index: int
    start: int
    end: int
    text: str
    code: str
    """The corpus label (``R:DET:FORM``); empty when no annotated edit matched this change."""

    category: str
    main_type: str
    """The kind of word the label names (``DET``, ``VERB``…), which the error view groups by."""

    row: int


@dataclass(frozen=True)
class Text:
    """One text of the test set, as every screen of the report shows it."""

    id: str
    """``<testset>:<language>/<id>``: two sets that both hold ``de-01`` are two texts."""

    label: str
    """What the page prints: the record's id, with the test set when the store holds more than one."""

    testset: str
    language: str
    band: str
    kind: str
    origin: str
    tokens: tuple[str, ...]
    """``tokenize(source)``, whitespace tokens included: the space the edits are numbered in."""

    rows: tuple[TextRow, ...]
    ref_edits: tuple[RefEdit, ...]


@dataclass(frozen=True)
class Item:
    """One version of one text: what it changed, what came of it, and what it cost."""

    variant: str
    repeat: int
    text: str
    edits: tuple[ModelEdit, ...]
    orphans: tuple[Orphan, ...]
    rows: tuple[RowText | None, ...]
    """The version's complete sentence per row of the text, in the same order as the text's.

    ``None`` for a row the version left as it was: the sentence is then the text's own, and the
    page reads it from there, so an unchanged sentence is not stored once per version."""

    ref_outcomes: tuple[str, ...]
    """``fixed``, ``located`` or ``missed`` for each reference edit, in the text's order."""

    counts: Mapping[str, int]
    f_beta: float | None
    failed: bool
    fallback: bool
    warning: str
    pieces: int
    cost: Mapping[str, Any]
    """Requests, tokens, seconds, cut-offs and memory; empty for a row that recorded none."""


@dataclass(frozen=True)
class ErrorShare:
    """How one variant did on one kind of reference error: shares, each the mean of its repeats."""

    fixed: float | None
    located: float | None
    missed: float | None
    repeats: int
    """How many repeats the shares are the mean of."""


@dataclass(frozen=True)
class ErrorRow:
    """One kind of reference error in one language, and how each variant did on it."""

    group: str
    """The corpus's main type of the error (``DET``, ``ORTH``…), or ``unlabelled``."""

    language: str
    total: int
    """How many errors of this kind the texts of the language hold."""

    shares: Mapping[str, ErrorShare]
    """Per variant: the share of these errors fixed, located and missed, per repeat and then
    averaged, so a variant of three repeats is not counted three times."""

    missed_by_all: Mapping[str, int]
    """Per eval version: how many of these errors every version of every variant of that eval
    version missed.  An error is counted once, however many repeats there are."""


@dataclass(frozen=True)
class Input:
    """One store the report was built from, and what of it could not be used."""

    directory: str
    files: int
    rows: int
    skipped: tuple[Skipped, ...]


@dataclass(frozen=True)
class ReportModel:
    """Everything the page shows, in one object that serialises to the page's data files."""

    inputs: tuple[Input, ...]
    texts: Mapping[str, Text]
    variants: tuple[Variant, ...]
    items: Mapping[str, Item]
    numbers: Mapping[str, numbers.VariantNumbers]
    errors: tuple[ErrorRow, ...]
    prompts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    """The instructions of each variant by the 8 characters of their hash, and whether exact."""

    beta: float = DEFAULT_BETA
    """The weight of the F-score every figure of the report was computed with."""

    def item(self, variant: str, repeat: int, text: str) -> Item | None:
        """Return one version of one text, or ``None`` when that version has not answered it."""
        return self.items.get(item_key(variant, repeat, text))

    def to_dict(self) -> dict[str, Any]:
        """Return the model as JSON-ready dicts, which is what the page's data files hold."""
        return {
            "inputs": [asdict(entry) for entry in self.inputs],
            "texts": {key: asdict(text) for key, text in self.texts.items()},
            "variants": [_variant_dict(variant) for variant in self.variants],
            "items": {key: _item_dict(item) for key, item in self.items.items()},
            "numbers": {key: asdict(found) for key, found in self.numbers.items()},
            "errors": [asdict(entry) for entry in self.errors],
            "eval_versions": list(variants.eval_versions(self.variants)),
            "eval_notes": _eval_notes(variants.eval_versions(self.variants)),
            "prompts": {key: dict(value) for key, value in self.prompts.items()},
            "deciding": numbers.DECIDING,
            "beta": self.beta,
        }


_OPTIONAL_EDIT_KEYS = ("category", "explanation", "ref")
"""The keys of an edit the page reads as absent when they are empty.

A text-mode version has no explanation and no category on any edit, and an extra edit touches no
reference edit, so the empty values are left out of the data file instead of being written once
per edit of every version."""


def _eval_notes(versions: Sequence[int]) -> dict[str, dict[str, str]]:
    """Return what the protocol table says about each eval version: its date and why it exists.

    The page shows it in its info panel, so a reader can tell what ``eval 3`` is without the
    repository.  A version the table does not record has no note.
    """
    entries = {
        int(entry["version"]): entry
        for entry in protocol_table.read_table().get("versions", ())
        if isinstance(entry, dict) and "version" in entry
    }
    notes: dict[str, dict[str, str]] = {}
    for version in versions:
        found = entries.get(version)
        if found is not None:
            notes[str(version)] = {
                "date": str(found.get("date", "")),
                "why": str(found.get("why", "")),
            }
    return notes


def _item_dict(item: Item) -> dict[str, Any]:
    """Return one version as JSON-ready dicts, without the empty optional keys of its edits."""
    stored = asdict(item)
    stored["edits"] = [
        {
            key: value
            for key, value in edit.items()
            if key not in _OPTIONAL_EDIT_KEYS or value not in ("", None)
        }
        for edit in stored["edits"]
    ]
    return stored


def item_key(variant: str, repeat: int, text: str) -> str:
    """Return the key of one version of one text: ``v3/r1/de-01``."""
    return f"{variant}/r{repeat}/{text}"


def build(loaded: Loaded, *, beta: float = DEFAULT_BETA) -> ReportModel:
    """Return the whole report of the store *loaded*.

    Args:
        loaded: The store as :func:`~..load.load` read it: its runs and its test sets.
        beta: Weight of the F-score; precision counts twice as much at ``0.5``.
    """
    runs = loaded.runs
    built = variants.build(runs, _texts_total(loaded))
    items_of = _items_by_variant(built, runs)
    by_text = _by_text(items_of)
    texts = _texts(loaded, by_text)
    items = _items(built, items_of, texts, beta)
    return ReportModel(
        inputs=(_input(loaded),),
        texts=texts,
        variants=built,
        items=items,
        errors=_errors(built, texts, items),
        beta=beta,
        prompts=prompts.prompts_of(built, _languages(loaded), _instructions(built, runs)),
        numbers={
            variant.id: numbers.build(
                variant.id,
                {
                    repeat: list(items_of[(variant.id, repeat)].values())
                    for repeat in variant.repeats
                    if (variant.id, repeat) in items_of
                },
                complete=variant.complete,
                beta=beta,
            )
            for variant in built
        },
    )


def _texts_total(loaded: Loaded) -> dict[str, int]:
    """Return how many texts each test set of the store holds, by set name."""
    return {name: len(records) for name, records in loaded.testsets.items()}


def _languages(loaded: Loaded) -> tuple[str, ...]:
    """Return the language codes of the store's texts, in the order the table lists them."""
    return tuple(
        languages.ordered(
            record.language for records in loaded.testsets.values() for record in records
        )
    )


def _instructions(
    built: Sequence[Variant], runs: Sequence[LoadedRun]
) -> dict[str, Mapping[str, str]]:
    """Return the instruction hash each run recorded per language, by variant id."""
    by_key = {variant.key: variant.id for variant in built}
    found: dict[str, Mapping[str, str]] = {}
    for run in runs:
        found[by_key[variants.identity_of(run.header)]] = run.header.protocol.instructions_dict()
    return found


def _input(loaded: Loaded) -> Input:
    """Return what the footer says about the store, and which of its files did not count."""
    return Input(
        directory=str(loaded.root),
        files=len(loaded.runs),
        rows=sum(len(run.items) for run in loaded.runs),
        skipped=loaded.skipped,
    )


def _items_by_variant(
    built: Sequence[Variant], runs: Sequence[LoadedRun]
) -> dict[tuple[str, int], dict[str, LoadedItem]]:
    """Return the answers of each repeat of each variant, keyed by the text's id."""
    by_key = {variant.key: variant.id for variant in built}
    found: dict[tuple[str, int], dict[str, LoadedItem]] = {}
    for run in runs:
        variant = by_key[variants.identity_of(run.header)]
        found[(variant, run.repeat)] = {item.item: item for item in run.items}
    return found


def _by_text(
    items_of: Mapping[tuple[str, int], Mapping[str, LoadedItem]],
) -> dict[str, list[LoadedItem]]:
    """Return every answer of every version of a text, keyed by the text's key.

    The key names the test set as well as the text: two sets of one store both hold ``de-01``, and
    an answer belongs to the set its run was asked about.
    """
    found: dict[str, list[LoadedItem]] = {}
    for items in items_of.values():
        for item in items.values():
            found.setdefault(text_key(item), []).append(item)
    return found


def text_key(item: LoadedItem) -> str:
    """Return the key of the text an answer is about: ``<testset>:<language>/<id>``."""
    return f"{item.header.input.testset}:{item.answer.text}"


def _texts(loaded: Loaded, by_text: Mapping[str, Sequence[LoadedItem]]) -> dict[str, Text]:
    """Return every text of the store, its rows wide enough for every version's edits."""
    found: dict[str, Text] = {}
    several = len(loaded.testsets) > 1
    for name, records in loaded.testsets.items():
        for record in records:
            key = f"{name}:{text_id(record)}"
            tokens = tokenize(record.source)
            versions = by_text.get(key, ())
            reference_changes = outcomes.reference_edits(
                record.source, record.reference, record.labels
            )
            # A row is wide enough for every edit of every version and of the reference, so a
            # reference edit that runs past a sentence boundary never lands outside its row.
            rows = outcomes.sentence_rows(
                _token_spans(record.source, record.sentence_spans),
                [
                    *(
                        edit
                        for item in versions
                        for edit in quality.ordered_edits(record.source, item.output)
                    ),
                    *(edit for edit, _, _ in reference_changes),
                ],
                len(tokens),
            )
            by_edit = outcomes.edits_by_label(record.source, record.labels)
            ref_edits = tuple(
                RefEdit(
                    index=index,
                    start=edit[0],
                    end=edit[1],
                    text="".join(edit[2]),
                    code=code,
                    category=category,
                    main_type=by_edit[edit].main_type if edit in by_edit else UNLABELLED,
                    row=outcomes.row_of(rows, edit[0], edit[1]),
                )
                for index, (edit, code, category) in enumerate(reference_changes)
            )
            found[key] = Text(
                id=key,
                label=f"{name}:{record.id}" if several else record.id,
                testset=name,
                language=record.language,
                band=record.band,
                kind=record.kind,
                origin=record.origin,
                tokens=tuple(tokens),
                rows=tuple(
                    TextRow(
                        start=row.start,
                        end=row.end,
                        source=_plain(tokens, row, _reference_spans(ref_edits, index)),
                        reference=_applied(tokens, row, _reference_spans(ref_edits, index)),
                    )
                    for index, row in enumerate(rows)
                ),
                ref_edits=ref_edits,
            )
    return found


def _token_spans(source: str, spans: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Return the token span of every character span of the corpus's sentence segmentation.

    A span that does not lie on token boundaries names no row and is left out, so the sentence
    merges with its neighbour instead of the text losing a row.
    """
    found: list[tuple[int, int]] = []
    for start, end in spans:
        first = char_to_token(source, start)
        last = char_to_token(source, end)
        if first is not None and last is not None and last >= first:
            found.append((first, last))
    return tuple(found)


def _items(
    built: Sequence[Variant],
    items_of: Mapping[tuple[str, int], Mapping[str, LoadedItem]],
    texts: Mapping[str, Text],
    beta: float,
) -> dict[str, Item]:
    """Return every version of every text that a run answered."""
    found: dict[str, Item] = {}
    for variant in built:
        for repeat in variant.repeats:
            for item in items_of.get((variant.id, repeat), {}).values():
                text = texts[text_key(item)]
                found[item_key(variant.id, repeat, text.id)] = _item(
                    variant, repeat, text, item, beta
                )
    return found


def _item(variant: Variant, repeat: int, text: Text, item: LoadedItem, beta: float) -> Item:
    """Return one version of one text: its edits, its sentences, its counts and its cost."""
    spans = tuple(SentenceRow(one.start, one.end) for one in text.rows)
    found = outcomes.outcomes(
        item.record.source,
        item.record.reference,
        item.record.labels,
        item.output,
        outcomes.changes_of(item.record.source, item.output, item.answer.review),
        spans,
    )
    return Item(
        variant=variant.id,
        repeat=repeat,
        text=text.id,
        edits=found.edits,
        orphans=found.orphans,
        rows=tuple(
            _applied(text.tokens, span, edits, _missed_spans(found, index))
            if (edits := _model_spans(found, index))
            else None
            for index, span in enumerate(spans)
        ),
        ref_outcomes=tuple(edit.outcome for edit in found.reference),
        counts=_counts(item, found),
        f_beta=pooled.f_beta(pooled.pooled([item]), beta),
        failed=item.failed,
        fallback=item.fallback,
        warning=item.answer.warning,
        pieces=item.answer.pieces,
        cost=_cost(item),
    )


def _errors(
    built: Sequence[Variant], texts: Mapping[str, Text], items: Mapping[str, Item]
) -> tuple[ErrorRow, ...]:
    """Return, per language and kind of reference error, how each variant did on it.

    The shares are per repeat and then averaged over the repeats, never counts summed over them,
    so a variant of three repeats weighs as much as one of one.  ``missed_by_all`` is decided per
    error, over every version of every variant of one eval version: an error one version fixed or
    located is not missed by all, however the others did.
    """
    totals: dict[tuple[str, str], int] = {}
    per_repeat: dict[tuple[str, str], dict[str, dict[int, dict[str, int]]]] = {}
    answered: dict[tuple[str, str], dict[str, dict[int, int]]] = {}
    missed_by_all: dict[tuple[str, str], dict[str, int]] = {}
    for text in texts.values():
        for edit in text.ref_edits:
            key = (text.language, edit.main_type)
            totals[key] = totals.get(key, 0) + 1
            outcomes_by_version: dict[int, list[str]] = {}
            for variant in built:
                for repeat in variant.repeats:
                    item = items.get(item_key(variant.id, repeat, text.id))
                    if item is None:
                        continue
                    outcome = item.ref_outcomes[edit.index]
                    counts = (
                        per_repeat.setdefault(key, {})
                        .setdefault(variant.id, {})
                        .setdefault(repeat, {"fixed": 0, "located": 0, "missed": 0})
                    )
                    counts[outcome] += 1
                    seen = answered.setdefault(key, {}).setdefault(variant.id, {})
                    seen[repeat] = seen.get(repeat, 0) + 1
                    outcomes_by_version.setdefault(variant.eval_version, []).append(outcome)
            for version, found in outcomes_by_version.items():
                if found and all(outcome == "missed" for outcome in found):
                    by_version = missed_by_all.setdefault(key, {})
                    by_version[str(version)] = by_version.get(str(version), 0) + 1
    return tuple(
        ErrorRow(
            group=group,
            language=language,
            total=totals[(language, group)],
            shares=_shares(per_repeat.get((language, group), {}), answered[(language, group)])
            if (language, group) in answered
            else {},
            missed_by_all=missed_by_all.get((language, group), {}),
        )
        for language, group in sorted(totals, key=lambda key: (key[0], -totals[key], key[1]))
    )


def _shares(
    counts: Mapping[str, Mapping[int, Mapping[str, int]]],
    answered: Mapping[str, Mapping[int, int]],
) -> dict[str, ErrorShare]:
    """Return per variant the mean over its repeats of each outcome's share of the errors."""
    found: dict[str, ErrorShare] = {}
    for variant, by_repeat in counts.items():
        parts = {
            name: [
                by_repeat[repeat][name] / answered[variant][repeat]
                for repeat in by_repeat
                if answered[variant][repeat]
            ]
            for name in ("fixed", "located", "missed")
        }
        found[variant] = ErrorShare(
            fixed=_mean(parts["fixed"]),
            located=_mean(parts["located"]),
            missed=_mean(parts["missed"]),
            repeats=len(by_repeat),
        )
    return found


def _mean(values: Sequence[float]) -> float | None:
    """Return the mean of *values*, or ``None`` when there are none."""
    return sum(values) / len(values) if values else None


def _counts(item: LoadedItem, found: Outcomes) -> dict[str, int]:
    """Return the figures of one version of one text, as the badges and the drawer show them."""
    exact, located, extra = found.counts
    fixed, located_ref, missed = found.reference_counts
    return {
        "exact": exact,
        "located": located,
        "extra": extra,
        "fixed": fixed,
        "located_ref": located_ref,
        "missed": missed,
        "tp": item.true_positives,
        "fp": item.false_positives,
        "found": item.found,
        "fn": item.false_negatives,
        "explained": item.explained,
        "needed": item.needed,
        "unclear": item.scored.unclear,
        "alone": int(item.scores.get("alone", 0)),
        "alone_of": int(item.scores.get("alone_of", 0)),
    }


def _cost(item: LoadedItem) -> dict[str, Any]:
    """Return what one version of one text cost, or an empty dict when nothing was measured."""
    performance = item.performance
    if performance is None:
        return {}
    return {
        "requests": performance.request_count,
        "tokens_in": performance.prompt_tokens,
        "tokens_out": performance.answer_tokens,
        "seconds": item.seconds,
        "generation_s": performance.generation_s,
        "cutoffs": sum(1 for request in performance.requests if request.done_reason == "length"),
        "vram_peak_mb": performance.vram_peak_mb,
    }


def _reference_spans(ref_edits: Sequence[RefEdit], row: int) -> list[Span4]:
    """Return the reference edits of one row as the assembler needs them."""
    return [
        (edit.start, edit.end, tuple(tokenize(edit.text)), edit.index)
        for edit in ref_edits
        if edit.row == row
    ]


def _model_spans(found: Outcomes, row: int) -> list[Span4]:
    """Return the model's edits of one row as the assembler needs them."""
    return [
        (edit.start, edit.end, tuple(tokenize(edit.text)), index)
        for index, edit in enumerate(found.edits)
        if edit.row == row
    ]


def _missed_spans(found: Outcomes, row: int) -> list[Span4]:
    """Return the reference errors of one row that the version missed, as the assembler needs."""
    return [
        (edit.start, edit.end, (), edit.index)
        for edit in found.reference
        if edit.row == row and edit.outcome == outcomes.MISSED
    ]


def _plain(tokens: Sequence[str], row: SentenceRow, edits: Sequence[Span4]) -> RowText:
    """Return one row's text as it is, with a mark on the tokens each edit covers."""
    offsets = [0]
    for token in tokens[row.start : row.end]:
        offsets.append(offsets[-1] + len(token))
    return RowText(
        text="".join(tokens[row.start : row.end]),
        marks=tuple(
            Mark(
                start=offsets[start - row.start],
                end=offsets[end - row.start],
                index=index,
            )
            for start, end, _, index in edits
        ),
    )


def _applied(
    tokens: Sequence[str], row: SentenceRow, edits: Sequence[Span4], missed: Sequence[Span4] = ()
) -> RowText:
    """Return one row's text with *edits* applied, and where each of them landed.

    The marks are character offsets into the returned text, so the page can mark a word without
    knowing how the tokens were spliced.  A deletion is a mark of zero width at the place the
    words were taken out.  *missed* are reference errors the version left alone: their words are
    the student's own, still in the text, and they get a mark of their own (``missed=True``), a
    zero-width one where the reference only adds a word (before an edit that starts at the same
    place).  One whose words an edit replaced is left out: they are no longer there.
    """
    pieces: list[str] = []
    marks: list[Mark] = []
    where: dict[int, int] = {}
    position = 0
    cursor = row.start

    def keep(until: int) -> None:
        nonlocal position
        for token in range(cursor, until):
            where[token] = position
            position += len(tokens[token])
            pieces.append(tokens[token])

    for start, end, replacement, index in sorted(edits, key=lambda edit: (edit[0], edit[1])):
        keep(max(cursor, start))
        where.setdefault(start, position)
        text = "".join(replacement)
        pieces.append(text)
        marks.append(Mark(start=position, end=position + len(text), index=index))
        position += len(text)
        cursor = max(cursor, end)
    keep(row.end)
    where[row.end] = position
    for start, end, _, index in missed:
        if start == end:
            if start in where:
                marks.append(Mark(start=where[start], end=where[start], index=index, missed=True))
        elif start in where and end - 1 in where:
            last = where[end - 1] + len(tokens[end - 1])
            marks.append(Mark(start=where[start], end=last, index=index, missed=True))
    marks.sort(key=lambda mark: (mark.start, mark.end))
    return RowText(text="".join(pieces), marks=tuple(marks))


def _variant_dict(variant: Variant) -> dict[str, Any]:
    """Return one variant as the page reads it: its label, its facts and how far it got."""
    return {
        "id": variant.id,
        "label": variant.label,
        "facts": dict(variant.facts),
        "repeats": list(variant.repeats),
        "texts_done": {str(repeat): done for repeat, done in variant.texts_done.items()},
        "complete": variant.complete,
        "eval_version": variant.eval_version,
    }


__all__ = [
    "ErrorRow",
    "ErrorShare",
    "Input",
    "Item",
    "Mark",
    "RefEdit",
    "ReportModel",
    "RowText",
    "Text",
    "TextRow",
    "build",
    "item_key",
    "text_key",
]
