"""Gate 1 beyond the single request: continuing a saved run, judging saved answers, saving new ones.

``tools/verify_model_catalog.py`` parses arguments and prints; what it decides lives here, so the
rules can be tested without the command line:

* :func:`open_check` — the server, the probe and the check, built the same way for every command;
* :func:`continue_problem` and :func:`other_weights` — whether the answers of an earlier
  invocation still describe the setup that is about to be continued;
* :func:`smoke_gate` — whether a saved setup that failed its smoke check is finished;
* :func:`judge_saved` — the runs that saved answers make, judged by the current rule;
* :func:`answer_saver` and :func:`open_answers` — saving every answer as it arrives;
* :func:`missing_requests` and :func:`expected_requests` — how many requests are still to be sent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from teachers_teammate.domain.answer_format import ANSWER_FORMAT_VERSION
from teachers_teammate.infrastructure.ollama_utils import ChatStats

from . import defaults, gate1_answers, protocol
from .check_rule import CheckRule, CheckRun, FirstTry
from .measure import ServerProbe, tool_revision
from .ports import OllamaChatPort
from .smoke import SMOKE_FIXTURES, setup_label, smoke_decision
from .structured_check import (
    FIXTURES,
    RUNS_AT_TEMPERATURE,
    Sent,
    StructuredOutputCheck,
    fixture_named,
    judge,
    review_budget,
    sent_for,
)

if TYPE_CHECKING:
    from teachers_teammate.domain.model_catalog import RequestProfile


class StaleAnswersError(ValueError):
    """Saved answers cannot be judged: they were made on another text, request or weights."""


def open_check(
    url: str,
    *,
    num_ctx: int,
    timeout: float,
    compact_json: str,
    deadline: float | None,
) -> tuple[OllamaChatPort, ServerProbe, StructuredOutputCheck]:
    """Return the port, the probe and the check every gate-1 command is built from.

    One place, so ``structured-output``, ``smoke`` and ``--continue`` cannot be set up differently.
    """
    port = OllamaChatPort(url, num_ctx=num_ctx)
    probe = ServerProbe(url)
    check = StructuredOutputCheck(
        port,
        rule=CheckRule(),
        timeout=timeout,
        compact_json=compact_json,
        deadline=deadline,
    )
    return port, probe, check


def model_info(probe: ServerProbe, tag: str) -> dict:
    """Return the server's facts about *tag*, or ``{}`` when it cannot be asked.

    The line is advisory: a network blip between two models must not end a run of hours.
    """
    try:
        return probe.model_info(tag)
    except (OSError, ValueError):
        return {}


# ── saving answers ────────────────────────────────────────────────────────────


def answer_saver(
    tag: str, probe: ServerProbe, path: Path | None, profile: RequestProfile
) -> Callable[[Sent, CheckRun, int], None]:
    """Return the callback that appends every answer to *path*, so ``recheck`` can judge it.

    Both attempts of a cut-off answer are saved, each with its number, so ``recheck`` judges the
    pair the way the live run did.
    """
    digest = str(model_info(probe, tag).get("digest", ""))
    recorded = {"compact_json": profile.compact_json, "temperature": profile.temperature}

    def save(sent: Sent, run: CheckRun, number: int) -> None:
        if path is None:
            return
        gate1_answers.append_answer(
            path,
            gate1_answers.Answer(
                tag=tag,
                digest=digest,
                fixture=sent.fixture,
                text_sha256=gate1_answers.text_sha256(sent.sent_text.text),
                round=sent.round,
                run=number,
                request_failed=sent.request_failed,
                too_long=sent.too_long,
                done_reason=run.done_reason,
                prompt_eval_count=run.prompt_eval_count,
                eval_count=run.eval_count,
                seconds=sent.seconds,
                answer=sent.raw,
                verdict=run.verdict.value,
                attempt=sent.attempt,
                request_profile=recorded,
            ),
        )

    return save


def open_answers(
    directory: Path | str | None,
    *,
    stamp: protocol.Stamp,
    probe: ServerProbe,
    window: int,
    temperature: float | None,
    compact_json: str,
    fit_limit: str = "",
) -> Path | None:
    """Create the answers file with its header and return it, or ``None`` without a directory."""
    if not directory:
        return None
    now = datetime.now(UTC)
    header = gate1_answers.Header(
        date=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        eval_version=stamp.eval_version,
        fingerprint=stamp.fingerprint,
        answer_format_version=ANSWER_FORMAT_VERSION,
        num_ctx=window,
        temperature=temperature,
        tool_commit=tool_revision(),
        ollama_version=probe.version(),
        compact_json=compact_json,
        fit_limit=fit_limit,
    )
    path = gate1_answers.answers_path(Path(directory), window, temperature, now)
    gate1_answers.start_answers(path, header)
    return path


# ── continuing a saved run ────────────────────────────────────────────────────


def continue_problem(
    header: gate1_answers.Header,
    *,
    num_ctx: int | None,
    temperature: float | None,
    compact_json: str,
) -> str | None:
    """Return why the answers of *header* cannot be continued, or ``None`` when they can.

    Only the answers of requests that came back in time carry over, so everything that decides
    what a request is has to match: the fingerprint of the request, the answer format, the
    window, the temperature and the request profile.  The Ollama version may differ.

    Args:
        header: The header of the answers file.
        num_ctx: The window asked for on the command line, or ``None`` for the file's own.
        temperature: The temperature asked for, or ``None`` for each model's own.
        compact_json: The ``--compact-json`` choice of this invocation.
    """
    current = protocol.fingerprint_sha256()
    if header.fingerprint != current:
        return (
            f"the answers were made with fingerprint {header.fingerprint}, the current one is "
            f"{current}: the request changed, so the answers are stale"
        )
    if header.answer_format_version != ANSWER_FORMAT_VERSION:
        return (
            f"the answers were made with answer format version {header.answer_format_version}, "
            f"the current one is {ANSWER_FORMAT_VERSION}"
        )
    if num_ctx is not None and num_ctx != header.num_ctx:
        return f"the answers were made at num_ctx {header.num_ctx}, not {num_ctx}"
    if (
        temperature is not None
        and header.temperature is not None
        and temperature != header.temperature
    ):
        return f"the answers were made at temperature {header.temperature:g}, not {temperature:g}"
    if header.compact_json != compact_json:
        return (
            f"the answers were made with --compact-json {header.compact_json}, not {compact_json}"
        )
    return None


def other_weights(
    saved: Sequence[gate1_answers.Answer], probe: ServerProbe, tag: str
) -> str | None:
    """Return why *saved* answers are of other weights than the tag now holds, or ``None``.

    A tag is a name that can be re-pointed; the digest pins the weights.  Answers of a tag that
    was pulled again are answers of another model.
    """
    current = str(model_info(probe, tag).get("digest", ""))
    recorded = next((answer.digest for answer in saved if answer.digest), "")
    if not current or not recorded or current == recorded:
        return None
    return (
        f"the tag was pulled again (digest {recorded[:12]} → {current[:12]}), "
        "so the answers are of other weights"
    )


@dataclass(frozen=True)
class SmokeGate:
    """Whether a saved setup is finished, and what to tell the user about it."""

    proceed: bool
    lines: tuple[str, ...] = ()


def smoke_gate(
    tag: str,
    previous: Sequence[CheckRun],
    profile: RequestProfile,
    *,
    window: int,
    rule: CheckRule,
    force: bool,
) -> SmokeGate:
    """Return whether to finish a saved setup, judged by what its smoke runs say.

    A setup whose smoke check failed is not finished unless *force* is given, and never when a
    second try could not have cured it: a soft failure is not a verdict, a hard one is.

    The smoke check stops at the first fixture that fails twice, so a file that goes on past that
    fixture is not a smoke check's: it is an interrupted gate-1 run, whose two early soft failures
    gate 1 tolerates, and it is continued.
    """
    smoke = smoke_decision(previous, rule=rule, compact_json=profile.compact_json)
    if smoke.passed:
        return SmokeGate(True)
    shown = (f"  {smoke.line(setup_label(tag, profile, window))}",)
    if not smoke.hard and went_past(previous, smoke.fixture):
        return SmokeGate(
            True,
            (*shown, "  → the run went on past that fixture: an interrupted gate-1 run, continued"),
        )
    if smoke.hard:
        return SmokeGate(
            False, (*shown, "  → not continued: a second try cannot cure this failure")
        )
    if not force:
        return SmokeGate(
            False, (*shown, "  → not continued: the smoke check failed; pass --force to finish it")
        )
    return SmokeGate(
        True, (*shown, "  → continued anyway (--force): a soft smoke failure is not a verdict")
    )


def went_past(runs: Sequence[CheckRun], failed: str) -> bool:
    """Whether *runs* hold more than a smoke check that stopped at the fixture *failed* would.

    That is a run of a fixture the smoke check does not send or sends after *failed*, or a third
    run of *failed* itself.
    """
    if failed not in SMOKE_FIXTURES:
        return False
    position = SMOKE_FIXTURES.index(failed)
    for run in runs:
        if run.fixture not in SMOKE_FIXTURES or SMOKE_FIXTURES.index(run.fixture) > position:
            return True
    return sum(1 for run in runs if run.fixture == failed) > 2


def missing_requests(temperature: float, done: set[tuple[str, int, int]]) -> int:
    """Return how many runs of the first round *done* does not hold yet."""
    per_fixture = RUNS_AT_TEMPERATURE if temperature > 0 else 1
    return sum(
        1
        for fixture in FIXTURES
        for number in range(1, per_fixture + 1)
        if (fixture.name, 1, number) not in done
    )


def expected_requests(temperatures: Sequence[float]) -> int:
    """Return how many requests the first round of models at *temperatures* will make.

    A second round is not in the figure; it is added when it starts, because only the rule knows
    whether a model needs one.
    """
    return sum(
        len(FIXTURES) * (RUNS_AT_TEMPERATURE if temperature > 0 else 1)
        for temperature in temperatures
    )


# ── judging saved answers ─────────────────────────────────────────────────────


def temperature_of(answer: gate1_answers.Answer, header: gate1_answers.Header) -> float:
    """Return the temperature *answer* was made at: its own, else the file's, else its model's."""
    own = answer.request_profile.get("temperature")
    if own is not None:
        return float(own)
    return defaults.temperature_for(answer.tag, header.temperature)


def profile_of_answer(answer: gate1_answers.Answer, header: gate1_answers.Header) -> RequestProfile:
    """Return the request profile *answer* was made with, as the answers file recorded it."""
    return defaults.request_profile_for(
        answer.tag,
        compact_json="on" if answer.compact_json else "off",
        temperature=temperature_of(answer, header),
    )


def by_attempt(saved: Sequence[gate1_answers.Answer]) -> list[list[gate1_answers.Answer]]:
    """Return the saved answers grouped into the runs they belong to, attempts in order."""
    grouped: dict[tuple[str, int, int], list[gate1_answers.Answer]] = {}
    for answer in saved:
        grouped.setdefault((answer.fixture, answer.round, answer.run), []).append(answer)
    return [sorted(attempts, key=lambda answer: answer.attempt) for attempts in grouped.values()]


def first_try(attempts: Sequence[gate1_answers.Answer]) -> FirstTry | None:
    """Return what the first try of *attempts* cost, or ``None`` when there was only one."""
    if len(attempts) < 2:
        return None
    first = attempts[0]
    return FirstTry(
        prompt_eval_count=first.prompt_eval_count,
        eval_count=first.eval_count,
        seconds=first.seconds,
    )


def judge_saved(
    tag: str, saved: Sequence[gate1_answers.Answer], header: gate1_answers.Header
) -> list[CheckRun]:
    """Return the runs *saved* produce, judged by the current rule.

    A cut-off answer was asked once more, so one run can hold two attempts: the last one is
    judged, and it is marked as the second try when an earlier one was cut off.  That is the
    verdict the live run gave, so a recheck of the same answers reproduces it.

    The text is rebuilt with the profile the answer was made under: the compact rule lengthens
    the system prompt, so it shortens the page that fills the budget, and a text rebuilt without
    it would not be the text the model saw.

    Raises:
        StaleAnswersError: a fixture is gone, or its text changed since the answers were saved,
            so they were made on a text the model never saw.
    """
    runs: list[CheckRun] = []
    for attempts in by_attempt(saved):
        answer = attempts[-1]
        try:
            fixture = fixture_named(answer.fixture)
        except KeyError:
            raise StaleAnswersError(
                f"{tag}: no fixture called {answer.fixture!r} any more"
            ) from None
        profile = profile_of_answer(answer, header)
        sent = sent_for(
            fixture,
            num_ctx=header.num_ctx,
            temperature=temperature_of(answer, header),
            round_number=answer.round,
            budget=review_budget(fixture.language, fixture.text_format, header.num_ctx, profile),
        )
        if gate1_answers.text_sha256(sent.sent_text.text) != answer.text_sha256:
            raise StaleAnswersError(
                f"{tag}: the text of {answer.fixture!r} changed since the answers were saved, "
                "so they were made on a text the model never saw: run the check again"
            )
        runs.append(
            judge(
                replace(
                    sent,
                    raw=answer.answer,
                    stats=ChatStats(
                        done_reason=answer.done_reason,
                        prompt_eval_count=answer.prompt_eval_count,
                        eval_count=answer.eval_count,
                    ),
                    seconds=answer.seconds,
                    request_failed=answer.request_failed,
                    too_long=answer.too_long,
                    attempt=answer.attempt,
                    compact_json=answer.compact_json,
                    first_cut_off=any(earlier.done_reason == "length" for earlier in attempts[:-1]),
                    first_try=first_try(attempts),
                )
            )
        )
    return runs
