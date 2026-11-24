"""The API keys the app holds, and the three places they can come from."""

from __future__ import annotations

import os

from teachers_teammate.application.api_keys import ApiKeys
from teachers_teammate.domain.credentials import KeyLocation

from ..fakes.fake_secret_store import FakeSecretStore


def test_a_key_the_app_holds_is_the_key_of_its_provider(monkeypatch) -> None:
    """
    Given  a key remembered for OpenAI
    When   the key of that provider is asked for
    Then   it is the key the app holds, and the environment is untouched
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    keys = ApiKeys(FakeSecretStore())

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
    keys = ApiKeys(FakeSecretStore())

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
    keys = ApiKeys(FakeSecretStore())

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
    keys = ApiKeys(FakeSecretStore())
    keys.remember("OPENAI_API_KEY", "sk-typed")

    assert keys.key_for("ollama") is None


def test_a_key_no_longer_held_lets_the_environment_count_again(monkeypatch) -> None:
    """
    Given  a key the app holds while the environment has another one
    When   the app is told to hold nothing for it
    Then   the environment's key is used again
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported")
    keys = ApiKeys(FakeSecretStore())
    keys.remember("OPENAI_API_KEY", "sk-typed")

    keys.remember("OPENAI_API_KEY", None)

    assert keys.held("OPENAI_API_KEY") is None
    assert keys.key_for("openai") == "sk-exported"


def test_a_key_only_the_store_has_is_the_key_of_its_provider(monkeypatch) -> None:
    """
    Given  the key saved in Settings, which the store keeps, and none in the environment
    When   the key of that provider is asked for twice
    Then   it is the stored key both times, and the store was asked once
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = FakeSecretStore()
    store.keys["OPENAI_API_KEY"] = "sk-stored"
    keys = ApiKeys(store)

    assert keys.key_for("openai") == "sk-stored"
    assert keys.key_for("openai") == "sk-stored"

    assert store.asked == ["OPENAI_API_KEY"]


def test_the_environment_wins_over_the_stored_key(monkeypatch) -> None:
    """
    Given  a key in the environment and another one the store keeps
    When   the key of that provider is asked for
    Then   the environment's key is used and the store is not asked at all
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported")
    store = FakeSecretStore()
    store.keys["OPENAI_API_KEY"] = "sk-stored"
    keys = ApiKeys(store)

    assert keys.key_for("openai") == "sk-exported"

    assert store.asked == []


def test_a_store_without_the_key_is_asked_once(monkeypatch) -> None:
    """
    Given  no key anywhere: neither the app nor the environment nor the store
    When   the key is asked for a few times
    Then   there is none, and the store was asked once, not on every call
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = FakeSecretStore()
    keys = ApiKeys(store)

    assert keys.key_for("openai") is None
    assert keys.key("OPENAI_API_KEY") == ""

    assert store.asked == ["OPENAI_API_KEY"]


def test_saving_and_forgetting_a_key_reaches_the_store(monkeypatch) -> None:
    """
    Given  an app with a store that keeps keys in the settings file
    When   a key is saved and later forgotten
    Then   the store holds it and is empty again, and the app uses what it saved
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = FakeSecretStore()
    keys = ApiKeys(store)

    assert keys.save("OPENAI_API_KEY", " sk-new ") is KeyLocation.SETTINGS_FILE
    assert store.keys == {"OPENAI_API_KEY": "sk-new"}

    keys.remember("OPENAI_API_KEY", None)
    assert keys.key("OPENAI_API_KEY") == "sk-new"

    keys.forget("OPENAI_API_KEY")
    assert store.keys == {}
    assert keys.key("OPENAI_API_KEY") == ""
