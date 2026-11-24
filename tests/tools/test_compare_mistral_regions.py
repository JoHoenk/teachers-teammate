"""Tests for the developer tool that compares the global and the EU Mistral endpoint."""

from __future__ import annotations

from datetime import date
import io
import urllib.error

import pytest

from teachers_teammate.infrastructure.providers import _mistral_common as common
from tools.dev import compare_mistral_regions as tool

TODAY = date(2026, 11, 20)
KEY = "k-secret-value"
BUILTIN = (
    "mistral-large-latest",
    "mistral-medium-latest",
    "mistral-small-latest",
    "open-mistral-nemo",
)


def _entry(model_id: str, **extra: object) -> dict:
    """Return a ``/models`` entry of a chat model."""
    return {"id": model_id, "capabilities": {"completion_chat": True}, **extra}


GLOBAL_ENTRIES = [
    _entry("mistral-large-latest"),
    _entry("mistral-small-latest", aliases=["mistral-small-2506"]),
    _entry("open-mistral-nemo"),
    _entry("mistral-medium-latest"),
    {"id": "codestral-latest"},
]
EU_ENTRIES = [
    _entry("mistral-large-latest", regions=["eu"]),
    _entry("mistral-small-latest", aliases=["mistral-small-2506"]),
    _entry("ministral-8b-latest"),
]


def test_each_built_in_model_is_looked_up_on_both_endpoints() -> None:
    """
    Given  a global endpoint that serves all four built-in models and an EU one that serves two
    When   the endpoints are compared
    Then   every built-in model says whether each endpoint serves it
    """
    result = tool.compare(GLOBAL_ENTRIES, EU_ENTRIES, BUILTIN, TODAY)

    assert {name: (on_global, on_eu) for name, on_global, on_eu in result.builtin} == {
        "mistral-large-latest": (True, True),
        "mistral-medium-latest": (True, False),
        "mistral-small-latest": (True, True),
        "open-mistral-nemo": (True, False),
    }


def test_models_only_one_endpoint_offers_are_listed_and_code_models_are_left_out() -> None:
    """
    Given  entries that differ between the endpoints, and a code model on the global one
    When   the endpoints are compared
    Then   each side lists what only it offers, and the code model is in neither list
    """
    result = tool.compare(GLOBAL_ENTRIES, EU_ENTRIES, BUILTIN, TODAY)

    assert result.only_global == ["mistral-medium-latest", "open-mistral-nemo"]
    assert result.only_eu == ["ministral-8b-latest"]
    assert "codestral-latest" not in result.global_.names


def test_an_alias_counts_as_served() -> None:
    """
    Given  a model that answers to a dated alias
    When   the endpoint's entries are read
    Then   the alias is a served name even though the list shows only the current one
    """
    served = tool.served_by(EU_ENTRIES, TODAY)

    assert "mistral-small-2506" in served.names
    assert "mistral-small-2506" not in served.offered


def test_the_report_marks_a_missing_model_and_names_the_entry_fields() -> None:
    """
    Given  a comparison in which the EU endpoint lacks a built-in model
    When   the report is formatted
    Then   the missing cell reads NO and the fields of an EU entry are listed
    """
    report = tool.format_report(tool.compare(GLOBAL_ENTRIES, EU_ENTRIES, BUILTIN, TODAY))
    built_in_part = report.split("Built-in list")[1]

    assert "mistral-medium-latest  yes     NO" in built_in_part
    assert "mistral-large-latest   yes     yes" in built_in_part
    assert "regions" in report.splitlines()[-1]


# Every kind of entry the app treats differently, in one answer.
ALL_KINDS = [
    _entry("mistral-small-latest"),
    _entry("mistral-small-2506", aliases=["mistral-small-latest"]),
    _entry("mistral-small-2501", aliases=["mistral-small-latest"]),
    _entry("mistral-large-2407", deprecation="2026-12-31"),
    _entry("mistral-tiny", deprecation="2025-01-01"),
    {"id": "old-model", "archived": True},
    {"id": "voxtral-mini-latest", "audio_transcription_capabilities": {"streaming": True}},
    _entry("mistral-embed"),
]


def test_every_entry_says_what_the_app_does_with_it() -> None:
    """
    Given  an answer with a current model, its pinned and older versions, a retiring and a
           retired model, an archived one, an audio model and an embedding model
    When   the endpoint's entries are read
    Then   each entry has the status the Settings window's list would give it, with the reason
           for a hidden one
    """
    served = tool.served_by(ALL_KINDS, TODAY)

    assert {model_id: str(status) for model_id, status in served.status.items()} == {
        "mistral-small-latest": "offered",
        "mistral-small-2506": "offered",
        "mistral-small-2501": "also served: alias or older version of an offered model",
        "mistral-large-2407": "retiring: 2026-12-31",
        "mistral-tiny": "retired",
        "old-model": "hidden: archived",
        "voxtral-mini-latest": "hidden: audio model",
        "mistral-embed": "hidden: code, OCR or embedding model",
    }


