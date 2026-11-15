"""Runtime addon installer for frozen builds.

Manages optional feature groups that are not bundled in the base installer:
  - privacy  (spacy)
  - nvidia / amd  (live GPU monitoring; installed automatically when a GPU is found)

The frozen binary re-invokes itself with ``--_pip_install_mode`` to run pip
inside the same Python interpreter that was bundled by PyInstaller, then
installs packages into a persistent user-writable directory that is prepended
to ``sys.path`` at every startup.

The installed app ships ``addon_constraints.txt`` (``tools/build/addon_constraints.py`` writes
it at build time): the exact version of every package the app bundles.  Every pip call passes it
as ``-c``, so an add-on can never bring its own numpy or pydantic that would shadow the bundled
one, and a folder left by an older app version is recognised (:func:`outdated_shared_packages`)
and kept off ``sys.path`` until the teacher updates the add-ons.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
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

from .subprocess_utils import no_console_window_kwargs

_logger = logging.getLogger(__name__)

ADDON_PRIVACY = "privacy"
ADDON_NVIDIA = "nvidia"
ADDON_AMD = "amd"

#: pip runs one at a time: two installs into the same ``--target`` directory (the
#: automatic GPU-monitoring install and Settings → Add-ons) would corrupt each other.
_PIP_LOCK = threading.Lock()

#: Per-connection network timeout (seconds) for installs nobody is watching, so a
#: network that silently drops traffic cannot hold a background thread for minutes.
BACKGROUND_PIP_TIMEOUT_S = 15

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

#: What every pip call of the installed app adds (see :func:`pip_policy_arguments`).
_PIP_UPGRADE = "--upgrade"
_PIP_ONLY_BINARY = ("--only-binary", ":all:")
_PIP_NO_CACHE = "--no-cache-dir"

_ADDON_PACKAGES: dict[str, list[str]] = {
    ADDON_PRIVACY: ["spacy>=3.7"],
    # nvidia-ml-py is the maintained replacement for the deprecated pyvnml package.
    # It installs the pynvml Python module (NVIDIA Management Library bindings).
    ADDON_NVIDIA: ["nvidia-ml-py"],
    # pyamdgpuinfo provides AMD GPU monitoring via the sysfs/DRM interface (Linux only).
    ADDON_AMD: ["pyamdgpuinfo"],
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
    ADDON_PRIVACY: "spacy",
    ADDON_NVIDIA: "pynvml",
    ADDON_AMD: "pyamdgpuinfo",
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
    ADDON_PRIVACY: ["spacy"],
    ADDON_NVIDIA: ["pynvml"],
    ADDON_AMD: ["pyamdgpuinfo"],
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


#: Packages the retired Paddle Add-on put into the packages folder.  ``opencv-contrib-python`` is
#: among them because it shadowed the app's own OpenCV (``pip --target`` folders come first on
#: ``sys.path``); numpy is deliberately not: spaCy may need the copy there.
_RETIRED_PACKAGES = frozenset({"paddlepaddle", "paddleocr", "paddlex", "opencv-contrib-python"})


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


def _remove_distribution(pkg_dir: Path, dist_info: Path) -> bool:
    """Delete the files *dist_info*'s ``RECORD`` lists (and the folder); return whether it did.

    Only files inside *pkg_dir* are touched, then the ``.dist-info`` folder, then folders left
    empty.  Without a readable ``RECORD`` nothing is removed.  Never raises: a file in use is
    logged and left behind.
    """
    try:
        lines = (dist_info / "RECORD").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    root = pkg_dir.resolve()
    folders: set[Path] = set()
    for line in lines:
        entry = line.rsplit(",", 2)[0].strip().strip('"')
        if not entry:
            continue
        target = (pkg_dir / entry).resolve()
        if root not in target.parents:
            continue
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


def remove_retired_addons(pkg_dir: Path) -> list[str]:
    """Delete the retired Paddle Add-on's files from *pkg_dir*; return the packages removed.

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


