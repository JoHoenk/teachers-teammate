"""The structured-output check: does a model answer the review format reliably?

This is what decides ``OllamaModelSpec.structured_output`` in the catalog.  It used to live inside
``tools/verify_model_catalog.py``, where it built the prompt, checked the size, called the server,
validated, scored and printed in one function and could not be tested without Ollama.  The server
is now behind :class:`~.ports.ChatPort`, the fixtures are data, and the rule that turns runs into
a verdict is :class:`~.check_rule.CheckRule`.

Six fixtures cover what the answer format has to handle: plain English, plain German, plain
French, Markdown with formatting marks, one full page of dense German prose, and one invented
German page that is sent as the longest prefix of whole sentences the app's own budget allows.
The two long ones are the ones that show a context window that is too small: German tokenises
longest of the three languages, so it is the worst case for the size the app sends.

A fixture is built from sentences, so the coverage verdict needs no hand-counted token positions:
:func:`text_of` joins them and records where each one lies.

One request is :func:`sent_for` (the text cut to the budget) and :func:`judge` (what came back,
judged).  ``judge`` is a pure function of the request and the answer, so an answer saved by
``--answers`` is judged by the same code as a live one.  The order is the documented one: too
long, request failed, cut off, invalid, no sentences, unchanged, then the rule's thresholds —
including the coverage of a long fixture, which is what :class:`~.check_rule.Verdict.PARTIAL` is
for.  :class:`~.check_rule.CheckRule` turns the runs into a decision, with a second round for a
model that fails exactly two of its first eighteen runs.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence, Set
from dataclasses import dataclass, replace
import time
from typing import TYPE_CHECKING

from teachers_teammate.domain.correction_policy import cut_off_tries
from teachers_teammate.domain.formatted_text import TextFormat

from .check_rule import CheckRule, CheckRun, Decision, FirstTry, Verdict
from .gate1_fixtures import FIXTURES, Fixture
from .ports import ChatPort, ServerUnavailableError
from .quality import Span, count_explained, untouched_spans
from .spans import token_spans

if TYPE_CHECKING:
    from teachers_teammate.domain.model_catalog import RequestProfile
    from teachers_teammate.infrastructure.ollama_utils import ChatStats

RUNS_AT_TEMPERATURE = 3
"""How often each fixture runs at a temperature above 0: a check that passes once says little."""

LONG_DEADLINE_FACTOR = 2.5
"""How many times the short fixtures' deadline the two long ones get."""

BUDGET_FIXTURE = "page DE at budget"
"""The fixture whose margin says how close a model comes to the window."""

MARGIN_WARNING_SHARE = 0.10
"""Share of the window below which the margin report warns: the answer nearly did not fit."""


@dataclass(frozen=True)
class SentText:
    """The text of a fixture as it is sent, with the positions of its sentences."""

    text: str
    char_spans: tuple[Span, ...]
    """``[start, end)`` character offsets of every sentence, in text order."""
    errors: tuple[int, ...]
    """Indices of the sentences a proofreader must change."""
    late_errors: tuple[int, ...]
    """Indices of the error sentences that start in the last third of the text."""

    def token_spans(self) -> tuple[Span, ...]:
        """Return the token span of every sentence, the positions ``untouched_spans`` reads."""
        return token_spans(self.text, self.char_spans)


@dataclass(frozen=True)
class Sent:
    """One request of the check: what was sent, what came back, and how it ended."""

    fixture: str
    temperature: float
    sent_text: SentText
    language: str = ""
    """The language *name*, as the app's corrector is asked for it."""
    text_format: TextFormat = "plain"
    num_ctx: int = 0
    round: int = 1
    raw: str = ""
    stats: ChatStats | None = None
    seconds: float = 0.0
    request_failed: bool = False
    timed_out: bool = False
    """Whether the request failed because no first token arrived in time: the model did not start,
    which a second try may cure, unlike a request the server refused."""
    deadline_hit: bool = False
    """Whether the answer was still being written at the deadline and was abandoned."""
    too_long: bool = False
    attempt: int = 1
    """1 for the first try, 2 for the second one after a cut-off answer."""
    first_cut_off: bool = False
    """Whether the first try was cut off, so this answer is the second one."""
    compact_json: bool = False
    """Whether the request carries the compact-JSON rule of the model's profile."""
    system: str = ""
    """The system text that went on the wire, so a check can see the profile it really sent."""
    first_try: FirstTry | None = None
    """What the first try cost, when this answer is the second one after a cut-off."""


