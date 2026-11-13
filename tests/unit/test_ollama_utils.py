"""Unit tests for teachers_teammate.infrastructure.ollama_utils."""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.exceptions import (
    OCRDeadlineError,
    OCRError,
    OCRTimeoutError,
    OllamaConnectionError,
    OllamaUnreachableError,
    ResponseTruncatedError,
    RunStoppedError,
)
from teachers_teammate.infrastructure import ollama_utils
from teachers_teammate.infrastructure.ollama_utils import (
    OllamaClient,
    _consume_iterator_with_first_token_timeout,
    is_local_ollama_url,
    normalize_ollama_url,
)


_REAL_OWN_HOST_NAMES = ollama_utils._own_host_names  # tests below monkeypatch the module name


@pytest.fixture(autouse=True)
def _fresh_own_host_names():
    """Keep the cached lookup of the computer's own names from leaking between tests."""
    _REAL_OWN_HOST_NAMES.cache_clear()
    yield
    _REAL_OWN_HOST_NAMES.cache_clear()


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


class _ResponseError(Exception):
    """A stand-in for ``ollama.ResponseError``, which carries the HTTP status code."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


def _chunk(text: str) -> MagicMock:
    """Return one stream chunk carrying *text*."""
    msg = MagicMock()
    msg.content = text
    chunk = MagicMock()
    chunk.message = msg
    return chunk


def _stream_raising(exc: BaseException):
    """Return a generator that raises *exc* on its first read, as a real stream does."""
    raise exc
    yield  # pragma: no cover  # makes this a generator


def _mock_ollama_with_streams(*streams) -> MagicMock:
    """Return a mock ``ollama`` module whose Client().chat() returns *streams* in turn."""
    mock_client = MagicMock()
    mock_client.chat.side_effect = list(streams)
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    mock_ollama.ResponseError = _ResponseError
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
    Then   OCRTimeoutError (an OCRError) mentioning the first-token timeout is raised, so a
           caller can tell a model that did not start from one that failed
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

    with pytest.raises(OCRTimeoutError, match="No output within") as raised:
        OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi", timeout=0.05)
    assert isinstance(raised.value, OCRError)


def test_an_answer_that_never_ends_is_abandoned_at_the_total_timeout() -> None:
    """
    Given  a stream that keeps producing chunks and never finishes
    When   it is consumed with a total timeout
    Then   OCRDeadlineError is raised after about that time, and the stream is closed so the
           server stops generating
    """
    import time  # noqa: PLC0415

    closed = []

    def _forever():
        try:
            while True:
                time.sleep(0.01)
                yield 1
        finally:
            closed.append(True)

    started = time.monotonic()
    with pytest.raises(OCRDeadlineError, match="still being written"):
        _consume_iterator_with_first_token_timeout(_forever(), 1.0, total_timeout=0.2)

    assert time.monotonic() - started < 1.0
    deadline = time.monotonic() + 2.0
    while not closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert closed == [True]


def test_a_cancel_event_ends_a_running_answer_quickly_and_closes_the_stream() -> None:
    """
    Given  a stream that keeps producing chunks and a cancel event set after a moment
    When   it is consumed with that event
    Then   RunStoppedError is raised within about half a second and the stream is closed so the
           server stops generating
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    closed = []
    cancel = threading.Event()

    def _forever():
        try:
            while True:
                time.sleep(0.01)
                yield 1
        finally:
            closed.append(True)

    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(RunStoppedError):
        _consume_iterator_with_first_token_timeout(_forever(), 5.0, cancel_event=cancel)

    assert time.monotonic() - started < 1.0
    deadline = time.monotonic() + 2.0
    while not closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert closed == [True]


