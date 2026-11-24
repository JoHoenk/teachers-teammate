"""Tests of the protocol of a measured run: the fingerprint of the request side, the prompts hash.

The version file itself is :mod:`tools.evaluation.core.protocol_table`, and its own tests are in
``test_evaluation_core.py``.  What is tested here is the capture: the request side the fingerprint
is made of, and the one checked way to get the version a new run records.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from tools.evaluation.core import protocol_table
from tools.evaluation.core.languages import LANGUAGE_NAMES
from tools.evaluation.core.modes import Exchange, Step
from tools.evaluation.run import protocol
from tools.evaluation.run.one_request import correction, correction_schema


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_fingerprint_of_the_current_version_is_the_one_in_the_file() -> None:
    """
    Given  the version file and the request side of today's code
    When   the stamp of the current version is asked for
    Then   it is returned, so a run may start; after a change of the request side this test fails
           with the message that says to add a version or undo the change
    """
    stamp = protocol.current_stamp()

    assert stamp.fingerprint in protocol_table.fingerprints_of(stamp.eval_version)


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_changed_prompt_stops_a_run_with_the_message_that_says_what_to_do(monkeypatch) -> None:
    """
    Given  a prompt that changed since the current version was recorded
    When   a run asks for the stamp it may record
    Then   it is refused, and the message names the version and says to add one or undo the change
    """
    version = protocol_table.current_version()
    monkeypatch.setitem(correction.PROMPTS, "german", "Ein anderer Prompt für {level}.")

    with pytest.raises(protocol.ProtocolError) as refused:
        protocol.current_stamp()

    assert f"since version {version}" in str(refused.value)
    assert f"add the fingerprint to version {version}" in str(refused.value)
    assert "undo the change" in str(refused.value)


def test_the_protocol_file_holds_every_version_and_the_current_one_is_what_the_tool_computes() -> (
    None
):
    """
    Given  the version file of the request side
    When   the current stamp is asked for
    Then   it is version 4, versions 1 to 3 are still recorded (their runs stay checkable), and
           the current fingerprint is the one the tool computes
    """
    stamp = protocol.current_stamp()
    table = protocol_table.read_table()

    assert stamp.eval_version == 4
    assert [entry["version"] for entry in table["versions"]] == [1, 2, 3, 4]
    assert stamp.fingerprint in protocol_table.fingerprints_of(4)
    for version in (1, 2, 3):
        assert protocol_table.accepts(version, protocol_table.fingerprints_of(version)[0])


def test_a_version_file_that_cannot_be_read_refuses_instead_of_crashing(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a version file that is not JSON, one whose `current` is not a number, and a missing one
    When   a run asks for the stamp
    Then   each gives a ProtocolError (a ValueError the command line turns into exit code 2)
    """
    broken = tmp_path / "protocol.json"
    monkeypatch.setattr(protocol_table, "TABLE_PATH", broken)
    with pytest.raises(protocol.ProtocolError, match="cannot be read"):
        protocol.current_stamp()

    broken.write_text("{", encoding="utf-8")
    with pytest.raises(protocol.ProtocolError, match="cannot be read"):
        protocol.current_stamp()

    entry = {"version": 1, "date": "2026-11-10", "fingerprints": ["a" * 64], "why": "first"}
    broken.write_text(
        json.dumps({"schema": 1, "current": "x", "versions": [entry]}), encoding="utf-8"
    )
    with pytest.raises(protocol.ProtocolError, match="not one of the versions"):
        protocol.current_stamp()


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_fingerprint_changes_when_a_prompt_changes(monkeypatch) -> None:
    """
    Given  the fingerprint of the request side
    When   the instructions of one language are built differently
    Then   the fingerprint changes, so the change cannot go unnoticed
    """
    before = protocol.fingerprint_sha256()
    monkeypatch.setitem(correction.PROMPTS, "german", "Ein anderer Prompt für {level}.")

    assert protocol.fingerprint_sha256() != before


