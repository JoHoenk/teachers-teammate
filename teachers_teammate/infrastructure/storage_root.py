"""Writable storage root selection, path-keying, and config-file resolution.

Responsibilities
----------------
- `resolve_storage_root()` — pick a writable directory for all persistent
  application data (state JSON files and preview images).  Tries candidates in
  priority order: env-var override → OS user-cache directory.
- `compute_cache_key(value)` — produce a short stable identifier from a string.
- `document_stem(path)` — the name a document's stored files carry: its stem and that key.
  Used by `StateRepository` for the JSON state file and by the cache service for the images.
- `resolve_config_root()` / `resolve_data_root()` — where the teacher's *own* data lives
  (settings; benchmark history and speed measurements), apart from the cache.
- `resolve_data_folder(name)` — one folder of the data root, moved there from the cache the
  first time it is asked for.
- `default_config_path()` — canonical path of the default ``ocr.toml`` file.
- `resolve_config_path(explicit)` — locate the config file to load.

Storage root candidates (in priority order)
-------------------------------------------
1. ``TEACHERS_TEAMMATE_TMPDIR`` environment variable, when set.
2. The per-user OS cache directory returned by ``platformdirs.user_cache_dir``.

Settings and history are not cache
----------------------------------
The cache (results, previews) may be deleted by cleanup tools at any time and costs nothing
but time to rebuild.  Settings, benchmark history and speed measurements cannot be rebuilt,
so they live in the per-user *config* and *data* folders (``platformdirs.user_config_dir`` /
``user_data_dir``).  With ``TEACHERS_TEAMMATE_TMPDIR`` set all three roots are that one folder.
Files an earlier version wrote into the cache are moved over once.

What this module does NOT do
-----------------------------
- It does not decide how files are laid out inside the storage root — that is
  `StateRepository` (state JSON) and `PreviewImageStore` (preview images).
- It does not know about the content of any stored file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import shutil

import platformdirs
from platformdirs import user_cache_dir

from ..config import override_root
from ..exceptions import ConfigFileNotFoundError, StorageResolutionError
from ..texts.run import config_file_missing, storage_root_unresolved

# StorageResolutionError is defined in exceptions.py; re-exported here for
# backwards-compatibility with any code that imported it from this module.
__all__ = [
    "StorageResolutionError",
    "StorageRoot",
    "compute_cache_key",
    "default_config_path",
    "document_stem",
    "migrate_once",
    "resolve_artifact_dir",
    "resolve_config_path",
    "resolve_config_root",
    "resolve_data_folder",
    "resolve_data_root",
    "resolve_storage_root",
]

_log = logging.getLogger(__name__)
_APP_NAME = "teachers_teammate"


@dataclass(frozen=True)
class StorageRoot:
    """Writable storage root plus the source that selected it."""

    path: Path
    source: str


def compute_cache_key(value: str) -> str:
    """Return a short stable cache key for path/identifier values."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def document_stem(path: Path) -> str:
    """Return the name a document's stored files carry: its stem and its key.

    Two files of the same name in different folders are two documents, so the key of the
    resolved path is part of every name derived from it — the state file and the stored images
    alike.  Without it the second ``Anna.pdf`` would overwrite the first one's pages.
    """
    return f"{path.stem}_{compute_cache_key(str(path.resolve()))}"


def _get_platform_cache_dir() -> Path:
    """Return the per-user OS cache directory used as the persistent cache root."""
    cache_dir = Path(user_cache_dir("teachers_teammate", appauthor=False, ensure_exists=True))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _platform_dir(kind: str) -> Path:
    """Return the per-user OS folder of *kind* (``"cache"``, ``"config"`` or ``"data"``).

    The folder is not created: callers that write create it, and the migration looks at the
    cache folder without making one.
    """
    finder = getattr(platformdirs, f"user_{kind}_dir")
    return Path(finder(_APP_NAME, appauthor=False))


def _legacy_cache_root() -> Path | None:
    """Return the OS cache folder earlier versions kept everything in.

    ``None`` under the override: there the old and the new place are the same folder.
    """
    return None if override_root() else _platform_dir("cache")


def _user_root(kind: str) -> Path:
    """Return the per-user folder of *kind*, or the storage root when it cannot be used."""
    override = override_root()
    if override is not None:
        return override
    path = _platform_dir(kind)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log.warning("Cannot use %s (%s); keeping the %s in the storage root", path, exc, kind)
        return resolve_storage_root().path
    return path


def app_data_folder() -> Path:
    """Return the per-user data folder of the app, without creating it.

    What belongs next to the data root but not in it — the add-on packages and the downloaded
    models — hangs off this folder.
    """
    return _platform_dir("data")


