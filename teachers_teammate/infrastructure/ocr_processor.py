"""OCR processors: extract text from a single preprocessed image.

:class:`OllamaOCRProcessor` — uses an Ollama vision model (default).
:class:`TesseractOCRProcessor` — uses a local Tesseract installation.
:class:`PaddleOCRProcessor` — uses PaddleOCR (fully local, no server needed).
:class:`LangChainOCRProcessor` — uses any LangChain vision-capable model.

All four implement :class:`~teachers_teammate.interfaces.OCRProcessor`.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

from ..exceptions import OCRError, ProviderNotAvailableError
from ..interfaces import OCRProcessor
from .ollama_utils import OllamaClient

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

_OCR_PROMPT = "Extract all visible text from this image in {language} exactly as written."

SUPPORTED_OCR_ENGINES: list[str] = ["ollama", "tesseract", "paddleocr", "langchain"]

_ENGINE_PREPROCESS_DEFAULTS: dict[str, str] = {
    "ollama": "grayscale",
    "tesseract": "clahe",
    "paddleocr": "grayscale",
    "langchain": "grayscale",
}


def default_preprocess_for_engine(engine: str) -> str:
    """Return the recommended preprocessing method for *engine*."""
    return _ENGINE_PREPROCESS_DEFAULTS.get(engine, "adaptive_threshold")


def get_ocr_prompt(language: str) -> str:
    """Return the OCR extraction prompt for *language*."""
    return _OCR_PROMPT.format(language=language)


class OllamaOCRProcessor(OCRProcessor):
    """Calls an Ollama vision model to extract text from a single image via streaming."""

    def __init__(
        self,
        model_name: str,
        client: OllamaClient,
        timeout: int = 300,
        temperature: float = 0.0,
    ) -> None:
        self._model = model_name
        self._client = client
        self._timeout = timeout
        self._temperature = temperature

    def process_image(self, image_path: Path, language: str = "English") -> str:
        """Extract text from a single preprocessed image.

        Raises:
            :exc:`~teachers_teammate.exceptions.OCRError`: on failure.
        """
        b64 = _encode_image(image_path)
        prompt = get_ocr_prompt(language)
        return self._client.chat(
            self._model,
            prompt,
            images=[b64],
            options={"temperature": self._temperature, "num_predict": 2048},
            timeout=self._timeout,
        )


def _encode_image(image_path: Path) -> str:
    with image_path.open("rb") as fh:
        return base64.b64encode(fh.read()).decode("utf-8")


# Language name → Tesseract 3-letter code (ISO 639-2/T)
_LANG_MAP: dict[str, str] = {
    "english": "eng",
    "german": "deu",
    "french": "fra",
    "spanish": "spa",
    "italian": "ita",
    "portuguese": "por",
    "dutch": "nld",
}


class TesseractOCRProcessor(OCRProcessor):
    """Extracts text using a local Tesseract installation.

    Requires ``pytesseract`` and ``Pillow``:
    ``pip install pytesseract Pillow``.
    Tesseract itself must also be installed on the system
    (https://github.com/tesseract-ocr/tesseract).
    """

    def process_image(self, image_path: Path, language: str = "English") -> str:
        """Extract text from *image_path* using Tesseract.

        Args:
            image_path: Path to the preprocessed image.
            language:   Natural language name (e.g. ``"German"``).  Common names
                        are mapped to Tesseract 3-letter codes automatically;
                        unknown values are passed through unchanged.

        Returns:
            Extracted text.

        Raises:
            :exc:`~teachers_teammate.exceptions.OCRError`: on failure.
        """
        try:
            # optional deps; lazy imports to avoid startup ImportError when pytesseract/Pillow is not installed
            from PIL import Image  # noqa: PLC0415
            import pytesseract  # noqa: PLC0415
        except ImportError as exc:
            raise OCRError(
                f"pytesseract or Pillow not installed: {exc} — run: pip install pytesseract Pillow"
            ) from exc
        try:
            lang_code = _LANG_MAP.get(language.lower(), language)
            img = Image.open(image_path)
            return pytesseract.image_to_string(img, lang=lang_code)
        except Exception as exc:  # pytesseract / PIL raise engine-specific errors
            raise OCRError(str(exc)) from exc


# Language name → PaddleOCR lang code
# Full list: https://paddlepaddle.github.io/PaddleOCR/latest/ppocr/blog/multi_languages.html
_PADDLE_LANG_MAP: dict[str, str] = {
    "english": "en",
    "german": "german",
    "french": "french",
    "spanish": "es",
    "italian": "it",
    "portuguese": "pt",
    "dutch": "nl",
}


def paddle_gpu_available() -> bool:
    """Return True when the installed paddle build has CUDA support and a GPU is present."""
    try:
        import paddle  # noqa: PLC0415

        return paddle.device.is_compiled_with_cuda() and paddle.device.get_device_count("gpu") > 0  # pylint: disable=no-member
    except Exception:  # noqa: BLE001  # paddle import/probe may raise on a broken or CPU-only install; treat as no GPU
        return False


class PaddleOCRProcessor(OCRProcessor):
    """Extracts text using PaddleOCR — completely local, no server required.

    Requires ``paddlepaddle`` and ``paddleocr``:
    ``pip install teachers-teammate[paddle]``  (CPU build) or
    ``pip install paddlepaddle-gpu paddleocr``  (GPU build).

    Model weights are downloaded automatically on first use (~100-400 MB per
    language) and cached in ``~/.paddleocr/``.  GPU inference is used when a
    CUDA-capable device is present and a GPU-enabled build is installed;
    otherwise it falls back to CPU automatically.

    .. note::
        PaddleOCR works on full-colour images.  Using binarising preprocessing
        methods (``adaptive_threshold``, ``clahe``) will degrade accuracy;
        prefer ``none`` or ``grayscale``.
    """

    def __init__(self, language: str = "English") -> None:
        self._current_language = language.lower()
        self._lang_code = _PADDLE_LANG_MAP.get(self._current_language, "en")
        self._paddle: object | None = None  # lazily initialised on first call

    def _get_paddle(self):
        if self._paddle is None:
            try:
                # Skip the latency check against the model hosting server on every
                # startup (models are still downloaded automatically on first use).
                # optional dep; lazy import so the paddle env var is set before PaddleOCR starts
                import os  # noqa: PLC0415

                os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
                # optional dep; lazy import to avoid startup ImportError when paddleocr is not installed
                from paddleocr import PaddleOCR  # noqa: PLC0415
            except ImportError as exc:
                raise ProviderNotAvailableError(
                    f"{exc} — run: pip install paddlepaddle paddleocr"
                ) from exc
            use_gpu = paddle_gpu_available()
            _kwargs: dict = {
                "lang": self._lang_code,
                "use_gpu": use_gpu,
                # Skip heavy document-orientation / unwarping models — not needed
                # for typical scanned pages and saves several seconds on first run.
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
                # Disable oneDNN (MKL-DNN): the CPU paddlepaddle build has a known
                # unimplemented instruction when oneDNN is active.  On GPU builds
                # MKL-DNN is irrelevant and must not be set.
                "enable_mkldnn": not use_gpu,
            }
            try:
                self._paddle = PaddleOCR(**_kwargs)
            except Exception:  # GPU init can fail (driver mismatch, OOM)
                if use_gpu:
                    # Silent retry on CPU — user still gets OCR, just slower
                    self._paddle = PaddleOCR(
                        **{**_kwargs, "use_gpu": False, "enable_mkldnn": False}
                    )
                else:
                    raise
        return self._paddle

    def process_image(self, image_path: Path, language: str = "English") -> str:
        """Extract text from *image_path* using PaddleOCR.

        Args:
            image_path: Path to the image file.
            language:   Language of the document.  If this differs from the
                        language used at construction time, the PaddleOCR model
                        is re-initialised for the new language.

        Raises:
            :exc:`~teachers_teammate.exceptions.OCRError`: on failure.
        """
        # Re-initialise when caller requests a different language.
        requested = language.lower()
        if requested != self._current_language:
            self._current_language = requested
            self._lang_code = _PADDLE_LANG_MAP.get(requested, "en")
            self._paddle = None  # force lazy re-init
        try:
            paddle = self._get_paddle()
            result = paddle.predict(str(image_path))
            if not result:
                return ""
            rec_texts = result[0].json.get("res", {}).get("rec_texts", [])
            return "\n".join(rec_texts)
        except RuntimeError as exc:
            raise OCRError(str(exc)) from exc
        except OCRError:
            raise
        except Exception as exc:
            raise OCRError(str(exc)) from exc


class LangChainOCRProcessor(OCRProcessor):
    """Calls any LangChain vision-capable model to extract text from a single image.

    Accepts any :class:`~langchain_core.language_models.BaseChatModel` that
    supports multimodal input (images).  Use
    :func:`~teachers_teammate.infrastructure.llm_factory.build_llm` to build the model before
    passing it in — this works with all supported providers (OpenAI, Anthropic,
    Google, Ollama, …).

    The image is base64-encoded and sent as a ``data:`` URI so no external
    file hosting is needed.
    """

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    def process_image(self, image_path: Path, language: str = "English") -> str:
        """Extract text from *image_path* using a LangChain vision model.

        Args:
            image_path: Path to the preprocessed image.
            language:   Language of the document text.

        Raises:
            :exc:`~teachers_teammate.exceptions.OCRError`: on failure.
        """
        try:
            from langchain_core.messages import HumanMessage  # noqa: PLC0415
            from langchain_core.output_parsers import StrOutputParser  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise OCRError(f"langchain-core is not installed: {exc}") from exc
        try:
            b64 = _encode_image(image_path)
            suffix = image_path.suffix.lower().lstrip(".")
            mime = (
                "jpeg"
                if suffix in ("jpg", "jpeg")
                else (suffix if suffix in ("png", "gif", "webp") else "png")
            )
            prompt = get_ocr_prompt(language)
            message = HumanMessage(
                content=[
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/{mime};base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ]
            )
            chain = self._model | StrOutputParser()
            result: str = chain.invoke([message])
            if not result.strip():
                raise OCRError("model returned an empty response")
            return result
        except OCRError:
            raise
        except Exception as exc:
            raise OCRError(str(exc)) from exc
