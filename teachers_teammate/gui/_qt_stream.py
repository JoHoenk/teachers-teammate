"""Bridge ``print()`` output from a QThread to a Qt signal.

Shared by the OCR worker (:mod:`teachers_teammate.gui._worker`) and the benchmark
worker so both can redirect ``stdout``/``stderr`` into a log widget.
"""

from __future__ import annotations

import io
from typing import Any


class QtStream(io.TextIOBase):
    """Forwards ``write()`` calls to a Qt signal — bridges ``print()`` in a QThread."""

    def __init__(self, signal: Any) -> None:
        super().__init__()
        self._signal = signal

    def write(self, text: str) -> int:
        if text:
            self._signal.emit(text)
        return len(text)

    def flush(self) -> None:
        pass
