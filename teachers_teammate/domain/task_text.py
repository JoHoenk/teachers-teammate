"""Task text: the printed lines of a worksheet, and how proofreading leaves them alone.

Worksheets and exams carry printed text next to the handwriting: header, task, form labels, point
boxes.  Text recognition reads all of it, and a proofreading that "corrects" it changes words the
student never wrote.  The rule is therefore: only correct what the student wrote.

The batch finds the passages (:func:`detect_task_text`): printed text repeats in every document of
one worksheet, nearly identical, in the same place and order, while a student's text does not.
The marked lines are not sent and come back unchanged (:func:`locate`); a "not task" entry of the
teacher takes a line back.

Pure: no I/O, no model, no library.  The caller converts a stored Markdown text to its plain text
and carries the positions back.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import math
import re
from typing import Literal

from .formatted_text import PAGE_LABEL

Kind = Literal["student", "task"]
"""What a segment of a text is: the student's own words, or printed text left alone."""

SIMILARITY = 0.9
"""How alike two normalised lines must be to be the same printed line."""

MATCH_SIMILARITY = 0.85
"""How alike a passage and a stretch of text must be to be found: a reading misreads a word."""

SHARE = 0.6
"""The part of a worksheet's documents that must carry a line for it to be printed text."""

MIN_DOCUMENTS = 3
"""Below this many documents nothing is detected: one copy does not repeat."""

MIN_WORDS = 3
"""A line of fewer words counts only next to task lines (a point box, a lone "Yes.")."""

MIN_SHARED_HEADER = 2
"""How many header lines two documents must share before one more line may differ."""

RARE_WORD_FORMS = 50
"""A word found in more forms than this is too common to pick comparison candidates by."""

CANDIDATES_PER_FORM = 5
"""How many forms of the word index one unmatched line is compared with: a printed line misread
in a word or two still shares most of its words, so it stands at the top of the queue."""

WORD_OVERLAP = 0.5
"""How many words two lines must share before their similarity is measured: the ratio is slow on
a long student line, and no printed line shares few words with it."""

Similarity = Callable[[str, str], float]
"""Returns how alike two normalised texts are, from 0 to 1."""


@dataclass(frozen=True)
class Segment:
    """One stretch of a plain text, and whether it is the student's or printed text."""

    start: int
    end: int
    kind: Kind


@dataclass(frozen=True)
class FoundPassage:
    """A passage as it was really found in one text, with the place it stands at."""

    passage: str
    """The passage as it is stored with the document."""
    start: int
    end: int
    text: str
    """The text of the passage as the document has it, not as the passage wrote it."""


@dataclass(frozen=True)
class Line:
    """One line of a text: its text, its range in the plain text, and its place on the page."""

    text: str
    start: int
    end: int
    page: int
    order: int
    norm: str

    @property
    def words(self) -> int:
        """Return how many words the normalised line has."""
        return len(self.norm.split())


def locate(
    plain: str,
    passages: Sequence[str],
    not_task: Sequence[str] = (),
    *,
    similarity: Similarity | None = None,
) -> tuple[Segment, ...]:
    """Return the segments of *plain*, covering it without gaps.

    A passage that is not found is ignored, so the text is proofread as it is today.  A "not task"
    entry wins over a passage, because the teacher's decision beats the marking it undoes.
    """
    lines = lines_of(plain, _whole_lines(passages, not_task))
    return segments_of(plain, lines, task_lines_of(lines, passages, not_task, similarity))


def found_passages(
    plain: str,
    passages: Sequence[str],
    not_task: Sequence[str] = (),
    *,
    similarity: Similarity | None = None,
) -> tuple[FoundPassage, ...]:
    """Return the passages that *plain* really holds, in document order.

    What is found is what enters the fingerprint of a stored proofreading, so a passage the text
    no longer holds counts for nothing.
    """
    if not passages:
        return ()
    lines = lines_of(plain, _whole_lines(passages, not_task))
    found: list[FoundPassage] = []
    for passage in passages:
        window = _window_of(lines, _wanted_lines(passage), similarity)
        if window is None:
            continue
        first, width = window
        start, end = lines[first].start, lines[first + width - 1].end
        if _overlaps_entry(lines, start, end, not_task, similarity):
            continue
        found.append(FoundPassage(passage, start, end, plain[start:end]))
    return tuple(sorted(found, key=lambda item: item.start))


