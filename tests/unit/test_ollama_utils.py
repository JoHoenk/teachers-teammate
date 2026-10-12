"""Unit tests for teachers_teammate.infrastructure.ollama_utils."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from teachers_teammate.exceptions import OCRError, OllamaConnectionError
from teachers_teammate.infrastructure import ollama_utils
from teachers_teammate.infrastructure.ollama_utils import (
    OllamaClient,
    _consume_iterator_with_first_token_timeout,
    is_local_ollama_url,
    normalize_ollama_url,
)

# ── normalize_ollama_url ───────────────────────────────────────────────────


def test_normalize_ollama_url_prepends_scheme_to_bare_host_port() -> None:
    """
    Given  a bare host:port string with no scheme
    When   normalize_ollama_url() is called
    Then   http:// is prepended and the result has no trailing slash
    """
    assert normalize_ollama_url("127.0.0.1:11434") == "http://127.0.0.1:11434"


def test_normalize_ollama_url_returns_default_for_empty_string() -> None:
    """
    Given  an empty string
    When   normalize_ollama_url() is called
    Then   the default URL "http://127.0.0.1:11434" is returned
    """
    assert normalize_ollama_url("") == "http://127.0.0.1:11434"


def test_normalize_ollama_url_strips_trailing_slash() -> None:
    """
    Given  a fully-qualified URL with a trailing slash
    When   normalize_ollama_url() is called
    Then   the trailing slash is removed
    """
    assert normalize_ollama_url("http://localhost:11434/") == "http://localhost:11434"


def test_normalize_ollama_url_leaves_well_formed_url_unchanged() -> None:
    """
    Given  a URL that already has a scheme and no trailing slash
    When   normalize_ollama_url() is called
    Then   the URL is returned as-is
    """
    url = "http://192.168.1.10:11434"
    assert normalize_ollama_url(url) == url


# ── helpers ────────────────────────────────────────────────────────────────


def _make_mock_ollama(models: list[str]) -> MagicMock:
    """Return a mock ``ollama`` module whose Client().list() returns *models*."""
    model_objects = []
    for name in models:
        m = MagicMock()
        m.model = name
        m.size = 1_000_000_000
        model_objects.append(m)
    list_resp = MagicMock()
    list_resp.models = model_objects
    mock_client = MagicMock()
    mock_client.list.return_value = list_resp
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    return mock_ollama


# ── OllamaClient.list_models ───────────────────────────────────────────────


def test_list_models_returns_names_on_success(monkeypatch) -> None:
    """
    Given  ollama.Client.list() returns two models
    When   OllamaClient.list_models() is called
    Then   a list of model name strings is returned
    """
    mock_ollama = _make_mock_ollama(["llama3:latest", "deepseek-ocr:latest"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    result = OllamaClient("http://127.0.0.1:11434").list_models()

    assert result == ["llama3:latest", "deepseek-ocr:latest"]


def test_list_models_returns_empty_on_connection_error(monkeypatch) -> None:
    """
    Given  ollama.Client.list() raises an exception
    When   OllamaClient.list_models() is called
    Then   an empty list is returned (no exception propagated)
    """
    mock_client = MagicMock()
    mock_client.list.side_effect = Exception("connection refused")
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    assert OllamaClient("http://127.0.0.1:11434").list_models() == []


def test_list_models_strips_trailing_slash_from_url(monkeypatch) -> None:
    """
    Given  a URL with a trailing slash
    When   OllamaClient.list_models() is called
    Then   the Client is constructed with the normalised URL (no trailing slash)
    """
    mock_ollama = _make_mock_ollama([])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    OllamaClient("http://127.0.0.1:11434/").list_models()

    host_arg = (
        mock_ollama.Client.call_args.kwargs.get("host") or mock_ollama.Client.call_args.args[0]
    )
    assert not str(host_arg).endswith("/"), f"Trailing slash in URL: {host_arg}"


# ── OllamaClient.list_models_with_size ────────────────────────────────────


def test_list_models_with_size_returns_tuples(monkeypatch) -> None:
    """
    Given  ollama.Client.list() returns models with size attributes
    When   OllamaClient.list_models_with_size() is called
    Then   a list of (name, size_bytes) tuples is returned
    """
    m1, m2 = MagicMock(), MagicMock()
    m1.model, m1.size = "llama3:latest", 4_000_000_000
    m2.model, m2.size = "mistral:7b", 7_000_000_000
    list_resp = MagicMock()
    list_resp.models = [m1, m2]
    mock_client = MagicMock()
    mock_client.list.return_value = list_resp
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    result = OllamaClient("http://127.0.0.1:11434").list_models_with_size()

    assert result == [("llama3:latest", 4_000_000_000), ("mistral:7b", 7_000_000_000)]


def test_list_models_with_size_returns_empty_on_error(monkeypatch) -> None:
    """
    Given  ollama.Client.list() raises an exception
    When   OllamaClient.list_models_with_size() is called
    Then   an empty list is returned
    """
    mock_client = MagicMock()
    mock_client.list.side_effect = Exception("timeout")
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    assert OllamaClient("http://127.0.0.1:11434").list_models_with_size() == []


# ── OllamaClient.check_connection ─────────────────────────────────────────


def test_check_connection_returns_true_with_models(monkeypatch) -> None:
    """
    Given  Ollama responds with a non-empty model list
    When   OllamaClient.check_connection() is called
    Then   (True, True, message with model count) is returned
    """
    mock_ollama = _make_mock_ollama(["model-a", "model-b"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    connected, model_ok, msg = OllamaClient("http://127.0.0.1:11434").check_connection()

    assert connected is True
    assert model_ok is True
    assert "2" in msg


def test_check_connection_returns_false_without_models(monkeypatch) -> None:
    """
    Given  Ollama responds but has no models pulled
    When   OllamaClient.check_connection() is called
    Then   (True, False, message about no models pulled) is returned
    """
    mock_ollama = _make_mock_ollama([])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    connected, model_ok, msg = OllamaClient("http://127.0.0.1:11434").check_connection()

    assert connected is True
    assert model_ok is False
    assert "no models" in msg.lower() or "✗" in msg


def test_check_connection_returns_false_on_connection_error(monkeypatch) -> None:
    """
    Given  ollama.Client.list() raises a connection-related exception
    When   OllamaClient.check_connection() is called
    Then   (False, False, message about unreachable server) is returned
    """
    mock_client = MagicMock()
    mock_client.list.side_effect = Exception("connection refused")
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    connected, model_ok, msg = OllamaClient("http://127.0.0.1:11434").check_connection()

    assert connected is False
    assert model_ok is False
    assert "Cannot connect" in msg or "✗" in msg


def test_check_connection_returns_false_on_timeout(monkeypatch) -> None:
    """
    Given  ollama.Client.list() raises a timeout exception
    When   OllamaClient.check_connection() is called
    Then   (False, False, message about timeout) is returned
    """
    mock_client = MagicMock()
    mock_client.list.side_effect = Exception("timeout occurred")
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    connected, model_ok, msg = OllamaClient("http://127.0.0.1:11434").check_connection()

    assert connected is False
    assert model_ok is False
    assert "timed out" in msg.lower() or "✗" in msg


def test_check_connection_with_model_found(monkeypatch) -> None:
    """
    Given  Ollama has the requested model available
    When   OllamaClient.check_connection() is called with that model name
    Then   (True, True, message confirming model availability) is returned
    """
    mock_ollama = _make_mock_ollama(["llama3:latest", "mistral:7b"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    connected, model_ok, msg = OllamaClient("http://127.0.0.1:11434").check_connection(
        "llama3:latest"
    )

    assert connected is True
    assert model_ok is True
    assert "llama3:latest" in msg


def test_check_connection_with_model_not_found(monkeypatch) -> None:
    """
    Given  Ollama does NOT have the requested model
    When   OllamaClient.check_connection() is called with that model name
    Then   (True, False, message listing available models) is returned
    """
    mock_ollama = _make_mock_ollama(["llama3:latest", "mistral:7b"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    connected, model_ok, msg = OllamaClient("http://127.0.0.1:11434").check_connection(
        "typo-model:latest"
    )

    assert connected is True
    assert model_ok is False
    assert "typo-model:latest" in msg
    assert "llama3:latest" in msg


def test_check_connection_model_matched_by_base_name(monkeypatch) -> None:
    """
    Given  the available list has 'llama3:8b' and the requested model is 'llama3'
    When   OllamaClient.check_connection() is called
    Then   (True, True, ...) is returned because the base name matches
    """
    mock_ollama = _make_mock_ollama(["llama3:8b"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    connected, model_ok, _ = OllamaClient("http://127.0.0.1:11434").check_connection("llama3")

    assert connected is True
    assert model_ok is True


def test_check_connection_no_model_returns_count(monkeypatch) -> None:
    """
    Given  Ollama has models and no specific model is requested
    When   OllamaClient.check_connection() is called without model argument
    Then   (True, True, message with model count) is returned
    """
    mock_ollama = _make_mock_ollama(["a", "b", "c"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    connected, model_ok, msg = OllamaClient("http://127.0.0.1:11434").check_connection()

    assert connected is True
    assert model_ok is True
    assert "3" in msg


# ── OllamaClient.chat ──────────────────────────────────────────────────────


def _make_chat_mock_ollama(content_chunks: list[str]) -> MagicMock:
    """Return a mock ``ollama`` module whose Client().chat() streams *content_chunks*."""
    chunks = []
    for text in content_chunks:
        msg = MagicMock()
        msg.content = text
        chunk = MagicMock()
        chunk.message = msg
        chunks.append(chunk)
    mock_client = MagicMock()
    mock_client.chat.return_value = iter(chunks)
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    mock_ollama.ResponseError = Exception
    return mock_ollama


def test_chat_returns_concatenated_content(monkeypatch) -> None:
    """
    Given  ollama.Client.chat() streams two content chunks
    When   OllamaClient.chat() is called
    Then   the concatenated text is returned
    """
    mock_ollama = _make_chat_mock_ollama(["Hello", " world"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    result = OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")

    assert result == "Hello world"


def test_chat_sends_text_only_payload_when_no_images(monkeypatch) -> None:
    """
    Given  no images kwarg is passed
    When   OllamaClient.chat() is called
    Then   the chat call's messages list has no 'images' key in the first message
    """
    mock_ollama = _make_chat_mock_ollama(["ok"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")

    call_kwargs = mock_ollama.Client.return_value.chat.call_args.kwargs
    messages = call_kwargs.get("messages", [])
    assert messages and "images" not in messages[0]


def test_chat_sends_images_in_payload(monkeypatch) -> None:
    """
    Given  images are passed to chat()
    When   OllamaClient.chat() is called
    Then   the chat call's messages list contains the images
    """
    mock_ollama = _make_chat_mock_ollama(["text"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    OllamaClient("http://127.0.0.1:11434").chat("llama3", "describe", images=["base64abc"])

    call_kwargs = mock_ollama.Client.return_value.chat.call_args.kwargs
    messages = call_kwargs.get("messages", [])
    assert messages and messages[0]["images"] == ["base64abc"]


def test_chat_raises_ocr_error_on_connection_error(monkeypatch) -> None:
    """
    Given  ollama.Client construction raises an exception
    When   OllamaClient.chat() is called
    Then   OCRError is raised
    """
    mock_ollama = MagicMock()
    mock_ollama.ResponseError = Exception
    mock_ollama.Client.side_effect = Exception("connection refused")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OCRError):
        OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")

    client = OllamaClient("http://127.0.0.1:11434")
    with pytest.raises(OCRError):
        client.chat("llama3", "hi")


def test_chat_raises_ocr_error_on_empty_stream(monkeypatch) -> None:
    """
    Given  ollama.Client.chat() streams only whitespace/empty content chunks
    When   OllamaClient.chat() is called
    Then   OCRError('model returned an empty response') is raised
    """
    mock_ollama = _make_chat_mock_ollama(["", "   ", ""])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OCRError, match="empty response"):
        OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")


def test_chat_raises_ocr_error_when_first_token_times_out(monkeypatch) -> None:
    """
    Given  ollama.Client.chat() returns a stream whose first chunk never arrives
    When   OllamaClient.chat() is called with a short timeout
    Then   OCRError mentioning the first-token timeout is raised
    """
    import time  # noqa: PLC0415

    def _never_yields():
        time.sleep(5)  # daemon reader thread blocks; main times out first
        yield None

    mock_client = MagicMock()
    mock_client.chat.return_value = _never_yields()
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    mock_ollama.ResponseError = Exception
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OCRError, match="No output within"):
        OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi", timeout=0.05)


def test_chat_propagates_mid_stream_error_as_ocr_error(monkeypatch) -> None:
    """
    Given  ollama.Client.chat() yields one chunk then raises mid-stream
    When   OllamaClient.chat() is called
    Then   the mid-stream error surfaces as an OCRError
    """
    msg = MagicMock()
    msg.content = "partial"
    chunk = MagicMock()
    chunk.message = msg

    def _fail_mid_stream():
        yield chunk
        raise RuntimeError("stream broke")

    mock_client = MagicMock()
    mock_client.chat.return_value = _fail_mid_stream()
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    mock_ollama.ResponseError = Exception
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OCRError, match="stream broke"):
        OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")


def test_chat_forwards_think_false_by_default(monkeypatch) -> None:
    """
    Given  no think kwarg is passed to chat()
    When   OllamaClient.chat() is called
    Then   the underlying client.chat receives think=False (reasoning suppressed)
    """
    mock_ollama = _make_chat_mock_ollama(["ok"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    OllamaClient("http://127.0.0.1:11434").chat("deepseek-r1", "hi")

    assert mock_ollama.Client.return_value.chat.call_args.kwargs.get("think") is False


def test_chat_retries_without_think_on_type_error(monkeypatch) -> None:
    """
    Given  an older ollama client whose chat() rejects the think kwarg with TypeError
    When   OllamaClient.chat() is called
    Then   it retries once without think and returns the streamed content
    """
    mock_ollama = _make_chat_mock_ollama(["recovered"])
    good_stream = mock_ollama.Client.return_value.chat.return_value
    mock_ollama.Client.return_value.chat.side_effect = [
        TypeError("chat() got an unexpected keyword argument 'think'"),
        good_stream,
    ]
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    result = OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")

    assert result == "recovered"
    # second (retry) call must omit the think kwarg
    retry_kwargs = mock_ollama.Client.return_value.chat.call_args_list[1].kwargs
    assert "think" not in retry_kwargs


def test_chat_retries_without_think_on_response_error(monkeypatch) -> None:
    """
    Given  a non-thinking model whose chat() rejects think with ResponseError
    When   OllamaClient.chat() is called
    Then   it retries once without think and returns the streamed content
    """
    mock_ollama = _make_chat_mock_ollama(["plain"])
    good_stream = mock_ollama.Client.return_value.chat.return_value
    mock_ollama.Client.return_value.chat.side_effect = [
        mock_ollama.ResponseError("model does not support thinking"),
        good_stream,
    ]
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    result = OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")

    assert result == "plain"


# ── _consume_iterator_with_first_token_timeout ─────────────────────────────


def test_consume_iterator_returns_all_items_when_first_arrives_in_time() -> None:
    """
    Given  an iterator that yields three items promptly
    When   _consume_iterator_with_first_token_timeout() is called
    Then   all items are returned in order
    """
    assert _consume_iterator_with_first_token_timeout(iter([1, 2, 3]), 1.0) == [1, 2, 3]


def test_consume_iterator_raises_when_first_item_times_out() -> None:
    """
    Given  an iterator whose first item is delayed beyond the timeout
    When   _consume_iterator_with_first_token_timeout() is called
    Then   OCRError mentioning the timeout is raised
    """
    import time  # noqa: PLC0415

    def _slow():
        time.sleep(5)
        yield 1

    with pytest.raises(OCRError, match="No output within"):
        _consume_iterator_with_first_token_timeout(_slow(), 0.05)


def test_consume_iterator_propagates_reader_exception_as_ocr_error() -> None:
    """
    Given  an iterator that raises after yielding one item
    When   _consume_iterator_with_first_token_timeout() is called
    Then   the exception is re-raised as an OCRError
    """

    def _boom():
        yield 1
        raise RuntimeError("kaboom")

    with pytest.raises(OCRError, match="kaboom"):
        _consume_iterator_with_first_token_timeout(_boom(), 1.0)


# ── OllamaClient.pull ──────────────────────────────────────────────────────


def test_pull_calls_progress_cb(monkeypatch) -> None:
    """
    Given  ollama.Client.pull streams two ProgressResponse objects
    When   OllamaClient.pull() is called with a progress callback
    Then   the callback is invoked once per response with (status, completed, total)
    """
    resp1 = MagicMock()
    resp1.status = "pulling manifest"
    resp1.completed = 0
    resp1.total = 0

    resp2 = MagicMock()
    resp2.status = "downloading"
    resp2.completed = 512
    resp2.total = 1024

    mock_client_instance = MagicMock()
    mock_client_instance.pull.return_value = iter([resp1, resp2])

    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client_instance

    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    calls: list[tuple] = []
    OllamaClient("http://127.0.0.1:11434").pull(
        "tinyllama", lambda s, c, t: calls.append((s, c, t))
    )

    assert len(calls) == 2
    assert calls[0] == ("pulling manifest", 0, 0)
    assert calls[1] == ("downloading", 512, 1024)


def test_pull_no_callback(monkeypatch) -> None:
    """
    Given  ollama.Client.pull streams responses
    When   OllamaClient.pull() is called without a progress callback
    Then   it completes without error
    """
    resp = MagicMock()
    resp.status = "success"
    resp.completed = 100
    resp.total = 100

    mock_client_instance = MagicMock()
    mock_client_instance.pull.return_value = iter([resp])

    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client_instance

    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    OllamaClient("http://127.0.0.1:11434").pull("tinyllama")  # no exception


# ── is_local_ollama_url ────────────────────────────────────────────────────


@pytest.mark.use_case("Hardware_Profile_Override")
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:11434", True),
        ("127.0.0.1:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://127.8.8.8:11434", True),  # whole 127.0.0.0/8 loopback range
        ("http://[::1]:11434", True),
        ("http://0.0.0.0:11434", True),
        ("", True),  # default URL is local
        ("http://192.168.1.50:11434", False),
        ("myserver.lan:11434", False),
        ("http://ollama.example.com", False),
        ("http://127.example.com:11434", False),  # a host NAME that merely starts with "127."
    ],
)
def test_is_local_ollama_url(url: str, expected: bool, monkeypatch) -> None:
    """
    Given  an Ollama server URL (and a computer that knows itself under no other name)
    When   is_local_ollama_url is called
    Then   loopback addresses (and the default URL) count as local, hosts do not
    """
    monkeypatch.setattr(ollama_utils, "_own_host_names", lambda: frozenset())
    assert is_local_ollama_url(url) is expected


@pytest.mark.use_case("Hardware_Profile_Override")
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://studio-pc:11434", True),  # this computer's host name
        ("http://STUDIO-PC.local:11434", True),  # case does not matter
        ("http://10.0.0.7:11434", True),  # this computer's own LAN address
        ("http://10.0.0.8:11434", False),  # another computer on the same network
    ],
)
def test_is_local_ollama_url_recognises_the_computers_own_names(
    url: str, expected: bool, monkeypatch
) -> None:
    """
    Given  a computer that knows itself as "studio-pc", "studio-pc.local" and 10.0.0.7
    When   the Ollama URL names one of these
    Then   it counts as local (the GPU of this computer is the one that runs the models)
    """
    monkeypatch.setattr(
        ollama_utils,
        "_own_host_names",
        lambda: frozenset({"studio-pc", "studio-pc.local", "10.0.0.7"}),
    )
    assert is_local_ollama_url(url) is expected


@pytest.mark.use_case("Hardware_Profile_Override")
def test_own_host_names_collects_name_and_addresses_and_survives_lookup_errors(
    monkeypatch,
) -> None:
    """
    Given  the host-name lookups of the socket module
    When   the own names are collected
    Then   host name, FQDN and addresses are lower-cased into one set, and a failing
           lookup yields an empty set instead of an error
    """
    uncached = ollama_utils._own_host_names.__wrapped__
    monkeypatch.setattr(ollama_utils.socket, "gethostname", lambda: "Studio-PC")
    monkeypatch.setattr(ollama_utils.socket, "getfqdn", lambda: "Studio-PC.local")
    monkeypatch.setattr(
        ollama_utils.socket, "gethostbyname_ex", lambda _h: ("Studio-PC", [], ["10.0.0.7"])
    )
    assert uncached() == frozenset({"studio-pc", "studio-pc.local", "10.0.0.7"})

    def failing(_host: str):
        raise OSError("no resolver")

    monkeypatch.setattr(ollama_utils.socket, "gethostbyname_ex", failing)
    # Names found before the failing lookup are kept.
    assert uncached() == frozenset({"studio-pc", "studio-pc.local"})


# ── OllamaClient.model_capabilities ────────────────────────────────────────


def _make_show_mock_ollama(show_resp: object) -> MagicMock:
    """Return a mock ``ollama`` module whose Client().show() returns *show_resp*."""
    mock_client = MagicMock()
    mock_client.show.return_value = show_resp
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    return mock_ollama


@pytest.mark.use_case("Model_Category_Guidance")
def test_model_capabilities_uses_show_capabilities_on_new_servers(monkeypatch) -> None:
    """
    Given  a newer Ollama server whose show() response lists capabilities
    When   model_capabilities is called
    Then   the capabilities are returned lowercased as a frozenset
    """
    show_resp = MagicMock()
    show_resp.capabilities = ["Completion", "Vision"]
    mock_ollama = _make_show_mock_ollama(show_resp)
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    caps = OllamaClient("http://127.0.0.1:11434").model_capabilities("qwen3-vl:8b")
    assert caps == frozenset({"completion", "vision"})


@pytest.mark.use_case("Model_Category_Guidance")
def test_model_capabilities_falls_back_to_families_heuristic(monkeypatch) -> None:
    """
    Given  an older server whose show() response has no capabilities but families ["clip"]
    When   model_capabilities is called
    Then   the vision-family heuristic returns {"vision"}
    """
    show_resp = MagicMock(spec=[])  # no "capabilities" attribute at all
    details = MagicMock()
    details.families = ["clip"]
    show_resp.details = details
    mock_ollama = _make_show_mock_ollama(show_resp)
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    assert OllamaClient("http://127.0.0.1:11434").model_capabilities("x") == frozenset({"vision"})


@pytest.mark.use_case("Model_Category_Guidance")
def test_model_capabilities_old_server_text_family_means_completion(monkeypatch) -> None:
    """
    Given  an older server reporting families ["general"] (no vision family)
    When   model_capabilities is called
    Then   {"completion"} is returned
    """
    show_resp = MagicMock(spec=[])
    details = MagicMock()
    details.families = ["general"]
    show_resp.details = details
    mock_ollama = _make_show_mock_ollama(show_resp)
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    assert OllamaClient("http://127.0.0.1:11434").model_capabilities("x") == frozenset(
        {"completion"}
    )


@pytest.mark.use_case("Model_Category_Guidance")
def test_model_capabilities_returns_none_on_error(monkeypatch) -> None:
    """
    Given  show() raises an exception
    When   model_capabilities is called
    Then   None is returned (best-effort introspection)
    """
    mock_client = MagicMock()
    mock_client.show.side_effect = Exception("boom")
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    assert OllamaClient("http://127.0.0.1:11434").model_capabilities("x") is None


@pytest.mark.use_case("Model_Category_Guidance")
@pytest.mark.parametrize(
    "error",
    [ConnectionError("refused"), TimeoutError("slow")],
    ids=["connection-refused", "timeout"],
)
def test_model_capabilities_raises_when_the_server_is_unreachable(monkeypatch, error) -> None:
    """
    Given  show() fails because the server cannot be reached or does not answer in time
    When   model_capabilities is called
    Then   OllamaConnectionError is raised, so callers can stop asking a dead server
           (unlike "model unknown", which stays None)
    """
    mock_client = MagicMock()
    mock_client.show.side_effect = error
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OllamaConnectionError):
        OllamaClient("http://127.0.0.1:11434").model_capabilities("x")


@pytest.mark.use_case("Model_Category_Guidance")
def test_client_passes_its_timeout_to_the_ollama_library(monkeypatch) -> None:
    """
    Given  a client created with a short timeout
    When   it talks to the server
    Then   the timeout reaches ollama.Client (the default stays 10 seconds)
    """
    mock_ollama = MagicMock()
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    OllamaClient("http://127.0.0.1:11434", timeout=3.0).model_capabilities("x")
    assert mock_ollama.Client.call_args.kwargs["timeout"] == 3.0

    OllamaClient("http://127.0.0.1:11434").model_capabilities("x")
    assert mock_ollama.Client.call_args.kwargs["timeout"] == 10


# ── OllamaClient.check_model ───────────────────────────────────────────────


class _RecordingReporter:
    """Minimal Reporter implementation recording warnings for assertions."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def status(self, line: str) -> None:
        _ = line

    def warn(self, line: str) -> None:
        self.warnings.append(line)