@pytest.mark.use_case("Model_Eval_Measure")
@pytest.mark.parametrize(
    ("target", "name", "value"),
    [
        (correction, "_HUMAN_REVIEW", "Text: {text}\nLanguage: {language}\n{explanation}"),
        (correction, "_HUMAN_TEXT", "Text: {text}\nLanguage: {language}"),
        (correction_schema.CorrectionAnswer, "model_json_schema", lambda: {"type": "object"}),
        (correction, "REVIEW_ANSWER_FACTOR", 3.0),
        (protocol, "repair_message", lambda previous, error: "Again, please."),
        (protocol, "NUM_CTX", 16384),
        (protocol, "ANSWER_FORMAT_VERSION", 99),
    ],
)
def test_the_fingerprint_follows_every_part_of_the_request_side(
    monkeypatch, target, name, value
) -> None:
    """
    Given  the fingerprint of the request side
    When   one part of what the app sends changes: the user message, the answer schema, the
           factor the long-text budget is computed with, the repair message, the window or the
           answer format version
    Then   the fingerprint changes, because it is captured from the request itself and not
           from a list someone has to keep
    """
    before = protocol.fingerprint_sha256()

    monkeypatch.setattr(target, name, value)

    assert protocol.fingerprint_sha256() != before


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_run_settings_are_not_part_of_the_fingerprint() -> None:
    """
    Given  the fingerprint of the request side
    When   the corrector is asked at another temperature and timeout than the capture's
    Then   the fingerprint is unchanged, because those belong to the run header, not to the request
    """
    from tools.evaluation.run import app_corrector  # noqa: PLC0415

    before = protocol.fingerprint_sha256()
    real = app_corrector.app_corrector_factory

    def other_settings(client, *, timeout, exchange, steps, num_ctx, profile):
        return lambda model, temperature: real(
            client,
            timeout=timeout * 3,
            exchange=exchange,
            steps=steps,
            num_ctx=num_ctx,
            profile=profile,
        )(model, 0.9)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(protocol, "app_corrector_factory", other_settings)
        assert protocol.fingerprint_sha256() == before


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_fingerprint_covers_both_compact_settings(monkeypatch) -> None:
    """
    Given  the fingerprint of the request side
    When   the compact-answer rule changes, and when a catalog model's profile changes
    Then   the rule changes the fingerprint, because both settings are captured; the catalog's
           choice per model does not, because it is recorded per run like the temperature
    """
    from teachers_teammate.domain import model_catalog  # noqa: PLC0415
    from teachers_teammate.domain.model_catalog import (  # noqa: PLC0415
        OLLAMA_CATALOG,
        RequestProfile,
    )

    before = protocol.fingerprint_sha256()
    assert set(protocol.fingerprint()["requests"]["one-request-review"]["de"]) == {
        "plain",
        "compact",
    }

    catalog = tuple(
        replace(spec, request_profile=RequestProfile(compact_json=True))
        if spec.tag == "gemma4:e4b"
        else spec
        for spec in OLLAMA_CATALOG
    )
    with monkeypatch.context() as patch:
        patch.setattr(model_catalog, "OLLAMA_CATALOG", catalog)
        assert protocol.fingerprint_sha256() == before

    monkeypatch.setattr(correction, "COMPACT_ANSWER_RULE", "Answer on one line, please.")
    assert protocol.fingerprint_sha256() != before


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_fingerprint_names_what_it_covers() -> None:
    """
    Given  the request side of a run
    When   it is read
    Then   it holds a request and a text budget for every setup and language, the repair message,
           and the answer format version, and printing it shows which part moved
    """
    side = protocol.fingerprint()

    assert set(side["requests"]) == {
        "one-request-review",
        "one-request-text",
        "text-first",
        "text-first/correct",
    }
    assert set(side["requests"]["one-request-review"]) == set(protocol.FINGERPRINT_LANGUAGES)
    assert (
        side["requests"]["one-request-review"]["de"] != side["requests"]["one-request-text"]["de"]
    )
    assert all(
        budget > 0
        for per_mode in side["text_budget"].values()
        for per_language in per_mode.values()
        for budget in per_language.values()
    )
    assert side["answer_format_version"] >= 1
    assert side["repair_answer_tokens"] == protocol.NUM_CTX // 2


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_fingerprint_covers_every_language_the_tool_can_ask_in() -> None:
    """
    Given  the table of the languages the tool can ask in
    When   the languages of the fingerprint are read
    Then   they are that table, so a new language changes the fingerprint and needs a new version
    """
    assert protocol.FINGERPRINT_LANGUAGES == tuple(LANGUAGE_NAMES)
    assert set(protocol.fingerprint()["requests"]["one-request-review"]) == set(LANGUAGE_NAMES)


def test_a_version_pair_the_file_does_not_record_is_not_recorded() -> None:
    """
    Given  a version and a fingerprint
    When   the pair is looked up in the version file
    Then   only the recorded pair counts, so a run of an unrecorded protocol is recognisable
    """
    stamp = protocol.current_stamp()

    assert protocol_table.accepts(stamp.eval_version, stamp.fingerprint)
    assert not protocol_table.accepts(stamp.eval_version, "0" * 64)
    assert not protocol_table.accepts(99, stamp.fingerprint)
    # a version read as a string is no version of the file, and must not match
    assert not protocol_table.accepts("1", stamp.fingerprint)  # ty: ignore[invalid-argument-type]


# ── the instructions hash (moved here from test_store.py) ─────────────────────


