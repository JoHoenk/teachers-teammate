"""The add-ons folder on disk: what it holds, and what does not belong there."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import shutil
import sys
import time

from .addon_list import (
    ADDON_PACKAGES,
    addon_constraints_path,
    normalise,
    read_constraints,
    requirement_name,
)
from .storage_root import app_data_folder

_logger = logging.getLogger(__name__)


#: Where the files of an old copy go while it is replaced: all of them move there or none do,
#: so a locked file never leaves a package half deleted.  Emptied at every start.
TRASH_SUFFIX = ".trash"


def get_packages_dir() -> Path:
    """Return the persistent user-writable directory for addon packages."""
    return app_data_folder() / "packages"


def packages_dir_readable(pkg_dir: Path) -> bool:
    """Return whether every .py file in *pkg_dir* can be opened by the current user.

    On Windows, packages installed while running as Administrator can end up with
    file ACLs that deny read access to the non-elevated user.  Injecting such a
    directory into sys.path causes a PermissionError deep inside Python's import
    machinery when the frozen bootstrap tries to read .py source files.
    """
    try:
        for p in pkg_dir.iterdir():
            if p.is_file() and p.suffix == ".py":
                with p.open("rb"):
                    pass
        return True
    except PermissionError as exc:
        _logger.warning("Cannot read the add-on packages in %s, not using them: %s", pkg_dir, exc)
        return False


#: Packages an earlier app version installed into the packages folder and no longer uses.
#: ``opencv-contrib-python`` is among them because it shadowed the app's own OpenCV
#: (``pip --target`` folders come first on ``sys.path``); numpy is deliberately not: spaCy may
#: need the copy there.  The two GPU-monitoring packages are gone because the app now reads the
#: card itself (``nvidia-ml-py`` is bundled, AMD comes from sysfs).
_RETIRED_PACKAGES = frozenset(
    {
        "paddlepaddle",
        "paddleocr",
        "paddlex",
        "opencv-contrib-python",
        "nvidia-ml-py",
        "pyamdgpuinfo",
    }
)


#: What a folder held when it was last listed, with the stamp of the listing: the four steps of
#: one start read the same folder, and the window asks for the installed add-ons beside them.
_listed: dict[Path, tuple[int, tuple[tuple[str, str, Path], ...]]] = {}


#: A folder stamp this much older than now cannot be shared with a later change (file systems
#: stamp with a granularity of up to two seconds).
_STAMP_SETTLED_NS = 3_000_000_000


def distributions(pkg_dir: Path) -> list[tuple[str, str, Path]]:
    """Return ``(normalised name, version, dist-info folder)`` of what *pkg_dir* holds.

    Read from the folder names only, which pip writes as ``<name>-<version>.dist-info``: cheap
    enough to do at every start.  The listing is kept against the folder's stamp, so a folder
    that did not change is read once (an install changes the stamp).  A stamp newer than the
    clock's granularity may still be shared by a change made right after the listing, so such a
    listing is not kept.
    """
    try:
        stamp = pkg_dir.stat().st_mtime_ns
    except OSError as exc:
        _logger.debug("Cannot read the add-on packages folder %s: %s", pkg_dir, exc)
        return []
    remembered = _listed.get(pkg_dir)
    if remembered is not None and remembered[0] == stamp:
        return list(remembered[1])
    found = _read_distributions(pkg_dir)
    if time.time_ns() - stamp > _STAMP_SETTLED_NS:
        _listed[pkg_dir] = (stamp, tuple(found))
    else:
        _listed.pop(pkg_dir, None)
    return found


def _read_distributions(pkg_dir: Path) -> list[tuple[str, str, Path]]:
    """List the ``*.dist-info`` folders of *pkg_dir*; empty when it cannot be read."""
    found: list[tuple[str, str, Path]] = []
    try:
        folders = sorted(pkg_dir.glob("*.dist-info"))
    except OSError:
        return found
    for folder in folders:
        name, _, version = folder.name.removesuffix(".dist-info").partition("-")
        found.append((normalise(name), version, folder))
    return found


def record_targets(pkg_dir: Path, dist_info: Path) -> list[Path] | None:
    """Return the resolved files inside *pkg_dir* that *dist_info*'s ``RECORD`` lists.

    ``None`` when the ``RECORD`` cannot be read (missing, or not valid UTF-8).
    """
    try:
        lines = (dist_info / "RECORD").read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as exc:
        _logger.debug("Cannot read the RECORD in %s: %s", dist_info, exc)
        return None
    root = pkg_dir.resolve()
    targets: list[Path] = []
    for line in lines:
        entry = line.rsplit(",", 2)[0].strip().strip('"')
        if not entry:
            continue
        target = (pkg_dir / entry).resolve()
        if root in target.parents:
            targets.append(target)
    return targets


def trash_root(live: Path) -> Path:
    """Return the folder the files of a replaced copy wait in, beside *live*."""
    return live.with_name(live.name + TRASH_SUFFIX)


def _move_aside(pkg_dir: Path, targets: list[Path]) -> bool:
    """Move every existing file of *targets* into the trash folder, all or none.

    ``False`` when one cannot move (a file Windows has locked): the files already moved are put
    back, so the package is as whole as it was.  Otherwise the moved files are deleted, which may
    fail for a file that is still mapped; the trash folder is emptied at the next start.
    """
    root = pkg_dir.resolve()
    trash = trash_root(pkg_dir) / str(time.time_ns())
    moved: list[tuple[Path, Path]] = []
    for target in targets:
        if not target.exists():
            continue
        aside = trash / target.relative_to(root)
        try:
            aside.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, aside)
        except OSError as exc:
            _logger.info("Could not move %s aside yet: %s", target, exc)
            for original, moved_to in reversed(moved):
                try:
                    os.replace(moved_to, original)
                except OSError as undo_exc:
                    _logger.warning("Could not put %s back: %s", original, undo_exc)
            shutil.rmtree(trash, ignore_errors=True)
            remove_empty_folder(trash_root(pkg_dir))
            return False
        moved.append((target, aside))
    shutil.rmtree(trash, ignore_errors=True)
    remove_empty_folder(trash_root(pkg_dir))
    return True


def remove_distribution(
    pkg_dir: Path,
    dist_info: Path,
    keep: frozenset[Path] = frozenset(),
    *,
    strict: bool = False,
) -> bool:
    """Delete the files *dist_info*'s ``RECORD`` lists (and the folder); return whether it did.

    Only files inside *pkg_dir* are touched, then the ``.dist-info`` folder, then folders left
    empty.  Files in *keep* (what another copy of the same package owns) stay.  Without a
    readable ``RECORD`` nothing is removed.  Never raises: a file in use is logged and left
    behind — unless *strict*, which first moves every file aside and, if one will not move,
    puts them all back and returns ``False`` without deleting anything, so the caller can wait
    for the next start.
    """
    targets = record_targets(pkg_dir, dist_info)
    if targets is None:
        return False
    root = pkg_dir.resolve()
    folders: set[Path] = set()
    removable = [target for target in targets if target not in keep]
    if strict:
        if not _move_aside(pkg_dir, removable):
            return False
        for target in removable:
            folders.update(p for p in target.parents if root in p.parents)
    else:
        for target in removable:
            try:
                target.unlink(missing_ok=True)
            except OSError as exc:
                _logger.warning("Could not remove %s: %s", target, exc)
                continue
            folders.update(p for p in target.parents if root in p.parents)
    shutil.rmtree(dist_info, ignore_errors=True)
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass  # not empty: something else lives there
    return True


def _remove_stale(pkg_dir: Path, stale: list[tuple[str, str, Path]]) -> list[str]:
    """Remove the *stale* distributions; return the names removed.

    An update installs the new copy beside the old one (``pip --target`` only replaces what has
    the same file name), so a stale ``.dist-info`` often shares its files with the fresh copy of
    the same package.  What another, not stale copy of that name lists stays.
    """
    if not stale:
        return []  # nothing to remove: do not read every RECORD of the folder (runs at start-up)
    stale_folders = {folder for _name, _version, folder in stale}
    owned: dict[str, set[Path]] = {}
    for name, _version, folder in distributions(pkg_dir):
        if folder not in stale_folders:
            owned.setdefault(name, set()).update(record_targets(pkg_dir, folder) or [])
    removed: list[str] = []
    for name, _version, dist_info in stale:
        if remove_distribution(pkg_dir, dist_info, frozenset(owned.get(name, ()))):
            removed.append(name)
    return removed


def remove_retired_addons(pkg_dir: Path) -> list[str]:
    """Delete the files of retired add-ons from *pkg_dir*; return the packages removed.

    Goes strictly by each package's ``RECORD`` file (see :func:`remove_distribution`).  A package
    without a readable ``RECORD`` is left alone, and so is everything else.
    """
    removed: list[str] = []
    if not pkg_dir.is_dir():
        return removed
    for name, _version, dist_info in distributions(pkg_dir):
        if name not in _RETIRED_PACKAGES or not remove_distribution(pkg_dir, dist_info):
            continue
        removed.append(name)
        _logger.info("Removed the retired %s package from %s", name, pkg_dir)
    return removed


def _wheel_tags(dist_info: Path) -> list[str]:
    """Return the ``Tag:`` lines of a ``.dist-info/WHEEL`` file; empty when it has none."""
    try:
        text = (dist_info / "WHEEL").read_text(encoding="utf-8")
    except OSError, ValueError:  # unreadable or not UTF-8: say nothing, leave it alone
        return []
    return [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.lower().startswith("tag:")
    ]


_PYTHON_TAG = re.compile(r"(py|cp)(\d)(\d*)")


def _running_python() -> tuple[int, int]:
    """Return ``(major, minor)`` of the running interpreter."""
    return sys.version_info[0], sys.version_info[1]


def _tag_fits(tag: str) -> bool:
    """Return whether a wheel tag runs on the running interpreter.

    * ``pyX-none-…`` / ``pyXY-none-…`` is pure Python for X (any minor) or for 3.Y and later:
      it fits when X is the running major and Y is at most the running minor, as pip accepts it.
    * ``cpXY-abi3-…`` uses the stable ABI, which is forward compatible: it fits when XY is at
      most the running version.
    * ``cpXY-cpXY…-…`` and ``cpXY-none-…`` are for one interpreter: they fit only that one.
    """
    parts = tag.split("-")
    if len(parts) != 3:
        return False
    python_tag, abi_tag, _platform_tag = parts
    major, minor = _running_python()
    for part in python_tag.split("."):
        match = _PYTHON_TAG.fullmatch(part)
        if match is None or int(match.group(2)) != major:
            continue
        kind, tag_minor = match.group(1), match.group(3)
        if abi_tag == "abi3":
            if kind == "cp" and tag_minor and int(tag_minor) <= minor:
                return True
        elif kind == "py":
            if abi_tag == "none" and (not tag_minor or int(tag_minor) <= minor):
                return True
        elif tag_minor and int(tag_minor) == minor:
            if abi_tag == "none" or abi_tag.removesuffix("m") == f"cp{major}{minor}":
                return True
    return False


def _foreign_distributions(pkg_dir: Path) -> list[tuple[str, str, Path]]:
    """Return the distributions in *pkg_dir* that were built for another Python.

    Only in the installed app: a source or wheel run has no bundle, and its Python is the
    developer's own.  A distribution without a readable ``RECORD`` is left alone too, since
    nothing could remove it and the folder would stay off for good.
    """
    if not getattr(sys, "frozen", False) or not pkg_dir.is_dir():
        return []
    return [
        found
        for found in distributions(pkg_dir)
        if (tags := _wheel_tags(found[2]))
        and not any(_tag_fits(tag) for tag in tags)
        and record_targets(pkg_dir, found[2]) is not None
    ]


def foreign_python_packages(pkg_dir: Path) -> list[str]:
    """Return the distributions in *pkg_dir* that were built for another Python.

    Read from each ``.dist-info/WHEEL``'s ``Tag:`` lines — one small file per distribution, so
    it is computed at every start like :func:`outdated_shared_packages`, with no marker file to
    keep in step.  A distribution whose tags all miss the running interpreter was installed by
    an add-on of an app version that bundled another Python; its compiled modules cannot be
    imported here.  A distribution without a ``WHEEL`` file is left alone: nothing says.  Always
    empty outside the installed app.

    Returns:
        The normalised names, sorted.
    """
    return sorted({name for name, _version, _folder in _foreign_distributions(pkg_dir)})


def outdated_shared_packages(pkg_dir: Path, constraints: Path | None) -> list[str]:
    """Return the packages in *pkg_dir* whose version is not the one the app bundles.

    These are the copies an earlier app version's add-on install left behind: the folder comes
    first on ``sys.path``, so such a copy would replace the bundled one.  Only packages named in
    both the folder and *constraints* count; an add-on's own packages are not in the app's list.
    ``None`` (no constraints: a source run) means nothing is outdated.

    Returns:
        The normalised names, sorted.
    """
    if constraints is None or not pkg_dir.is_dir():
        return []
    pins = read_constraints(constraints)
    return sorted(
        {
            name
            for name, version, _folder in distributions(pkg_dir)
            if name in pins and version != pins[name]
        }
    )


def addons_outdated() -> bool:
    """Return whether the add-on folder holds packages from an older app version or another Python.

    ``False`` outside the installed app."""
    pkg_dir = get_packages_dir()
    return bool(
        outdated_shared_packages(pkg_dir, addon_constraints_path())
        or foreign_python_packages(pkg_dir)
    )


def installed_addon_requirements(pkg_dir: Path | None = None) -> list[str]:
    """Return the requirement of every add-on whose package is in the add-on folder.

    What an update installs again (spaCy language models are separate packages and are not
    listed: they share no package with the app).
    """
    installed = {name for name, _version, _folder in distributions(pkg_dir or get_packages_dir())}
    requirements: list[str] = []
    for packages in ADDON_PACKAGES.values():
        for requirement in packages:
            if requirement_name(requirement) in installed and requirement not in requirements:
                requirements.append(requirement)
    return requirements


def drop_outdated_shared_packages(pkg_dir: Path, constraints: Path | None) -> list[str]:
    """Delete the outdated shared copies an update's install did not replace; return their names.

    The update installs every add-on again with the bundle's versions, which replaces the shared
    packages those add-ons need.  A copy nothing needs any more (an older add-on's dependency) is
    left over, and the app has its own: it goes, by its ``RECORD`` file.  The copy the update
    just installed has the bundle's version and stays.
    """
    if constraints is None or not pkg_dir.is_dir():
        return []
    pins = read_constraints(constraints)
    stale = [d for d in distributions(pkg_dir) if d[0] in pins and d[1] != pins[d[0]]]
    removed = _remove_stale(pkg_dir, stale)
    for name in removed:
        _logger.info("Removed the outdated %s copy from %s", name, pkg_dir)
    return removed


def drop_addon_distribution(pkg_dir: Path, requirement: str, before: frozenset[Path]) -> list[str]:
    """Delete the old copies of the add-on *requirement* a failed update left; return their names.

    *before* is the ``.dist-info`` folders the update started with.  Only those go: another
    add-on's update may have installed a fresh copy of the same package meanwhile (every provider
    brings ``langchain-core``), and that copy stays, with the files it shares.  The old copy is
    removed, never a shared package: the folder then holds neither it nor a new one, so it no
    longer counts as outdated and readiness says the add-on is missing — which is true, so
    installing it again is what helps.  Nothing in the folder is loaded at this point (it was
    off ``sys.path``), so no file is locked by this process.  An empty result means nothing was
    removed (no readable ``RECORD``).
    """
    name = requirement_name(requirement)
    stale = [found for found in distributions(pkg_dir) if found[0] == name and found[2] in before]
    removed = _remove_stale(pkg_dir, stale)
    for removed_name in removed:
        _logger.info("Removed the %s copy of the failed add-on from %s", removed_name, pkg_dir)
    return removed


def drop_foreign_packages(pkg_dir: Path) -> list[str]:
    """Delete the distributions built for another Python; return their names.

    The update installs every add-on again, which replaces the package folders with the ones for
    the running interpreter.  A copy nothing needs any more (an older add-on's dependency) is
    left over and cannot be imported here: it goes, by its ``RECORD`` file.  The copy the update
    just installed (same name, fits this Python) keeps its files.
    """
    removed = _remove_stale(pkg_dir, _foreign_distributions(pkg_dir))
    for name in removed:
        _logger.info("Removed the %s copy built for another Python from %s", name, pkg_dir)
    return removed


def addon_in_folder(distribution: str, pkg_dir: Path | None = None) -> bool:
    """Return whether the add-on folder holds the package *distribution* (pip name)."""
    wanted = normalise(distribution)
    return any(name == wanted for name, _v, _f in distributions(pkg_dir or get_packages_dir()))


def drop_redundant_shared_packages(pkg_dir: Path, constraints: Path | None) -> list[str]:
    """Delete copies of bundled packages at the bundled version; return their names.

    The add-on folder comes first on ``sys.path``, so a copy of a package the app bundles is
    never needed there — at the bundled version it is the same code twice.  Earlier versions
    filled the folder with such copies; this cleans them up.  A copy at another version stays
    (:func:`outdated_shared_packages` reports it, and an update replaces it).
    """
    if constraints is None or not pkg_dir.is_dir():
        return []
    pins = read_constraints(constraints)
    redundant = [d for d in distributions(pkg_dir) if pins.get(d[0]) == d[1]]
    removed = _remove_stale(pkg_dir, redundant)
    for name in removed:
        _logger.info("Removed the redundant %s copy from %s", name, pkg_dir)
    return removed


def remove_empty_folder(folder: Path) -> None:
    """Remove *folder* when nothing is left in it."""
    try:
        folder.rmdir()
    except OSError:
        pass  # not empty, or already gone
