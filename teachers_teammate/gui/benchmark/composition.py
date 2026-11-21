"""Composition helpers for assembling the benchmark window dependencies."""

from __future__ import annotations

from ...application.benchmark_service import BenchmarkApplicationService
from ...application.composition import AppServices, build_app_services
from ._benchmark_window import BenchmarkWindow


def build_benchmark_window(
    *,
    app_services: AppServices | None = None,
    app_service: BenchmarkApplicationService | None = None,
    worker_factory=None,
) -> BenchmarkWindow:
    """Create the benchmark window.

    The main window passes its own services, so both share one set of caches; the stand-alone
    entry point has none and builds the process's services here.
    """
    services = app_services or build_app_services()
    service = app_service or BenchmarkApplicationService(app_services=services)
    return BenchmarkWindow(app_service=service, worker_factory=worker_factory)


__all__ = ["build_benchmark_window"]
