"""Proofreading options the GUI shows, answered without the GUI importing infrastructure."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..config import DEFAULTS
from ..domain.prompt_text import migrate_saved_default

#: What each step of the proofreading exchange is called where a teacher reads it.
PROMPT_STEP_TITLES: dict[str, str] = {
    "correct": "Correct the text",
    "explain": "Explain the corrections",
    "unclear": "List unclear expressions",
}


def prompt_step_title(step: str) -> str:
    """Return the teacher-facing name of a proofreading step."""
    return PROMPT_STEP_TITLES.get(step, step)


def review_has_own_model(
    values: Mapping[str, object], effective_model: Callable[[str, str, str], str]
) -> bool:
    """Return whether the content review uses another service or model than proofreading.

    Both sides are compared as a run would use them (*effective_model*: provider, model, task;
    an empty model means the task's default), so "no model chosen" on one side and the default
    on the other are the same model.  The Settings page ticks its override from this, and the
    Setup Assistant leaves a review that has its own model alone.
    """

    def effective(provider_key: str, model_key: str, task: str) -> tuple[str, str]:
        provider = str(values.get(provider_key) or DEFAULTS[provider_key])
        return provider, effective_model(provider, str(values.get(model_key) or ""), task)

    return effective("evaluate_provider", "evaluate_model", "evaluation") != effective(
        "correction_provider", "correction_model", "correction"
    )


def migrate_saved_evaluation_prompt(prompt: str) -> str:
    """Reset a saved default copy to ``""`` ("use the built-in prompt"), keep custom text.

    The evaluation prompt has no per-language presets; "" resolves to the built-in
    default, so a saved copy of it means the same.
    """
    return migrate_saved_default(prompt, DEFAULTS["evaluate_prompt"])
