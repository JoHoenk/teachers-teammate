"""The exchanges the app can run, and the registry a profile picks one from.

A new exchange is one module in this package plus one ``register_exchange`` call; nothing above
this layer changes, because every exchange returns the same
:class:`~.base.ExchangeResult`.
"""

from __future__ import annotations

from ....domain.proofreading.result import ExchangeKind
from .base import (
    EXCHANGES,
    Exchange,
    ExchangeCost,
    ExchangeFactory,
    ExchangeResult,
    ProofreadContext,
    build_exchange,
    register_exchange,
)
from .t import TExchange

register_exchange(ExchangeKind.TEXT_FIRST, TExchange)

__all__ = [
    "EXCHANGES",
    "Exchange",
    "ExchangeCost",
    "ExchangeFactory",
    "ExchangeResult",
    "ProofreadContext",
    "TExchange",
    "build_exchange",
]
