"""Unit tests for teachers_teammate.infrastructure.llm_factory."""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeListChatModel
import pytest

from teachers_teammate.domain.providers import ProviderInfo
from teachers_teammate.infrastructure.llm_factory import (
    build_llm,
    get_provider_data_region,
    get_provider_info,
    list_provider_models,
    list_provider_models_checked,
    list_providers,
    provider_package,
    provider_package_required,
)
from teachers_teammate.messages import MessageKey


def test_list_providers_returns_non_empty_list() -> None:
    """
    Given  the providers package with at least one provider module
    When   list_providers() is called
    Then   the returned list is non-empty
    """
    providers = list_providers()
    assert len(providers) > 0


def test_list_providers_includes_known_providers() -> None:
    """
    Given  the installed providers package
    When   list_providers() is called
    Then   ollama, openai, anthropic, and google are all in the result
    """
    providers = list_providers()
    for expected in ("ollama", "openai", "anthropic", "google"):
        assert expected in providers, f"Expected provider '{expected}' not found"


def test_list_providers_returns_sorted_list() -> None:
    """
    Given  the installed providers package
    When   list_providers() is called
    Then   the returned list is sorted in alphabetical order
    """
    providers = list_providers()
    assert providers == sorted(providers)


def test_all_providers_are_discovered_by_get_provider_info() -> None:
    """
    Given  all discovered provider names
    When   get_provider_info() is called for each
    Then   each one answers with the ProviderInfo its module declares
    """
    for name in list_providers():
        info = get_provider_info(name)
        assert isinstance(info, ProviderInfo), f"{name}: no ProviderInfo"
        assert info.name == name


def test_get_provider_info_returns_correct_types() -> None:
    """
    Given  all discovered provider names
    When   get_provider_info() is called for each
    Then   the switches are bool and the model list is a tuple of str
    """
    for name in list_providers():
        info = get_provider_info(name)
        assert info is not None
        assert isinstance(info.needs_api_key, bool), f"{name}: needs_api_key not bool"
        assert isinstance(info.needs_base_url, bool), f"{name}: needs_base_url not bool"
        assert isinstance(info.can_list_models, bool), f"{name}: can_list_models not bool"
        assert isinstance(info.models, tuple), f"{name}: models not tuple"
        assert all(isinstance(model, str) for model in info.models), f"{name}: models not str"


def test_build_llm_calls_provider_create(mocker) -> None:
    """
    Given  _load_provider patched to return a mock provider module
    When   build_llm() is called with a provider name and model
    Then   the mock module's create() is called and its return value is returned
    """
    fake = FakeListChatModel(responses=["ok"])
    mock_provider = mocker.Mock()
    mock_provider.create.return_value = fake
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )
    result = build_llm("openai", "gpt-4o-mini", base_url="")
    assert result is fake
    mock_provider.create.assert_called_once()


def test_build_llm_hands_the_key_it_was_given_to_the_provider(mocker, monkeypatch) -> None:
    """
    Given  the environment holds one key for the provider and the app passes another
    When   build_llm() is called with that key
    Then   the provider is built with the key it was given, and the environment is not written
    """
    import os  # noqa: PLC0415

    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER = get_provider_info("openai")
    mock_provider.create.return_value = FakeListChatModel(responses=["ok"])
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    build_llm("openai", "gpt-4o-mini", base_url="", api_key="sk-from-the-app")

    mock_provider.create.assert_called_once_with(
        "gpt-4o-mini", temperature=0.7, api_key="sk-from-the-app"
    )
    assert os.environ["OPENAI_API_KEY"] == "sk-from-the-environment"


def test_build_llm_falls_back_on_the_environment_when_given_no_key(mocker, monkeypatch) -> None:
    """
    Given  a key exported in the environment and no key passed on
    When   build_llm() is called
    Then   the provider is built with the environment's key
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER = get_provider_info("openai")
    mock_provider.create.return_value = FakeListChatModel(responses=["ok"])
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    build_llm("openai", "gpt-4o-mini", base_url="")

    mock_provider.create.assert_called_once_with(
        "gpt-4o-mini", temperature=0.7, api_key="sk-from-the-environment"
    )


def test_build_llm_with_an_empty_key_does_not_fall_back_on_the_environment(
    mocker, monkeypatch
) -> None:
    """
    Given  a key exported in the environment and the app holding none (an empty key)
    When   build_llm() is called with that empty key
    Then   the provider is built with the empty key, not the environment's
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER = get_provider_info("openai")
    mock_provider.create.return_value = FakeListChatModel(responses=["ok"])
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    build_llm("openai", "gpt-4o-mini", base_url="", api_key="")

    mock_provider.create.assert_called_once_with("gpt-4o-mini", temperature=0.7, api_key="")


