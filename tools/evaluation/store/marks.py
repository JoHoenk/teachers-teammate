"""The marks: one identity for everything a person marks, and the rule that makes them key data.

A mark is a person's verdict on one edit, and its identity is ``(text, span, replacement)`` with the
span in **characters** of the text's ``source``.  That identity is what lets a mark
survive a tokenizer change, and it is the only thing two markers can disagree about: the same edit,
called ``valid`` by one and ``wrong`` by the other.

A mark has a kind.  An ``edit`` mark judges an edit and takes ``valid``, ``wrong`` or ``vague``; an
``explanation`` mark judges the explanation of an edit and takes ``correct``, ``wrong`` or ``vague``,
and the explanation's hash joins its identity, so two models that wrote the same explanation share
the verdict and two that wrote different ones do not.  What a mark is *about* stays the edit, which
is what :func:`edit_identity` returns and what a disagreement is counted on.

Only ``edit`` marks decide accepted alternatives: an explanation called ``wrong`` says the
explanation is false, not that the edit is.

Two things are built from a set of marks:

* **accepted alternatives** — the ``valid`` edit marks of one marker, and only those no other
  marker's edit mark called ``wrong``.  They become part of the key group, so adding one moves ``key_sha256`` and no
  run.  A disagreement is *listed*, never resolved here: which of two markers is right is a
  person's decision, and the tool's job is to show it.
* **the unmatched list** — marks whose edit no stored answer makes any more, which
  :func:`~.validate.unmatched_marks` finds.  They are listed, never dropped.

Nothing here writes a file: :mod:`.reader` and :mod:`.writer` do that.  The two lookups at the end
read the store, because a marks file is filed under the question hash of its set and not under the
set's name, so finding the marks of a set is a question about the store and not about a mark.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from ..core.records import AcceptedEdit, Mark, question_sha256
from . import layout, reader

Identity = tuple[str, tuple[int, int], str, str]
"""What a mark is: the text's id, the character span, the replacement, and the explanation's hash.

The last part is empty for an edit mark, so an edit mark's identity is the three-part one with an
empty tail.
"""

EditIdentity = tuple[str, tuple[int, int], str]
"""What a mark is *about*: the text's id, the character span, and the replacement."""

EDIT = "edit"
EXPLANATION = "explanation"
"""The two kinds of mark: one judges an edit, the other the explanation of an edit."""

VALID = "valid"
WRONG = "wrong"
"""The two values that decide an accepted alternative; ``vague`` and ``correct`` decide nothing."""


def identity(mark: Mark) -> Identity:
    """Return what *mark* is: the edit, and the explanation when it is about one."""
    return (mark.text, mark.span, mark.replacement, mark.explanation_sha256)


def edit_identity(mark: Mark) -> EditIdentity:
    """Return what *mark* is about, which is what two markers can agree or disagree on."""
    return (mark.text, mark.span, mark.replacement)


def by_text(marks: Iterable[Mark]) -> dict[str, list[Mark]]:
    """Return *marks* grouped by the text they are about, in the order they were given."""
    found: dict[str, list[Mark]] = {}
    for mark in marks:
        found.setdefault(mark.text, []).append(mark)
    return found


def by_identity(marks: Iterable[Mark]) -> dict[Identity, list[Mark]]:
    """Return *marks* grouped by identity, so two verdicts on one thing are one entry."""
    found: dict[Identity, list[Mark]] = {}
    for mark in marks:
        found.setdefault(identity(mark), []).append(mark)
    return found


def disagreements(
    marks_by_marker: Mapping[str, Sequence[Mark]],
) -> list[tuple[EditIdentity, list[str]]]:
    """Return the edits one marker called ``valid`` and another called ``wrong``, with the markers.

    The list is sorted by identity so a report prints the same order twice, and it is what a person
    reads before deciding which marker to accept.  It is counted on the edit, not on the explanation:
    a spot-check ``wrong`` says the explanation is false, and the edit it explains is still the edit.
    """
    verdicts: dict[EditIdentity, dict[str, str]] = {}
    for marker, marks in marks_by_marker.items():
        for mark in marks:
            if mark.kind == EDIT and mark.value in (VALID, WRONG):
                verdicts.setdefault(edit_identity(mark), {})[marker] = mark.value
    found = [
        (key, sorted(markers))
        for key, markers in verdicts.items()
        if VALID in markers.values() and WRONG in markers.values()
    ]
    return sorted(found, key=lambda entry: entry[0])