def passage_parts(
    plain: str,
    passages: Sequence[str],
    not_task: Sequence[str] = (),
    *,
    similarity: Similarity | None = None,
) -> tuple[str, ...]:
    """Return the hash parts of the passages *plain* holds: their text as *plain* has it."""
    return tuple(
        found.text for found in found_passages(plain, passages, not_task, similarity=similarity)
    )


def new_passages(
    plain: str,
    existing: Sequence[str],
    candidates: Sequence[str],
    not_task: Sequence[str] = (),
    *,
    similarity: Similarity | None = None,
) -> tuple[str, ...]:
    """Return the *candidates* that mark a line of *plain* that *existing* does not mark yet.

    A detection over a changed queue finds a passage again with a line more or less; only one
    that covers a new line is worth keeping, so the marks do not pile up and a document's
    proofreading key changes only when its task text does.
    """
    ratio = similarity or _ratio
    lines = lines_of(plain, _whole_lines([*existing, *candidates], not_task))
    covered = _window_lines(lines, existing, ratio)
    fresh: list[str] = []
    for text in candidates:
        lines_found = _window_lines(lines, [text], ratio)
        if lines_found - covered:
            fresh.append(text)
            covered |= lines_found
    return tuple(fresh)


def lines_of(plain: str, whole: Collection[str] = ()) -> tuple[Line, ...]:
    """Return the lines of *plain* that carry text, with their range and their page.

    A ``Page N:`` label starts a new page and is no line itself; a blank line is no line either.
    A label with a fill-in ("Name: Anna M.") splits: the label is the line, and the fill-in after
    it is left out, because what a student wrote there is never printed text.  A line whose
    normalised text is in *whole* stays whole: the batch saw it printed, fill-in and all, or a
    passage holds it so.
    """
    lines: list[Line] = []
    page = 0
    labelled = False
    order = 0
    at = 0
    for raw in plain.split("\n"):
        start = at
        at += len(raw) + 1
        if not raw.strip():
            continue
        if PAGE_LABEL.fullmatch(raw.strip()):
            if labelled:
                page += 1
            labelled = True
            order = 0
            continue
        if whole and normalise(raw) in whole:
            text, end = raw, start + len(raw)
        else:
            text, end = _label_without_fill_in(raw, start)
        lines.append(Line(text, start, end, page, order, normalise(text)))
        order += 1
    return tuple(lines)


def normalise(text: str) -> str:
    """Return *text* as lines are compared: folded, without markers, numbering and blanks."""
    folded = _LIST_MARKER.sub("", text.casefold())
    folded = _BLANKS.sub(" ", folded)
    folded = " ".join(folded.split())
    return folded.rstrip(_END_PUNCTUATION)


def segments_of(plain: str, lines: Sequence[Line], task: Iterable[int]) -> tuple[Segment, ...]:
    """Return the segments of *plain*, with the lines at the indices of *task* left alone.

    The segments follow each other without a gap.  A printed segment is exactly its lines: what
    stands between two of them belongs to the student, since that is a label's fill-in, and so does
    the whitespace at the edges.
    """
    if not plain:
        return ()
    if not lines:
        return (Segment(0, len(plain), "student"),)
    marked = set(task)
    spans: list[tuple[int, int, Kind]] = []
    for index, line in enumerate(lines):
        kind: Kind = "task" if index in marked else "student"
        spans.append((line.start, line.end, kind))
        if index + 1 < len(lines) and plain[line.end : lines[index + 1].start].strip():
            # What a label carries behind it is the student's, never the printed line's.
            spans.append((line.end, lines[index + 1].start, "student"))
    segments: list[Segment] = []
    for start, end, kind in spans:
        if not segments:
            segments.append(Segment(0, end, kind))
        elif segments[-1].kind == kind:
            segments[-1] = Segment(segments[-1].start, end, kind)
        else:
            boundary = segments[-1].end if segments[-1].kind == "task" else start
            segments[-1] = Segment(segments[-1].start, boundary, segments[-1].kind)
            segments.append(Segment(boundary, end, kind))
    if segments[-1].kind == "task":
        if segments[-1].end < len(plain):
            segments.append(Segment(segments[-1].end, len(plain), "student"))
    else:
        segments[-1] = Segment(segments[-1].start, len(plain), segments[-1].kind)
    return tuple(segments)


