"""Several writing models, one text: where their answers differ, and what each one cost.

A teacher picks a document they have read, ticks two to four writing models and sees what each of
them changed.  The comparison is built here, from the answers alone: this module knows nothing
about models, settings or requests, so the same figures can be read by a window, a test and a
report.

* **Sites** — the places where any answer changed the text.  The changes of all versions are
  grouped where they touch the same words, so a place one model fixed differently by each model is
  one row with one option per wording.  Two changes that merely stand next to each other stay two
  rows (``spans_touch`` does not join an insertion to the word beside it).
* **Rows** — the sentences of the student's text, as wide as the widest change any version makes,
  so every version's answer can be read in the same rows and no explanation lands outside its row.
* **Figures** — per version: how many changes, how many another version made too (shared), how
  many no other version touched (alone), how many the model explained, how many expressions it
  found unclear, and what went wrong: text lost, parts left out and put back, marks added, a
  failed answer, a fallback.
* **Marks** — the teacher's own verdicts on the options (``option_marks``), carried by the options
  and counted per version: right, wrong, unmarked, and the right changes a model missed.

There is no winner.  One text, answered with temperatures above 0, shows differences, not which
model is better in general.  What decides is the teacher's marks.

Pure: no I/O, no Qt, no provider imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from .edit_agreement import (
    ExplainedCount,
    count_explained,
    ordered_edits,
    spans_touch,
    token_spans,
    word_positions,
)
from .marks import added_marks
from .option_marks import Identity, MarkCount, OptionMark, Verdict, identity, verdicts_of
from .review import AlignedReview, CorrectionReview, align
from .sentences import SentenceRow, Span, sentence_rows, sentence_spans
from .text_loss import TextLoss, measure, words
from .word_diff import Edit, text_offsets, tokenize

__all__ = [
    "ChangeSite",
    "Comparison",
    "Made",
    "Option",
    "Row",
    "Version",
    "VersionFigures",
    "compare",
]

#: What one version's note says about one option: its wording and the sentence behind it.
Note = tuple[Span, str, str]


@dataclass(frozen=True)
class Version:
    """One model's answer to the text, as the comparison reads it."""

    key: str
    """Identity of the settings the answer was made with: the row's name in the comparison."""

    text: str
    """The answer, in plain text (see ``TextFormatter.plain_text``)."""

    review: CorrectionReview | None = None
    """The model's explanations, or ``None`` in corrections-only mode."""

    seconds: float = 0.0
    """How long the answer took, loading the model included."""

    failed: bool = False
    """``True`` when the model answered unusably and *text* is the unchanged source."""

    fell_back: bool = False
    """``True`` when part of what was asked is missing: explanations, or a stretch put back."""

    left_out: int = 0
    """How many stretches of the text the model left out and the app put back."""

    repeats: tuple[str, ...] = ()
    """The same model's further answers, in plain text: a second run shows what is chance."""


@dataclass(frozen=True)
class Made:
    """One version's option at a change site, with what it said about it."""

    key: str
    """The version that wrote this replacement."""

    category: str
    """The note's category, or ``other`` when the version explained nothing."""

    explanation: str
    """The note's sentence, or ``""``."""

    first_run: bool = True
    """``True`` when the version's first answer wrote this option; a wording that only a further
    run of the same model wrote is chance, and the counts of a version are those of its first."""


@dataclass(frozen=True)
class Option:
    """One way the student's words could read: a replacement one or more versions chose."""

    start: int
    end: int
    """Half-open span in the characters of the plain text that this option replaces."""

    replacement: str
    """What stands there in these versions, in the notation of the text."""

    made_by: tuple[Made, ...]
    """The versions that made it, in the order the comparison lists them."""

    verdict: Verdict | None = None
    """What the teacher said about this option; ``None`` when nobody marked it."""

    once: bool = False
    """``True`` when one of the model's runs wrote this wording and another did not: unsure."""

    @property
    def keys(self) -> tuple[str, ...]:
        """Return the versions that made this option."""
        return tuple(made.key for made in self.made_by)

    def first_by(self, key: str) -> bool:
        """Return whether the first answer of the version *key* wrote this option."""
        return any(made.key == key and made.first_run for made in self.made_by)


@dataclass(frozen=True)
class ChangeSite:
    """One place where at least one version changed the student's text."""

    start: int
    end: int
    """Half-open span in the characters of the plain text; equal ends for an insertion."""

    original: str
    """The student's words at this place; ``""`` for an insertion."""

    options: tuple[Option, ...]
    """The replacements, in the order the versions first made them."""

    @property
    def agreed(self) -> int:
        """Return how many versions made the most-shared option (``1`` when all differ)."""
        return max((len(option.made_by) for option in self.options), default=0)

    @property
    def unanimous(self) -> bool:
        """Return whether every version that changed here wrote the same replacement."""
        return len(self.options) == 1


