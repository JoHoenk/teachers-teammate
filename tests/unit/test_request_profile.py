"""Unit tests for the request profile of the exchange (``domain/request_profile.py``)."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from teachers_teammate.domain import model_catalog
from teachers_teammate.domain.model_catalog import OLLAMA_CATALOG, find_spec, request_profile
from teachers_teammate.domain.proofreading.result import ExchangeKind
from teachers_teammate.domain.request_profile import (
    DEFAULT_REQUEST_PROFILE,
    STEP_ONE_PROMPTS,
    RequestProfile,
    StepProfile,
    review_temperature,
)


def test_the_default_profile_runs_the_three_step_exchange() -> None:
    """
    Given  the profile a model the catalog does not know gets
    When   it is read
    Then   it names the text-first exchange, checks the unclear wording and explains in
           batches of 20
    """
    assert DEFAULT_REQUEST_PROFILE.exchange is ExchangeKind.TEXT_FIRST
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


def test_step_one_gets_the_measured_temperature_and_the_others_the_makers() -> None:
    """
    Given  a catalog model with a measured step-① temperature and a maker's value
    When   its request profile is asked for
    Then   step ① carries the measured value and steps ② and ③ the maker's
    """
    profile = request_profile("qwen3.5:9b")

    assert profile.correct.temperature == 0.05
    assert profile.explain.temperature == 0.7
    assert profile.unclear.temperature == 0.7


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


def test_the_fingerprint_changes_with_the_switches() -> None:
    """
    Given  profiles that differ in the unclear switch or the batch size
    When   their fingerprints are compared
    Then   every one of them differs
    """
    base = RequestProfile().fingerprint()

    assert replace(RequestProfile(), unclear_check=False).fingerprint() != base
    assert replace(RequestProfile(), explain_batch=5).fingerprint() != base


def test_the_fingerprint_names_the_exchange_by_its_stored_id() -> None:
    """
    Given  the default profile
    When   its fingerprint is read
    Then   it starts with the stored id "T", so the enum's own word can change without making
           every stored proofreading stale
    """
    assert RequestProfile().fingerprint().startswith("T|")


def test_the_fingerprint_is_stable_for_the_same_profile() -> None:
    """
    Given  two profiles with the same settings
    When   their fingerprints are compared
    Then   they are equal, so the cache key of a run does not change between runs
    """
    assert RequestProfile().fingerprint() == RequestProfile().fingerprint()


def test_a_step_can_carry_sampling_values_besides_the_temperature() -> None:
    """
    Given  a step profile with a presence penalty and a top-p, given in any order
    When   the step is read
    Then   the values are kept sorted by name and read back as the options dict of a request
    """
    step = StepProfile(options=(("top_p", 0.9), ("presence_penalty", 0.0)))

    assert step.options == (("presence_penalty", 0.0), ("top_p", 0.9))
    assert step.sampling() == {"presence_penalty": 0.0, "top_p": 0.9}
    assert StepProfile().sampling() == {}


@pytest.mark.parametrize("name", ["seed", "num_ctx", "temperature", "banana"])
def test_a_value_a_request_may_not_carry_is_refused(name: str) -> None:
    """
    Given  an option that belongs to the run, the app or nobody (seed, window, temperature, a typo)
    When   a step profile is built with it
    Then   it is refused and the message names the allowed options
    """
    with pytest.raises(ValueError, match="unknown sampling option") as refused:
        StepProfile(options=((name, 1.0),))

    assert "presence_penalty" in str(refused.value)


def test_the_fingerprint_changes_with_a_sampling_value_and_only_then() -> None:
    """
    Given  a profile without sampling values and the same profile with one on step one
    When   their fingerprints are compared
    Then   they differ, the value is in the sampled one, and the plain one lists none, so a cached
           answer is never reused across sampling values
    """
    plain = RequestProfile()
    sampled = replace(plain, correct=StepProfile(options=(("presence_penalty", 0.0),)))

    assert plain.fingerprint() != sampled.fingerprint()
    assert "presence_penalty=0" in sampled.fingerprint()
    assert "=" not in plain.fingerprint()


def test_a_repeated_name_and_a_fractional_top_k_are_refused() -> None:
    """
    Given  a step profile that names one option twice, and one with top_k 20.5
    When   it is built
    Then   both are refused, so a value never depends on the order it was given in
    """
    with pytest.raises(ValueError, match="more than once"):
        StepProfile(options=(("top_p", 0.5), ("top_p", 0.9)))
    with pytest.raises(ValueError, match="whole number"):
        StepProfile(options=(("top_k", 20.5),))
    assert StepProfile(options=(("top_k", 20.0),)).sampling() == {"top_k": 20.0}


def test_two_close_sampling_values_have_two_fingerprints() -> None:
    """
    Given  two profiles whose top_p differs in the eighth digit
    When   their fingerprints are compared
    Then   they differ, so a cached answer is not reused for the other value
    """
    low = replace(RequestProfile(), correct=StepProfile(options=(("top_p", 0.9),)))
    near = replace(RequestProfile(), correct=StepProfile(options=(("top_p", 0.90000001),)))

    assert low.fingerprint() != near.fingerprint()


def test_only_step_one_can_carry_a_prompt_variant_and_only_a_known_one() -> None:
    """
    Given  a step profile with a variant, an unknown id, and a profile with a variant on step two
    When   each is built
    Then   the first is kept, the unknown id is refused, and step two refuses a variant
    """
    assert StepProfile(prompt="keep").prompt == "keep"
    assert StepProfile().prompt == ""
    with pytest.raises(ValueError, match="no step ① prompt variant"):
        StepProfile(prompt="banana")
    with pytest.raises(ValueError, match="only step ① has prompt variants"):
        RequestProfile(explain=StepProfile(prompt="keep"))


def test_no_model_gets_a_prompt_variant_until_one_is_adopted(monkeypatch) -> None:
    """
    Given  the table of adopted variants, empty today
    When   the profile of a model is asked for, then a variant is adopted for one tag
    Then   every model has the standard prompt first; afterwards only that tag has the variant
           (also written with :latest), and an online service never does
    """
    assert STEP_ONE_PROMPTS == {}
    assert request_profile("gemma4:12b").correct.prompt == ""

    monkeypatch.setitem(STEP_ONE_PROMPTS, "qwen3.5:4b", "keep")

    assert request_profile("qwen3.5:4b").correct.prompt == "keep"
    assert request_profile("qwen3.5:4b").explain.prompt == ""
    assert request_profile("gemma4:12b").correct.prompt == ""
    assert request_profile("qwen3.5:4b", "openai").correct.prompt == ""


def test_a_variant_of_a_model_has_the_prompt_of_the_model(monkeypatch) -> None:
    """
    Given  a prompt variant adopted for "qwen3.5:9b"
    When   the profile of its other quantisation and of another model is asked for
    Then   the quantisation has the variant, as it has the model's temperature, and the other
           model has the standard prompt
    """
    monkeypatch.setitem(STEP_ONE_PROMPTS, "qwen3.5:9b", "keep")

    assert request_profile("qwen3.5:9b-q8_0").correct.prompt == "keep"
    assert request_profile("qwen3.5:9b").correct.prompt == "keep"
    assert request_profile("qwen3.5:4b-q8_0").correct.prompt == ""


def test_the_fingerprint_of_a_profile_follows_the_prompt_variant_and_its_text(monkeypatch) -> None:
    """
    Given  profiles that differ in the step-one prompt variant, and a variant whose text changes
    When   the fingerprints are compared
    Then   the standard prompt keeps the fingerprint it had, each variant has its own, and the
           same id with another text is another fingerprint
    """
    from teachers_teammate.domain import correction_prompts  # noqa: PLC0415

    standard = RequestProfile().fingerprint()
    keep = RequestProfile(correct=StepProfile(prompt="keep")).fingerprint()
    find = RequestProfile(correct=StepProfile(prompt="find")).fingerprint()

    assert standard == RequestProfile(correct=StepProfile(prompt="")).fingerprint()
    assert len({standard, keep, find}) == 3

    monkeypatch.setitem(
        correction_prompts.CORRECT_VARIANTS, "keep", correction_prompts.KEEP_SYSTEM + " More."
    )

    assert RequestProfile(correct=StepProfile(prompt="keep")).fingerprint() != keep


def test_every_profile_names_an_exchange_the_app_can_build() -> None:
    """
    Given  the catalog's models and every provider module the app ships
    When   their request profiles are read
    Then   each names an exchange of ``EXCHANGES``, so no profile points at nothing
    """
    from teachers_teammate.domain.model_catalog import DEFAULT_WRITING_MODEL, OLLAMA_CATALOG  # noqa: PLC0415
    from teachers_teammate.infrastructure.llm_factory import list_providers  # noqa: PLC0415
    from teachers_teammate.infrastructure.proofreading.exchanges import (  # noqa: PLC0415
        EXCHANGES,
    )

    profiles = [spec.request_profile for spec in OLLAMA_CATALOG]
    profiles += [request_profile(DEFAULT_WRITING_MODEL, provider) for provider in list_providers()]

    assert profiles
    assert {profile.exchange for profile in profiles} <= set(EXCHANGES)


def test_unknown_models_get_the_default_profile_and_the_measured_step_one_temperature() -> None:
    """
    Given  a tag the catalog does not know
    When   its request profile is asked for
    Then   it is the default apart from step ①, which runs at the measured 0.4
    """
    profile = request_profile("some-model:7b")

    assert profile.compact_json is False
    assert profile.exchange == DEFAULT_REQUEST_PROFILE.exchange
    assert profile.explain == DEFAULT_REQUEST_PROFILE.explain
    assert profile.unclear == DEFAULT_REQUEST_PROFILE.unclear
    assert profile.temperature == 0.4


def test_a_variant_of_a_low_temperature_model_keeps_its_models_step_one_temperature() -> None:
    """
    Given  a quantised tag of a model that falls with every step up in temperature
    When   its request profile is asked for
    Then   step ① runs at that model's 0.05, not at the default 0.4, and a longer name that only
           starts like a measured tag does not
    """
    assert request_profile("qwen3.5:4b-q8_0").temperature == 0.05
    assert request_profile("ministral-3:8b-instruct-2512-q4_K_M").temperature == 0.05
    assert request_profile("qwen3.5:40b").temperature == 0.4


def test_every_catalog_model_starts_on_the_default_profile() -> None:
    """
    Given  the catalog
    When   each model's request profile is read
    Then   all of them are the default, so no request changes until a gate-1 run decides
    """
    assert {spec.request_profile for spec in OLLAMA_CATALOG} == {DEFAULT_REQUEST_PROFILE}


def test_a_catalog_profile_is_found_by_its_tag() -> None:
    """
    Given  a catalog model whose profile asks for compact JSON
    When   the profile is asked for by tag, with and without the ``:latest`` suffix
    Then   the catalog's profile is the base, not the default
    """
    spec = replace(
        find_spec("gemma4:e4b"),  # ty: ignore[invalid-argument-type]  # the tag is in the catalog
        request_profile=RequestProfile(compact_json=True),
    )
    catalog = tuple(spec if other.tag == spec.tag else other for other in OLLAMA_CATALOG)
    with patch.object(model_catalog, "OLLAMA_CATALOG", catalog):
        assert request_profile("gemma4:e4b").compact_json is True
        assert request_profile("gemma4").compact_json is False


def test_step_one_gets_the_measured_temperature_and_the_other_steps_the_makers() -> None:
    """
    Given  models with and without a measured step-① temperature, with and without ``:latest``
    When   their request profile and review temperature are asked for
    Then   step ① gets the measured value (0.4 where none was measured), steps ② and ③ the
           maker's, and the review the maker's or 0.7
    """
    assert request_profile("gemma4:e4b").correct.temperature == 0.4
    assert request_profile("gemma4:e4b").explain.temperature == 1.0
    assert request_profile("qwen3.5:9b").correct.temperature == 0.05
    assert request_profile("qwen3.5:9b").explain.temperature == 0.7
    assert request_profile("ministral-3:8b").correct.temperature == 0.05
    assert request_profile("gemma4:e4b:latest").correct.temperature == 0.4
    assert request_profile("gpt-oss:20b").correct.temperature == 0.4
    assert request_profile("hf.co/some/candidate:Q4").correct.temperature == 0.4
    assert review_temperature("gemma4:e4b") == 1.0
    assert review_temperature("gpt-oss:20b") == 0.7


def test_the_temperature_tables_apply_to_ollama_only() -> None:
    """
    Given  an online provider asked about a name that is also a recommended Ollama tag
    When   the request profile and review temperature are asked for
    Then   neither table applies: every step gets 0.2, the review 0.7
    """
    assert request_profile("gemma4:e4b", "google").correct.temperature == 0.2
    assert request_profile("gemma4:e4b", "google").explain.temperature == 0.2
    assert review_temperature("gemma4:e4b", "google") == 0.7


@pytest.mark.use_case("Model_Request_Profile")
def test_the_request_profile_asks_gpt_oss_for_a_level_not_the_flag() -> None:
    """
    Given  gpt-oss (which ignores think=False), a model of another family and an online provider
    When   the request profile of each is read
    Then   gpt-oss gets the lowest reasoning level and the others keep the flag off, so the cache
           key, the evaluation tools and the request read one value
    """
    assert request_profile("gpt-oss:20b").think == "low"
    assert request_profile("hf.co/unsloth/gpt-oss-20b-GGUF:UD-Q4_K_XL").think == "low"
    assert request_profile("gemma4:e4b").think is False
    assert request_profile("gpt-oss:20b", "openai").think is False