def test_a_cancel_event_ends_the_wait_for_the_first_token() -> None:
    """
    Given  a stream that never produces a first chunk and a cancel event set after a moment
    When   it is consumed with a long first-token timeout
    Then   RunStoppedError is raised long before that timeout
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    cancel = threading.Event()

    def _silent():
        time.sleep(5)
        yield 1

    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(RunStoppedError):
        _consume_iterator_with_first_token_timeout(_silent(), 30.0, cancel_event=cancel)

    assert time.monotonic() - started < 1.5


def test_a_chat_request_is_not_even_opened_when_already_cancelled(monkeypatch) -> None:
    """
    Given  an OllamaClient whose cancel event is already set
    When   chat() is called
    Then   RunStoppedError is raised and the server is never asked
    """
    import threading  # noqa: PLC0415

    mock_ollama = _make_chat_mock_ollama(["never"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(RunStoppedError):
        OllamaClient("http://127.0.0.1:11434", cancel_event=cancel).chat("llama3", "hi")

    mock_ollama.Client.return_value.chat.assert_not_called()


def test_a_cancel_event_that_is_not_set_changes_nothing(monkeypatch) -> None:
    """
    Given  an OllamaClient with a cancel event that stays unset
    When   chat() is called
    Then   the answer arrives as without an event
    """
    import threading  # noqa: PLC0415

    monkeypatch.setitem(sys.modules, "ollama", _make_chat_mock_ollama(["Hello", " world"]))

    result = OllamaClient("http://127.0.0.1:11434", cancel_event=threading.Event()).chat(
        "llama3", "hi"
    )

    assert result == "Hello world"


def test_a_total_timeout_does_not_cut_a_stream_that_finishes_in_time() -> None:
    """
    Given  a short stream
    When   it is consumed with a generous total timeout
    Then   every item is returned, as without one
    """
    assert _consume_iterator_with_first_token_timeout(iter([1, 2, 3]), 1.0, total_timeout=5.0) == [
        1,
        2,
        3,
    ]


def test_chat_propagates_mid_stream_error_as_ocr_error(monkeypatch) -> None:
    """
    Given  ollama.Client.chat() yields one chunk then raises mid-stream
    When   OllamaClient.chat() is called
    Then   the mid-stream error surfaces as an OCRError
    """

    def _fail_mid_stream():
        yield _chunk("partial")
        raise RuntimeError("stream broke")

    mock_ollama = _mock_ollama_with_streams(_fail_mid_stream())
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
    Given  a non-thinking model whose stream rejects think with a 400 that names it
    When   OllamaClient.chat() is called
    Then   it retries once without think and returns the streamed content
    """
    mock_ollama = _mock_ollama_with_streams(
        _stream_raising(_ResponseError("model does not support thinking", 400)),
        iter([_chunk("plain")]),
    )
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    result = OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")

    assert result == "plain"
    retry_kwargs = mock_ollama.Client.return_value.chat.call_args_list[1].kwargs
    assert "think" not in retry_kwargs


def test_chat_does_not_retry_a_model_that_is_not_pulled(monkeypatch) -> None:
    """
    Given  a stream that fails on its first read with a 404 (the model is not pulled)
    When   OllamaClient.chat() is called
    Then   OCRError carries the status code and the request was made only once, so a missing
           model does not double the wait
    """
    mock_ollama = _mock_ollama_with_streams(
        _stream_raising(_ResponseError("model not found", 404)),
    )
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OCRError) as raised:
        OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")

    assert raised.value.status_code == 404
    assert mock_ollama.Client.return_value.chat.call_count == 1


def test_chat_reports_a_refused_connection_on_the_first_read_as_unreachable(monkeypatch) -> None:
    """
    Given  a stream that raises httpx.ConnectError on its first read (a real client makes the
           request there, not at the call)
    When   OllamaClient.chat() is called
    Then   OllamaUnreachableError is raised, so an outage is not blamed on the model
    """
    import httpx  # noqa: PLC0415

    mock_ollama = _mock_ollama_with_streams(
        _stream_raising(httpx.ConnectError("connection refused")),
    )
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OllamaUnreachableError, match="cannot connect to Ollama"):
        OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")


def test_chat_reports_a_connection_lost_mid_stream_as_a_plain_ocr_error(monkeypatch) -> None:
    """
    Given  a stream that yields one chunk and then raises httpx.ReadError
    When   OllamaClient.chat() is called
    Then   a plain OCRError is raised, not OllamaUnreachableError: a server that dies mid-answer
           can be the model's doing, and the evaluation tools ask the server afterwards
    """
    import httpx  # noqa: PLC0415

    def _breaks_after_one_chunk():
        yield _chunk("partial")
        raise httpx.ReadError("connection reset")

    mock_ollama = _mock_ollama_with_streams(_breaks_after_one_chunk())
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OCRError) as raised:
        OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi")

    assert not isinstance(raised.value, OllamaUnreachableError)


