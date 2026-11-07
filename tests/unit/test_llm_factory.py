"""Unit tests for teachers_teammate.infrastructure.llm_factory."""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeListChatModel
import pytest

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

_REQUIRED_INFO_KEYS = frozenset(
    {"needs_api_key", "needs_base_url", "can_list_models", "default_model", "models"}
)


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


def test_all_providers_have_required_info_keys() -> None:
    """
    Given  all discovered provider names
    When   get_provider_info() is called for each
    Then   every info dict contains the five required metadata keys
    """
    for name in list_providers():
        info = get_provider_info(name)
        missing = _REQUIRED_INFO_KEYS - info.keys()
        assert not missing, f"Provider '{name}' missing keys: {missing}"


def test_get_provider_info_returns_correct_types() -> None:
    """
    Given  all discovered provider names
    When   get_provider_info() is called for each
    Then   each metadata value has the expected Python type (bool / str / list)
    """
    for name in list_providers():
        info = get_provider_info(name)
        assert isinstance(info["needs_api_key"], bool), f"{name}: needs_api_key not bool"
        assert isinstance(info["needs_base_url"], bool), f"{name}: needs_base_url not bool"
        assert isinstance(info["can_list_models"], bool), f"{name}: can_list_models not bool"
        assert isinstance(info["default_model"], str), f"{name}: default_model not str"
        assert isinstance(info["models"], list), f"{name}: models not list"


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
    result = build_llm("openai", "gpt-4o-mini")
    assert result is fake
    mock_provider.create.assert_called_once()


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
    Given  a mock provider module that has PROVIDER_INFO but no list_models attribute
    When   list_provider_models() is called
    Then   the static models list from PROVIDER_INFO is returned
    """
    mock_mod = mocker.Mock(spec=["PROVIDER_INFO"])
    mock_mod.PROVIDER_INFO = {
        "models": ["model-a", "model-b"],
    }
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


def test_build_llm_raises_value_error_for_unknown_provider() -> None:
    """
    Given  a provider name that does not match any module
    When   build_llm() is called
    Then   a ValueError is raised
    """
    with pytest.raises(ValueError, match="Unknown correction provider"):
        build_llm("nonexistent_provider_xyz", "some-model")


def test_build_llm_catches_system_exit_from_provider(mocker) -> None:
    """
    Given  a provider's create() calls sys.exit(1) (e.g. missing dependency)
    When   build_llm() is called
    Then   a RuntimeError is raised instead of SystemExit propagating to the caller
    """
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER_INFO = {"needs_base_url": False}
    mock_provider.create.side_effect = SystemExit(1)
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    with pytest.raises(RuntimeError, match="could not be loaded"):
        build_llm("openai", "gpt-4o")


def test_build_llm_passes_base_url_when_provider_needs_it(mocker) -> None:
    """
    Given  a provider whose PROVIDER_INFO has needs_base_url=True
    When   build_llm() is called with base_url='http://custom:11434'
    Then   create() is called with that base_url keyword argument
    """
    fake = FakeListChatModel(responses=["ok"])
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER_INFO = {"needs_base_url": True}
    mock_provider.create.return_value = fake
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    result = build_llm("ollama", "llama3", base_url="http://custom:11434")

    assert result is fake
    mock_provider.create.assert_called_once_with(
        "llama3", base_url="http://custom:11434", temperature=0.7
    )


def test_get_provider_info_returns_empty_dict_for_nonexistent_provider() -> None:
    """
    Given  a provider name that has no corresponding module in the providers package
    When   get_provider_info() is called
    Then   an empty dict is returned (ImportError is handled internally)
    """
    result = get_provider_info("completely_nonexistent_provider_xyz")
    assert result == {}


def test_build_llm_forwards_temperature_to_provider_create(mocker) -> None:
    """
    Given  a provider module with PROVIDER_INFO needs_base_url=False
    When   build_llm() is called with temperature=0.3
    Then   create() receives temperature=0.3
    """
    fake = FakeListChatModel(responses=["ok"])
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER_INFO = {"needs_base_url": False}
    mock_provider.create.return_value = fake
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    build_llm("openai", "gpt-4o", temperature=0.3)

    mock_provider.create.assert_called_once_with("gpt-4o", temperature=0.3)


def test_build_llm_forwards_temperature_with_base_url(mocker) -> None:
    """
    Given  a provider module with PROVIDER_INFO needs_base_url=True
    When   build_llm() is called with base_url and temperature=0.1
    Then   create() receives both base_url and temperature=0.1
    """
    fake = FakeListChatModel(responses=["ok"])
    mock_provider = mocker.Mock()
    mock_provider.PROVIDER_INFO = {"needs_base_url": True}
    mock_provider.create.return_value = fake
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._load_provider", return_value=mock_provider
    )

    build_llm("ollama", "llama3", base_url="http://custom:11434", temperature=0.1)

    mock_provider.create.assert_called_once_with(
        "llama3", base_url="http://custom:11434", temperature=0.1
    )


def test_checked_listing_of_a_provider_that_cannot_be_asked_is_builtin(mocker) -> None:
    """
    Given  a provider that cannot list its models (can_list_models false)
    When   list_provider_models_checked() is called
    Then   its static list is returned and marked built-in (by design, not a failure)
    """
    mock_mod = mocker.Mock(spec=["PROVIDER_INFO"])
    mock_mod.PROVIDER_INFO = {"models": ["m-1"], "can_list_models": False}
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
    mock_mod = mocker.Mock(spec=["PROVIDER_INFO", "list_models"])
    mock_mod.PROVIDER_INFO = {"models": [], "can_list_models": True}
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
    mock_mod.PROVIDER_INFO = {"models": ["m-1", "m-2"]}
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
    Given  a provider module whose PROVIDER_INFO omits data_region
    When   its data region is asked for
    Then   it is "unknown"
    """
    module = mocker.Mock(spec=["PROVIDER_INFO"])
    module.PROVIDER_INFO = {"display_name": "Old"}
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._import_provider_module",
        return_value=module,
    )
    assert get_provider_data_region("old") == "unknown"


