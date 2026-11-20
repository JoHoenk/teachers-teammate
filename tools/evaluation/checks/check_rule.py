"""The pass rule of the structured-output check, as data instead of upper-cased strings.

A model's ``structured_output`` flag in the catalog is decided here.  Before it was decided by
comparing upper-cased problem strings and by looking for string prefixes, which meant that a
new verdict had to be added in three places and silently slipped through when the wording
differed.  A :class:`Verdict` is now an enum with an explicit :attr:`Verdict.is_hard`, and
:class:`CheckRule` turns a run into a verdict and a set of runs into a :class:`Decision`.

The decision has a second round: a model with exactly two soft failures in the first round is
not failed on the spot but run again, and passes with at most four soft failures over both
rounds.  That is what makes the rule fair to a model that fails one run in twenty: with one
failure allowed in 18 runs such a model would fail the gate 7 % of the time, with the second
round 7 % of the runs are re-examined instead.
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
    """Ollama stopped at the context limit, so the answer is cut off.

    This is decided before the answer is validated: a cut-off JSON answer almost never
    validates, so judging the schema first would hide the real reason.  The order of the
    verdicts is too long, request failed, cut off, invalid, no sentences, unchanged, then the
    rule's own thresholds.
    """

    CUT_OFF_ONCE = "CUT OFF, THE SECOND TRY ANSWERED"
    """The first answer was cut off and the second one was usable.

    A soft failure: the app asks once more before it falls back, so a teacher keeps the
    explanations of that piece.  It costs time, which the speed table shows, and a model that
    does it often is slow rather than unusable.  A model that is cut off every time is
    :attr:`TRUNCATED` and fails hard.
    """

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

    PARTIAL = "ANSWERED ONLY PART OF THE TEXT"
    """A long text whose answer left too much of it, or its end, untouched."""

    TOO_FEW_EXPLAINED = "TOO FEW EXPLAINED"
    """Fewer changes carried an explanation than the rule allows."""

    @property
    def is_hard(self) -> bool:
        """Whether this verdict always fails a model, however few runs produced it.

        These three mean the answer cannot be used at the app's context window; the others are
        a stray miss, and one of those in a whole check does not take the badge away.
        :attr:`CUT_OFF_ONCE` is soft on purpose: the app's second try answered.
        """
        return self in (Verdict.TRUNCATED, Verdict.REQUEST_FAILED, Verdict.REQUEST_TOO_LONG)


class Decision(Enum):
    """What a set of runs says about a model: pass, fail, or run the second round."""

    PASS = "pass"
    FAIL = "fail"
    MORE_RUNS = "more runs"


@dataclass(frozen=True)
class FirstTry:
    """What the first try of a cut-off run cost, so the runaway is visible in the record.

    A cut-off answer that the second try answered is a soft failure, and the run line shows the
    second try's numbers.  Without the first try's, the cost of the runaway — the tokens and the
    minutes it spent before the window cut it off — would not be in the record at all.
    """

    prompt_eval_count: int = 0
    eval_count: int = 0
    seconds: float = 0.0

    def line(self) -> str:
        """Return the first try as one bracket: its tokens and its seconds."""
        return f"first try: {self.prompt_eval_count + self.eval_count:,} tok, {self.seconds:.1f}s"


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
    covered: int = 0
    """Error sentences of a long fixture that the answer did not leave untouched."""
    cover_of: int = 0
    """Error sentences of a long fixture; 0 for a short one, which has no coverage verdict."""
    late_covered: int = 0
    """Covered error sentences that start in the last third of the text."""
    late_of: int = 0
    """Error sentences that start in the last third of the text; 0 for a short fixture."""
    done_reason: str = ""
    prompt_eval_count: int = 0
    eval_count: int = 0
    seconds: float = 0.0
    num_ctx: int = 0
    """The context window the request was sent at, for the margin."""
    round: int = 1
    """1 for the first round, 2 for the runs of the second one."""
    first_try: FirstTry | None = None
    """The first try of a run whose first answer was cut off, so its cost stays in the record."""
    timed_out: bool = False
    """Whether a failed request failed because no first token arrived in time."""
    deadline_hit: bool = False
    """Whether the answer was still being written when the deadline abandoned it: a runaway."""

    @property
    def passed(self) -> bool:
        """Whether this run is a pass."""
        return self.verdict is Verdict.OK

    @property
    def margin(self) -> int:
        """Return the tokens the window had left: negative when Ollama shifted the context."""
        return self.num_ctx - self.prompt_eval_count - self.eval_count

    def settings_line(self) -> str:
        """Return the run's numbers as one line for the report."""
        fields = [
            f"{self.placed}/{self.total} placed",
            f"{self.explained}/{self.needed} explained",
            f"{self.orphans} orphans",
        ]
        if self.cover_of:
            fields.append(
                f"{self.covered}/{self.cover_of} covered (late {self.late_covered}/{self.late_of})"
            )
        fields += [
            f"{self.prompt_eval_count}+{self.eval_count} tok",
            f"margin {self.margin:,}",
            f"done={self.done_reason or '?'}",
            f"{self.seconds:.1f}s",
        ]
        return f"T={self.temperature:g} " + "  ".join(fields)


