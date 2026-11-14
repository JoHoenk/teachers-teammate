"""OCR processors: extract text from a single preprocessed image.

:class:`OllamaOCRProcessor` — uses an Ollama vision model (default).
:class:`~teachers_teammate.infrastructure.ppocr.engine.PpOcrProcessor` — the built-in reader
(fully local, no server needed).

Both implement :class:`~teachers_teammate.interfaces.OCRProcessor`.  Online vision
models are intentionally not supported: a page cannot be anonymised before it is read.
"""

from __future__ import annotations

import base64
from pathlib import Path

from ..domain.data_location import LOCAL_OCR_ENGINES
from ..interfaces import OCRProcessor
from .ollama_utils import OllamaClient

_OCR_PROMPT = "Extract all visible text from this image in {language} exactly as written."

OCR_ANSWER_TOKENS = 2048
"""Tokens a page's answer may take.  A page of handwriting needs more than a short answer, and a
page that runs into the limit keeps the text read so far."""

# Every engine reads pages on a computer the user controls (see domain/data_location.py).
SUPPORTED_OCR_ENGINES: list[str] = list(LOCAL_OCR_ENGINES)


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
            options={"temperature": self._temperature, "num_predict": OCR_ANSWER_TOKENS},
            timeout=self._timeout,
            # The page limit is deliberate: a page that runs into it keeps the text read so far.
            allow_truncated=True,
        )


def _encode_image(image_path: Path) -> str:
    with image_path.open("rb") as fh:
        return base64.b64encode(fh.read()).decode("utf-8")
