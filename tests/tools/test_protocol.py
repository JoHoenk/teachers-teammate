"""Tests of the protocol of a measured run: the version file, its fingerprint, the prompts hash."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from teachers_teammate.infrastructure import correction, correction_schema
from tools.model_eval import protocol
from tools.model_eval.languages import LANGUAGE_NAMES


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_fingerprint_of_the_current_version_is_the_one_in_the_file() -> None:
    """
    Given  the version file and the request side of today's code
    When   the stamp of the current version is asked for
    Then   it is returned, so a run may start; after a change of the request side this test fails
           with the message that says to add a version or undo the change
    """
    stamp = protocol.current_stamp()

    assert protocol.entry(stamp.eval_version)["fingerprint"] == stamp.fingerprint


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_changed_prompt_stops_a_run_with_the_message_that_says_what_to_do(monkeypatch) -> None:
    """
    Given  a prompt that changed since the current version was recorded
    When   a run asks for the stamp it may record
    Then   it is refused, and the message names the version and says to add one or undo the change
    """
    version = int(protocol.read_protocol()["current"])
    monkeypatch.setitem(correction.PROMPTS, "german", "Ein anderer Prompt für {level}.")

    with pytest.raises(protocol.ProtocolError) as refused:
        protocol.current_stamp()

    assert f"since version {version}" in str(refused.value)
    assert "add a version" in str(refused.value)
    assert "undo the change" in str(refused.value)


def test_the_protocol_file_starts_at_version_one() -> None:
    """
    Given  the version file of the request side
    When   the current stamp is asked for
    Then   it is version 1, the only entry, and its fingerprint is the one the tool computes
    """
    stamp = protocol.current_stamp()

    assert stamp.eval_version == 1
    assert [v["version"] for v in protocol.read_protocol()["versions"]] == [1]
    assert protocol.entry(1)["fingerprint"] == stamp.fingerprint


def test_a_version_file_that_cannot_be_read_refuses_instead_of_crashing(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a version file that is not JSON, one whose `current` is not a number, and a missing one
    When   a run asks for the stamp
    Then   each gives a ProtocolError (a ValueError the command line turns into exit code 2)
    """
    broken = tmp_path / "protocol.json"
    monkeypatch.setattr(protocol, "PROTOCOL_PATH", broken)
    with pytest.raises(protocol.ProtocolError, match="cannot be read"):
        protocol.current_stamp()

    broken.write_text("{", encoding="utf-8")
    with pytest.raises(protocol.ProtocolError, match="cannot be read"):
        protocol.current_stamp()

    entry = {"version": 1, "date": "2026-11-10", "fingerprint": "a" * 64, "why": "first"}
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
    from tools.model_eval import app_corrector  # noqa: PLC0415

    before = protocol.fingerprint_sha256()
    real = app_corrector.app_corrector_factory

    def other_settings(client, *, timeout, mode, num_ctx, profile):
        return lambda model, temperature: real(
            client, timeout=timeout * 3, mode=mode, num_ctx=num_ctx, profile=profile
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
    assert set(protocol.fingerprint()["requests"]["review"]["de"]) == {"plain", "compact"}

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
    Then   it holds a request and a text budget for every mode and language, the repair message,
           and the answer format version, and printing it shows which part moved
    """
    side = protocol.fingerprint()

    assert set(side["requests"]) == {"review", "text"}
    assert set(side["requests"]["review"]) == set(protocol.FINGERPRINT_LANGUAGES)
    assert side["requests"]["review"]["de"] != side["requests"]["text"]["de"]
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
    assert set(protocol.fingerprint()["requests"]["review"]) == set(LANGUAGE_NAMES)


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_version_file_is_valid() -> None:
    """
    Given  the committed version file
    When   it is checked
    Then   the versions ascend, `current` is one of them, and each has a fingerprint and a reason
    """
    assert protocol.problems() == []


def test_a_version_file_that_breaks_the_rules_is_named(tmp_path: Path) -> None:
    """
    Given  a version file whose versions descend, repeat, and whose `current` is missing
    When   it is checked
    Then   every one of those is reported
    """
    broken = {
        "schema": 1,
        "current": 3,
        "versions": [
            {"version": 2, "date": "2026-11-10", "fingerprint": "a" * 64, "why": "second"},
            {"version": 1, "date": "2026-11-10", "fingerprint": "b" * 64, "why": ""},
            {"version": 2, "date": "2026-11-10", "fingerprint": "c" * 64, "why": "again"},
        ],
    }
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(broken), encoding="utf-8")

    found = protocol.problems(protocol.read_protocol(path))

    assert any("ascending" in problem for problem in found)
    assert any("appears twice" in problem for problem in found)
    assert any("version 1 has no reason" in problem for problem in found)
    assert any("current 3 is not one of the versions" in problem for problem in found)


def test_a_version_pair_the_file_does_not_record_is_not_recorded() -> None:
    """
    Given  a version and a fingerprint
    When   the pair is looked up in the version file
    Then   only the recorded pair counts, so a run of an unrecorded protocol is recognisable
    """
    stamp = protocol.current_stamp()

    assert protocol.is_recorded(stamp.eval_version, stamp.fingerprint)
    assert not protocol.is_recorded(stamp.eval_version, "0" * 64)
    assert not protocol.is_recorded(99, stamp.fingerprint)
    assert not protocol.is_recorded("1", stamp.fingerprint)


# ── the instructions hash (moved here from test_store.py) ─────────────────────


def test_the_instructions_hash_follows_the_prompt_of_each_language(monkeypatch):
    """
    Given  the hash of the instructions of a run in German, English and French
    When   the German preset changes
    Then   the hash of a run with German changes, the hash of English and French alone does not
    """
    both = protocol.instructions_sha256(["de", "en", "fr"])
    english_french = protocol.instructions_sha256(["en", "fr"])
    german = protocol.instructions_sha256(["de"])
    assert (
        len({german, protocol.instructions_sha256(["en"]), protocol.instructions_sha256(["fr"])})
        == 3
    )

    monkeypatch.setitem(correction.PROMPTS, "german", "Ein anderer Prompt für {level}.")

    assert protocol.instructions_sha256(["de"]) != german
    assert protocol.instructions_sha256(["de", "en", "fr"]) != both
    assert protocol.instructions_sha256(["en", "fr"]) == english_french


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
    Given  the same languages asked in review mode and in text mode
    When   the instructions are hashed
    Then   the two hashes differ, because the answer format is part of the request
    """
    assert protocol.instructions_sha256(["de"], mode="review") != protocol.instructions_sha256(
        ["de"], mode="text"
    )


def test_the_instructions_hash_of_a_compact_run_differs_from_a_plain_one():
    """
    Given  the same languages asked in review mode with and without the compact-JSON rule
    When   the instructions are hashed
    Then   the two hashes differ, because the rule is part of the request, and text mode (which
           never carries it) hashes the same either way
    """
    plain = protocol.instructions_sha256(["de"])

    assert protocol.instructions_sha256(["de"], compact_json=True) != plain
    assert protocol.instructions_sha256(["de"], compact_json=False) == plain
    assert protocol.instructions_sha256(
        ["de"], mode="text", compact_json=True
    ) == protocol.instructions_sha256(["de"], mode="text")
