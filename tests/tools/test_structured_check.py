"""Tests of the structured-output check: the fixtures, the size, the request and the judging.

Everything here runs offline: the server is a scripted :class:`~tools.model_eval.ports.ChatPort`,
so what is checked is the check's own logic, not a model.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import re

import pytest

from teachers_teammate.domain.correction_review import tokenize
from teachers_teammate.domain.model_catalog import RequestProfile
from teachers_teammate.infrastructure.correction import COMPACT_ANSWER_RULE
from teachers_teammate.infrastructure.ollama_utils import ChatStats
from tools.model_eval.check_rule import CheckRun, Verdict
from tools.model_eval.answer_mode import AnswerMode
from tools.model_eval.app_corrector import app_corrector_factory, innermost
from tools.model_eval.structured_check import (
    BUDGET_FIXTURE,
    FIXTURES,
    Fixture,
    StructuredOutputCheck,
    margin_lines,
    report_lines,
    review_budget,
    review_request,
    text_of,
)

Answer = Callable[[str, str], tuple[str, ChatStats]]


def _stats(done_reason: str = "stop", prompt: int = 100, answer: int = 100) -> ChatStats:
    """Return the statistics of a finished answer."""
    return ChatStats(done_reason=done_reason, prompt_eval_count=prompt, eval_count=answer)


def _answer_json(*sentences: tuple[str, str]) -> str:
    """Return a valid review answer that corrects each ``(original, corrected)`` pair."""
    return json.dumps(
        {
            "sentences": [
                {
                    "original": original,
                    "corrected": corrected,
                    "changes": [
                        {
                            "original": original,
                            "corrected": corrected,
                            "category": "grammar",
                            "explanation": "Die Form ist hier nicht richtig.",
                        }
                    ],
                }
                for original, corrected in sentences
            ],
            "unclear": [],
        },
        ensure_ascii=False,
    )


def _sentences_of(text: str) -> list[str]:
    """Return the sentences of *text*, each of them an exact substring of it."""
    return [piece for piece in re.split(r"(?<=[.!?])\s+", text) if piece.strip()]


def _corrected_answer(prompt: str) -> str:
    """Return a valid answer that changes every sentence of the text in *prompt*.

    A change of punctuation only is enough for the check to see the sentence as touched, and
    the explanation is there for the runs that count it.
    """
    text = prompt.split("Text:\n\n", 1)[1]
    return _answer_json(*[(sentence, sentence + "!") for sentence in _sentences_of(text)])


class _ScriptedChat:
    """A chat port that answers from a function instead of a server."""

    def __init__(self, answer: Answer, *, num_ctx: int = 8192) -> None:
        self._answer = answer
        self.num_ctx = num_ctx
        self.requests: list[tuple[str, str, str]] = []

    def chat_with_stats(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        options: dict | None = None,
        think: bool | str = False,
        timeout: float = 120.0,
        format: dict | str | None = None,  # pylint: disable=redefined-builtin
        total_timeout: float | None = None,
    ) -> tuple[str, ChatStats]:
        """Return the scripted answer for this request."""
        self.requests.append((model, system, prompt))
        return self._answer(system, prompt)


def test_a_cut_off_answer_is_truncated_even_when_it_does_not_validate() -> None:
    """
    Given  a model that stops at the context limit in the middle of its JSON answer
    When   the check judges the run
    Then   the verdict is TRUNCATED and not INVALID, with the partial counts left at 0
    """
    chat = _ScriptedChat(
        lambda system, prompt: ('{"sentences": [{"original": "Gestern', _stats("length"))
    )

    runs = StructuredOutputCheck(chat).run("m", 0.0)

    assert {run.verdict for run in runs} == {Verdict.TRUNCATED}
    assert all(run.placed == 0 and run.total == 0 for run in runs)


def test_a_cut_off_answer_that_still_validates_keeps_its_counts() -> None:
    """
    Given  a model that stops at the context limit after a complete, valid answer
    When   the check judges the run
    Then   the verdict is TRUNCATED and the placed and total counts are filled in
    """
    answer = _answer_json(
        (
            "Gestern hat sie ins Kino gegangen mit sein Freundin.",
            "Gestern ist sie mit ihrer Freundin ins Kino gegangen.",
        )
    )
    chat = _ScriptedChat(lambda system, prompt: (answer, _stats("length")))

    runs = StructuredOutputCheck(chat).run("m", 0.0)

    assert {run.verdict for run in runs} == {Verdict.TRUNCATED}
    assert all(run.total == 1 for run in runs)


def test_cut_off_once_is_soft_and_twice_is_hard() -> None:
    """
    Given  a model whose first answer is cut off once and whose second one is usable, and a
           model that is cut off every time
    When   the check runs above temperature 0, where a second try can differ
    Then   the first spends one soft failure on it and still passes the rule, and the second is
           TRUNCATED, which fails hard
    """
    calls = {"sent": 0}

    def once(system: str, prompt: str) -> tuple[str, ChatStats]:
        calls["sent"] += 1
        if calls["sent"] == 1:
            return '{"sentences": [{"original": "Gestern', _stats("length")
        return _corrected_answer(prompt), _stats()

    retried = StructuredOutputCheck(_ScriptedChat(once))
    runs = retried.run("m", 0.2)

    assert [run.verdict for run in runs].count(Verdict.CUT_OFF_ONCE) == 1
    assert retried.passes(runs)

    always = StructuredOutputCheck(
        _ScriptedChat(lambda system, prompt: ('{"sentences": [', _stats("length")))
    )
    cut_off = always.run("m", 0.2)

    assert {run.verdict for run in cut_off} == {Verdict.TRUNCATED}
    assert not always.passes(cut_off)


@pytest.mark.parametrize("temperature", [0.0, 0.2])
def test_the_check_runs_every_fixture(temperature: float) -> None:
    """
    Given  a scripted server and a temperature
    When   the check runs
    Then   every fixture was sent, once at temperature 0 and three times above it
    """
    chat = _ScriptedChat(lambda system, prompt: ("{}", _stats()))

    runs = StructuredOutputCheck(chat).run("m", temperature)

    assert len(runs) == len(chat.requests)
    assert len({run.fixture for run in runs}) == len(FIXTURES)
    assert len(runs) == len(FIXTURES) * (3 if temperature > 0 else 1)


def test_a_model_with_two_soft_failures_gets_a_second_round() -> None:
    """
    Given  a model that answers two of its first-round requests with an invalid answer and
           every later one validly
    When   the check runs at a temperature above 0
    Then   it runs a second round of every fixture and passes, with the runs of both rounds
           marked by their round number
    """
    seen = {"count": 0}

    def answer(system: str, prompt: str) -> tuple[str, ChatStats]:
        seen["count"] += 1
        if seen["count"] <= 2:
            return "not json", _stats()
        return _corrected_answer(prompt), _stats()

    chat = _ScriptedChat(answer)
    check = StructuredOutputCheck(chat)

    runs = check.run("m", 0.2)

    assert len(runs) == 2 * len(FIXTURES) * 3
    assert {run.round for run in runs} == {1, 2}
    assert check.passes(runs)


OLD_FULL_PAGE_DE = """Am letzten Wochenende ich bin mit meine Klasse auf ein Ausflug in die Berge gefahren. Wir haben uns um sieben Uhr am Bahnhof getroffen und der Lehrer hat die Fahrkarten für alle gekauft. Weil wir haben lange warten müssen, war der Zug schon voll und wir mussten stehen. Nach zwei Stunden wir sind endlich angekommen und die Sonne hat geschienen, obwohl der Wetterbericht hatte Regen angesagt.

