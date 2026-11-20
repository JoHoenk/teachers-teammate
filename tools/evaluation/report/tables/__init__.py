"""The tables a report prints: what a model found, what it cost, and what it would gain.

Each module is one question and one markdown table, and each is pure: scored items in, text out.
They sit above :mod:`..summary` and :mod:`..groups`, which compute the numbers, and below the
command lines, which only choose what to print.  Nothing here reads a file or sends a request.
"""

from __future__ import annotations

__all__: list[str] = []
