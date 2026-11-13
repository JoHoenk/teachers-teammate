"""The per-setup smoke check: does this setup work at all, before it costs a night?

A **setup** is one model asked one way: tag, request profile (compact JSON on or off),
temperature, window and answer mode.  Every new setup — a candidate, a quantisation, the compact
rule, a temperature arm — used to cost a full gate-1 run of 18 to 36 requests to find out
whether it works at all.  The smoke check sends gate 1's own requests, cheap to expensive, one
run each, and stops at the first fixture that fails twice: at most ten requests, a few seconds to
a few minutes.

It is gate 1's first stage, not a verdict.  A pass means "no sign of a systematic fault", never
"passes gate 1"; a setup that is wrong 30 % of the time passes here and meets gate 1, which is
where the eighteen runs are.

**When a setup fails** (:func:`smoke_decision`): only when the same fixture fails twice in a row,
which is the app's own "ask once more", so one unlucky run never rejects a model.  At a 5 %
soft-failure rate per run a good model is rejected 0.25 % of the time per fixture; a setup that
fails 70 % of the time or more is caught at least 49 % per fixture it fails on.  Two failures a
second try cannot cure fail at once: a request that did not come back, and the collapse to one
sentence on the full page.

**The answers are gate 1's answers** (``--answers``): the smoke runs are run 1 of each fixture
(and run 2 of a fixture that needed its retry), so ``structured-output --continue`` finishes the
setup from that file and sends only the runs still missing.  The one difference is the timeout,
120 s instead of 300 — and it bounds only the wait for the **first token**, not the answer: a model
that has begun to answer may take minutes (``qwen3.5:4b`` took 121 to 253 s for the full page).
The first request gets :data:`LOAD_ALLOWANCE` more, because it includes the model's load.  A
request that waited in vain is retried like any failure, and is not saved as a run, so gate 1 asks
that run again under its own timeout.

**A runaway is bounded by a deadline, not by the window.**  A model that loops keeps writing —
``mistral:latest`` wrote 81,920 tokens in 937 s, and a 3B Granite held a run for minutes on a
fixture that takes 5 s — so every request has :data:`SMOKE_DEADLINE` seconds for the whole answer
(two and a half times that for the long fixtures).  Past it the request is abandoned, the server
stops generating, and the run counts as cut off: asked once more, and a setup that runs away twice
fails.

The rule is pure (:func:`smoke_decision`, :func:`text_smoke_decision`), so it can be replayed on
saved answers without a server; :class:`SmokeCheck` and :class:`TextSmokeCheck` are what send.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
import time
from typing import TYPE_CHECKING

from .check_rule import CheckRule, CheckRun, Verdict
from .ports import ServerUnavailableError
from .structured_check import (
    Fixture,
    Sent,
    StructuredOutputCheck,
    fixture_named,
    judge,
    text_of,
    text_request,
    window_of,
)

if TYPE_CHECKING:
    from teachers_teammate.domain.formatted_text import TextFormat
    from teachers_teammate.domain.model_catalog import RequestProfile

    from .performance import LoadedModel
    from .ports import ChatPort, ProbePort

SMOKE_FIXTURES: tuple[str, ...] = ("plain DE", "Markdown", "plain EN", "plain FR", "full page DE")
"""The fixtures the smoke check sends, cheap to expensive.

The short ones take seconds and show the profile, the load, the placement and the explanations;
the full page takes a minute and shows a collapse, a runaway and a partial page.  The budget
fixture is not part of it: it is the same fault as the full page at twice the cost.
"""

FULL_PAGE = "full page DE"
"""The fixture whose coverage shows a collapse to one sentence."""

COLLAPSE_COVERAGE = 0.20
"""Share of the full page's error sentences at or below which the answer collapsed to one."""

SMOKE_TIMEOUT = 120.0
"""Seconds a smoke request may wait for its first token.

It bounds the wait only, not an answer that has begun (``qwen3.5:4b`` took 121 to 253 s for the
full page).  A runaway (``mistral:latest`` wrote 81,920 tokens in 937 s) is stopped by
:data:`SMOKE_DEADLINE`.
"""

SMOKE_DEADLINE = 240.0
"""Seconds a whole answer to a short fixture may take: four times the slowest valid one recorded
(62 s, EuroLLM 9B on Markdown).  The long fixtures get 2.5 times as much, 600 s, against the
slowest valid full page recorded, 372 s."""

