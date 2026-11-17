"""View preferences that outlive a document: how large the Check tab's text is read.

The teacher zooms the text with Ctrl+wheel; the size is one integer in the window's settings
store, so it holds across documents and across restarts.  This module is the one place that
reads and writes it, so the wheel, the stored value and the default cannot disagree.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

TEXT_POINT_SIZE_KEY = "view/text_point_size"
"""The point size the Check tab's texts are read at."""

TEXT_POINT_SIZE_DEFAULT = 12
"""12 pt: a proportional text at the size of the rest of the window."""

TEXT_POINT_SIZE_MIN = 8
TEXT_POINT_SIZE_MAX = 24


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
