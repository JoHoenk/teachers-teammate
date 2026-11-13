"""Tests of the per-setup smoke check: the rule, the runner and the two commands that use it.

Everything here runs offline: the server is a scripted :class:`~tools.model_eval.ports.ChatPort`
that answers per fixture, so what is checked is the rule and the wiring, not a model.  The one
test that reads real answers (``test_the_smoke_rule_on_saved_answers``) skips when the local
gate-1 answers are absent.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import re

import pytest

from teachers_teammate.domain.context_budget import BASE_NUM_CTX
from teachers_teammate.exceptions import (
    OCRDeadlineError,
    OCRError,
    OCRTimeoutError,
    OllamaUnreachableError,
)
from teachers_teammate.infrastructure.correction import COMPACT_ANSWER_RULE
from teachers_teammate.infrastructure.ollama_utils import ChatStats
from tools import verify_model_catalog
from tools.model_eval import gate1_answers, gate_one, protocol, structured_check
from tools.model_eval.check_rule import CheckRule, CheckRun, Verdict
from tools.model_eval.performance import LoadedModel
from tools.model_eval.ports import ServerUnavailableError
from tools.model_eval.smoke import (
    LOAD_ALLOWANCE,
    SMOKE_DEADLINE,
    SMOKE_FIXTURES,
    TEXT_SMOKE_FIXTURES,
    Smoke,
    SmokeCheck,
    TextAnswer,
    TextSmokeCheck,
    loaded_warnings,
    setup_label,
    smoke_decision,
    text_smoke_decision,
)
from tools.model_eval.structured_check import (
    FIXTURES,
    StructuredOutputCheck,
    fixture_named,
    review_request,
    text_of,
)

CANDIDATE = "invented:1b"
"""A tag the catalog does not know, so the command only prints a pass or fail for it."""

DIGEST = "sha256:0123456789abcdef0123456789abcdef"

Answer = Callable[[str], tuple[str, ChatStats]]


def _stats(done_reason: str = "stop", prompt: int = 100, answer: int = 100) -> ChatStats:
    """Return the statistics of a finished answer."""
    return ChatStats(done_reason=done_reason, prompt_eval_count=prompt, eval_count=answer)


def _answer_json(*sentences: tuple[str, str], explain: bool = True) -> str:
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
                            "explanation": "Die Form ist hier nicht richtig." if explain else "",
                        }
                    ],
                }
                for original, corrected in sentences
            ],
            "unclear": [],
        },
        ensure_ascii=False,
    )


def _sentences_of(prompt: str) -> list[str]:
    """Return the sentences of the text *prompt* carries, each an exact substring of it."""
    text = prompt.split("Text:\n\n", 1)[1]
    return [piece for piece in re.split(r"(?<=[.!?])\s+", text) if piece.strip()]


def _ok(prompt: str) -> tuple[str, ChatStats]:
    """Return a good answer: every sentence changed, every change explained."""
    return _answer_json(
        *[(sentence, sentence + "!") for sentence in _sentences_of(prompt)]
    ), _stats()


def _too_few_placed(prompt: str) -> tuple[str, ChatStats]:
    """Return an answer whose third sentence cannot be spliced back into the text."""
    pairs = [(sentence, sentence + "!") for sentence in _sentences_of(prompt)[:2]]
    pairs.append(("Ein Satz, den der Text nicht enthält.", "Ein Satz, den der Text enthält."))
    return _answer_json(*pairs), _stats()


def _unexplained(prompt: str) -> tuple[str, ChatStats]:
    """Return an answer that inserts a word into every sentence and explains nothing."""
    pairs = [(sentence, sentence.replace(" ", " sehr ", 1)) for sentence in _sentences_of(prompt)]
    return _answer_json(*pairs, explain=False), _stats()


def _no_sentences(_prompt: str) -> tuple[str, ChatStats]:
    """Return a valid answer that lists no sentence at all."""
    return json.dumps({"sentences": [], "unclear": []}), _stats(answer=12)


def _truncated(_prompt: str) -> tuple[str, ChatStats]:
    """Return an answer the window cut off in the middle of its JSON."""
    return '{"sentences": [{"original": "Gestern', _stats("length", answer=8192)


def _collapse(prompt: str) -> tuple[str, ChatStats]:
    """Return an answer that covers only the first two sentences: the collapse signature."""
    pairs = [(sentence, sentence + "!") for sentence in _sentences_of(prompt)[:2]]
    return _answer_json(*pairs), _stats(answer=310)


def _text_ok(name: str) -> Answer:
    """Return an answer that is the text of the fixture *name*, with one word corrected."""
    source = text_of(fixture_named(name)).text

    def answer(_prompt: str) -> tuple[str, ChatStats]:
        """Return the corrected text, which is what text mode answers with."""
        return source.replace("sein Freundin", "seiner Freundin"), _stats()

    return answer


def _failing(_prompt: str) -> tuple[str, ChatStats]:
    """Fail the request, as a model that is not pulled or does not fit the card does."""
    raise OCRError("the request did not come back")


def _unreachable(_prompt: str) -> tuple[str, ChatStats]:
    """Fail the request as an Ollama server that is not running does."""
    raise OllamaUnreachableError("cannot connect to Ollama at http://127.0.0.1:11434")


def _timed_out(_prompt: str) -> tuple[str, ChatStats]:
    """Fail the request as a model that never produced a first token does."""
    raise OCRTimeoutError("No output within 120.0s — the model may still be loading")


def _runaway(_prompt: str) -> tuple[str, ChatStats]:
    """Fail the request as the deadline does for a model that is still writing."""
    raise OCRDeadlineError("the answer was still being written after 240s")


class _ScriptedChat:
    """A chat port that answers per fixture from a queue of scripted answers.

    The last answer of a queue is reused, so a fixture that always answers the same way needs
    one entry.  The fixture is recognised by its text, which the prompt carries.
    """

    def __init__(
        self, answers: dict[str, list[Answer]] | None = None, *, num_ctx: int = BASE_NUM_CTX
    ) -> None:
        self._answers = {name: list(queue) for name, queue in (answers or {}).items()}
        self.num_ctx = num_ctx
        self.requests: list[tuple[str, str, str]] = []
        self.timeouts: list[float] = []
        self.deadlines: list[float | None] = []

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
        """Return the scripted answer of the fixture this prompt carries."""
        self.requests.append((model, system, prompt))
        self.timeouts.append(timeout)
        self.deadlines.append(total_timeout)
        queue = self._answers.get(self._fixture_of(prompt), [_ok])
        return (queue.pop(0) if len(queue) > 1 else queue[0])(prompt)

    def _fixture_of(self, prompt: str) -> str:
        """Return the name of the fixture whose text the prompt carries, whole or cut to the window."""
        for fixture in FIXTURES:
            text = text_of(fixture).text
            if text in prompt or text[:200] in prompt:
                return fixture.name
        raise AssertionError("the prompt carries no fixture")

    def prompts_of(self, fixture: str) -> list[str]:
        """Return the prompts that carried *fixture*."""
        text = text_of(fixture_named(fixture)).text
        return [prompt for _model, _system, prompt in self.requests if text in prompt]


class _Probe:
    """A server that answers without a server."""

    def __init__(self, url: str = "", loaded: tuple[LoadedModel, ...] = ()) -> None:
        self.url = url
        self._loaded = loaded

    def version(self) -> str:
        """Return the version of the invented server."""
        return "0.40.13"

    def model_info(self, tag: str) -> dict:
        """Return the digest and details of the invented model."""
        return {"digest": DIGEST, "quantization": "Q4_K_M", "parameter_size": "1B", "family": "x"}

    def loaded(self) -> tuple[LoadedModel, ...]:
        """Return what the invented server holds in memory."""
        return self._loaded


def _smoke_of(chat: _ScriptedChat, *, compact_json: str = "catalog", probe=None) -> Smoke:
    """Run the smoke check of one setup against *chat* and return its result."""
    check = StructuredOutputCheck(chat, compact_json=compact_json)
    return SmokeCheck(check, probe=probe).run(CANDIDATE, 0.2)


def test_one_unlucky_run_does_not_fail_a_setup() -> None:
    """
    Given  a model that places too few sentences once and answers well on the second try
    When   the smoke check runs
    Then   the setup passes, with a warning that the fixture needed its retry
    """
    chat = _ScriptedChat({"plain DE": [_too_few_placed, _ok]})

    smoke = _smoke_of(chat)

    assert smoke.passed
    assert "plain DE: failed once, the second try answered" in smoke.warnings
    assert len(chat.prompts_of("plain DE")) == 2


def test_the_same_fixture_failing_twice_fails_the_setup() -> None:
    """
    Given  a model that places 2 of 3 sentences on the plain German fixture, twice in a row
    When   the smoke check runs
    Then   the setup fails with that reason, and the fixture was asked exactly twice
    """
    chat = _ScriptedChat({"plain DE": [_too_few_placed, _too_few_placed]})

    smoke = _smoke_of(chat)

    assert not smoke.passed
    assert smoke.reason == "plain DE: placed 2 of 3 sentences, twice"
    assert len(chat.prompts_of("plain DE")) == 2
    assert smoke.requests == 2


def test_a_full_page_collapse_fails_at_once() -> None:
    """
    Given  a model that answers one sentence of the full page, as qwen3.5:9b did with the rule
    When   the smoke check runs with the compact rule
    Then   the setup fails at once, naming the collapse and the rule to try instead
    """
    chat = _ScriptedChat({"full page DE": [_collapse]})

    smoke = _smoke_of(chat, compact_json="on")

    assert not smoke.passed
    assert smoke.hard
    assert "collapse" in smoke.reason
    assert "try --compact-json off" in smoke.reason
    assert len(chat.prompts_of("full page DE")) == 1


def test_a_runaway_fails_after_the_second_try() -> None:
    """
    Given  a model that is cut off at the window on the full page, twice
    When   the smoke check runs
    Then   the setup fails as cut off twice, and the full page was asked twice — each run with
           the app's own second try, so four requests
    """
    chat = _ScriptedChat({"full page DE": [_truncated, _truncated]})

    smoke = _smoke_of(chat)

    assert not smoke.passed
    assert smoke.reason == "full page DE: cut off at the window, twice"
    assert len(chat.prompts_of("full page DE")) == 4


def test_a_refused_request_fails_at_once() -> None:
    """
    Given  a request the server refuses, as for a model that is not pulled
    When   the smoke check runs
    Then   the setup fails at once and hard, because a second try meets the same fault
    """
    chat = _ScriptedChat({"plain DE": [_failing]})

    smoke = _smoke_of(chat)

    assert not smoke.passed
    assert smoke.hard
    assert smoke.reason == "plain DE: the request failed"
    assert len(chat.prompts_of("plain DE")) == 1


def test_a_request_that_waited_in_vain_is_retried_and_left_out_of_the_runs() -> None:
    """
    Given  a first request that gets no first token in time and a second one that answers
    When   the smoke check runs
    Then   the setup passes with a warning, and the timed-out run is not handed to gate 1, which
           asks that run again under its own timeout
    """
    chat = _ScriptedChat({"plain DE": [_timed_out, _ok]})

    smoke = _smoke_of(chat)

    assert smoke.passed
    assert "plain DE: failed once, the second try answered" in smoke.warnings
    assert ("plain DE", 1, 1) not in smoke.done
    assert ("plain DE", 1, 2) in smoke.done
    assert all(not run.timed_out for run in smoke.runs)


def test_two_requests_that_waited_in_vain_are_a_soft_failure() -> None:
    """
    Given  a model that gives no first token twice in a row
    When   the smoke check runs
    Then   the setup fails, softly: gate 1 could still finish it under its longer timeout
    """
    chat = _ScriptedChat({"plain DE": [_timed_out, _timed_out]})

    smoke = _smoke_of(chat)

    assert not smoke.passed
    assert not smoke.hard
    assert smoke.reason == "plain DE: no first token within the timeout, twice"
    assert smoke.runs == ()


def test_the_first_request_may_wait_for_the_model_to_load() -> None:
    """
    Given  a smoke check of a model that is not in memory yet
    When   it sends its first request
    Then   that request waits the load allowance longer for its first token, and the rest do not
    """
    chat = _ScriptedChat()

    _smoke_of(chat)

    assert chat.timeouts[0] == 300.0 + LOAD_ALLOWANCE
    assert set(chat.timeouts[1:]) == {300.0}


def test_a_runaway_is_abandoned_at_the_deadline_and_fails_after_the_second_try() -> None:
    """
    Given  a model that is still writing when the deadline comes, on every try
    When   the smoke check runs with a deadline
    Then   each try is abandoned, asked once more like any cut-off answer, and the setup fails as
           a runaway, in four requests and not in an hour of waiting
    """
    chat = _ScriptedChat({"plain DE": [_runaway]})
    check = StructuredOutputCheck(chat, deadline=SMOKE_DEADLINE)

    smoke = SmokeCheck(check).run(CANDIDATE, 0.2)

    assert not smoke.passed
    assert smoke.hard
    assert smoke.reason == "plain DE: still writing at the deadline, twice (a runaway)"
    assert len(chat.prompts_of("plain DE")) == 4


def test_a_runaway_that_the_second_try_answers_passes_with_a_warning() -> None:
    """
    Given  a first try that runs away and a second one that answers, as the app asks again
    When   the smoke check runs
    Then   the setup passes with a warning, like any cut-off answer the second try cured
    """
    chat = _ScriptedChat({"plain DE": [_runaway, _ok]})

    smoke = SmokeCheck(StructuredOutputCheck(chat, deadline=SMOKE_DEADLINE)).run(CANDIDATE, 0.2)

    assert smoke.passed
    assert "plain DE: the first answer was cut off, the second one answered" in smoke.warnings


def test_the_long_fixtures_get_two_and_a_half_times_the_deadline() -> None:
    """
    Given  a smoke check with a deadline of 240 s
    When   it sends the short fixtures and the full page
    Then   the short ones carry 240 s and the full page 600 s, and with no deadline none is sent
    """
    chat = _ScriptedChat()

    SmokeCheck(StructuredOutputCheck(chat, deadline=SMOKE_DEADLINE)).run(CANDIDATE, 0.2)

    assert chat.deadlines[:-1] == [SMOKE_DEADLINE] * (len(chat.deadlines) - 1)
    assert chat.deadlines[-1] == SMOKE_DEADLINE * 2.5
    unlimited = _ScriptedChat()
    SmokeCheck(StructuredOutputCheck(unlimited)).run(CANDIDATE, 0.2)
    assert set(unlimited.deadlines) == {None}


def test_a_cut_off_then_a_good_answer_passes_with_a_warning() -> None:
    """
    Given  a first answer the window cut off and a second one that answered, as the app asks
    When   the smoke check runs
    Then   the setup passes, with a warning that the first answer was cut off
    """
    chat = _ScriptedChat({"plain DE": [_truncated, _ok]})

    smoke = _smoke_of(chat)

    assert smoke.passed
    assert "plain DE: the first answer was cut off, the second one answered" in smoke.warnings
    assert len(chat.prompts_of("plain DE")) == 2


def test_an_unexplained_markdown_answer_fails() -> None:
    """
    Given  a model that changes the Markdown text without explaining a single change, twice
    When   the smoke check runs
    Then   the setup fails on the Markdown fixture
    """
    chat = _ScriptedChat({"Markdown": [_unexplained, _unexplained]})

    smoke = _smoke_of(chat)

    assert not smoke.passed
    assert smoke.reason.startswith("Markdown: explained 0 of")


def test_a_single_no_sentences_answer_warns() -> None:
    """
    Given  one answer that lists no sentence, then a good one
    When   the smoke check runs
    Then   the setup passes: one empty answer is a chance event, not a systematic fault
    """
    chat = _ScriptedChat({"plain DE": [_no_sentences, _ok]})

    smoke = _smoke_of(chat)

    assert smoke.passed
    assert "plain DE: failed once, the second try answered" in smoke.warnings


def test_the_cheap_fixtures_run_first_and_a_failure_stops_the_rest() -> None:
    """
    Given  a model that fails the cheapest fixture twice
    When   the smoke check runs
    Then   the full page is never sent, so a broken setup costs two requests and not ten
    """
    chat = _ScriptedChat({"plain DE": [_too_few_placed, _too_few_placed]})

    smoke = _smoke_of(chat)

    assert not smoke.passed
    assert len(chat.requests) == 2
    assert chat.prompts_of("full page DE") == []
    assert smoke.requests == 2


def test_smoke_sends_gate_ones_request() -> None:
    """
    Given  a setup that passes
    When   the smoke check sends its first fixture
    Then   the system and user text are the ones the structured-output check sends
    """
    chat = _ScriptedChat()

    _smoke_of(chat)

    fixture = fixture_named(SMOKE_FIXTURES[0])
    system, user = review_request(fixture.language, fixture.text_format, text_of(fixture).text)
    assert chat.requests[0][1:] == (system, user)


def test_the_profile_on_the_wire_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  a request that drops the compact rule although the profile asks for it
    When   the smoke check runs with the compact rule on
    Then   the setup fails, naming the request that is not the profile's
    """

    def plain_request(language, text_format, text, *, compact_json=False):
        """Build the plain request whatever the profile asks, as the v3 run did."""
        return review_request(language, text_format, text, compact_json=False)

    monkeypatch.setattr(structured_check, "review_request", plain_request)
    chat = _ScriptedChat()

    smoke = _smoke_of(chat, compact_json="on")

    assert not smoke.passed
    assert smoke.hard
    assert "lacks the compact rule" in smoke.reason
    assert len(chat.requests) == 1