def test_a_provider_function_decides_the_region_from_the_settings(mocker) -> None:
    """
    Given  a provider that answers its region from a module-level data_region() function
    When   the settings select a European endpoint
    Then   the function's answer is used, and it is passed the settings
    """
    module = mocker.Mock(spec=["PROVIDER_INFO", "data_region"])
    module.PROVIDER_INFO = {"data_region": "unknown"}
    module.data_region = lambda **settings: "eu" if settings.get("eu") else "non_eu"
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._import_provider_module",
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
    module = mocker.Mock(spec=["PROVIDER_INFO", "data_region"])

    def broken(**_settings: object) -> str:
        raise RuntimeError("boom")

    module.data_region = broken
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._import_provider_module",
        return_value=module,
    )
    assert get_provider_data_region("custom") == "unknown"


def test_an_unrecognised_region_string_counts_as_unknown(mocker) -> None:
    """
    Given  a provider that declares a region the app does not know
    When   its data region is asked for
    Then   it is "unknown" rather than a claim of privacy
    """
    module = mocker.Mock(spec=["PROVIDER_INFO"])
    module.PROVIDER_INFO = {"data_region": "europe-ish"}
    mocker.patch(
        "teachers_teammate.infrastructure.llm_factory._import_provider_module",
        return_value=module,
    )
    assert get_provider_data_region("odd") == "unknown"


def test_the_vendor_country_is_available_for_the_tooltip() -> None:
    """
    Given  providers that declare where the vendor is at home
    When   their PROVIDER_INFO is read
    Then   the country is there for the tooltip (and absent where it is not declared)
    """
    assert get_provider_info("mistral_eu").get("vendor_country") == "France"
    assert get_provider_info("openai").get("vendor_country") == "USA"
    assert not get_provider_info("ollama").get("vendor_country")