def test_the_table_lists_every_entry_of_both_endpoints_offered_ones_first() -> None:
    """
    Given  entries of every kind on the global endpoint, and on the EU endpoint one of them plus
           one model the global endpoint lacks
    When   the report is formatted
    Then   the table has a row for every ID of either endpoint, says which endpoint has it, and
           lists the offered models before the others
    """
    eu_entries = [_entry("mistral-small-latest"), _entry("ministral-3b-latest")]
    comparison = tool.compare(ALL_KINDS, eu_entries, BUILTIN, TODAY)
    report = tool.format_report(comparison)
    table = report.split("All models")[1].split("Built-in list")[0]

    rows = comparison.rows
    assert len(rows) == len(ALL_KINDS) + 1
    assert "All models (9):" in report
    assert rows[0][0] == "ministral-3b-latest"  # offered, sorted by ID before mistral-small-…
    assert (rows[0][1], rows[0][2]) == (False, True)
    assert [row[0] for row in rows][-3:] == ["mistral-embed", "old-model", "voxtral-mini-latest"]
    assert "mistral-tiny" in table
    assert "hidden: archived" in table


def test_an_entry_the_endpoints_treat_differently_says_both() -> None:
    """
    Given  a model that the global endpoint serves and the EU endpoint marks as archived
    When   the endpoints are compared
    Then   its row says what each endpoint does with it
    """
    comparison = tool.compare(
        [_entry("m-x")], [{**_entry("m-x"), "archived": True}], BUILTIN, TODAY
    )

    assert comparison.rows == [("m-x", True, True, "global: offered / EU: hidden: archived")]


def test_the_summary_counts_the_entries_by_what_became_of_them() -> None:
    """
    Given  an endpoint answer with entries of several kinds
    When   the report is formatted
    Then   the endpoint's line gives the number of entries and a count per kind
    """
    report = tool.format_report(tool.compare(ALL_KINDS, ALL_KINDS, BUILTIN, TODAY))

    assert (
        "global  https://api.mistral.ai/v1: 8 entries "
        "(2 offered, 1 retiring, 1 also served, 1 retired, 3 hidden)"
    ) in report


def test_without_a_key_the_tool_asks_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  no MISTRAL_API_KEY in the environment
    When   the tool runs
    Then   it exits 2, says the key is not set, and makes no request
    """
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    def refuse(*_args: object) -> list:
        raise AssertionError("a request was made without a key")

    monkeypatch.setattr(tool.common, "fetch_model_entries", refuse)

    assert tool.main([]) == 2
    assert "MISTRAL_API_KEY is not set" in capsys.readouterr().err


def test_the_tool_asks_each_endpoint_with_the_key_and_never_prints_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a key and two endpoints that answer
    When   the tool runs
    Then   each endpoint is asked once with the key, the report is printed, exit is 0, and the
           key appears in no output
    """
    monkeypatch.setenv("MISTRAL_API_KEY", KEY)
    asked: list[tuple[str, str]] = []

    def answer(base_url: str, api_key: str) -> list:
        asked.append((base_url, api_key))
        return GLOBAL_ENTRIES if base_url == common.GLOBAL_BASE_URL else EU_ENTRIES

    monkeypatch.setattr(tool.common, "fetch_model_entries", answer)

    assert tool.main([]) == 0
    out = capsys.readouterr()
    assert asked == [(common.GLOBAL_BASE_URL, KEY), (common.EU_BASE_URL, KEY)]
    assert "EU      https://api.eu.mistral.ai/v1" in out.out
    assert KEY not in out.out + out.err


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (
            urllib.error.HTTPError("u", 401, "no", None, io.BytesIO()),  # ty: ignore[invalid-argument-type]
            "EU endpoint answered with error 401",
        ),
        (urllib.error.URLError("down"), "EU endpoint: no connection"),
        (ValueError("html"), "EU endpoint: the answer is not a model list"),
    ],
)
def test_an_endpoint_that_does_not_answer_fails_the_run_without_leaking_the_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    message: str,
) -> None:
    """
    Given  an EU endpoint that refuses the key, is unreachable, or answers with something else
    When   the tool runs
    Then   it exits 1 with a plain sentence naming the endpoint, prints no report and not the key
    """
    monkeypatch.setenv("MISTRAL_API_KEY", KEY)

    def answer(base_url: str, _api_key: str) -> list:
        if base_url == common.EU_BASE_URL:
            raise failure
        return GLOBAL_ENTRIES

    monkeypatch.setattr(tool.common, "fetch_model_entries", answer)

    assert tool.main([]) == 1
    out = capsys.readouterr()
    assert message in out.err
    assert out.out == ""
    assert KEY not in out.err