@dataclass(frozen=True)
class Row:
    """One row of the side-by-side view: a sentence, or several, as every version reads it."""

    start: int
    end: int
    """The token span of the row in the student's text (see ``sentence_rows``)."""

    source: str
    """The student's text of the row."""

    versions: tuple[str, ...]
    """Each version's text of the row, aligned with :attr:`Comparison.versions`."""

    changed: tuple[bool, ...]
    """Whether each version changed anything in this row, aligned with :attr:`versions`."""


@dataclass(frozen=True)
class VersionFigures:
    """What one version did, and what it cost."""

    key: str
    changes: int
    """How many places this version changed."""

    shared: int
    """Changes another version made too, word for word."""

    alone: int
    """Changes no other version touched: only this model saw something here."""

    explained: ExplainedCount
    """How many changes carry an explanation, how many need one, and orphans."""

    unclear: int
    """Expressions this model called unclear or ambiguous."""

    marks_added: bool
    """``True`` when the answer carries Markdown marks the student's text did not have."""

    loss: TextLoss
    """What the answer kept of the words it was given."""

    left_out: int
    """Stretches the model left out and the app put back."""

    failed: bool
    """The model answered unusably; its text is the unchanged source."""

    fell_back: bool
    """Part of what was asked is missing from the answer."""

    seconds: float
    """How long the answer took."""

    words: int
    """How many words the text has, the figure a time is measured against."""

    marked: MarkCount
    """What the teacher's verdicts come to for this version."""

    runs: int = 1
    """How many times this model answered the text: one, or more when it was run again."""

    in_every_run: int = 0
    """Changes it made in every run: the ones that are not chance."""

    once: int = 0
    """Changes it made in some runs but not all: unsure whether they belong to the text."""

    @property
    def seconds_per_100_words(self) -> float:
        """Return the time this answer took per 100 words of the text (``0`` for an empty text)."""
        return self.seconds * 100 / self.words if self.words else 0.0

    @property
    def problems(self) -> tuple[str, ...]:
        """Return what is wrong with this answer, as ids the words of a window name.

        ``[]`` for an answer that is the text, corrected.  The ids are stable, because a window,
        a test and a line of a report all list the same problems in the same order.
        """
        found: list[str] = []
        if self.failed:
            found.append("failed")
        if self.fell_back:
            found.append("fell_back")
        if self.left_out:
            found.append("left_out")
        if self.loss.lost:
            found.append("text_lost")
        if self.loss.added:
            found.append("text_added")
        if self.marks_added:
            found.append("marks_added")
        return tuple(found)


@dataclass(frozen=True)
class Comparison:
    """One text, the answers of several writing models, and where they differ."""

    source: str
    """The student's text, in plain text: what every answer starts from."""

    versions: tuple[VersionFigures, ...]
    """What each version did, in the order the comparison was asked for."""

    sites: tuple[ChangeSite, ...]
    """Every place at least one version changed, in text order."""

    rows: tuple[Row, ...]
    """The text's sentences as every version reads them, in text order."""

    @property
    def words(self) -> int:
        """Return how many words the text has."""
        return words(self.source)

    def figures(self, key: str) -> VersionFigures | None:
        """Return the figures of the version *key*, or ``None`` when it is not compared here."""
        return next((figures for figures in self.versions if figures.key == key), None)


def compare(
    source: str,
    versions: Sequence[Version],
    *,
    text_sha: str = "",
    marks: Sequence[OptionMark] = (),
) -> Comparison:
    """Return how *versions* differ on *source*, and what each of them cost.

    Args:
        source: The student's text, in plain text: what every answer starts from.
        versions: The answers, in the order the comparison lists them (one per model).
        text_sha: Hash of *source*: what the teacher's marks are filed under.
        marks: The teacher's verdicts on this text's options; marks of another text are ignored.

    Returns:
        The comparison: the change sites, the rows of the side-by-side view, and one set of
        figures per version.
    """
    tokens = tokenize(source)
    positions = word_positions(tokens)
    offsets = text_offsets(source)
    edits = [ordered_edits(source, version.text) for version in versions]
    runs = {version.key: (version.text, *version.repeats) for version in versions}
    found = [
        (version.key, run, edit)
        for version in versions
        for run, answer in enumerate(runs[version.key])
        for edit in ordered_edits(source, answer)
    ]
    aligned = {
        version.key: align(source, version.text, version.review)
        for version in versions
        if version.review is not None
    }
    notes = {version.key: _notes(version, aligned.get(version.key)) for version in versions}
    verdicts = verdicts_of(text_sha, marks)
    sites = _sites(
        source,
        found,
        positions,
        offsets,
        notes,
        text_sha,
        verdicts,
        {key: len(answers) for key, answers in runs.items()},
    )
    rows = sentence_rows(
        token_spans(source, sentence_spans(source)),
        [edit for one in edits for edit in one],
        len(tokens),
    )
    figures = tuple(
        _figures(source, version, aligned.get(version.key), found, positions, offsets, sites)
        for version in versions
    )
    return Comparison(
        source=source,
        versions=figures,
        sites=sites,
        rows=tuple(
            Row(
                start=row.start,
                end=row.end,
                source="".join(tokens[row.start : row.end]),
                versions=tuple(_row_text(tokens, made, row, len(tokens)) for made in edits),
                changed=tuple(_row_changed(made, row, len(tokens)) for made in edits),
            )
            for row in rows
        ),
    )


