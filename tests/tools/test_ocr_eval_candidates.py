"""The candidates: what a model screen runs, and what the catalog and the survey say of them."""

from __future__ import annotations

from teachers_teammate.domain.model_catalog import OLLAMA_CATALOG
from tools.ocr_eval import candidates


def test_every_catalogued_reading_model_is_screened() -> None:
    """Given the catalog
    When   screen() lists what a model screen runs
    Then   every reading model of it is there, before the candidates
    """
    catalogued = {spec.tag for spec in OLLAMA_CATALOG if spec.category == "ocr"}

    assert catalogued
    assert catalogued <= set(candidates.screen())
    assert candidates.screen()[: len(catalogued)] == tuple(
        spec.tag for spec in OLLAMA_CATALOG if spec.category == "ocr"
    )


def test_a_candidate_says_where_it_came_from() -> None:
    """Given the candidates
    When   each is read
    Then   none of them claims a measurement, only the web survey
    """
    assert candidates.CANDIDATES
    assert {candidate.source for candidate in candidates.CANDIDATES} == {candidates.SURVEY}


def test_a_candidate_is_found_by_its_tag() -> None:
    """Given a candidate's tag
    When   find() is asked for it
    Then   the candidate comes back; an unknown tag gives nothing
    """
    tag = candidates.CANDIDATES[0].tag

    assert candidates.find(tag) is not None
    assert candidates.find("nobody/knows-this") is None


def test_the_licence_of_a_catalogued_model_is_the_catalog_s() -> None:
    """Given a catalogued reading model that states its licence
    When   its licence is asked for
    Then   the catalog's answer is returned, not a survey's claim
    """
    spec = next(
        spec for spec in OLLAMA_CATALOG if spec.category == "ocr" and spec.licence is not None
    )

    assert candidates.licence(spec.tag) == spec.licence.name


def test_a_model_nobody_read_a_licence_for_has_none() -> None:
    """Given a tag neither the catalog nor the survey knows
    When   its licence is asked for
    Then   it is empty: a licence nobody read is not a licence
    """
    assert candidates.licence("nobody/knows-this") == ""


def test_a_card_prompt_is_only_there_where_it_was_typed_in() -> None:
    """Given one card whose prompt is transcribed
    When   card_prompt() is asked for it and for a model without one
    Then   the transcribed prompt comes back, and nothing for the other
    """
    tag = next(iter(candidates.CARD_PROMPTS))

    assert candidates.card_prompt(tag) == candidates.CARD_PROMPTS[tag]
    assert candidates.card_prompt("chandra-ocr-2") == ""
