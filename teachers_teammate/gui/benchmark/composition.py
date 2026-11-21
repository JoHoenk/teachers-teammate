"""Composition helpers for assembling the benchmark window dependencies."""

from __future__ import annotations

from ...application.benchmark_service import BenchmarkApplicationService
from ...application.composition import build_app_services
from ...application.service import ProcessingApplicationService
from ._benchmark_window import BenchmarkWindow


def build_benchmark_window(
    *,
    processing: ProcessingApplicationService | None = None,
    app_service: BenchmarkApplicationService | None = None,
    worker_factory=None,
) -> BenchmarkWindow:
    """Create the benchmark window.

    The main window passes its own *processing* service, so both share one set of caches; the
    stand-alone entry point has none and builds the process's services here.
    """
    service = app_service or BenchmarkApplicationService(
        processing_service=processing or build_app_services().processing
    )
    return BenchmarkWindow(app_service=service, worker_factory=worker_factory)


__all__ = ["build_benchmark_window"]
