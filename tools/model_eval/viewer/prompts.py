"""The instructions a variant's requests carried, as text a reader can read.

A run's header keeps only the hash of its instructions (``instructions_sha256``).  The page shows
the instructions themselves, built from the code that builds the requests (the same functions the
hash is made from), and says whether they are *exactly* what the run sent: the hash of what is
built now is compared with the run's.  When it differs, the text is today's, and the page says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from teachers_teammate.domain.correction_policy import DEFAULT_LEVEL
from teachers_teammate.domain.correction_prompts import (
    CORRECT_SYSTEM,
    EXPLAIN_SYSTEM,
    PROMPT_VERSION,
    UNCLEAR_SYSTEM,
    parameters,
)
from teachers_teammate.domain.formatted_text import PLAIN
from teachers_teammate.infrastructure.correction import instruction_parts

from .. import protocol
from ..answer_mode import AnswerMode
from ..languages import LANGUAGE_NAMES
from .variants import Variant

_EXCHANGE_STEPS = (
    ("correct", "Step ①: the corrected text", CORRECT_SYSTEM),
    ("explain", "Step ②: the explanations", EXPLAIN_SYSTEM),
    ("unclear", "Step ③: unclear passages", UNCLEAR_SYSTEM),
)


def prompt_text(mode: AnswerMode, language: str, *, compact_json: bool) -> str:
    """Return the instructions of *mode* for the language called *language* (``German``)."""
    if mode.asks_exchange:
        steps = _EXCHANGE_STEPS[:1] if mode is AnswerMode.CORRECT_ONLY else _EXCHANGE_STEPS
        parts = [f"prompt version {PROMPT_VERSION}"]
        parts.extend(f"── {title} ──\n{system}" for _, title, system in steps)
        parts.append(
            f"── Sent with the text ({language}) ──\n{parameters(language, level=DEFAULT_LEVEL)}"
        )
        return "\n\n".join(parts)
    policy, fixed = instruction_parts(
        "",
        language,
        level=DEFAULT_LEVEL,
        text_format=PLAIN,
        mode=mode.value,
        answer_format="",
        compact_json=compact_json,
    )
    return f"{policy}\n\n{fixed}"


def prompts_of(built: Sequence[Variant], codes: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Return the instructions of every variant, keyed by the 8 characters the facts carry.

    Each entry holds ``text`` and ``exact`` (whether today's code builds the very instructions
    the run was asked with).  A variant whose mode is not one the tool builds is left out.
    """
    languages = [code for code in codes if code in LANGUAGE_NAMES]
    if not languages:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for variant in built:
        key = variant.facts.get("prompt8", "")
        if not key or key in found or "," in key:
            continue
        try:
            mode = AnswerMode(variant.facts["mode"])
            compact = variant.facts.get("profile") == "compact"
            name = sorted(LANGUAGE_NAMES[code] for code in languages)[0]
            text = prompt_text(mode, name, compact_json=compact)
            built_hash = protocol.instructions_sha256(languages, mode=mode, compact_json=compact)
        except ValueError, KeyError:
            continue
        found[key] = {"text": text, "exact": built_hash.startswith(key)}
    return found


__all__ = ["prompt_text", "prompts_of"]
