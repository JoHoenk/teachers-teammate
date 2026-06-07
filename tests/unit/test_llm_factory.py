"""Unit tests for teachers_teammate.infrastructure.llm_factory."""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeListChatModel
import pytest

from teachers_teammate.infrastructure.llm_factory import (
    build_llm,
    get_provider_info,
    list_provider_models,
    list_providers,
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