Zuerst sind wir ein steilen Weg hinauf gegangen, wo man aufpassen muss, weil die Steine locker liegen. Meine Freundin ist hingefallen und hat sich das Knie verletzt, aber sie hat gesagt, das tut nicht so weh. Auf dem Gipfel war es windig und wir haben unsere Jacken angezogen. Von oben wir konnten das ganze Tal sehen und die kleinen Häuser sahen wie Spielzeug aus. Ein Mitschüler hat ein Fernrohr mitgebracht und wir haben die Vögel beobachtet.

Zum Mittagessen wir hatten Brote und Äpfel dabei, aber die Butter war in der Sonne weich geworden. Danach haben wir ein Spiel gespielt, bei dem man sich die Namen merken muss. Es hat viel Spass gemacht und alle haben gelacht. Der Lehrer hat gesagt, dass wir leise sein sollen, damit die anderen Wanderer nicht gestört werden.

Auf dem Rückweg hat es angefangen zu regnen und wir sind schnell gelaufen. Weil der Weg nass war, ist eine Mädchen ausgerutscht, aber es ist nichts passiert. Im Zug haben wir müde aus dem Fenster geschaut und manche sind eingeschlafen. Als wir zu Hause angekommen sind, hat meine Mutter gefragt, wie es war, und ich habe gesagt, es war ein sehr schöner Tag. Am nächsten Tag ich hatte Muskelkater, aber das war es wert.

