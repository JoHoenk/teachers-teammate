"""Defaults both writing-model CLIs share, so they cannot drift apart."""

from __future__ import annotations

from dataclasses import replace
import os
from typing import TYPE_CHECKING

from teachers_teammate.domain.model_catalog import request_profile

if TYPE_CHECKING:
    from teachers_teammate.domain.model_catalog import RequestProfile

COMPACT_JSON_CHOICES: tuple[str, ...] = ("catalog", "on", "off")
"""What ``--compact-json`` accepts: the catalog's choice per model, or one value for all."""


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


def request_profile_for(
    model: str, *, compact_json: str = "catalog", temperature: float | None = None
) -> RequestProfile:
    """Return the profile *model* is asked with, as the app would ask it.

    The catalog's profile is the base: its compact-JSON choice and the temperature the model's
    maker recommends (a model without one gets the app's 0.2).  ``--compact-json on``/``off``
    overrides the rule for every model, and an explicit ``--temperature`` overrides the
    temperature.

    Args:
        model: The tag the request goes to.
        compact_json: ``catalog`` (the model's own choice), ``on`` or ``off``.
        temperature: The temperature to send, or ``None`` for the model's own.
    """
    profile = request_profile(model)
    if compact_json == "on":
        profile = replace(profile, compact_json=True)
    elif compact_json == "off":
        profile = replace(profile, compact_json=False)
    if temperature is not None:
        # ``--temperature`` is the temperature of step ① (the one that rewrites the text); the
        # JSON-answering steps ② and ③ keep their own.
        profile = replace(profile, correct=replace(profile.correct, temperature=temperature))
    return profile


def temperature_for(model: str, override: float | None = None) -> float:
    """Return the temperature the tools send *model*: *override* if given, else its own.

    The one place a command line's ``--temperature`` is resolved, so every tool measures the
    temperature the app sends unless told otherwise.
    """
    return request_profile_for(model, temperature=override).temperature
