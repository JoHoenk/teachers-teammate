"""Unit tests for teachers_teammate.application.providers."""

from __future__ import annotations

from typing import cast

import pytest

from teachers_teammate.application import api_keys as api_keys_module
from teachers_teammate.application import providers as providers_module
from teachers_teammate.application.api_keys import ApiKeys
from teachers_teammate.application.composition import build_app_services
from teachers_teammate.application.ports import OllamaServer
from teachers_teammate.application.providers import ProviderService
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.domain.ollama_snapshot import InstalledModel, ServerSnapshot
from teachers_teammate.domain.providers import ProviderInfo

from ..fakes.fake_secret_store import FakeSecretStore


def _as_listing(fake):
    """Wrap a fake that returns model names as the checked listing the service asks for."""
    return lambda provider, **kwargs: ModelListing(list(fake(provider, **kwargs)))


class _NoServer:
    """An Ollama server nothing here asks: these tests are about the providers and their keys."""

    def check_connection(self, url: str, model: str = "") -> tuple[bool, bool, str]:
        """Fail loudly: a test that reaches the server needs the fake below instead."""
        raise AssertionError(f"the server at {url} was asked about {model!r}")


class _FakeServer:
    """An Ollama server port: the answer it gives, and what it was asked to forget."""

    def __init__(self, answer: ServerSnapshot) -> None:
        self.answer = answer
        self.calls = 0
        self.forgotten: list[str] = []

    def snapshot(self, url: str) -> ServerSnapshot:
        """Return the scripted answer and count the request."""
        _ = url
        self.calls += 1
        return self.answer

    def cached_snapshot(self, url: str, *, max_age: float | None = None) -> ServerSnapshot | None:
        """Return the scripted answer as what is held, without counting a request."""
        _ = url, max_age
        return None if not self.answer.models else self.answer

    def forget(self, url: str) -> None:
        """Remember that the server was forgotten."""
        self.forgotten.append(url)


def _service(**kwargs) -> ProviderService:
    """Return the service over a server that is never asked."""
    return ProviderService(
        ollama=cast(OllamaServer, _NoServer()), credentials=ApiKeys(FakeSecretStore()), **kwargs
    )


def _service_with(server: object) -> ProviderService:
    """Return the service over the fake Ollama server port *server*."""
    return ProviderService(
        ollama=cast(OllamaServer, server), credentials=ApiKeys(FakeSecretStore())
    )


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
    providers = _service()

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
    providers = _service()

    providers.list_provider_models("openai")
    providers.invalidate_model_cache("openai")
    providers.list_provider_models("openai")

    assert call_count == 2


@pytest.mark.unit
def test_one_ollama_server_is_shared_by_every_service_that_reads_it() -> None:
    """
    Given  the built services
    When   the services that read the Ollama server are looked at
    Then   they all hold the one instance, so a pull seen by one is seen by all and no second
           cache can drift away from it
    """
    services = build_app_services()

    assert services.providers._ollama is services.ollama
    assert services.recommendations._ollama is services.ollama
    assert services.speeds._ollama is services.ollama
    assert services.run_checks._ollama is services.ollama
    assert services.comparison._ollama is services.ollama


@pytest.mark.use_case("Model_Category_Guidance")
def test_an_ollama_list_comes_from_the_service_that_owns_the_server() -> None:
    """
    Given  an Ollama server that answered which models it has installed
    When   the models are listed twice and the list is then forgotten
    Then   every look goes to the service that keeps that answer, not through the provider
           module, and forgetting is handed to it
    """
    server = _FakeServer(ServerSnapshot(models=(InstalledModel("a:latest"),)))
    providers = _service_with(server)

    first = providers.list_provider_models("ollama", base_url="http://host:11434/")
    second = providers.list_provider_models("ollama", base_url="http://host:11434")
    providers.invalidate_model_cache("ollama", "http://host:11434")

    assert first == second == ["a:latest"]
    assert server.calls == 2
    assert server.forgotten == ["http://host:11434"]


@pytest.mark.unit
def test_an_unreachable_ollama_says_why_instead_of_listing_nothing() -> None:
    """
    Given  an Ollama server that cannot be asked
    When   the models are listed
    Then   the listing is a failed one that carries the reason without the check line's
           mark, so a picker can say why in its own line instead of showing an empty list
    """
    server = _FakeServer(ServerSnapshot(unreachable="✗ Cannot connect to Ollama"))
    providers = _service_with(server)

    listing = providers.list_provider_models_checked("ollama", base_url="http://127.0.0.1:11434")

    assert listing.models == []
    assert listing.source == "failed"
    assert listing.problem == "Cannot connect to Ollama"


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
    providers = _service()

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
    providers = _service(importable_module=lambda module: module not in absent)

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
    packages = _service().list_provider_packages()

    pip_names = [pip for _module, pip in packages.values()]
    assert pip_names.count("langchain-mistralai") == 1
    assert len(pip_names) == len(set(pip_names))
    assert {"openai", "anthropic", "google", "cohere", "ollama"} <= set(packages)


