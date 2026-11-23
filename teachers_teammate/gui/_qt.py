"""The values Qt hands back as ``None`` although they must exist.

A menu bar, a menu entry and a pixmap item are made a line earlier, so "missing" is a
programming error, not a case to handle.  :func:`present` states that once: an ``assert``
says the same but disappears under ``python -O``.
"""

from __future__ import annotations


def present[T](value: T | None, what: str) -> T:
    """Return *value*, or raise when Qt returned nothing where something must exist."""
    if value is None:
        raise RuntimeError(f"Qt returned no {what}")
    return value
