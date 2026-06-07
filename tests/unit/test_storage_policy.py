"""Unit tests for storage root resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.infrastructure import storage_root


def test_resolve_storage_root_uses_override_when_writable(monkeypatch, tmp_path: Path) -> None:
    """
    Given  TEACHERS_TEAMMATE_TMPDIR points to a writable path
    When   resolve_storage_root() is called
    Then   the override path is selected with source='override'
    """
    override = tmp_path / "override-root"
    platform = tmp_path / "platform-root"
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(override))
    monkeypatch.setattr(storage_root, "_get_platform_cache_dir", lambda: platform)
    monkeypatch.setattr(storage_root, "_probe_writable", lambda _path: (True, None))

    resolved = storage_root.resolve_storage_root()

    assert resolved.path == override
    assert resolved.source == "override"


def test_resolve_storage_root_falls_back_to_platform_when_override_unwritable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """
    Given  TEACHERS_TEAMMATE_TMPDIR is set but not writable
    When   resolve_storage_root() is called
    Then   platform cache is selected as fallback
    """
    override = tmp_path / "override-root"
    platform = tmp_path / "platform-root"
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(override))
    monkeypatch.setattr(storage_root, "_get_platform_cache_dir", lambda: platform)

    def _probe(path: Path) -> tuple[bool, str | None]:
        if path == override:
            return False, "permission denied"
        return True, None

    monkeypatch.setattr(storage_root, "_probe_writable", _probe)

    resolved = storage_root.resolve_storage_root()

    assert resolved.path == platform
    assert resolved.source == "platform"


def test_resolve_storage_root_raises_when_no_candidate_is_writable(
    monkeypatch, tmp_path: Path
) -> None:
    """
    Given  both override and platform candidates are unwritable
    When   resolve_storage_root() is called
    Then   StorageResolutionError is raised with candidate details
    """
    override = tmp_path / "override-root"
    platform = tmp_path / "platform-root"
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(override))
    monkeypatch.setattr(storage_root, "_get_platform_cache_dir", lambda: platform)
    monkeypatch.setattr(
        storage_root,
        "_probe_writable",
        lambda path: (False, f"blocked: {path.name}"),
    )

    with pytest.raises(storage_root.StorageResolutionError) as exc:
        storage_root.resolve_storage_root()

    message = str(exc.value)
    assert str(override) in message
    assert str(platform) in message
