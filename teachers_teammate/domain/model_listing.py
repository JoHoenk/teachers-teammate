"""Result of asking an AI service which models it offers.

A provider can answer in three ways, and the settings dialogs say which one they show:
the live list the service returned, the built-in list of a service that cannot be asked, or
the built-in list shown *because* asking failed (no key, key refused, no connection).  A
failed answer must never look like a successful one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ListingSource = Literal["live", "builtin", "failed"]
"""``live``: returned by the service; ``builtin``: the service cannot be asked, by design;
``failed``: asking failed, so the built-in list stands in."""


@dataclass(frozen=True)
class ModelListing:
    """Model names plus where they came from."""

    models: list[str]
    source: ListingSource = "live"
    problem: str = ""
    """Short plain-language reason when *source* is ``failed`` (empty otherwise)."""

    @property
    def cacheable(self) -> bool:
        """Whether the answer may be remembered for a while.

        A failed or empty answer is never remembered: a key entered or a server started a
        moment later must be noticed on the next look.
        """
        return bool(self.models) and self.source != "failed"
