"""Unit tests for teachers_teammate.infrastructure.addon_manager."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.infrastructure import addon_manager
from teachers_teammate.infrastructure.addon_manager import (
    ADDON_PADDLE,
    ADDON_PRIVACY,
    _packages_dir_readable,
    clear_stale_modules,
    download_spacy_model_subprocess,
    get_packages_dir,
    inject_packages_dir,
    install_addon_subprocess,
    is_addon_importable,
)


# ── get_packages_dir ──────────────────────────────────────────────────────────


def test_get_packages_dir_returns_path() -> None:
    """
    Given  no special setup
    When   get_packages_dir() is called
    Then   it returns a Path instance
    """
    result = get_packages_dir()
    assert isinstance(result, Path)
    assert result.name == "packages"


# ── inject_packages_dir ───────────────────────────────────────────────────────


def test_inject_packages_dir_inserts_at_index_zero(tmp_path: Path) -> None:
    """
    Given  the packages dir is not on sys.path
    When   inject_packages_dir() is called
    Then   the packages dir is inserted at sys.path[0]
    """
    pkg_dir = tmp_path / "packages"
    with patch(
        "teachers_teammate.infrastructure.addon_manager.get_packages_dir",
        return_value=pkg_dir,
    ):
        original = sys.path.copy()
        inject_packages_dir()
        assert sys.path[0] == str(pkg_dir)
        # Restore
        sys.path[:] = original


def test_inject_packages_dir_is_idempotent(tmp_path: Path) -> None:
    """
    Given  inject_packages_dir() has already been called once
    When   inject_packages_dir() is called a second time
    Then   the packages dir appears at most once in sys.path
    """
    pkg_dir = tmp_path / "packages2"
    pkg_str = str(pkg_dir)
    with patch(
        "teachers_teammate.infrastructure.addon_manager.get_packages_dir",
        return_value=pkg_dir,
    ):
        original = sys.path.copy()
        inject_packages_dir()
        inject_packages_dir()
        assert sys.path.count(pkg_str) == 1
        sys.path[:] = original


def test_inject_packages_dir_does_not_double_insert_if_already_present(tmp_path: Path) -> None:
    """
    Given  the packages dir string is already present in sys.path
    When   inject_packages_dir() is called
    Then   no duplicate entry is added to sys.path
    """
    pkg_dir = tmp_path / "packages3"
    pkg_str = str(pkg_dir)
    with patch(
        "teachers_teammate.infrastructure.addon_manager.get_packages_dir",
        return_value=pkg_dir,
    ):
        original = sys.path.copy()
        sys.path.insert(0, pkg_str)
        inject_packages_dir()
        assert sys.path.count(pkg_str) == 1
        sys.path[:] = original


# ── is_addon_importable ───────────────────────────────────────────────────────


def test_is_addon_importable_returns_false_when_spacy_missing() -> None:
    """
    Given  spacy is not installed (absent from sys.modules)
    When   is_addon_importable(ADDON_PRIVACY) is called
    Then   False is returned
    """
    with patch.dict(sys.modules, {"spacy": None}):
        assert is_addon_importable(ADDON_PRIVACY) is False


def test_is_addon_importable_returns_true_when_spacy_present() -> None:
    """
    Given  spacy is importable (find_spec returns a non-None spec)
    When   is_addon_importable(ADDON_PRIVACY) is called
    Then   True is returned
    """
    # Patch find_spec at the import site: inserting a MagicMock into sys.modules causes
    # importlib.util.find_spec to raise ValueError because __spec__ is not set on MagicMock.
    with patch(
        "teachers_teammate.infrastructure.addon_manager.importlib.util.find_spec",
        return_value=MagicMock(),
    ):
        assert is_addon_importable(ADDON_PRIVACY) is True


def test_is_addon_importable_returns_false_when_paddleocr_missing() -> None:
    """
    Given  paddleocr is not installed (find_spec returns None)
    When   is_addon_importable(ADDON_PADDLE) is called
    Then   False is returned
    """
    with patch(
        "teachers_teammate.infrastructure.addon_manager.importlib.util.find_spec", return_value=None
    ):
        assert is_addon_importable(ADDON_PADDLE) is False


def test_is_addon_importable_unknown_addon_returns_false() -> None:
    """
    Given  an unrecognised addon name not in the registry
    When   is_addon_importable() is called
    Then   False is returned
    """
    assert is_addon_importable("nonexistent") is False


# ── clear_stale_modules ───────────────────────────────────────────────────────


def test_clear_stale_modules_removes_spacy_entries() -> None:
    """
    Given  spacy and submodule entries are present in sys.modules
    When   clear_stale_modules(ADDON_PRIVACY) is called
    Then   all spacy-prefixed keys are removed from sys.modules
    """
    fake_modules = {"spacy": MagicMock(), "spacy.util": MagicMock(), "spacy.lang.en": MagicMock()}
    with patch.dict(sys.modules, fake_modules):
        clear_stale_modules(ADDON_PRIVACY)
        assert "spacy" not in sys.modules
        assert "spacy.util" not in sys.modules
        assert "spacy.lang.en" not in sys.modules


def test_clear_stale_modules_removes_paddle_entries() -> None:
    """
    Given  paddle and paddleocr entries are present in sys.modules
    When   clear_stale_modules(ADDON_PADDLE) is called
    Then   all paddle-prefixed keys are removed from sys.modules
    """
    fake_modules = {
        "paddleocr": MagicMock(),
        "paddle": MagicMock(),
        "paddle.device": MagicMock(),
    }
    with patch.dict(sys.modules, fake_modules):
        clear_stale_modules(ADDON_PADDLE)
        assert "paddleocr" not in sys.modules
        assert "paddle" not in sys.modules
        assert "paddle.device" not in sys.modules


def test_clear_stale_modules_leaves_unrelated_modules() -> None:
    """
    Given  unrelated modules (e.g. 'requests') are in sys.modules
    When   clear_stale_modules(ADDON_PRIVACY) is called
    Then   the unrelated modules remain untouched
    """
    with patch.dict(sys.modules, {"spacy": MagicMock()}):
        requests_before = sys.modules.get("requests")
        clear_stale_modules(ADDON_PRIVACY)
        assert sys.modules.get("requests") is requests_before


def test_clear_stale_modules_unknown_addon_is_noop() -> None:
    """
    Given  an unrecognised addon name
    When   clear_stale_modules() is called
    Then   no exception is raised (silently ignored)
    """
    clear_stale_modules("nonexistent")  # must not raise


# ── install_addon_subprocess ──────────────────────────────────────────────────


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_install_addon_subprocess_uses_pip_install_mode_flag(tmp_path: Path) -> None:
    """
    Given  a frozen build
    When   install_addon_subprocess() is called
    Then   the subprocess command includes --_pip_install_mode and --target
    """
    captured: list[list[str]] = []

    class _FakeProc:
        returncode = 0
        stdout = iter([])

        def wait(self) -> None:
            pass

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    def fake_popen(cmd: list[str], **_kwargs: object) -> object:
        captured.append(cmd)
        return _FakeProc()

    with patch.object(addon_manager.sys, "frozen", True, create=True):
        with patch("subprocess.Popen", side_effect=fake_popen):
            install_addon_subprocess(ADDON_PRIVACY, tmp_path / "pkg", lambda _: None)

    assert len(captured) == 1
    assert sys.executable == captured[0][0]
    assert "--_pip_install_mode" in captured[0]
    assert "install" in captured[0]
    assert "--target" in captured[0]


def test_install_addon_subprocess_streams_progress_cb(tmp_path: Path) -> None:
    """
    Given  pip emits output lines during installation
    When   install_addon_subprocess() is called with a progress callback
    Then   the callback receives each output line
    """
    lines_received: list[str] = []

    class _FakeProc:
        returncode = 0
        stdout = iter(["Collecting spacy\n", "Installing...\n"])

        def wait(self) -> None:
            pass

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    with patch("subprocess.Popen", return_value=_FakeProc()):
        install_addon_subprocess(ADDON_PRIVACY, tmp_path / "pkg", lines_received.append)

    assert "Collecting spacy\n" in lines_received
    assert "Installing...\n" in lines_received


def test_install_addon_subprocess_raises_on_nonzero_exit(tmp_path: Path) -> None:
    """
    Given  pip exits with a non-zero return code
    When   install_addon_subprocess() is called
    Then   RuntimeError is raised with the exit code in the message
    """

    class _FakeProc:
        returncode = 1
        stdout = iter(["ERROR: something went wrong\n"])

        def wait(self) -> None:
            pass

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    with patch("subprocess.Popen", return_value=_FakeProc()):
        with pytest.raises(RuntimeError, match="pip exited with code 1"):
            install_addon_subprocess(ADDON_PRIVACY, tmp_path / "pkg", lambda _: None)


def test_install_addon_subprocess_raises_oserror_on_bad_target(tmp_path: Path) -> None:
    """
    Given  the target packages directory cannot be created (read-only parent)
    When   install_addon_subprocess() is called
    Then   OSError or PermissionError propagates
    """
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o444)

    try:
        with pytest.raises((OSError, PermissionError)):
            install_addon_subprocess(
                ADDON_PRIVACY,
                read_only / "sub" / "pkg",
                lambda _: None,
            )
    finally:
        read_only.chmod(0o755)


def test_install_addon_subprocess_unknown_addon_raises_key_error(tmp_path: Path) -> None:
    """
    Given  an addon name not present in the addon registry
    When   install_addon_subprocess() is called
    Then   KeyError is raised
    """
    with pytest.raises(KeyError):
        install_addon_subprocess("nonexistent", tmp_path / "pkg", lambda _: None)


# ── download_spacy_model_subprocess ──────────────────────────────────────────


class _FakeProc:
    """Minimal context-manager stand-in for subprocess.Popen."""

    def __init__(self, returncode: int = 0, stdout_lines: list[str] | None = None) -> None:
        self.returncode = returncode
        self.stdout = iter(stdout_lines or [])

    def wait(self) -> None:
        pass

    def __enter__(self) -> object:
        return self

    def __exit__(self, *_: object) -> None:
        pass


@pytest.mark.use_case("Language_Model_Download")
def test_download_spacy_model_uses_spacy_download_mode_flag(tmp_path: Path) -> None:
    """
    Given  a spaCy model name and a frozen build
    When   download_spacy_model_subprocess() is called
    Then   the subprocess argv includes --_spacy_download_mode and the model name
    """
    captured: list[list[str]] = []

    def fake_popen(cmd: list[str], **_kwargs: object) -> object:
        captured.append(cmd)
        return _FakeProc()

    with (
        patch.object(sys, "frozen", True, create=True),
        patch("subprocess.Popen", side_effect=fake_popen),
    ):
        download_spacy_model_subprocess("en_core_web_sm", tmp_path / "pkg", lambda _: None)

    assert len(captured) == 1
    assert sys.executable == captured[0][0]
    assert "--_spacy_download_mode" in captured[0]
    assert "en_core_web_sm" in captured[0]
    assert "--target" in captured[0]


def test_download_spacy_model_streams_progress_cb(tmp_path: Path) -> None:
    """
    Given  the download process emits output lines (frozen build)
    When   download_spacy_model_subprocess() is called with a progress callback
    Then   the callback receives each output line
    """
    received: list[str] = []
    proc = _FakeProc(stdout_lines=["Downloading en_core_web_sm\n", "Done\n"])

    with (
        patch.object(sys, "frozen", True, create=True),
        patch("subprocess.Popen", return_value=proc),
    ):
        download_spacy_model_subprocess("en_core_web_sm", tmp_path / "pkg", received.append)

    assert received == ["Downloading en_core_web_sm\n", "Done\n"]


def test_download_spacy_model_raises_on_nonzero_exit(tmp_path: Path) -> None:
    """
    Given  the download process exits with a non-zero return code (frozen build)
    When   download_spacy_model_subprocess() is called
    Then   RuntimeError is raised with the exit code in the message
    """
    proc = _FakeProc(returncode=1, stdout_lines=["ERROR\n"])

    with (
        patch.object(sys, "frozen", True, create=True),
        patch("subprocess.Popen", return_value=proc),
    ):
        with pytest.raises(RuntimeError, match="Model download exited with code 1"):
            download_spacy_model_subprocess("en_core_web_sm", tmp_path / "pkg", lambda _: None)


def test_download_spacy_model_raises_oserror_on_bad_target(tmp_path: Path) -> None:
    """
    Given  the target packages directory cannot be created (read-only parent)
    When   download_spacy_model_subprocess() is called
    Then   OSError or PermissionError propagates
    """
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o444)

    try:
        with pytest.raises((OSError, PermissionError)):
            download_spacy_model_subprocess(
                "en_core_web_sm", read_only / "sub" / "pkg", lambda _: None
            )
    finally:
        read_only.chmod(0o755)


# ── _packages_dir_readable ────────────────────────────────────────────────────


def test_packages_dir_readable_true_for_empty_dir(tmp_path: Path) -> None:
    """
    Given  an empty packages directory
    When   _packages_dir_readable() is called
    Then   True is returned
    """
    assert _packages_dir_readable(tmp_path) is True


def test_packages_dir_readable_true_for_readable_py(tmp_path: Path) -> None:
    """
    Given  a packages directory containing a readable .py file
    When   _packages_dir_readable() is called
    Then   True is returned
    """
    (tmp_path / "mod.py").write_text("x = 1\n")
    assert _packages_dir_readable(tmp_path) is True


def test_packages_dir_readable_false_on_permission_error(tmp_path: Path) -> None:
    """
    Given  opening a .py file in the packages dir raises PermissionError
    When   _packages_dir_readable() is called
    Then   False is returned
    """
    (tmp_path / "mod.py").write_text("x = 1\n")
    with patch("pathlib.Path.open", side_effect=PermissionError):
        assert _packages_dir_readable(tmp_path) is False


def test_packages_dir_readable_false_when_listing_denied(tmp_path: Path) -> None:
    """
    Given  iterating the packages directory raises PermissionError
    When   _packages_dir_readable() is called
    Then   False is returned
    """
    with patch("pathlib.Path.iterdir", side_effect=PermissionError):
        assert _packages_dir_readable(tmp_path) is False


# ── install_addon_subprocess Windows branch ──────────────────────────────────


def test_install_addon_subprocess_sets_no_window_flag_on_win32(tmp_path: Path) -> None:
    """
    Given  sys.platform is 'win32'
    When   install_addon_subprocess() is called
    Then   CREATE_NO_WINDOW (0x08000000) is passed as creationflags to Popen
    """
    captured_kwargs: list[dict] = []

    def fake_popen(_cmd: list[str], **kwargs: object) -> object:
        captured_kwargs.append(kwargs)
        return _FakeProc()

    with patch.object(addon_manager.sys, "platform", "win32"):
        with patch("subprocess.Popen", side_effect=fake_popen):
            install_addon_subprocess(ADDON_PRIVACY, tmp_path / "pkg", lambda _: None)

    assert captured_kwargs[0]["creationflags"] == 0x08000000


# ── get_packages_dir fallback ─────────────────────────────────────────────────


def test_get_packages_dir_falls_back_without_platformdirs() -> None:
    """
    Given  platformdirs is not importable
    When   get_packages_dir() is called
    Then   the ~/.local/share fallback path is used and ends with ('share', 'teachers_teammate', 'packages')
    """
    with patch.dict(sys.modules, {"platformdirs": None}):
        result = get_packages_dir()
    assert result.parts[-3:] == ("share", "teachers_teammate", "packages")


# ── inject_packages_dir skip-when-unreadable ─────────────────────────────────


def test_inject_packages_dir_skips_when_unreadable(tmp_path: Path, monkeypatch) -> None:
    """
    Given  the packages directory exists but is unreadable
    When   inject_packages_dir() is called
    Then   the directory is not added to sys.path
    """
    pkg_dir = tmp_path / "packages"
    pkg_dir.mkdir()
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: pkg_dir)
    monkeypatch.setattr(addon_manager, "_packages_dir_readable", lambda _p: False)

    pkg_str = str(pkg_dir)
    original = list(sys.path)
    try:
        inject_packages_dir()
        assert pkg_str not in sys.path
    finally:
        sys.path[:] = original
