"""The facts of this computer that a cache key is made of.

The machine reads its own model folder, asks the operating system for its language and hashes the
proofreading exchange's prompts, so :mod:`teachers_teammate.cache_keys` stays a pure function.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.request_profile import RequestProfile
from .onnx.store import ModelStore
from .preprocessing.identity import available_page_steps
from .proofreading.corrector import exchange_identity
from .system_locale import system_language


@dataclass(frozen=True)
class MachineKeyFacts:
    """This computer: its model folder and its system language."""

    model_store: ModelStore
    """Where the page models are."""

    def page_steps(self) -> tuple[str, ...]:
        return available_page_steps(self.model_store)

    def system_language(self) -> str:
        # An unsupported locale is as good as unknown: the automatic extra model is then left
        # out, and the key says so.
        return system_language() or ""

    def exchange_identity(self, profile: RequestProfile, *, corrections_only: bool) -> str:
        return exchange_identity(profile, corrections_only=corrections_only)