def test_chat_closes_the_inner_stream_when_the_reader_is_abandoned(monkeypatch) -> None:
    """
    Given  a stream whose first chunk arrives after the first-token timeout
    When   OllamaClient.chat() gives up and the reader thread is abandoned
    Then   the inner stream was closed, so the HTTP response does not stay open
    """
    import time  # noqa: PLC0415

    closed = []

    def _slow_stream():
        try:
            time.sleep(0.3)
            yield _chunk("late")
            while True:
                time.sleep(0.3)
                yield _chunk("more")
        finally:
            closed.append(True)

    mock_ollama = _mock_ollama_with_streams(_slow_stream())
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OCRTimeoutError):
        OllamaClient("http://127.0.0.1:11434").chat("llama3", "hi", timeout=0.05)

    deadline = time.monotonic() + 3.0
    while not closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert closed == [True]


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


@pytest.mark.parametrize(
    "url", ["", "localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"]
)
def test_is_local_ollama_url_answers_loopback_without_looking_up_own_names(
    url: str, monkeypatch
) -> None:
    """
    Given  a lookup of the computer's own names that must never run
    When   is_local_ollama_url is called with the default or a loopback URL
    Then   it answers True without that (possibly slow) lookup
    """

    def forbidden() -> frozenset[str]:
        raise AssertionError("own names looked up for a loopback URL")

    monkeypatch.setattr(ollama_utils, "_own_host_names", forbidden)
    assert is_local_ollama_url(url) is True


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

    def uncached() -> frozenset[str]:
        ollama_utils._own_host_names.cache_clear()
        return ollama_utils._own_host_names()

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


@pytest.mark.use_case("Hardware_Profile_Override")
def test_warm_up_resolves_own_names_once_in_the_background(monkeypatch) -> None:
    """
    Given  an empty cache of this computer's own names
    When   the warm-up runs and the names are then asked for
    Then   the lookup ran once, off the calling thread, and later asks reuse its answer
    """
    import threading  # noqa: PLC0415

    calls: list[str] = []

    def hostname() -> str:
        calls.append(threading.current_thread().name)
        return "studio-pc"

    ollama_utils._own_host_names.cache_clear()
    monkeypatch.setattr(ollama_utils.socket, "gethostname", hostname)
    monkeypatch.setattr(ollama_utils.socket, "getfqdn", lambda: "studio-pc")
    monkeypatch.setattr(ollama_utils.socket, "gethostbyname_ex", lambda _h: ("", [], []))

    ollama_utils.warm_up_own_host_names()
    names = ollama_utils._own_host_names()

    assert names == frozenset({"studio-pc"})
    assert calls == ["own-host-names"]


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


def test_list_models_or_error_tells_unreachable_from_empty(monkeypatch) -> None:
    """
    Given  one server that answers with no models and one that cannot be reached
    When   OllamaClient.list_models_or_error() is called on each
    Then   the first yields ([], "") and the second ([], <message>), one request each
    """
    mock_ollama = _make_mock_ollama([])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)
    client = mock_ollama.Client.return_value

    assert OllamaClient("http://127.0.0.1:11434").list_models_or_error() == ([], "")

    client.list.side_effect = ConnectionError("Connection refused")
    models, error = OllamaClient("http://127.0.0.1:11434").list_models_or_error()

    assert models == []
    assert "Cannot connect to Ollama" in error
    assert client.list.call_count == 2


# ── Structured output ──────────────────────────────────────────────────────


