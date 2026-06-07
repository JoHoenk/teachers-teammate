"""Entry point: ``python -m teachers_teammate.gui.benchmark``."""

from ...application.service import ProcessingApplicationService

# Prepend the addon packages dir to sys.path before any optional imports.
# Routed through the application service (the GUI's gateway) rather than
# reaching into infrastructure directly.
ProcessingApplicationService().inject_addon_packages_dir()

from ._benchmark_window import build_benchmark_window, main_benchmark  # noqa: E402

main_benchmark(window_factory=build_benchmark_window)
