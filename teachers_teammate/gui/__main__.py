"""Entry point: ``python -m teachers_teammate.gui``."""

from ..application.composition import inject_addon_packages_dir

# Prepend the addon packages dir to sys.path before any optional imports.
# Routed through the application service (the GUI's gateway) rather than
# reaching into infrastructure directly.
inject_addon_packages_dir()

from ._main_window import main_gui  # noqa: E402
from .composition import build_main_window  # noqa: E402

main_gui(window_factory=build_main_window)
