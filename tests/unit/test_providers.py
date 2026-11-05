"""Unit tests for each provider module in teachers_teammate.infrastructure.providers.*

Tests are parametrized over every .py file found in the providers directory,
so newly added providers are automatically covered without any changes here.
"""

from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
from types import ModuleType
import urllib.error
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
    Then   the expected provider modules (ollama, openai, anthropic, google, mistral,
           mistral_eu, cohere) are all present
    """
    for expected in (
        "ollama",
        "openai",
        "anthropic",
        "google",
        "mistral",
        "mistral_eu",
        "cohere",
    ):
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
        ("mistral_eu", "langchain_mistralai"),
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


# ── list_models() / create() — mistral (global + EU) ──────────────────────

_MISTRAL_PROVIDERS = [
    ("mistral", "https://api.mistral.ai/v1"),
    ("mistral_eu", "https://api.eu.mistral.ai/v1"),
]


_URLOPEN = "teachers_teammate.infrastructure.providers._mistral_common.urllib.request.urlopen"


def _models_response(payload: object) -> MagicMock:
    """A urlopen() result (context manager) whose body is *payload* as JSON."""
    body = io.BytesIO(json.dumps(payload).encode())
    response = MagicMock()
    response.__enter__.return_value = body
    return response


@pytest.mark.parametrize(("name", "base_url"), _MISTRAL_PROVIDERS)
def test_mistral_list_models_queries_own_endpoint_and_keeps_chat_models(
    name: str, base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an API key and an endpoint answering with chat, embedding and archived models
    When   list_models() of the global or the EU provider is called
    Then   that provider's own /models URL is asked, with the key as bearer token, and only
           the non-archived chat models are returned, sorted
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    response = _models_response(
        {
            "data": [
                {"id": "mistral-small-latest", "capabilities": {"completion_chat": True}},
                {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
                {"id": "old-model", "archived": True, "capabilities": {"completion_chat": True}},
                {"id": "codestral-latest"},
            ]
        }
    )
    with patch(_URLOPEN, return_value=response) as urlopen:
        result = _load(name).list_models()

    request = urlopen.call_args.args[0]
    assert result == ["codestral-latest", "mistral-small-latest"]
    assert request.full_url == f"{base_url}/models"
    assert request.get_header("Authorization") == "Bearer k-test"


@pytest.mark.parametrize("name", [n for n, _ in _MISTRAL_PROVIDERS])
def test_mistral_list_models_without_key_returns_static_list_without_network(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  no MISTRAL_API_KEY is set
    When   list_models() is called
    Then   the static fallback list is returned and no request is made
    """
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with patch(_URLOPEN) as urlopen:
        result = _load(name).list_models()

    assert result == list(_load(name).PROVIDER_INFO["models"])
    urlopen.assert_not_called()


@pytest.mark.parametrize("name", [n for n, _ in _MISTRAL_PROVIDERS])
@pytest.mark.parametrize("failure", ["http_error", "bad_json", "wrong_shape", "empty"])
def test_mistral_list_models_falls_back_to_static_on_failure(
    name: str, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an API key, and the models request fails, returns invalid JSON, an unexpected
           shape or lists no models
    When   list_models() is called
    Then   the static fallback list from PROVIDER_INFO is returned
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    kwargs: dict = {}
    if failure == "http_error":
        kwargs["side_effect"] = urllib.error.HTTPError("u", 401, "Unauthorized", None, None)  # ty: ignore[invalid-argument-type]
    elif failure == "bad_json":
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b"<html>")
        kwargs["return_value"] = response
    elif failure == "wrong_shape":
        kwargs["return_value"] = _models_response(["not", "a", "dict"])
    else:
        kwargs["return_value"] = _models_response({"data": []})
    with patch(_URLOPEN, **kwargs):
        result = _load(name).list_models()

    assert result == list(_load(name).PROVIDER_INFO["models"])