def _make_check_model_ollama(
    models: list[str], show_resp: object, ps_models: list[str] | None = None
) -> MagicMock:
    """Return a mock ``ollama`` module wired for a check_model() call."""
    model_objects = []
    for name in models:
        m = MagicMock()
        m.model = name
        model_objects.append(m)
    list_resp = MagicMock()
    list_resp.models = model_objects
    ps_resp = MagicMock()
    ps_resp.models = [m for m in model_objects if ps_models is not None and m.model in ps_models]
    mock_client = MagicMock()
    mock_client.list.return_value = list_resp
    mock_client.show.return_value = show_resp
    mock_client.ps.return_value = ps_resp
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    return mock_ollama


@pytest.mark.use_case("Model_Category_Guidance")
def test_check_model_warns_for_non_vision_model(monkeypatch) -> None:
    """
    Given  an installed model whose capabilities contain no "vision"
    When   check_model is called
    Then   a warning is reported through the reporter
    """
    show_resp = MagicMock()
    show_resp.capabilities = ["completion"]
    mock_ollama = _make_check_model_ollama(["llama3:latest"], show_resp)
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    reporter = _RecordingReporter()
    OllamaClient("http://127.0.0.1:11434").check_model("llama3:latest", reporter)
    assert any("may not support vision" in w for w in reporter.warnings)


@pytest.mark.use_case("Model_Category_Guidance")
def test_check_model_no_warning_for_vision_model(monkeypatch) -> None:
    """
    Given  an installed model with the "vision" capability
    When   check_model is called
    Then   no vision warning is reported
    """
    show_resp = MagicMock()
    show_resp.capabilities = ["completion", "vision"]
    mock_ollama = _make_check_model_ollama(["qwen3-vl:8b"], show_resp)
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    reporter = _RecordingReporter()
    OllamaClient("http://127.0.0.1:11434").check_model("qwen3-vl:8b", reporter)
    assert not any("may not support vision" in w for w in reporter.warnings)
