"""The layer above the report: what leaves the machine, and the list that lets a reader find it.

Two things are published, and both are derived from the store and nothing else:

* the **redacted export** (:mod:`.export`) — a local run becomes a committed run file whose texts
  are cut, with the numbers from :func:`~..report.score.score`;
* the **index** (:mod:`.index`) — one row per run, computed from the run file itself, so the list
  cannot say more than the files do.

This layer sits above :mod:`..report` because both need scores, and :mod:`..store` must not import
the report.  Nothing here reads a corpus except to ask whether an item is restricted.
"""

from __future__ import annotations

from .export import (
    ACTIONS,
    REFUSED,
    REPLACE,
    SKIPPED,
    STALE,
    UNCHANGED,
    WRITE,
    Plan,
    PlanEntry,
    export,
    plan,
    redactor_of,
)
from .index import INDEX_NAME, RunSummary, index_markdown, summaries, summarise_run, write_index

__all__ = [
    "ACTIONS",
    "INDEX_NAME",
    "REFUSED",
    "REPLACE",
    "SKIPPED",
    "STALE",
    "UNCHANGED",
    "WRITE",
    "Plan",
    "PlanEntry",
    "RunSummary",
    "export",
    "index_markdown",
    "plan",
    "redactor_of",
    "summaries",
    "summarise_run",
    "write_index",
]
