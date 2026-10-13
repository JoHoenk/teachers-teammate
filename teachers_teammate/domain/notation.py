"""Prompt hints that explain the stored Markdown notation to correction/evaluation LLMs.

Appended to the *system* prompt (never to the user-editable presets) only when the text
is Markdown, so prompts for plain text -- and their cache fingerprints -- stay unchanged.
The hints must not contain braces: they are inserted into a LangChain prompt template.
"""

from __future__ import annotations

from typing import Literal

from .formatted_text import TextFormat

NotationTask = Literal["correction", "evaluation"]

_CORRECTION_HINT = (
    "The text is written in Markdown. ~~text~~ marks words the writer crossed out; "
    "<sup>text</sup> marks words the writer inserted above the line; <sub>text</sub> marks "
    "words written below the line; <u>text</u> is underlined. "
    "Keep all of these marks and the Markdown structure unchanged. "
    "Do not correct crossed-out words; use them only as context."
)

_EVALUATION_HINT = (
    "The text is written in Markdown. ~~text~~ marks words the writer crossed out; they are "
    "not part of the submission and must not be evaluated. <sup>text</sup> marks words the "
    "writer inserted above the line; treat them as part of the sentence. "
    "All other Markdown marks only describe formatting."
)

_HINTS: dict[NotationTask, str] = {
    "correction": _CORRECTION_HINT,
    "evaluation": _EVALUATION_HINT,
}


def with_notation_hint(system: str, text_format: TextFormat, *, task: NotationTask) -> str:
    """Return *system* with the notation hint for *task* appended when *text_format* is Markdown."""
    if text_format != "markdown":
        return system
    return f"{system}\n\n{_HINTS[task]}"