def task_lines_of(
    lines: Sequence[Line],
    passages: Sequence[str],
    not_task: Sequence[str] = (),
    similarity: Similarity | None = None,
) -> set[int]:
    """Return the indices of the lines that are printed text, and no longer the student's."""
    ratio = similarity or _ratio
    found = _window_lines(lines, passages, ratio)
    for entry in not_task:
        found -= _window_lines(lines, [entry], ratio)
    return found


@dataclass(frozen=True)
class TaskDocument:
    """What the detection reads of one document: its plain text, its marks and the teacher's
    "not task" entries."""

    name: str
    plain: str
    marks: tuple[str, ...] = ()
    not_task: tuple[str, ...] = ()


@dataclass(frozen=True)
class Worksheet:
    """One worksheet of a queue: the names of its documents."""

    documents: tuple[str, ...] = ()


@dataclass(frozen=True)
class Detection:
    """What the batch found: the printed passages per document, and the worksheets."""

    passages: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    worksheets: tuple[Worksheet, ...] = ()

    @property
    def documents_with_task_text(self) -> int:
        """Return how many documents have printed text found."""
        return len(self.passages)


@dataclass(frozen=True)
class TaskTextFound:
    """What one detection found for a run: the documents it read, and the print it found."""

    read: int = 0
    """The documents with a transcription the detection could read."""
    documents: int = 0
    """Of those, the ones it found printed text in."""
    worksheets: int = 0
    """How many worksheets it told apart."""


def detect_task_text(
    documents: Sequence[TaskDocument], *, similarity: Similarity | None = None
) -> Detection:
    """Return the printed text of a queue, document by document, and its worksheets.

    Printed text repeats in every document of one worksheet: nearly identical, in the same place
    and in the same order, while a student's own writing does not.  A queue may hold several
    worksheets; the run of repeated lines at the top of the first page is what tells them apart,
    so two classes with one school header and two different tasks are two groups.

    Below :data:`MIN_DOCUMENTS` documents nothing is found: one copy does not repeat.  A passage
    a document already holds is not the detection's to drop; the caller adds what is found and
    keeps what is there.
    """
    ratio = similarity or _ratio
    docs = [document for document in documents if document.plain.strip()]
    if len(docs) < MIN_DOCUMENTS:
        return Detection()
    whole = _repeated_lines(document.plain for document in docs)
    lines = [lines_of(document.plain, whole) for document in docs]
    families, family_at = _families(lines, ratio)
    printed = [
        {index: family for index, family in family_at[doc].items() if _counted(families, family)}
        for doc in range(len(docs))
    ]
    # The teacher's "not task" entries hold for their own document only.
    excluded = [_families_after_all(document.not_task, families, ratio) for document in docs]
    headers = [_header_of(doc_lines, printed[index]) for index, doc_lines in enumerate(lines)]
    found: dict[str, tuple[str, ...]] = {}
    worksheets: list[Worksheet] = []
    for head, members in _worksheets(headers):
        tasks = _task_families(members, printed)
        for index in members:
            marked = _marked(
                lines[index], head, printed[index], families, tasks, excluded[index], ratio
            )
            texts = _passage_texts(docs[index].plain, lines[index], marked)
            if texts:
                found[docs[index].name] = texts
        if head:
            worksheets.append(Worksheet(tuple(docs[index].name for index in members)))
    return Detection(passages=found, worksheets=tuple(worksheets))


# ── The batch's families ───────────────────────────────────────────────────


@dataclass
class _Family:
    """One printed line, as every document of a worksheet carries it."""

    forms: Counter[str] = field(default_factory=Counter)
    """How often each normalised form was read."""

    lines: dict[int, int] = field(default_factory=dict)
    """Document index -> the line index of its first occurrence."""

    @property
    def consensus(self) -> str:
        """Return the form most documents carry: how the line is printed."""
        return self.forms.most_common(1)[0][0] if self.forms else ""


