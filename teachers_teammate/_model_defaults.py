"""Default LLM model names per provider and pipeline task.

This module is intentionally free of infrastructure dependencies so that
:mod:`teachers_teammate.config` can resolve model defaults without importing
from the infrastructure layer.  The Ollama defaults come from the domain model
catalog (``DEFAULT_WRITING_MODEL``) so there is a single source of truth for the model
name.  There is no OCR task: text recognition runs only on engines the user controls.
"""

from __future__ import annotations

from .domain.model_catalog import DEFAULT_WRITING_MODEL

_OLLAMA_GENERAL_DEFAULT = DEFAULT_WRITING_MODEL

_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "correction": {
        "ollama": _OLLAMA_GENERAL_DEFAULT,
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-haiku-20240307",
        "google": "gemini-2.0-flash",
        "mistral": "mistral-small-latest",
        "cohere": "command-r-plus",
    },
    "evaluation": {
        "ollama": _OLLAMA_GENERAL_DEFAULT,
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-haiku-20240307",
        "google": "gemini-2.0-flash",
        "mistral": "mistral-small-latest",
        "cohere": "command-r-plus",
    },
}


def default_model_for_task(provider: str, task: str) -> str:
    """Return the default model name for *provider* and pipeline *task*."""
    task_defaults = _DEFAULT_MODELS.get(task, _DEFAULT_MODELS["correction"])
    return task_defaults.get(provider, "gpt-4o-mini")
