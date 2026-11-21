"""Unit tests for teachers_teammate.application.providers."""

from __future__ import annotations

import pytest

from teachers_teammate.application import providers as providers_module
from teachers_teammate.application.composition import build_app_services
from teachers_teammate.application.providers import ProviderService
from teachers_teammate.domain.model_listing import ModelListing


def _as_listing(fake):
    """Wrap a fake that returns model names as the checked listing the service asks for."""
    return lambda provider, **kwargs: ModelListing(list(fake(provider, **kwargs)))


# ── the model lists ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_list_provider_models_uses_ttl_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  a ProviderService whose list_provider_models is called once
    When   list_provider_models() is called a second time within the TTL window
    Then   the underlying infrastructure function is called only once (cache hit)
    """
    call_count = 0

    def _fake_list(provider, **_kwargs):
        nonlocal call_count
        call_count += 1
        return ["model-a", "model-b"]

    monkeypatch.setattr(providers_module, "list_provider_models_checked", _as_listing(_fake_list))
    providers = ProviderService()

    first = providers.list_provider_models("openai")
    second = providers.list_provider_models("openai")

    assert first == ["model-a", "model-b"]
    assert second == ["model-a", "model-b"]
    assert call_count == 1


@pytest.mark.unit
def test_invalidate_model_cache_forces_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  a ProviderService with a cached model list for 'openai'
    When   invalidate_model_cache('openai') is called and then list_provider_models again
    Then   the underlying infrastructure function is called a second time
    """
    call_count = 0

    def _fake_list(provider, **_kwargs):
        nonlocal call_count
        call_count += 1
        return ["model-a"]

    monkeypatch.setattr(providers_module, "list_provider_models_checked", _as_listing(_fake_list))
    providers = ProviderService()

    providers.list_provider_models("openai")
    providers.invalidate_model_cache("openai")
    providers.list_provider_models("openai")

    assert call_count == 2


@pytest.mark.use_case("Model_Category_Guidance")
def test_ollama_model_cache_key_ignores_url_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  the model list cached under "http://host:11434/"
    When   it is invalidated through "http://host:11434"
    Then   the entry is gone (both spellings share one cache entry)
    """
    calls: list[str] = []

    def fake_list(provider: str, **_kw) -> list[str]:
        calls.append(provider)
        return ["a:latest"]

    monkeypatch.setattr(providers_module, "list_provider_models_checked", _as_listing(fake_list))
    providers = ProviderService()

    providers.list_provider_models("ollama", base_url="http://host:11434/")
    providers.list_provider_models("ollama", base_url="http://host:11434")
    assert len(calls) == 1  # second call was a cache hit

    providers.invalidate_model_cache("ollama", "http://host:11434")
    providers.list_provider_models("ollama", base_url="http://host:11434/")
    assert len(calls) == 2


@pytest.mark.unit
def test_empty_model_list_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  an Ollama server that is unreachable (empty model list) at first
    When   it is started and the list is requested again
    Then   the second call asks the server again instead of serving the cached failure
    """
    answers = [[], ["qwen3-vl:8b"]]
    monkeypatch.setattr(
        providers_module,
        "list_provider_models_checked",
        _as_listing(lambda *_a, **_k: answers.pop(0)),
    )
    providers = ProviderService()

    first = providers.list_provider_models("ollama", base_url="http://127.0.0.1:11434")
    second = providers.list_provider_models("ollama", base_url="http://127.0.0.1:11434")

    assert first == []
    assert second == ["qwen3-vl:8b"]


