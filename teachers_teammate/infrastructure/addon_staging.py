"""Installing an add-on now, and taking it into use at the next start.

pip installs into an empty ``packages.staging/<id>`` folder beside the live one, and the merge
replaces a package by its ``RECORD``.  Windows locks a loaded ``.pyd``, so a replacement that
fails waits in ``packages.pending/`` and is applied before anything is imported from the folder.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
import logging
from pathlib import Path
import shutil
import time

from ..exceptions import AddonInstallError, InstallFailure
from .addon_folder import (
    distributions,
    drop_redundant_shared_packages,
    get_packages_dir,
    record_targets,
    remove_distribution,
    remove_empty_folder,
    remove_retired_addons,
    trash_root,
)
from .addon_list import addon_constraints_path, normalise, read_constraints

_logger = logging.getLogger(__name__)


#: The folder pip installs into, beside the live one: pip never writes into a folder the
#: running app has imported from (Windows locks a loaded ``.pyd``).
STAGING_SUFFIX = ".staging"


#: The folder a distribution waits in when its old copy could not be replaced yet.
PENDING_SUFFIX = ".pending"


#: A staging folder younger than this may belong to an install another process still runs; only
#: an older one is the leftover of a crash.
_STAGING_MIN_AGE_S = 6 * 3600


# ── Staging: pip never writes into the live folder ────────────────────────────


def staging_root(live: Path) -> Path:
    """Return the folder pip installs into, beside *live*."""
    return live.with_name(live.name + STAGING_SUFFIX)


def pending_root(live: Path) -> Path:
    """Return the folder a distribution waits in until the next start, beside *live*."""
    return live.with_name(live.name + PENDING_SUFFIX)


def new_staging_dir(live: Path) -> Path:
    """Return a fresh, empty staging folder for one pip run."""
    return staging_root(live) / str(time.time_ns())


def _has_version(pkg_dir: Path, name: str, version: str) -> bool:
    """Return whether *pkg_dir* already holds *name* at *version*."""
    return any(
        found == name and found_version == version
        for found, found_version, _folder in distributions(pkg_dir)
    )


def _remove_copies(pkg_dir: Path, name: str) -> bool:
    """Remove every copy of *name* in *pkg_dir* by its ``RECORD``; ``False`` if one is locked."""
    for found, _version, dist_info in distributions(pkg_dir):
        if found == name and not remove_distribution(pkg_dir, dist_info, strict=True):
            return False
    return True


def _move_distribution(source: Path, destination: Path, dist_info: Path) -> bool:
    """Move one distribution's files from *source* into *destination*; ``False`` if it failed.

    The files come from the ``RECORD``, so nothing outside the distribution moves.  The
    ``.dist-info`` folder goes last: a distribution without it is not installed.  A failure
    moves the files already moved back, so *destination* never keeps half a package.
    """
    targets = record_targets(source, dist_info)
    if targets is None:
        _logger.warning("Cannot move %s: it has no readable RECORD", dist_info)
        return False
    root = source.resolve()
    info_root = dist_info.resolve()
    folders: set[Path] = set()
    moved: list[tuple[Path, Path]] = []
    try:
        for target in targets:
            if target == info_root or info_root in target.parents or not target.exists():
                continue
            relative = target.relative_to(root)
            (destination / relative).parent.mkdir(parents=True, exist_ok=True)
            (destination / relative).unlink(missing_ok=True)
            shutil.move(str(target), str(destination / relative))
            moved.append((target, destination / relative))
            folders.update(p for p in target.parents if root in p.parents)
        shutil.move(str(dist_info), str(destination / dist_info.name))
    except OSError as exc:
        _logger.warning("Could not move %s into %s: %s", dist_info.name, destination, exc)
        for original, moved_to in reversed(moved):
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved_to), str(original))
            except OSError as undo_exc:
                _logger.warning("Could not put %s back: %s", original, undo_exc)
        return False
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        remove_empty_folder(folder)
    return True


def _park(source: Path, live: Path, dist_info: Path) -> bool:
    """Move a distribution that cannot replace its old copy into ``packages.pending/``."""
    pending = pending_root(live)
    try:
        pending.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.warning("Cannot make %s: %s", pending, exc)
        return False
    return _move_distribution(source, pending, dist_info)


class _Merge(StrEnum):
    """What became of one distribution that was to move into the live folder."""

    MERGED = "merged"
    PENDING = "pending"  # waits in packages.pending/ for the next start
    FAILED = "failed"  # in neither place: the install did not work


def _merge_one(source: Path, live: Path, name: str, dist_info: Path) -> _Merge:
    """Move one distribution into *live*; it waits for the next start when it cannot."""
    if not _remove_copies(live, name):
        return _Merge.PENDING if _park(source, live, dist_info) else _Merge.FAILED
    if _move_distribution(source, live, dist_info):
        return _Merge.MERGED
    # The old copy is gone and the new one would not go in (it was put back in *source*):
    # next start may manage.
    return _Merge.PENDING if _park(source, live, dist_info) else _Merge.FAILED


def merge_staged_packages(staging: Path, live: Path, constraints: Path | None) -> list[str]:
    """Move what pip installed into *staging* into *live*; return the names left pending.

    Per distribution in *staging*:

    * named in the bundle's *constraints* at that version — not copied, the bundle has it;
    * the same name and version already in *live* — skipped;
    * otherwise the old copy goes by its ``RECORD`` and the new one moves in.  A removal that
      fails because a file is locked (Windows) leaves the old copy whole and parks the new one
      in ``packages.pending/``; the merge goes on with the next one.

    ``None`` (a source run) means no constraints: nothing is treated as bundled.

    Raises:
        AddonInstallError: when a distribution could be neither merged nor parked (after the
            others were handled).  *staging* still holds it until the caller removes it.
    """
    pins = read_constraints(constraints) if constraints is not None else {}
    live.mkdir(parents=True, exist_ok=True)
    pending: list[str] = []
    failed: list[str] = []
    for name, version, dist_info in distributions(staging):
        if pins.get(name) == version or _has_version(live, name, version):
            continue
        outcome = _merge_one(staging, live, name, dist_info)
        if outcome is _Merge.PENDING:
            pending.append(name)
        elif outcome is _Merge.FAILED:
            failed.append(name)
    if failed:
        _logger.error("Could not install %s into %s", ", ".join(failed), live)
        raise AddonInstallError(InstallFailure.MERGE_FAILED)
    return pending


def apply_pending_packages(live: Path, constraints: Path | None) -> list[str]:
    """Move what waited in ``packages.pending/`` into *live*; return the names still waiting.

    The same rules as :func:`merge_staged_packages`.  A distribution the bundle already has, or
    one that arrived in the meantime, is dropped from the pending folder.
    """
    pending = pending_root(live)
    if not pending.is_dir():
        return []
    pins = read_constraints(constraints) if constraints is not None else {}
    still: list[str] = []
    for name, version, dist_info in distributions(pending):
        if pins.get(name) == version or _has_version(live, name, version):
            remove_distribution(pending, dist_info)
            continue
        if _merge_one(pending, live, name, dist_info) is not _Merge.MERGED:
            still.append(name)
    remove_empty_folder(pending)
    return still


def remove_leftover_folders(live: Path) -> None:
    """Delete what an install left beside *live*: the trash, and staging folders of a crash.

    A staging folder younger than ``_STAGING_MIN_AGE_S`` is kept: another process may be
    installing into it right now.
    """
    shutil.rmtree(trash_root(live), ignore_errors=True)
    root = staging_root(live)
    try:
        children = list(root.iterdir())
    except OSError:
        return  # no staging folder: nothing to tidy up
    cutoff = time.time() - _STAGING_MIN_AGE_S
    for child in children:
        try:
            if child.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue  # gone since it was listed: another process tidied it
        shutil.rmtree(child, ignore_errors=True)
    remove_empty_folder(root)


#: The folders :func:`prepare_packages_dir` has cleaned in this process.
_prepared: set[Path] = set()


def prepare_packages_dir_once() -> None:
    """Run :func:`prepare_packages_dir` unless this process already did it for the folder."""
    if get_packages_dir() not in _prepared:
        prepare_packages_dir()


def prepare_packages_dir() -> None:
    """Finish an interrupted install and clean up, before anything is imported from the folder.

    Called at every normal start, before :func:`inject_packages_dir`: deletes the add-ons that
    are retired, applies what waited in ``packages.pending/``, drops copies of bundled packages,
    and deletes what a crashed install left behind.  The add-ons are optional, so a step that
    fails is logged and the app starts anyway.
    """
    live = get_packages_dir()
    constraints = addon_constraints_path()
    steps: tuple[tuple[str, Callable[[], object]], ...] = (
        ("remove the retired add-ons", lambda: remove_retired_addons(live)),
        ("apply the pending packages", lambda: apply_pending_packages(live, constraints)),
        ("drop the redundant packages", lambda: drop_redundant_shared_packages(live, constraints)),
        ("remove the leftover folders", lambda: remove_leftover_folders(live)),
    )
    for what, step in steps:
        try:
            step()
        except OSError as exc:
            _logger.warning("Could not %s in %s: %s", what, live, exc)
    _prepared.add(live)


def addons_restart_needed() -> bool:
    """Return whether a distribution waits for the next start (``packages.pending/``)."""
    try:
        return any(pending_root(get_packages_dir()).iterdir())
    except OSError:
        return False  # no pending folder: nothing waits for the next start


def pending_addon_packages() -> list[str]:
    """Return the normalised names of the distributions waiting for the next start."""
    return sorted(
        {name for name, _version, _folder in distributions(pending_root(get_packages_dir()))}
    )


def addon_waits_for_restart(distribution: str) -> bool:
    """Return whether *distribution* waits in ``packages.pending/`` for the next start."""
    return normalise(distribution) in pending_addon_packages()