def test_the_instructions_hash_follows_the_prompt_of_each_language(monkeypatch):
    """
    Given  the hash of the instructions of a one-request review in German, English and French
    When   the German preset changes
    Then   the hash of a run with German changes, the hash of English and French alone does not
    """
    review = Exchange.ONE_REQUEST_REVIEW
    both = protocol.instructions_sha256(["de", "en", "fr"], exchange=review)
    english_french = protocol.instructions_sha256(["en", "fr"], exchange=review)
    german = protocol.instructions_sha256(["de"], exchange=review)
    assert (
        len(
            {
                german,
                protocol.instructions_sha256(["en"], exchange=review),
                protocol.instructions_sha256(["fr"], exchange=review),
            }
        )
        == 3
    )

    monkeypatch.setitem(correction.PROMPTS, "german", "Ein anderer Prompt für {level}.")

    assert protocol.instructions_sha256(["de"], exchange=review) != german
    assert protocol.instructions_sha256(["de", "en", "fr"], exchange=review) != both
    assert protocol.instructions_sha256(["en", "fr"], exchange=review) == english_french


def test_the_instructions_hash_does_not_depend_on_the_order_of_the_languages():
    """
    Given  the same languages named in two orders
    When   the instructions are hashed
    Then   the hash is the same, as the file format says (alphabetical by language name)
    """
    assert protocol.instructions_sha256(["fr", "de", "en"]) == protocol.instructions_sha256(
        ["de", "en", "fr"]
    )


def test_a_language_without_a_name_cannot_be_hashed():
    """
    Given  a language code the tool does not know
    When   the instructions are hashed
    Then   it is refused, because the English prompt would be hashed silently in its place
    """
    with pytest.raises(ValueError, match="no language name"):
        protocol.instructions_sha256(["xx"])


def test_the_instructions_hash_of_a_text_run_differs_from_a_review_run():
    """
    Given  the same languages asked in the one-request review and the one-request text exchange
    When   the instructions are hashed
    Then   the two hashes differ, because the answer format is part of the request
    """
    assert protocol.instructions_sha256(
        ["de"], exchange=Exchange.ONE_REQUEST_REVIEW
    ) != protocol.instructions_sha256(["de"], exchange=Exchange.ONE_REQUEST_TEXT)


def test_the_instructions_hash_of_a_compact_run_differs_from_a_plain_one():
    """
    Given  the same languages asked in the one-request review with and without the compact rule
    When   the instructions are hashed
    Then   the two hashes differ, because the rule is part of the request, and the one-request
           text exchange (which never carries it) hashes the same either way
    """
    review = Exchange.ONE_REQUEST_REVIEW
    text = Exchange.ONE_REQUEST_TEXT
    plain = protocol.instructions_sha256(["de"], exchange=review)

    assert protocol.instructions_sha256(["de"], exchange=review, compact_json=True) != plain
    assert protocol.instructions_sha256(["de"], exchange=review, compact_json=False) == plain
    assert protocol.instructions_sha256(
        ["de"], exchange=text, compact_json=True
    ) == protocol.instructions_sha256(["de"], exchange=text)


def test_the_exchange_instructions_hash_and_the_fingerprint_follow_its_prompts(
    monkeypatch,
) -> None:
    """
    Given  the app's proofreading exchange
    When   the instructions are hashed, and one of the exchange's prompts changes
    Then   the hash differs from that of the one-request exchanges and follows the prompt, and so
           does the fingerprint of the protocol, because the requests it hashes carry that prompt
    """
    from teachers_teammate.domain import correction_prompts  # noqa: PLC0415

    before = protocol.fingerprint_sha256()
    exchange = protocol.instructions_sha256(["de", "en"])

    monkeypatch.setattr(
        correction_prompts, "CORRECT_SYSTEM", correction_prompts.CORRECT_SYSTEM + " x"
    )

    assert exchange != protocol.instructions_sha256(
        ["de", "en"], exchange=Exchange.ONE_REQUEST_TEXT
    )
    assert exchange != protocol.instructions_sha256(["de", "en"])
    assert protocol.fingerprint_sha256() != before


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_correct_only_run_sends_exactly_the_first_request_of_the_exchange() -> None:
    """
    Given  the request side of the exchange and of its step ① alone
    When   their requests for the probe text are compared, per language and profile
    Then   the step-① setup sends one request, and it hashes like the exchange's first one, so
           such a run measures the corrections of the exchange and nothing else
    """
    side = protocol.fingerprint()["requests"]

    for code in protocol.FINGERPRINT_LANGUAGES:
        for label in (
            "plain",
            "no-unclear",
            "sampled",
            "prompt-keep",
            "prompt-find",
            "prompt-shot",
        ):
            only = side["text-first/correct"][code][label]
            assert len(only) == 1
            assert only[0] == side["text-first"][code][label][0]
        assert len(side["text-first"][code]["plain"]) == 3
        assert len(side["text-first"][code]["no-unclear"]) == 2


