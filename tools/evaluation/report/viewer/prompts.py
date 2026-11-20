"""The instructions a variant's requests carried, as text a reader can read.

A run's header keeps only the hash of its instructions, one per language
(``instructions_sha256``).  The page shows the instructions themselves, built from the code that
builds the requests (the same functions the hash is made from), and says whether they are
*exactly* what the run sent: the hash of what is built now is compared with the run's own.  When it
differs, the text is today's, and the page says so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from teachers_teammate.domain.correction_policy import DEFAULT_LEVEL
from teachers_teammate.domain.correction_prompts import (
    EXPLAIN_SYSTEM,
    PROMPT_VERSION,
    UNCLEAR_SYSTEM,
    correct_system,
    parameters,
)
from teachers_teammate.domain.formatted_text import PLAIN

from ...core.languages import LANGUAGE_NAMES
from ...core.modes import ONE_REQUEST_OUTPUT, Exchange, Step
from .variants import Variant

STANDARD_PROMPT = "standard"
"""The step-① prompt a run records when it asked the standard one, which is asked as ``""``."""


def _exchange_steps(variant: str) -> tuple[tuple[Step, str, str], ...]:
    """Return the step, its title and its system prompt; step ① is *variant*'s."""
    return (
        (Step.CORRECT, "Step ①: the corrected text", correct_system(variant)),
        (Step.EXPLAIN, "Step ②: the explanations", EXPLAIN_SYSTEM),
        (Step.UNCLEAR, "Step ③: unclear passages", UNCLEAR_SYSTEM),
    )


def prompt_text(
    exchange: Exchange,
    steps: tuple[Step, ...],
    language: str,
    *,
    compact_json: bool,
    prompt: str = "",
) -> str:
    """Return the instructions of *exchange* with *steps* for the language called *language*.

    *language* is the language's name (``German``); *prompt* is the step-① prompt variant the run
    was asked with (``""``: the standard prompt).
    """
    if exchange is Exchange.TEXT_FIRST:
        shown = [item for item in _exchange_steps(prompt) if item[0] in steps]
        parts = [f"prompt version {PROMPT_VERSION}"]
        parts.extend(f"── {title} ──\n{system}" for _, title, system in shown)
        parts.append(
            f"── Sent with the text ({language}) ──\n{parameters(language, level=DEFAULT_LEVEL)}"
        )
        return "\n\n".join(parts)
    # The request builder pulls in the app's answer schema; a report that shows no instructions
    # does not need it, so it is imported where it is used.
    from ...run.one_request.correction import instruction_parts  # noqa: PLC0415

    policy, fixed = instruction_parts(
        "",
        language,
        level=DEFAULT_LEVEL,
        text_format=PLAIN,
        mode=ONE_REQUEST_OUTPUT[exchange],
        answer_format="",
        compact_json=compact_json,
    )
    return f"{policy}\n\n{fixed}"


def prompts_of(
    built: Sequence[Variant],
    codes: Sequence[str],
    hashes: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, Any]]:
    """Return the instructions of every variant, keyed by the 8 characters the facts carry.

    Each entry holds ``text`` and ``exact`` (whether today's code builds the very instructions the
    run was asked with).  A variant whose exchange is not one the tool builds is left out.

    Args:
        built: The variants of the report.
        codes: The language codes of the report, of which only the ones with a name are shown.
        hashes: The per-language instruction hashes each run recorded, by variant id.
    """
    languages = [code for code in codes if code in LANGUAGE_NAMES]
    if not languages:
        return {}
    name = sorted(LANGUAGE_NAMES[code] for code in languages)[0]
    found: dict[str, dict[str, Any]] = {}
    for variant in built:
        key = variant.facts.get("prompt8", "")
        if not key or key in found or "," in key:
            continue
        try:
            exchange = Exchange(variant.key.exchange)
            steps = tuple(Step(step) for step in variant.key.steps)
            compact = bool(variant.key.get("compact_json"))
            asked = _asked(str(variant.key.get("step_one_prompt")))
            text = prompt_text(exchange, steps, name, compact_json=compact, prompt=asked)
        except ValueError, KeyError:
            continue
        found[key] = {
            "text": text,
            "exact": _exact(exchange, steps, compact, asked, hashes.get(variant.id, {})),
        }
    return found


def _asked(step_one_prompt: str) -> str:
    """Return the step-① prompt as the request builder takes it: the standard one is ``""``."""
    return "" if step_one_prompt == STANDARD_PROMPT else step_one_prompt


def _exact(
    exchange: Exchange,
    steps: tuple[Step, ...],
    compact_json: bool,
    asked: str,
    stored: Mapping[str, str],
) -> bool:
    """Return whether today's code builds the very instructions the run was asked with.

    The run recorded one hash per language, so every one of them has to match: a prompt that
    changed in German alone makes the text shown today's, not the run's.
    """
    if not stored:
        return False
    # The capture of the request side imports the app's correctors; see :func:`prompt_text`.
    from ...run import protocol  # noqa: PLC0415

    for code, value in stored.items():
        if code not in LANGUAGE_NAMES:
            return False
        built = protocol.instructions_sha256(
            [code], exchange=exchange, steps=steps, compact_json=compact_json, prompt=asked
        )
        if built != value:
            return False
    return True


__all__ = [
    "STANDARD_PROMPT",
    "prompt_text",
    "prompts_of",
]