def test_the_compact_rule_reaches_the_wire() -> None:
    """
    Given  a setup that asks with the compact rule
    When   the smoke check sends its first request
    Then   the system text carries the rule, so the profile is what was sent
    """
    chat = _ScriptedChat()

    smoke = _smoke_of(chat, compact_json="on")

    assert smoke.passed
    assert chat.requests[0][1].endswith(COMPACT_ANSWER_RULE)


def test_a_setup_that_runs_partly_on_the_cpu_warns() -> None:
    """
    Given  a model the server holds partly in main memory and at another window
    When   the smoke check runs
    Then   the setup passes with both warnings, because neither is a fault of the answer
    """
    loaded = (
        LoadedModel("invented:1b", size_bytes=1000, size_vram_bytes=500, context_length=4096),
    )
    chat = _ScriptedChat()

    smoke = _smoke_of(chat, probe=_Probe(loaded=loaded))

    assert smoke.passed
    assert any("partly on the processor" in warning for warning in smoke.warnings)
    assert any("4,096 tokens, not the 8,192 asked" in warning for warning in smoke.warnings)


def test_the_loaded_model_is_matched_by_tag() -> None:
    """
    Given  a server that holds another model, and one whose tag is written with ``:latest``
    When   the loaded models are read for a tag
    Then   only the same model is reported, and nothing is said about the other
    """
    loaded = (
        LoadedModel("other:1b", size_bytes=1000, size_vram_bytes=1000, context_length=8192),
        LoadedModel(
            "invented:1b:latest", size_bytes=1000, size_vram_bytes=1000, context_length=8192
        ),
    )

    assert loaded_warnings(loaded, "invented:1b", 8192) == ()
    assert loaded_warnings(loaded[:1], "invented:1b", 8192) == ()


