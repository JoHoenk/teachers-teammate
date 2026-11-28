"""The read-only view of the add-ons the application asks about."""

from __future__ import annotations

from pathlib import Path

from .addon_folder import addon_in_folder, addons_outdated, get_packages_dir
from .addon_imports import is_addon_importable
from .addon_staging import addon_waits_for_restart, addons_restart_needed, pending_addon_packages


class AddonPackages:
    """The add-on packages folder of this installation: what it holds, and what waits.

    Each method looks its function up by name when it is called, so patching one of them in a
    test changes what this object answers.
    """

    def packages_dir(self) -> Path:
        """Return the folder the add-on packages are installed into."""
        return get_packages_dir()

    def outdated(self) -> bool:
        """Return whether an app update left add-ons that wait for *Update add-ons*."""
        return addons_outdated()

    def in_folder(self, distribution: str) -> bool:
        """Return whether *distribution* is installed as an add-on."""
        return addon_in_folder(distribution)

    def restart_needed(self) -> bool:
        """Return whether an install waits for the next start."""
        return addons_restart_needed()

    def pending(self) -> list[str]:
        """Return the names of the packages that wait for the next start."""
        return pending_addon_packages()

    def waits_for_restart(self, distribution: str) -> bool:
        """Return whether *distribution* is installed but waits for the next start."""
        return addon_waits_for_restart(distribution)

    def importable(self, addon: str) -> bool:
        """Return whether the named add-on package can be imported now."""
        return is_addon_importable(addon)