def test_list_provider_models_returns_empty_list_on_exception(mocker) -> None:
    """
    Given  importlib.import_module patched so that list_models() raises a RuntimeError
    When   list_provider_models() is called
    Then   an empty list is returned (graceful fallback, no exception propagated)
    """
    # Patch importlib.import_module so that list_models raises; fallback is []
    mock_mod = mocker.Mock()
    mock_mod.list_models.side_effect = RuntimeError("network error")
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory.importlib.import_module",
        return_value=mock_mod,
    )
    result = list_provider_models("openai")
    assert result == []


def test_list_provider_models_returns_static_list_when_no_list_models(mocker) -> None:
    """
    Given  a mock provider module that has PROVIDER but no list_models attribute
    When   list_provider_models() is called
    Then   the static models list from PROVIDER is returned
    """
    mock_mod = mocker.Mock(spec=["PROVIDER"])
    mock_mod.PROVIDER = ProviderInfo(
        name="acme", display_name="Acme", models=("model-a", "model-b")
    )
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory.importlib.import_module",
        return_value=mock_mod,
    )
    result = list_provider_models("openai")
    assert result == ["model-a", "model-b"]


@pytest.mark.parametrize("name", ["ollama", "openai", "anthropic", "google", "mistral", "cohere"])
def test_each_named_provider_is_discoverable(name: str) -> None:
    """
    Given  the installed providers package
    When   list_providers() is called
    Then   the expected provider name is present in the result
    """
    providers = list_providers()
    assert name in providers


@pytest.mark.parametrize("name", ["_helpers", "_mistral_common", "..config", "no_such_provider"])
def test_a_name_that_is_no_provider_imports_nothing(name: str) -> None:
    """
    Given  a name that is a private module of the package, a path or no provider at all
    When   its metadata and its models are asked for
    Then   nothing is imported and nothing is promised
    """
    assert get_provider_info(name) is None
    assert list_provider_models(name) == []


def test_build_llm_raises_value_error_for_unknown_provider() -> None:
    """
    Given  a provider name that does not match any module
    When   build_llm() is called
    Then   a ValueError naming the unknown provider and the known ones is raised
    """
    with pytest.raises(ValueError, match="nonexistent_provider_xyz") as raised:
        build_llm("nonexistent_provider_xyz", "some-model", base_url="")

    assert raised.value.args[0].key is MessageKey.UNKNOWN_PROVIDER_ERROR
    assert raised.value.args[0].args["provider"] == "nonexistent_provider_xyz"


def test_build_llm_says_so_when_a_listed_provider_cannot_be_imported(mocker) -> None:
    """
    Given  a provider that is listed but whose module fails to import (a package is missing)
    When   build_llm() is called
    Then   it is not called an unknown provider: the error says the provider could not be loaded
    """
    mocker.patch("teachers_teammate.infrastructure.llm_factory._load_provider", return_value=None)

    with pytest.raises(RuntimeError, match="could not be loaded"):
        build_llm("openai", "gpt-4o", base_url="")


def test_build_llm_catches_system_exit_from_provider(mocker) -> None:
    """
    Given  a provider's create() calls sys.exit(1) (e.g. missing dependency)
    When   build_llm() is called
    Then   a RuntimeError is raised instead of SystemExit propagating to the caller
    """
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER = ProviderInfo(name="acme", display_name="Acme")
    mock_provider.create.side_effect = SystemExit(1)
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    with pytest.raises(RuntimeError) as raised:
        build_llm("openai", "gpt-4o", base_url="")

    assert raised.value.args[0].key is MessageKey.PROVIDER_NOT_LOADED


def test_build_llm_passes_base_url_when_provider_needs_it(mocker) -> None:
    """
    Given  a provider whose PROVIDER has needs_base_url=True
    When   build_llm() is called with base_url='http://custom:11434'
    Then   create() is called with that base_url keyword argument
    """
    fake = FakeListChatModel(responses=["ok"])
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER = ProviderInfo(name="acme", display_name="Acme", needs_base_url=True)
    mock_provider.create.return_value = fake
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    result = build_llm("ollama", "llama3", base_url="http://custom:11434")

    assert result is fake
    mock_provider.create.assert_called_once_with(
        "llama3", base_url="http://custom:11434", temperature=0.7, api_key=None
    )