In der Schule haben wir danach über den Ausflug gesprochen und jeder hat einen kleinen Text darüber geschrieben. Mir hat am besten gefallen, wie wir zusammen den steilen Weg geschafft haben, weil keiner aufgegeben hat. Nächstes Jahr ich möchte wieder mitkommen, vielleicht sogar im Winter, wenn Schnee liegt. Der Lehrer hat gesagt, dass er uns dann früher Bescheid gibt, damit alle planen können.
"""

OLD_SHORT_TEXTS = {
    "plain EN": "Yesterday she have gone to the cinema with his friend. "
    "It was very nice because they was there for the first time.",
    "plain DE": "Gestern hat sie ins Kino gegangen mit sein Freundin. "
    "Es war sehr schön weil sie war dort zum ersten Mal.",
    "plain FR": "Hier elle a aller au cinéma avec son ami. "
    "C'était très bien parce que ils était là pour la première fois.",
    "Markdown": "Dear **diary**, my ~~backpack~~ passport and me went to school.\n\n"
    "- it was *very* nice\n- we have see a film",
}


def _fixture(name: str) -> Fixture:
    """Return the fixture called *name*."""
    return next(fixture for fixture in FIXTURES if fixture.name == name)


def _budget(fixture: Fixture, num_ctx: int) -> int:
    """Return the characters one review request carries for *fixture* at *num_ctx*."""
    return review_budget(fixture.language, fixture.text_format, num_ctx)


def test_the_full_page_text_is_unchanged() -> None:
    """
    Given  the full page as it was written before the fixtures became sentences
    When   the fixture's text is built
    Then   it is that text, byte for byte (the literal's closing newline is formatting, not
           text: a text does not end with a line break)
    """
    assert text_of(_fixture("full page DE")).text == OLD_FULL_PAGE_DE.rstrip("\n")


def test_the_short_fixtures_are_unchanged() -> None:
    """
    Given  the four short fixtures as they were written before the change
    When   their texts are built
    Then   they are those texts, byte for byte
    """
    for name, text in OLD_SHORT_TEXTS.items():
        assert text_of(_fixture(name)).text == text


@pytest.mark.parametrize("max_chars", [None, 8192, 16384])
def test_sentence_spans_tile_the_text(max_chars: int | None) -> None:
    """
    Given  every fixture, whole and cut to a budget
    When   its sentence spans are read
    Then   they are in order, do not overlap, and cover every non-whitespace token
    """
    for fixture in FIXTURES:
        sent = text_of(fixture, max_chars)
        spans = sent.token_spans()
        assert len(spans) == len(sent.char_spans)
        previous = 0
        for start, end in spans:
            assert start >= previous
            assert end >= start
            previous = end
        tokens = tokenize(sent.text)
        covered = [token for start, end in spans for token in tokens[start:end] if token.strip()]
        assert covered == [token for token in tokens if token.strip()]


@pytest.mark.parametrize("num_ctx", [None, 8192, 16384])
def test_every_long_text_ends_in_errors(num_ctx: int | None) -> None:
    """
    Given  the two long fixtures, whole and cut to the budget of a window
    When   their error sentences are read
    Then   at least two of them start in the last third of the text, so a model that stops
           early is found
    """
    for name in ("full page DE", "page DE at budget"):
        fixture = _fixture(name)
        budget = None if num_ctx is None else _budget(fixture, num_ctx)
        assert len(text_of(fixture, budget).late_errors) >= 2


@pytest.mark.parametrize("num_ctx", [8192, 16384])
def test_the_budget_fixture_fills_the_budget(num_ctx: int) -> None:
    """
    Given  the fixture that is sent as the longest prefix that fits
    When   it is cut to the budget of a window
    Then   the prefix is at least 90 % of the budget, and the whole text is longer than the
           budget at 16384, so the cut really happens
    """
    fixture = _fixture("page DE at budget")
    budget = _budget(fixture, num_ctx)

    assert len(text_of(fixture, budget).text) >= budget * 0.9
    assert len(text_of(fixture).text) > _budget(fixture, 16384)


def test_the_budget_fixture_is_within_its_limits() -> None:
    """
    Given  the invented page that fills the budget
    When   its paragraphs and sentences are measured
    Then   the text is 6,500 to 7,000 characters, every paragraph is 300 to 450 characters and
           carries an error, and no sentence is longer than 200 characters
    """
    fixture = _fixture("page DE at budget")

    assert 6500 <= len(text_of(fixture).text) <= 7000
    for paragraph in fixture.paragraphs:
        assert 300 <= len(" ".join(sentence.text for sentence in paragraph)) <= 450
        assert any(sentence.error for sentence in paragraph)
    assert all(len(sentence.text) <= 200 for p in fixture.paragraphs for sentence in p)


class _RecordingClient:
    """An Ollama client that records the request instead of sending it."""

    def __init__(self, answer: str = '{"sentences": [], "unclear": []}') -> None:
        self.answer = answer
        self.requests: list[dict] = []
        self.last_stats = None

    def chat(
        self,
        model: str,
        prompt: str,
        *,
        options: dict | None = None,
        think: bool | str = False,
        timeout: float = 120.0,
        format: dict | str | None = None,  # pylint: disable=redefined-builtin
        system: str = "",
    ) -> str:
        """Record the request and return the scripted answer."""
        del think
        self.requests.append(
            {"model": model, "prompt": prompt, "system": system, "options": options}
        )
        return self.answer


@pytest.mark.parametrize("num_ctx", [8192, 16384])
def test_every_fixture_fits_the_review_budget(num_ctx: int) -> None:
    """
    Given  every fixture that is sent whole, at both windows the app can use
    When   its text is measured against the app's own review budget
    Then   it fits, so the gate never sends a text the app would split
    """
    for fixture in FIXTURES:
        if fixture.fill_budget:
            continue
        budget = review_budget(fixture.language, fixture.text_format, num_ctx)
        assert len(text_of(fixture).text) <= budget


def test_the_gate_sends_the_apps_review_request() -> None:
    """
    Given  the app's own corrector, asked for a review of the same text
    When   the gate builds its request
    Then   the system instructions and the message are the same, so the gate cannot quietly
           check something the app does not send
    """
    fixture = _fixture("plain DE")
    sent = text_of(fixture)
    client = _RecordingClient()
    corrector = app_corrector_factory(client, timeout=5.0, mode=AnswerMode.REVIEW, num_ctx=8192)(
        "m", 0.2
    )

    corrector.correct(sent.text, fixture.language, text_format=fixture.text_format)

    assert len(client.requests) == 1
    assert review_request(fixture.language, fixture.text_format, sent.text) == (
        client.requests[0]["system"],
        client.requests[0]["prompt"],
    )


def test_gate_one_sends_the_compact_rule_when_the_profile_asks() -> None:
    """
    Given  a model whose profile asks for the compact-JSON rule, and one whose profile does not
    When   the gate sends its requests
    Then   the system text of the first ends with the rule and the second's never holds it, so
           the header's "request profile" line is what was really asked
    """
    asking = _ScriptedChat(lambda system, prompt: ("{}", _stats()))
    plain = _ScriptedChat(lambda system, prompt: ("{}", _stats()))

    StructuredOutputCheck(asking, compact_json="on").run("m", 0.0)
    StructuredOutputCheck(plain, compact_json="off").run("m", 0.0)

    assert asking.requests
    assert all(system.endswith(COMPACT_ANSWER_RULE) for _model, system, _prompt in asking.requests)
    assert not any(COMPACT_ANSWER_RULE in system for _model, system, _prompt in plain.requests)


def _app_budget(language: str, text_format, num_ctx: int, profile: RequestProfile) -> int:
    """Return the review budget the app's own corrector reports for *profile*."""
    corrector = app_corrector_factory(
        _RecordingClient(), timeout=5.0, mode=AnswerMode.REVIEW, num_ctx=num_ctx, profile=profile
    )("m", 0.2)
    budget = innermost(corrector).max_text_chars(language, text_format=text_format)
    assert budget is not None
    return budget


