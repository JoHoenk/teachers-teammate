"""Asking a model: the requests, the protocol they are made under, and the run file they fill.

The layer above `store`: it sends texts to a server, measures what they cost, and writes the
answers through the store's writer.  It never scores, groups or compares — an answer line holds
what the model said and what it cost, and nothing derived — so a scorer change needs no new
run and a run cannot disagree with the report about the same answer.

Everything that reaches a server, a card or the clock is a seam (:class:`.runner.Seams`), so the
loop can be driven without any of them.
"""

from __future__ import annotations

__all__: list[str] = []