def test_chat_forwards_the_format_schema(monkeypatch) -> None:
    """
    Given  a JSON schema passed as format to chat()
    When   OllamaClient.chat() is called
    Then   the underlying client.chat receives it as its format argument
    """
    mock_ollama = _make_chat_mock_ollama(["{}"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)
    schema = {"type": "object"}

    OllamaClient("http://127.0.0.1:11434").chat("m", "hi", format=schema)

    assert mock_ollama.Client.return_value.chat.call_args.kwargs["format"] == schema


def test_chat_sends_no_format_by_default(monkeypatch) -> None:
    """
    Given  no format passed to chat()
    When   OllamaClient.chat() is called
    Then   the underlying client.chat receives no format argument at all
    """
    mock_ollama = _make_chat_mock_ollama(["ok"])
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    OllamaClient("http://127.0.0.1:11434").chat("m", "hi")

    assert "format" not in mock_ollama.Client.return_value.chat.call_args.kwargs


# ── Cut-off answers (done_reason="length") ─────────────────────────────────


def _make_chat_mock_with_done_reason(
    content_chunks: list[str],
    done_reason: str,
    *,
    prompt_eval_count: int = 1200,
    eval_count: int = 400,
    durations: dict[str, int] | None = None,
) -> MagicMock:
    """Return a mock ``ollama`` module whose stream ends with a chunk reporting *done_reason*."""
    chunks = []
    for text in content_chunks:
        msg = MagicMock()
        msg.content = text
        chunk = MagicMock()
        chunk.message = msg
        chunk.done_reason = None
        chunks.append(chunk)
    last = MagicMock()
    last.message = MagicMock()
    last.message.content = ""
    last.done_reason = done_reason
    last.prompt_eval_count = prompt_eval_count
    last.eval_count = eval_count
    for name, value in (durations or {}).items():
        setattr(last, name, value)
    chunks.append(last)
    mock_client = MagicMock()
    mock_client.chat.return_value = iter(chunks)
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value = mock_client
    mock_ollama.ResponseError = Exception
    return mock_ollama


def test_chat_raises_when_the_answer_was_cut_off(monkeypatch) -> None:
    """
    Given  a stream whose last chunk reports done_reason="length"
    When   OllamaClient.chat() is called
    Then   ResponseTruncatedError explaining the working memory is raised, not the partial text
    """
    mock_ollama = _make_chat_mock_with_done_reason(["Der Text bricht hier ab"], "length")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(ResponseTruncatedError, match="too long for the model's working memory"):
        OllamaClient("http://127.0.0.1:11434").chat("m", "hi")


def test_chat_with_stats_returns_the_cut_off_answer_and_its_marker(monkeypatch) -> None:
    """
    Given  a stream whose last chunk reports done_reason="length" and token counts
    When   OllamaClient.chat_with_stats() is called
    Then   the partial text and statistics are returned, with truncated True and the counts
    """
    mock_ollama = _make_chat_mock_with_done_reason(
        ["Der Text"], "length", prompt_eval_count=8100, eval_count=92
    )
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    content, stats = OllamaClient("http://127.0.0.1:11434").chat_with_stats("m", "hi")

    assert content == "Der Text"
    assert stats.done_reason == "length"
    assert stats.truncated is True
    assert stats.prompt_eval_count == 8100
    assert stats.eval_count == 92


def test_chat_with_stats_carries_the_durations_the_server_reports(monkeypatch) -> None:
    """
    Given  a stream whose last chunk reports the four durations Ollama measures, in nanoseconds
    When   OllamaClient.chat_with_stats() is called
    Then   the statistics carry them, so a caller can work out tokens per second and load time
    """
    durations = {
        "total_duration": 5_000_000_000,
        "load_duration": 1_500_000_000,
        "prompt_eval_duration": 500_000_000,
        "eval_duration": 3_000_000_000,
    }
    mock_ollama = _make_chat_mock_with_done_reason(
        ["Fertig."], "stop", eval_count=300, durations=durations
    )
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    _, stats = OllamaClient("http://127.0.0.1:11434").chat_with_stats("m", "hi")

    assert (stats.total_duration, stats.load_duration) == (5_000_000_000, 1_500_000_000)
    assert (stats.prompt_eval_duration, stats.eval_duration) == (500_000_000, 3_000_000_000)


def test_chat_with_stats_reports_zero_durations_when_the_server_gives_none(monkeypatch) -> None:
    """
    Given  a stream whose last chunk reports no durations (an older server)
    When   OllamaClient.chat_with_stats() is called
    Then   all four durations are 0 and nothing else changes
    """
    mock_ollama = _make_chat_mock_with_done_reason(["Fertig."], "stop")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    _, stats = OllamaClient("http://127.0.0.1:11434").chat_with_stats("m", "hi")

    assert (stats.total_duration, stats.load_duration) == (0, 0)
    assert (stats.prompt_eval_duration, stats.eval_duration) == (0, 0)
    assert stats.done_reason == "stop"


def test_chat_keeps_a_complete_answer(monkeypatch) -> None:
    """
    Given  a stream whose last chunk reports done_reason="stop"
    When   OllamaClient.chat() is called
    Then   the complete answer is returned unchanged
    """
    mock_ollama = _make_chat_mock_with_done_reason(["Fertig."], "stop")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    assert OllamaClient("http://127.0.0.1:11434").chat("m", "hi") == "Fertig."


def test_chat_logs_the_token_counts_at_debug(monkeypatch, caplog) -> None:
    """
    Given  a stream that reports done_reason, prompt_eval_count and eval_count
    When   OllamaClient.chat() is called with DEBUG logging on
    Then   one debug record names the model, the reason and both token counts
    """
    mock_ollama = _make_chat_mock_with_done_reason(
        ["Fertig."], "stop", prompt_eval_count=1024, eval_count=64
    )
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with caplog.at_level(logging.DEBUG, logger="teachers_teammate.infrastructure.ollama_utils"):
        OllamaClient("http://127.0.0.1:11434").chat("qwen3.5:9b", "hi")

    records = [r.getMessage() for r in caplog.records if "done_reason" in r.getMessage()]
    assert records and "qwen3.5:9b" in records[0]
    assert "1024" in records[0] and "64" in records[0]


def test_proofreading_options_hold_one_fixed_context_size() -> None:
    """
    Given  the temperature of a proofreading request
    When   proofreading_options() is called
    Then   the options carry that temperature and the one shared context size
    """
    options = ollama_utils.proofreading_options(0.7)

    assert options == {"temperature": 0.7, "num_ctx": ollama_utils.PROOFREADING_NUM_CTX}
    assert ollama_utils.PROOFREADING_NUM_CTX == 8192


def test_chat_can_return_a_cut_off_answer_when_the_caller_allows_it(monkeypatch) -> None:
    """
    Given  a stream whose last chunk reports done_reason="length"
    When   OllamaClient.chat() is called with allow_truncated=True
    Then   the partial text is returned instead of an error
    """
    mock_ollama = _make_chat_mock_with_done_reason(["Der Text bricht"], "length")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    result = OllamaClient("http://127.0.0.1:11434").chat("m", "hi", allow_truncated=True)

    assert result == "Der Text bricht"


def test_chat_refuses_an_empty_cut_off_answer(monkeypatch) -> None:
    """
    Given  a stream whose last chunk reports done_reason="length" and carried no text at all
    When   OllamaClient.chat() is called with allow_truncated=True
    Then   OCRError is raised — an empty answer is no partial page, so it must not pass for one
    """
    mock_ollama = _make_chat_mock_with_done_reason([], "length")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OCRError, match="empty response"):
        OllamaClient("http://127.0.0.1:11434").chat("m", "hi", allow_truncated=True)