LOAD_ALLOWANCE = 600.0
"""Seconds added to the first request's wait, because a cold model is loaded into memory by it."""

TEXT_SMOKE_FIXTURES: tuple[str, ...] = ("plain DE", "Markdown", "plain FR")
"""The fixtures of the text-mode smoke check: the three short ones, in that order."""

TEXT_LENGTH_RATIO: tuple[float, float] = (0.6, 1.5)
"""How far a text-mode answer may be from the length of its text and still be that text."""

MARKERS: tuple[str, ...] = ("**", "~~", "*", "- ")
"""The Markdown marks a text-mode answer must not lose."""

_REASONS: dict[Verdict, str] = {
    Verdict.TRUNCATED: "cut off at the window, twice",
    Verdict.INVALID: "the answer did not validate, twice",
    Verdict.NO_SENTENCES: "answered nothing, twice",
    Verdict.UNCHANGED: "changed nothing, twice",
}
"""The reason a fixture failed twice, for the verdicts that need no numbers."""


@dataclass(frozen=True)
class Smoke:
    """What the smoke check says about one setup: a pass, or a failure and its reason."""

    passed: bool
    reason: str
    warnings: tuple[str, ...] = ()
    """What is worth knowing but does not fail the setup: one empty answer, a retry, a split."""
    requests: int = 0
    """How many requests the smoke check sent."""
    seconds: float = 0.0
    hard: bool = False
    """Whether a second try could not have cured it, so ``--continue --force`` does not either."""
    runs: tuple[CheckRun, ...] = ()
    """The runs the smoke check made, so gate 1 can continue from them."""
    done: frozenset[tuple[str, int, int]] = frozenset()
    """The ``(fixture, round, run)`` keys of those runs, which gate 1 does not send again."""
    fixture: str = ""
    """The fixture that failed the setup, or empty when it passed or no fixture is to blame."""

    def line(self, setup: str) -> str:
        """Return the one line of the setup: its name, the result and the reason."""
        return f"smoke {setup}: {'PASS' if self.passed else 'FAIL'} — {self.reason}"


def setup_label(tag: str, profile: RequestProfile, window: int) -> str:
    """Return the setup as one name: the tag, its profile and its window.

    The label is what the record and the screen's table show, so two setups of one model — the
    compact rule on and off, two temperatures — cannot be confused.
    """
    rule = "compact JSON" if profile.compact_json else "plain"
    temperature = "the app's own" if profile.temperature is None else f"T={profile.temperature:g}"
    return f"{tag} ({rule}, {temperature}, {window})"


def collapsed(run: CheckRun) -> bool:
    """Whether the answer collapsed to one sentence: few error sentences, none at the end.

    ``qwen3.5:9b`` with the compact rule covered 2 of 10 error sentences and none of the two in
    the last third; a good model covers 9 of 10 and both late ones.  The share is "at most a
    fifth", because the recorded collapse sits exactly on the fifth.
    """
    return (
        bool(run.cover_of)
        and run.covered / run.cover_of <= COLLAPSE_COVERAGE
        and not run.late_covered
    )


def immediate_failure(
    run: CheckRun, *, rule: CheckRule | None = None, compact_json: bool = False
) -> str | None:
    """Return why *run* fails its setup at once, or ``None`` when a second try could cure it.

    A request the server refused and a collapse on the full page are not bad luck: the same
    request sent again meets the same fault, so the setup is not asked twice.

    Args:
        run: The first run of a fixture.
        rule: The rule the run is judged by; the default thresholds when ``None``.
        compact_json: Whether the setup asks with the compact rule, so the collapse can name it.
    """
    judged = CheckRule() if rule is None else rule
    verdict = judged.verdict(run)
    if verdict is Verdict.REQUEST_FAILED and not run.timed_out:
        return "the request failed"
    if verdict is Verdict.REQUEST_TOO_LONG:
        return "the request does not fit the window"
    if run.fixture == FULL_PAGE and collapsed(run):
        hint = "; likely the compact rule, try --compact-json off" if compact_json else ""
        return (
            f"answered {run.covered} of {run.cover_of} error sentences on the full page"
            f" (collapse){hint}"
        )
    return None


class StepKind(Enum):
    """What the smoke check does after an attempt of a fixture."""

    PASS = "pass"
    """The fixture is done; go on to the next one."""

    RETRY = "retry"
    """Ask the same fixture once more (the app's own "ask once more")."""

    FAIL = "fail"
    """The setup fails here."""


