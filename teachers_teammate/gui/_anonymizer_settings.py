"""The anonymizer settings as the panel keeps them: read into a config, written back.

Both directions sit here because they have to agree on what a symbolic value means: ``None``
patterns stand for the built-in defaults, so a later change to them reaches this teacher, while
an empty list means "no regex anonymization" and is kept.  No widgets, no Qt.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from ..domain.anonymization import DEFAULT_PATTERNS, AnonymizerConfig, resolve_patterns
from ..settings_schema import SECONDARY_MODEL_NONE


def build(settings: Mapping[str, Any]) -> AnonymizerConfig:
    """Return the :class:`AnonymizerConfig` the settings keys stand for."""
    return AnonymizerConfig(
        primary_model=settings.get("anonymizer_primary_model") or None,
        # "auto:system" and "none" are meaningful values, not "not set"
        secondary_model=settings.get("anonymizer_secondary_model") or SECONDARY_MODEL_NONE,
        patterns=resolve_patterns(settings.get("anonymizer_patterns")),
    )


def store(settings: MutableMapping[str, Any], config: AnonymizerConfig) -> None:
    """Write *config* back into *settings*."""
    settings["anonymizer_primary_model"] = config.primary_model
    settings["anonymizer_secondary_model"] = config.secondary_model
    # Unchanged defaults stay symbolic (None) so later changes to them reach this user;
    # an empty list is kept, it means "no regex anonymization".
    settings["anonymizer_patterns"] = (
        None if config.patterns == tuple(DEFAULT_PATTERNS) else list(config.patterns)
    )