def test_get_provider_info_returns_none_for_nonexistent_provider() -> None:
    """
    Given  a provider name that has no corresponding module in the providers package
    When   get_provider_info() is called
    Then   None is returned (ImportError is handled internally)
    """
    result = get_provider_info("completely_nonexistent_provider_xyz")
    assert result is None


def test_build_llm_forwards_temperature_to_provider_create(mocker) -> None:
    """
    Given  a provider module with PROVIDER needs_base_url=False
    When   build_llm() is called with temperature=0.3
    Then   create() receives temperature=0.3
    """
    fake = FakeListChatModel(responses=["ok"])
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER = ProviderInfo(name="acme", display_name="Acme")
    mock_provider.create.return_value = fake
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    build_llm("openai", "gpt-4o", base_url="", temperature=0.3)

    mock_provider.create.assert_called_once_with("gpt-4o", temperature=0.3, api_key=None)


def test_build_llm_forwards_temperature_with_base_url(mocker) -> None:
    """
    Given  a provider module with PROVIDER needs_base_url=True
    When   build_llm() is called with base_url and temperature=0.1
    Then   create() receives both base_url and temperature=0.1
    """
    fake = FakeListChatModel(responses=["ok"])
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER = ProviderInfo(name="acme", display_name="Acme", needs_base_url=True)
    mock_provider.create.return_value = fake
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    build_llm("ollama", "llama3", base_url="http://custom:11434", temperature=0.1)

    mock_provider.create.assert_called_once_with(
        "llama3", base_url="http://custom:11434", temperature=0.1, api_key=None
    )


def test_checked_listing_of_a_provider_that_cannot_be_asked_is_builtin(mocker) -> None:
    """
    Given  a provider that cannot list its models (can_list_models false)
    When   list_provider_models_checked() is called
    Then   its static list is returned and marked built-in (by design, not a failure)
    """
    mock_mod = mocker.Mock(spec=["PROVIDER"])
    mock_mod.PROVIDER = ProviderInfo(name="acme", display_name="Acme", models=("m-1",))
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory.importlib.import_module",
        return_value=mock_mod,
    )

    listing = list_provider_models_checked("anthropic")

    assert (listing.models, listing.source) == (["m-1"], "builtin")


def test_checked_listing_of_a_provider_that_can_be_asked_is_live(mocker) -> None:
    """
    Given  a provider that lists its models (can_list_models true, no list_models_checked)
    When   list_provider_models_checked() is called
    Then   the answer is marked live
    """
    mock_mod = mocker.Mock(spec=["PROVIDER", "list_models"])
    mock_mod.PROVIDER = ProviderInfo(name="acme", display_name="Acme", can_list_models=True)
    mock_mod.list_models.return_value = ["a:latest"]
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory.importlib.import_module",
        return_value=mock_mod,
    )

    listing = list_provider_models_checked("ollama", base_url="http://x")

    assert (listing.models, listing.source) == (["a:latest"], "live")


def test_checked_listing_uses_the_provider_own_checked_function(mocker) -> None:
    """
    Given  a provider with its own list_models_checked()
    When   list_provider_models_checked() is called
    Then   that provider's listing is returned unchanged (it knows live from fallback)
    """
    from teachers_teammate.domain.model_listing import ModelListing  # noqa: PLC0415

    expected = ModelListing(["fallback"], "failed", "no key")
    mock_mod = mocker.Mock()
    mock_mod.list_models_checked.return_value = expected
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory.importlib.import_module",
        return_value=mock_mod,
    )

    assert list_provider_models_checked("mistral") is expected


def test_checked_listing_reports_a_provider_that_raises_as_failed(mocker) -> None:
    """
    Given  a provider whose list_models_checked() raises
    When   list_provider_models_checked() is called
    Then   a failed, empty listing comes back instead of the exception
    """
    mock_mod = mocker.Mock()
    mock_mod.PROVIDER = ProviderInfo(name="acme", display_name="Acme")
    mock_mod.list_models_checked.side_effect = RuntimeError("boom")
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory.importlib.import_module",
        return_value=mock_mod,
    )

    listing = list_provider_models_checked("mistral")

    assert listing.source == "failed"
    assert listing.models == []


def test_checked_listing_of_a_provider_that_raises_keeps_its_builtin_models(mocker) -> None:
    """
    Given  a provider whose list_models_checked() raises but whose metadata lists models
    When   list_provider_models_checked() is called
    Then   the listing is failed with a reason and carries the built-in models, so the dialog
           still has names to offer
    """
    mock_mod = mocker.Mock()
    mock_mod.PROVIDER = ProviderInfo(name="acme", display_name="Acme", models=("m-1", "m-2"))
    mock_mod.list_models_checked.side_effect = RuntimeError("boom")
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory.importlib.import_module",
        return_value=mock_mod,
    )

    listing = list_provider_models_checked("mistral")

    assert (listing.source, listing.models) == ("failed", ["m-1", "m-2"])
    assert listing.problem


