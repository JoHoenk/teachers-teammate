"""What a teacher calls right or wrong in a comparison, and what that comes to per model.

One text cannot rank writing models, so the teacher decides: a change they read and call right is
a fix that should be there, one they call wrong is not.  A mark is on the **option** — the wording
that stands at one place — because two models that wrote the same wording wrote the same change
and the teacher judges it once.

A mark holds no text.  Its identity is the text's hash, the character span of the student's words
and the hash of the replacement, exactly as the evaluation tools identify a marked edit, so a mark
survives a change of the tokenizer and cannot carry a document's text into the data folder.

From the verdicts, one model's answer is counted: the changes it made that are right, the ones it
made that are wrong, the ones nobody marked, and the right changes it **missed** — the ones it
neither wrote nor covered with another change of the same place.  ``EditScore`` turns the counts
into precision and recall; F0.5 is read once every change of that model is marked, because an
unmarked change says nothing about the model.

Pure: no I/O, no Qt, no provider imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from .edit_agreement import EditScore

__all__ = [
    "Identity",
    "MarkCount",
    "OptionMark",
    "Verdict",
    "has_foreign_verdict",
    "identity",
]


class Verdict(StrEnum):
    """What a teacher says about one option of a comparison."""

    RIGHT = "right"
    """The change should be there: the student was wrong here."""

    WRONG = "wrong"
    """The change should not be there: the student's own words are correct."""


Identity = tuple[str, int, int, str]
"""What a mark is about: the text's hash, the character span, and the replacement's hash."""


def identity(text_sha: str, start: int, end: int, replacement: str) -> Identity:
    """Return what the option that replaces ``text[start:end]`` with *replacement* is.

    Args:
        text_sha: Hash of the plain transcription the option belongs to.
        start: First character of the student's words the option replaces.
        end: One past their last character; equal to *start* for an insertion.
        replacement: The wording that stands there in this option.

    Returns:
        The identity a mark of this option carries, and the one a comparison looks it up by.
    """
    return (text_sha, start, end, sha256(replacement.encode("utf-8")).hexdigest())


@dataclass(frozen=True)
class OptionMark:
    """One teacher's verdict on one option of one text's comparison."""

    text_sha: str
    """Hash of the plain transcription the option belongs to."""

    start: int
    """First character of the student's words the option replaces."""

    end: int
    """One past their last character; equal to *start* for an insertion."""

    replacement_sha256: str
    """Hash of the wording that stands there in the option the teacher judged."""

    verdict: Verdict
    """What the teacher said about it."""

    @property
    def identity(self) -> Identity:
        """Return what this mark is about."""
        return (self.text_sha, self.start, self.end, self.replacement_sha256)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable record; a mark holds no text of the document."""
        return {
            "start": self.start,
            "end": self.end,
            "replacement_sha256": self.replacement_sha256,
            "verdict": str(self.verdict),
        }

    @classmethod
    def from_dict(cls, text_sha: str, record: Mapping[str, object]) -> OptionMark | None:
        """Return the mark *record* holds about the text *text_sha*, or ``None`` when it is none.

        A record of another shape or with a verdict this app does not know is no mark: the teacher
        marks the change again, which costs one click.
        """
        start = record.get("start")
        end = record.get("end")
        digest = record.get("replacement_sha256")
        verdict = record.get("verdict")
        if not isinstance(start, int) or not isinstance(end, int):
            return None
        if not isinstance(digest, str) or not isinstance(verdict, str):
            return None
        try:
            known = Verdict(verdict)
        except ValueError:  # a verdict of a newer app: it says nothing this app can count
            return None
        return cls(
            text_sha=text_sha,
            start=start,
            end=end,
            replacement_sha256=digest,
            verdict=known,
        )


def has_foreign_verdict(record: Mapping[str, object]) -> bool:
    """Return whether *record* is a verdict of a newer app: whole, but with a verdict not known here.

    It counts for nothing in this app, and it is somebody's work, so a store that writes the
    verdicts again keeps it as it is.
    """
    if not isinstance(record.get("start"), int) or not isinstance(record.get("end"), int):
        return False
    verdict = record.get("verdict")
    if not isinstance(record.get("replacement_sha256"), str) or not isinstance(verdict, str):
        return False
    try:
        Verdict(verdict)
    except ValueError:  # not a verdict of this app: it is a newer app's
        return True
    return False


def verdicts_of(text_sha: str, marks: Sequence[OptionMark]) -> dict[Identity, Verdict]:
    """Return the verdicts about the text *text_sha*, keyed by the option they are about.

    Marks of another text are left out: a comparison counts only the judgements about its own
    text, whatever a store or a window hands it.
    """
    return {mark.identity: mark.verdict for mark in marks if mark.text_sha == text_sha}


@dataclass(frozen=True)
class MarkCount:
    """What a teacher's verdicts come to for one version of a comparison."""

    right: int
    """Changes of this version the teacher called right."""

    wrong: int
    """Changes of this version the teacher called wrong."""

    unmarked: int
    """Changes of this version nobody marked yet."""

    found: int
    """Right changes this version made, or covered with another right change of that place."""

    missed: int
    """Right changes of the text this version neither made nor covered."""

    @property
    def complete(self) -> bool:
        """Return whether every change of this version is marked, so a score says something."""
        return self.unmarked == 0

    @property
    def score(self) -> EditScore:
        """Return precision and recall of the changes this version made.

        The right options are the reference: a change called wrong counts against the model, and a
        right change it missed counts as not found.  Unmarked changes are counted in neither, which
        is why :attr:`f_beta` waits until there are none.
        """
        return EditScore(
            true_positives=self.right,
            false_positives=self.wrong,
            found=self.found,
            false_negatives=self.missed,
        )

    @property
    def f_beta(self) -> float | None:
        """Return F0.5 of the marked changes, or ``None`` while a change of this one is unmarked."""
        return self.score.f_beta() if self.complete else None
