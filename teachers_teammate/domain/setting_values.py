"""The closed sets of values a setting accepts that no other domain module owns."""

from __future__ import annotations

from enum import StrEnum


class PreprocessMethod(StrEnum):
    """How a page is prepared before it is read."""

    AUTO = "auto"
    """The automatic preparation: looks at each page and straightens, turns and evens it out."""

    NONE = "none"
    """The original page, without a step."""


class ContextSetting(StrEnum):
    """The context window of the content review: sized by the computer, or pinned in tokens."""

    AUTO = "auto"
    TOKENS_8192 = "8192"
    TOKENS_16384 = "16384"
    TOKENS_32768 = "32768"
