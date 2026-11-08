"""The pass rule of the structured-output check, as data instead of upper-cased strings.

A model's ``structured_output`` flag in the catalog is decided here.  Before it was decided by
comparing upper-cased problem strings and by looking for string prefixes, which meant that a
new verdict had to be added in three places and silently slipped through when the wording
differed.  A :class:`Verdict` is now an enum with an explicit :attr:`Verdict.is_hard`, and
:class:`CheckRule` turns a run into a verdict and a set of runs into a pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum


class Verdict(Enum):
    """Why one structured-output run failed, or that it passed."""

    OK = ""
    """The answer was usable."""

    TRUNCATED = "TRUNCATED ANSWER"
    """Ollama stopped at the context limit, so the answer is cut off."""

    REQUEST_FAILED = "REQUEST FAILED"
    """The request did not come back at all (model missing, server gone, timeout)."""

    REQUEST_TOO_LONG = "REQUEST TOO LONG"
    """The request would not have fitted the context window, so it was not sent."""

    INVALID = "ANSWER DID NOT VALIDATE AGAINST THE SCHEMA"
    """The answer was not JSON matching the review schema."""

    NO_SENTENCES = "VALID ANSWER, BUT IT LISTS NO SENTENCES"
    """A valid answer that explains nothing, on an error-dense text."""

    UNCHANGED = "VALID ANSWER, BUT IT CHANGED NOTHING"
    """A valid answer that left the error-dense text untouched."""

    TOO_FEW_PLACED = "TOO FEW PLACED"
    """Fewer sentences could be spliced back into the text than the rule allows."""

    TOO_FEW_EXPLAINED = "TOO FEW EXPLAINED"
    """Fewer changes carried an explanation than the rule allows."""

    @property
    def is_hard(self) -> bool:
        """Whether this verdict always fails a model, however few runs produced it.

        These three mean the answer cannot be used at the app's context window; the others are
        a stray miss, and one of those in a whole check does not take the badge away.
        """
        return self in (Verdict.TRUNCATED, Verdict.REQUEST_FAILED, Verdict.REQUEST_TOO_LONG)


@dataclass(frozen=True)
class CheckRun:
    """What one request of the structured-output check produced."""

    fixture: str
    temperature: float
    verdict: Verdict
    placed: int = 0
    total: int = 0
    explained: int = 0
    needed: int = 0
    orphans: int = 0
    done_reason: str = ""
    prompt_eval_count: int = 0
    eval_count: int = 0
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        """Whether this run is a pass."""
        return self.verdict is Verdict.OK

    def settings_line(self) -> str:
        """Return the run's numbers as one line for the report."""
        fields = [
            f"{self.placed}/{self.total} placed",
            f"{self.explained}/{self.needed} explained",
            f"{self.orphans} orphans",
            f"{self.prompt_eval_count}+{self.eval_count} tok",
            f"done={self.done_reason or '?'}",
            f"{self.seconds:.1f}s",
        ]
        return f"T={self.temperature:g} " + "  ".join(fields)


@dataclass(frozen=True)
class CheckRule:
    """The thresholds a model has to reach to be marked as answering the review format."""

    placement_threshold: float = 0.90
    """Share of the answer's sentences that must be placeable in the text."""

    explained_floor: float = 0.40
    """Share of the changes that must carry an explanation, in every single run."""

    explained_overall: float = 0.75
    """Share of the changes that must carry an explanation, over all runs of a model."""

    allowed_failed_runs: int = 1
    """How many runs may fail without a hard failure, so one stray miss is tolerated."""

    def verdict(self, run: CheckRun) -> Verdict:
        """Return the verdict of *run*, judging its numbers against the thresholds.

        A run that already carries a failure verdict keeps it; only a run that came back with
        numbers is judged here.
        """
        if run.verdict is not Verdict.OK:
            return run.verdict
        ratio = run.placed / run.total if run.total else 0.0
        if ratio < self.placement_threshold:
            return Verdict.TOO_FEW_PLACED
        if run.needed and run.explained / run.needed < self.explained_floor:
            return Verdict.TOO_FEW_EXPLAINED
        return Verdict.OK

    def passes(self, runs: Sequence[CheckRun]) -> bool:
        """Return whether a model passes the rule on *runs*.

        One stray failed run is allowed, any hard failure is not, and the explanation ratio over
        all runs together must reach :attr:`explained_overall`, which does not depend on one
        unlucky run.
        """
        if not runs:
            return False
        verdicts = [self.verdict(run) for run in runs]
        failed = [verdict for verdict in verdicts if verdict is not Verdict.OK]
        if any(verdict.is_hard for verdict in failed):
            return False
        if len(failed) > self.allowed_failed_runs:
            return False
        needed = sum(run.needed for run in runs)
        if needed and sum(run.explained for run in runs) / needed < self.explained_overall:
            return False
        return True