@dataclass(frozen=True)
class CheckRule:
    """The thresholds a model has to reach to be marked as answering the review format."""

    placement_threshold: float = 0.90
    """Share of the answer's sentences that must be placeable in the text."""

    coverage_threshold: float = 0.80
    """Share of a long fixture's error sentences the answer must touch."""

    explained_floor: float = 0.40
    """Share of the changes that must carry an explanation, in every single run."""

    explained_overall: float = 0.75
    """Share of the changes that must carry an explanation, over all runs of a model."""

    allowed_failed_runs: int = 1
    """How many runs may fail without a hard failure, so one stray miss is tolerated."""

    retry_at: int = 2
    """Exactly this many soft failures in the first round ask for a second round."""

    allowed_after_retry: int = 4
    """How many runs may fail over both rounds together."""

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
        if run.cover_of and (
            run.covered / run.cover_of < self.coverage_threshold
            or (run.late_of and not run.late_covered)
        ):
            return Verdict.PARTIAL
        if run.needed and run.explained / run.needed < self.explained_floor:
            return Verdict.TOO_FEW_EXPLAINED
        return Verdict.OK

    def decision(self, runs: Sequence[CheckRun], *, rounds: int, can_retry: bool) -> Decision:
        """Return what *runs* say about the model.

        Any hard failure fails the model in either round.  In the first round one soft failure
        is tolerated, exactly :attr:`retry_at` of them ask for a second round when *can_retry*
        (the temperature is above 0, so a repeat is not the same answer again), and anything
        else fails.  Over both rounds :attr:`allowed_after_retry` soft failures are tolerated.
        A pass also needs the explained share over all runs to reach
        :attr:`explained_overall`, which does not depend on one unlucky run.

        Args:
            runs: The runs of the model, both rounds together when *rounds* is 2.
            rounds: How many rounds *runs* covers.
            can_retry: Whether a second round is possible at this temperature.
        """
        if not runs:
            return Decision.FAIL
        failed = [verdict for verdict in map(self.verdict, runs) if verdict is not Verdict.OK]
        if any(verdict.is_hard for verdict in failed):
            return Decision.FAIL
        first = [self.verdict(run) for run in runs if run.round <= 1]
        first_failed = sum(1 for verdict in first if verdict is not Verdict.OK)
        if first_failed <= self.allowed_failed_runs and len(failed) <= self.allowed_after_retry:
            # The first round alone passes; a second round kept from an older rule (by
            # ``recheck``) may add failures, but not more than the two rounds allow together.
            return Decision.PASS if self._explained_enough(runs) else Decision.FAIL
        if rounds <= 1:
            if can_retry and first_failed == self.retry_at:
                return Decision.MORE_RUNS
            return Decision.FAIL
        if first_failed <= self.retry_at and len(failed) <= self.allowed_after_retry:
            return Decision.PASS if self._explained_enough(runs) else Decision.FAIL
        return Decision.FAIL

    def passes(self, runs: Sequence[CheckRun]) -> bool:
        """Return whether a model passes the rule on *runs* of one round."""
        return self.decision(runs, rounds=1, can_retry=False) is Decision.PASS

    def _explained_enough(self, runs: Sequence[CheckRun]) -> bool:
        """Return whether the explained share over all *runs* reaches the overall threshold."""
        needed = sum(run.needed for run in runs)
        return not needed or sum(run.explained for run in runs) / needed >= self.explained_overall
