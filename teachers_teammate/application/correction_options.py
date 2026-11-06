"""Proofreading options the GUI shows, answered without the GUI importing infrastructure."""

from __future__ import annotations

from ..config import DEFAULTS
from ..domain.correction_prompts import PROMPTS, migrate_saved_default, migrate_saved_prompt


def prompt_ignores_level(prompt: str) -> bool:
    """Whether *prompt* is a custom instruction text that does not use the ``{level}`` setting.

    Empty prompts and preset names resolve to a built-in preset, which always uses the level.
    """
    return bool(prompt.strip()) and prompt not in PROMPTS and "{level}" not in prompt


def migrate_saved_correction_prompt(prompt: str) -> str:
    """Reset a saved preset copy to ``""`` ("use the built-in prompt"), keep custom text.

    Older versions stored the built-in preset text in the settings; on this version such
    a text would be sent as a custom policy next to answer-format rules that say the
    opposite.  Equal (after whitespace normalisation) to any current or released preset
    means: the teacher never customised anything.
    """
    return migrate_saved_prompt(prompt)


def migrate_saved_evaluation_prompt(prompt: str) -> str:
    """Reset a saved default copy to ``""`` ("use the built-in prompt"), keep custom text.

    The evaluation prompt has no per-language presets; "" resolves to the built-in
    default, so a saved copy of it means the same.
    """
    return migrate_saved_default(prompt, DEFAULTS["evaluate_prompt"])