def test_chat_refuses_a_request_that_cannot_fit_the_context_window(monkeypatch) -> None:
    """
    Given  a prompt whose estimated size and an equally long answer exceed num_ctx
    When   OllamaClient.chat() is called with that num_ctx
    Then   ResponseTruncatedError is raised and no request is sent to Ollama
    """
    mock_ollama = _make_chat_mock_with_done_reason(["x"], "stop")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)
    too_long = (
        "Ein Satz mit vielen Wörtern. " * 500
    )  # ~14.5k characters, ~4.8k tokens: twice that > 8192

    with pytest.raises(ResponseTruncatedError, match="too long for the model's working memory"):
        OllamaClient("http://127.0.0.1:11434").chat(
            "m", too_long, options=ollama_utils.proofreading_options(0.7)
        )

    mock_ollama.Client.return_value.chat.assert_not_called()


def test_chat_sends_a_request_that_fits_the_context_window(monkeypatch) -> None:
    """
    Given  a one-page prompt well inside num_ctx
    When   OllamaClient.chat() is called with the proofreading options
    Then   the request is sent and the answer returned
    """
    mock_ollama = _make_chat_mock_with_done_reason(["Fertig."], "stop")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)
    page = "Ein Satz mit vielen Wörtern. " * 80  # ~2.3k characters

    result = OllamaClient("http://127.0.0.1:11434").chat(
        "m", page, options=ollama_utils.proofreading_options(0.7), system="Korrigiere."
    )

    assert result == "Fertig."


