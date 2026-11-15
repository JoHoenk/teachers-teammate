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


def test_resolve_storage_root_does_not_touch_platform_dir_when_override_works(
    monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a writable override and a platform cache dir that cannot be created
    When   resolve_storage_root() is called
    Then   the override is selected and the platform dir is never resolved
    """
    override = tmp_path / "override-root"
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(override))

    def _read_only_home() -> Path:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(storage_root, "_get_platform_cache_dir", _read_only_home)

    resolved = storage_root.resolve_storage_root()

    assert resolved.path == override
    assert resolved.source == "override"


def test_resolve_storage_root_raises_when_platform_dir_cannot_be_created(
    monkeypatch,
) -> None:
    """
    Given  no override and a platform cache dir that cannot be created
    When   resolve_storage_root() is called
    Then   StorageResolutionError is raised instead of a raw OSError
    """
    monkeypatch.delenv("TEACHERS_TEAMMATE_TMPDIR", raising=False)

    def _read_only_home() -> Path:
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(storage_root, "_get_platform_cache_dir", _read_only_home)

    with pytest.raises(storage_root.StorageResolutionError):
        storage_root.resolve_storage_root()


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


# ── settings and history live outside the cache ────────────────────────────


def _user_folders(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    """Point the three per-user folders into *tmp_path* and drop the override."""
    cache, config, data = tmp_path / "cache", tmp_path / "config", tmp_path / "data"
    folders = {"cache": cache, "config": config, "data": data}
    monkeypatch.delenv("TEACHERS_TEAMMATE_TMPDIR", raising=False)
    monkeypatch.setattr(storage_root, "_platform_dir", lambda kind: folders[kind])
    monkeypatch.setattr(storage_root, "_get_platform_cache_dir", lambda: cache)
    cache.mkdir()
    return cache, config, data


def test_config_and_data_roots_are_not_the_cache_folder(monkeypatch, tmp_path: Path) -> None:
    """
    Given  no override and three different per-user folders
    When   the config root, the data root and the default config path are asked for
    Then   they sit in the config and data folders, not in the cache
    """
    cache, config, data = _user_folders(monkeypatch, tmp_path)

    assert storage_root.resolve_config_root() == config
    assert storage_root.resolve_data_root() == data
    assert storage_root.default_config_path() == config / "ocr.toml"
    assert cache not in storage_root.default_config_path().parents


def test_override_keeps_everything_under_one_folder(monkeypatch, tmp_path: Path) -> None:
    """
    Given  TEACHERS_TEAMMATE_TMPDIR is set
    When   the config root, the data root and a data folder are asked for
    Then   all of them are inside the override, as the portable setup and the tests expect
    """
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path))

    assert storage_root.resolve_config_root() == tmp_path
    assert storage_root.resolve_data_root() == tmp_path
    assert storage_root.default_config_path() == tmp_path / "ocr.toml"
    assert storage_root.resolve_data_folder("speed") == tmp_path / "speed"


def test_config_file_in_the_cache_is_moved_once(monkeypatch, tmp_path: Path) -> None:
    """
    Given  an ocr.toml an earlier version saved in the cache folder and none in the config folder
    When   resolve_config_path(None) is called
    Then   the file is moved to the config folder and that path is returned
    """
    cache, config, _data = _user_folders(monkeypatch, tmp_path)
    (cache / "ocr.toml").write_text("language = 'de'\n", encoding="utf-8")

    result = storage_root.resolve_config_path(None)

    assert result == config / "ocr.toml"
    assert result.read_text(encoding="utf-8") == "language = 'de'\n"
    assert not (cache / "ocr.toml").exists()


def test_config_file_in_the_config_folder_wins_over_the_cache(monkeypatch, tmp_path: Path) -> None:
    """
    Given  an ocr.toml in the cache and a newer one in the config folder
    When   resolve_config_path(None) is called
    Then   the config folder's file is returned and the old one is left alone
    """
    cache, config, _data = _user_folders(monkeypatch, tmp_path)
    config.mkdir()
    (config / "ocr.toml").write_text("new", encoding="utf-8")
    (cache / "ocr.toml").write_text("old", encoding="utf-8")

    result = storage_root.resolve_config_path(None)

    assert result is not None
    assert result.read_text(encoding="utf-8") == "new"
    assert (cache / "ocr.toml").read_text(encoding="utf-8") == "old"


def test_config_file_that_cannot_be_moved_is_still_read(monkeypatch, tmp_path: Path) -> None:
    """
    Given  an ocr.toml in the cache and a move that fails
    When   resolve_config_path(None) is called
    Then   the old file is returned, so the teacher's settings are not lost
    """
    cache, _config, _data = _user_folders(monkeypatch, tmp_path)
    (cache / "ocr.toml").write_text("old", encoding="utf-8")

    def refuse(*_args: object) -> None:
        raise OSError("in use")

    monkeypatch.setattr(storage_root.shutil, "move", refuse)

    assert storage_root.resolve_config_path(None) == cache / "ocr.toml"


def test_no_config_file_anywhere_gives_none(monkeypatch, tmp_path: Path) -> None:
    """
    Given  neither the cache nor the config folder holds an ocr.toml
    When   resolve_config_path(None) is called
    Then   None is returned
    """
    _user_folders(monkeypatch, tmp_path)

    assert storage_root.resolve_config_path(None) is None


@pytest.mark.parametrize("name", ["benchmark", "speed"])
def test_data_folder_in_the_cache_is_moved_once(monkeypatch, tmp_path: Path, name: str) -> None:
    """
    Given  a benchmark or speed folder in the cache folder
    When   resolve_data_folder() is called twice
    Then   the folder, with its content, moves to the data folder the first time and stays there
    """
    cache, _config, data = _user_folders(monkeypatch, tmp_path)
    (cache / name / "sub").mkdir(parents=True)
    (cache / name / "sub" / "run.json").write_text("{}", encoding="utf-8")

    first = storage_root.resolve_data_folder(name)
    second = storage_root.resolve_data_folder(name)

    assert first == second == data / name
    assert (data / name / "sub" / "run.json").read_text(encoding="utf-8") == "{}"
    assert not (cache / name).exists()


def test_data_folder_present_in_both_places_keeps_both(monkeypatch, tmp_path: Path) -> None:
    """
    Given  a speed folder in the cache and one in the data folder
    When   resolve_data_folder("speed") is called
    Then   the data folder's one is used and the cache's is not touched
    """
    cache, _config, data = _user_folders(monkeypatch, tmp_path)
    (cache / "speed").mkdir()
    (cache / "speed" / "old.json").write_text("old", encoding="utf-8")
    (data / "speed").mkdir(parents=True)

    assert storage_root.resolve_data_folder("speed") == data / "speed"
    assert (cache / "speed" / "old.json").exists()


def test_config_root_falls_back_to_the_storage_root(monkeypatch, tmp_path: Path) -> None:
    """
    Given  a config folder that cannot be created
    When   the config root is asked for
    Then   the storage root is used instead of raising
    """
    cache, config, _data = _user_folders(monkeypatch, tmp_path)
    config.write_text("a file, not a folder", encoding="utf-8")

    assert storage_root.resolve_config_root() == cache
