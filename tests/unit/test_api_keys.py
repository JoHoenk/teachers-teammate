"""The API keys the app holds, and the two places they can come from."""

from __future__ import annotations

import os

from teachers_teammate.application.api_keys import ApiKeys


def test_a_key_the_app_holds_is_the_key_of_its_provider(monkeypatch) -> None:
    """
    Given  a key remembered for OpenAI
    When   the key of that provider is asked for
    Then   it is the key the app holds, and the environment is untouched
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    keys = ApiKeys()

    keys.remember("OPENAI_API_KEY", "sk-held")

    assert keys.key_for("openai") == "sk-held"
    assert "OPENAI_API_KEY" not in os.environ


def test_a_key_exported_before_start_is_found_in_the_environment(monkeypatch) -> None:
    """
    Given  a key exported in the environment and nothing the app holds
    When   the key of that provider is asked for
    Then   the environment's key is the one used, and it is known to be set outside the app
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported")
    keys = ApiKeys()

    assert keys.key_for("openai") == "sk-exported"
    assert keys.key("OPENAI_API_KEY") == "sk-exported"
    assert keys.held("OPENAI_API_KEY") is None
    assert keys.set_outside_app("OPENAI_API_KEY") is True


def test_a_key_the_app_holds_wins_over_the_environment(monkeypatch) -> None:
    """
    Given  a key exported in the environment and another one typed into the app
    When   the key of that provider is asked for
    Then   the typed key is used, and the key no longer counts as set outside the app
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported")
    keys = ApiKeys()

    keys.remember("OPENAI_API_KEY", "sk-typed")

    assert keys.key_for("openai") == "sk-typed"
    assert keys.set_outside_app("OPENAI_API_KEY") is False


def test_a_provider_that_needs_no_key_has_none(monkeypatch) -> None:
    """
    Given  the app holds and the environment has a key for OpenAI
    When   the key of the local server, which reads none, is asked for
    Then   there is none
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported")
    keys = ApiKeys()
    keys.remember("OPENAI_API_KEY", "sk-typed")

    assert keys.key_for("ollama") is None


def test_a_key_no_longer_held_lets_the_environment_count_again(monkeypatch) -> None:
    """
    Given  a key the app holds while the environment has another one
    When   the app is told to hold nothing for it
    Then   the environment's key is used again
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported")
    keys = ApiKeys()
    keys.remember("OPENAI_API_KEY", "sk-typed")

    keys.remember("OPENAI_API_KEY", None)

    assert keys.held("OPENAI_API_KEY") is None
    assert keys.key_for("openai") == "sk-exported"
