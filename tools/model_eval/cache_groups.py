"""The groups a local result folder holds, so gate 2 can read one like a committed run.

A run writes its rows into a folder while it goes, next to a header file that says what it was
asked; :func:`~.viewer.load.read_cache` reads both and scores every row again.  This module turns
those runs into the groups :mod:`.gate_two` judges, with the same key and the same refusal as
:func:`~.grouping.groups` makes of committed files: two runs of one key with the same repeat number
are not one group, and a folder that pools two request profiles is refused rather than guessed.

The last session header of a file decides what the run was asked, as it does for the report: a
resumed file has several, and the rows that are there now are the last session's.  A file whose
sessions disagree never reaches here, because ``read_cache`` skips it with a reason.
"""

from __future__ import annotations

from collections.abc import Iterable

from . import grouping
from .viewer import load


def cache_groups(caches: Iterable[load.Cache]) -> list[grouping.RunGroup]:
    """Return the groups the runs of *caches* make, each with its repeats in order.

    Args:
        caches: The folders, already read against one test set.

    Returns:
        One group per key, sorted as :meth:`~.grouping.GroupKey.order` sorts them.

    Raises:
        ValueError: if two runs of one group carry the same repeat number.
    """
    grouped: dict[grouping.GroupKey, list[load.CacheRun]] = {}
    for cache in caches:
        for run in cache.runs:
            grouped.setdefault(grouping.GroupKey.of_local(run.header), []).append(run)
    built: list[grouping.RunGroup] = []
    for key in sorted(grouped, key=grouping.GroupKey.order):
        ordered = sorted(grouped[key], key=lambda run: run.repeat)
        repeats = [run.repeat for run in ordered]
        if len(set(repeats)) != len(repeats):
            raise ValueError(
                f"{key.model}: two runs of repeat {sorted(repeats)} are in one group; "
                "a repeat is one run"
            )
        built.append(
            grouping.RunGroup(
                key=key,
                runs=tuple(ordered),
                items=tuple(tuple(run.rows) for run in ordered),
            )
        )
    return built


__all__ = ["cache_groups"]
