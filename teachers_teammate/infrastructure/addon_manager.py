"""Runtime addon installer for frozen builds.

Manages optional feature groups that are not bundled in the base installer:
  - privacy  (spacy)
  - paddle   (paddlepaddle + paddleocr)

The frozen binary re-invokes itself with ``--_pip_install_mode`` to run pip
inside the same Python interpreter that was bundled by PyInstaller, then
installs packages into a persistent user-writable directory that is prepended
to ``sys.path`` at every startup.
"""

from __future__ import annotations

from collections.abc import Callable
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ADDON_PRIVACY = "privacy"
ADDON_PADDLE = "paddle"
ADDON_NVIDIA = "nvidia"
ADDON_AMD = "amd"
ADDON_TESSERACT = "tesseract_py"

# LangChain provider addons — one constant per installable package.
ADDON_LLM_CORE = "llm_core"
ADDON_LLM_OLLAMA = "llm_ollama"
ADDON_LLM_OPENAI = "llm_openai"
ADDON_LLM_ANTHROPIC = "llm_anthropic"
ADDON_LLM_GOOGLE = "llm_google"
ADDON_LLM_MISTRAL = "llm_mistral"
ADDON_LLM_COHERE = "llm_cohere"

_ADDON_PACKAGES: dict[str, list[str]] = {
    ADDON_PRIVACY: ["spacy>=3.7"],
    ADDON_PADDLE: ["paddlepaddle>=3.0", "paddleocr>=2.9"],
    # nvidia-ml-py is the maintained replacement for the deprecated pyvnml package.
    # It installs the pynvml Python module (NVIDIA Management Library bindings).
    ADDON_NVIDIA: ["nvidia-ml-py"],
    # pyamdgpuinfo provides AMD GPU monitoring via the sysfs/DRM interface (Linux only).
    ADDON_AMD: ["pyamdgpuinfo"],
    ADDON_TESSERACT: ["pytesseract>=0.3"],
    # LangChain packages — not bundled by PyInstaller; installed at runtime via Downloads.
    ADDON_LLM_CORE: ["langchain-core>=0.3"],
    ADDON_LLM_OLLAMA: ["langchain-ollama>=0.2"],
    ADDON_LLM_OPENAI: ["langchain-openai>=0.2"],
    ADDON_LLM_ANTHROPIC: ["langchain-anthropic>=0.1"],
    ADDON_LLM_GOOGLE: ["langchain-google-genai>=2.0"],
    ADDON_LLM_MISTRAL: ["langchain-mistralai>=0.1"],
    ADDON_LLM_COHERE: ["langchain-cohere>=0.1"],
}

# Top-level module name used to check importability for each addon.
_ADDON_CHECK_MODULE: dict[str, str] = {
    ADDON_PRIVACY: "spacy",
    ADDON_PADDLE: "paddleocr",
    ADDON_NVIDIA: "pynvml",
    ADDON_AMD: "pyamdgpuinfo",
    ADDON_TESSERACT: "pytesseract",
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
    ADDON_PADDLE: ["paddleocr", "paddlepaddle", "paddle"],
    ADDON_NVIDIA: ["pynvml"],
    ADDON_AMD: ["pyamdgpuinfo"],
    ADDON_TESSERACT: ["pytesseract"],
    ADDON_LLM_CORE: ["langchain_core"],
    ADDON_LLM_OLLAMA: ["langchain_ollama"],
    ADDON_LLM_OPENAI: ["langchain_openai"],
    ADDON_LLM_ANTHROPIC: ["langchain_anthropic"],
    ADDON_LLM_GOOGLE: ["langchain_google_genai"],
    ADDON_LLM_MISTRAL: ["langchain_mistralai"],
    ADDON_LLM_COHERE: ["langchain_cohere"],
}


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


def inject_packages_dir() -> None:
    """Prepend the addon packages directory to sys.path (idempotent).

    Safe to call multiple times — inserts at most once. Also registers the
    directory with os.add_dll_directory on Windows so native extension DLL
    dependencies are found by the loader.

    If the packages directory exists but its files are not readable by the
    current user (e.g. installed via 'Run as Administrator'), injection is
    silently skipped to avoid a PermissionError in the import machinery.
    """
    pkg_dir = get_packages_dir()
    pkg_str = str(pkg_dir)
    if pkg_str not in sys.path:
        if pkg_dir.exists() and not _packages_dir_readable(pkg_dir):
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
) -> None:
    """Install *packages* into *target* using the bundled pip (frozen) or system pip (source).

    In frozen builds the executable re-invokes itself with ``--_pip_install_mode``
    so that pip runs inside the same Python interpreter that PyInstaller bundled.
    In non-frozen builds a regular ``python -m pip install --upgrade`` is used.
    Output lines are streamed to *progress_cb* in real time.

    Raises:
        OSError: if *target* cannot be created (permissions, disk full).
        RuntimeError: if pip exits with a non-zero return code.
    """
    target.mkdir(parents=True, exist_ok=True)

    if getattr(sys, "frozen", False):
        cmd = [
            sys.executable,
            "--_pip_install_mode",
            "install",
            "--target",
            str(target),
            "--prefer-binary",
            *packages,
        ]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *packages]

    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }
    # Prevent a console window from flashing on Windows windowed builds.
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    with subprocess.Popen(cmd, **kwargs) as proc:
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
) -> None:
    """Install *addon* packages into *target* using the bundled pip.

    Raises:
        KeyError: if *addon* is not a known addon name.
        OSError: if *target* cannot be created (permissions, disk full).
        RuntimeError: if pip exits with a non-zero return code.
    """
    install_packages_subprocess(_ADDON_PACKAGES[addon], target, progress_cb)


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
            "--prefer-binary",
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
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    with subprocess.Popen(cmd, **kwargs) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            progress_cb(line)
        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(
            f"Model download exited with code {proc.returncode}. Check the log above for details."
        )