def addon_constraints_path() -> Path | None:
    """Return the constraints file the installed app ships, or ``None``.

    ``None`` outside a frozen build: a source checkout or a wheel install has no bundle whose
    versions an add-on could clash with.  A frozen build without the file (the smoke test
    reports that) is logged and treated like a source run.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or meipass is None:
        return None
    path = Path(meipass) / ADDON_CONSTRAINTS_RELATIVE
    if path.is_file():
        return path
    _logger.warning("The add-on constraints file %s is missing", path)
    return None


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
    """Return whether the add-on folder holds packages from an older app version."""
    return bool(outdated_shared_packages(get_packages_dir(), addon_constraints_path()))


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
    left over, and the app has its own: it goes, by its ``RECORD`` file.
    """
    outdated = set(outdated_shared_packages(pkg_dir, constraints))
    removed: list[str] = []
    for name, _version, dist_info in _distributions(pkg_dir):
        if name in outdated and _remove_distribution(pkg_dir, dist_info):
            removed.append(name)
            _logger.info("Removed the outdated %s copy from %s", name, pkg_dir)
    return removed


def addon_in_folder(distribution: str, pkg_dir: Path | None = None) -> bool:
    """Return whether the add-on folder holds the package *distribution* (pip name)."""
    wanted = _normalise(distribution)
    return any(name == wanted for name, _v, _f in _distributions(pkg_dir or get_packages_dir()))


def update_installed_addons(progress_cb: Callable[[str], None]) -> None:
    """Bring the add-on folder in line with the app after an app update.

    Installs every add-on in the folder again with the bundle's versions, removes the outdated
    shared copies that left over, and makes the folder importable.  Language models are not
    touched: they share no package with the app.

    Raises:
        OSError: if the folder cannot be written.
        RuntimeError: if pip fails (offline, a conflict with the bundle, ...).
    """
    pkg_dir = get_packages_dir()
    requirements = installed_addon_requirements(pkg_dir)
    if requirements:
        install_packages_subprocess(requirements, pkg_dir, progress_cb)
    drop_outdated_shared_packages(pkg_dir, addon_constraints_path())
    inject_packages_dir()
    for addon in _ADDON_STALE_PREFIXES:
        clear_stale_modules(addon)


def pip_policy_arguments(*, upgrade: bool = True) -> list[str]:
    """Return the pip arguments the installed app adds to every install (none from source).

    * ``--upgrade``: ``pip --target`` leaves a package folder that already exists alone, so an
      old numpy copy would never be replaced.
    * ``--only-binary :all:``: a source build would start the GUI executable as its build
      interpreter, and a school computer has no compiler.
    * ``--no-cache-dir``: pip's download cache is shared with other Python programs and never
      cleaned; nothing here installs the same wheel twice.
    * ``-c <constraints>``: every package the app bundles stays at the bundled version, or pip
      says why the add-on cannot be installed.

    Args:
        upgrade: ``False`` for a language model, whose install must leave alone what is there.
    """
    if not getattr(sys, "frozen", False):
        return []
    arguments = [*_PIP_ONLY_BINARY, _PIP_NO_CACHE]
    if upgrade:
        arguments.insert(0, _PIP_UPGRADE)
    constraints = addon_constraints_path()
    if constraints is not None:
        arguments += ["-c", str(constraints)]
    return arguments


