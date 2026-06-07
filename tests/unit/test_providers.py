"""Unit tests for each provider module in teachers_teammate.infrastructure.providers.*

Tests are parametrized over every .py file found in the providers directory,
so newly added providers are automatically covered without any changes here.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

_PROVIDERS_DIR = (
    Path(__file__).parent.parent.parent / "teachers_teammate" / "infrastructure" / "providers"
)
_PROVIDER_NAMES = sorted(p.stem for p in _PROVIDERS_DIR.glob("*.py") if not p.stem.startswith("_"))

_REQUIRED_KEYS = frozenset(
    {"needs_api_key", "needs_base_url", "can_list_models", "default_model", "models"}
)


def _load(name: str) -> ModuleType:
    return importlib.import_module(f"teachers_teammate.infrastructure.providers.{name}")


@pytest.mark.parametrize("name", _PROVIDER_NAMES)
def test_provider_info_is_dict(name: str) -> None:
    """
    Given  a provider module identified by name
    When   its PROVIDER_INFO attribute is inspected
    Then   it is a dict
    """
    mod = _load(name)
    assert isinstance(mod.PROVIDER_INFO, dict)


@pytest.mark.parametrize("name", _PROVIDER_NAMES)
def test_provider_info_has_required_keys(name: str) -> None:
    """
    Given  a provider module identified by name
    When   its PROVIDER_INFO dict is checked
    Then   all five required metadata keys are present
    """
    info = _load(name).PROVIDER_INFO
    missing = _REQUIRED_KEYS - info.keys()
    assert not missing, f"Provider '{name}' missing keys: {missing}"


@pytest.mark.parametrize("name", _PROVIDER_NAMES)
def test_provider_info_value_types(name: str) -> None:
    """
    Given  a provider module identified by name
    When   the types of PROVIDER_INFO values are checked
    Then   needs_api_key/needs_base_url/can_list_models are bool, default_model is a
           non-empty str, and models is a list
    """
    info = _load(name).PROVIDER_INFO
    assert isinstance(info["needs_api_key"], bool)
    assert isinstance(info["needs_base_url"], bool)
    assert isinstance(info["can_list_models"], bool)
    assert isinstance(info["default_model"], str)
    assert len(info["default_model"]) > 0, f"Provider '{name}' has empty default_model"
    assert isinstance(info["models"], list)


@pytest.mark.parametrize("name", _PROVIDER_NAMES)
def test_provider_create_is_callable(name: str) -> None:
    """
    Given  a provider module identified by name
    When   its create attribute is inspected
    Then   it is callable
    """
    assert callable(_load(name).create)


@pytest.mark.parametrize("name", _PROVIDER_NAMES)
def test_provider_list_models_is_callable(name: str) -> None:
    """
    Given  a provider module identified by name
    When   its list_models attribute is inspected
    Then   it is callable
    """
    assert callable(_load(name).list_models)


def test_all_expected_providers_are_present() -> None:
    """
    Given  the providers directory
    When   all .py module names are collected
    Then   the six expected provider modules (ollama, openai, anthropic, google,
           mistral, cohere) are all present
    """
    for expected in ("ollama", "openai", "anthropic", "google", "mistral", "cohere"):
        assert expected in _PROVIDER_NAMES, f"Provider module '{expected}.py' not found"


# ── list_models() — static-list providers ──────────────────────────────────


@pytest.mark.parametrize("name", ["anthropic", "cohere"])
def test_static_list_providers_return_non_empty_model_list(name: str) -> None:
    """
    Given  a provider whose list_models() returns a hard-coded static list
    When   list_models() is called
    Then   a non-empty list of strings is returned without any network call
    """
    mod = _load(name)
    result = mod.list_models()
    assert isinstance(result, list)
    assert len(result) > 0
    assert all(isinstance(m, str) for m in result)


def test_ollama_list_models_returns_list_when_server_unreachable(mocker) -> None:
    """
    Given  the Ollama server is unreachable (OllamaClient.list_models returns [])
    When   ollama.list_models() is called
    Then   an empty list is returned (no exception propagated)
    """
    mocker.patch(
        "teachers_teammate.infrastructure.ollama_utils.OllamaClient.list_models",
        return_value=[],
    )
    mod = _load("ollama")
    result = mod.list_models(base_url="http://127.0.0.1:11434")
    assert result == []


def test_openai_list_models_falls_back_to_static_list_when_api_unavailable() -> None:
    """
    Given  the OpenAI API is unavailable (import works but client.models.list raises)
    When   openai.list_models() is called
    Then   the static fallback model list from PROVIDER_INFO is returned
    """
    import sys  # noqa: PLC0415

    mock_openai_mod = MagicMock()
    mock_client = MagicMock()
    mock_client.models.list.side_effect = Exception("no network")  # pylint: disable=no-member
    mock_openai_mod.OpenAI.return_value = mock_client

    with patch.dict(sys.modules, {"openai": mock_openai_mod}):
        mod = _load("openai")
        result = mod.list_models()

    from teachers_teammate.infrastructure.providers.openai import PROVIDER_INFO  # noqa: PLC0415

    assert result == list(PROVIDER_INFO["models"])


# ── create() — installed providers (langchain_openai, langchain_ollama) ───


def test_openai_create_returns_base_chat_model() -> None:
    """
    Given  langchain-openai is installed
    When   openai.create('gpt-4o-mini') is called
    Then   create() calls ChatOpenAI with the correct model name
    """
    import sys  # noqa: PLC0415

    from langchain_core.language_models import BaseChatModel  # noqa: PLC0415

    mock_instance = MagicMock(spec=BaseChatModel)
    fake_mod = MagicMock()
    fake_mod.ChatOpenAI = MagicMock(return_value=mock_instance)
    # ChatOpenAI is imported lazily inside create(); inject a fake module.
    with patch.dict(sys.modules, {"langchain_openai": fake_mod}):
        mod = _load("openai")
        result = mod.create("gpt-4o-mini")

    fake_mod.ChatOpenAI.assert_called_once_with(model="gpt-4o-mini", temperature=0.7)
    assert result is mock_instance


def test_ollama_create_returns_base_chat_model() -> None:
    """
    Given  langchain-ollama is installed
    When   ollama.create('llama3', base_url='http://...') is called
    Then   a BaseChatModel instance is returned (no server connection at construction time)
    """
    import sys  # noqa: PLC0415

    from langchain_core.language_models import BaseChatModel  # noqa: PLC0415

    fake_instance = MagicMock(spec=BaseChatModel)
    fake_mod = MagicMock()
    fake_mod.ChatOllama = MagicMock(return_value=fake_instance)

    with patch.dict(sys.modules, {"langchain_ollama": fake_mod}):
        mod = _load("ollama")
        model = mod.create("llama3", base_url="http://127.0.0.1:11434")

    fake_mod.ChatOllama.assert_called_once_with(
        model="llama3",
        base_url="http://127.0.0.1:11434",
        temperature=0.7,
    )
    assert model is fake_instance


# ── create() — uninstalled providers exit with error ──────────────────────


@pytest.mark.parametrize(
    ("provider_name", "package_name"),
    [
        ("anthropic", "langchain_anthropic"),
        ("google", "langchain_google_genai"),
        ("mistral", "langchain_mistralai"),
        ("cohere", "langchain_cohere"),
    ],
)
def test_uninstalled_provider_create_raises_runtime_error(
    provider_name: str,
    package_name: str,
) -> None:
    """
    Given  the provider's LangChain package is not installed
    When   create() is called
    Then   RuntimeError is raised (not ImportError or SystemExit propagating to the caller)
    """
    import sys  # noqa: PLC0415

    with patch.dict(sys.modules, {package_name: None}):
        mod = _load(provider_name)
        with pytest.raises(RuntimeError, match="is not installed"):
            mod.create("some-model")


# ── list_models() — mistral ───────────────────────────────────────────────


def test_mistral_list_models_returns_api_models_when_sdk_available() -> None:
    """
    Given  the mistralai SDK is available and client.models.list() succeeds
    When   mistral.list_models() is called
    Then   the model IDs from the API response are returned (sorted)
    """
    import sys  # noqa: PLC0415

    fake_model = MagicMock()
    fake_model.id = "mistral-large-latest"
    fake_result = MagicMock()
    fake_result.data = [fake_model]

    fake_client = MagicMock()
    fake_client.models.list.return_value = fake_result  # pylint: disable=no-member

    fake_mistral_mod = MagicMock()
    fake_mistral_mod.Mistral.return_value = fake_client

    with patch.dict(sys.modules, {"mistralai": fake_mistral_mod}):
        mod = _load("mistral")
        result = mod.list_models()

    assert result == ["mistral-large-latest"]


def test_mistral_list_models_falls_back_to_static_on_exception() -> None:
    """
    Given  the mistralai SDK raises an exception (e.g. auth error)
    When   mistral.list_models() is called
    Then   the static fallback list from PROVIDER_INFO is returned
    """
    import sys  # noqa: PLC0415

    fake_mistral_mod = MagicMock()
    fake_mistral_mod.Mistral.side_effect = Exception("auth error")

    with patch.dict(sys.modules, {"mistralai": fake_mistral_mod}):
        mod = _load("mistral")
        result = mod.list_models()

    from teachers_teammate.infrastructure.providers.mistral import PROVIDER_INFO  # noqa: PLC0415

    assert result == list(PROVIDER_INFO["models"])


# ── list_models() — google ────────────────────────────────────────────────


def test_google_list_models_returns_api_models_when_sdk_available() -> None:
    """
    Given  google.generativeai is available and list_models() returns Gemini models
    When   google.list_models() is called
    Then   the filtered and sorted model names (without 'models/' prefix) are returned
    """
    import sys  # noqa: PLC0415

    fake_m1 = MagicMock()
    fake_m1.name = "models/gemini-2.0-flash"
    fake_m1.supported_generation_methods = ["generateContent"]

    fake_m2 = MagicMock()
    fake_m2.name = "models/text-bison-001"
    fake_m2.supported_generation_methods = ["generateContent"]

    fake_genai_mod = MagicMock()
    fake_genai_mod.list_models.return_value = [fake_m1, fake_m2]

    with patch.dict(sys.modules, {"google.generativeai": fake_genai_mod}):
        import teachers_teammate.infrastructure.providers.google as google_mod  # noqa: PLC0415

        result = google_mod.list_models()

    assert "gemini-2.0-flash" in result


def test_google_list_models_falls_back_to_static_on_exception() -> None:
    """
    Given  google.generativeai raises an exception when list_models() is called
    When   google.list_models() is called
    Then   the static fallback list from PROVIDER_INFO is returned
    """
    import sys  # noqa: PLC0415

    fake_genai_mod = MagicMock()
    fake_genai_mod.list_models.side_effect = Exception("API error")

    with patch.dict(sys.modules, {"google.generativeai": fake_genai_mod}):
        import teachers_teammate.infrastructure.providers.google as google_mod  # noqa: PLC0415

        result = google_mod.list_models()

    from teachers_teammate.infrastructure.providers.google import PROVIDER_INFO  # noqa: PLC0415

    assert result == list(PROVIDER_INFO["models"])


# ── list_models() — openai success path ──────────────────────────────────


def test_openai_list_models_returns_chat_models_from_api() -> None:
    """
    Given  the OpenAI API returns a list including both chat and non-chat model IDs
    When   openai.list_models() is called
    Then   only models matching chat prefixes are returned (sorted)
    """
    import sys  # noqa: PLC0415

    gpt4 = MagicMock()
    gpt4.id = "gpt-4o"
    gpt35 = MagicMock()
    gpt35.id = "gpt-3.5-turbo"
    non_chat = MagicMock()
    non_chat.id = "dall-e-3"

    mock_client = MagicMock()
    mock_client.models.list.return_value = [gpt4, gpt35, non_chat]  # pylint: disable=no-member
    mock_openai_mod = MagicMock()
    mock_openai_mod.OpenAI.return_value = mock_client

    with patch.dict(sys.modules, {"openai": mock_openai_mod}):
        mod = _load("openai")
        result = mod.list_models()

    assert "gpt-4o" in result
    assert "gpt-3.5-turbo" in result
    assert "dall-e-3" not in result


# ── create() — installed providers (mocked langchain packages) ────────────


@pytest.mark.parametrize(
    ("provider_name", "package_name", "class_name"),
    [
        ("anthropic", "langchain_anthropic", "ChatAnthropic"),
        ("google", "langchain_google_genai", "ChatGoogleGenerativeAI"),
        ("mistral", "langchain_mistralai", "ChatMistralAI"),
        ("cohere", "langchain_cohere", "ChatCohere"),
    ],
)
def test_installed_provider_create_returns_model_instance(
    provider_name: str,
    package_name: str,
    class_name: str,
) -> None:
    """
    Given  the provider's LangChain package is installed (mocked)
    When   create('some-model') is called
    Then   the provider-specific Chat class is instantiated with model='some-model'
    """
    import sys  # noqa: PLC0415
    from langchain_core.language_models import BaseChatModel  # noqa: PLC0415

    fake_instance = MagicMock(spec=BaseChatModel)
    fake_mod = MagicMock()
    setattr(fake_mod, class_name, MagicMock(return_value=fake_instance))

    with patch.dict(sys.modules, {package_name: fake_mod}):
        mod = _load(provider_name)
        result = mod.create("some-model")

    chat_cls = getattr(fake_mod, class_name)
    chat_cls.assert_called_once_with(model="some-model", temperature=0.7)
    assert result is fake_instance


def test_ollama_list_models_returns_models_when_server_reachable() -> None:
    """
    Given  the Ollama server is reachable and OllamaClient.list_models returns a list
    When   ollama.list_models(base_url=...) is called
    Then   the list returned by OllamaClient.list_models is returned
    """
    with patch(
        "teachers_teammate.infrastructure.ollama_utils.OllamaClient.list_models",
        return_value=["llama3", "mistral"],
    ):
        mod = _load("ollama")
        result = mod.list_models(base_url="http://127.0.0.1:11434")
    assert result == ["llama3", "mistral"]


@pytest.mark.parametrize(
    ("provider_name", "package_name"),
    [
        ("openai", "langchain_openai"),
        ("ollama", "langchain_ollama"),
    ],
)
def test_uninstalled_openai_ollama_provider_create_raises_runtime_error(
    provider_name: str,
    package_name: str,
) -> None:
    """
    Given  langchain-openai or langchain-ollama is not installed
    When   create() is called
    Then   RuntimeError is raised (not ImportError or SystemExit)
    """
    import sys  # noqa: PLC0415

    with patch.dict(sys.modules, {package_name: None}):
        mod = _load(provider_name)
        with pytest.raises(RuntimeError, match="is not installed"):
            mod.create("some-model", base_url="http://127.0.0.1:11434")