def resolve_config_root() -> Path:
    """Return the folder of the settings file (``ocr.toml``)."""
    return _user_root("config")


def resolve_data_root() -> Path:
    """Return the folder of the data the teacher cannot rebuild (history, speed data)."""
    return _user_root("data")


def migrate_once(old: Path, new: Path) -> bool:
    """Move *old* to *new* when *old* exists and *new* does not.

    When both exist, *new* wins and *old* stays untouched.  A move that fails (read-only
    folder, file in use) is logged and leaves *old* where it is.

    Returns:
        ``True`` when something was moved.
    """
    if new.exists() or not old.exists():
        return False
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(old, new)
    except OSError as exc:
        _log.warning("Could not move %s to %s: %s", old, new, exc)
        return False
    _log.info("Moved %s to %s", old, new)
    return True


def resolve_data_folder(name: str) -> Path:
    """Return the folder *name* of the data root, moved over from the cache on first use."""
    folder = resolve_data_root() / name
    legacy = _legacy_cache_root()
    if legacy is not None:
        migrate_once(legacy / name, folder)
    return folder


def _probe_writable(path: Path) -> tuple[bool, str | None]:
    """Try to create, write, read, and delete a sentinel file in *path*."""
    sentinel = path / ".pp_write_probe"
    fd = -1
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd = os.open(sentinel, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        os.write(fd, b"ok")
        os.close(fd)
        fd = -1
        with sentinel.open("rb") as handle:
            _ = handle.read(2)
        sentinel.unlink(missing_ok=True)
        return True, None
    except OSError as exc:
        return False, str(exc)
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass


def resolve_artifact_dir(output_dir: Path) -> Path:
    """Return the artifact directory derived from *output_dir*.

    Computes a short stable key from *output_dir* and places the artifact
    folder inside the platform storage root.  This is the canonical location
    for per-run cache state and preview images.

    Args:
        output_dir: The pipeline output directory (``Config.output_dir``).

    Returns:
        ``<storage_root>/artifacts/<key>`` as a :class:`~pathlib.Path`.
    """
    root = resolve_storage_root().path
    key = compute_cache_key(str(output_dir.resolve()))
    return root / "artifacts" / key


def resolve_storage_root() -> StorageRoot:
    """Resolve a writable storage root using ordered policy candidates.

    Candidates are resolved lazily so an unusable platform cache dir (for example a
    read-only home) is never touched when the override already works.
    """
    candidates: list[tuple[Callable[[], Path], str]] = []
    override = os.environ.get("TEACHERS_TEAMMATE_TMPDIR", "").strip()
    if override:
        candidates.append((lambda: Path(override), "override"))

    candidates.append((_get_platform_cache_dir, "platform"))

    failures: list[str] = []
    for resolve, source in candidates:
        try:
            candidate = resolve()
        except OSError as exc:
            failures.append(f"{source}: {exc}")
            continue
        ok, reason = _probe_writable(candidate)
        if ok:
            return StorageRoot(path=candidate, source=source)
        failures.append(f"{candidate}: {reason}")

    reasons = "\n".join(failures) if failures else "no candidates"
    raise StorageResolutionError(storage_root_unresolved(reasons))


_DEFAULT_CONFIG_NAME = "ocr.toml"


def default_config_path() -> Path:
    """Return the default ``ocr.toml`` path inside the config root."""
    return resolve_config_root() / _DEFAULT_CONFIG_NAME


def resolve_config_path(explicit: str | None) -> Path | None:
    """Return the config file path to load, or ``None`` if none should be used.

    Search order:
    1. Explicit ``--config`` path (raises :exc:`ConfigFileNotFoundError` if missing).
    2. ``ocr.toml`` in the config root.  An ``ocr.toml`` an earlier version left in the cache
       folder is moved there first, unless one is already there.

    Args:
        explicit: Value of ``--config`` from the pre-parse, or ``None``.

    Returns:
        Resolved :class:`~pathlib.Path`, or ``None`` when no config file is found.
    """
    if explicit is not None:
        p = Path(explicit)
        if not p.is_file():
            raise ConfigFileNotFoundError(config_file_missing(p))
        return p
    storage_default = default_config_path()
    legacy_root = _legacy_cache_root()
    if legacy_root is not None:
        legacy = legacy_root / _DEFAULT_CONFIG_NAME
        if legacy.is_file() and not storage_default.exists():
            if not migrate_once(legacy, storage_default):
                return legacy  # could not be moved: still read what the teacher saved
    return storage_default if storage_default.is_file() else None