def test_the_budget_measures_the_request_that_is_sent() -> None:
    """
    Given  a model whose profile asks for the compact-JSON rule, and one whose profile does not
    When   the gate measures the budget and builds the request
    Then   the budget is the one the app's corrector reports for the same profile, and the
           longer system prompt of the compact rule leaves less room for the text
    """
    fixture = _fixture("plain DE")
    plain = RequestProfile()
    compact = RequestProfile(compact_json=True)

    assert review_budget(fixture.language, fixture.text_format, 8192, plain) == _app_budget(
        fixture.language, fixture.text_format, 8192, plain
    )
    assert review_budget(fixture.language, fixture.text_format, 8192, compact) == _app_budget(
        fixture.language, fixture.text_format, 8192, compact
    )
    assert review_budget(fixture.language, fixture.text_format, 8192, compact) < review_budget(
        fixture.language, fixture.text_format, 8192, plain
    )


def test_a_cut_off_once_line_shows_the_first_attempt() -> None:
    """
    Given  a model whose first answer is cut off after 7,214 tokens and whose second one is
           usable
    When   the check runs above temperature 0 and the run is printed
    Then   the line of the CUT_OFF_ONCE run names the first try's tokens and seconds, so the cost
           of the runaway is in the record and not only the second try's
    """
    calls = {"sent": 0}

    def once(system: str, prompt: str) -> tuple[str, ChatStats]:
        calls["sent"] += 1
        if calls["sent"] == 1:
            return '{"sentences": [{"original": "Gestern', _stats("length", prompt=956, answer=6258)
        return _corrected_answer(prompt), _stats()

    check = StructuredOutputCheck(_ScriptedChat(once))
    runs = check.run("m", 0.2)

    line = next(
        line for line in report_lines(runs, check.rule) if Verdict.CUT_OFF_ONCE.value in line
    )
    assert "(first try: 7,214 tok, " in line
    assert line.endswith("s)")


