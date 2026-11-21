"""What a teacher does to a document: the edits of a card action, and the work they leave.

Two halves, one subject.  The first turns a card action in the Check tab — *Take out*, *Put
back*, *Use suggestion* — into the :class:`TextEdit` values that make it, in character offsets of
the *plain* text (``TextFormatter.plain_text``), so the caller can apply them to the document it
shows and let the normal commit path store the result.

The second is the model of the teacher's own texts: :class:`TeacherWork`, kept apart from the
machine results in the cache, and :func:`current_texts`, the one function every reader calls to
learn which text a document has now.  The machine texts stay untouched; the teacher's texts win
where they exist.  A transcription part is bound to nothing (it is what the student wrote); a
corrections part is bound to one machine answer (:func:`proofreading_id`), so a new answer makes
it not apply rather than laying it over a text it was not written for.

The whitespace is the whole difficulty.  A hunk of the word diff is trimmed of the layout around
it, so putting the student's words back where the model removed them would glue them to their
neighbours (``"Der bunte Hund"`` → ``"DerHund"``).  One function, :func:`_fit_gap`, decides the
separators of every edit: a replacement keeps the ones it has, an insertion gets a space only
where it would otherwise touch a word, and a removal takes one adjacent space with it.  Words are
never joined across a block boundary.

Pure: no I/O, no Qt, no Markdown.  The offsets are those of the text ``align()`` saw.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import re

from .formatted_text import TextFormat, normalize_text_format
from .proofreading.quotes import map_forward
from .proofreading.review import AlignedChange, AlignedReview, AlignedUnclear, EditNote, UnclearNote
from .proofreading.word_diff import Hunk

_WORD_CHAR = re.compile(r"\w", flags=re.UNICODE)

#: Whitespace a removal may take with it.  A line break is never taken: it separates blocks.
_TAKEABLE = " \t\u00a0"


@dataclass(frozen=True)
class TextEdit:
    """Replace ``corrected_plain[start:end]`` by *text* (``start == end``: an insertion)."""

    start: int
    end: int
    text: str


def apply_edits(text: str, edits: Sequence[TextEdit]) -> str:
    """Return *text* with every edit of *edits* applied, last to first.

    The edits must be disjoint; applying them from the end keeps the offsets of the ones before
    valid.  The tests use this to check an action without a widget.
    """
    result = text
    for edit in sorted(edits, key=lambda item: (item.start, item.end), reverse=True):
        result = result[: edit.start] + edit.text + result[edit.end :]
    return result


def take_out(
    change_group: Sequence[AlignedChange], raw_plain: str, corrected_plain: str
) -> tuple[TextEdit, ...]:
    """Return the edits that undo one item of the review: the student's words come back.

    Every hunk of the item is undone: a replacement gets the student's words back, a deletion
    puts them back where they were, and an insertion is removed.  The edits are in text order
    and disjoint, so the caller applies them last to first.
    """
    hunks = [change.hunk for change in change_group]
    edits: list[TextEdit] = []
    for index, hunk in enumerate(hunks):
        # A removal may take one adjacent space; it must not take the neighbour hunk's.
        low = hunks[index - 1].corrected_end if index else 0
        high = hunks[index + 1].corrected_start if index + 1 < len(hunks) else len(corrected_plain)
        words = raw_plain[hunk.raw_start : hunk.raw_end] if hunk.original else ""
        edits.append(
            _fit_gap(
                corrected_plain,
                hunk.corrected_start,
                hunk.corrected_end,
                words,
                low=low,
                high=high,
            )
        )
    return tuple(edits)


def put_back(
    note: EditNote, aligned: AlignedReview, raw_plain: str, corrected_plain: str
) -> tuple[TextEdit, ...]:
    """Return the edits that put a taken-out edit back into the corrected text.

    The words the edit changed are found by carrying its span of the student's text forward
    through the current diff, and replaced by the model's wording.  ``()`` when a part of the
    edit cannot be put back: its words are no longer where the note says they are, or the
    teacher's own change stands there now — the card then says so and offers no button.
    """
    hunks = [change.hunk for change in aligned.changes]
    teacher = [change.hunk for change in aligned.changes if change.by_teacher]
    edits: list[TextEdit] = []
    for part in note.parts:
        if raw_plain[part.raw_start : part.raw_end] != part.original:
            return ()
        start, end = map_forward((part.raw_start, part.raw_end), hunks)
        if any(_touches(hunk, start, end) for hunk in teacher):
            return ()
        edits.append(_fit_gap(corrected_plain, start, end, part.corrected))
    return tuple(edits)


def use_suggestion(
    item: AlignedUnclear, aligned: AlignedReview, raw_plain: str, corrected_plain: str
) -> tuple[TextEdit, ...]:
    """Return the edits that replace an unclear expression by the model's suggestion.

    The expression's span is carried forward through the current diff, so a suggestion on words
    the model also changed lands on the corrected wording.  ``()`` when the expression was not
    found in the text: there is nothing to replace.
    """
    if not item.anchored or item.start is None or item.end is None:
        return ()
    if not 0 <= item.start <= item.end <= len(raw_plain):
        return ()
    hunks = [change.hunk for change in aligned.changes]
    start, end = map_forward((item.start, item.end), hunks)
    return (_fit_gap(corrected_plain, start, end, item.note.recommendation),)


def replace_words(
    corrected_spans: Sequence[tuple[int, int]],
    raw_span: tuple[int, int],
    aligned: AlignedReview,
    corrected_plain: str,
    text: str,
) -> tuple[TextEdit, ...]:
    """Return the edit that puts *text* in place of the words an item has in the corrected text.

    The words are the ones the item stands on now: its spans in the corrected text, so an
    insertion is replaced and not added to.  An item with no such span (a stretch the model left
    out) has the student's words there, which *raw_span* carries forward through the current
    diff.  ``()`` when the words are not in the text.
    """
    if corrected_spans:
        first = min(start for start, _end in corrected_spans)
        last = max(end for _start, end in corrected_spans)
    else:
        start, end = raw_span
        if not 0 <= start <= end:
            return ()
        first, last = map_forward((start, end), [change.hunk for change in aligned.changes])
    if not 0 <= first <= last <= len(corrected_plain):
        return ()
    return (_fit_gap(corrected_plain, first, last, text),)


def suggestion_is_wording(note: UnclearNote) -> bool:
    """Whether the note's recommendation is a wording to use, not a question to ask.

    Step ③ answers with one string: either a clearer wording or the question to put to the
    student.  A question ends with ``?``; anything else is offered as *Use suggestion*.
    """
    text = note.recommendation.strip()
    return bool(text) and not text.endswith("?")


def _fit_gap(
    text: str, start: int, end: int, replacement: str, *, low: int = 0, high: int | None = None
) -> TextEdit:
    """Return the edit that puts *replacement* in place of ``text[start:end]``.

    A replacement keeps the separators it has, because the hunk's span was trimmed of layout and
    so begins and ends where the words do.  An insertion gets one space on the side where it
    would otherwise touch a word, and none next to punctuation or a line start or end.  A removal
    takes one adjacent space with it — the one after, else the one before, never a line break —
    so the words around it do not end up with two spaces between them.  *low* and *high* bound
    the space a removal may take, so two edits of one item never overlap.
    """
    limit = len(text) if high is None else high
    if start < end and replacement:
        return TextEdit(start, end, replacement)
    if start == end and replacement:
        return _insertion(text, start, replacement)
    if start < end:
        return _removal(text, start, end, low=low, high=limit)
    return TextEdit(start, end, replacement)


def _insertion(text: str, at: int, words: str) -> TextEdit:
    """Return the edit that inserts *words* at *at*, with the spaces they need."""
    before = text[at - 1] if at > 0 else ""
    after = text[at] if at < len(text) else ""
    lead = " " if _is_word(before) else ""
    trail = " " if _is_word(after) else ""
    return TextEdit(at, at, lead + words + trail)


def _removal(text: str, start: int, end: int, *, low: int, high: int) -> TextEdit:
    """Return the edit that removes ``text[start:end]`` and one adjacent space."""
    after = text[end] if end < len(text) else ""
    if after in _TAKEABLE and end + 1 <= high:
        return TextEdit(start, end + 1, "")
    before = text[start - 1] if start > 0 else ""
    if before in _TAKEABLE and start - 1 >= low:
        return TextEdit(start - 1, end, "")
    return TextEdit(start, end, "")


def _is_word(char: str) -> bool:
    """Whether *char* is a letter, digit or underscore — what a word is made of."""
    return bool(char) and _WORD_CHAR.match(char) is not None


def _touches(hunk: Hunk, start: int, end: int) -> bool:
    """Whether the corrected span *start*..*end* touches *hunk* (a teacher's own change)."""
    if start == end:
        return hunk.corrected_start <= start <= hunk.corrected_end
    return start < hunk.corrected_end and hunk.corrected_start < end


# ── The teacher's work ────────────────────────────────────────────────────────

#: Version of a stored work file.  A file of a newer version is never read or overwritten.
WORK_VERSION = 1


class WorkTooNewError(Exception):
    """A work file was written by a newer version of the app, so it is left alone."""


@dataclass(frozen=True)
class TranscriptionWork:
    """The teacher's transcription: what the student wrote, as they read it.

    Bound to nothing — it is the text of the document itself, checked against the scan.  It
    exists when the teacher fixed or checked the transcription, so *Checked* names the exact
    text it was given.
    """

    text: str
    text_format: TextFormat
    checked: bool = False


@dataclass(frozen=True)
class CorrectionsWork:
    """The teacher's corrections of one machine answer, and what they decided about it.

    *bound_to* is the :func:`proofreading_id` of the answer this was written on.  A new answer —
    other settings, a re-run, the same settings after the cache was emptied — has another id,
    and this part no longer applies: it is ignored on read and replaced on the next write.
    """

    bound_to: str
    text: str | None = None
    """The corrected text; ``None`` while it is the machine's own."""
    checked: bool = False
    looked_at: frozenset[str] = frozenset()
    """Keys of the items the teacher has seen (``"edit:3"``, ``"unclear:2"``)."""
    kept: frozenset[str] = frozenset()
    """Items the teacher decided to leave as they are; empty until the app offers that."""
    kept_under: str = ""
    """The correction config hash the teacher kept this answer over, so these settings do not
    ask again."""

    @property
    def worked_on(self) -> bool:
        """Whether the teacher put work into this answer that a new one would throw away.

        Looking at an item is not work: it is cheap to lose, and a run that proofreads again
        would otherwise never be able to.
        """
        return self.text is not None or self.checked or bool(self.kept)


@dataclass(frozen=True)
class TeacherWork:
    """Everything the teacher did to one document, keyed by the document's content."""

    source_hash: str
    """``compute_file_hash`` of the document: one essay has one work, whatever it is called."""
    name: str
    """The file name at the last write; for people looking at the folder, never a key."""
    transcription: TranscriptionWork | None = None
    corrections: CorrectionsWork | None = None

    @property
    def empty(self) -> bool:
        """Whether there is nothing left to store: the file is deleted."""
        return self.transcription is None and self.corrections is None

    def to_dict(self) -> dict:
        """Return the stored shape of this work, ``version`` first."""
        return {
            "version": WORK_VERSION,
            "source_hash": self.source_hash,
            "name": self.name,
            "transcription": (
                None
                if self.transcription is None
                else {
                    "text": self.transcription.text,
                    "text_format": self.transcription.text_format,
                    "checked": self.transcription.checked,
                }
            ),
            "corrections": (
                None
                if self.corrections is None
                else {
                    "bound_to": self.corrections.bound_to,
                    "text": self.corrections.text,
                    "checked": self.corrections.checked,
                    "looked_at": sorted(self.corrections.looked_at),
                    "kept": sorted(self.corrections.kept),
                    "kept_under": self.corrections.kept_under,
                }
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping) -> TeacherWork:
        """Return the work stored in *raw*.

        Raises:
            WorkTooNewError: the file was written by a newer version of the app.
            ValueError: the file is not a work file.
        """
        version = raw.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("work file without a version")
        if version > WORK_VERSION:
            raise WorkTooNewError(f"work file version {version} is newer than {WORK_VERSION}")
        source_hash = raw.get("source_hash")
        if not isinstance(source_hash, str) or not source_hash:
            raise ValueError("work file without a source hash")
        return cls(
            source_hash=source_hash,
            name=str(raw.get("name") or ""),
            transcription=_transcription_from(raw.get("transcription")),
            corrections=_corrections_from(raw.get("corrections")),
        )


def _transcription_from(raw: object) -> TranscriptionWork | None:
    """Return the transcription part stored in *raw*, or ``None`` when there is none."""
    if not isinstance(raw, Mapping):
        return None
    text = raw.get("text")
    if not isinstance(text, str):
        raise ValueError("transcription without a text")
    return TranscriptionWork(
        text=text,
        text_format=normalize_text_format(raw.get("text_format")),
        checked=bool(raw.get("checked", False)),
    )


def _corrections_from(raw: object) -> CorrectionsWork | None:
    """Return the corrections part stored in *raw*, or ``None`` when there is none."""
    if not isinstance(raw, Mapping):
        return None
    bound_to = raw.get("bound_to")
    if not isinstance(bound_to, str) or not bound_to:
        raise ValueError("corrections without the answer they were made on")
    text = raw.get("text")
    if text is not None and not isinstance(text, str):
        raise ValueError("the corrections text is not a text")
    return CorrectionsWork(
        bound_to=bound_to,
        text=text,
        checked=bool(raw.get("checked", False)),
        looked_at=frozenset(str(key) for key in raw.get("looked_at") or ()),
        kept=frozenset(str(key) for key in raw.get("kept") or ()),
        kept_under=str(raw.get("kept_under") or ""),
    )


def text_identity(text: str) -> str:
    """Return the identity of *text*: its SHA-256, so two equal texts are one text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proofreading_id(source_identity: str, corrected: str, review: Mapping | None) -> str:
    """Return the identity of one machine answer: its source, its text and its review.

    The answer itself, not the settings that asked for it: two runs with identical settings
    answer differently (sampling), and the teacher's corrections belong to the answer they were
    written on.  The review is hashed with sorted keys, so its order does not matter.
    """
    payload = json.dumps(
        {"source": source_identity, "corrected": corrected, "review": review},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MachineTexts:
    """What the cache holds for one document: the machine's texts, untouched by the teacher."""

    raw_text: str
    raw_text_format: TextFormat
    corrected_text: str = ""
    """The proofread text; empty without a proofreading."""
    review: Mapping | None = None
    correction_source_hash: str = ""
    """``text_identity`` of the transcription the proofreading was made from; ``""`` = text 1."""

    @property
    def source_identity(self) -> str:
        """The identity of the transcription the stored proofreading was made from."""
        return self.correction_source_hash or text_identity(self.raw_text)


@dataclass(frozen=True)
class CurrentTexts:
    """The texts a document has now: the teacher's where they exist, the machine's else."""

    transcription: str
    transcription_format: TextFormat
    machine_transcription: str
    """What the recognition read, so a copy equal to it can be dropped again."""
    corrected: str
    machine_corrected: str
    proofreading_id: str
    """The identity of the stored answer; ``""`` without one that is current."""
    transcription_work: TranscriptionWork | None
    corrections_work: CorrectionsWork | None
    """Only the part that applies to the stored answer."""


def current_texts(machine: MachineTexts, work: TeacherWork | None) -> CurrentTexts:
    """Return the texts *machine* and *work* make together: the one merge point of every reader.

    The transcription is the teacher's copy where there is one, else what the recognition read.
    The corrected text is the teacher's where their part applies to the stored answer, else the
    machine's.  A proofreading made from another transcription than the current one is not
    current: the document has no proofreading until a run makes one, so its text is not shown
    (it would be diffed against a transcription it was not made from), it has no identity, and
    no corrections part applies to it.
    """
    transcription_work = work.transcription if work is not None else None
    transcription = transcription_work.text if transcription_work is not None else machine.raw_text
    transcription_format = (
        transcription_work.text_format
        if transcription_work is not None
        else machine.raw_text_format
    )
    machine_corrected = machine.corrected_text
    current_id = ""
    if machine_corrected.strip():
        if proofreading_source_matches(machine, work):
            current_id = proofreading_id(machine.source_identity, machine_corrected, machine.review)
        else:
            machine_corrected = ""
    corrections_work = work.corrections if work is not None else None
    applied = (
        corrections_work
        if corrections_work is not None and current_id and corrections_work.bound_to == current_id
        else None
    )
    corrected = (
        applied.text if applied is not None and applied.text is not None else machine_corrected
    )
    return CurrentTexts(
        transcription=transcription,
        transcription_format=transcription_format,
        machine_transcription=machine.raw_text,
        corrected=corrected,
        machine_corrected=machine_corrected,
        proofreading_id=current_id,
        transcription_work=transcription_work,
        corrections_work=applied,
    )


@dataclass(frozen=True)
class WorkStatus:
    """What the teacher changed and decided about one document, as a reader shows it.

    A summary of the work for the window: which texts they changed, what they checked, which
    items they looked at.  The texts themselves are in :class:`CurrentTexts`; this says what to
    mark, so the queue and the Check tab do not read the parts themselves.
    """

    transcription_changed: bool = False
    transcription_checked: bool = False
    corrections_changed: bool = False
    corrections_checked: bool = False
    looked_at: frozenset[str] = frozenset()
    kept_over_settings: bool = False
    """The stored answer is stale only by its settings and was kept because they worked on it."""
    proofreading_id: str = ""
    """The identity of the stored answer; ``""`` without one that is current."""

    @property
    def checked_label(self) -> str:
        """``"checked"``, ``"text"`` or ``""`` — the wording itself is the window's."""
        if self.corrections_checked:
            return "checked"
        if self.transcription_checked:
            return "text"
        return ""


def work_status(
    work: TeacherWork | None, current: CurrentTexts, *, kept_over_settings: bool = False
) -> WorkStatus:
    """Return what *work* says about the document *current* describes."""
    transcription = work.transcription if work is not None else None
    corrections = current.corrections_work
    return WorkStatus(
        transcription_changed=(
            transcription is not None and transcription.text != current.machine_transcription
        ),
        transcription_checked=transcription is not None and transcription.checked,
        corrections_changed=corrections is not None and corrections.text is not None,
        corrections_checked=corrections is not None and corrections.checked,
        looked_at=corrections.looked_at if corrections is not None else frozenset(),
        kept_over_settings=kept_over_settings,
        proofreading_id=current.proofreading_id,
    )


def proofreading_source_matches(machine: MachineTexts, work: TeacherWork | None) -> bool:
    """Whether the stored proofreading was made from the transcription the document has now.

    The guard against a store emptied on one side only: the teacher's transcription gone while
    the cache still holds a proofreading made from it.  Without a proofreading there is nothing
    to be stale.
    """
    if not machine.corrected_text.strip():
        return True
    transcription = (
        work.transcription.text
        if work is not None and work.transcription is not None
        else machine.raw_text
    )
    return machine.source_identity == text_identity(transcription)


def with_transcription(
    work: TeacherWork, text: str, text_format: TextFormat, machine: MachineTexts
) -> TeacherWork:
    """Return *work* with *text* as the teacher's transcription.

    A copy that is the recognition's own text and not checked is no work at all, so the part is
    dropped.  A proofreading is made from the transcription, so storing a new one drops the
    corrections part: they were written on an answer that no longer applies.
    """
    checked = work.transcription.checked if work.transcription is not None else False
    part = (
        None
        if text == machine.raw_text and not checked
        else TranscriptionWork(text=text, text_format=text_format, checked=checked)
    )
    corrections = None if machine.corrected_text.strip() else work.corrections
    return replace(work, transcription=part, corrections=corrections)


def with_corrected(work: TeacherWork, text: str, current: CurrentTexts) -> TeacherWork:
    """Return *work* with *text* as the teacher's corrected text, bound to the current answer.

    A text that is the machine's own again drops the copy but keeps the part: *Checked* and the
    items looked at still belong to this answer.  A part bound to another answer is replaced —
    its items are not the ones this text has.
    """
    if not current.proofreading_id:
        return work
    existing = work.corrections
    base = (
        existing
        if existing is not None and existing.bound_to == current.proofreading_id
        else CorrectionsWork(bound_to=current.proofreading_id)
    )
    return replace(
        work,
        corrections=replace(base, text=None if text == current.machine_corrected else text),
    )


def with_checked(work: TeacherWork, checked: bool, current: CurrentTexts) -> TeacherWork:
    """Return *work* with the document marked as checked, or not.

    *Checked* marks what the document has now: with a proofreading it is the corrections that
    are checked, and a new proofreading starts unchecked; without one it is the transcription,
    which is then stored as a copy — the check names the exact text it was given.
    """
    if current.proofreading_id:
        existing = work.corrections
        base = (
            existing
            if existing is not None and existing.bound_to == current.proofreading_id
            else CorrectionsWork(bound_to=current.proofreading_id)
        )
        return replace(work, corrections=replace(base, checked=checked))
    part = work.transcription
    if part is None:
        if not checked:
            return work
        part = TranscriptionWork(
            text=current.transcription, text_format=current.transcription_format, checked=True
        )
    else:
        part = replace(part, checked=checked)
    if not part.checked and part.text == current.machine_transcription:
        part = None
    return replace(work, transcription=part)


def with_looked_at(work: TeacherWork, keys: Iterable[str], current: CurrentTexts) -> TeacherWork:
    """Return *work* with *keys* marked as looked at, in the part of the current answer.

    Looking at an item is stored at once (a few KB, written atomically) and never undone; it is
    not work a run has to keep, so it does not make the answer one to keep.
    """
    if not current.proofreading_id:
        return work
    existing = work.corrections
    base = (
        existing
        if existing is not None and existing.bound_to == current.proofreading_id
        else CorrectionsWork(bound_to=current.proofreading_id)
    )
    return replace(work, corrections=replace(base, looked_at=base.looked_at | frozenset(keys)))


def kept_over(
    work: TeacherWork, expected_correction_hash: str, current: CurrentTexts
) -> TeacherWork:
    """Return *work* with the answer kept over *expected_correction_hash*.

    The teacher answered *Keep my changes* for these settings: the same settings never ask
    again, and a run with them keeps the answer without asking.  Only a part that applies to the
    stored answer is stamped; one bound to another answer is not what the teacher kept.
    """
    if work.corrections is None or current.corrections_work is None:
        return work
    return replace(work, corrections=replace(work.corrections, kept_under=expected_correction_hash))


def without_corrections(work: TeacherWork) -> TeacherWork:
    """Return *work* without the corrections part: the answer it was written on is gone."""
    return replace(work, corrections=None)


def without_work_for_reading(work: TeacherWork) -> TeacherWork:
    """Return *work* without either part: the document is read again from its pages."""
    return replace(work, transcription=None, corrections=None)


def looked_at_progress(keys: Sequence[str], looked_at: Iterable[str]) -> tuple[int, int]:
    """Return ``(looked at, countable)`` over *keys*, the items the list counts.

    Only the keys the list has now count: an item that is gone (a correction taken out, a
    proofreading replaced) is not a thing the teacher still has to look at.
    """
    seen = set(looked_at)
    return sum(1 for key in keys if key in seen), len(keys)
