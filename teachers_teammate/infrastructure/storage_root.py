"""Writable storage root selection, path-keying, and config-file resolution.

Responsibilities
----------------
- `resolve_storage_root()` — pick a writable directory for all persistent
  application data (state JSON files and preview images).  Tries candidates in
  priority order: env-var override → OS user-cache directory.
- `compute_cache_key(value)` — produce a short stable identifier from a string.
  Used by `StateRepository` to map a source-file path to a JSON state-file name.
- `default_config_path()` — canonical path of the default ``ocr.toml`` file.
- `resolve_config_path(explicit)` — locate the config file to load.

Storage root candidates (in priority order)
-------------------------------------------
1. ``TEACHERS_TEAMMATE_TMPDIR`` environment variable, when set.
2. The per-user OS cache directory returned by ``platformdirs.user_cache_dir``
   (falls back to ``~/.cache/teachers_teammate`` when ``platformdirs`` is absent).

What this module does NOT do
-----------------------------
- It does not decide how files are laid out inside the storage root — that is
  `StateRepository` (state JSON) and `PreviewImageStore` (preview images).
- It does not know about the content of any stored file.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from ..exceptions import ConfigFileNotFoundError, StorageResolutionError

# StorageResolutionError is defined in exceptions.py; re-exported here for
# backwards-compatibility with any code that imported it from this module.
__all__ = [
    "StorageResolutionError",
    "StorageRoot",
    "compute_cache_key",
    "default_config_path",
    "resolve_artifact_dir",
    "resolve_config_path",
    "resolve_storage_root",
]


@dataclass(frozen=True)
class StorageRoot:
    """Writable storage root plus the source that selected it."""

    path: Path
    source: str


def compute_cache_key(value: str) -> str:
    """Return a short stable cache key for path/identifier values."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _get_platform_cache_dir() -> Path:
    """Return the per-user OS cache directory used as the persistent cache root."""
    try:
        from platformdirs import user_cache_dir  # noqa: PLC0415
    except ImportError:
        cache_dir = Path.home() / ".cache" / "teachers_teammate"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    cache_dir = Path(user_cache_dir("teachers_teammate", appauthor=False, ensure_exists=True))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


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
    """Resolve a writable storage root using ordered policy candidates."""
    candidates: list[tuple[Path, str]] = []
    override = os.environ.get("TEACHERS_TEAMMATE_TMPDIR", "").strip()
    if override:
        candidates.append((Path(override), "override"))

    candidates.append((_get_platform_cache_dir(), "platform"))

    failures: list[str] = []
    for candidate, source in candidates:
        ok, reason = _probe_writable(candidate)
        if ok:
            return StorageRoot(path=candidate, source=source)
        failures.append(f"{candidate}: {reason}")

    reasons = "\n".join(failures) if failures else "no candidates"
    raise StorageResolutionError(f"Could not resolve writable storage root:\n{reasons}")


_DEFAULT_CONFIG_NAME = "ocr.toml"


def default_config_path() -> Path:
    """Return the default ``ocr.toml`` path inside the platform storage root."""
    return resolve_storage_root().path / _DEFAULT_CONFIG_NAME


def resolve_config_path(explicit: str | None) -> Path | None:
    """Return the config file path to load, or ``None`` if none should be used.

    Search order:
    1. Explicit ``--config`` path (raises :exc:`ConfigFileNotFoundError` if missing).
    2. ``ocr.toml`` in the platform storage root.

    Args:
        explicit: Value of ``--config`` from the pre-parse, or ``None``.

    Returns:
        Resolved :class:`~pathlib.Path`, or ``None`` when no config file is found.
    """
    if explicit is not None:
        p = Path(explicit)
        if not p.is_file():
            raise ConfigFileNotFoundError(f"Config file not found: {p}")
        return p
    storage_default = default_config_path()
    return storage_default if storage_default.is_file() else None