@pytest.mark.parametrize("name", [n for n, _ in _MISTRAL_PROVIDERS])
def test_mistral_listing_of_a_live_answer_is_marked_live(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an API key and an endpoint that answers with chat models
    When   list_models_checked() is called
    Then   the listing is live and has no problem text
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    with patch(_URLOPEN, return_value=_models_response({"data": [{"id": "glm-test"}]})):
        listing = _load(name).list_models_checked()

    assert (listing.source, listing.models, listing.problem) == ("live", ["glm-test"], "")


@pytest.mark.parametrize("name", [n for n, _ in _MISTRAL_PROVIDERS])
@pytest.mark.parametrize(
    ("failure", "expected_problem"),
    [
        ("no_key", "no Mistral key is set"),
        ("refused", "Mistral did not accept the key"),
        ("server_error", "Mistral answered with error 500"),
        ("offline", "no connection to Mistral"),
        ("bad_json", "Mistral answered unexpectedly"),
        ("empty", "Mistral returned no chat models"),
    ],
)
def test_mistral_listing_after_a_failure_says_why_and_is_not_live(
    name: str, failure: str, expected_problem: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a key that is missing, refused, a server error, no connection, an invalid answer
           or an answer without chat models
    When   list_models_checked() is called
    Then   the built-in list is returned marked as failed, with the reason in plain words,
           so the dialog cannot present it as the service's own answer
    """
    if failure == "no_key":
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    else:
        monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    kwargs: dict = {}
    if failure == "refused":
        kwargs["side_effect"] = urllib.error.HTTPError("u", 401, "Unauthorized", None, None)  # ty: ignore[invalid-argument-type]
    elif failure == "server_error":
        kwargs["side_effect"] = urllib.error.HTTPError("u", 500, "Server", None, None)  # ty: ignore[invalid-argument-type]
    elif failure == "offline":
        kwargs["side_effect"] = urllib.error.URLError("unreachable")
    elif failure == "bad_json":
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b"<html>")
        kwargs["return_value"] = response
    elif failure == "empty":
        kwargs["return_value"] = _models_response({"data": []})
    with patch(_URLOPEN, **kwargs):
        listing = _load(name).list_models_checked()

    assert listing.source == "failed"
    assert listing.problem == expected_problem
    assert listing.models == list(_load(name).PROVIDER_INFO["models"])


@pytest.mark.parametrize("name", ["openai", "google"])
def test_listing_of_keyed_providers_without_key_is_failed_without_network(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  the provider's API key is not set
    When   list_models_checked() is called
    Then   the built-in list is returned as a failed listing and the SDK is never asked
    """
    mod = _load(name)
    monkeypatch.delenv(mod.PROVIDER_INFO["env_key"], raising=False)

    listing = mod.list_models_checked()

    assert listing.source == "failed"
    assert "key is set" in listing.problem
    assert listing.models == list(mod.PROVIDER_INFO["models"])


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "did not accept the key"),
        (403, "did not accept the key"),
        (None, "answered unexpectedly"),
    ],
)
def test_problem_from_exception_reads_the_http_status(status: int | None, expected: str) -> None:
    """
    Given  an SDK error carrying an HTTP status (or none)
    When   problem_from_exception() describes it
    Then   a refused key is named as such and anything unknown is "answered unexpectedly"
    """
    from teachers_teammate.infrastructure.providers._helpers import (  # noqa: PLC0415
        problem_from_exception,
    )

    exc = RuntimeError("boom")
    exc.status_code = status

    assert expected in problem_from_exception(exc, "Acme")


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (400, "Acme did not accept the key"),
        (401, "Acme did not accept the key"),
        (403, "Acme did not accept the key"),
        (404, "Acme answered with error 404"),
        (500, "Acme answered with error 500"),
    ],
)
def test_problem_from_exception_reads_the_status_of_a_plain_http_error(
    code: int, expected: str
) -> None:
    """
    Given  an error from a plain HTTP request (urllib), which is an OSError too
    When   problem_from_exception() describes it
    Then   its status decides (a refused key, or the error number) and it is not reported as
           a missing connection
    """
    from teachers_teammate.infrastructure.providers._helpers import (  # noqa: PLC0415
        problem_from_exception,
    )

    exc = urllib.error.HTTPError("u", code, "x", None, None)  # ty: ignore[invalid-argument-type]

    assert problem_from_exception(exc, "Acme") == expected


def test_problem_from_exception_names_a_missing_connection() -> None:
    """
    Given  a connection error
    When   problem_from_exception() describes it
    Then   it says there is no connection to the service
    """
    from teachers_teammate.infrastructure.providers._helpers import (  # noqa: PLC0415
        problem_from_exception,
    )

    assert problem_from_exception(ConnectionError("down"), "Acme") == "no connection to Acme"


@pytest.mark.parametrize("name", _PROVIDER_NAMES)
def test_provider_declares_the_package_it_needs(name: str) -> None:
    """
    Given  a provider module
    When   its PROVIDER_INFO is read
    Then   it names the importable module and the pip package it needs, so the dialogs and
           the run check need no list of their own
    """
    package = _load(name).PROVIDER_INFO.get("package")

    assert isinstance(package, dict)
    assert package["module"].startswith("langchain_")
    assert package["pip"].startswith("langchain-")


