"""Proofreading options the GUI shows, answered without the GUI importing infrastructure."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..config import DEFAULTS, Config
from ..domain.formatted_text import TextFormat
from ..domain.notation import with_notation_hint
from ..domain.prompt_text import migrate_saved_default
from ..domain.stages import Stage


def proofreading_prompts(config: Config) -> tuple[tuple[str, str], ...]:
    """Return ``(step, system prompt)`` for the steps this config sends.

    Taken from the exchange itself, so the *Options…* dialog and *What is sent* show the
    requests a run really makes and cannot drift from them.  *Corrections only* leaves
    step ① alone; a model without the unclear check leaves step ③ out.  The step is a key:
    the adapter names it, through :func:`teachers_teammate.texts.review.prompt_step_title`.
    """
    from ..infrastructure.proofreading.corrector import exchange_prompts  # noqa: PLC0415

    return exchange_prompts(
        config.correction_profile,
        corrections_only=config.correction_output == "text",
    )


def evaluation_fixed_instructions(text_format: TextFormat = "plain") -> str:
    """Return what the app appends to an evaluation prompt: the notation hint, if any.

    Markdown is the only format with a hint, so for plain text this is ``""``.
    """
    return with_notation_hint("", text_format, task=Stage.EVALUATION).strip()


def review_has_own_model(
    values: Mapping[str, object], effective_model: Callable[[str, str, str], str]
) -> bool:
    """Return whether the content review uses another service or model than proofreading.

    Both sides are compared as a run would use them (*effective_model*: provider, model, task;
    an empty model means the task's default), so "no model chosen" on one side and the default
    on the other are the same model.  The Settings page ticks its override from this, and the
    Setup Assistant leaves a review that has its own model alone.
    """

    def effective(provider_key: str, model_key: str, task: Stage) -> tuple[str, str]:
        provider = str(values.get(provider_key) or DEFAULTS[provider_key])
        return provider, effective_model(provider, str(values.get(model_key) or ""), task)

    return effective("evaluate_provider", "evaluate_model", Stage.EVALUATION) != effective(
        "correction_provider", "correction_model", Stage.CORRECTION
    )


def migrate_saved_evaluation_prompt(prompt: str) -> str:
    """Reset a saved default copy to ``""`` ("use the built-in prompt"), keep custom text.

    The evaluation prompt has no per-language presets; "" resolves to the built-in
    default, so a saved copy of it means the same.
    """
    return migrate_saved_default(prompt, DEFAULTS["evaluate_prompt"])
