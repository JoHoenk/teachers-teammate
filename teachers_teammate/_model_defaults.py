"""Default LLM model names per provider and pipeline task.

This module is intentionally free of infrastructure dependencies so that
:mod:`teachers_teammate.config` can resolve model defaults without importing
from the infrastructure layer.
"""

from __future__ import annotations

_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "ocr": {
        "ollama": "gpt-oss:20b",
        "openai": "gpt-4o",
        "anthropic": "claude-opus-4-5",
        "google": "gemini-2.0-flash",
        "mistral": "pixtral-large-latest",
    },
    "correction": {
        "ollama": "gpt-oss:20b",
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-haiku-20240307",
        "google": "gemini-2.0-flash",
        "mistral": "mistral-small-latest",
        "cohere": "command-r-plus",
    },
    "evaluation": {
        "ollama": "gpt-oss:20b",
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