@dataclass(frozen=True)
class Step:
    """The smoke check's next move, and why it fails when it does."""

    kind: StepKind
    reason: str = ""
    """For a failure a second try cannot cure: the reason, in words."""


def next_step(
    run: CheckRun, *, rule: CheckRule | None = None, compact_json: bool = False, retry: bool = False
) -> Step:
    """Return what the smoke check does after *run*: the one rule both the live check and a replay use.

    A usable attempt passes.  A failure a second try cannot cure fails at once.  Any other failure
    of a first attempt is asked once more, and a failed retry fails the fixture.

    Args:
        run: The attempt just judged.
        rule: The rule the run is judged by; the default thresholds when ``None``.
        compact_json: Whether the setup asks with the compact rule, so a collapse can name it.
        retry: Whether *run* is the second attempt of its fixture.
    """
    judged = CheckRule() if rule is None else rule
    if passes(run, judged):
        return Step(StepKind.PASS)
    if retry:
        return Step(StepKind.FAIL)
    if (why := immediate_failure(run, rule=judged, compact_json=compact_json)) is not None:
        return Step(StepKind.FAIL, why)
    return Step(StepKind.RETRY)


def smoke_decision(
    runs: Sequence[CheckRun], *, rule: CheckRule | None = None, compact_json: bool = False
) -> Smoke:
    """Return what *runs* say about their setup: a pass, or a failure and its reason.

    The runs are grouped by fixture in the order they were made: the first run of a fixture is
    its first try, a second run of the same fixture is the retry.  A fixture fails the setup only
    when both tries fail, so one unlucky run never rejects a model; a request that failed and a
    collapse on the full page fail at once, because a second try cannot cure them.

    Args:
        runs: The runs of one setup, in the order they were made.
        rule: The rule the runs are judged by; the default thresholds when ``None``.
        compact_json: Whether the setup asks with the compact rule, so a collapse can name it.
    """
    judged = CheckRule() if rule is None else rule
    grouped: dict[str, list[CheckRun]] = {}
    for run in runs:
        grouped.setdefault(run.fixture, []).append(run)
    warnings: list[str] = []
    requests = len(runs)
    seconds = sum(run.seconds for run in runs)
    for name in SMOKE_FIXTURES:
        attempts = grouped.get(name, [])
        if not attempts:
            continue
        first = attempts[0]
        step = next_step(first, rule=judged, compact_json=compact_json)
        if step.kind is StepKind.PASS:
            if first.verdict is Verdict.CUT_OFF_ONCE:
                # The app asked once more and the second answer was usable: soft, and it costs time.
                warnings.append(f"{name}: the first answer was cut off, the second one answered")
            continue
        if step.kind is StepKind.FAIL:
            return Smoke(
                False,
                f"{name}: {step.reason}",
                tuple(warnings),
                requests,
                seconds,
                hard=True,
                fixture=name,
            )
        if (
            len(attempts) > 1
            and next_step(attempts[1], rule=judged, compact_json=compact_json, retry=True).kind
            is StepKind.PASS
        ):
            warnings.append(f"{name}: failed once, the second try answered")
            continue
        return Smoke(
            False,
            f"{name}: {_reason(first, judged.verdict(first))}",
            tuple(warnings),
            requests,
            seconds,
            hard=any(
                judged.verdict(attempt).is_hard and not attempt.timed_out
                for attempt in attempts[:2]
            ),
            fixture=name,
        )
    return Smoke(
        True, "no sign of a systematic fault; gate 1 decides", tuple(warnings), requests, seconds
    )


def passes(run: CheckRun, rule: CheckRule) -> bool:
    """Whether *run* is a pass: usable, or usable on the app's own second try after a cut-off."""
    return run.verdict is Verdict.CUT_OFF_ONCE or rule.verdict(run) is Verdict.OK


def _reason(run: CheckRun, verdict: Verdict) -> str:
    """Return why a fixture failed twice, in the words of its numbers."""
    if run.timed_out:
        return "no first token within the timeout, twice"
    if run.deadline_hit:
        return "still writing at the deadline, twice (a runaway)"
    if verdict in _REASONS:
        return _REASONS[verdict]
    if verdict is Verdict.TOO_FEW_PLACED:
        return f"placed {run.placed} of {run.total} sentences, twice"
    if verdict is Verdict.PARTIAL:
        return f"answered {run.covered} of {run.cover_of} error sentences, twice"
    if verdict is Verdict.TOO_FEW_EXPLAINED:
        return f"explained {run.explained} of {run.needed} changes, twice"
    return f"{verdict.value.lower()}, twice"