def test_the_setup_label_names_the_profile_and_the_window() -> None:
    """
    Given  a model asked with the compact rule at 0.2 and a window of 8192
    When   the setup is named for the record
    Then   the label carries the rule, the temperature and the window, so two setups differ
    """
    profile = verify_model_catalog.defaults.request_profile_for(
        CANDIDATE, compact_json="on", temperature=0.2
    )

    assert setup_label(CANDIDATE, profile, 8192) == f"{CANDIDATE} (compact JSON, T=0.2, 8192)"


def test_the_rule_reads_the_runs_of_one_setup() -> None:
    """
    Given  a run that failed and its retry that passed
    When   the rule is applied to the two runs
    Then   the setup passes with a warning, and a single failing run alone would not
    """
    failed = CheckRun(fixture="plain DE", temperature=0.2, verdict=Verdict.OK, placed=2, total=3)
    passed = CheckRun(fixture="plain DE", temperature=0.2, verdict=Verdict.OK, placed=3, total=3)

    assert smoke_decision([failed]).passed is False
    assert smoke_decision([failed, passed]).passed is True


def test_the_text_smoke_fails_an_answer_that_is_not_the_text() -> None:
    """
    Given  a text-mode answer that is an explanation instead of the corrected text, twice
    When   the text-mode rule is applied
    Then   the setup fails, because the answer is not recognisably the input
    """
    source = "Gestern hat sie ins Kino gegangen mit sein Freundin."
    answers = [
        TextAnswer("plain DE", source, "Hier ist die korrigierte Version: " + source * 3),
        TextAnswer("plain DE", source, "Hier ist die korrigierte Version: " + source * 3),
    ]

    smoke = text_smoke_decision(answers)

    assert not smoke.passed
    assert "not the corrected text" in smoke.reason


