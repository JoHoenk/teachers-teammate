"""Runtime addon installer for frozen builds.

Manages optional feature groups that are not bundled in the base installer:
  - privacy  (spacy)
  - the LangChain provider packages of the online AI services

The frozen binary re-invokes itself with ``--_pip_install_mode`` to run pip
inside the same Python interpreter that was bundled by PyInstaller, then
installs packages into a persistent user-writable directory that is prepended
to ``sys.path`` at every startup.

The installed app ships ``addon_constraints.txt`` (``tools/build/addon_constraints.py`` writes
it at build time): the exact version of every package the app bundles.  Every pip call passes it
as ``-c``, so an add-on can never bring its own numpy or pydantic that would shadow the bundled
one, and a folder left by an older app version is recognised (:func:`outdated_shared_packages`)
and kept off ``sys.path`` until the teacher updates the add-ons.  A folder built for another
Python is treated the same way (:func:`foreign_python_packages`): its compiled modules cannot be
imported here, so the app asks for *Update add-ons* instead of failing on the first import.

Next to it the bundle carries ``addon_lock.txt`` (``tools/build/addon_lock.py``): the versions of
the add-ons' own packages and their dependencies that this release was tested with, passed as a
second ``-c``.  Without it a teacher would get whatever is newest on the day they click *Install*.

pip never writes into the live folder.  It installs into an empty ``packages.staging/<id>``
folder beside it, and the app merges the result (:func:`merge_staged_packages`): a package the
bundle already has at that version is not copied, a copy that is already there at the same
version is skipped, and anything else replaces the old copy by its ``RECORD``.  Windows locks a
loaded ``.pyd``, so a replacement that fails moves to ``packages.pending/`` and is applied at the
next start (:func:`prepare_packages_dir`), before anything is imported from the folder.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
import importlib
import importlib.util
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

from ..domain.local_models import PRIVACY_ADDON_ID
from ..exceptions import AddonInstallError, AddonUpdateError, InstallFailure
from .spacy_models import spacy_model_url
from .subprocess_utils import no_console_window_kwargs

_logger = logging.getLogger(__name__)

#: pip runs one at a time: two installs into the same ``--target`` directory would
#: corrupt each other.
_PIP_LOCK = threading.Lock()

# LangChain provider addons — one constant per installable package.
ADDON_LLM_CORE = "llm_core"
ADDON_LLM_OLLAMA = "llm_ollama"
ADDON_LLM_OPENAI = "llm_openai"
ADDON_LLM_ANTHROPIC = "llm_anthropic"
ADDON_LLM_GOOGLE = "llm_google"
ADDON_LLM_MISTRAL = "llm_mistral"
ADDON_LLM_COHERE = "llm_cohere"

#: Where the build puts the constraints file, relative to the folder the bundle's files sit in.
ADDON_CONSTRAINTS_RELATIVE = Path("teachers_teammate") / "_build" / "addon_constraints.txt"

#: Where the build puts the add-on lock, next to the constraints file.
ADDON_LOCK_RELATIVE = Path("teachers_teammate") / "_build" / "addon_lock.txt"

#: The folder pip installs into, beside the live one: pip never writes into a folder the
#: running app has imported from (Windows locks a loaded ``.pyd``).
STAGING_SUFFIX = ".staging"
#: The folder a distribution waits in when its old copy could not be replaced yet.
PENDING_SUFFIX = ".pending"
#: Where the files of an old copy go while it is replaced: all of them move there or none do,
#: so a locked file never leaves a package half deleted.  Emptied at every start.
TRASH_SUFFIX = ".trash"
#: A staging folder younger than this may belong to an install another process still runs; only
#: an older one is the leftover of a crash.
_STAGING_MIN_AGE_S = 6 * 3600


#: What every pip call of the installed app adds (see :func:`pip_policy_arguments`).
_PIP_ONLY_BINARY = ("--only-binary", ":all:")
_PIP_NO_CACHE = "--no-cache-dir"
#: pip would end its output with "a new release of pip is available… run <exe> -m pip install
#: --upgrade pip" — advice a teacher must not follow, and one that cannot work in the bundle.
_PIP_NO_VERSION_CHECK = "--disable-pip-version-check"

_ADDON_PACKAGES: dict[str, list[str]] = {
    PRIVACY_ADDON_ID: ["spacy>=3.7"],
    # LangChain packages — not bundled by PyInstaller; installed at runtime via Add-ons.
    ADDON_LLM_CORE: ["langchain-core>=1.4.7"],
    ADDON_LLM_OLLAMA: ["langchain-ollama>=0.2"],
    ADDON_LLM_OPENAI: ["langchain-openai>=0.2"],
    ADDON_LLM_ANTHROPIC: ["langchain-anthropic>=0.1"],
    ADDON_LLM_GOOGLE: ["langchain-google-genai>=2.0"],
    ADDON_LLM_MISTRAL: ["langchain-mistralai>=1.1"],
    ADDON_LLM_COHERE: ["langchain-cohere>=0.1"],
}

# Top-level module name used to check importability for each addon.
_ADDON_CHECK_MODULE: dict[str, str] = {
    PRIVACY_ADDON_ID: "spacy",
    ADDON_LLM_CORE: "langchain_core",
    ADDON_LLM_OLLAMA: "langchain_ollama",
    ADDON_LLM_OPENAI: "langchain_openai",
    ADDON_LLM_ANTHROPIC: "langchain_anthropic",
    ADDON_LLM_GOOGLE: "langchain_google_genai",
    ADDON_LLM_MISTRAL: "langchain_mistralai",
    ADDON_LLM_COHERE: "langchain_cohere",
}

# sys.modules key prefixes to evict after a successful install so that
# importlib.util.find_spec() performs a fresh filesystem probe.
_ADDON_STALE_PREFIXES: dict[str, list[str]] = {
    PRIVACY_ADDON_ID: ["spacy"],
    ADDON_LLM_CORE: ["langchain_core"],
    ADDON_LLM_OLLAMA: ["langchain_ollama"],
    ADDON_LLM_OPENAI: ["langchain_openai"],
    ADDON_LLM_ANTHROPIC: ["langchain_anthropic"],
    ADDON_LLM_GOOGLE: ["langchain_google_genai"],
    ADDON_LLM_MISTRAL: ["langchain_mistralai"],
    ADDON_LLM_COHERE: ["langchain_cohere"],
}


def addon_requirements() -> dict[str, list[str]]:
    """Return the pip requirements of every add-on, by add-on name (a copy)."""
    return {addon: list(packages) for addon, packages in _ADDON_PACKAGES.items()}


def addon_package_names() -> list[str]:
    """Return the normalised name of every package an add-on installs, without its version."""
    return [
        _normalise(re.split(r"[<>=!~\[;,\s]", requirement, maxsplit=1)[0])
        for packages in _ADDON_PACKAGES.values()
        for requirement in packages
    ]


def get_packages_dir() -> Path:
    """Return the persistent user-writable directory for addon packages."""
    try:
        from platformdirs import user_data_dir  # noqa: PLC0415

        return Path(user_data_dir("teachers_teammate", appauthor=False)) / "packages"
    except ImportError:
        return Path.home() / ".local" / "share" / "teachers_teammate" / "packages"


def _packages_dir_readable(pkg_dir: Path) -> bool:
    """Return False if any .py file in *pkg_dir* cannot be opened by the current user.

    On Windows, packages installed while running as Administrator can end up with
    file ACLs that deny read access to the non-elevated user.  Injecting such a
    directory into sys.path causes a PermissionError deep inside Python's import
    machinery when the frozen bootstrap tries to read .py source files.
    """
    try:
        for p in pkg_dir.iterdir():
            if p.is_file() and p.suffix == ".py":
                try:
                    p.open("rb").close()
                    return True
                except PermissionError:
                    return False
        return True  # directory is empty or has no .py files
    except PermissionError:
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


def _normalise(name: str) -> str:
    """Return *name* in PEP 503 form (``Nvidia_ML.py`` → ``nvidia-ml-py``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalised_name(dist_info: Path) -> str:
    """Return the package name of a ``<name>-<version>.dist-info`` folder, PEP 503 normalised."""
    return _normalise(dist_info.name.removesuffix(".dist-info").split("-", 1)[0])


def _distributions(pkg_dir: Path) -> list[tuple[str, str, Path]]:
    """Return ``(normalised name, version, dist-info folder)`` of what *pkg_dir* holds.

    Read from the folder names only, which pip writes as ``<name>-<version>.dist-info``:
    cheap enough to do at every start.
    """
    found: list[tuple[str, str, Path]] = []
    try:
        folders = sorted(pkg_dir.glob("*.dist-info"))
    except OSError:
        return found
    for folder in folders:
        name, _, version = folder.name.removesuffix(".dist-info").partition("-")
        found.append((_normalise(name), version, folder))
    return found


def _record_targets(pkg_dir: Path, dist_info: Path) -> list[Path] | None:
    """Return the resolved files inside *pkg_dir* that *dist_info*'s ``RECORD`` lists.

    ``None`` when the ``RECORD`` cannot be read (missing, or not valid UTF-8).
    """
    try:
        lines = (dist_info / "RECORD").read_text(encoding="utf-8").splitlines()
    except OSError, ValueError:
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
            _remove_empty_folder(trash_root(pkg_dir))
            return False
        moved.append((target, aside))
    shutil.rmtree(trash, ignore_errors=True)
    _remove_empty_folder(trash_root(pkg_dir))
    return True


def _remove_distribution(
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
    targets = _record_targets(pkg_dir, dist_info)
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
    for name, _version, folder in _distributions(pkg_dir):
        if folder not in stale_folders:
            owned.setdefault(name, set()).update(_record_targets(pkg_dir, folder) or [])
    removed: list[str] = []
    for name, _version, dist_info in stale:
        if _remove_distribution(pkg_dir, dist_info, frozenset(owned.get(name, ()))):
            removed.append(name)
    return removed


def remove_retired_addons(pkg_dir: Path) -> list[str]:
    """Delete the files of retired add-ons from *pkg_dir*; return the packages removed.

    Goes strictly by each package's ``RECORD`` file (see :func:`_remove_distribution`).  A package
    without a readable ``RECORD`` is left alone, and so is everything else.
    """
    removed: list[str] = []
    if not pkg_dir.is_dir():
        return removed
    for name, _version, dist_info in _distributions(pkg_dir):
        if name not in _RETIRED_PACKAGES or not _remove_distribution(pkg_dir, dist_info):
            continue
        removed.append(name)
        _logger.info("Removed the retired %s package from %s", name, pkg_dir)
    return removed


# ── The constraints of the installed app ────────────────────────────────────────────────────


def _bundled_build_file(relative: Path) -> Path | None:
    """Return a file the build put into the bundle, or ``None`` outside a frozen build."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or meipass is None:
        return None
    path = Path(meipass) / relative
    if path.is_file():
        return path
    _logger.warning("The bundled file %s is missing", path)
    return None


def addon_constraints_path() -> Path | None:
    """Return the constraints file the installed app ships, or ``None``.

    ``None`` outside a frozen build: a source checkout or a wheel install has no bundle whose
    versions an add-on could clash with.  A frozen build without the file (the smoke test
    reports that) is logged and treated like a source run.
    """
    return _bundled_build_file(ADDON_CONSTRAINTS_RELATIVE)


def addon_lock_path() -> Path | None:
    """Return the add-on lock the installed app ships, or ``None``.

    The lock holds the versions of the add-ons' packages and their dependencies that this
    release was tested with (``tools/build/addon_lock.py``).  Without it pip would install
    whatever is newest on the day a teacher clicks *Install*, which is a version nobody ran.
    """
    return _bundled_build_file(ADDON_LOCK_RELATIVE)


def read_constraints(path: Path) -> dict[str, str]:
    """Return ``{normalised name: version}`` from a constraints file of ``name==version`` lines."""
    pins: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return pins
    for line in text.splitlines():
        name, separator, version = line.strip().partition("==")
        if separator and name and version and not name.startswith("#"):
            pins[_normalise(name)] = version.strip()
    return pins


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
        for found in _distributions(pkg_dir)
        if (tags := _wheel_tags(found[2]))
        and not any(_tag_fits(tag) for tag in tags)
        and _record_targets(pkg_dir, found[2]) is not None
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
            for name, version, _folder in _distributions(pkg_dir)
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
    installed = {name for name, _version, _folder in _distributions(pkg_dir or get_packages_dir())}
    requirements: list[str] = []
    for packages in _ADDON_PACKAGES.values():
        for requirement in packages:
            name = _normalise(re.split(r"[<>=!~\[;\s]", requirement, maxsplit=1)[0])
            if name in installed and requirement not in requirements:
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
    stale = [d for d in _distributions(pkg_dir) if d[0] in pins and d[1] != pins[d[0]]]
    removed = _remove_stale(pkg_dir, stale)
    for name in removed:
        _logger.info("Removed the outdated %s copy from %s", name, pkg_dir)
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
    wanted = _normalise(distribution)
    return any(name == wanted for name, _v, _f in _distributions(pkg_dir or get_packages_dir()))


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
        for found, found_version, _folder in _distributions(pkg_dir)
    )


def _remove_copies(pkg_dir: Path, name: str) -> bool:
    """Remove every copy of *name* in *pkg_dir* by its ``RECORD``; ``False`` if one is locked."""
    for found, _version, dist_info in _distributions(pkg_dir):
        if found == name and not _remove_distribution(pkg_dir, dist_info, strict=True):
            return False
    return True


def _move_distribution(source: Path, destination: Path, dist_info: Path) -> bool:
    """Move one distribution's files from *source* into *destination*; ``False`` if it failed.

    The files come from the ``RECORD``, so nothing outside the distribution moves.  The
    ``.dist-info`` folder goes last: a distribution without it is not installed.  A failure
    moves the files already moved back, so *destination* never keeps half a package.
    """
    targets = _record_targets(source, dist_info)
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
        _remove_empty_folder(folder)
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
    for name, version, dist_info in _distributions(staging):
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
    for name, version, dist_info in _distributions(pending):
        if pins.get(name) == version or _has_version(live, name, version):
            _remove_distribution(pending, dist_info)
            continue
        if _merge_one(pending, live, name, dist_info) is not _Merge.MERGED:
            still.append(name)
    _remove_empty_folder(pending)
    return still


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
    redundant = [d for d in _distributions(pkg_dir) if pins.get(d[0]) == d[1]]
    removed = _remove_stale(pkg_dir, redundant)
    for name in removed:
        _logger.info("Removed the redundant %s copy from %s", name, pkg_dir)
    return removed


def _remove_empty_folder(folder: Path) -> None:
    """Remove *folder* when nothing is left in it."""
    try:
        folder.rmdir()
    except OSError:
        pass  # not empty, or already gone


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
        return
    cutoff = time.time() - _STAGING_MIN_AGE_S
    for child in children:
        try:
            if child.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(child, ignore_errors=True)
    _remove_empty_folder(root)


def prepare_packages_dir() -> None:
    """Finish an interrupted install and clean up, before anything is imported from the folder.

    Called at every normal start, before :func:`inject_packages_dir`: applies what waited in
    ``packages.pending/``, drops copies of bundled packages, and deletes what a crashed install
    left behind.  The add-ons are optional, so a step that fails is logged and the app starts
    anyway.
    """
    live = get_packages_dir()
    constraints = addon_constraints_path()
    steps: tuple[tuple[str, Callable[[], object]], ...] = (
        ("apply the pending packages", lambda: apply_pending_packages(live, constraints)),
        ("drop the redundant packages", lambda: drop_redundant_shared_packages(live, constraints)),
        ("remove the leftover folders", lambda: remove_leftover_folders(live)),
    )
    for what, step in steps:
        try:
            step()
        except OSError as exc:
            _logger.warning("Could not %s in %s: %s", what, live, exc)


def addons_restart_needed() -> bool:
    """Return whether a distribution waits for the next start (``packages.pending/``)."""
    try:
        return any(pending_root(get_packages_dir()).iterdir())
    except OSError:
        return False


def pending_addon_packages() -> list[str]:
    """Return the normalised names of the distributions waiting for the next start."""
    return sorted(
        {name for name, _version, _folder in _distributions(pending_root(get_packages_dir()))}
    )


def addon_waits_for_restart(distribution: str) -> bool:
    """Return whether *distribution* waits in ``packages.pending/`` for the next start."""
    return _normalise(distribution) in pending_addon_packages()


def update_installed_addons(progress_cb: Callable[[str], None]) -> None:
    """Bring the add-on folder in line with the app after an app update.

    Installs every add-on in the folder again with the bundle's versions, removes the outdated
    shared copies and the ones built for another Python that left over, and makes the folder
    importable.  Language models are not touched: they share no package with the app, and a
    ``py3-none-any`` model runs on any Python.

    Each add-on is installed on its own: one without a wheel for this Python (or a failed
    download) does not keep the others, nor the clean-up, from happening.  The folder is made
    usable first; the failures are reported afterwards, and an add-on whose old copy could not
    be replaced is gone from the folder (install it again on Settings → Add-ons).

    Raises:
        OSError: if the folder cannot be written.
        RuntimeError: if this is not the installed app.
        AddonUpdateError: if pip failed for some add-ons (offline, a conflict with the
            bundle, ...); each failure is an :class:`AddonInstallError` when its reason is known.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Add-ons are updated only in the installed app")
    pkg_dir = get_packages_dir()
    failures: list[tuple[str, RuntimeError]] = []
    for requirement in installed_addon_requirements(pkg_dir):
        try:
            install_packages_subprocess([requirement], pkg_dir, progress_cb)
        except RuntimeError as exc:
            _logger.warning("Could not update %s: %s", requirement, exc)
            failures.append((requirement, exc))
    drop_outdated_shared_packages(pkg_dir, addon_constraints_path())
    drop_foreign_packages(pkg_dir)
    inject_packages_dir()
    for addon in _ADDON_STALE_PREFIXES:
        clear_stale_modules(addon)
    if failures:
        raise AddonUpdateError(failures)


def pip_policy_arguments() -> list[str]:
    """Return the pip arguments the installed app adds to every install (none from source).

    * ``--only-binary :all:``: a source build would start the GUI executable as its build
      interpreter, and a school computer has no compiler.
    * ``--disable-pip-version-check``: no "a new release of pip is available" notice in the log.
    * ``--no-cache-dir``: pip's download cache is shared with other Python programs and never
      cleaned; nothing here installs the same wheel twice.
    * ``-c <constraints>``: every package the app bundles stays at the bundled version, or pip
      says why the add-on cannot be installed.
    * ``-c <lock>``: the add-ons' own packages and their dependencies stay at the versions this
      release was tested with, instead of whatever is newest that day.

    No ``--upgrade``: pip installs into an empty staging folder, so there is nothing to replace.
    """
    if not getattr(sys, "frozen", False):
        return []
    arguments = [*_PIP_ONLY_BINARY, _PIP_NO_CACHE, _PIP_NO_VERSION_CHECK]
    for path in (addon_constraints_path(), addon_lock_path()):
        if path is not None:
            arguments += ["-c", str(path)]
    return arguments


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
    remove_retired_addons(pkg_dir)
    if pkg_str not in sys.path:
        if pkg_dir.exists() and not _packages_dir_readable(pkg_dir):
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
        except OSError:
            pass
    # Clear the PathFinder cache so importlib.metadata re-scans the addon dir.
    # Required after pip installs packages into the dir via subprocess — the
    # parent process caches None for any path that didn't exist at startup.
    importlib.invalidate_caches()


def addon_module(addon: str) -> str:
    """Return the top-level module *addon* provides (``privacy`` → ``spacy``)."""
    return _ADDON_CHECK_MODULE[addon]


def is_addon_importable(addon: str) -> bool:
    """Return True if the top-level module for *addon* can be found by the importer."""
    module = _ADDON_CHECK_MODULE.get(addon, "")
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
    prefixes = _ADDON_STALE_PREFIXES.get(addon, [])
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


#: What a failed pip run most often is, read from pip's own output, in this order: the first
#: group that matches wins.  Being offline comes before a missing version, because pip ends an
#: offline run with "No matching distribution found" after its retries.
_PIP_FAILURE_HINTS: tuple[tuple[tuple[str, ...], InstallFailure], ...] = (
    (("no space left", "not enough space", "errno 28"), InstallFailure.DISK_FULL),
    (
        (
            "permission denied",
            "winerror 5",
            "access is denied",
            "zugriff verweigert",
            "being used by another process",
        ),
        InstallFailure.FILE_IN_USE,
    ),
    (
        (
            "name resolution",
            "temporary failure",
            "failed to establish a new connection",
            "connection refused",
            "connection aborted",
            "connectionerror",
            "max retries exceeded",
            "timed out",
            "network is unreachable",
            "proxyerror",
            "certificate verify failed",
        ),
        InstallFailure.OFFLINE,
    ),
    (("could not find a version", "no matching distribution"), InstallFailure.NO_VERSION),
)


def _failure_reason(lines: list[str], fallback: InstallFailure) -> InstallFailure:
    """Return why a pip run failed, read from its output; *fallback* if unknown."""
    text = " ".join(lines).lower()
    for needles, reason in _PIP_FAILURE_HINTS:
        if any(needle in text for needle in needles):
            return reason
    return fallback


def _run_pip(cmd: list[str], progress_cb: Callable[[str], None]) -> tuple[int, list[str]]:
    """Run *cmd*, stream its output to *progress_cb*; return the exit code and the lines."""
    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
        **no_console_window_kwargs(),
    }
    lines: list[str] = []
    with subprocess.Popen(cmd, **kwargs) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            progress_cb(line)
        proc.wait()
    return proc.returncode, lines


def _install_into_staging(
    arguments: list[str],
    target: Path,
    progress_cb: Callable[[str], None],
    fallback: InstallFailure,
) -> None:
    """Run pip with *arguments* into a fresh staging folder, then merge it into *target*.

    ``--target`` goes last, which pip accepts after the requirements; the staging folder is
    empty, so pip never has to replace a file the running app has loaded.  The folder is
    removed whatever happens.
    """
    staging = new_staging_dir(target)
    staging.mkdir(parents=True)
    try:
        code, lines = _run_pip([*arguments, "--target", str(staging)], progress_cb)
        if code != 0:
            raise AddonInstallError(_failure_reason(lines, fallback))
        merge_staged_packages(staging, target, addon_constraints_path())
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        _remove_empty_folder(staging_root(target))


def install_packages_subprocess(
    packages: list[str],
    target: Path,
    progress_cb: Callable[[str], None],
    *,
    network_timeout: int | None = None,
) -> None:
    """Install *packages* into *target* using the bundled pip (frozen) or system pip (source).

    In frozen builds the executable re-invokes itself with ``--_pip_install_mode``
    so that pip runs inside the same Python interpreter that PyInstaller bundled, with the
    arguments of :func:`pip_policy_arguments` (bundle constraints, wheels only, no cache).
    pip installs into a staging folder beside *target*, and the app merges the result
    (:func:`merge_staged_packages`), so no file the running app has loaded is replaced.
    In non-frozen builds a regular ``python -m pip install --upgrade`` is used, which installs
    into the active environment and not into *target*.
    Output lines are streamed to *progress_cb* in real time.  Installs are
    serialised process-wide.

    Args:
        network_timeout: Per-connection timeout in seconds (with a single retry)
            for installs that run unattended; ``None`` keeps pip's defaults.

    Raises:
        OSError: if *target* cannot be created (permissions, disk full).
        AddonInstallError: if pip exits with a non-zero return code (a ``RuntimeError``).
    """
    target.mkdir(parents=True, exist_ok=True)
    network = (
        ["--timeout", str(network_timeout), "--retries", "1"] if network_timeout is not None else []
    )

    with _PIP_LOCK:
        if getattr(sys, "frozen", False):
            _install_into_staging(
                [
                    sys.executable,
                    "--_pip_install_mode",
                    "install",
                    *pip_policy_arguments(),
                    *network,
                    *packages,
                ],
                target,
                progress_cb,
                InstallFailure.INSTALL_FAILED,
            )
            return
        code, lines = _run_pip(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                _PIP_NO_VERSION_CHECK,
                *network,
                *packages,
            ],
            progress_cb,
        )
    if code != 0:
        raise AddonInstallError(_failure_reason(lines, InstallFailure.INSTALL_FAILED))