def text_of(fixture: Fixture, max_chars: int | None = None) -> SentText:
    """Return the text of *fixture*, cut to the longest prefix of whole sentences that fits.

    Sentences are joined with one space and paragraphs with a blank line, so the text of a
    fixture is exactly what its sentences say.  With *max_chars* the text stops before the first
    sentence that would cross it: a prefix of whole sentences, with the paragraph breaks where
    they fall, because a sentence cut in the middle is not a text a teacher would send.

    Args:
        fixture: The fixture to build the text of.
        max_chars: The budget to fill, or ``None`` for the whole text.

    Returns:
        The text, the character span of every sentence in it, and the indices of the error
        sentences and of those that start in the last third of the text.
    """
    pieces: list[str] = []
    spans: list[Span] = []
    errors: list[int] = []
    length = 0
    for paragraph in fixture.paragraphs:
        for position, sentence in enumerate(paragraph):
            separator = "" if not pieces else ("\n\n" if position == 0 else " ")
            start = length + len(separator)
            end = start + len(sentence.text)
            if max_chars is not None and end > max_chars:
                return _sent_text("".join(pieces), spans, errors)
            pieces.append(separator + sentence.text)
            spans.append((start, end))
            if sentence.error:
                errors.append(len(spans) - 1)
            length = end
    return _sent_text("".join(pieces), spans, errors)


def _sent_text(text: str, spans: list[Span], errors: list[int]) -> SentText:
    """Return the built text with its spans and the error sentences of its last third."""
    late = tuple(index for index in errors if spans[index][0] >= len(text) * 2 / 3)
    return SentText(text=text, char_spans=tuple(spans), errors=tuple(errors), late_errors=late)


def review_budget(
    language: str,
    text_format: TextFormat,
    num_ctx: int,
    profile: RequestProfile | None = None,
) -> int:
    """Return how many characters of text one review request carries at *num_ctx*.

    The figure is measured on the request :func:`review_request` builds, which is the one the
    app's own corrector sends (``test_the_gate_sends_the_apps_review_request`` pins that), so the
    gate and the app cannot disagree about the size the app really sends: the system prompt and
    the fixed part of the message come out of the window first, and what is left is shared with
    an answer :data:`~teachers_teammate.domain.context_budget.REVIEW_ANSWER_FACTOR` times the
    text.

    Args:
        language: The language *name*, as the app's corrector is asked for it.
        text_format: The notation of the text (Markdown adds a hint to the system prompt).
        num_ctx: The context window the request is sent at.
        profile: The request profile the request is built with; the default for a model the
            catalog does not know.  The compact-JSON rule lengthens the system prompt, so it
            shortens the text one request carries.
    """
    from teachers_teammate.domain.context_budget import (  # noqa: PLC0415
        REVIEW_ANSWER_FACTOR,
        ContextBudget,
    )
    from teachers_teammate.domain.model_catalog import (  # noqa: PLC0415
        DEFAULT_REQUEST_PROFILE,
    )

    compact = DEFAULT_REQUEST_PROFILE.compact_json if profile is None else profile.compact_json
    system, user = review_request(language, text_format, "", compact_json=compact)
    return ContextBudget(num_ctx).max_text_chars(
        len(system) + len(user), answer_factor=REVIEW_ANSWER_FACTOR
    )


