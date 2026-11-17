"""Comparing and migrating saved prompt texts.

Pure string handling: no I/O.  The proofreading prompts are fixed and versioned
(``correction_prompts.py``); the one prompt a teacher can still edit is the content review's,
and these two helpers decide whether a saved text is a copy of the built-in one.
"""

from __future__ import annotations


def normalize_prompt_text(text: str) -> str:
    """Return *text* with all runs of whitespace collapsed, for prompt comparison."""
    return " ".join(text.split())


def migrate_saved_default(prompt: str, default: str) -> str:
    """Return ``""`` when a saved *prompt* is a copy of *default*, else *prompt*.

    For prompts without per-language presets (content review): ``""`` already means
    "use the built-in default", so a saved copy of it means the same.
    """
    if prompt.strip() and normalize_prompt_text(prompt) == normalize_prompt_text(default):
        return ""
    return prompt
