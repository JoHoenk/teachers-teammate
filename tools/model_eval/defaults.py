"""Defaults both writing-model CLIs share, so they cannot drift apart."""

from __future__ import annotations

import os


def default_ollama_url() -> str:
    """Return the server to talk to when ``--ollama-url`` is not given.

    ``$OLLAMA_HOST`` may be a bind address (``0.0.0.0``) rather than an address to connect to,
    so it is normalised exactly as the app normalises it.  Both tools used to treat the variable
    differently; they now share this function.
    """
    from teachers_teammate.infrastructure.ollama_utils import (  # noqa: PLC0415
        normalize_ollama_url,
    )

    return normalize_ollama_url(os.environ.get("OLLAMA_HOST", ""))


def default_temperature() -> float:
    """Return the app's proofreading temperature, so a check measures what a teacher gets."""
    from teachers_teammate.config import DEFAULTS  # noqa: PLC0415

    return float(DEFAULTS["correction_temperature"])
