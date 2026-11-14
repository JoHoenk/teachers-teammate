"""Hunks to edits: grouping, sentence groups, explain batches, note merging, overlap filter.

The word diff (:mod:`teachers_teammate.domain.proofreading.word_diff`) says where the corrected
text differs from the student's; this module says which of those differences a teacher sees as
*one* edit, and how the edits are handed to the explain step.

Two hunks become one edit when

* they are a **move** — a deleted run of words that reappears as an inserted run in the same
  sentence, whatever the distance (the German verb bracket: ``weil ich habe … gewartet`` becomes
  ``weil ich … gewartet habe``), or
* they are at most :data:`GROUP_GAP_WORDS` words apart in the same sentence (two mistakes side by
  side: ``ein grosse Hund`` becomes ``ein großer Hund``).

Hunks are never grouped across a sentence boundary; an edit that crosses one joins the sentences
into one group, which is what the explain step shows as context.

Pure: no I/O, no provider imports, no spaCy (the clause check of the optional comma is injected).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
import re

from .sentences import sentence_index, sentence_spans
from .word_diff import Hunk, similarity, word_diff

GROUP_GAP_WORDS = 2
"""Two hunks of one sentence with at most this many words between them are one edit.

A stand-in until the grouping rule is derived from saved answers: a word-order change is two hunks
(``ich bin`` -> ``bin ich`` inserts before and deletes after), and a verb bracket is two hunks a few
words apart.
"""

Side = str
"""Which text a span belongs to: ``"raw"`` (the student's) or ``"corrected"``."""

ClauseCheck = Callable[[str, str], bool]
"""Decides whether the text before and after a comma are two main clauses (spaCy, injected)."""

_WORD = re.compile(r"\w+", re.UNICODE)

_CONJUNCTIONS = frozenset({"und", "oder"})


@dataclass(frozen=True)
class EditPart:
    """One hunk of an edit: ``raw[raw_start:raw_end]`` becomes *corrected*."""

    raw_start: int
    raw_end: int
    """Half-open span in the raw text; ``raw_start == raw_end`` for an insertion."""
    original: str
    """The student's words, empty for an insertion."""
    corrected: str
    """The corrected words, empty for a deletion."""


@dataclass(frozen=True)
class Edit:
    """One edit of the correction: the hunks a teacher reads as a single change."""

    id: int
    """Number of the edit, 1..k in text order over the whole text."""
    parts: tuple[EditPart, ...]
    """The hunks of the edit, in text order and disjoint."""
    sentences: tuple[int, ...]
    """Indices of the source sentences the edit touches (context only)."""

    @property
    def raw_start(self) -> int:
        """Offset of the first character the edit changes."""
        return self.parts[0].raw_start

    @property
    def raw_end(self) -> int:
        """Offset after the last character the edit changes."""
        return max(part.raw_end for part in self.parts)

    @property
    def original(self) -> str:
        """The student's words of every part, in order."""
        return " ".join(part.original for part in self.parts if part.original)

    @property
    def corrected(self) -> str:
        """The corrected words of every part, in order."""
        return " ".join(part.corrected for part in self.parts if part.corrected)


@dataclass(frozen=True)
class Batch:
    """One explain request: the edits of one or more sentence groups."""

    groups: tuple[tuple[int, ...], ...]
    """The sentence groups the batch shows as context, in text order."""
    edits: tuple[Edit, ...]
    """The edits the batch asks about, in text order."""
    whole_group: bool = True
    """``False`` when a single group held more edits than a batch may carry and was cut."""


@dataclass(frozen=True)
class NoteAnswer:
    """What the explain step said about one edit."""

    id: int
    category: str
    explanation: str


@dataclass(frozen=True)
class MergedNotes:
    """The notes of one explain answer after merging them by id."""

    notes: tuple[NoteAnswer, ...]
    """One note per known id, in id order."""
    unknown: int = 0
    """Notes for ids the batch did not ask about (dropped)."""
    duplicates: int = 0
    """Notes that repeated an id already answered (merged into the first)."""
    missing: tuple[int, ...] = ()
    """Ids the answer left out; they are asked for once more."""


def edits(source: str, corrected: str) -> tuple[Edit, ...]:
    """Return the edits of *corrected* against *source*, ids 1..k in text order."""
    return edits_from_hunks(word_diff(source, corrected), source, sentence_spans(source))


def edits_from_hunks(
    hunks: Sequence[Hunk], source: str, spans: tuple[tuple[int, int], ...]
) -> tuple[Edit, ...]:
    """Return the edits of *hunks*, grouped by the move and gap rules, ids in text order."""
    return tuple(
        Edit(
            id=number,
            parts=tuple(_part(hunk) for hunk in group),
            sentences=_touched(group, spans),
        )
        for number, group in enumerate(_group(hunks, source, spans), 1)
    )


def _part(hunk: Hunk) -> EditPart:
    """Return *hunk* as an :class:`EditPart`."""
    return EditPart(
        raw_start=hunk.raw_start,
        raw_end=hunk.raw_end,
        original=hunk.original,
        corrected=hunk.corrected,
    )


def _group(
    hunks: Sequence[Hunk], source: str, spans: tuple[tuple[int, int], ...]
) -> list[list[Hunk]]:
    """Return *hunks* grouped into edits: moves first, then neighbours a few words apart."""
    return _merge_by_gap(_move_units(hunks, spans), source, spans)


def _move_units(hunks: Sequence[Hunk], spans: tuple[tuple[int, int], ...]) -> list[list[Hunk]]:
    """Return the hunks as units, a deleted run and its reinsertion forming one unit."""
    units: list[list[Hunk]] = []
    paired: set[int] = set()
    for index, hunk in enumerate(hunks):
        if index in paired:
            continue
        partner = _move_partner(hunks, index, spans, paired)
        if partner < 0:
            units.append([hunk])
            continue
        paired.update((index, partner))
        units.append(sorted((hunk, hunks[partner]), key=lambda found: found.raw_start))
    return units


def _move_partner(
    hunks: Sequence[Hunk], index: int, spans: tuple[tuple[int, int], ...], paired: Collection[int]
) -> int:
    """Return the index of the hunk that moves the same words as ``hunks[index]``, or ``-1``.

    A move is a deletion and an insertion of the same words (case and punctuation ignored) in the
    same sentence.  A replacement is never half of a move: it changes words where they stand.
    """
    hunk = hunks[index]
    if hunk.kind == "replace":
        return -1
    mine = _normal(hunk.original or hunk.corrected)
    if not mine:
        return -1
    sentence = sentence_index(spans, hunk.raw_start)
    for other_index in range(index + 1, len(hunks)):
        if other_index in paired:
            continue
        other = hunks[other_index]
        if other.kind in (hunk.kind, "replace"):
            continue
        if _normal(other.corrected or other.original) != mine:
            continue
        if sentence_index(spans, other.raw_start) != sentence:
            continue
        return other_index
    return -1


def _merge_by_gap(
    units: Sequence[Sequence[Hunk]], source: str, spans: tuple[tuple[int, int], ...]
) -> list[list[Hunk]]:
    """Return *units* merged into the nearest group that lies in one sentence and close enough.

    A move's two halves can lie far apart with other hunks between them, so a unit is compared
    with every hunk of a group, not only the last one: a hunk between the halves joins the move
    only when it is itself within :data:`GROUP_GAP_WORDS` words of one of them.
    """
    merged: list[list[Hunk]] = []
    for unit in units:
        home = next(
            (
                group
                for group in reversed(merged)
                if any(_joins(held, new, source, spans) for held in group for new in unit)
            ),
            None,
        )
        if home is None:
            merged.append(list(unit))
            continue
        home.extend(unit)
        home.sort(key=lambda hunk: (hunk.raw_start, hunk.raw_end))
    return merged


def _joins(a: Hunk, b: Hunk, source: str, spans: tuple[tuple[int, int], ...]) -> bool:
    """Whether two hunks are close enough to be one edit.

    Two hunks of different sentences are one edit only when the first of them reaches the end of
    its sentence: the model then changed the boundary itself (it joined two sentences), and the
    change of the first word of the next sentence belongs to that edit.
    """
    last, first = (a, b) if (a.raw_start, a.raw_end) <= (b.raw_start, b.raw_end) else (b, a)
    sentence = sentence_index(spans, last.raw_start)
    if sentence_index(spans, first.raw_start) != sentence:
        if sentence < 0 or last.raw_end < spans[sentence][1]:
            return False
    return _words_between(source, last.raw_end, first.raw_start) <= GROUP_GAP_WORDS


def _words_between(text: str, start: int, end: int) -> int:
    """Return how many words *text* has between two offsets."""
    return len(_WORD.findall(text[start:end]))


def _normal(text: str) -> str:
    """Return *text* as lower-cased words only, for comparing what a move carries."""
    return " ".join(_WORD.findall(text.casefold()))


def _touched(group: Sequence[Hunk], spans: tuple[tuple[int, int], ...]) -> tuple[int, ...]:
    """Return the indices of the sentences *group* touches, in order and without a gap.

    A hunk that runs across a whole sentence touches it too, and a sentence between two sentences
    of one edit belongs to it, so the sentence groups stay runs of neighbours.
    """
    indices = {sentence_index(spans, hunk.raw_start) for hunk in group}
    indices |= {sentence_index(spans, max(hunk.raw_start, hunk.raw_end - 1)) for hunk in group}
    found = [index for index in indices if index >= 0]
    return tuple(range(min(found), max(found) + 1)) if found else ()


def sentence_groups(
    spans: tuple[tuple[int, int], ...], found: Sequence[Edit]
) -> tuple[tuple[int, ...], ...]:
    """Return the sentences of *spans* grouped: an edit that crosses a boundary joins them.

    Without a crossing edit every sentence is its own group, in text order.
    """
    parent = list(range(len(spans)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for edit in found:
        for other in edit.sentences[1:]:
            first, second = root(edit.sentences[0]), root(other)
            if first != second:
                parent[max(first, second)] = min(first, second)
    groups: dict[int, list[int]] = {}
    for index in range(len(spans)):
        groups.setdefault(root(index), []).append(index)
    return tuple(tuple(members) for members in groups.values())


def batches(
    found: Sequence[Edit], groups: Sequence[tuple[int, ...]], limit: int
) -> tuple[Batch, ...]:
    """Return the explain batches: whole sentence groups, at most *limit* edits each.

    A single group with more edits than *limit* is cut at edit boundaries; every part repeats the
    whole group as context and is marked as not whole (:attr:`Batch.whole_group`).
    """
    if limit < 1:
        raise ValueError("a batch holds at least one edit")
    by_group = _edits_by_group(found, groups)
    out: list[Batch] = []
    current: list[Edit] = []
    current_groups: list[tuple[int, ...]] = []
    for index, group in enumerate(groups):
        mine = by_group.get(index, [])
        if not mine:
            continue
        if len(mine) > limit:
            if current:
                out.append(Batch(tuple(current_groups), tuple(current)))
                current, current_groups = [], []
            for start in range(0, len(mine), limit):
                out.append(Batch((group,), tuple(mine[start : start + limit]), whole_group=False))
            continue
        if current and len(current) + len(mine) > limit:
            out.append(Batch(tuple(current_groups), tuple(current)))
            current, current_groups = [], []
        current.extend(mine)
        current_groups.append(group)
    if current:
        out.append(Batch(tuple(current_groups), tuple(current)))
    return tuple(out)


def _edits_by_group(
    found: Sequence[Edit], groups: Sequence[tuple[int, ...]]
) -> dict[int, list[Edit]]:
    """Return the edits of every group, keyed by the group's index."""
    where = {sentence: index for index, group in enumerate(groups) for sentence in group}
    by_group: dict[int, list[Edit]] = {}
    for edit in found:
        index = where.get(edit.sentences[0]) if edit.sentences else None
        if index is not None:
            by_group.setdefault(index, []).append(edit)
    return by_group


def merge_notes(answers: Sequence[NoteAnswer], known: Collection[int]) -> MergedNotes:
    """Merge the notes of one explain answer by id.

    A note for an id the batch did not ask about is dropped and counted; a second note for an id
    already answered is merged into the first and counted; an id without a note is reported as
    missing, so the caller can ask for it once more.
    """
    wanted = set(known)
    merged: dict[int, NoteAnswer] = {}
    unknown = duplicates = 0
    for answer in answers:
        if answer.id not in wanted:
            unknown += 1
            continue
        if answer.id in merged:
            duplicates += 1
            merged[answer.id] = _join(merged[answer.id], answer)
            continue
        merged[answer.id] = answer
    missing = tuple(number for number in sorted(wanted) if number not in merged)
    return MergedNotes(
        notes=tuple(merged[number] for number in sorted(merged)),
        unknown=unknown,
        duplicates=duplicates,
        missing=missing,
    )


def _join(first: NoteAnswer, second: NoteAnswer) -> NoteAnswer:
    """Return two notes for one id as one: the first category, both explanations."""
    category = first.category
    if not category or category == "other":
        category = second.category
    explanation = first.explanation
    if second.explanation and second.explanation not in explanation:
        explanation = f"{explanation} {second.explanation}".strip()
    return NoteAnswer(id=first.id, category=category, explanation=explanation)


def touches_edit(span: tuple[int, int], hunks: Sequence[Hunk], *, side: Side = "corrected") -> bool:
    """Whether *span* shares words with an edit (an insertion counts as inside it)."""
    start, end = span
    for hunk in hunks:
        low, high = _span_of(hunk, side)
        if low == high:
            if start < low < end:
                return True
        elif max(start, low) < min(end, high):
            return True
    return False


def _span_of(hunk: Hunk, side: Side) -> tuple[int, int]:
    """Return the span of *hunk* on *side* of the diff."""
    if side == "raw":
        return hunk.raw_start, hunk.raw_end
    return hunk.corrected_start, hunk.corrected_end


def drop_overlapping[T](
    notes: Sequence[T],
    span_of: Callable[[T], tuple[int, int]],
    hunks: Sequence[Hunk],
    *,
    side: Side = "corrected",
) -> tuple[tuple[T, ...], int]:
    """Return *notes* without the ones that touch an edit, and how many were dropped.

    A flag on words the correction changed is not a separate observation: the edit already says
    what is wrong there, and two notes on one span would contradict each other.
    """
    kept = tuple(note for note in notes if not touches_edit(span_of(note), hunks, side=side))
    return kept, len(notes) - len(kept)


def optional_comma(edit: Edit, source: str, clause_check: ClauseCheck | None) -> bool:
    """Whether *edit* only inserts a comma before "und" / "oder" between two main clauses.

    Such a comma is a matter of style, not a mistake: it is shown as optional and the evaluation
    tools do not count it as a false alarm.  Without a clause check (no spaCy model of the text
    language) the rule is off and the comma is a normal edit.
    """
    if clause_check is None or len(edit.parts) != 1:
        return False
    part = edit.parts[0]
    if part.original.strip() or part.corrected.strip() != ",":
        return False
    after = source[part.raw_end :]
    word = _WORD.match(after.lstrip())
    if word is None or word.group().casefold() not in _CONJUNCTIONS:
        return False
    return clause_check(source[: part.raw_start], after)


def group_after(
    corrected: str,
    spans: tuple[tuple[int, int], ...],
    group: Sequence[int],
    hunks: Sequence[Hunk],
    mark: Callable[[Hunk], str] | None = None,
) -> str:
    """Return the sentences of *group* as *corrected* has them.

    The text is cut from the corrected text itself, so the spacing is the model's and a hunk that
    reaches into the next sentence (a joined sentence) never doubles or loses a word.  The group
    is a run of neighbouring sentences (:func:`sentence_groups`), so every hunk that starts in it
    ends in it.  *mark* returns the text written in front of a hunk's replacement — the explain
    step numbers its edits there.
    """
    first, last = group[0], group[-1]
    before = [hunk for hunk in hunks if sentence_index(spans, hunk.raw_start) < first]
    mine = [hunk for hunk in hunks if first <= sentence_index(spans, hunk.raw_start) <= last]
    ahead = before[-1].offset_after if before else 0
    low = spans[first][0] + ahead
    high = spans[last][1] + (mine[-1].offset_after if mine else ahead)
    if mine:
        low = min(low, mine[0].corrected_start)
        high = max(high, mine[-1].corrected_end)
    pieces: list[str] = []
    position = low
    for hunk in mine:
        start = max(hunk.corrected_start, position)
        pieces += [corrected[position:start], mark(hunk) if mark else ""]
        position = start
    pieces.append(corrected[position:high])
    return "".join(pieces)


FRAMING_WORDS = 3
"""Net words a model may add at the very start or end of the text before it counts as framing.

A correction adds a word or two inside the text; three or more new words before the first or after
the last original word are a lead-in ("Here is the corrected text:") or a remark ("I fixed 10
mistakes.").
"""


_SAME_LINE = 0.7
"""A first line this similar to the source's first line, bar a colon, is that line."""


def framing_problem(source: str, corrected: str) -> str | None:
    """Return why *corrected* is *source* with something written around it, else ``None``.

    Step ① must answer the corrected text and nothing else.  The window check does not see a short
    lead-in, so it is looked for here, on plain text: a first line that ends in a colon where the
    source's does not, or :data:`FRAMING_WORDS` or more new words before the first (or after the
    last) word of the source.
    """
    source, corrected = source.strip(), corrected.strip()
    first = next((line for line in corrected.splitlines() if line.strip()), "")
    source_first = next((line for line in source.splitlines() if line.strip()), "")
    if first.rstrip().endswith(":") and not source_first.rstrip().endswith(":"):
        # A colon the model added to the text's own first line is a correction, not a lead-in.
        own = similarity(first.rstrip().removesuffix(":").rstrip(), source_first.rstrip())
        if own < _SAME_LINE:
            return "the answer starts with a lead-in line, not with the text"
    found = word_diff(source, corrected)
    if not found:
        return None
    head, tail = found[0], found[-1]
    if head.raw_start == 0 and head.corrected_start == 0 and _added_words(head) >= FRAMING_WORDS:
        return "the answer starts with words that are not in the text (a lead-in)"
    if (
        tail.raw_end == len(source)
        and tail.corrected_end == len(corrected)
        and _added_words(tail) >= FRAMING_WORDS
    ):
        return "the answer ends with words that are not in the text (a remark)"
    return None


def _added_words(hunk: Hunk) -> int:
    """Return how many more words *hunk* has in the corrected text than in the source."""
    return len(_WORD.findall(hunk.corrected)) - len(_WORD.findall(hunk.original))
