"""View preferences that outlive a document: the Check tab's text size and its pane sizes.

The teacher zooms the text with Ctrl+wheel and drags the panes of the Check tab; both are kept in
the window's settings store, so they hold across documents and across restarts.  This module is
the one place that reads and writes them, so the wheel, the drag, the stored value and the default
cannot disagree.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

TEXT_POINT_SIZE_KEY = "view/text_point_size"
"""The point size the Check tab's texts are read at."""

TEXT_POINT_SIZE_DEFAULT = 11
"""A proportional text a little smaller than the rest of the window."""

TEXT_POINT_SIZE_MIN = 8
TEXT_POINT_SIZE_MAX = 24

CHECK_SPLIT_KEY = "view/check_split"
"""The widths of the Check tab's three panes: the scan, the text and the side panel."""

CHECK_CARD_SPLIT_KEY = "view/check_card_split"
"""The heights of the Check tab's card and the list below it."""


def text_point_size(settings_factory: Callable[[], Any]) -> int:
    """Return the stored point size, clamped to what the views can show."""
    raw = str(settings_factory().value(TEXT_POINT_SIZE_KEY, "") or "").strip()
    try:
        size = int(raw)
    except ValueError:
        return TEXT_POINT_SIZE_DEFAULT
    return min(max(size, TEXT_POINT_SIZE_MIN), TEXT_POINT_SIZE_MAX)


def set_text_point_size(settings_factory: Callable[[], Any], point_size: int) -> None:
    """Remember the point size the teacher zoomed to."""
    size = min(max(int(point_size), TEXT_POINT_SIZE_MIN), TEXT_POINT_SIZE_MAX)
    settings_factory().setValue(TEXT_POINT_SIZE_KEY, str(size))


def check_split(settings_factory: Callable[[], Any]) -> tuple[int, ...]:
    """Return the stored widths of the Check tab's panes, or ``()`` for the default ones."""
    return _sizes(settings_factory().value(CHECK_SPLIT_KEY, ""), 3)


def set_check_split(settings_factory: Callable[[], Any], sizes: Sequence[int]) -> None:
    """Remember the widths the teacher dragged the Check tab's panes to."""
    if len(sizes) == 3:
        settings_factory().setValue(CHECK_SPLIT_KEY, _sizes_text(sizes))


def check_card_split(settings_factory: Callable[[], Any]) -> tuple[int, ...]:
    """Return the stored heights of the Check tab's card and list, or ``()`` for the default."""
    return _sizes(settings_factory().value(CHECK_CARD_SPLIT_KEY, ""), 2)


def set_check_card_split(settings_factory: Callable[[], Any], sizes: Sequence[int]) -> None:
    """Remember the heights the teacher dragged the card and the list to."""
    if len(sizes) == 2:
        settings_factory().setValue(CHECK_CARD_SPLIT_KEY, _sizes_text(sizes))


def _sizes(raw: object, count: int) -> tuple[int, ...]:
    """Return *count* positive sizes read from *raw*, or ``()`` when it does not hold them."""
    parts = str(raw or "").split(",")
    if len(parts) != count:
        return ()
    try:
        sizes = tuple(int(part) for part in parts)
    except ValueError:
        # A stored value that holds no numbers: the callers use the default sizes.
        return ()
    return sizes if all(size > 0 for size in sizes) else ()


def _sizes_text(sizes: Sequence[int]) -> str:
    """Return *sizes* as the text the settings store keeps."""
    return ",".join(str(int(size)) for size in sizes)