def test_the_text_smoke_fails_a_runaway_at_the_deadline() -> None:
    """
    Given  a text-mode model that is still writing at the deadline, on both tries
    When   the text smoke check runs
    Then   the setup fails as a runaway after two requests
    """
    chat = _ScriptedChat({"plain DE": [_runaway]})

    smoke = TextSmokeCheck(chat).run(CANDIDATE, 0.2)

    assert not smoke.passed
    assert smoke.reason == "plain DE: still writing at the deadline (a runaway), twice"
    assert chat.deadlines == [SMOKE_DEADLINE, SMOKE_DEADLINE]


def test_the_text_smoke_warns_on_one_empty_answer() -> None:
    """
    Given  one empty text-mode answer and a good one on the retry
    When   the text-mode rule is applied
    Then   the setup passes with a warning, as the review-mode rule does
    """
    source = "Gestern hat sie ins Kino gegangen mit sein Freundin."
    answers = [
        TextAnswer("plain DE", source, ""),
        TextAnswer("plain DE", source, "Gestern ist sie mit ihrer Freundin ins Kino gegangen."),
    ]

    smoke = text_smoke_decision(answers)

    assert smoke.passed
    assert "plain DE: failed once, the second try answered" in smoke.warnings


def test_the_text_smoke_fails_a_markdown_answer_that_lost_its_marks() -> None:
    """
    Given  a Markdown text whose answer dropped the marks, twice
    When   the text-mode rule is applied
    Then   the setup fails, because the marks are what the teacher wrote
    """
    source = "Dear **diary**, my ~~backpack~~ passport went to school."
    answers = [
        TextAnswer(
            "Markdown",
            source,
            "Dear diary, my backpack passport went to school.",
            text_format="markdown",
        ),
        TextAnswer(
            "Markdown",
            source,
            "Dear diary, my backpack passport went to school.",
            text_format="markdown",
        ),
    ]

    smoke = text_smoke_decision(answers)

    assert not smoke.passed
    assert "lost the Markdown marks" in smoke.reason