def test_provider_package_names_module_and_pip_name() -> None:
    """
    Given  the Mistral providers, which share one package, and a name that is no provider
    When   provider_package() is read
    Then   both Mistral providers name langchain_mistralai / langchain-mistralai, and the
           unknown name has none
    """
    assert provider_package("mistral") == ("langchain_mistralai", "langchain-mistralai")
    assert provider_package("mistral_eu") == provider_package("mistral")
    assert provider_package("nonexistent") is None


def test_the_ollama_connector_is_optional_and_the_others_are_required() -> None:
    """
    Given  the providers
    When   provider_package_required() is asked
    Then   Ollama (talked to directly) needs no package, every online service does
    """
    assert provider_package_required("ollama") is False
    assert provider_package_required("mistral") is True
    assert provider_package_required("openai") is True


# ── Data region per provider ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("provider", "region"),
    [
        ("mistral_eu", "eu"),
        ("mistral", "unknown"),
        ("openai", "non_eu"),
        ("anthropic", "non_eu"),
        ("google", "non_eu"),
        ("cohere", "non_eu"),
        ("ollama", "unknown"),
    ],
)
def test_every_shipped_provider_declares_its_data_region(provider: str, region: str) -> None:
    """
    Given  a provider that ships with the app
    When   its data region is asked for
    Then   it matches the vendor's declared processing location (unknown when undeclared)
    """
    assert get_provider_data_region(provider) == region


def test_an_unknown_provider_has_no_declared_region() -> None:
    """
    Given  a provider name with no module
    When   its data region is asked for
    Then   it is "unknown", never "eu"
    """
    assert get_provider_data_region("completely_nonexistent_provider_xyz") == "unknown"


def test_a_provider_without_the_key_counts_as_unknown(mocker) -> None:
    """
    Given  a provider module that declares no PROVIDER
    When   its data region is asked for
    Then   it is "unknown"
    """
    module = mocker.Mock(spec=["create"])
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider",
        return_value=module,
    )
    assert get_provider_data_region("old") == "unknown"


def test_a_provider_function_decides_the_region_from_the_settings(mocker) -> None:
    """
    Given  a provider that answers its region from a module-level data_region() function
    When   the settings select a European endpoint
    Then   the function's answer is used, and it is passed the settings
    """
    module = mocker.Mock(spec=["PROVIDER", "data_region"])
    module.PROVIDER = ProviderInfo(name="acme", display_name="Acme")
    module.data_region = lambda **settings: "eu" if settings.get("eu") else "non_eu"
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider",
        return_value=module,
    )

    assert get_provider_data_region("custom", eu=True) == "eu"
    assert get_provider_data_region("custom") == "non_eu"


def test_a_region_function_that_raises_counts_as_unknown(mocker) -> None:
    """
    Given  a provider whose data_region() function raises
    When   its data region is asked for
    Then   it is "unknown" instead of an error reaching the user
    """
    module = mocker.Mock(spec=["PROVIDER", "data_region"])

    def broken(**_settings: object) -> str:
        raise RuntimeError("boom")

    module.data_region = broken
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider",
        return_value=module,
    )
    assert get_provider_data_region("custom") == "unknown"


def test_an_unrecognised_region_string_counts_as_unknown(mocker) -> None:
    """
    Given  a provider that declares a region the app does not know
    When   its data region is asked for
    Then   it is "unknown" rather than a claim of privacy
    """
    module = mocker.Mock(spec=["PROVIDER"])
    module.PROVIDER = mocker.Mock(data_region="europe-ish")
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider",
        return_value=module,
    )
    assert get_provider_data_region("odd") == "unknown"


def test_the_vendor_country_is_available_for_the_tooltip() -> None:
    """
    Given  providers that declare where the vendor is at home
    When   their PROVIDER is read
    Then   the country is there for the tooltip (and absent where it is not declared)
    """
    mistral_eu, openai = get_provider_info("mistral_eu"), get_provider_info("openai")
    assert mistral_eu is not None and mistral_eu.vendor_country == "France"
    assert openai is not None and openai.vendor_country == "USA"
    ollama = get_provider_info("ollama")
    assert ollama is not None and not ollama.vendor_country