def profile_problem(sent: Sent, profile: RequestProfile) -> str | None:
    """Return why the request on the wire is not the profile's, or ``None`` when it is.

    The compact rule lengthens the system prompt, so a request that does not carry it is a
    different request from the one the profile names — which is what the gate-1 v3 run sent
    without noticing.  The check reads the system text that went to the port, not the
    profile it meant to send.
    """
    from teachers_teammate.infrastructure.correction import COMPACT_ANSWER_RULE  # noqa: PLC0415

    carries = COMPACT_ANSWER_RULE in sent.system
    if carries == profile.compact_json:
        return None
    asked = "asks for it" if profile.compact_json else "does not ask for it"
    return (
        f"the request on the wire {'carries' if carries else 'lacks'} the compact rule, "
        f"though the profile {asked}"
    )


def loaded_warnings(loaded: Sequence[LoadedModel], tag: str, window: int) -> tuple[str, ...]:
    """Return what the server says about the loaded *tag*: its window and its memory split.

    A model that sits partly in main memory runs at a fraction of the speed, and one loaded at
    another window is not the setup that was asked for.  Both are warnings: the setup may still
    answer every fixture.
    """
    from teachers_teammate.domain.model_catalog import normalize_tag  # noqa: PLC0415

    model = next((item for item in loaded if normalize_tag(item.name) == normalize_tag(tag)), None)
    if model is None:
        return ()
    warnings: list[str] = []
    if model.context_length and model.context_length != window:
        warnings.append(
            f"the server loaded it at {model.context_length:,} tokens, not the {window:,} asked"
        )
    if model.size_bytes and model.size_vram_bytes < model.size_bytes:
        warnings.append(
            f"runs partly on the processor ({model.on_card_share * 100:.0f} % on the card)"
        )
    return tuple(warnings)


class SmokeCheck:
    """Send gate 1's cheapest fixtures to one setup and say whether it works at all.

    The requests are gate 1's own (:meth:`StructuredOutputCheck.send`), so every answer is saved
    to gate 1's answers file and gate 1 continues from it.  The check stops at the first fixture
    that fails twice, so a setup that is broken at once costs two requests and not ten.

    Args:
        check: The structured-output check whose requests and rule this uses.
        probe: The server, for the window and the memory split of the loaded model; ``None``
            skips those two checks.
        on_run: Called with every run as it is judged, for the record.
        on_sent: Called with every request, its judged run and its run number, to save it.
        on_request: Called before each request with ``(tag, fixture, run, attempt)``.
    """

    def __init__(
        self,
        check: StructuredOutputCheck,
        *,
        probe: ProbePort | None = None,
        on_run: Callable[[CheckRun], None] | None = None,
        on_sent: Callable[[Sent, CheckRun, int], None] | None = None,
        on_request: Callable[[str, str, int, int], None] | None = None,
    ) -> None:
        self._check = check
        self._probe = probe
        self._on_run = on_run
        self._on_sent = on_sent
        self._on_request = on_request

    def run(self, tag: str, temperature: float) -> Smoke:
        """Send the smoke fixtures to *tag* and return what they say about the setup.

        A request that waited for its first token in vain is a failed run like any other: it is
        retried, and it is left out of the runs and the keys handed to gate 1, which asks that run
        again under its own timeout.
        """
        profile = self._check.profile_of(tag, temperature)
        rule = self._check.rule
        runs: list[CheckRun] = []
        kept: list[tuple[tuple[str, int, int], CheckRun]] = []
        warnings: list[str] = []
        started = time.monotonic()

        def smoke_of(**fields) -> Smoke:
            """Return the verdict of the runs so far, with the runs gate 1 may keep."""
            return replace(
                smoke_decision(runs, rule=rule, compact_json=profile.compact_json),
                runs=tuple(run for _key, run in kept),
                done=frozenset(key for key, _run in kept),
                **fields,
            )

        for name in SMOKE_FIXTURES:
            fixture = fixture_named(name)
            sent, run = self._one(tag, fixture, temperature, 1, first=not runs)
            runs.append(run)
            if not run.timed_out:
                kept.append(((fixture.name, 1, 1), run))
            if len(runs) == 1:
                if (problem := profile_problem(sent, profile)) is not None:
                    return replace(
                        smoke_of(),
                        passed=False,
                        reason=problem,
                        warnings=(),
                        requests=len(runs),
                        seconds=time.monotonic() - started,
                        hard=True,
                    )
                warnings.extend(self._loaded_warnings(tag))
            step = next_step(run, rule=rule, compact_json=profile.compact_json)
            if step.kind is StepKind.RETRY:
                _retry_sent, retry = self._one(tag, fixture, temperature, 2)
                runs.append(retry)
                if not retry.timed_out:
                    kept.append(((fixture.name, 1, 2), retry))
                step = next_step(retry, rule=rule, compact_json=profile.compact_json, retry=True)
            if step.kind is StepKind.FAIL:
                break
        smoke = smoke_of()
        return replace(smoke, warnings=tuple(warnings) + smoke.warnings)

    def _one(
        self,
        tag: str,
        fixture: Fixture,
        temperature: float,
        run_number: int,
        *,
        first: bool = False,
    ) -> tuple[Sent, CheckRun]:
        """Send one run of *fixture* and return the request that answered and its judged run.

        The first request of a setup also loads the model, so it may wait :data:`LOAD_ALLOWANCE`
        longer for its first token.  An attempt that timed out is not given to ``on_sent``: it is
        not saved as a run.
        """
        attempts = self._check.send(
            tag,
            fixture,
            temperature,
            run_number=run_number,
            on_request=self._on_request,
            timeout=self._check.timeout + LOAD_ALLOWANCE if first else None,
        )
        if self._on_sent is not None:
            for attempt in attempts:
                if not attempt.timed_out:
                    self._on_sent(attempt, judge(attempt), run_number)
        run = judge(attempts[-1])
        if self._on_run is not None:
            self._on_run(run)
        return attempts[-1], run

    def _loaded_warnings(self, tag: str) -> tuple[str, ...]:
        """Return what the server says about the loaded model, or nothing without a probe."""
        if self._probe is None:
            return ()
        try:
            loaded = self._probe.loaded()
        except (OSError, ValueError):
            return ()
        return loaded_warnings(loaded, tag, self._check.window)