def test_the_text_smoke_sends_the_apps_text_request() -> None:
    """
    Given  a setup that only runs in text mode
    When   the text-mode smoke check sends its fixtures
    Then   it sends the app's text-mode request, without a schema
    """
    chat = _ScriptedChat({name: [_text_ok(name)] for name in TEXT_SMOKE_FIXTURES})

    smoke = TextSmokeCheck(chat).run(CANDIDATE, 0.2)

    assert smoke.passed
    assert len(chat.requests) == 3
    system, prompt = chat.requests[0][1:]
    assert "JSON" not in system
    assert "Gestern hat sie ins Kino gegangen mit sein Freundin." in prompt


# ── the command line ──────────────────────────────────────────────────────────


def _install(monkeypatch: pytest.MonkeyPatch, chat: _ScriptedChat, probe=None) -> _ScriptedChat:
    """Make the command talk to a scripted port and probe, and return the port."""
    for module in (verify_model_catalog, gate_one):
        monkeypatch.setattr(module, "OllamaChatPort", lambda url, num_ctx: chat)
        monkeypatch.setattr(module, "ServerProbe", lambda url: probe or _Probe(url))
    return chat


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    """Run the command with *argv* and return its exit code and what it printed."""
    code = verify_model_catalog.main(list(argv))
    return code, capsys.readouterr().out


