"""Result of asking an AI service which models it offers.

A provider can answer in three ways, and the settings dialogs say which one they show:
the live list the service returned, the built-in list of a service that cannot be asked, or
the built-in list shown *because* asking failed (no key, key refused, no connection).  A
failed answer must never look like a successful one.

The names alone are enough to fill a combo box, but not to *order* it: an online service
returns several entries per model (a ``-latest`` alias and its dated versions) and knows
which ones retire soon.  :class:`ListedModel` carries that per-model extra, and
:attr:`ModelListing.details` carries it along with the names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

ListingSource = Literal["live", "builtin", "failed"]
"""``live``: returned by the service; ``builtin``: the service cannot be asked, by design;
``failed``: asking failed, so the built-in list stands in."""

ModelGroup = Literal["current", "pinned", "own", "other"]
"""Where a model belongs in the list: the one entry per model, a dated version of it, a
fine-tuned model of the user's own, or anything else (shown last)."""


@dataclass(frozen=True)
class ListedModel:
    """One model of a curated list: what to offer, and what to say about it."""

    id: str
    """What is stored in the settings and sent to the API."""
    group: ModelGroup = "current"
    label: str = ""
    """Short right-hand label next to the name, e.g. ``"pinned version"``."""
    description: str = ""
    """The service's own one-line description, shown as the entry's tooltip."""
    context_tokens: int | None = None
    retires_on: date | None = None
    """The day the service stops serving this model (``None``: no known end)."""
    replacement: str = ""
    """The model the service suggests instead once this one is gone."""


@dataclass(frozen=True)
class CuratedModels:
    """The models of one service to offer, plus the ones hidden for retiring soon."""

    offered: tuple[ListedModel, ...] = ()
    retiring: tuple[ListedModel, ...] = ()
    """Pulled from the list but still served today, so a saved choice can be warned about."""
    also_served: tuple[str, ...] = ()
    """Names the service still accepts but the list does not show (aliases, older dated
    versions), so a saved choice among them is not called "not offered"."""

    @property
    def ids(self) -> list[str]:
        """Return the model IDs to show, in the order to show them."""
        return [model.id for model in self.offered]


@dataclass(frozen=True)
class ModelListing:
    """Model names plus where they came from."""

    models: list[str]
    source: ListingSource = "live"
    problem: str = ""
    """Short plain-language reason when *source* is ``failed`` (empty otherwise)."""
    details: tuple[ListedModel, ...] = ()
    """Per-model extras (group, label, tooltip) when the service provides them."""
    retiring: tuple[ListedModel, ...] = ()
    """Models hidden from the list because they retire soon, kept for a warning."""
    also_served: tuple[str, ...] = ()
    """Names the service still accepts but the list does not show."""

    @property
    def cacheable(self) -> bool:
        """Whether the answer may be remembered for a while.

        A failed or empty answer is never remembered: a key entered or a server started a
        moment later must be noticed on the next look.
        """
        return bool(self.models) and self.source != "failed"
