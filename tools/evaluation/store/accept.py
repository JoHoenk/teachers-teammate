"""Turning a marker's verdicts into key data: the accepted alternatives of a test set.

A marker reads a spot-check sheet and calls each edit ``valid``, ``wrong``, ``vague`` or ``correct``.
The ``valid`` ones are corrections the annotators did not make but a person judged right, and they
become part of the key: an answer that makes one is not punished for it.  That is a deliberate
step, so it is one command and one file, and the file is part of the key group — ``key_sha256`` moves
and no run does.

Three rules keep the step honest:

* only the named marker's ``valid`` edits are accepted, and only those no other marker called
  ``wrong``; a disagreement is *listed*, never resolved here, because which of two markers is right
  is a person's decision;
* the file is **merged**, not replaced: an accepted edit records who accepted it, so accepting a
  second marker's marks keeps the first's, and accepting the same marker again replaces only its own
  contribution;
* a mark whose edit is no longer an edit of any stored answer is listed too, so a mark that a
  differ change invalidated is visible instead of silently dropped.

Nothing here reads a corpus: a mark holds no text, and the accepted file holds a span and a
replacement, which is what the key group hashes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..core.records import AcceptedEdit, Answer, Mark, TestRecord, text_id
from . import layout, reader, validate, writer
from . import marks as marks_module


@dataclass(frozen=True)
class Accepted:
    """What one ``accept-marks`` wrote, and what a person has to look at before trusting it."""

    path: Path
    testset: str
    marker: str
    edits: int
    """Accepted alternatives in the file after the merge."""
    texts: int
    """Texts that hold at least one."""
    added: tuple[tuple[str, tuple[int, int], str], ...] = ()
    """The alternatives this call added, sorted."""
    removed: tuple[tuple[str, tuple[int, int], str], ...] = ()
    """The alternatives this call dropped, because the marker no longer calls them valid."""
    disagreements: tuple[tuple[tuple[str, tuple[int, int], str], list[str]], ...] = ()
    """Edits one marker called ``valid`` and another ``wrong``, with the markers, sorted."""
    unmatched: tuple[Mark, ...] = ()
    """Marks whose edit no stored answer makes any more, so a reader can look at them."""

    def lines(self) -> list[str]:
        """Return what the command prints: one line per fact, and one per thing to look at."""
        found = [
            f"wrote {self.path} ({self.edits} accepted alternatives on {self.texts} texts)",
            f"  {len(self.added)} added, {len(self.removed)} removed by {self.marker!r}",
            f"the key of {self.testset} moved: its key_sha256 is another one now, and no run did",
        ]
        found.extend(
            f"  + {text} {span[0]}:{span[1]} {replacement!r}"
            for text, span, replacement in self.added
        )
        found.extend(
            f"  - {text} {span[0]}:{span[1]} {replacement!r}"
            for text, span, replacement in self.removed
        )
        if self.disagreements:
            found.append(f"{len(self.disagreements)} edits two markers disagree about:")
            found.extend(
                f"  {text} {span[0]}:{span[1]} {replacement!r}: {', '.join(markers)}"
                for (text, span, replacement), markers in self.disagreements
            )
        if self.unmatched:
            found.append(f"{len(self.unmatched)} marks name an edit no stored answer makes:")
            found.extend(
                f"  {mark.text} {mark.span[0]}:{mark.span[1]} {mark.replacement!r} ({mark.value})"
                for mark in self.unmatched
            )
        return found


def accept_marks(root: Path, testset: str, marker: str) -> Accepted:
    """Write the accepted alternatives of *marker* on *testset*, and return what to look at.

    Args:
        root: The store's directory.
        testset: The test set the marks are about.
        marker: The marker whose ``valid`` verdicts become key data.

    Returns:
        The file written, how many edits it holds, and the disagreements and unmatched marks.

    Raises:
        KeyError: if *marker* has no marks on this set, so a typo cannot silently accept nothing.
        FormatError: if a record file or a marks file is not of a format this reader knows.
    """
    records = reader.read_testset(root, testset)
    by_id = {text_id(record): record for record in records}
    question = marks_module.question_of(root, testset)
    by_marker = marks_module.marks_of(root, question)
    fresh = marks_module.accepted_by_text(by_marker, marker)
    existing = reader.read_accepted(root, testset)
    merged, added, removed = _merged(existing, fresh, marker)
    path = layout.accepted_path(root, testset)
    writer.write_accepted(path, merged)
    return Accepted(
        path=path,
        testset=testset,
        marker=marker,
        edits=sum(len(edits) for edits in merged.values()),
        texts=len(merged),
        added=tuple(added),
        removed=tuple(removed),
        disagreements=tuple(marks_module.disagreements(by_marker)),
        unmatched=tuple(_unmatched(root, testset, by_marker, by_id)),
    )


def _merged(
    existing: Mapping[str, Sequence[AcceptedEdit]],
    fresh: Mapping[str, Sequence[AcceptedEdit]],
    marker: str,
) -> tuple[
    dict[str, list[AcceptedEdit]],
    list[tuple[str, tuple[int, int], str]],
    list[tuple[str, tuple[int, int], str]],
]:
    """Return the accepted file after *marker*'s contribution is replaced by *fresh*.

    An edit whose only marker is *marker* goes when *marker* no longer calls it valid; an edit
    another marker also accepted stays, with *marker* taken out of its markers.  The result is
    sorted by text and span, so two runs of the same command write the same bytes.
    """
    kept: dict[str, dict[tuple[tuple[int, int], str], AcceptedEdit]] = {}
    for text, edits in existing.items():
        for edit in edits:
            others = tuple(name for name in edit.markers if name != marker)
            if others:
                kept.setdefault(text, {})[_identity(edit)] = replace(edit, markers=others)
    for text, edits in fresh.items():
        for edit in edits:
            found = kept.setdefault(text, {}).get(_identity(edit))
            markers = (marker,) if found is None else tuple(sorted({*found.markers, marker}))
            kept[text][_identity(edit)] = replace(edit, markers=markers)
    merged = {
        text: [edits[key] for key in sorted(edits)] for text, edits in sorted(kept.items()) if edits
    }
    before = {_named(text, edit) for text, edits in existing.items() for edit in edits}
    after = {_named(text, edit) for text, edits in merged.items() for edit in edits}
    return merged, sorted(after - before), sorted(before - after)


def _identity(edit: AcceptedEdit) -> tuple[tuple[int, int], str]:
    """Return what makes two accepted alternatives the same one: the edit, not who accepted it."""
    return (edit.span, edit.replacement)


def _named(text: str, edit: AcceptedEdit) -> tuple[str, tuple[int, int], str]:
    """Return an accepted alternative as a person reads it: its text, its span and its replacement."""
    return (text, edit.span, edit.replacement)


def _unmatched(
    root: Path,
    testset: str,
    by_marker: Mapping[str, Sequence[Mark]],
    by_id: Mapping[str, TestRecord],
) -> list[Mark]:
    """Return the marks whose edit no stored answer of *testset* makes any more."""
    answers: dict[str, list[Answer]] = {}
    for stored in reader.read_runs(root):
        if stored.run.header.input.testset != testset:
            continue
        for answer in stored.run.answers:
            answers.setdefault(answer.text, []).append(answer)
    return validate.unmatched_marks(
        [mark for marks in by_marker.values() for mark in marks], answers, by_id
    )


__all__ = ["Accepted", "accept_marks"]