def test_chat_does_not_estimate_the_size_of_a_request_without_num_ctx(monkeypatch) -> None:
    """
    Given  a very long prompt in a request that sets no num_ctx (like an OCR request)
    When   OllamaClient.chat() is called
    Then   the request is sent unchanged: only proofreading requests are size-checked
    """
    mock_ollama = _make_chat_mock_with_done_reason(["Fertig."], "stop")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    result = OllamaClient("http://127.0.0.1:11434").chat(
        "m", "Wort " * 20000, options={"temperature": 0}
    )

    assert result == "Fertig."


def test_chat_reports_a_window_spent_on_thinking_as_cut_off_not_empty(monkeypatch) -> None:
    """
    Given  a reasoning model whose whole window went into thinking: no text, done_reason="length"
    When   OllamaClient.chat() is called
    Then   ResponseTruncatedError (working memory) is raised, not "model returned an empty response"
    """
    mock_ollama = _make_chat_mock_with_done_reason([""], "length")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(ResponseTruncatedError, match="working memory"):
        OllamaClient("http://127.0.0.1:11434").chat("m", "hi")


def test_chat_still_reports_an_empty_answer_that_ended_normally(monkeypatch) -> None:
    """
    Given  a stream with no text that ended with done_reason="stop"
    When   OllamaClient.chat() is called
    Then   OCRError("model returned an empty response") is raised
    """
    mock_ollama = _make_chat_mock_with_done_reason([""], "stop")
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OCRError, match="empty response"):
        OllamaClient("http://127.0.0.1:11434").chat("m", "hi")


def test_chat_with_stats_tells_an_unreachable_server_from_a_failed_request(monkeypatch) -> None:
    """
    Given  a server that cannot be connected to, and one that answers with an HTTP error
    When   OllamaClient.chat_with_stats() is called
    Then   the first raises OllamaUnreachableError, the second a plain OCRError, and both are
           OCRErrors, so callers that handle a failed request keep working
    """
    import httpx  # noqa: PLC0415

    mock_ollama = _mock_ollama_with_streams(
        _stream_raising(httpx.ConnectError("refused")),
        _stream_raising(_ResponseError("model not found", 404)),
    )
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    with pytest.raises(OllamaUnreachableError) as unreachable:
        OllamaClient("http://127.0.0.1:11434").chat_with_stats("m", "hi")
    assert isinstance(unreachable.value, OCRError)

    with pytest.raises(OCRError) as refused:
        OllamaClient("http://127.0.0.1:11434").chat_with_stats("m", "hi")
    assert not isinstance(refused.value, OllamaUnreachableError)