@pytest.mark.unit
def test_failed_listing_is_asked_again_while_a_live_one_is_remembered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  a provider whose first answer is a failed listing (no key) and then a live one
    When   the models are listed three times
    Then   the failed answer is not remembered (the second call asks again, and so notices a
           key entered meanwhile), the live one is, and the cache reports its source
    """
    answers = [
        ModelListing(["built-in"], "failed", "no Mistral key is set"),
        ModelListing(["glm-live"], "live"),
    ]
    calls = 0

    def fake(_provider: str, **_kw: str) -> ModelListing:
        nonlocal calls
        calls += 1
        return answers.pop(0)

    monkeypatch.setattr(providers_module, "list_provider_models_checked", fake)
    providers = ProviderService()

    first = providers.list_provider_models_checked("mistral")
    second = providers.list_provider_models_checked("mistral")
    third = providers.list_provider_models_checked("mistral")

    assert (first.source, second.source, third.source) == ("failed", "live", "live")
    assert calls == 2
    cached = providers.get_cached_listing("mistral")
    assert cached is not None and cached.models == ["glm-live"]


# ── what a provider needs ─────────────────────────────────────────────────


@pytest.mark.unit
def test_missing_provider_package_names_the_pip_package_only_when_absent() -> None:
    """
    Given  a service where langchain_mistralai is absent but langchain_openai is installed
    When   missing_provider_package() is asked for each, and for Ollama
    Then   Mistral (global and EU) names its pip package, the installed one and Ollama (whose
           connector is optional) name none
    """
    absent = {"langchain_mistralai"}
    providers = ProviderService(importable_module=lambda module: module not in absent)

    assert providers.missing_provider_package("mistral") == "langchain-mistralai"
    assert providers.missing_provider_package("mistral_eu") == "langchain-mistralai"
    assert providers.missing_provider_package("openai") is None
    assert providers.missing_provider_package("ollama") is None


@pytest.mark.unit
def test_provider_packages_list_a_shared_package_once() -> None:
    """
    Given  Mistral global and EU, which use one package
    When   list_provider_packages() is read
    Then   the package appears once, and every other provider has its own entry
    """
    packages = ProviderService().list_provider_packages()

    pip_names = [pip for _module, pip in packages.values()]
    assert pip_names.count("langchain-mistralai") == 1
    assert len(pip_names) == len(set(pip_names))
    assert {"openai", "anthropic", "google", "cohere", "ollama"} <= set(packages)


@pytest.mark.parametrize(
    ("info", "key_set", "missing"),
    [
        ({"needs_api_key": True, "env_key": "TT_TEST_KEY"}, False, True),
        ({"needs_api_key": True, "env_key": "TT_TEST_KEY"}, True, False),
        ({"needs_api_key": False, "env_key": ""}, False, False),
        ({}, False, False),
    ],
)
def test_provider_key_missing_follows_the_provider_info_and_environment(
    monkeypatch: pytest.MonkeyPatch, info: dict, key_set: bool, missing: bool
) -> None:
    """
    Given  a provider that does or does not need an API key, with the key set or not
    When   the service is asked whether the key is missing
    Then   it is missing only for a provider that needs a key while none is set
    """
    monkeypatch.delenv("TT_TEST_KEY", raising=False)
    if key_set:
        monkeypatch.setenv("TT_TEST_KEY", "k")
    monkeypatch.setattr(providers_module, "get_provider_info", lambda _provider: info)

    assert ProviderService().provider_key_missing("any") is missing


# ── check_connection ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_check_connection_provider_ollama_success(monkeypatch) -> None:
    """
    Given  provider='ollama' and OllamaClient.check_connection reports success
    When   the provider's check for ollama is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(
        ollama_utils.OllamaClient,
        "check_connection",
        lambda self, model="": (True, True, "✓ Connected — 1 model(s) available"),
    )
    services = build_app_services()
    ok, _, msg = services.providers.check_connection("ollama")
    assert ok is True


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("google", "GOOGLE_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
        ("mistral_eu", "MISTRAL_API_KEY"),
        ("cohere", "COHERE_API_KEY"),
    ],
)
@pytest.mark.unit
@pytest.mark.use_case("Service_Availability_Check")
def test_check_connection_provider_returns_true_when_env_var_set(
    provider: str, env_var: str, monkeypatch
) -> None:
    """
    Given  a known LLM provider and its API key env var is set to a non-empty value
    When   the provider's check for <name> is called
    Then   returns (connected=True, model_ok=True, <message containing the env var name>)
    """
    monkeypatch.setenv(env_var, "sk-test")
    services = build_app_services()
    ok, _, msg = services.providers.check_connection(provider)
    assert ok is True
    assert env_var in msg


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
    ],
)
@pytest.mark.unit
def test_check_connection_provider_returns_false_when_env_var_missing(
    provider: str, env_var: str, monkeypatch
) -> None:
    """
    Given  a known LLM provider and its API key env var is not set
    When   the provider's check for <name> is called
    Then   returns (connected=False, model_ok=False, <message containing the env var name>)
    """
    monkeypatch.delenv(env_var, raising=False)
    services = build_app_services()
    ok, _, msg = services.providers.check_connection(provider)
    assert ok is False
    assert env_var in msg


# ── list_supported_suffixes


@pytest.mark.unit
def test_refreshing_the_ollama_model_list_drops_the_classifications_of_that_server() -> None:
    """
    Given  the built services, with a classification kept for one Ollama server
    When   the model list of that server is refreshed
    Then   the recommendations forget that server's classifications, and no other's
    """
    from teachers_teammate.infrastructure.ollama_utils import normalize_ollama_url  # noqa: PLC0415

    services = build_app_services()
    kept = {"qwen3.5:9b": frozenset()}
    url, other = "http://127.0.0.1:11434", "http://192.168.1.50:11434"
    cache = services.recommendations._classification
    cache[normalize_ollama_url(url)] = dict(kept)
    cache[normalize_ollama_url(other)] = dict(kept)

    services.providers.invalidate_model_cache("ollama", url)

    assert normalize_ollama_url(url) not in cache
    assert normalize_ollama_url(other) in cache