@pytest.mark.use_case("Model_Eval_Measure")
def test_sampling_values_change_the_request_of_their_own_step_only() -> None:
    """
    Given  the plain and the sampled profile of the exchange
    When   their requests are compared
    Then   the sampled profile changes the requests it sets values for and not the third, and the
           plain profile's requests are the ones version 2 recorded
    """
    side = protocol.fingerprint()["requests"]["text-first"]["de"]

    assert side["plain"][0] != side["sampled"][0]  # step ①: presence_penalty and top_p
    assert side["plain"][1] != side["sampled"][1]  # step ②: top_k
    assert side["plain"][2] == side["sampled"][2]  # step ③: nothing set


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_correct_only_instructions_hash_covers_step_one_alone(monkeypatch) -> None:
    """
    Given  the exchange asked for step ① alone
    When   the instructions are hashed and the prompt of step two or of step one changes
    Then   the hash differs from the whole exchange's, ignores a change of step two's prompt, and
           follows a change of step one's
    """
    from teachers_teammate.domain import correction_prompts  # noqa: PLC0415

    only = protocol.instructions_sha256(["de"], steps=(Step.CORRECT,))
    assert only != protocol.instructions_sha256(["de"])

    monkeypatch.setattr(
        correction_prompts, "EXPLAIN_SYSTEM", correction_prompts.EXPLAIN_SYSTEM + " x"
    )
    assert protocol.instructions_sha256(["de"], steps=(Step.CORRECT,)) == only

    monkeypatch.setattr(
        correction_prompts, "CORRECT_SYSTEM", correction_prompts.CORRECT_SYSTEM + " x"
    )
    assert protocol.instructions_sha256(["de"], steps=(Step.CORRECT,)) != only


#: The request side of every setup, pinned as it was before the one-request path moved out of the
#: app.  Saved evaluation runs are looked up by these hashes, so the move must not change a byte
#: of them: a failure here means the move changed what a run sends, not that the pin is stale.
_PINNED: dict[str, object] = {
    "sha256": "e8bec644cb5f69c9ff437f0e2c1e1a813aade338fb56d5b14146cabe4417260d",
    "one-request-review": [
        "5cee79f68176628d5db29b8c87cb462b020b50e183595071bbac0f015ef7ae39",
        "5cee79f68176628d5db29b8c87cb462b020b50e183595071bbac0f015ef7ae39",
    ],
    "one-request-text": ["a415cfd5dac6d0939f34c7e299a6a1bc16345ac1d163c52c391f555c887a163c"],
    "text-first": [
        "f2c609ed873c9fe4f5f23d46c3ceb43da40e308525b15cd45b567d224bb2b8a3",
        "56305d109118e175b206013dba229d1efd2e357be1339073279c392f7c82454c",
        "d3472b62ae50d44668f53ad4c12464b7c8220304f3a6960a2184317b1e2b2e3f",
    ],
    "text-first/correct": ["f2c609ed873c9fe4f5f23d46c3ceb43da40e308525b15cd45b567d224bb2b8a3"],
    "one-request-review_budget": 2796,
    "one-request-text_budget": 2796,
    "text-first_budget": 4749,
    "text-first/correct_budget": 4749,
    "repair": "cd4964a4f3f7e0afff0d82aa83607604b5d997921c35aa953b2b50a7437d46db",
    "answer_format_version": 1,
    "repair_answer_tokens": 4096,
}


def test_the_request_side_is_byte_identical_to_the_pinned_one() -> None:
    """
    Given  the request side of today's code, for every setup and the German plain profile
    When   it is compared with the values pinned before the one-request path moved
    Then   every hash, every text budget and the repair message are unchanged, so a saved run is
           still found under the version it names
    """
    side = protocol.fingerprint()

    assert protocol.fingerprint_sha256() == _PINNED["sha256"]
    for setup in ("one-request-review", "one-request-text", "text-first", "text-first/correct"):
        assert side["requests"][setup]["de"]["plain"] == _PINNED[setup], setup
        assert side["text_budget"][setup]["de"]["plain"] == _PINNED[f"{setup}_budget"], setup
    assert side["repair"] == _PINNED["repair"]
    assert side["answer_format_version"] == _PINNED["answer_format_version"]
    assert side["repair_answer_tokens"] == _PINNED["repair_answer_tokens"]


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_tools_name_the_apps_exchange_with_the_apps_word() -> None:
    """
    Given  the exchange the app proofreads with and the tools' name for it
    When   their values are compared
    Then   they are the same word, so a stored run and the app speak of one exchange
    """
    from teachers_teammate.domain.proofreading.result import ExchangeKind  # noqa: PLC0415

    assert Exchange.TEXT_FIRST.value == ExchangeKind.TEXT_FIRST.value
