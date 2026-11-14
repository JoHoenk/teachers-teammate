"""The exchanges the app can run, and the registry a profile picks one from.

A new exchange is one module in this package plus one entry in :data:`EXCHANGES`; nothing above
this layer changes, because every exchange returns the same
:class:`~.base.ExchangeResult`.
"""

from __future__ import annotations

from .base import (
    EXCHANGES,
    Exchange,
    ExchangeCost,
    ExchangeFactory,
    ExchangeResult,
    ProofreadContext,
    build_exchange,
)
from .fallback import FallbackExchange
from .t import TExchange

EXCHANGES["T"] = TExchange

__all__ = [
    "EXCHANGES",
    "Exchange",
    "ExchangeCost",
    "ExchangeFactory",
    "ExchangeResult",
    "FallbackExchange",
    "ProofreadContext",
    "TExchange",
    "build_exchange",
]
