"""Running pip for an add-on: into a staging folder, never into the live one.

Every call passes the bundle's ``addon_constraints.txt`` (the exact version of every package the
app bundles) and ``addon_lock.txt`` (the versions the add-ons' own packages were tested with), so
an add-on can never shadow a bundled package and a teacher does not get whatever is newest.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Any

from ..exceptions import AddonInstallError, AddonUpdateError, InstallFailure
from ..messages import addons_update_needs_install
from .addon_folder import (
    distributions,
    drop_addon_distribution,
    drop_foreign_packages,
    drop_outdated_shared_packages,
    get_packages_dir,
    installed_addon_requirements,
    remove_empty_folder,
)
from .addon_imports import clear_stale_modules, inject_packages_dir
from .addon_list import (
    ADDON_CHECK_MODULE,
    ADDON_PACKAGES,
    addon_constraints_path,
    addon_lock_path,
    requirement_name,
)
from .addon_staging import merge_staged_packages, new_staging_dir, staging_root
from .spacy_models import spacy_model_url
from .subprocess_utils import child_env, no_console_window_kwargs

_logger = logging.getLogger(__name__)


#: pip runs one at a time: two installs into the same ``--target`` directory would
#: corrupt each other.
_PIP_LOCK = threading.Lock()


#: How many lines of a failed pip run the log keeps: the last words say why it stopped.
PIP_FAILURE_LINES = 20


#: What every pip call of the installed app adds (see :func:`pip_policy_arguments`).
_PIP_ONLY_BINARY = ("--only-binary", ":all:")


_PIP_NO_CACHE = "--no-cache-dir"


#: pip would end its output with "a new release of pip is available… run <exe> -m pip install
#: --upgrade pip" — advice a teacher must not follow, and one that cannot work in the bundle.
_PIP_NO_VERSION_CHECK = "--disable-pip-version-check"


#: Failures that say nothing about the installed copy: the same update can work later.
_RETRYABLE = frozenset(
    {InstallFailure.OFFLINE, InstallFailure.DISK_FULL, InstallFailure.FILE_IN_USE}
)


def _is_retryable(error: RuntimeError) -> bool:
    """Return whether *error* is one a later try can get past, so the old copy stays."""
    return isinstance(error, AddonInstallError) and error.reason in _RETRYABLE


def update_installed_addons(progress_cb: Callable[[str], None]) -> None:
    """Bring the add-on folder in line with the app after an app update.

    Installs every add-on in the folder again with the bundle's versions, removes the outdated
    shared copies and the ones built for another Python that left over, and makes the folder
    importable.  Language models are not touched: they share no package with the app, and a
    ``py3-none-any`` model runs on any Python.

    Each add-on is installed on its own: one without a wheel for this Python (or a failed
    download) does not keep the others from updating.  An add-on whose install failed for a reason
    that stays (no version fits, a conflict with the bundle) is removed from the folder, so the
    folder holds neither its old copy nor a new one: readiness then says the add-on is missing,
    which is true, instead of offering an add-on that would run against the bundle's packages it
    was not built for.  A failure a later try can get past (offline, disk full, a file in use)
    keeps the old copy, and the folder stays outdated so the update can be repeated.

    Raises:
        OSError: if the folder cannot be written.
        RuntimeError: if this is not the installed app.
        AddonUpdateError: if pip failed for some add-ons (offline, a conflict with the
            bundle, ...); each failure is an :class:`AddonInstallError` when its reason is known.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError(addons_update_needs_install())
    pkg_dir = get_packages_dir()
    failures: list[tuple[str, RuntimeError]] = []
    installed_names: set[str] = set()
    before = frozenset(folder for _name, _version, folder in distributions(pkg_dir))
    for requirement in installed_addon_requirements(pkg_dir):
        try:
            install_packages_subprocess([requirement], pkg_dir, progress_cb)
        except RuntimeError as exc:
            _logger.warning("Could not update %s: %s", requirement, exc)
            failures.append((requirement, exc))
        else:
            installed_names.add(requirement_name(requirement))
    # A copy is dropped only when retrying cannot help.  A name another requirement just
    # installed is not touched: the fresh copy lives under the same folder name.
    unusable = [
        requirement
        for requirement, error in failures
        if requirement_name(requirement) not in installed_names and not _is_retryable(error)
    ]
    gone = [
        requirement
        for requirement in unusable
        if drop_addon_distribution(pkg_dir, requirement, before)
    ]
    # An old copy that stays still needs the shared packages it was installed with.
    if len(unusable) == len(failures):
        drop_outdated_shared_packages(pkg_dir, addon_constraints_path())
    drop_foreign_packages(pkg_dir)
    inject_packages_dir()
    for addon in ADDON_CHECK_MODULE:
        clear_stale_modules(addon)
    if failures:
        raise AddonUpdateError(failures, gone=gone)


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
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
        "env": child_env(),
        **no_console_window_kwargs(),
    }
    lines: list[str] = []
    with subprocess.Popen(cmd, **kwargs) as proc:
        if proc.stdout is None:  # a PIPE always opens a stream; without one nothing comes back
            raise RuntimeError("the pip process opened no output stream")
        for line in proc.stdout:
            lines.append(line)
            progress_cb(line)
        proc.wait()
    if proc.returncode != 0:
        _log_pip_failure(cmd, lines)
    return proc.returncode, lines


def _log_pip_failure(cmd: list[str], lines: list[str]) -> None:
    """Write the tail of a failed pip run to the log: the installer's pane is gone with it."""
    _logger.warning("pip failed: %s", " ".join(cmd[1:]))
    for line in lines[-PIP_FAILURE_LINES:]:
        _logger.warning("%s", line.rstrip())


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
        remove_empty_folder(staging_root(target))


def install_packages_subprocess(
    packages: Sequence[str],
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
        ADDON_PACKAGES[addon], target, progress_cb, network_timeout=network_timeout
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