def _families(
    lines: Sequence[Sequence[Line]], similarity: Similarity
) -> tuple[list[_Family], list[dict[int, int]]]:
    """Return the families of the queue's lines, and which family each document's lines hold.

    Exact forms are grouped first, because most printed lines read identically; only the rest are
    compared fuzzily, and only with forms that share a rare word with them.  Two lines of one
    document never belong to one family: a sentence repeated inside an essay is not printed.
    """
    exact: dict[str, list[int]] = {}
    for doc, doc_lines in enumerate(lines):
        for line in doc_lines:
            exact.setdefault(line.norm, []).append(doc)
    families: list[_Family] = []
    owner: dict[str, int] = {}
    index = _WordIndex()
    for form in sorted(exact, key=lambda form: (-len(set(exact[form])), form)):
        family = index.nearest(form, families, similarity)
        if family is None:
            families.append(_Family())
            family = len(families) - 1
        index.add(form, family)
        owner[form] = family
        families[family].forms[form] = len(exact[form])
    family_at: list[dict[int, int]] = [{} for _ in lines]
    for doc, doc_lines in enumerate(lines):
        for line_index, line in enumerate(doc_lines):
            family = owner[line.norm]
            if doc in families[family].lines:
                continue
            families[family].lines[doc] = line_index
            family_at[doc][line_index] = family
    return families, family_at


def _counted(families: Sequence[_Family], family: int) -> bool:
    """Whether a family repeats in enough documents to be printed text."""
    return len(families[family].lines) >= MIN_DOCUMENTS


class _WordIndex:
    """The rare words of the forms seen so far, to find the forms worth comparing with a line."""

    def __init__(self) -> None:
        self._family_of: dict[str, int] = {}
        self._words: dict[str, list[str]] = {}

    def add(self, form: str, family: int) -> None:
        """Remember *form* as a member of *family*."""
        self._family_of[form] = family
        for word in set(form.split()):
            self._words.setdefault(word, []).append(form)

    def nearest(self, form: str, families: Sequence[_Family], similarity: Similarity) -> int | None:
        """Return the family *form* belongs to, or ``None`` when it belongs to none.

        Only forms that share a rare word come into question, and only the families of the most
        similar of them are kept.
        """
        shared: Counter[int] = Counter()
        for word in set(form.split()):
            forms = self._words.get(word, [])
            if len(forms) > RARE_WORD_FORMS:
                continue
            for other in forms:
                shared[self._family_of[other]] += 1
        best: tuple[float, int] | None = None
        for family, _count in shared.most_common(CANDIDATES_PER_FORM):
            printed_line = families[family].consensus
            if _overlap(form, printed_line) < WORD_OVERLAP:
                continue
            if not _worth_measuring(form, printed_line):
                continue
            score = similarity(form, printed_line)
            if score >= SIMILARITY and (best is None or score > best[0]):
                best = (score, family)
        return best[1] if best is not None else None


def _repeated_lines(plains: Iterable[str]) -> frozenset[str]:
    """Return the normalised lines that :data:`MIN_DOCUMENTS` documents or more carry whole.

    A label whose fill-in reads the same everywhere ("Thema: Die Umwelt") is printed, fill-in
    and all, and stays one line.
    """
    counts: Counter[str] = Counter()
    for plain in plains:
        counts.update({normalise(raw) for raw in plain.split("\n") if raw.strip()})
    return frozenset(line for line, count in counts.items() if count >= MIN_DOCUMENTS)


def _families_after_all(
    entries: Sequence[str], families: Sequence[_Family], similarity: Similarity
) -> set[int]:
    """Return the families the teacher said to proofread after all, in one document."""
    if not entries:
        return set()
    wanted = [normalise(text) for text in entries]
    return {
        family
        for family, printed in enumerate(families)
        if any(
            _overlap(printed.consensus, text) >= WORD_OVERLAP
            and _worth_measuring(printed.consensus, text)
            and similarity(printed.consensus, text) >= SIMILARITY
            for text in wanted
        )
    }