def _smoke_argv(answers: Path, *extra: str) -> list[str]:
    """Return the arguments of a smoke run over the candidate, saving its answers."""
    return [
        "smoke",
        "--ollama-url",
        "http://127.0.0.1:1",
        "--temperature",
        "0.2",
        "--tag",
        CANDIDATE,
        "--answers",
        str(answers),
        "--fit-limit",
        "off",
        *extra,
    ]


def _structured_argv(*extra: str) -> list[str]:
    """Return the arguments of a structured-output run over the candidate."""
    return [
        "structured-output",
        "--ollama-url",
        "http://127.0.0.1:1",
        "--temperature",
        "0.2",
        "--tag",
        CANDIDATE,
        "--fit-limit",
        "off",
        *extra,
    ]


def test_the_smoke_command_prints_a_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a setup that passes
    When   ``verify_model_catalog smoke`` runs
    Then   it prints the setup's line and a table with the result, the requests and the reason
    """
    _install(monkeypatch, _ScriptedChat())

    code, out = _run(capsys, *_smoke_argv(tmp_path))

    assert code == 0
    assert f"smoke {CANDIDATE} (plain, T=0.2, 8192): PASS" in out
    assert "result  requests  seconds  reason" in out
    assert "no sign of a systematic fault; gate 1 decides" in out


def test_the_smoke_command_fails_a_broken_setup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a setup that fails the cheapest fixture twice
    When   ``verify_model_catalog smoke`` runs
    Then   it exits with code 1 and the table says FAIL with the reason
    """
    _install(monkeypatch, _ScriptedChat({"plain DE": [_too_few_placed, _too_few_placed]}))

    code, out = _run(capsys, *_smoke_argv(tmp_path))

    assert code == 1
    assert "FAIL" in out
    assert "placed 2 of 3 sentences, twice" in out


def test_structured_output_skips_the_runs_of_a_failed_smoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a setup whose smoke check fails
    When   ``structured-output`` runs without ``--no-smoke``
    Then   the eighteen runs are not sent; a hard failure is a FAIL as it stands, and a soft one
           is "no verdict", because gate 1 tolerates soft failures
    """
    soft = _install(monkeypatch, _ScriptedChat({"plain DE": [_too_few_placed, _too_few_placed]}))

    code, out = _run(capsys, *_structured_argv())

    assert code == 1
    assert "skipped: the smoke check failed" in out
    assert f"{CANDIDATE}: NO VERDICT" in out
    assert len(soft.requests) == 2

    hard = _install(monkeypatch, _ScriptedChat({"plain DE": [_failing]}))

    code, out = _run(capsys, *_structured_argv())

    assert code == 1
    assert f"{CANDIDATE}: FAIL" in out
    assert len(hard.requests) == 1


def test_an_interrupted_gate_one_run_is_continued_past_its_two_early_soft_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  an answers file of a straight run in which the first two runs of one fixture failed
           softly and the third answered, as gate 1 tolerates
    When   ``--continue`` is given that file without ``--force``
    Then   it is not taken for a failed smoke check: the run went on past that fixture, so it is
           continued
    """
    _install(monkeypatch, _ScriptedChat({"plain DE": [_too_few_placed, _too_few_placed, _ok]}))
    _run(capsys, *_structured_argv("--no-smoke", "--answers", str(tmp_path)))
    path = next(tmp_path.glob("*.jsonl"))

    _code, out = _run(capsys, *_structured_argv("--continue", str(path)))

    assert "interrupted gate-1 run, continued" in out
    assert "pass --force" not in out


