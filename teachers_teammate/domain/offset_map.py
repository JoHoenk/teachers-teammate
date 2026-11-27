"""Where the characters of a text end up after some of its runs were replaced.

A text is rarely the same length after a literal replacement: putting a name back in place of a
placeholder (``[PERSON_1]`` → ``Anna Müller``) makes it longer, and every offset after that place
moves.  An :class:`OffsetMap` records the replaced runs, so a position of the old text can be
carried over to the new one without searching for it again.

Pure: no I/O, no provider imports.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from functools import cached_property


@dataclass(frozen=True)
class OffsetMap:
    """Positions of a text before and after literal replacements of some of its runs.

    ``runs`` holds ``(old_start, old_end, new_start, new_end)`` per replaced run, sorted by
    ``old_start`` and disjoint.  Outside the runs a position moves by the length difference of
    the runs before it; inside a replaced run it has no single counterpart, so
    :meth:`start` and :meth:`end` widen it to the replacement's own bounds.
    """

    runs: tuple[tuple[int, int, int, int], ...] = ()

    def start(self, offset: int) -> int:
        """Return where *offset* begins in the new text.

        A position inside a replaced run becomes the start of its replacement: the words that
        stood there are gone, and what stands there now begins at that point.
        """
        run = self._run_at(offset)
        if run is not None:
            return run[2]
        return offset + self._shift(offset)

    def end(self, offset: int) -> int:
        """Return where *offset* ends in the new text.

        A position inside a replaced run becomes the end of its replacement, so a span that
        reaches into a replaced run covers the whole replacement.  A span that ends exactly where
        a run begins stops before the replacement: the run is not part of it.
        """
        run = self._run_at(offset)
        if run is not None and offset > run[0]:
            return run[3]
        return offset + self._shift(offset)

    def back(self, offset: int) -> int:
        """Return where *offset* of the new text stands in the old one.

        A position inside a replacement becomes the start of the run it replaced: the words that
        stand there now are not the ones that were there.  The inverse of :meth:`start`.
        """
        index = bisect_right(self._new_ends, offset)
        if index < len(self.runs):
            old_start, _old_end, new_start, new_end = self.runs[index]
            if new_start <= offset < new_end:
                return old_start
        return offset - self._shifts[index]

    @cached_property
    def _old_starts(self) -> tuple[int, ...]:
        """Where each run begins in the old text (sorted, for the bisect)."""
        return tuple(run[0] for run in self.runs)

    @cached_property
    def _old_ends(self) -> tuple[int, ...]:
        """Where each run ends in the old text (sorted, for the bisect)."""
        return tuple(run[1] for run in self.runs)

    @cached_property
    def _new_ends(self) -> tuple[int, ...]:
        """Where each run's replacement ends in the new text (sorted, for the bisect)."""
        return tuple(run[3] for run in self.runs)

    @cached_property
    def _shifts(self) -> tuple[int, ...]:
        """The shift after the first *n* runs, for *n* = 0 … ``len(runs)``."""
        shifts = [0]
        for old_start, old_end, new_start, new_end in self.runs:
            shifts.append(shifts[-1] + (new_end - new_start) - (old_end - old_start))
        return tuple(shifts)

    def _run_at(self, offset: int) -> tuple[int, int, int, int] | None:
        """Return the replaced run that holds *offset*, or ``None``.

        The runs are half-open, so a position at a run's end is already past it.
        """
        index = bisect_right(self._old_starts, offset) - 1
        if index < 0:
            return None
        run = self.runs[index]
        return run if offset < run[1] else None

    def _shift(self, offset: int) -> int:
        """Return how far the new text runs ahead of the old one at *offset*."""
        return self._shifts[bisect_right(self._old_ends, offset)]
