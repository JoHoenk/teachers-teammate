"""Unit tests for teachers_teammate.infrastructure.ocr_processor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.exceptions import OCRError
from teachers_teammate.infrastructure.ocr_processor import (
    OllamaOCRProcessor,
    _encode_image,
    get_ocr_prompt,
)
from teachers_teammate.infrastructure.ollama_utils import OllamaClient


def _make_ollama_proc(
    model: str = "test-model", url: str = "http://localhost:11434", timeout: int = 10
) -> OllamaOCRProcessor:
    return OllamaOCRProcessor(model_name=model, client=OllamaClient(url), timeout=timeout)


# ── _encode_image ──────────────────────────────────────────────────────────


def test_encode_image_returns_non_empty_string(sample_png: Path) -> None:
    """
    Given  a valid PNG file
    When   _encode_image() is called
    Then   a non-empty string is returned
    """
    result = _encode_image(sample_png)
    assert isinstance(result, str)
    assert len(result) > 0


def test_encode_image_returns_valid_base64(sample_png: Path) -> None:
    """
    Given  a valid PNG file
    When   _encode_image() is called and the result is base64-decoded
    Then   the decoded bytes are non-empty (confirming valid base64 encoding)
    """
    import base64  # noqa: PLC0415

    result = _encode_image(sample_png)
    # Should not raise
    decoded = base64.b64decode(result)
    assert len(decoded) > 0


# ── OllamaOCRProcessor ─────────────────────────────────────────────────────


def test_ollama_process_image_returns_extracted_text(
    mocker,
    sample_png: Path,
) -> None:
    """
    Given  OllamaClient.chat patched to return "extracted text"
    When   OllamaOCRProcessor.process_image() is called
    Then   the text from chat() is returned
    """
    mocker.patch.object(OllamaClient, "chat", return_value="extracted text")

    proc = _make_ollama_proc()
    result = proc.process_image(sample_png, language="English")
    assert result == "extracted text"


def test_ollama_process_image_sends_correct_model(
    mocker,
    sample_png: Path,
) -> None:
    """
    Given  an OllamaOCRProcessor constructed with model_name="my-vision-model"
    When   process_image() is called
    Then   OllamaClient.chat is called with model="my-vision-model" as first argument
    """
    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="text")

    proc = _make_ollama_proc(model="my-vision-model")
    proc.process_image(sample_png, language="English")

    assert mock_chat.call_args.args[0] == "my-vision-model"


def test_ollama_process_image_passes_temperature_in_options(
    mocker,
    sample_png: Path,
) -> None:
    """
    Given  an OllamaOCRProcessor constructed with temperature=0.5
    When   process_image() is called
    Then   OllamaClient.chat receives options={"temperature": 0.5, ...}
    """
    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="text")

    proc = OllamaOCRProcessor(
        model_name="model", client=OllamaClient("http://localhost:11434"), temperature=0.5
    )
    proc.process_image(sample_png, language="English")

    call_kwargs = mock_chat.call_args.kwargs
    assert call_kwargs["options"]["temperature"] == 0.5


def test_ollama_ocr_processor_default_temperature_is_zero(
    mocker,
    sample_png: Path,
) -> None:
    """
    Given  an OllamaOCRProcessor constructed without an explicit temperature
    When   process_image() is called
    Then   OllamaClient.chat receives options={"temperature": 0.0, ...}
    """
    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="text")

    proc = _make_ollama_proc()
    proc.process_image(sample_png, language="English")

    call_kwargs = mock_chat.call_args.kwargs
    assert call_kwargs["options"]["temperature"] == 0.0


def test_ollama_process_image_propagates_ocr_error_from_client(
    mocker,
    sample_png: Path,
) -> None:
    """
    Given  OllamaClient.chat raises OCRError
    When   OllamaOCRProcessor.process_image() is called
    Then   the OCRError is propagated unchanged (the processor adds no error handling)

    The various failure modes (connection refused, HTTP error, empty response,
    timeout) are owned by OllamaClient.chat and tested in test_ollama_utils.py.
    """
    mocker.patch.object(OllamaClient, "chat", side_effect=OCRError("connection refused"))

    proc = _make_ollama_proc()
    with pytest.raises(OCRError):
        proc.process_image(sample_png, language="English")


# ── OllamaOCRProcessor — payload construction ──────────────────────────────


def test_ollama_bare_url_is_normalized_in_client() -> None:
    """
    Given  url="localhost:11434" with no scheme
    When   OllamaClient is constructed (and passed to OllamaOCRProcessor)
    Then   client.base_url is "http://localhost:11434"
    """
    proc = _make_ollama_proc(url="localhost:11434")
    assert proc._client.base_url == "http://localhost:11434"


def test_ollama_process_image_includes_language_in_payload(
    mocker,
    sample_png: Path,
) -> None:
    """
    Given  language="German" and OllamaClient.chat patched
    When   OllamaOCRProcessor.process_image() is called
    Then   the word "German" appears in the prompt passed to chat()
    """
    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="text")

    proc = _make_ollama_proc()
    proc.process_image(sample_png, language="German")

    prompt_arg = mock_chat.call_args.args[1]
    assert "German" in prompt_arg


def test_ollama_process_image_includes_non_empty_base64_image(
    mocker,
    sample_png: Path,
) -> None:
    """
    Given  a valid PNG and OllamaClient.chat patched
    When   OllamaOCRProcessor.process_image() is called
    Then   the images kwarg passed to chat() contains a non-empty valid base64 string
    """
    import base64 as _b64  # noqa: PLC0415

    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="text")

    proc = _make_ollama_proc()
    proc.process_image(sample_png, language="English")

    images_kwarg = mock_chat.call_args.kwargs["images"]
    b64_str = images_kwarg[0]
    assert isinstance(b64_str, str) and len(b64_str) > 0
    assert len(_b64.b64decode(b64_str)) > 0


# ── get_ocr_prompt ─────────────────────────────────────────────────────────


def test_get_ocr_prompt_embeds_language() -> None:
    """
    Given  a language name
    When   get_ocr_prompt() is called
    Then   the returned prompt contains that language name
    """
    prompt = get_ocr_prompt("German")
    assert "German" in prompt


def test_ollama_process_image_passes_prompt_with_language(
    mocker,
    sample_png: Path,
) -> None:
    """
    Given  an OllamaOCRProcessor
    When   process_image() is called with language='English'
    Then   OllamaClient.chat receives a prompt containing 'English'
    """
    mock_chat = mocker.patch.object(OllamaClient, "chat", return_value="text")

    proc = _make_ollama_proc(model="llava:latest")
    proc.process_image(sample_png, language="English")

    prompt_arg = mock_chat.call_args.args[1]
    assert "English" in prompt_arg


def test_ollama_ocr_keeps_the_text_read_when_a_page_hits_the_output_limit(
    monkeypatch, sample_png: Path
) -> None:
    """
    Given  a vision model whose answer stops at the num_predict page limit (done_reason="length")
    When   OllamaOCRProcessor.process_image() is called
    Then   the text read so far is returned, not an error about the model's working memory
    """
    import sys  # noqa: PLC0415

    chunk = MagicMock()
    chunk.message.content = "Seite eins, bis zum Limit"
    chunk.done_reason = "length"
    chunk.prompt_eval_count = 900
    chunk.eval_count = 2048
    mock_ollama = MagicMock()
    mock_ollama.Client.return_value.chat.return_value = iter([chunk])
    mock_ollama.ResponseError = Exception
    monkeypatch.setitem(sys.modules, "ollama", mock_ollama)

    text = _make_ollama_proc().process_image(sample_png, language="German")

    assert text == "Seite eins, bis zum Limit"