# ── Worksheets ─────────────────────────────────────────────────────────────


def _header_of(lines: Sequence[Line], printed: Mapping[int, int]) -> tuple[int, ...]:
    """Return the families of the run of printed lines at the top of the first page.

    A page carries a header, then the student's answers; the run ends at the first line that is
    no printed line.  A line the scan read badly is no printed line here, so the run may be one
    line shorter than the worksheet's; two groups compare with that much difference allowed.
    """
    head: list[int] = []
    for index, line in enumerate(lines):
        family = printed.get(index)
        if line.page > 0 or family is None:
            break
        head.append(family)
    return tuple(head)


def _worksheets(
    headers: Sequence[tuple[int, ...]],
) -> tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]:
    """Return the worksheet groups as ``(header, document indices)``.

    Two documents belong to one worksheet when their first page carries the same run of printed
    lines; one line of difference is allowed, because a scan misreads one.  The documents whose
    first page carries no printed line form one group of their own, so a queue whose print does
    not start at the top is still served.
    """
    groups: list[tuple[tuple[int, ...], list[int]]] = []
    ungrouped: list[int] = []
    for index, head in enumerate(headers):
        if not head:
            ungrouped.append(index)
            continue
        for beside, members in groups:
            if _same_header(head, beside):
                members.append(index)
                break
        else:
            groups.append((head, [index]))
    found = [(head, tuple(members)) for head, members in groups]
    if len(ungrouped) >= MIN_DOCUMENTS:
        found.append(((), tuple(ungrouped)))
    return tuple(found)


def _same_header(one: tuple[int, ...], other: tuple[int, ...]) -> bool:
    """Whether two headers are the same run of printed lines, one line of difference allowed.

    The difference is allowed only when :data:`MIN_SHARED_HEADER` lines or more are the same:
    a one-line header that differs is another worksheet, not a misread line.
    """
    if one == other:
        return True
    if min(len(one), len(other)) < MIN_SHARED_HEADER + (len(one) == len(other)):
        return False
    if len(one) == len(other):
        return sum(1 for left, right in zip(one, other, strict=True) if left != right) == 1
    shorter, longer = (one, other) if len(one) < len(other) else (other, one)
    if len(longer) - len(shorter) != 1:
        return False
    return any(longer[:drop] + longer[drop + 1 :] == shorter for drop in range(len(longer)))


# ── Task text inside one worksheet ──────────────────────────────────────────


def _task_families(members: Sequence[int], printed: Sequence[Mapping[int, int]]) -> tuple[int, ...]:
    """Return the families that are printed text inside one worksheet, in reading order.

    A family counts when at least :data:`SHARE` of the worksheet's documents carry it, and when
    it keeps the order the others stand in: Aufgabe 1 before Aufgabe 2.
    """
    places: dict[int, dict[int, int]] = {}
    for doc in members:
        for line_index, family in printed[doc].items():
            places.setdefault(family, {})[doc] = line_index
    needed = max(MIN_DOCUMENTS, math.ceil(SHARE * len(members)))
    chosen: list[int] = []
    for family in sorted(
        (family for family, docs in places.items() if len(docs) >= needed),
        key=lambda family: _median(places[family]),
    ):
        if not _keeps_order(family, chosen, places):
            continue
        chosen.append(family)
    return tuple(chosen)


def _median(places: Mapping[int, int]) -> float:
    """Return the middle place of a family over the documents that carry it."""
    values = sorted(places.values())
    middle = len(values) // 2
    if len(values) % 2:
        return float(values[middle])
    return (values[middle - 1] + values[middle]) / 2


def _keeps_order(
    family: int, chosen: Sequence[int], places: Mapping[int, Mapping[int, int]]
) -> bool:
    """Whether *family* comes after the chosen families in most documents that carry both."""
    positions = places.get(family, {})
    if not chosen or not positions:
        return True
    breaks = sum(
        1
        for doc, position in positions.items()
        if any(position < places[other][doc] for other in chosen if doc in places.get(other, {}))
    )
    return breaks <= (1 - SHARE) * len(positions)


