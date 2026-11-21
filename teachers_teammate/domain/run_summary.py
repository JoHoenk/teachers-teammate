"""What a run did, sorted by outcome: the one result every adapter (GUI, CLI) reads.

Pure data, no I/O.  A run that was stopped is neither a success nor a failure: the documents
that were interrupted or never started are listed on their own, and the exit code of the
command line stays ``0`` unless something really failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RunStatus(StrEnum):
    """How a run ended as a whole; the order of the members is the order of precedence."""

    DONE = "done"
    STOPPED = "stopped"
    FAILED = "failed"


class FileStatus(StrEnum):
    """What happened to one file of a run: four outcomes, one field."""

    SAVED = "saved"
    FAILED = "failed"
    SKIPPED = "skipped"
    """Left out, not failed: a scan while text recognition is off, or a file that is gone."""
    STOPPED = "stopped"
    """The run was stopped while this file was processed; what was done is kept."""


@dataclass(frozen=True)
class FileOutcome:
    """What happened to one file of a run: the one result the adapters read.

    No texts: a row that needs one reads the document from the store, so the run's payload
    cannot drift from what is stored.
    """

    source_id: str
    name: str
    status: FileStatus
    error: str = ""
    """Why the file failed or was left out; empty when it was saved."""
    warnings: tuple[str, ...] = ()
    """Remarks about a saved file (a stage that fell back); empty when every stage succeeded."""
    stopped_stage: str = ""
    """With ``STOPPED``: the step the stop cut short; empty when the file did not get that far."""


@dataclass(frozen=True)
class RunSummary:
    """The file names of a run, sorted by what happened to them."""

    succeeded: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    """Left out, not failed: a scan while text recognition is off, or a file that is gone."""
    stopped: tuple[str, ...] = ()
    """Started, then interrupted by "Stop now"; what was already done is kept."""
    not_started: tuple[str, ...] = ()
    """Still waiting in the queue when the run was stopped."""
    error: str = ""
    """Why the run could not start or broke off (folder refused, server unreachable, …)."""

    @property
    def status(self) -> RunStatus:
        """Failed beats stopped beats done."""
        if self.error or self.failed:
            return RunStatus.FAILED
        if self.stopped or self.not_started:
            return RunStatus.STOPPED
        return RunStatus.DONE

    @property
    def exit_code(self) -> int:
        """``1`` only when something failed; a stopped run is not an error."""
        return 1 if self.status is RunStatus.FAILED else 0