def accepted(marks_by_marker: Mapping[str, Sequence[Mark]], marker: str) -> list[AcceptedEdit]:
    """Return the accepted alternatives of *marker*: its ``valid`` edits no other marker called wrong.

    Args:
        marks_by_marker: Every marker's marks on one test set, keyed by the marker's name.
        marker: The marker whose ``valid`` verdicts become key data.

    Returns:
        One :class:`~..core.records.AcceptedEdit` per accepted edit, sorted by span, so the key hash
        of a set does not depend on the order the marks were read in.

    Raises:
        KeyError: if *marker* has no marks, so a typo cannot silently accept nothing.
    """
    return [edit for _, edit in _accepted_marks(marks_by_marker, marker)]


def accepted_by_text(
    marks_by_marker: Mapping[str, Sequence[Mark]], marker: str
) -> dict[str, list[AcceptedEdit]]:
    """Return :func:`accepted` grouped by the text each alternative belongs to.

    Raises:
        KeyError: if *marker* has no marks.
    """
    found: dict[str, list[AcceptedEdit]] = {}
    for mark, edit in _accepted_marks(marks_by_marker, marker):
        found.setdefault(mark.text, []).append(edit)
    return found


def _accepted_marks(
    marks_by_marker: Mapping[str, Sequence[Mark]], marker: str
) -> list[tuple[Mark, AcceptedEdit]]:
    """Return the marks of *marker* that are accepted, with the alternative each stands for."""
    if marker not in marks_by_marker:
        known = ", ".join(sorted(marks_by_marker)) or "none"
        raise KeyError(f"no marks of {marker!r}; the store has: {known}")
    refused = {
        edit_identity(mark)
        for name, marks in marks_by_marker.items()
        if name != marker
        for mark in marks
        if mark.kind == EDIT and mark.value == WRONG
    }
    kept = [
        mark
        for mark in marks_by_marker[marker]
        if mark.kind == EDIT and mark.value == VALID and edit_identity(mark) not in refused
    ]
    kept.sort(key=lambda mark: (mark.text, mark.span))
    return [(mark, AcceptedEdit(span=mark.span, replacement=mark.replacement)) for mark in kept]


def question_of(root: Path, testset: str) -> str:
    """Return the question hash the marks of *testset* are filed under.

    Raises:
        FormatError: if the set's records are not of a format this reader knows.
        OSError: if the store has no such set.
    """
    return question_sha256(reader.read_testset(root, testset))


def marks_of(root: Path, question: str) -> dict[str, list[Mark]]:
    """Return every marker's marks on the set whose question hashes to *question*.

    A set is named by its question hash in the file name, so a set that was rebuilt with other
    texts has other marks and the old ones are not read.
    """
    found: dict[str, list[Mark]] = {}
    for path in layout.marks_files(root):
        if layout.question_of(path) != question:
            continue
        found.setdefault(layout.marker_of(path), []).extend(reader.read_marks(path))
    return found


def marks_on(root: Path, question: str) -> list[Mark]:
    """Return every mark on the set whose question hashes to *question*, marker by marker."""
    return [mark for marks in marks_of(root, question).values() for mark in marks]


__all__ = [
    "EDIT",
    "EXPLANATION",
    "VALID",
    "WRONG",
    "EditIdentity",
    "Identity",
    "accepted",
    "accepted_by_text",
    "by_identity",
    "by_text",
    "disagreements",
    "edit_identity",
    "identity",
    "marks_of",
    "marks_on",
    "question_of",
]