@pytest.mark.parametrize(("name", "base_url"), _MISTRAL_PROVIDERS)
def test_mistral_create_passes_explicit_endpoint_and_seed(
    name: str, base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  langchain_mistralai is installed (mocked) and MISTRAL_BASE_URL points elsewhere
    When   create() of the global or the EU provider is called
    Then   ChatMistralAI gets the provider's own endpoint (never the environment's), the
           model, the temperature and a fixed random_seed
    """
    import sys  # noqa: PLC0415

    monkeypatch.setenv("MISTRAL_BASE_URL", "https://elsewhere.example/v1")
    fake_mod = MagicMock()
    with patch.dict(sys.modules, {"langchain_mistralai": fake_mod}):
        _load(name).create("mistral-small-latest", temperature=0.2)

    kwargs = fake_mod.ChatMistralAI.call_args.kwargs
    assert kwargs["endpoint"] == base_url
    assert kwargs["model"] == "mistral-small-latest"
    assert kwargs["temperature"] == 0.2
    assert isinstance(kwargs["random_seed"], int)


def test_mistral_providers_share_key_and_differ_in_display_name() -> None:
    """
    Given  the global and the EU Mistral providers
    When   their PROVIDER_INFO is compared
    Then   they use the same API key variable and models, are both online services, and the
           EU one is named so the region is visible wherever the app says where data goes
    """
    glob, eu = _load("mistral").PROVIDER_INFO, _load("mistral_eu").PROVIDER_INFO

    assert glob["env_key"] == eu["env_key"] == "MISTRAL_API_KEY"
    assert glob["models"] == eu["models"]
    assert not glob.get("self_hosted")
    assert not eu.get("self_hosted")
    assert "EU" in eu["display_name"]
    assert "EU" not in glob["display_name"]


# ── list_models() — google ────────────────────────────────────────────────


_GOOGLE_URLOPEN = "teachers_teammate.infrastructure.providers.google.urllib.request.urlopen"


def test_google_list_models_asks_the_rest_api_and_keeps_text_generating_gemini_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  a Google key and an answer in two pages with Gemini, embedding and non-Gemini models
    When   google.list_models_checked() is called
    Then   every page is read with the key in a header (not the URL), and only the Gemini
           models that can generate text come back, sorted and without the 'models/' prefix,
           marked as live
    """
    monkeypatch.setenv("GOOGLE_API_KEY", "k-test")
    pages = [
        _models_response(
            {
                "models": [
                    {
                        "name": "models/gemini-2.0-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/embedding-001",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                    {
                        "name": "models/text-bison-001",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                ],
                "nextPageToken": "page2",
            }
        ),
        _models_response(
            {
                "models": [
                    {
                        "name": "models/gemini-1.5-pro",
                        "supportedGenerationMethods": ["generateContent", "countTokens"],
                    },
                    {
                        "name": "models/gemini-embedding",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            }
        ),
    ]
    with patch(_GOOGLE_URLOPEN, side_effect=pages) as urlopen:
        listing = _load("google").list_models_checked()

    first, second = (call.args[0] for call in urlopen.call_args_list)
    assert (listing.source, listing.models) == ("live", ["gemini-1.5-pro", "gemini-2.0-flash"])
    assert first.get_header("X-goog-api-key") == "k-test"
    assert "k-test" not in first.full_url
    assert "pageToken=page2" in second.full_url


@pytest.mark.parametrize(
    ("failure", "expected_problem"),
    [
        ("bad_key", "Google did not accept the key"),
        ("server_error", "Google answered with error 500"),
        ("offline", "no connection to Google"),
        ("empty", "Google returned no chat models"),
    ],
)
def test_google_listing_after_a_failure_says_why_and_falls_back_to_the_static_list(
    failure: str, expected_problem: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a Google key and an answer that is a wrong key (400), a server error, no connection
           or a list without Gemini models
    When   google.list_models_checked() and list_models() are called
    Then   the static list is returned as a failed listing with the reason in plain words
    """
    monkeypatch.setenv("GOOGLE_API_KEY", "k-test")
    kwargs: dict = {}
    if failure == "bad_key":
        kwargs["side_effect"] = urllib.error.HTTPError("u", 400, "Bad Request", None, None)  # ty: ignore[invalid-argument-type]
    elif failure == "server_error":
        kwargs["side_effect"] = urllib.error.HTTPError("u", 500, "Server", None, None)  # ty: ignore[invalid-argument-type]
    elif failure == "offline":
        kwargs["side_effect"] = urllib.error.URLError("unreachable")
    else:
        kwargs["return_value"] = _models_response({"models": []})
    mod = _load("google")
    with patch(_GOOGLE_URLOPEN, **kwargs):
        listing = mod.list_models_checked()
        plain = mod.list_models()

    assert (listing.source, listing.problem) == ("failed", expected_problem)
    assert listing.models == plain == list(mod.PROVIDER_INFO["models"])


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