def review_request(
    language: str, text_format: TextFormat, text: str, *, compact_json: bool = False
) -> tuple[str, str]:
    """Return ``(system, user)`` of the request the app's corrector would send for *text*.

    Args:
        language: The language *name*, as the app's corrector is asked for it.
        text_format: The notation of the text.
        text: The text the message carries.
        compact_json: Whether the model's profile asks for the compact-JSON rule, which lengthens
            the system text (so a budget measured without it would send a text the app splits).
    """
    from teachers_teammate.infrastructure.correction import request_messages  # noqa: PLC0415

    return request_messages(
        text, language, text_format=text_format, mode="review", compact_json=compact_json
    )


def text_request(language: str, text_format: TextFormat, text: str) -> tuple[str, str]:
    """Return ``(system, user)`` of the text-mode request the app's corrector sends for *text*.

    Text mode answers with the corrected text only, so it has no schema and the shorter system
    prompt.  The smoke check of that mode sends this request, which is the one a teacher's run
    sends when the review answer is not usable.

    Args:
        language: The language *name*, as the app's corrector is asked for it.
        text_format: The notation of the text.
        text: The text the message carries.
    """
    from teachers_teammate.infrastructure.correction import request_messages  # noqa: PLC0415

    return request_messages(text, language, text_format=text_format, mode="text")


def fixture_named(name: str) -> Fixture:
    """Return the fixture called *name*.

    Raises:
        KeyError: if the check has no fixture of that name, naming the ones it has.
    """
    for fixture in FIXTURES:
        if fixture.name == name:
            return fixture
    known = ", ".join(fixture.name for fixture in FIXTURES)
    raise KeyError(f"no fixture called {name!r}; the check has {known}")


def placeable(answer_json: str, source: str) -> tuple[int, int, str] | None:
    """Return ``(placed, total, corrected)`` for an answer; ``None`` if it does not validate."""
    from teachers_teammate.infrastructure.correction_schema import (  # noqa: PLC0415
        validate_answer,
    )

    answer, _error = validate_answer(answer_json)
    return None if answer is None else _placed(answer, source)


def _placed(answer, source: str) -> tuple[int, int, str]:
    """Return ``(placed, total, corrected)`` for a validated *answer*."""
    from teachers_teammate.domain.correction_review import splice  # noqa: PLC0415
    from teachers_teammate.infrastructure.correction_schema import to_review  # noqa: PLC0415

    review = to_review(answer)
    total = len(review.sentences)
    if not total:
        return 0, 0, source
    corrected, unplaced = splice(source, review.sentences)
    return total - len(unplaced), total, corrected


def judge(sent: Sent) -> CheckRun:
    """Return the run *sent* produced, judged from what came back.

    A pure function of the request and the answer, so a saved answer is judged by the same code
    as a live one (``verify_model_catalog recheck``).  The order is the documented one: too
    long, request failed, cut off, invalid, no sentences, unchanged, then the rule's thresholds.
    An answer that is the second try after a cut-off one is :attr:`Verdict.CUT_OFF_ONCE` when it
    is otherwise usable.

    Args:
        sent: The request, the answer and the statistics Ollama reported for it.
    """
    from teachers_teammate.application.formatting import TextFormatter  # noqa: PLC0415
    from teachers_teammate.infrastructure.correction_schema import (  # noqa: PLC0415
        to_review,
        validate_answer,
    )
    from teachers_teammate.infrastructure.ollama_utils import ChatStats  # noqa: PLC0415

    stats = sent.stats or ChatStats()
    answer, _error = validate_answer(sent.raw)
    counted = None if answer is None else _placed(answer, sent.sent_text.text)
    run = CheckRun(
        fixture=sent.fixture,
        temperature=sent.temperature,
        verdict=Verdict.OK,
        done_reason=stats.done_reason,
        prompt_eval_count=stats.prompt_eval_count,
        eval_count=stats.eval_count,
        seconds=sent.seconds,
        num_ctx=sent.num_ctx,
        round=sent.round,
        first_try=sent.first_try,
        deadline_hit=sent.deadline_hit,
    )
    if sent.too_long:
        return replace(run, verdict=Verdict.REQUEST_TOO_LONG)
    if sent.request_failed:
        return replace(run, verdict=Verdict.REQUEST_FAILED, timed_out=sent.timed_out)
    if stats.truncated:
        # A cut-off answer is cut off whether or not what arrived still validates: the partial
        # counts are filled in when it does, and left at 0 when it does not.
        placed, total = (counted[0], counted[1]) if counted is not None else (0, 0)
        return replace(run, verdict=Verdict.TRUNCATED, placed=placed, total=total)
    if counted is None:
        return replace(run, verdict=Verdict.INVALID)
    assert answer is not None  # counted is None otherwise
    placed, total, corrected = counted
    if not total:
        return replace(run, verdict=Verdict.NO_SENTENCES)
    source_plain = TextFormatter.plain_text(sent.sent_text.text, sent.text_format)
    corrected_plain = TextFormatter.plain_text(corrected, sent.text_format)
    explained = count_explained(source_plain, corrected_plain, to_review(answer))
    covered, late_covered = _coverage(sent.sent_text, source_plain, corrected_plain)
    if corrected_plain == source_plain:
        verdict = Verdict.UNCHANGED
    elif sent.first_cut_off:
        # The first answer was cut off and this one is usable: the app asks once more before it
        # falls back, so the teacher keeps the explanations.  Soft, and it costs time.
        verdict = Verdict.CUT_OFF_ONCE
    else:
        verdict = Verdict.OK
    return replace(
        run,
        verdict=verdict,
        placed=placed,
        total=total,
        explained=explained.explained,
        needed=explained.needed,
        orphans=explained.orphans,
        covered=covered,
        cover_of=len(sent.sent_text.errors),
        late_covered=late_covered,
        late_of=len(sent.sent_text.late_errors),
    )