def test_an_answer_that_stops_halfway_is_partial() -> None:
    """
    Given  a model that corrects the error sentences of the first half of the full page and
           leaves the rest of the text alone
    When   the check judges the run
    Then   the verdict is PARTIAL, with five of the ten error sentences covered and none of
           the two in the last third
    """
    sent = text_of(_fixture("full page DE"))
    first_half = sent.errors[:5]
    answer = _answer_json(
        *[
            (sent.text[start:end], sent.text[start:end] + "!")
            for start, end in (sent.char_spans[index] for index in first_half)
        ]
    )
    chat = _ScriptedChat(lambda system, prompt: (answer, _stats()))

    runs = StructuredOutputCheck(chat).run("m", 0.0)

    page = next(run for run in runs if run.fixture == "full page DE")
    assert page.verdict is Verdict.OK
    assert (page.covered, page.cover_of) == (5, 10)
    assert (page.late_covered, page.late_of) == (0, 2)
    assert StructuredOutputCheck(chat).rule.verdict(page) is Verdict.PARTIAL


def test_the_margin_is_what_the_window_has_left() -> None:
    """
    Given  a run that used 100 tokens for the request and 100 for the answer at 8192
    When   its margin is read and the model's margins are summarised
    Then   the margin is 7,992 tokens, and the summary reports it without a warning
    """
    run = CheckRun(
        fixture=BUDGET_FIXTURE,
        temperature=0.2,
        verdict=Verdict.OK,
        prompt_eval_count=100,
        eval_count=100,
        num_ctx=8192,
    )

    assert run.margin == 7992
    assert margin_lines([run]) == [f"  smallest margin on {BUDGET_FIXTURE}: 7,992 tokens"]


