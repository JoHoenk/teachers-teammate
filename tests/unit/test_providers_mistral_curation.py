"""Tests for the Mistral model curation (``curate_mistral_models``).

The fixture ``tests/fixtures/mistral_models.json`` is a hand-written ``/v1/models`` answer
shaped like the live one: several aliases per model, dated versions, code/OCR/audio/
moderation models, an archived model, models that retire, and one fine-tuned model.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any

import pytest

from teachers_teammate.domain.model_listing import CuratedModels
from teachers_teammate.infrastructure.providers._mistral_common import (
    curate_mistral_models,
)

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "mistral_models.json"

#: A day before every retirement date in the fixture, so all of them are still served.
_TODAY = date(2024, 1, 1)


def _entries() -> list[dict[str, Any]]:
    """Return the model entries of the recorded ``/v1/models`` answer."""
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = payload["data"]
    return entries


def _curated() -> CuratedModels:
    """Return the curated model list of the fixture for :data:`_TODAY`."""
    return curate_mistral_models(_entries(), _TODAY)


def _ids(models: Any) -> list[str]:
    """Return the model IDs of *models*, in order."""
    return [model.id for model in models]


def test_one_entry_per_model_family() -> None:
    """
    Given  a live answer listing mistral-small-latest and mistral-small-2506 separately
    When   the models are curated
    Then   the current group holds mistral-small-latest only, and the dated ID appears once,
           in the pinned group
    """
    offered = _curated().offered

    assert [m.id for m in offered if m.group == "current"].count("mistral-small-latest") == 1
    assert [m.id for m in offered if m.group == "current"].count("mistral-small-2506") == 0
    assert [m.id for m in offered if m.group == "pinned"].count("mistral-small-2506") == 1


def test_dated_versions_are_labelled_and_sort_below_the_current_models() -> None:
    """
    Given  a live answer with a dated version of two models
    When   the models are curated
    Then   both dated IDs are in the pinned group with the "pinned version" label, and the
           pinned group comes after every current model
    """
    offered = _curated().offered

    pinned = [model for model in offered if model.group == "pinned"]
    assert _ids(pinned) == ["mistral-large-2411", "mistral-small-2506"]
    assert {model.label for model in pinned} == {"pinned version"}
    last_current = max(index for index, model in enumerate(offered) if model.group == "current")
    first_pinned = min(index for index, model in enumerate(offered) if model.group == "pinned")
    assert first_pinned > last_current


def test_biggest_model_comes_first_and_the_default_model_is_marked_suggested() -> None:
    """
    Given  a live answer with large, medium, small and ministral models
    When   the models are curated
    Then   the current group is ordered large, medium, small, ministral, others, and the
           default model carries the "suggested" label
    """
    current = [model for model in _curated().offered if model.group == "current"]

    assert _ids(current) == [
        "mistral-large-latest",
        "mistral-medium-latest",
        "mistral-small-latest",
        "ministral-3b-2410",
        "ministral-8b-2410",
        "open-mistral-nemo",
    ]
    labels = {model.id: model.label for model in current}
    assert labels["mistral-small-latest"] == "★ suggested"
    assert labels["mistral-large-latest"] == ""


def test_deprecated_in_the_past_is_dropped_entirely() -> None:
    """
    Given  a live answer with a model whose retirement date has passed
    When   the models are curated
    Then   that model is in neither the offered list nor the retiring list
    """
    curated = _curated()

    assert "mistral-large-2407" not in _ids(curated.offered)
    assert "mistral-large-2407" not in _ids(curated.retiring)


def test_deprecated_in_the_future_is_hidden_but_reported() -> None:
    """
    Given  a live answer with a model that retires in the future and its replacement
    When   the models are curated
    Then   the model is not offered but is returned as retiring, with the date and the model
           the service suggests instead
    """
    curated = _curated()
    retiring = {model.id: model for model in curated.retiring}

    assert "pixtral-large-latest" not in _ids(curated.offered)
    assert retiring["pixtral-large-latest"].retires_on == date(2099, 12, 31)
    assert retiring["pixtral-large-latest"].replacement == "mistral-large-latest"


@pytest.mark.parametrize(
    "model_id",
    [
        "codestral-latest",  # code
        "devstral-small-latest",  # code
        "mistral-ocr-latest",  # OCR
        "voxtral-mini-latest",  # audio
        "mistral-moderation-latest",  # classification
        "mistral-tiny",  # archived
    ],
)
def test_models_that_cannot_proofread_are_dropped(model_id: str) -> None:
    """
    Given  a live answer with a code, OCR, audio, moderation or archived model
    When   the models are curated
    Then   that model is in neither the offered list nor the retiring list
    """
    curated = _curated()

    assert model_id not in _ids(curated.offered)
    assert model_id not in _ids(curated.retiring)


def test_fine_tuned_models_are_listed_last_and_labelled() -> None:
    """
    Given  a live answer with one fine-tuned model
    When   the models are curated
    Then   it is the last entry, in the "own" group, labelled "your model"
    """
    offered = _curated().offered

    assert offered[-1].id == "ft:mistral-small-latest:5f1c2b9e"
    assert (offered[-1].group, offered[-1].label) == ("own", "your model")


def test_description_and_context_size_are_kept_for_the_tooltip() -> None:
    """
    Given  a live answer whose entries carry descriptions and context sizes
    When   the models are curated
    Then   the offered entries keep them, and a current model also names the pinned version
    """
    offered = {model.id: model for model in _curated().offered}

    small = offered["mistral-small-latest"]
    assert small.description.startswith("A small, fast model")
    assert "Currently mistral-small-2506." in small.description
    assert small.context_tokens == 131072
    assert offered["ministral-3b-2410"].description == ("A tiny model for simple, short texts.")


def test_a_service_that_offers_nothing_usable_is_reported_as_empty() -> None:
    """
    Given  a live answer with only an archived model
    When   the models are curated
    Then   nothing is offered and nothing is retiring, so the caller can say the list failed
    """
    curated = curate_mistral_models([{"id": "old", "archived": True}], _TODAY)

    assert (curated.ids, list(curated.retiring)) == ([], [])


def test_an_entry_without_capabilities_is_kept() -> None:
    """
    Given  a live answer whose entry says nothing about its capabilities
    When   the models are curated
    Then   the model is offered, because an absent answer is not a refusal
    """
    curated = curate_mistral_models([{"id": "glm-test"}], _TODAY)

    assert curated.ids == ["glm-test"]


def _entry(model_id: str, **extra: Any) -> dict[str, Any]:
    """Return a minimal chat-capable model entry."""
    return {"id": model_id, "capabilities": {"completion_chat": True}, **extra}


def test_the_dated_version_stays_offered_when_only_the_latest_alias_retires() -> None:
    """
    Given  a family whose -latest alias retires in the future but whose dated version does not
    When   the models are curated
    Then   the dated version is still offered (pinned) and the alias is reported as retiring
    """
    entries = [
        _entry("mistral-large-latest", deprecation="2027-01-01", aliases=["mistral-large-2411"]),
        _entry("mistral-large-2411", aliases=["mistral-large-latest"]),
    ]

    curated = curate_mistral_models(entries, date(2026, 11, 8))

    assert _ids(curated.offered) == ["mistral-large-2411"]
    assert curated.offered[0].group == "pinned"
    assert _ids(curated.retiring) == ["mistral-large-latest"]


def test_an_entry_without_an_id_is_ignored_and_the_rest_is_kept() -> None:
    """
    Given  an answer holding one entry that has no ID next to valid models
    When   the models are curated
    Then   the entry is skipped and the valid models are offered
    """
    entries = [{"capabilities": {"completion_chat": True}}, _entry("mistral-small-latest")]

    curated = curate_mistral_models(entries, date(2026, 11, 8))

    assert _ids(curated.offered) == ["mistral-small-latest"]


def test_a_fine_tuned_model_past_its_retirement_date_is_not_offered() -> None:
    """
    Given  a fine-tuned model whose retirement date has passed and one that is still served
    When   the models are curated
    Then   only the one still served is offered under "your model"
    """
    entries = [
        _entry("ft:old:1", type="fine-tuned", deprecation="2025-01-01"),
        _entry("ft:new:2", type="fine-tuned"),
    ]

    curated = curate_mistral_models(entries, date(2026, 11, 8))

    assert _ids(curated.offered) == ["ft:new:2"]
    assert _ids(curated.retiring) == []


def test_aliases_that_are_not_listed_are_still_reported_as_served() -> None:
    """
    Given  the recorded answer, where mistral-medium-2508 is only an alias of
           mistral-medium-latest
    When   the models are curated
    Then   the alias is not offered but is reported as still served
    """
    curated = _curated()

    assert "mistral-medium-2508" not in _ids(curated.offered)
    assert "mistral-medium-2508" in curated.also_served
    assert "mistral-medium-latest" not in curated.also_served  # offered, so not "also"
    assert "mistral-large-2407" not in curated.also_served  # retired, no longer served