def _marked(
    lines: Sequence[Line],
    head: Sequence[int],
    printed: Mapping[int, int],
    families: Sequence[_Family],
    tasks: Sequence[int],
    excluded: set[int],
    similarity: Similarity,
) -> set[int]:
    """Return the indices of the lines of one document that are printed text."""
    wanted = set(tasks) | set(head)
    marked = {
        index for index, family in printed.items() if family in wanted and family not in excluded
    }
    marked |= _broken(lines, printed, families, wanted, excluded, similarity)
    return _settled(lines, marked, printed, families, excluded)


def _broken(
    lines: Sequence[Line],
    printed: Mapping[int, int],
    families: Sequence[_Family],
    wanted: Iterable[int],
    excluded: set[int],
    similarity: Similarity,
) -> set[int]:
    """Return the pairs of lines one printed line was broken into, where it is printed text.

    A reader can break a printed line at another word from one scan to the next; the two halves
    together are the line every other document carries.
    """
    found: set[int] = set()
    for index in range(len(lines) - 1):
        if index in printed or index + 1 in printed:
            continue
        if lines[index].page != lines[index + 1].page:
            continue
        joined = f"{lines[index].norm} {lines[index + 1].norm}"
        for family in wanted:
            printed_line = families[family].consensus
            if family in excluded or not printed_line:
                continue
            if joined == printed_line or (
                _worth_measuring(joined, printed_line)
                and similarity(joined, printed_line) >= SIMILARITY
            ):
                found.update((index, index + 1))
                break
    return found


def _settled(
    lines: Sequence[Line],
    marked: set[int],
    printed: Mapping[int, int],
    families: Sequence[_Family],
    excluded: set[int],
) -> set[int]:
    """Return *marked* with the lines a neighbour makes printed text, and nothing else.

    A line of fewer than :data:`MIN_WORDS` words counts only next to printed lines ("Name:" in a
    header block yes, a lone "Yes." no), and so does a line that is not the form most documents
    carry: a printed line misread in one scan is still caught by the block it stands in, while a
    student's copy of a task, standing between student lines, is proofread.  A family the teacher
    said to proofread after all stays out, however short it is.
    """
    settled = set(marked)
    changed = True
    while changed:
        changed = False
        for index, line in enumerate(lines):
            family = printed.get(index)
            if index in settled or family is None or family in excluded:
                continue
            doubted = line.words < MIN_WORDS or line.norm != families[family].consensus
            if doubted and _neighbour(index, lines) & settled:
                settled.add(index)
                changed = True
    return settled


def _neighbour(index: int, lines: Sequence[Line]) -> set[int]:
    """Return the lines that stand directly before and after *index*, on the same page."""
    found: set[int] = set()
    if index > 0 and lines[index - 1].page == lines[index].page:
        found.add(index - 1)
    if index + 1 < len(lines) and lines[index + 1].page == lines[index].page:
        found.add(index + 1)
    return found


def _passage_texts(plain: str, lines: Sequence[Line], marked: Iterable[int]) -> tuple[str, ...]:
    """Return the text of the printed runs of one document."""
    return tuple(
        plain[segment.start : segment.end].strip()
        for segment in segments_of(plain, lines, marked)
        if segment.kind == "task" and plain[segment.start : segment.end].strip()
    )


# ── Lines and windows ──────────────────────────────────────────────────────


def _window_lines(lines: Sequence[Line], texts: Sequence[str], similarity: Similarity) -> set[int]:
    """Return the indices of the lines the *texts* cover, one window per text."""
    found: set[int] = set()
    for text in texts:
        window = _window_of(lines, _wanted_lines(text), similarity)
        if window is not None:
            first, width = window
            found.update(range(first, first + width))
    return found