def test_a_small_margin_is_warned_about() -> None:
    """
    Given  a run that left less than a tenth of the window free
    When   the model's margins are summarised
    Then   the summary warns, because the answer nearly did not fit
    """
    run = CheckRun(
        fixture=BUDGET_FIXTURE,
        temperature=0.2,
        verdict=Verdict.OK,
        prompt_eval_count=4000,
        eval_count=4000,
        num_ctx=8192,
    )

    lines = margin_lines([run])

    assert lines[0].endswith("192 tokens")
    assert lines[1].startswith("  WARNING: less than 10 % of the window left")


def test_a_cut_off_at_temperature_0_is_not_asked_again() -> None:
    """
    Given  a model whose first answer is cut off, checked at temperature 0
    When   the check sends that request
    Then   it is sent once, as the app does: a second try would be the same answer again
    """
    sent = {"count": 0}

    def cut_off(system: str, prompt: str) -> tuple[str, ChatStats]:
        sent["count"] += 1
        return '{"sentences": [', _stats("length")

    check = StructuredOutputCheck(_ScriptedChat(cut_off))

    attempts = check.send("m", FIXTURES[0], 0.0)

    assert len(attempts) == 1
    assert sent["count"] == 1


PINNED_FIXTURE_HASHES = {
    "plain EN": "0f3273653f0bc501a86cbed383847c75aceac2b9a3c8ada6afc5ae110aa38743",
    "plain DE": "a5a5ec5796bb59eeea9080c5afd260491d5f840a208a8b96f7bef49817968cef",
    "plain FR": "a9b2889e85163f6fba45851587167a5ceae313e05bb917f0b73c353f8fcf2376",
    "Markdown": "5169337c425948b9c429f4e79475e8f1875c08505ffe75487cec61fa6e4fa460",
    "full page DE": "7200b2192a880c2169e48022b9966f250082093437c28e0046e024e6430ccb48",
    "page DE at budget": "e7ef977d888e65d5ff35353680621f79ce2303b97e77139b5f22da0014990f3f",
}
"""The SHA-256 of every fixture's text (the budget fixture cut at 5,000 characters).

Saved answers carry the hash of the text they were made on and are refused when it differs, so a
fixture is only edited together with a new protocol version, never by accident."""


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture.name)
def test_a_fixture_text_is_the_one_the_saved_answers_were_made_on(fixture) -> None:
    """
    Given  a fixture of gate 1
    When   its text is built (the budget fixture at 5,000 characters)
    Then   its hash is the pinned one, so saved answers still describe the text a model is sent
    """
    import hashlib  # noqa: PLC0415

    sent = text_of(fixture, 5000 if fixture.fill_budget else None)

    assert (
        hashlib.sha256(sent.text.encode("utf-8")).hexdigest() == PINNED_FIXTURE_HASHES[fixture.name]
    )