def test_a_smoke_that_stopped_at_a_fixture_still_needs_force(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  an answers file of a smoke check that stopped at the first fixture
    When   ``--continue`` is given that file without ``--force``
    Then   it is refused, because nothing in the file went on past the failure
    """
    _install(monkeypatch, _ScriptedChat({"plain DE": [_too_few_placed, _too_few_placed]}))
    _run(capsys, *_smoke_argv(tmp_path))
    path = next(tmp_path.glob("*.jsonl"))

    _code, out = _run(capsys, *_structured_argv("--continue", str(path)))

    assert "pass --force to finish it" in out


def test_a_request_that_waited_in_vain_is_not_saved_as_a_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a smoke run whose first request got no first token and whose retry answered
    When   its answers are read
    Then   the file holds the retry as run 2 and no failed request, so gate 1 asks run 1 again
    """
    _install(monkeypatch, _ScriptedChat({"plain DE": [_timed_out, _ok]}))
    _run(capsys, *_smoke_argv(tmp_path))
    path = next(tmp_path.glob("*.jsonl"))

    _header, answers = gate1_answers.read_answers(path)

    assert not any(answer.request_failed for answer in answers)
    assert [(a.fixture, a.run) for a in answers if a.fixture == "plain DE"] == [("plain DE", 2)]


def test_no_smoke_skips_the_stage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  ``--no-smoke``
    When   ``structured-output`` runs
    Then   the smoke check is not run and every fixture is sent straight away
    """
    chat = _install(monkeypatch, _ScriptedChat())

    code, out = _run(capsys, *_structured_argv("--no-smoke"))

    assert code == 0
    assert "smoke " not in out
    assert len(chat.requests) == len(FIXTURES) * 3


def test_continue_sends_only_the_runs_the_smoke_did_not(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a smoke run that made run 1 of five fixtures
    When   ``structured-output --continue`` finishes the setup
    Then   it sends the thirteen missing runs, eighteen in all, and the verdict is the same as
           a straight eighteen-run check on the same answers
    """
    chat = _install(monkeypatch, _ScriptedChat())
    _run(capsys, *_smoke_argv(tmp_path))
    path = next(tmp_path.glob("*.jsonl"))
    assert len(chat.requests) == len(SMOKE_FIXTURES)
    chat.requests.clear()

    code, out = _run(capsys, *_structured_argv("--continue", str(path)))

    assert code == 0
    assert len(chat.requests) == 18 - len(SMOKE_FIXTURES)
    assert "→ PASS" in out
    straight = _install(monkeypatch, _ScriptedChat())
    _code, straight_out = _run(capsys, *_structured_argv("--no-smoke"))
    assert len(straight.requests) == 18
    assert f"{CANDIDATE}: PASS" in straight_out


def test_continue_counts_a_retry_as_run_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a smoke run in which one fixture failed once and passed on its retry
    When   ``structured-output --continue`` finishes the setup
    Then   that fixture gets one more request and not two, so the setup ends with three runs
    """
    chat = _install(monkeypatch, _ScriptedChat({"plain DE": [_too_few_placed, _ok]}))
    _run(capsys, *_smoke_argv(tmp_path))
    path = next(tmp_path.glob("*.jsonl"))
    assert len(chat.prompts_of("plain DE")) == 2
    chat.requests.clear()

    code, _out = _run(capsys, *_structured_argv("--continue", str(path)))

    assert code == 0
    assert len(chat.prompts_of("plain DE")) == 1
    assert len(chat.requests) == 18 - 6


def test_continue_refuses_answers_made_under_another_setup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  answers made with the plain request
    When   ``--continue`` is asked for the compact rule
    Then   it refuses the file by name and sends nothing
    """
    chat = _install(monkeypatch, _ScriptedChat())
    _run(capsys, *_smoke_argv(tmp_path, "--compact-json", "off"))
    path = next(tmp_path.glob("*.jsonl"))
    chat.requests.clear()

    code, out = _run(capsys, *_structured_argv("--continue", str(path), "--compact-json", "on"))

    assert code == 2
    assert "the answers were made with --compact-json off, not on" in out
    assert chat.requests == []


def test_continue_finishes_a_failed_smoke_when_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a setup whose smoke check failed softly, twice on one fixture
    When   ``--continue`` runs without ``--force`` and then with it
    Then   the first refuses to finish it and the second sends the rest and gives a verdict
    """
    chat = _install(monkeypatch, _ScriptedChat({"plain DE": [_too_few_placed, _too_few_placed]}))
    _run(capsys, *_smoke_argv(tmp_path))
    path = next(tmp_path.glob("*.jsonl"))
    chat.requests.clear()

    code, out = _run(capsys, *_structured_argv("--continue", str(path)))

    assert code == 1
    assert "not continued: the smoke check failed; pass --force to finish it" in out
    assert chat.requests == []

    code, out = _run(capsys, *_structured_argv("--continue", str(path), "--force"))

    assert "continued anyway (--force)" in out
    assert len(chat.requests) == 18 - 2
    assert "→ FAIL" in out


def test_continue_does_not_finish_a_hard_smoke_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a setup whose smoke check failed because the request did not come back
    When   ``--continue --force`` runs
    Then   it still refuses: a second try cannot cure that failure, so it is a verdict
    """
    chat = _install(monkeypatch, _ScriptedChat({"plain DE": [_failing]}))
    _run(capsys, *_smoke_argv(tmp_path))
    path = next(tmp_path.glob("*.jsonl"))
    chat.requests.clear()

    code, out = _run(capsys, *_structured_argv("--continue", str(path), "--force"))

    assert code == 1
    assert "not continued: a second try cannot cure this failure" in out
    assert chat.requests == []


def test_the_smoke_rule_on_saved_answers() -> None:
    """
    Given  the answers of the gate-1 runs of 2026-11-11 (local, not in the repository)
    When   the smoke rule is replayed on them, each run as the smoke run and the next as its
           retry, and a setup fails when any of the three pairings fails
    Then   no model that passed gate 1 is rejected, every recorded systematic failure is, and on
           the compact rule the rule and gate 1 agree on all seven models
    """
    plain = sorted(Path.home().glob("gec-testset/gate1-2026-11/*.jsonl")) + sorted(
        Path.home().glob("gec-testset/gate1-2026-11-v3/off/*.jsonl")
    )
    compact = sorted(Path.home().glob("gec-testset/gate1-2026-11-v3/on-1/*.jsonl"))
    if not plain or not compact:
        pytest.skip("the gate-1 answers of 2026-11-11 are not on this machine")

    rejected = {path: _rejected_setups(path) for path in plain + compact}

    for path in plain:
        for tag in ("qwen3:4b", "gemma4:e4b", "ministral-3:14b"):
            assert tag not in rejected[path], f"{tag} was rejected on {path.name}"
    assert any("gpt-oss:20b" in rejected[path] for path in plain)
    assert any("ministral-3:8b" in rejected[path] for path in plain)
    assert any("gemma4:e2b" in rejected[path] for path in plain)
    assert any("qwen3.5:9b" in rejected[path] for path in compact)
    for path in compact:
        for tag in ("gemma4:e4b", "gemma4:12b"):
            assert tag not in rejected[path], f"{tag} was rejected on {path.name}"


def _rejected_setups(path: Path) -> set[str]:
    """Return the models the smoke rule rejects on the answers of *path*.

    Each fixture's runs are replayed three times, each run as the smoke run and the next as its
    retry; a setup is rejected when any of the three pairings fails, which is the worst case for
    a good model.
    """
    header, answers = gate1_answers.read_answers(path)
    compact = header.compact_json == "on"
    rejected: set[str] = set()
    for tag, saved in gate1_answers.by_tag(answers).items():
        keys = [(attempts[-1].fixture, attempts[-1].run) for attempts in gate_one.by_attempt(saved)]
        runs = gate_one.judge_saved(tag, saved, header)
        per_fixture: dict[str, dict[int, CheckRun]] = {}
        for (fixture, number), run in zip(keys, runs, strict=True):
            per_fixture.setdefault(fixture, {})[number] = run
        for by_run in per_fixture.values():
            numbers = sorted(by_run)
            if len(numbers) < 2:
                continue
            for index, number in enumerate(numbers):
                retry = numbers[(index + 1) % len(numbers)]
                if not smoke_decision([by_run[number], by_run[retry]], compact_json=compact).passed:
                    rejected.add(tag)
    return rejected


def test_an_unreachable_server_stops_the_review_smoke_check_instead_of_failing_the_model() -> None:
    """
    Given  a server that cannot be reached
    When   the smoke check of a review setup runs
    Then   the check stops with ServerUnavailableError and no verdict: an outage says nothing
           about the model, and a "failed" setup would be skipped, or fail gate 1, for good
    """
    chat = _ScriptedChat({"plain DE": [_unreachable]})

    with pytest.raises(ServerUnavailableError):
        _smoke_of(chat)


def test_an_unreachable_server_stops_the_text_smoke_check_too() -> None:
    """
    Given  a server that cannot be reached
    When   the smoke check of a text-mode setup runs
    Then   it stops with ServerUnavailableError, as the review smoke check does
    """
    chat = _ScriptedChat({"plain DE": [_unreachable]})

    with pytest.raises(ServerUnavailableError):
        TextSmokeCheck(chat).run(CANDIDATE, 0.2)


def test_a_tool_bug_is_not_a_failed_model() -> None:
    """
    Given  a chat port that raises an error no server or model can cause
    When   the smoke check runs
    Then   the error reaches the developer instead of becoming "the request failed"
    """

    def broken(_prompt: str) -> tuple[str, ChatStats]:
        raise TypeError("a bug in the tool")

    with pytest.raises(TypeError):
        _smoke_of(_ScriptedChat({"plain DE": [broken]}))


def test_next_step_is_the_one_rule_for_the_live_check_and_the_replay() -> None:
    """
    Given  a usable run, an unlucky one, a request the server refused, and the retry of an
           unlucky one
    When   the smoke check asks what to do after each
    Then   a usable run passes, an unlucky first run is asked again, a refused request fails at
           once with its reason, and an unlucky retry fails the fixture
    """
    from tools.model_eval.smoke import StepKind, next_step  # noqa: PLC0415

    good = CheckRun(fixture="plain DE", temperature=0.2, verdict=Verdict.OK, placed=3, total=3)
    unlucky = CheckRun(fixture="plain DE", temperature=0.2, verdict=Verdict.OK, placed=2, total=3)
    refused = CheckRun(
        fixture="plain DE", temperature=0.2, verdict=Verdict.REQUEST_FAILED, placed=0, total=3
    )

    assert next_step(good).kind is StepKind.PASS
    assert next_step(unlucky).kind is StepKind.RETRY
    assert next_step(refused).kind is StepKind.FAIL
    assert next_step(refused).reason == "the request failed"
    assert next_step(unlucky, retry=True).kind is StepKind.FAIL
    assert next_step(good, retry=True).kind is StepKind.PASS