def _in_row(start: int, end: int, row: SentenceRow, total: int) -> bool:
    """Return whether the change ``[start, end)`` is shown in *row*, of a text of *total* tokens.

    An insertion stands between two tokens: it belongs to the row that starts there, or to the last
    row when it comes after the final token, so it is in exactly one row.
    """
    if start == end:
        return row.start <= start < row.end or start == row.end == total
    return end > row.start and start < row.end


def _row_changed(edits: Sequence[Edit], row: SentenceRow, total: int) -> bool:
    """Return whether a version with *edits* changed anything in *row*."""
    return any(_in_row(start, end, row, total) for start, end, _ in edits)


def _notes(version: Version, aligned: AlignedReview | None) -> tuple[Note, ...]:
    """Return the notes *version* wrote about its answer, as ``(span, category, explanation)``."""
    if aligned is None or version.failed:
        return ()
    return tuple(
        ((change.hunk.raw_start, change.hunk.raw_end), change.category, change.explanation)
        for change in aligned.changes
        if change.note is not None
    )


def _sites(
    source: str,
    found: Sequence[tuple[str, int, Edit]],
    positions: Sequence[int],
    offsets: Sequence[int],
    notes: dict[str, tuple[Note, ...]],
    text_sha: str,
    verdicts: Mapping[Identity, Verdict],
    runs: Mapping[str, int],
) -> tuple[ChangeSite, ...]:
    """Return the change sites of every run of every version, in text order.

    Two changes of one version or of two versions are one site when their word spans touch, and a
    version's runs count as one version here: ``runs`` says how many answers it gave, so a place
    only one of them changed is still one site, marked as unsure.  A change that merely stands
    beside another stays a site of its own.
    """
    placed = [
        (key, run, edit, (positions[edit[0]], positions[edit[1]])) for key, run, edit in found
    ]
    return tuple(
        _site(source, group, offsets, notes, text_sha, verdicts, runs)
        for group in _grouped(sorted(placed, key=lambda item: (item[3], item[2])))
    )


def _grouped(
    placed: Sequence[tuple[str, int, Edit, Span]],
) -> list[list[tuple[str, int, Edit, Span]]]:
    """Return the placed changes grouped where their word spans touch, in text order.

    A group grows while a change touches any change already in it, so three models that each fixed
    the same place differently are one group even when the outer two do not touch.
    """
    groups: list[list[tuple[str, int, Edit, Span]]] = []
    for item in placed:
        joining = [
            group for group in groups if any(spans_touch(item[3], other[3]) for other in group)
        ]
        merged = [other for group in joining for other in group]
        groups = [group for group in groups if group not in joining]
        groups.append([*merged, item])
    groups.sort(key=lambda group: (min(item[3] for item in group), max(item[3] for item in group)))
    return groups


def _site(
    source: str,
    group: Sequence[tuple[str, int, Edit, Span]],
    offsets: Sequence[int],
    notes: dict[str, tuple[Note, ...]],
    text_sha: str,
    verdicts: Mapping[Identity, Verdict],
    runs: Mapping[str, int],
) -> ChangeSite:
    """Return the site of one group of touching changes, with one option per distinct wording.

    An option is its own wording *and* the words it replaces: two versions that write the same
    replacement over different words changed different things, and the teacher can call one right
    and the other wrong.  A version that wrote the same wording in two of its runs made one option,
    and that option is unsure when one of its runs did not write it.
    """
    start = offsets[min(edit[0] for _, _, edit, _ in group)]
    end = offsets[max(edit[1] for _, _, edit, _ in group)]
    span: Span = (start, end)
    options: list[Option] = []
    writers: list[dict[str, set[int]]] = []
    for key, run, edit, _ in group:
        replacement = "".join(edit[2])
        made = Made(key=key, category="other", explanation="", first_run=run == 0)
        for note_span, category, explanation in notes.get(key, ()):
            if spans_touch(note_span, span):
                made = replace(made, category=category, explanation=explanation)
                break
        option_start, option_end = offsets[edit[0]], offsets[edit[1]]
        verdict = verdicts.get(identity(text_sha, option_start, option_end, replacement))
        for index, option in enumerate(options):
            if (option.start, option.end, option.replacement) == (
                option_start,
                option_end,
                replacement,
            ):
                if option.first_by(key):
                    made = replace(made, first_run=True)
                options[index] = Option(
                    option.start,
                    option.end,
                    option.replacement,
                    (*[one for one in option.made_by if one.key != key], made),
                    verdict,
                    option.once,
                )
                writers[index].setdefault(key, set()).add(run)
                break
        else:
            options.append(Option(option_start, option_end, replacement, (made,), verdict))
            writers.append({key: {run}})
    return ChangeSite(
        start=start,
        end=end,
        original=source[start:end],
        options=tuple(
            Option(
                option.start,
                option.end,
                option.replacement,
                option.made_by,
                option.verdict,
                any(len(seen) < runs.get(key, 1) for key, seen in writers[index].items()),
            )
            for index, option in enumerate(options)
        ),
    )


