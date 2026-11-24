"""The instruction a vision model gets for a page, and the language it reads.

Pure text: the prompt is part of the OCR stage's cache key, so changing a word here re-reads
every stored page of that language.
"""

from __future__ import annotations

_OCR_PROMPT = "Extract all visible text from this image in {language} exactly as written."


def get_ocr_prompt(language: str) -> str:
    """Return the OCR extraction prompt for *language*."""
    return _OCR_PROMPT.format(language=language)
