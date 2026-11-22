"""Teacher's Teammate — Benchmark GUI sub-package.

Entry point::

    python -m teachers_teammate.gui.benchmark

Lets the user run a document through OCR configurations, keep a timestamped
history of runs, and compare two of them side-by-side (image · text A · text B ·
diff + similarity).
"""

from __future__ import annotations

from ...texts import app as app_text

try:
    from ._benchmark_window import BenchmarkWindow, main_benchmark
except ImportError as _exc:  # pragma: no cover - mirrors gui/__init__ guard
    raise ImportError(app_text.MISSING_PYSIDE_BENCHMARK) from _exc

__all__ = ["BenchmarkWindow", "main_benchmark"]