def _window_of(
    lines: Sequence[Line], wanted: tuple[str, ...], similarity: Similarity | None
) -> tuple[int, int] | None:
    """Return where *wanted* stands in *lines* as ``(first line, how many)``, or ``None``.

    A reader breaks a printed line differently from one scan to the next, so a window one line
    shorter or longer than the passage is compared too.  An exact window is the answer; otherwise
    the most similar one wins, if it is similar enough.
    """
    if not wanted or not lines:
        return None
    ratio = similarity or _ratio
    target = "\n".join(wanted)
    best: tuple[float, int, int] | None = None
    for start in range(len(lines)):
        for width in (len(wanted) - 1, len(wanted), len(wanted) + 1):
            if width <= 0 or start + width > len(lines):
                continue
            candidate = "\n".join(line.norm for line in lines[start : start + width])
            if candidate == target:
                return start, width
            if not _worth_measuring(candidate, target):
                continue
            score = ratio(candidate, target)
            if score >= MATCH_SIMILARITY and (best is None or score > best[0]):
                best = (score, start, width)
    return (best[1], best[2]) if best is not None else None


def _overlaps_entry(
    lines: Sequence[Line],
    start: int,
    end: int,
    not_task: Sequence[str],
    similarity: Similarity | None,
) -> bool:
    """Whether the range *start*..*end* holds a range the teacher said to proofread after all."""
    ratio = similarity or _ratio
    for entry in not_task:
        window = _window_of(lines, _wanted_lines(entry), ratio)
        if window is None:
            continue
        first, width = window
        if lines[first].start < end and start < lines[first + width - 1].end:
            return True
    return False


def _whole_lines(passages: Sequence[str], not_task: Sequence[str]) -> frozenset[str]:
    """Return the normalised lines of *passages* and *not_task*: lines that stay whole.

    A passage that holds a label with its fill-in marks the whole line, so the line is not split
    where the passage is looked for.
    """
    texts = [*passages, *not_task]
    return frozenset(line for text in texts for line in _wanted_lines(text))


def _wanted_lines(text: str) -> tuple[str, ...]:
    """Return the normalised non-blank lines of a passage or an entry."""
    return tuple(norm for norm in (normalise(line) for line in text.split("\n")) if norm)


_LABEL_WITH_FILL_IN = re.compile(r"^\s*(?P<label>[^:]{1,60}):\s*(?P<fill>\S.*)$")

# The dashes and bullets are the point.
_LIST_MARKER = re.compile(
    r"^\s*(?:[-–—*•+]|\(?\d{1,2}[.)]|[a-zA-Z][.)])\s+"  # noqa: RUF001
)

_BLANKS = re.compile(r"[_.]{2,}|…")

_NUMBERED = re.compile(r"\d+[a-zA-Z]?[.)]?$")

_END_PUNCTUATION = " .,;:!?\"'“”„’»«"  # noqa: RUF001  # the quotes are the point


def _label_without_fill_in(text: str, start: int) -> tuple[str, int]:
    """Return a line's text without the fill-in it carries after a label, and that text's end.

    "Name: Anna M." is the line "Name:" and the answer "Anna M.": what stands after the label is
    written by hand.  A label has at most three words before its colon, so a sentence with a colon
    is no label; and a label that ends in a number ("Aufgabe 1:") heads a task, whose text after
    the colon is printed too.
    """
    match = _LABEL_WITH_FILL_IN.match(text)
    if match is None:
        return text, start + len(text)
    label = match.group("label").strip()
    if not label or len(label.split()) > 3 or _NUMBERED.search(label):
        return text, start + len(text)
    colon = match.end("label")
    return text[: colon + 1], start + colon + 1


def _overlap(one: str, other: str) -> float:
    """Return how many words two normalised texts share, from 0 to 1."""
    left, right = set(one.split()), set(other.split())
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _worth_measuring(one: str, other: str) -> bool:
    """Whether two texts can match at all.

    A ratio counts the characters both texts carry at most, and never more, so two texts that share
    too few characters cannot reach a match.  The comparison is the slow step on a long answer, and
    counting characters is cheap.
    """
    if not one or not other:
        return False
    shared = sum((Counter(one) & Counter(other)).values())
    return 2 * shared >= MATCH_SIMILARITY * (len(one) + len(other))


def _ratio(one: str, other: str) -> float:
    """Return how alike two texts are, from 0 to 1, on characters."""
    if one == other:
        return 1.0
    if not one or not other or not _worth_measuring(one, other):
        return 0.0
    return SequenceMatcher(None, one, other).ratio()
