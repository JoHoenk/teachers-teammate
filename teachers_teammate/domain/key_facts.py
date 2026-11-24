"""What a cache key needs to know about the machine it is computed on.

A key describes a stored result, and part of that description is the computer: which page models
are downloaded, which language its system is set to, and what the proofreading exchange's
requests hash to.  The port is here, in ``domain``; the machine that answers it is an adapter.
"""

from __future__ import annotations

from typing import Protocol

from .model_catalog import RequestProfile


class KeyFacts(Protocol):
    """The machine's own facts a cache key is made of.

    Handed to :mod:`teachers_teammate.cache_keys`, so a key is a pure function of the settings
    and these three answers: computed on another computer, or in a test, it is the same as long
    as the facts are.
    """

    def page_steps(self) -> tuple[str, ...]:
        """Return the page models the automatic preparation can run: ``"turn"``, ``"flatten"``."""
        ...

    def system_language(self) -> str:
        """Return the language this computer is set to, for the automatic extra model."""
        ...

    def exchange_identity(self, profile: RequestProfile, *, corrections_only: bool) -> str:
        """Return what names the proofreading exchange's requests for *profile* in a key."""
        ...
