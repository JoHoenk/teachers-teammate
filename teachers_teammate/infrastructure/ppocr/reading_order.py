"""Reading order: put the boxes of a page into rows, top to bottom, left to right."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .detect import RotatedBox


def reading_order(boxes: Sequence[RotatedBox]) -> list[list[RotatedBox]]:
    """Return the rows of *boxes* in reading order.

    Sort by centre y; a box joins the current row when its centre y is within half the median
    box height of the row's mean centre y; each row is sorted by its left edge.  One column
    only: two columns are read as rows across both.
    """
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: b.centre[1])
    tolerance = float(np.median([b.bounds[3] - b.bounds[1] for b in ordered])) / 2
    rows: list[list[RotatedBox]] = [[ordered[0]]]
    for box in ordered[1:]:
        mean_y = float(np.mean([b.centre[1] for b in rows[-1]]))
        if abs(box.centre[1] - mean_y) <= tolerance:
            rows[-1].append(box)
        else:
            rows.append([box])
    return [sorted(row, key=lambda b: b.bounds[0]) for row in rows]
