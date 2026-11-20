"""The sessions of one run: whether a run may be resumed into its file, and what differs.

A run is one run file.  Its first line is the header, and every time ``run`` starts on it, it
appends one *session* line: the date, the tool revision, what the server held and the idle
graphics memory.  The header is written once, so a resumed run has to agree with it on everything
that decides what the texts mean, or the file would hold answers made under two protocols while
reading as one run.  ``run`` checks that before it sends anything; the store's validation checks
it again as a backstop.

The session lines themselves may differ freely: they describe one start, not the run.

The protocol is compared by its **version**, not by its fingerprint: a version may accept several
fingerprints, and an additive change of the request (a new optional field) adds one without changing
what the texts mean.  A change of what was sent still refuses, because the instructions are compared
exactly, per language.

A file whose last line was cut off mid-write is not a refusal: the killed append left half an answer
behind, and the run that resumes into the file sends that text again.  The half line is dropped
first, atomically, so the file is whole before the next answer is appended to it.  A file torn in
its header is refused, because nobody can say which session made the answers below it.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from ..core import protocol_table
from ..core.formats import FormatError, TornTail, read_jsonl, write_text_atomic
from ..core.records import RunHeader, read_run_file

_NESTED: tuple[str, ...] = ("input", "protocol", "run", "model_info", "environment")
"""The blocks of a header that are compared field by field, so a refusal names the one that moved."""


def _short(value: Any) -> str:
    """Return *value* as one line of a refusal message."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _differences(prefix: str, before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Return one line per key of two blocks that differs, *prefix* naming the block."""
    return [
        f"{prefix}{key}: {_short(before.get(key))} → {_short(after.get(key))}"
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    ]


def resume_conflicts(previous: RunHeader, current: RunHeader) -> list[str]:
    """Return one line per fact on which a stored header and a new session disagree.

    The model's digest is compared as well as the tag, because a tag can be re-pointed; both sit
    in ``protocol`` and ``model_info``, so the field-by-field walk covers them.  The protocol's
    fingerprint is the one field compared by version: two fingerprints of one version are one
    protocol, and a fingerprint the table does not record is compared as it is.
    """
    before, after = previous.to_dict(), current.to_dict()
    if _one_version(previous, current):
        # Two fingerprints of one version are one protocol: an additive change of the request adds
        # a fingerprint without changing what the texts mean.
        before["protocol"].pop("fingerprint", None)
        after["protocol"].pop("fingerprint", None)
    found: list[str] = []
    if before.get("task") != after.get("task"):
        found.append(f"task: {_short(before.get('task'))} → {_short(after.get('task'))}")
    for key in _NESTED:
        found.extend(_differences(f"{key}.", before.get(key) or {}, after.get(key) or {}))
    return found


def _one_version(previous: RunHeader, current: RunHeader) -> bool:
    """Return whether both headers' fingerprints are recorded, and by one version of the table."""
    before = protocol_table.version_of(previous.protocol.fingerprint)
    after = protocol_table.version_of(current.protocol.fingerprint)
    return before is not None and before == after


@dataclass(frozen=True)
class Resume:
    """What a resume found: why it may not happen, and the torn answer it dropped."""

    problem: str | None = None
    """Why the run may not be resumed into its file, or ``None`` when it may."""

    dropped: str | None = None
    """What to print after a torn answer was dropped, or ``None`` when nothing was torn."""


def resume_problem(path: Path, header: RunHeader, *, done: Collection[str]) -> Resume:
    """Return why a run may not be resumed into *path*, and what it dropped on the way.

    *header* is the header the new session would write.  A file of answers without a header cannot
    be resumed into: nobody can say which session made them.  A header without answers is fine,
    because the previous session may have stopped before its first text.  Every refusal says which
    file to delete to start the run over.

    A torn last line is dropped from the file before the resume, so the next answer is appended to a
    whole file.  A file torn in its header is refused instead: the header is the first line, and
    without it the answers below it belong to no session.
    """
    restart = f"delete {path} to start this run over"
    if not path.exists():
        if done:
            return Resume(
                problem=(
                    f"{path.name}: {len(done)} answers but no header, so nobody can say which "
                    f"session made them; {restart}"
                )
            )
        return Resume()
    try:
        records, torn = read_jsonl(path, torn_tail="report")
        stored = read_run_file(records, source=str(path), torn=torn)
    except (FormatError, OSError) as error:
        return Resume(problem=f"{error}; {restart}")
    dropped = None
    if torn is not None:
        _drop_torn_tail(torn)
        dropped = f"dropped a torn answer at {path.name}:{torn.line}; it is sent again"
    conflicts = resume_conflicts(stored.header, header)
    if not conflicts:
        return Resume(dropped=dropped)
    return Resume(
        problem=(
            f"{path.name}: this run cannot be resumed into, because the sessions would not be one "
            "run:\n  " + "\n  ".join(conflicts) + f"\n{restart}"
        ),
        dropped=dropped,
    )


def _drop_torn_tail(torn: TornTail) -> None:
    """Cut *torn*'s file back to its last complete line, in one atomic replace."""
    text = torn.path.read_text(encoding="utf-8")
    write_text_atomic(torn.path, text[: text.rfind("\n") + 1])


__all__ = ["Resume", "resume_conflicts", "resume_problem"]