def test_a_finished_answer_that_is_already_queued_survives_a_stop() -> None:
    """
    Given  a stream whose answer is complete while the cancel event is already set
    When   it is consumed
    Then   the finished answer is returned instead of being thrown away
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    cancel = threading.Event()

    def _answer():
        yield 1
        yield 2
        # Both chunks and the end marker are queued before the consumer looks again.
        time.sleep(0.05)

    stream = _answer()
    real_next_item = ollama_utils._next_item

    def next_item_after_stop(item_queue, timeout, cancel_event):
        if not cancel.is_set():
            deadline = time.monotonic() + 5
            while item_queue.qsize() < 3 and time.monotonic() < deadline:
                time.sleep(0.01)  # until both chunks and the end marker are queued
            cancel.set()  # ... then press "Stop now"
        return real_next_item(item_queue, timeout, cancel_event)

    with patch.object(ollama_utils, "_next_item", next_item_after_stop):
        result = _consume_iterator_with_first_token_timeout(stream, 5.0, cancel_event=cancel)

    assert result == [1, 2]


@pytest.mark.parametrize("reason", ["stop", "first_token_timeout", "deadline"])
def test_giving_up_on_a_request_cuts_its_connection(reason: str) -> None:
    """
    Given  a stream that never answers (or never ends) and a caller that can cut the connection
    When   the request is stopped, times out waiting for the first token, or hits its deadline
    Then   on_abandon is called once, so the server stops working on the abandoned request
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    cancel = threading.Event()
    cut: list[bool] = []

    def _silent():
        time.sleep(3)
        yield 1

    def _endless():
        while True:
            time.sleep(0.01)
            yield 1

    kwargs: dict = {"on_abandon": lambda: cut.append(True)}
    if reason == "stop":
        kwargs["cancel_event"] = cancel
        threading.Timer(0.1, cancel.set).start()
        stream, first, expected = _silent(), 30.0, RunStoppedError
    elif reason == "first_token_timeout":
        stream, first, expected = _silent(), 0.1, OCRTimeoutError
    else:
        kwargs["total_timeout"] = 0.1
        stream, first, expected = _endless(), 5.0, OCRDeadlineError

    with pytest.raises(expected):
        _consume_iterator_with_first_token_timeout(stream, first, **kwargs)

    assert cut == [True]


def test_a_failing_on_abandon_does_not_hide_the_stop() -> None:
    """
    Given  an on_abandon callback that raises
    When   the request is stopped
    Then   RunStoppedError is still what the caller sees
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    cancel = threading.Event()
    cancel.set()

    def _silent():
        time.sleep(1)
        yield 1

    def boom() -> None:
        raise RuntimeError("already closed")

    with pytest.raises(RunStoppedError):
        _consume_iterator_with_first_token_timeout(
            _silent(), 30.0, cancel_event=cancel, on_abandon=boom
        )


def test_cut_connection_closes_the_http_client_of_the_request() -> None:
    """
    Given  an ollama client with an httpx client inside
    When   its connection is cut
    Then   the httpx client is closed (a blocked read fails and the server sees the drop)
    """
    client = MagicMock()

    ollama_utils._cut_connection(client)

    client._client.close.assert_called_once_with()


def test_the_ollama_wrappers_let_a_stop_through_instead_of_degrading_to_a_warning() -> None:
    """
    Given  an Ollama client whose request is cut off by "Stop now"
    When   invoke_ollama_chat or invoke_ollama_structured runs
    Then   RunStoppedError propagates (any other error still becomes a warning)
    """
    from teachers_teammate.infrastructure._llm_invoke import (  # noqa: PLC0415
        invoke_ollama_chat,
        invoke_ollama_structured,
    )

    client = MagicMock()
    client.chat.side_effect = RunStoppedError

    def chat() -> tuple[str, str | None]:
        return invoke_ollama_chat(
            client,
            model="m",
            prompt="p",
            timeout=1.0,
            temperature=0.2,
            fallback="raw",
            failure="failed ({exc})",
        )

    def structured():
        return invoke_ollama_structured(
            client,
            model="m",
            prompt="p",
            timeout=1.0,
            temperature=0.2,
            schema={},
            parse=lambda text: (text, ""),
        )

    with pytest.raises(RunStoppedError):
        chat()
    with pytest.raises(RunStoppedError):
        structured()

    client.chat.side_effect = RuntimeError("boom")
    assert chat() == ("raw", "failed (boom)")


def test_stop_requested_reads_an_optional_event() -> None:
    """
    Given  no event, an unset event and a set event
    When   stop_requested() is asked
    Then   it is False, False, True
    """
    import threading  # noqa: PLC0415

    from teachers_teammate.exceptions import stop_requested  # noqa: PLC0415

    event = threading.Event()
    assert stop_requested(None) is False
    assert stop_requested(event) is False
    event.set()
    assert stop_requested(event) is True
