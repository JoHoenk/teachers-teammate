"""Unit tests for the request profile of the exchange (``domain/model_catalog.py``)."""

from __future__ import annotations

from dataclasses import replace

from teachers_teammate.domain.model_catalog import (
    DEFAULT_REQUEST_PROFILE,
    RequestProfile,
    StepProfile,
    request_profile,
)


def test_the_default_profile_runs_the_three_step_exchange() -> None:
    """
    Given  the profile a model the catalog does not know gets
    When   it is read
    Then   it names exchange T, checks the unclear wording and explains in batches of 20
    """
    assert DEFAULT_REQUEST_PROFILE.exchange == "T"
    assert DEFAULT_REQUEST_PROFILE.unclear_check is True
    assert DEFAULT_REQUEST_PROFILE.explain_batch == 20


def test_every_step_has_its_own_profile() -> None:
    """
    Given  a profile with a different temperature per step
    When   the profile of a step is asked for
    Then   each step answers with its own
    """
    profile = RequestProfile(
        correct=StepProfile(temperature=0.1),
        explain=StepProfile(temperature=0.2),
        unclear=StepProfile(temperature=0.3),
    )

    assert profile.step("correct").temperature == 0.1
    assert profile.step("explain").temperature == 0.2
    assert profile.step("unclear").temperature == 0.3


def test_the_review_step_of_the_prepared_exchange_uses_the_first_step() -> None:
    """
    Given  a profile
    When   the profile of R's review step is asked for
    Then   it is the one of step ①, because R rewrites the text
    """
    profile = RequestProfile(correct=StepProfile(temperature=0.4))

    assert profile.step("review").temperature == 0.4


def test_the_temperature_and_thinking_of_the_profile_are_the_first_step() -> None:
    """
    Given  a profile whose first step has a temperature and a thinking level
    When   the profile's own temperature and thinking are read
    Then   they are the ones of step ①, which is the step that rewrites the text
    """
    profile = RequestProfile(correct=StepProfile(temperature=0.7, think="low"))

    assert profile.temperature == 0.7
    assert profile.think == "low"


def test_a_model_gets_its_recommended_temperature_in_every_step() -> None:
    """
    Given  a catalog model with a recommended temperature
    When   its request profile is asked for
    Then   all three steps carry that temperature
    """
    profile = request_profile("qwen3:8b")

    assert profile.correct.temperature == profile.explain.temperature
    assert profile.correct.temperature == profile.unclear.temperature


def test_a_reasoning_family_is_asked_for_its_lowest_level_in_every_step() -> None:
    """
    Given  a model of a family that ignores the thinking flag
    When   its request profile is asked for
    Then   all three steps ask for the lowest reasoning level
    """
    profile = request_profile("gpt-oss:20b")

    assert profile.correct.think == "low"
    assert profile.explain.think == "low"
    assert profile.unclear.think == "low"


def test_the_fingerprint_changes_with_a_step_setting() -> None:
    """
    Given  two profiles that differ in one step's temperature
    When   their fingerprints are compared
    Then   they differ, so a cached answer of one is not reused for the other
    """
    first = RequestProfile()
    second = replace(first, explain=StepProfile(temperature=0.9))

    assert first.fingerprint() != second.fingerprint()


def test_the_fingerprint_changes_with_the_exchange_and_the_switches() -> None:
    """
    Given  profiles that differ in the exchange, the unclear switch or the batch size
    When   their fingerprints are compared
    Then   every one of them differs
    """
    base = RequestProfile().fingerprint()

    assert replace(RequestProfile(), exchange="R-walk").fingerprint() != base
    assert replace(RequestProfile(), unclear_check=False).fingerprint() != base
    assert replace(RequestProfile(), explain_batch=5).fingerprint() != base


def test_the_fingerprint_is_stable_for_the_same_profile() -> None:
    """
    Given  two profiles with the same settings
    When   their fingerprints are compared
    Then   they are equal, so the cache key of a run does not change between runs
    """
    assert RequestProfile().fingerprint() == RequestProfile().fingerprint()
