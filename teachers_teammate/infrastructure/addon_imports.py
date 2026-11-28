"""``sys.path``, and the add-on modules it makes importable."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys

from .addon_folder import (
    foreign_python_packages,
    get_packages_dir,
    outdated_shared_packages,
    packages_dir_readable,
)
from .addon_list import ADDON_CHECK_MODULE, addon_constraints_path

_logger = logging.getLogger(__name__)


def inject_packages_dir() -> None:
    """Prepend the addon packages directory to sys.path (idempotent).

    Safe to call multiple times — inserts at most once. Also registers the
    directory with os.add_dll_directory on Windows so native extension DLL
    dependencies are found by the loader.

    If the packages directory exists but its files are not readable by the
    current user (e.g. installed via 'Run as Administrator'), injection is
    silently skipped to avoid a PermissionError in the import machinery.

    A folder that holds packages of an older app version (:func:`outdated_shared_packages`) or
    packages built for another Python (:func:`foreign_python_packages`) is skipped too: its copy
    of numpy or pydantic would replace the bundled one, and its compiled modules would not
    import.  The app runs on its own bundle until the teacher updates the add-ons.
    """
    pkg_dir = get_packages_dir()
    pkg_str = str(pkg_dir)
    if pkg_str not in sys.path:
        if pkg_dir.exists() and not packages_dir_readable(pkg_dir):
            return
        outdated = outdated_shared_packages(pkg_dir, addon_constraints_path())
        foreign = foreign_python_packages(pkg_dir)
        if outdated or foreign:
            _logger.warning(
                "Add-ons need an update after the app update; not using %s (%s)",
                pkg_dir,
                ", ".join(sorted({*outdated, *foreign})),
            )
            return
        sys.path.insert(0, pkg_str)
    if sys.platform == "win32" and pkg_dir.exists() and hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(pkg_str)
        except OSError as exc:
            _logger.warning(
                "Cannot add %s to the DLL search path, native modules may not load: %s",
                pkg_str,
                exc,
            )
    # Clear the PathFinder cache so importlib.metadata re-scans the addon dir.
    # Required after pip installs packages into the dir via subprocess — the
    # parent process caches None for any path that didn't exist at startup.
    importlib.invalidate_caches()


def is_addon_importable(addon: str) -> bool:
    """Return True if the top-level module for *addon* can be found by the importer."""
    module = ADDON_CHECK_MODULE.get(addon, "")
    return bool(module) and importlib.util.find_spec(module) is not None


def can_install_addons() -> bool:
    """Return True when add-ons can be pip-installed without touching a dev environment.

    Only frozen builds qualify: they carry their own pip and install into the
    private packages directory.  A source checkout would modify the active
    virtual environment, so nothing is ever installed there automatically.
    """
    if not getattr(sys, "frozen", False):
        return False
    return importlib.util.find_spec("pip") is not None


def clear_stale_modules(addon: str) -> None:
    """Evict cached sys.modules entries left over from a failed import attempt.

    importlib.util.find_spec stores ``None`` sentinels for modules that could
    not be found. Removing those entries forces a fresh filesystem probe on the
    next find_spec / import call so the newly installed package is discovered.
    """
    module = ADDON_CHECK_MODULE.get(addon, "")
    prefixes = [module] if module else []
    # Snapshot keys first to avoid mutation-during-iteration.
    to_remove = [
        k
        for k in list(sys.modules.keys())
        if any(k == p or k.startswith(p + ".") for p in prefixes)
    ]
    for key in to_remove:
        sys.modules.pop(key, None)
    # After pip installs into a directory that didn't exist at startup, the
    # PathFinder caches None for that path in sys.path_importer_cache.
    # invalidate_caches() clears that stale entry so find_spec() re-scans.
    importlib.invalidate_caches()