@dataclass(frozen=True)
class TextAnswer:
    """One text-mode answer of the smoke check, before it is judged."""

    fixture: str
    source: str
    """The text that was sent."""
    answer: str
    done_reason: str = ""
    seconds: float = 0.0
    request_failed: bool = False
    deadline_hit: bool = False
    """Whether the answer was still being written at the deadline and was abandoned."""
    text_format: TextFormat = "plain"


def text_problem(answer: TextAnswer) -> str | None:
    """Return why one text-mode answer is unusable, or ``None`` when it is.

    Text mode has no schema to validate against, so the answer is judged by what it is: empty,
    cut off or missing, or not recognisably the input — a preamble, an explanation instead of the
    text, a thinking block.  A Markdown text must keep its marks.
    """
    if answer.request_failed:
        return "the request failed"
    if answer.deadline_hit:
        return "still writing at the deadline (a runaway)"
    if answer.done_reason == "length":
        return "cut off at the window"
    text = answer.answer.strip()
    if not text:
        return "answered nothing"
    ratio = len(text) / len(answer.source) if answer.source else 1.0
    low, high = TEXT_LENGTH_RATIO
    if not low <= ratio <= high:
        return f"the answer is {ratio:.1f} times the text, so it is not the corrected text"
    if answer.text_format == "markdown" and _lost_marks(answer.source, text):
        return "the answer lost the Markdown marks"
    return None


def _lost_marks(source: str, corrected: str) -> bool:
    """Whether *corrected* dropped a Markdown mark that *source* carries."""
    return any(source.count(marker) > corrected.count(marker) for marker in MARKERS)


