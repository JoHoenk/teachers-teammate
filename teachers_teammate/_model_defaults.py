"""Default LLM model names per provider and pipeline task.

This module is intentionally free of infrastructure dependencies so that
:mod:`teachers_teammate.config` can resolve model defaults without importing
from the infrastructure layer.  The Ollama defaults come from the domain model
catalog (``DEFAULT_WRITING_MODEL``) so there is a single source of truth for the model
name.  There is no OCR task: text recognition runs only on engines the user controls.
"""

from __future__ import annotations

from .domain.data_location import PROVIDER_OLLAMA
from .domain.model_catalog import DEFAULT_WRITING_MODEL
from .domain.stages import Stage

_OLLAMA_GENERAL_DEFAULT = DEFAULT_WRITING_MODEL

#: The online correction defaults must answer the review JSON schema.  All of
#: them support structured output; ``claude-3-haiku-20240307`` was retired by the provider
#: and is replaced by its current small model.
_DEFAULT_MODELS: dict[Stage, dict[str, str]] = {
    Stage.CORRECTION: {
        PROVIDER_OLLAMA: _OLLAMA_GENERAL_DEFAULT,
        "openai": "gpt-4o-mini",
        "anthropic": "claude-haiku-4-5",
        "google": "gemini-2.0-flash",
        "mistral": "mistral-small-latest",
        "mistral_eu": "mistral-small-latest",
        "cohere": "command-r-plus",
    },
    Stage.EVALUATION: {
        PROVIDER_OLLAMA: _OLLAMA_GENERAL_DEFAULT,
        "openai": "gpt-4o-mini",
        "anthropic": "claude-haiku-4-5",
        "google": "gemini-2.0-flash",
        "mistral": "mistral-small-latest",
        "mistral_eu": "mistral-small-latest",
        "cohere": "command-r-plus",
    },
}


def default_model_for_task(provider: str, task: Stage | str) -> str:
    """Return the default model name for *provider* and pipeline *task*."""
    stage = Stage._value2member_map_.get(task)
    task_defaults = _DEFAULT_MODELS.get(stage, _DEFAULT_MODELS[Stage.CORRECTION])
    return task_defaults.get(provider, "gpt-4o-mini")