def _figures(
    source: str,
    version: Version,
    aligned: AlignedReview | None,
    found: Sequence[tuple[str, int, Edit]],
    positions: Sequence[int],
    offsets: Sequence[int],
    sites: Sequence[ChangeSite],
) -> VersionFigures:
    """Return what *version* did with *source*, counted against the other versions' changes.

    The counts are those of the version's first answer; ``runs``, ``in_every_run`` and ``once``
    say what its further answers came to.
    """
    mine = [
        (edit, (positions[edit[0]], positions[edit[1]]))
        for key, run, edit in found
        if key == version.key and run == 0
    ]
    others = [
        (edit, (positions[edit[0]], positions[edit[1]]))
        for key, _, edit in found
        if key != version.key
    ]
    answers = [version.text, *version.repeats]
    written = [
        {_option_identity(edit, offsets) for edit in ordered_edits(source, answer)}
        for answer in answers
    ]
    in_every_run = len(set.intersection(*written))
    made = len(set.union(*written))
    return VersionFigures(
        key=version.key,
        changes=len(mine),
        shared=sum(1 for edit, _ in mine if any(other == edit for other, _ in others)),
        alone=sum(
            1 for _, span in mine if not any(spans_touch(span, other) for _, other in others)
        ),
        explained=count_explained(source, version.text, version.review, aligned=aligned),
        unclear=version.review.unclear_count if version.review is not None else 0,
        marks_added=added_marks(source, version.text),
        loss=measure(source, version.text),
        left_out=version.left_out,
        failed=version.failed,
        fell_back=version.fell_back,
        seconds=version.seconds,
        words=words(source),
        marked=_marked(sites, version.key),
        runs=len(answers),
        in_every_run=in_every_run,
        once=made - in_every_run,
    )


def _marked(sites: Sequence[ChangeSite], key: str) -> MarkCount:
    """Return what the teacher's verdicts on the options of *sites* come to for the version *key*.

    The options a version made are those of its first answer, the answer its other figures count;
    a wording only a further run wrote is not counted for it.  A version that made no option at a
    place left it alone, so a right change it did not make is a change it missed — unless it wrote
    another right change of the same words, which is what two models fixing one error differently
    do.
    """
    right: list[Span] = []
    made: list[tuple[Span, Verdict | None]] = []
    for site in sites:
        for option in site.options:
            span: Span = (option.start, option.end)
            if option.verdict is Verdict.RIGHT:
                right.append(span)
            if option.first_by(key):
                made.append((span, option.verdict))
    taken = [span for span, verdict in made if verdict is Verdict.RIGHT]
    found = sum(1 for span in right if any(spans_touch(span, other) for other in taken))
    return MarkCount(
        right=len(taken),
        wrong=sum(1 for _span, verdict in made if verdict is Verdict.WRONG),
        unmarked=sum(1 for _span, verdict in made if verdict is None),
        found=found,
        missed=len(right) - found,
    )


def _option_identity(edit: Edit, offsets: Sequence[int]) -> tuple[int, int, str]:
    """Return what one change is: the characters it replaces and the wording it puts there."""
    return (offsets[edit[0]], offsets[edit[1]], "".join(edit[2]))


def _row_text(tokens: Sequence[str], edits: Sequence[Edit], row: SentenceRow, total: int) -> str:
    """Return how one version reads the row *row* of the student's text.

    The row's tokens are those of *tokens*; a change inside the row stands as its replacement.  A
    row is as wide as the widest change of every version, so no change is cut in two here.
    """
    pieces: list[str] = []
    cursor = row.start
    for start, end, replacement in edits:
        if not _in_row(start, end, row, total):
            continue
        if start > cursor:
            pieces.extend(tokens[cursor:start])
        pieces.extend(replacement)
        cursor = max(cursor, min(end, row.end))
    if cursor < row.end:
        pieces.extend(tokens[cursor : row.end])
    return "".join(pieces)