def text_smoke_decision(answers: Sequence[TextAnswer]) -> Smoke:
    """Return what the text-mode *answers* say about their setup.

    The same two-in-a-row rule as :func:`smoke_decision`: a fixture fails the setup only when
    both its tries fail.  The mode has no recorded failure to calibrate from, so it is a first
    guess and the first screen that uses it recalibrates it.
    """
    grouped: dict[str, list[TextAnswer]] = {}
    for answer in answers:
        grouped.setdefault(answer.fixture, []).append(answer)
    warnings: list[str] = []
    seconds = sum(answer.seconds for answer in answers)
    for name in TEXT_SMOKE_FIXTURES:
        attempts = grouped.get(name, [])
        if not attempts:
            continue
        problem = text_problem(attempts[0])
        if problem is None:
            continue
        if len(attempts) > 1 and text_problem(attempts[1]) is None:
            warnings.append(f"{name}: failed once, the second try answered")
            continue
        return Smoke(False, f"{name}: {problem}, twice", tuple(warnings), len(answers), seconds)
    return Smoke(
        True,
        "no sign of a systematic fault; gate 1 decides",
        tuple(warnings),
        len(answers),
        seconds,
    )


class TextSmokeCheck:
    """Send the text-mode smoke fixtures to one setup: the gate for models that only run there.

    Models that run in text mode only have no review answer to judge, so this sends the app's
    text-mode request for the three short fixtures, each once and once more on a failure.

    Args:
        chat: The server.
        timeout: Seconds one request may wait for its first token.
        on_run: Called with every answer as it comes back, for the record.
        deadline: Seconds a whole answer may take; ``None`` for no limit.
    """

    def __init__(
        self,
        chat: ChatPort,
        *,
        timeout: float = SMOKE_TIMEOUT,
        on_run: Callable[[TextAnswer], None] | None = None,
        deadline: float | None = SMOKE_DEADLINE,
    ) -> None:
        self._chat = chat
        self._timeout = timeout
        self._deadline = deadline
        self._on_run = on_run

    def run(self, tag: str, temperature: float) -> Smoke:
        """Send the text-mode fixtures to *tag* and return what they say about the setup."""
        answers: list[TextAnswer] = []
        for name in TEXT_SMOKE_FIXTURES:
            fixture = fixture_named(name)
            answers.append(self._ask(tag, fixture, temperature))
            if text_problem(answers[-1]) is None:
                continue
            answers.append(self._ask(tag, fixture, temperature))
            if text_problem(answers[-1]) is not None:
                break
        return text_smoke_decision(answers)

    def _ask(self, tag: str, fixture: Fixture, temperature: float) -> TextAnswer:
        """Send the text-mode request for *fixture* once and return what came back."""
        from teachers_teammate.domain.model_catalog import request_profile  # noqa: PLC0415
        from teachers_teammate.exceptions import (  # noqa: PLC0415
            OCRDeadlineError,
            OCRError,
            OllamaUnreachableError,
        )
        from teachers_teammate.infrastructure.ollama_utils import (  # noqa: PLC0415
            proofreading_options,
        )

        source = text_of(fixture).text
        system, user = text_request(fixture.language, fixture.text_format, source)
        started = time.monotonic()
        try:
            raw, stats = self._chat.chat_with_stats(
                tag,
                user,
                system=system,
                options=proofreading_options(temperature, window_of(self._chat)),
                think=request_profile(tag).think,
                timeout=self._timeout,
                total_timeout=self._deadline,
            )
        except OCRDeadlineError:
            answer = TextAnswer(
                fixture.name,
                source,
                "",
                deadline_hit=True,
                seconds=time.monotonic() - started,
                text_format=fixture.text_format,
            )
        except OllamaUnreachableError as error:
            raise ServerUnavailableError(str(error)) from error
        except OCRError:  # the model or the server failed this request; judge it
            answer = TextAnswer(
                fixture.name,
                source,
                "",
                request_failed=True,
                seconds=time.monotonic() - started,
                text_format=fixture.text_format,
            )
        else:
            answer = TextAnswer(
                fixture.name,
                source,
                raw,
                stats.done_reason,
                time.monotonic() - started,
                text_format=fixture.text_format,
            )
        if self._on_run is not None:
            self._on_run(answer)
        return answer


__all__ = [
    "COLLAPSE_COVERAGE",
    "FULL_PAGE",
    "LOAD_ALLOWANCE",
    "SMOKE_DEADLINE",
    "SMOKE_FIXTURES",
    "SMOKE_TIMEOUT",
    "TEXT_SMOKE_FIXTURES",
    "Smoke",
    "SmokeCheck",
    "Step",
    "StepKind",
    "TextAnswer",
    "TextSmokeCheck",
    "collapsed",
    "immediate_failure",
    "loaded_warnings",
    "next_step",
    "passes",
    "profile_problem",
    "setup_label",
    "smoke_decision",
    "text_problem",
    "text_smoke_decision",
]
