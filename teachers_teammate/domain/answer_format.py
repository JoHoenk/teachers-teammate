"""The answer format of the proofreading stage: what the app tells the model about its reply.

Pure text, no I/O.  The **instructions** (``correction_prompts``) say *what* to correct; the
**answer format** here says *how to fill in the reply*.  It is English for every language, names
no language and has no placeholders -- the languages travel in the message that carries the text
-- so a teacher can read and edit it as plain text.

The review format describes the same structure as ``infrastructure/correction_schema.py``.
Providers that bind a schema send its field descriptions too; Ollama only constrains the keys,
so the structure is spelled out here as well.  A unit test keeps :data:`REVIEW_FIELDS` and the
schema in step.
"""

from __future__ import annotations

ANSWER_FORMAT_VERSION = 3
"""Bumped whenever the built-in formats or the schema change: it is part of the cache key, and
an edited format written for another version is not used (see :func:`migrate_saved_formats`)."""

REVIEW_FIELDS: tuple[str, ...] = (
    "sentences",
    "original",
    "corrected",
    "changes",
    "category",
    "explanation",
    "unclear",
    "original_expression",
    "why_unclear",
    "recommended_version",
    "meaning_determinable",
)
"""Every key of the review answer; an edited review format should name all of them."""

CATEGORY_NAMES: tuple[str, ...] = (
    "grammar",
    "spelling",
    "punctuation",
    "word_choice",
    "word_order",
    "other",
)

REVIEW_FORMAT = (
    "Answer with a JSON object that has two lists.\n"
    '"sentences": every sentence you changed or that contains unclear wording, in the order '
    "they appear in the text. Each entry has:\n"
    '- "original": the sentence exactly as written, including mistakes, punctuation and '
    "formatting marks\n"
    '- "corrected": the corrected sentence, without any marker\n'
    '- "changes": one entry per separate edit (an empty list if the sentence is only unclear). '
    "Each change has:\n"
    '  - "original" and "corrected": the words before and after the edit; "" for an insertion '
    "or a deletion\n"
    '  - "category": one of ' + ", ".join(CATEGORY_NAMES) + "\n"
    '  - "explanation": one short sentence saying why the edit was made\n'
    '"unclear": every unclear or ambiguous expression in the text, even a grammatically '
    "correct one. Each entry has:\n"
    '- "original_expression": the exact words from the text\n'
    '- "why_unclear": what makes it unclear or ambiguous\n'
    '- "recommended_version": a possible clearer version, in the language of the text\n'
    '- "meaning_determinable": false if the intended meaning cannot be determined from the text\n'
    "Write corrections and recommended versions in the language of the text; never translate the "
    "student's text. Write explanations and reasons in the explanation language."
)

TEXT_FORMAT = (
    "Return only the corrected text, nothing else. "
    "Keep the line breaks, paragraphs and formatting of the text exactly as they are, and do "
    "not reorder, add or remove any sentence. "
    "Write in the language of the text; never translate the student's text."
)


def builtin_format(mode: str) -> str:
    """Return the built-in answer format of *mode* (``review``; anything else = corrections only)."""
    return REVIEW_FORMAT if mode == "review" else TEXT_FORMAT


def resolve_format(mode: str, custom: str = "") -> str:
    """Return the format sent for *mode*: the teacher's *custom* text, else the built-in one."""
    return custom.strip() or builtin_format(mode)


def missing_fields(review_format: str) -> list[str]:
    """Return the review fields that an edited *review_format* does not name.

    Empty text means "the built-in format" and is complete by definition.
    """
    if not review_format.strip():
        return []
    return [name for name in REVIEW_FIELDS if name not in review_format]


def migrate_saved_formats(review: str, text: str, version: int) -> tuple[str, str, bool]:
    """Return ``(review, text, reset)`` for saved formats written for answer format *version*.

    An edited format that was written for another version describes another structure than the
    one the app asks for now, so it is dropped (``reset`` is true) instead of being used
    silently.  A saved copy of a built-in text is not an edit either: it is stored as ``""``.
    """
    review, text = review.strip(), text.strip()
    if version != ANSWER_FORMAT_VERSION:
        return "", "", bool(review or text)
    if _same(review, REVIEW_FORMAT):
        review = ""
    if _same(text, TEXT_FORMAT):
        text = ""
    return review, text, False


def normalize_format_text(text: str) -> str:
    """Return *text* with all runs of whitespace collapsed, for comparing formats."""
    return " ".join(text.split())


def _same(a: str, b: str) -> bool:
    return normalize_format_text(a) == normalize_format_text(b)