def inject_packages_dir() -> None:
    """Prepend the addon packages directory to sys.path (idempotent).

    Safe to call multiple times — inserts at most once. Also registers the
    directory with os.add_dll_directory on Windows so native extension DLL
    dependencies are found by the loader.

    If the packages directory exists but its files are not readable by the
    current user (e.g. installed via 'Run as Administrator'), injection is
    silently skipped to avoid a PermissionError in the import machinery.

    A folder that holds packages of an older app version (:func:`outdated_shared_packages`) is
    skipped too: its copy of numpy or pydantic would replace the bundled one.  The app runs on
    its own bundle until the teacher updates the add-ons.
    """
    pkg_dir = get_packages_dir()
    pkg_str = str(pkg_dir)
    remove_retired_addons(pkg_dir)
    if pkg_str not in sys.path:
        if pkg_dir.exists() and not _packages_dir_readable(pkg_dir):
            return
        outdated = outdated_shared_packages(pkg_dir, addon_constraints_path())
        if outdated:
            _logger.warning(
                "Add-ons need an update after the app update; not using %s (%s)",
                pkg_dir,
                ", ".join(outdated),
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


def gpu_monitoring_addon(vendors: Iterable[str], platform_name: str | None = None) -> str | None:
    """Return the add-on that gives live GPU/VRAM readings for *vendors*, or None.

    NVIDIA wins when both vendors are present (same order as the GPU dialog).
    ``pyamdgpuinfo`` reads the Linux ``amdgpu`` sysfs interface, so an AMD GPU on
    another platform has no add-on.

    Args:
        vendors: ``GpuInfo.vendor`` values of the detected GPUs.
        platform_name: ``sys.platform`` value; defaults to the running platform.
    """
    found = set(vendors)
    if "nvidia" in found:
        return ADDON_NVIDIA
    if "amd" in found and (platform_name or sys.platform).startswith("linux"):
        return ADDON_AMD
    return None


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
    In non-frozen builds a regular ``python -m pip install --upgrade`` is used.
    Output lines are streamed to *progress_cb* in real time.  Installs are
    serialised process-wide.

    Args:
        network_timeout: Per-connection timeout in seconds (with a single retry)
            for installs that run unattended; ``None`` keeps pip's defaults.

    Raises:
        OSError: if *target* cannot be created (permissions, disk full).
        RuntimeError: if pip exits with a non-zero return code.
    """
    target.mkdir(parents=True, exist_ok=True)
    network = (
        ["--timeout", str(network_timeout), "--retries", "1"] if network_timeout is not None else []
    )

    if getattr(sys, "frozen", False):
        cmd = [
            sys.executable,
            "--_pip_install_mode",
            "install",
            "--target",
            str(target),
            *pip_policy_arguments(),
            *network,
            *packages,
        ]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *network, *packages]

    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
        **no_console_window_kwargs(),
    }

    with _PIP_LOCK, subprocess.Popen(cmd, **kwargs) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            progress_cb(line)
        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(
            f"pip exited with code {proc.returncode}. Check the log above for details."
        )


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
        RuntimeError: if pip exits with a non-zero return code.
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
    ``pip install <model>`` fails.  In frozen builds the executable is
    re-invoked with ``--_spacy_download_mode`` to resolve the model URL inside
    the frozen Python.  In non-frozen builds the URL is resolved in-process
    using spaCy's own helpers before calling pip via subprocess.

    *model* is a spaCy model name such as ``en_core_web_sm`` or ``xx_ent_wiki_sm``.
    Output lines are streamed to *progress_cb* in real time.

    Raises:
        OSError: if *target* cannot be created (permissions, disk full).
        RuntimeError: if spaCy is not installed or the download fails.
    """
    target.mkdir(parents=True, exist_ok=True)

    if getattr(sys, "frozen", False):
        cmd = [
            sys.executable,
            "--_spacy_download_mode",
            model,
            "--target",
            str(target),
            *pip_policy_arguments(upgrade=False),
        ]
    else:
        try:
            from urllib.parse import urljoin  # noqa: PLC0415

            from spacy import about as _spacy_about  # noqa: PLC0415
            from spacy.cli.download import (  # noqa: PLC0415
                get_compatibility,
                get_model_filename,
                get_version,
            )
        except ImportError as exc:
            raise RuntimeError(f"spaCy is not installed; cannot download model: {exc}") from exc

        compat = get_compatibility()
        ver = get_version(model, compat)
        filename = get_model_filename(model, ver, sdist=False)
        base = _spacy_about.__download_url__
        if not base.endswith("/"):
            base += "/"
        download_url = urljoin(base, filename)
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(target),
            "--prefer-binary",
            download_url,
        ]

    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
        **no_console_window_kwargs(),
    }

    with _PIP_LOCK, subprocess.Popen(cmd, **kwargs) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            progress_cb(line)
        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(
            f"Model download exited with code {proc.returncode}. Check the log above for details."
        )