def _coverage(sent_text: SentText, source_plain: str, corrected_plain: str) -> tuple[int, int]:
    """Return how many error sentences, and how many of the late ones, the answer touched.

    An error sentence counts as covered when the word diff to the corrected text does not leave
    it alone.  A short fixture has no error flags, so both figures are 0 and the rule has no
    coverage verdict for it.
    """
    if not sent_text.errors:
        return 0, 0
    untouched = untouched_spans(source_plain, corrected_plain, sent_text.token_spans())
    covered = sum(1 for index in sent_text.errors if not untouched[index])
    late = sum(1 for index in sent_text.late_errors if not untouched[index])
    return covered, late


def seed_settings(port: ChatPort, tag: str) -> str:
    """Return the sampling settings *tag* ships with, as one line for the report.

    ``temperature`` and ``num_ctx`` are left out: the app overrides both on every request.
    """
    import json  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    client = getattr(port, "client", None)
    url = getattr(client, "base_url", None) or "http://127.0.0.1:11434"
    request = urllib.request.Request(
        f"{str(url).rstrip('/')}/api/show",
        data=json.dumps({"model": tag}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            info = json.load(response)
    except Exception as exc:  # noqa: BLE001  # the line is advisory; the check reports the rest
        return f"settings unknown ({exc})"
    shipped = [
        " ".join(line.split())
        for line in (info.get("parameters") or "").splitlines()
        if line.split()[:1] not in (["temperature"], ["num_ctx"], ["stop"])
    ]
    quant = (info.get("details") or {}).get("quantization_level", "?")
    return f"quantisation {quant}; own settings: {'; '.join(shipped) or 'none'}"


def sent_for(
    fixture: Fixture,
    *,
    num_ctx: int,
    temperature: float,
    round_number: int = 1,
    budget: int | None = None,
) -> Sent:
    """Return the request the check sends for *fixture* at *num_ctx*, before sending it.

    The text is cut to the app's own review budget, so the gate and the app cannot disagree
    about what one request carries.  A fixture that is not cut to the budget and is longer than
    it is marked too long instead of being sent: CI keeps the shipped fixtures within the
    budget, so this only happens when the prompt grew between CI and the run.

    Args:
        fixture: The fixture to send.
        num_ctx: The context window the request is sent at.
        temperature: The temperature the request is sent at.
        round_number: 1 for the first round, 2 for the second one.
        budget: The characters one request carries, when the caller already knows it.
    """
    if budget is None:
        budget = review_budget(fixture.language, fixture.text_format, num_ctx)
    sent_text = text_of(fixture, budget if fixture.fill_budget else None)
    return Sent(
        fixture=fixture.name,
        temperature=temperature,
        sent_text=sent_text,
        language=fixture.language,
        text_format=fixture.text_format,
        num_ctx=num_ctx,
        round=round_number,
        too_long=len(sent_text.text) > budget,
    )


class StructuredOutputCheck:
    """Run the review answer format against every fixture and decide the catalog verdict."""

    def __init__(
        self,
        chat: ChatPort,
        rule: CheckRule | None = None,
        timeout: float = 300.0,
        compact_json: str = "catalog",
        deadline: float | None = None,
    ):
        self._chat = chat
        self._rule = rule or CheckRule()
        self._timeout = timeout
        self._compact_json = compact_json
        self._deadline = deadline
        self._budgets: dict[tuple[str, TextFormat, int, bool], int] = {}

    def profile_of(self, tag: str, temperature: float) -> RequestProfile:
        """Return the request profile *tag* is checked with, as the app would ask it."""
        from . import defaults  # noqa: PLC0415

        return defaults.request_profile_for(
            tag, compact_json=self._compact_json, temperature=temperature
        )

    @property
    def rule(self) -> CheckRule:
        """Return the rule a model has to pass."""
        return self._rule

    @property
    def window(self) -> int:
        """Return the context window the check sends at."""
        return window_of(self._chat)

    @property
    def compact_json(self) -> str:
        """Return the ``--compact-json`` choice the check asks with."""
        return self._compact_json

    def deadline_of(self, fixture: Fixture) -> float | None:
        """Return the seconds a whole answer to *fixture* may take, or ``None`` for no limit.

        The long fixtures get :data:`LONG_DEADLINE_FACTOR` times the short ones': the slowest
        valid full page recorded took 372 s (EuroLLM 9B), the slowest short one 62 s.
        """
        if self._deadline is None:
            return None
        return self._deadline * (LONG_DEADLINE_FACTOR if fixture.long else 1.0)

    @property
    def timeout(self) -> float:
        """Return the seconds a request may wait for its first token."""
        return self._timeout

    def run(
        self,
        tag: str,
        temperature: float,
        on_run=None,
        on_retry=None,
        on_sent=None,
        on_request=None,
        previous: Sequence[CheckRun] = (),
        done: Set[tuple[str, int, int]] = frozenset(),
    ) -> list[CheckRun]:
        """Send every fixture to *tag* and return what each request produced.

        A model with exactly two soft failures in the first round is run a second time, at a
        temperature above 0 only: at temperature 0 a repeat would be the same answer again.
        *on_retry* is told how many soft failures asked for it, so the command line can say so,
        *on_sent* receives every request with its run, so the answers can be saved, and
        *on_request* is told ``(tag, fixture, run_number, attempt)`` before each request is sent,
        so a progress display can name the one that is running.

        *previous* are the runs an earlier invocation already made — the smoke check's, or those
        of a run that was interrupted — judged by the same rule: they count towards the decision
        and are not sent again.  *done* names them as ``(fixture, round, run)``, which is what
        the answers file holds, so a continued run sends only what is missing.

        Args:
            tag: The model to ask.
            temperature: The temperature to ask it at.
            on_run: Called with every new run, its number and how many runs the fixture has.
            on_retry: Called with the number of soft failures that asked for a second round.
            on_sent: Called with every request, its judged run and its run number.
            on_request: Called before each request with ``(tag, fixture, run, attempt)``.
            previous: The runs an earlier invocation made, already judged.
            done: The ``(fixture, round, run)`` keys of *previous*, which are not sent again.
        """
        per_fixture = RUNS_AT_TEMPERATURE if temperature > 0 else 1
        results = self._round(tag, temperature, per_fixture, 1, on_run, on_sent, on_request, done)
        known = list(previous) + results
        if self._rule.decision(known, rounds=1, can_retry=temperature > 0) is Decision.MORE_RUNS:
            if on_retry is not None:
                on_retry(sum(1 for run in known if self._rule.verdict(run) is not Verdict.OK))
            results += self._round(
                tag, temperature, per_fixture, 2, on_run, on_sent, on_request, done
            )
        return results

    def _round(
        self,
        tag: str,
        temperature: float,
        per_fixture: int,
        number: int,
        on_run,
        on_sent=None,
        on_request=None,
        done: Set[tuple[str, int, int]] = frozenset(),
    ) -> list[CheckRun]:
        """Send every fixture *per_fixture* times and return the runs of round *number*.

        A run named by *done* was made by an earlier invocation and is skipped.
        """
        results: list[CheckRun] = []
        for fixture in FIXTURES:
            for run_number in range(1, per_fixture + 1):
                if (fixture.name, number, run_number) in done:
                    continue
                attempts = self.send(
                    tag,
                    fixture,
                    temperature,
                    round_number=number,
                    run_number=run_number,
                    on_request=on_request,
                )
                run = judge(attempts[-1])
                results.append(run)
                if on_sent is not None:
                    for attempt in attempts:
                        on_sent(attempt, judge(attempt), run_number)
                if on_run is not None:
                    on_run(run, run_number, per_fixture)
        return results

    def _budget(self, fixture: Fixture, profile: RequestProfile) -> int:
        """Return the characters one review request carries for *fixture*, computed once."""
        key = (fixture.language, fixture.text_format, window_of(self._chat), profile.compact_json)
        if key not in self._budgets:
            self._budgets[key] = review_budget(*key[:3], profile)
        return self._budgets[key]

    def decision(self, runs: list[CheckRun]) -> Decision:
        """Return the decision for *runs*, which carry the round they belong to.

        A second round is never asked for here: :meth:`run` has already run it when it was
        possible, so a set of runs that would ask for one is a fail.
        """
        rounds = max((run.round for run in runs), default=1)
        return self._rule.decision(runs, rounds=rounds, can_retry=False)

    def passes(self, runs: list[CheckRun]) -> bool:
        """Return whether *runs* pass the rule."""
        return self.decision(runs) is Decision.PASS

    def send(
        self,
        tag: str,
        fixture: Fixture,
        temperature: float,
        *,
        round_number: int = 1,
        run_number: int = 1,
        on_request=None,
        timeout: float | None = None,
    ) -> list[Sent]:
        """Send the review request for *fixture* and return every attempt, unjudged.

        A first answer the window cut off is asked once more, as the app does for a model on
        this computer (:func:`~teachers_teammate.domain.correction_policy.cut_off_tries`: not at
        temperature 0, where a second try would be the same answer), so the check measures the
        request a teacher's run sends.  A second cut-off is not asked again.  The list holds one
        attempt, or two when the first was cut off.  *on_request* is told before each attempt is sent.  *timeout* replaces the check's own
        wait for the first token, which the smoke check uses to give a cold model time to load.

        One request of the check, which is what the smoke check sends too: it asks the same
        fixture the same way, so its answers are gate 1's answers.
        """
        profile = self.profile_of(tag, temperature)
        sent = replace(
            sent_for(
                fixture,
                num_ctx=window_of(self._chat),
                temperature=temperature,
                round_number=round_number,
                budget=self._budget(fixture, profile),
            ),
            compact_json=profile.compact_json,
        )
        if sent.too_long:
            # CI keeps the shipped fixtures within the budget; this stays for a prompt that
            # grew between CI and the run.
            return [sent]
        if on_request is not None:
            on_request(tag, fixture.name, run_number, 1)
        first = self._ask(tag, sent, timeout)
        if first.stats is None or not first.stats.truncated:
            return [first]
        if cut_off_tries(online=False, temperature=temperature) < 2:
            return [first]
        if on_request is not None:
            on_request(tag, fixture.name, run_number, 2)
        return [
            first,
            replace(
                self._ask(tag, sent),
                attempt=2,
                first_cut_off=True,
                first_try=FirstTry(
                    prompt_eval_count=first.stats.prompt_eval_count,
                    eval_count=first.stats.eval_count,
                    seconds=first.seconds,
                ),
            ),
        ]

    def _ask(self, tag: str, sent: Sent, timeout: float | None = None) -> Sent:
        """Send *sent* once and return what came back, unjudged."""
        from teachers_teammate.exceptions import (  # noqa: PLC0415
            OCRDeadlineError,
            OCRError,
            OCRTimeoutError,
            OllamaUnreachableError,
        )
        from teachers_teammate.infrastructure.correction_schema import schema_json  # noqa: PLC0415
        from teachers_teammate.infrastructure.ollama_utils import (  # noqa: PLC0415
            proofreading_options,
        )

        system, user = review_request(
            sent.language, sent.text_format, sent.sent_text.text, compact_json=sent.compact_json
        )
        deadline = self.deadline_of(fixture_named(sent.fixture))
        started = time.monotonic()
        try:
            raw, stats = self._chat.chat_with_stats(
                tag,
                user,
                system=system,
                options=proofreading_options(sent.temperature, sent.num_ctx),
                timeout=self._timeout if timeout is None else timeout,
                format=schema_json(),
                total_timeout=deadline,
            )
        except OCRDeadlineError:
            # A runaway: still writing at the deadline.  It is judged as cut off, which it is as
            # far as a teacher is concerned, and asked once more like any cut-off answer.
            from teachers_teammate.infrastructure.ollama_utils import ChatStats  # noqa: PLC0415

            return replace(
                sent,
                stats=ChatStats(done_reason="length"),
                deadline_hit=True,
                system=system,
                seconds=time.monotonic() - started,
            )
        except OllamaUnreachableError as error:
            # An outage says nothing about the model: stop, so nothing is recorded as its failure.
            raise ServerUnavailableError(str(error)) from error
        except OCRError as error:  # the model or the server failed this request; judge it
            return replace(
                sent,
                request_failed=True,
                timed_out=isinstance(error, OCRTimeoutError),
                system=system,
                seconds=time.monotonic() - started,
            )
        return replace(
            sent, raw=raw, stats=stats, system=system, seconds=time.monotonic() - started
        )


def window_of(chat: ChatPort) -> int:
    """Return the context window *chat* sends at (the app's default when it does not say)."""
    from teachers_teammate.infrastructure.ollama_utils import PROOFREADING_NUM_CTX  # noqa: PLC0415

    return int(getattr(chat, "num_ctx", PROOFREADING_NUM_CTX))


def report_lines(runs: Iterator[CheckRun] | list[CheckRun], rule: CheckRule) -> list[str]:
    """Return the per-run report lines of *runs*, judged by *rule*.

    A run whose first answer was cut off and whose second one answered shows the second try's
    numbers and, after the verdict, what the first try cost: the runaway is the reason the run
    took minutes, and it would otherwise not be in the record at all.
    """
    lines: list[str] = []
    for run in runs:
        verdict = rule.verdict(run)
        line = f"  {run.fixture:<18} {run.settings_line()}  [{verdict.value or 'OK'}]"
        if run.first_try is not None:
            line += f" ({run.first_try.line()})"
        lines.append(line)
    return lines


def margin_lines(runs: list[CheckRun]) -> list[str]:
    """Return the per-model margin lines: the smallest margin on the budget fixture.

    The margin is what the window had left after the request and the answer.  It is not a
    verdict: it says how close a model came to the limit, which is what a model that passes
    today may fail on tomorrow.  Below a tenth of the window the line warns.
    """
    margins = [
        run.margin
        for run in runs
        if run.fixture == BUDGET_FIXTURE and run.prompt_eval_count and run.eval_count
    ]
    if not margins:
        return []
    smallest = min(margins)
    window = max(run.num_ctx for run in runs)
    lines = [f"  smallest margin on {BUDGET_FIXTURE}: {smallest:,} tokens"]
    if smallest < window * MARGIN_WARNING_SHARE:
        lines.append(
            f"  WARNING: less than {MARGIN_WARNING_SHARE * 100:.0f} % of the window left"
            f" ({smallest:,} of {window:,})"
        )
    return lines
