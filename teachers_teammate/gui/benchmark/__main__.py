"""Entry point: ``python -m teachers_teammate.gui.benchmark``."""

from ...texts.messages import install

# First: a start-up that fails below is read by a teacher, as a sentence.
install()

from ...application.composition import inject_addon_packages_dir  # noqa: E402

# Prepend the addon packages dir to sys.path before any optional imports.
# Routed through the application service (the GUI's gateway) rather than
# reaching into infrastructure directly.
inject_addon_packages_dir()

from ._benchmark_window import main_benchmark  # noqa: E402
from .composition import build_benchmark_window  # noqa: E402

main_benchmark(window_factory=build_benchmark_window)
