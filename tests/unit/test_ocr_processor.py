"""Unit tests for teachers_teammate.infrastructure.ocr_processor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.exceptions import OCRError
from teachers_teammate.infrastructure.ocr_processor import (
    OllamaOCRProcessor,
    TesseractOCRProcessor,
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


# ── TesseractOCRProcessor ──────────────────────────────────────────────────


def test_tesseract_raises_ocr_error_when_pytesseract_missing(
    sample_png: Path,
) -> None:
    """
    Given  pytesseract is not importable (simulated via sys.modules mock)
    When   TesseractOCRProcessor.process_image() is called
    Then   an OCRError is raised (not ImportError propagating)
    """
    import sys  # noqa: PLC0415

    with patch.dict(sys.modules, {"pytesseract": None, "PIL": None, "PIL.Image": None}):
        proc = TesseractOCRProcessor()
        with pytest.raises(OCRError, match="pytesseract or Pillow not installed"):
            proc.process_image(sample_png, language="English")


def test_tesseract_returns_text_from_pytesseract(
    sample_png: Path,
) -> None:
    """
    Given  pytesseract.image_to_string is mocked to return 'hello world'
    When   TesseractOCRProcessor.process_image() is called
    Then   'hello world' is returned
    """
    mock_pil = MagicMock()
    mock_img = MagicMock()
    mock_pil.Image.open.return_value = mock_img

    mock_tess = MagicMock()
    mock_tess.image_to_string.return_value = "hello world"

    with patch.dict(
        "sys.modules", {"PIL": mock_pil, "PIL.Image": mock_pil.Image, "pytesseract": mock_tess}
    ):
        proc = TesseractOCRProcessor()
        result = proc.process_image(sample_png, language="English")

    assert result == "hello world"
    mock_tess.image_to_string.assert_called_once_with(mock_img, lang="eng")


def test_tesseract_maps_german_language_to_deu(
    sample_png: Path,
) -> None:
    """
    Given  language='German'
    When   TesseractOCRProcessor.process_image() is called
    Then   pytesseract.image_to_string receives lang='deu'
    """
    mock_pil = MagicMock()
    mock_tess = MagicMock()
    mock_tess.image_to_string.return_value = "text"

    with patch.dict(
        "sys.modules", {"PIL": mock_pil, "PIL.Image": mock_pil.Image, "pytesseract": mock_tess}
    ):
        proc = TesseractOCRProcessor()
        proc.process_image(sample_png, language="German")

    _, call_kwargs = mock_tess.image_to_string.call_args
    assert call_kwargs.get("lang") == "deu"


def test_tesseract_unknown_language_passed_through(
    sample_png: Path,
) -> None:
    """
    Given  language='Custom'  (not in the built-in map)
    When   TesseractOCRProcessor.process_image() is called
    Then   pytesseract.image_to_string receives lang='Custom' unchanged
    """
    mock_pil = MagicMock()
    mock_tess = MagicMock()
    mock_tess.image_to_string.return_value = "text"

    with patch.dict(
        "sys.modules", {"PIL": mock_pil, "PIL.Image": mock_pil.Image, "pytesseract": mock_tess}
    ):
        proc = TesseractOCRProcessor()
        proc.process_image(sample_png, language="Custom")

    _, call_kwargs = mock_tess.image_to_string.call_args
    assert call_kwargs.get("lang") == "Custom"


def test_tesseract_raises_ocr_error_on_pytesseract_exception(
    sample_png: Path,
) -> None:
    """
    Given  pytesseract.image_to_string raises a RuntimeError
    When   TesseractOCRProcessor.process_image() is called
    Then   an OCRError is raised (not the raw RuntimeError)
    """
    mock_pil = MagicMock()
    mock_tess = MagicMock()
    mock_tess.image_to_string.side_effect = RuntimeError("tesseract crashed")

    with patch.dict(
        "sys.modules", {"PIL": mock_pil, "PIL.Image": mock_pil.Image, "pytesseract": mock_tess}
    ):
        proc = TesseractOCRProcessor()
        with pytest.raises(OCRError):
            proc.process_image(sample_png, language="English")


# ── LangChainOCRProcessor ──────────────────────────────────────────────────


def test_langchain_ocr_returns_model_output(sample_png: Path) -> None:
    """
    Given  a mocked LangChain model that returns 'langchain text'
    When   LangChainOCRProcessor.process_image() is called
    Then   'langchain text' is returned
    """
    from teachers_teammate.infrastructure.ocr_processor import LangChainOCRProcessor  # noqa: PLC0415

    mock_model = MagicMock()
    # Simulate chain.invoke() returning the text
    mock_chain_result = "langchain text"
    # The processor builds: chain = self._model | StrOutputParser()
    # We mock __or__ on the model to return an object whose invoke returns our text
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = mock_chain_result
    mock_model.__or__ = MagicMock(return_value=mock_chain)

    proc = LangChainOCRProcessor(mock_model)
    result = proc.process_image(sample_png, language="English")
    assert result == "langchain text"


def test_langchain_ocr_raises_ocr_error_on_empty_response(sample_png: Path) -> None:
    """
    Given  a mocked LangChain model chain that returns an empty string
    When   LangChainOCRProcessor.process_image() is called
    Then   an OCRError is raised with 'empty response' in the message
    """
    from teachers_teammate.infrastructure.ocr_processor import LangChainOCRProcessor  # noqa: PLC0415

    mock_model = MagicMock()
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = "   "  # whitespace only → treated as empty
    mock_model.__or__ = MagicMock(return_value=mock_chain)

    proc = LangChainOCRProcessor(mock_model)
    with pytest.raises(OCRError, match="empty response"):
        proc.process_image(sample_png, language="English")


def test_langchain_ocr_raises_ocr_error_on_invoke_exception(sample_png: Path) -> None:
    """
    Given  a mocked LangChain model chain that raises a ValueError
    When   LangChainOCRProcessor.process_image() is called
    Then   an OCRError is raised (not the raw ValueError)
    """
    from teachers_teammate.infrastructure.ocr_processor import LangChainOCRProcessor  # noqa: PLC0415

    mock_model = MagicMock()
    mock_chain = MagicMock()
    mock_chain.invoke.side_effect = ValueError("bad token")
    mock_model.__or__ = MagicMock(return_value=mock_chain)

    proc = LangChainOCRProcessor(mock_model)
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


# ── PaddleOCRProcessor ──────────────────────────────────────────────────────


def test_paddle_raises_ocr_error_when_paddleocr_not_installed(
    sample_png: Path,
) -> None:
    """
    Given  the paddleocr package is not installed (simulated via sys.modules)
    When   PaddleOCRProcessor.process_image() is called
    Then   an OCRError is raised (wrapping the RuntimeError from _get_paddle)
    """
    import sys  # noqa: PLC0415

    from teachers_teammate.infrastructure.ocr_processor import PaddleOCRProcessor  # noqa: PLC0415

    with patch.dict(sys.modules, {"paddleocr": None}):
        proc = PaddleOCRProcessor(language="English")
        with pytest.raises(OCRError):
            proc.process_image(sample_png, language="English")


def test_paddle_process_image_returns_joined_texts(
    sample_png: Path,
) -> None:
    """
    Given  a mocked PaddleOCR whose predict() returns recognised text lines
    When   PaddleOCRProcessor.process_image() is called
    Then   the lines are joined with newlines and returned
    """
    from teachers_teammate.infrastructure.ocr_processor import PaddleOCRProcessor  # noqa: PLC0415

    mock_result = MagicMock()
    mock_result.__getitem__ = MagicMock(
        side_effect=lambda i: MagicMock(json={"res": {"rec_texts": ["line one", "line two"]}})
    )
    mock_paddle_instance = MagicMock()
    mock_paddle_instance.predict.return_value = mock_result

    mock_paddle_class = MagicMock(return_value=mock_paddle_instance)

    import sys  # noqa: PLC0415

    mock_paddle_module = MagicMock()
    mock_paddle_module.PaddleOCR = mock_paddle_class

    with patch.dict(sys.modules, {"paddleocr": mock_paddle_module}):
        proc = PaddleOCRProcessor(language="English")
        result = proc.process_image(sample_png, language="English")

    assert result == "line one\nline two"


def test_paddle_reinitializes_on_language_change(
    sample_png: Path,
) -> None:
    """
    Given  a PaddleOCRProcessor initialised for English
    When   process_image() is called with language='German'
    Then   the internal paddle instance is cleared (forcing re-init with the new language)
    """
    from teachers_teammate.infrastructure.ocr_processor import PaddleOCRProcessor  # noqa: PLC0415

    mock_result = MagicMock()
    mock_result.__getitem__ = MagicMock(
        side_effect=lambda i: MagicMock(json={"res": {"rec_texts": []}})
    )
    mock_paddle_instance = MagicMock()
    mock_paddle_instance.predict.return_value = mock_result

    mock_paddle_class = MagicMock(return_value=mock_paddle_instance)

    import sys  # noqa: PLC0415

    mock_paddle_module = MagicMock()
    mock_paddle_module.PaddleOCR = mock_paddle_class

    with patch.dict(sys.modules, {"paddleocr": mock_paddle_module}):
        proc = PaddleOCRProcessor(language="English")
        # First call — initialised for English
        proc.process_image(sample_png, language="English")
        first_call_count = mock_paddle_class.call_count

        # Second call with different language — should re-initialise
        proc.process_image(sample_png, language="German")
        assert mock_paddle_class.call_count > first_call_count


def test_paddle_returns_empty_string_when_predict_returns_falsy(
    sample_png: Path,
) -> None:
    """
    Given  PaddleOCR.predict() returns an empty / falsy result
    When   PaddleOCRProcessor.process_image() is called
    Then   an empty string is returned (no exception)
    """
    import sys  # noqa: PLC0415

    from teachers_teammate.infrastructure.ocr_processor import PaddleOCRProcessor  # noqa: PLC0415

    mock_paddle_instance = MagicMock()
    mock_paddle_instance.predict.return_value = None

    mock_paddle_class = MagicMock(return_value=mock_paddle_instance)
    mock_paddle_module = MagicMock()
    mock_paddle_module.PaddleOCR = mock_paddle_class

    with patch.dict(sys.modules, {"paddleocr": mock_paddle_module}):
        proc = PaddleOCRProcessor(language="English")
        result = proc.process_image(sample_png, language="English")

    assert result == ""


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
