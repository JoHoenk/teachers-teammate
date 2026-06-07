r"""Post-processing of raw OCR model output.

LLM-based OCR engines — especially reasoning models such as DeepSeek-R1 run
through Ollama — sometimes leak chat-template artifacts into the extracted text:
reasoning blocks (``<think>...</think>``) and special control tokens. Two token
flavours occur:

* DeepSeek's full-width-pipe form using U+FF5C, e.g. the begin/end-of-sentence,
  User and Assistant markers.
* The ASCII/ChatML form, e.g. ``<|im_start|>``, ``<|im_end|>``, ``<|endoftext|>``
  (Qwen / many GGUF models).

:func:`clean_ocr_text` strips these so only the actual transcribed text flows
downstream into anonymisation / correction / evaluation / DOCX.
"""

from __future__ import annotations

import re

# U+FF5C FULLWIDTH VERTICAL LINE — the delimiter DeepSeek uses for its control
# tokens. Kept as an escape so source stays free of ambiguous-unicode literals.
_FW_PIPE = "\uff5c"

# Paired reasoning block, e.g. ``<think>...</think>`` — drop tags and content.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)

# Dangling close: reasoning models often emit the reasoning then ``</think>``
# then the answer, with the opening tag swallowed by the chat template. Drop
# everything up to and including the first ``</think>``.
_DANGLING_THINK_RE = re.compile(r"^.*?</think>", re.IGNORECASE | re.DOTALL)

# Bracketed special tokens, both the DeepSeek full-width-pipe form and the
# ASCII/ChatML form (which also covers ``<|im_end|>`` etc. — same shape).
_FULLWIDTH_TOKEN_RE = re.compile(f"<{_FW_PIPE}[^>]*?{_FW_PIPE}>")
_ASCII_TOKEN_RE = re.compile(r"<\|[^>]*?\|>")

# Three-or-more consecutive newlines left behind by removals → collapse to two.
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def clean_ocr_text(text: str) -> str:
    """Strip reasoning blocks and chat-template control tokens from model output.

    Args:
        text: Raw text as returned by an OCR processor.

    Returns:
        The text with reasoning blocks and special tokens removed, surrounding
        blank lines collapsed, and leading/trailing whitespace stripped.
    """
    if not text:
        return text

    text = _THINK_BLOCK_RE.sub("", text)
    # Only treat a lone ``</think>`` as a dangling close if one remains after
    # paired blocks above have been removed.
    if "</think>" in text.lower():
        text = _DANGLING_THINK_RE.sub("", text)

    text = _FULLWIDTH_TOKEN_RE.sub("", text)
    text = _ASCII_TOKEN_RE.sub("", text)

    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
