"""How a failure that happened off the GUI thread is said to a teacher."""

from __future__ import annotations

import sys

from ..texts import privacy


def spacy_problem(exc: BaseException) -> str | None:
    """Explain a missing spaCy install / model (``None`` for any other error)."""
    if isinstance(exc, ImportError):
        return privacy.spacy_missing(str(exc), installed_app=bool(getattr(sys, "frozen", False)))
    if isinstance(exc, OSError):
        return privacy.spacy_model_missing(str(exc))
    return None