def install_addon_subprocess(
    addon: str,
    target: Path,
    progress_cb: Callable[[str], None],
    *,
    network_timeout: int | None = None,
) -> None:
    """Install *addon* packages into *target* using the bundled pip.

    Raises:
        KeyError: if *addon* is not a known addon name.
        OSError: if *target* cannot be created (permissions, disk full).
        AddonInstallError: if pip exits with a non-zero return code (a ``RuntimeError``).
    """
    install_packages_subprocess(
        _ADDON_PACKAGES[addon], target, progress_cb, network_timeout=network_timeout
    )


def download_spacy_model_subprocess(
    model: str,
    target: Path,
    progress_cb: Callable[[str], None],
) -> None:
    """Download a spaCy model into *target* using pip.

    spaCy models are hosted on GitHub Releases, not PyPI, so a plain
    ``pip install <model>`` fails.  The URL comes from the pin in
    :mod:`teachers_teammate.infrastructure.spacy_models`, not from spaCy's
    compatibility table, so the model a teacher gets is the one this release
    names.  In frozen builds the executable is re-invoked with
    ``--_spacy_download_mode`` to run pip inside the frozen Python.
    The model is downloaded into a staging folder and merged into *target* like a package.

    *model* is a spaCy model name such as ``en_core_web_sm`` or ``xx_ent_wiki_sm``.
    Output lines are streamed to *progress_cb* in real time.

    Raises:
        OSError: if *target* cannot be created (permissions, disk full).
        KeyError: if *model* has no pin.
        AddonInstallError: if the download fails (a ``RuntimeError``).
    """
    target.mkdir(parents=True, exist_ok=True)
    download_url = spacy_model_url(model)

    if getattr(sys, "frozen", False):
        arguments = [
            sys.executable,
            "--_spacy_download_mode",
            download_url,
            *pip_policy_arguments(),
        ]
    else:
        arguments = [
            sys.executable,
            "-m",
            "pip",
            "install",
            _PIP_NO_VERSION_CHECK,
            "--prefer-binary",
            download_url,
        ]

    with _PIP_LOCK:
        _install_into_staging(arguments, target, progress_cb, InstallFailure.DOWNLOAD_FAILED)


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