@pytest.mark.parametrize(
    ("info", "key_set", "missing"),
    [
        (
            ProviderInfo(name="any", display_name="Any", needs_api_key=True, env_key="TT_TEST_KEY"),
            False,
            True,
        ),
        (
            ProviderInfo(name="any", display_name="Any", needs_api_key=True, env_key="TT_TEST_KEY"),
            True,
            False,
        ),
        (ProviderInfo(name="any", display_name="Any"), False, False),
        (None, False, False),
    ],
)
def test_provider_key_missing_follows_the_provider_and_environment(
    monkeypatch: pytest.MonkeyPatch, info: ProviderInfo | None, key_set: bool, missing: bool
) -> None:
    """
    Given  a declaration that does or does not need an API key, with the app holding one or not
    When   the service is asked whether the key is missing
    Then   it is missing only for a provider that needs a key while the app holds none
    """
    monkeypatch.setattr(providers_module, "get_provider_info", lambda _provider: info)
    monkeypatch.setattr(api_keys_module.llm_factory, "get_provider_info", lambda _provider: info)
    keys = ApiKeys(FakeSecretStore())
    if key_set:
        keys.remember("TT_TEST_KEY", "k")

    service = ProviderService(ollama=cast(OllamaServer, _NoServer()), credentials=keys)

    assert service.provider_key_missing("any") is missing


# ── what a provider declares ──────────────────────────────────────────────


@pytest.mark.unit
def test_names_and_key_answers_come_from_the_declaration() -> None:
    """
    Given  the shipped providers and a name nobody declares
    When   the service is asked for a display name, a key variable and a key page
    Then   each answer is what the provider declares, and an unknown name has none
    """
    service = _service()

    assert service.display_name("mistral_eu") == "Mistral (EU)"
    assert service.env_key("mistral_eu") == "MISTRAL_API_KEY"
    assert service.key_page("MISTRAL_API_KEY") == "https://console.mistral.ai/api-keys"
    assert service.display_name("nonexistent") == "nonexistent"
    assert service.env_key("nonexistent") == ""
    assert service.key_page("NOT_A_KEY") == ""


@pytest.mark.unit
def test_a_shared_package_names_every_service_that_needs_it() -> None:
    """
    Given  the shipped providers, two of which share one package
    When   the service is asked which services need that package
    Then   both are named, and a package nobody declares has none
    """
    service = _service()

    assert service.package_services("langchain-mistralai") == ["Mistral", "Mistral (EU)"]
    assert service.package_services("langchain-openai") == ["OpenAI (ChatGPT)"]
    assert service.package_services("no-such-package") == []


@pytest.mark.unit
def test_only_ollamas_package_is_an_optional_connector() -> None:
    """
    Given  the shipped providers
    When   the service is asked whether a provider's package is an optional connector
    Then   it is for Ollama, which the app talks to directly, and not for an online service
    """
    service = _service()

    assert service.package_is_connector("ollama") is True
    assert service.package_is_connector("openai") is False
    assert service.package_is_connector("nonexistent") is False


@pytest.mark.unit
def test_provider_needs_key_follows_the_declaration() -> None:
    """
    Given  a service talked to directly, an online service and a name nobody declares
    When   the service is asked whether the provider needs an API key
    Then   only the online service does
    """
    service = _service()

    assert service.provider_needs_key("ollama") is False
    assert service.provider_needs_key("openai") is True
    assert service.provider_needs_key("nonexistent") is False


# ── check_connection ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_check_connection_provider_ollama_success(monkeypatch) -> None:
    """
    Given  provider='ollama' and OllamaClient.check_connection reports success
    When   the provider's check for ollama is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    from teachers_teammate.infrastructure import ollama_client  # noqa: PLC0415

    monkeypatch.setattr(
        ollama_client.OllamaClient,
        "check_connection",
        lambda self, model="": (True, True, "✓ Connected — 1 model(s) available"),
    )
    services = build_app_services()
    ok, _, msg = services.providers.check_connection("ollama", url="http://127.0.0.1:11434")
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
    ok, _, msg = services.providers.check_connection(provider, url="http://127.0.0.1:11434")
    assert ok is True
    assert env_var in msg


@pytest.mark.unit
def test_a_key_removed_in_the_app_is_not_taken_from_the_environment(monkeypatch) -> None:
    """
    Given  a key exported in the environment that the app then removed
    When   the connection of that provider is checked
    Then   it answers that the key is not set
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported")
    services = build_app_services()
    services.keys.remember("OPENAI_API_KEY", "")

    ok, _, msg = services.providers.check_connection("openai", url="http://127.0.0.1:11434")

    assert ok is False
    assert "OPENAI_API_KEY" in msg


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
    ok, _, msg = services.providers.check_connection(provider, url="http://127.0.0.1:11434")
    assert ok is False
    assert env_var in msg


# ── list_supported_suffixes
