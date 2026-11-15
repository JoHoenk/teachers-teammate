"""Unit tests for teachers_teammate.infrastructure.addon_manager."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.infrastructure import addon_manager
from teachers_teammate.infrastructure.addon_manager import (
    ADDON_AMD,
    ADDON_NVIDIA,
    ADDON_PRIVACY,
    _packages_dir_readable,
    can_install_addons,
    clear_stale_modules,
    download_spacy_model_subprocess,
    get_packages_dir,
    gpu_monitoring_addon,
    inject_packages_dir,
    install_addon_subprocess,
    is_addon_importable,
    remove_retired_addons,
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


# ── gpu_monitoring_addon / can_install_addons ─────────────────────────────────


@pytest.mark.parametrize(
    ("vendors", "platform_name", "expected"),
    [
        (["nvidia"], "win32", ADDON_NVIDIA),
        (["nvidia"], "linux", ADDON_NVIDIA),
        (["amd"], "linux", ADDON_AMD),
        (["amd"], "win32", None),
        (["amd"], "darwin", None),
        (["amd", "nvidia"], "linux", ADDON_NVIDIA),
        ([], "linux", None),
    ],
)
def test_gpu_monitoring_addon_follows_vendor_and_platform(vendors, platform_name, expected) -> None:
    """
    Given  detected GPU vendors on a platform
    When   gpu_monitoring_addon is called
    Then   NVIDIA wins, AMD only counts on Linux, and no GPU means no add-on
    """
    assert gpu_monitoring_addon(vendors, platform_name) == expected


def test_can_install_addons_false_in_source_checkout(monkeypatch) -> None:
    """
    Given  a normal (not frozen) Python process
    When   can_install_addons is called
    Then   False is returned so a dev environment is never modified automatically
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert can_install_addons() is False


def test_can_install_addons_true_for_frozen_build_with_pip(monkeypatch) -> None:
    """
    Given  a frozen build in which pip can be imported
    When   can_install_addons is called
    Then   True is returned
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with patch("importlib.util.find_spec", return_value=object()):
        assert can_install_addons() is True


def test_can_install_addons_false_for_frozen_build_without_pip(monkeypatch) -> None:
    """
    Given  a frozen build without a bundled pip
    When   can_install_addons is called
    Then   False is returned
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with patch("importlib.util.find_spec", return_value=None):
        assert can_install_addons() is False


# ── unattended installs: network timeout and serialisation ───────────────────


def test_install_addon_subprocess_passes_network_timeout_to_pip(tmp_path: Path) -> None:
    """
    Given  an unattended install with a network timeout
    When   install_addon_subprocess() is called in a frozen build
    Then   pip gets --timeout and --retries 1 before the package names
    """
    captured: list[list[str]] = []

    def fake_popen(cmd: list[str], **_kwargs: object) -> object:
        captured.append(cmd)
        return _FakeProc()

    with (
        patch.object(sys, "frozen", True, create=True),
        patch("subprocess.Popen", side_effect=fake_popen),
    ):
        install_addon_subprocess(ADDON_NVIDIA, tmp_path / "pkg", lambda _: None, network_timeout=15)

    cmd = captured[0]
    assert cmd[cmd.index("--timeout") + 1] == "15"
    assert cmd[cmd.index("--retries") + 1] == "1"
    assert cmd.index("--timeout") < cmd.index("nvidia-ml-py")


def test_install_addon_subprocess_keeps_pip_defaults_without_timeout(tmp_path: Path) -> None:
    """
    Given  an install started by the user (no network timeout)
    When   install_addon_subprocess() is called
    Then   no --timeout argument is passed
    """
    captured: list[list[str]] = []

    def fake_popen(cmd: list[str], **_kwargs: object) -> object:
        captured.append(cmd)
        return _FakeProc()

    with patch("subprocess.Popen", side_effect=fake_popen):
        install_addon_subprocess(ADDON_NVIDIA, tmp_path / "pkg", lambda _: None)

    assert "--timeout" not in captured[0]


def test_pip_installs_run_one_at_a_time(tmp_path: Path) -> None:
    """
    Given  two installs started from different threads (automatic one and Downloads)
    When   both run
    Then   the second pip process starts only after the first one has finished
    """
    import threading  # noqa: PLC0415

    events: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()

    class _SlowProc(_FakeProc):
        def __init__(self, label: str) -> None:
            super().__init__()
            self.label = label

        def __enter__(self) -> object:
            events.append(f"start {self.label}")
            if self.label == "a":
                first_started.set()
                release_first.wait(timeout=5)
            return self

        def __exit__(self, *_: object) -> None:
            events.append(f"end {self.label}")

    labels = iter(["a", "b"])

    def fake_popen(_cmd: list[str], **_kwargs: object) -> object:
        return _SlowProc(next(labels))

    def run() -> None:
        install_addon_subprocess(ADDON_NVIDIA, tmp_path / "pkg", lambda _: None)

    with patch("subprocess.Popen", side_effect=fake_popen):
        t1 = threading.Thread(target=run)
        t1.start()
        assert first_started.wait(timeout=5)
        t2 = threading.Thread(target=run)
        t2.start()
        t2.join(timeout=0.3)
        assert t2.is_alive()  # waits for the lock while "a" still runs
        release_first.set()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert events == ["start a", "end a", "start b", "end b"]


def test_no_console_window_kwargs_on_windows(monkeypatch) -> None:
    """
    Given  the platform is Windows
    When   no_console_window_kwargs is called
    Then   CREATE_NO_WINDOW is returned as creationflags
    """
    from teachers_teammate.infrastructure.subprocess_utils import (  # noqa: PLC0415
        no_console_window_kwargs,
    )

    monkeypatch.setattr(sys, "platform", "win32")
    assert no_console_window_kwargs() == {"creationflags": 0x08000000}


def test_no_console_window_kwargs_elsewhere(monkeypatch) -> None:
    """
    Given  the platform is Linux
    When   no_console_window_kwargs is called
    Then   nothing is returned, because creationflags exists only on Windows
    """
    from teachers_teammate.infrastructure.subprocess_utils import (  # noqa: PLC0415
        no_console_window_kwargs,
    )

    monkeypatch.setattr(sys, "platform", "linux")
    assert no_console_window_kwargs() == {}


# ── remove_retired_addons ─────────────────────────────────────────────────────


def _plant_package(pkg_dir: Path, dist: str, files: list[str], *, record: bool = True) -> None:
    """Create *files* under *pkg_dir* and a ``<dist>.dist-info`` that lists them in RECORD."""
    dist_info = pkg_dir / f"{dist}.dist-info"
    dist_info.mkdir(parents=True)
    for name in files:
        path = pkg_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    if record:
        lines = [f"{name},sha256=abc,1" for name in files]
        lines.append(f"{dist}.dist-info/RECORD,,")
        (dist_info / "RECORD").write_text("\n".join(lines), encoding="utf-8")


def test_remove_retired_addons_deletes_exactly_the_recorded_files(tmp_path: Path) -> None:
    """
    Given  a packages folder with the old Paddle add-on and an unrelated package
    When   remove_retired_addons() runs
    Then   only the files in the retired packages' RECORD go, the empty folders with them
    """
    _plant_package(tmp_path, "paddleocr-3.0.0", ["paddleocr/__init__.py", "paddleocr/sub/m.py"])
    _plant_package(tmp_path, "opencv_contrib_python-4.10.0.84", ["cv2/__init__.py"])
    _plant_package(tmp_path, "spacy-3.7.0", ["spacy/__init__.py"])
    (tmp_path / "paddleocr" / "stray.txt").write_text("not in RECORD", encoding="utf-8")

    removed = remove_retired_addons(tmp_path)

    assert sorted(removed) == ["opencv-contrib-python", "paddleocr"]
    assert not (tmp_path / "paddleocr" / "__init__.py").exists()
    assert not (tmp_path / "paddleocr" / "sub").exists()
    assert (tmp_path / "paddleocr" / "stray.txt").exists()  # not listed: left alone
    assert not (tmp_path / "cv2").exists()
    assert not (tmp_path / "paddleocr-3.0.0.dist-info").exists()
    assert (tmp_path / "spacy" / "__init__.py").exists()
    assert (tmp_path / "spacy-3.7.0.dist-info" / "RECORD").exists()


def test_remove_retired_addons_leaves_a_package_without_record(tmp_path: Path) -> None:
    """
    Given  a retired package whose dist-info has no RECORD
    When   remove_retired_addons() runs
    Then   nothing of it is touched
    """
    _plant_package(tmp_path, "paddlepaddle-3.0.0", ["paddle/__init__.py"], record=False)

    assert remove_retired_addons(tmp_path) == []
    assert (tmp_path / "paddle" / "__init__.py").exists()


def test_remove_retired_addons_never_leaves_the_packages_folder(tmp_path: Path) -> None:
    """
    Given  a RECORD that lists a path outside the packages folder
    When   remove_retired_addons() runs
    Then   the outside file is not deleted
    """
    packages = tmp_path / "packages"
    packages.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    _plant_package(packages, "paddlex-3.0.0", ["paddlex/__init__.py", "../outside.txt"])

    remove_retired_addons(packages)

    assert outside.exists()
    assert not (packages / "paddlex").exists()


def test_remove_retired_addons_ignores_a_missing_folder(tmp_path: Path) -> None:
    """
    Given  a packages folder that does not exist
    When   remove_retired_addons() runs
    Then   it returns an empty list
    """
    assert remove_retired_addons(tmp_path / "nope") == []


# ── The bundle's constraints: the pip policy, outdated copies, the update ─────


def _dist_info(pkg_dir: Path, name: str, version: str, files: tuple[str, ...] = ()) -> None:
    """Plant what pip leaves for a package: ``<name>-<version>.dist-info`` with a RECORD."""
    info = pkg_dir / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    record = [f"{name}/__init__.py,sha256=x,1", *(f"{file},sha256=x,1" for file in files)]
    (pkg_dir / name).mkdir(exist_ok=True)
    (pkg_dir / name / "__init__.py").write_text("", encoding="utf-8")
    for file in files:
        (pkg_dir / file).parent.mkdir(parents=True, exist_ok=True)
        (pkg_dir / file).write_text("", encoding="utf-8")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")


@pytest.fixture(name="frozen_bundle")
def _frozen_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A frozen app whose bundle pins numpy 2.5.4 and pydantic 2.14.3; returns the pins file."""
    bundle = tmp_path / "bundle"
    pins = bundle / addon_manager.ADDON_CONSTRAINTS_RELATIVE
    pins.parent.mkdir(parents=True)
    pins.write_text("numpy==2.5.4\npydantic==2.14.3\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    return pins


def test_pip_policy_is_empty_for_a_source_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  a source run or wheel install
    When   the pip policy is asked for
    Then   there is nothing to add
    """
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert addon_manager.pip_policy_arguments() == []


def test_pip_policy_of_the_installed_app(frozen_bundle: Path) -> None:
    """
    Given  the installed app
    When   the pip policy is asked for
    Then   it upgrades, takes wheels only, skips the cache and passes the bundle's constraints
    """
    arguments = addon_manager.pip_policy_arguments()

    assert arguments == [
        "--upgrade",
        "--only-binary",
        ":all:",
        "--no-cache-dir",
        "-c",
        str(frozen_bundle),
    ]
    assert "--upgrade" not in addon_manager.pip_policy_arguments(upgrade=False)


def test_a_frozen_build_without_the_file_installs_without_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a frozen build whose bundle lacks the constraints file
    When   the pip policy is asked for
    Then   wheels-only and no-cache still apply, but no -c
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    arguments = addon_manager.pip_policy_arguments()

    assert "--only-binary" in arguments
    assert "-c" not in arguments


def test_installed_app_pip_call_carries_the_policy(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  the installed app
    When   an add-on and a spaCy model are installed
    Then   both pip commands carry the constraints, wheels-only and no cache; only the
           add-on's upgrades what is there
    """
    captured: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = iter([])

        def wait(self) -> None:
            pass

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **_k: captured.append(cmd) or _Proc())

    install_addon_subprocess(ADDON_PRIVACY, tmp_path / "pkg", lambda _: None)
    download_spacy_model_subprocess("en_core_web_sm", tmp_path / "pkg", lambda _: None)

    assert len(captured) == 2
    addon, model = captured[0], captured[1]
    for command in captured:
        assert command[command.index("-c") + 1] == str(frozen_bundle)
        assert "--only-binary" in command
        assert "--no-cache-dir" in command
    assert "--upgrade" in addon
    assert "--upgrade" not in model


def test_source_pip_call_has_no_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  a source run
    When   a package is installed
    Then   pip is not given the bundle's policy (a dev environment has no bundle)
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    captured: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = iter([])

        def wait(self) -> None:
            pass

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **_k: captured.append(cmd) or _Proc())

    addon_manager.install_packages_subprocess(["six"], tmp_path / "pkg", lambda _: None)

    assert "-c" not in captured[0]
    assert "--only-binary" not in captured[0]


def test_outdated_shared_packages_compares_folder_and_constraints(
    tmp_path: Path, frozen_bundle: Path
) -> None:
    """
    Given  a folder with numpy 2.4.0 (the bundle has 2.5.4), pydantic 2.14.3 and spaCy
    When   the outdated packages are asked for
    Then   only numpy is named: same version and packages the app does not ship do not count
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "numpy", "2.4.0")
    _dist_info(pkg_dir, "pydantic", "2.14.3")
    _dist_info(pkg_dir, "spacy", "3.8.0")

    assert addon_manager.outdated_shared_packages(pkg_dir, frozen_bundle) == ["numpy"]


def test_nothing_is_outdated_without_constraints_or_folder(tmp_path: Path) -> None:
    """
    Given  a source run (no constraints), and a folder that does not exist
    When   the outdated packages are asked for
    Then   the list is empty
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "numpy", "1.0")

    assert addon_manager.outdated_shared_packages(pkg_dir, None) == []
    assert addon_manager.outdated_shared_packages(tmp_path / "none", tmp_path / "c.txt") == []


def test_inject_skips_a_folder_from_an_older_app(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an add-on folder holding another numpy than the bundle
    When   inject_packages_dir() runs
    Then   the folder is not put on sys.path, and it is once the copy matches
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "numpy", "2.4.0")
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: pkg_dir)

    inject_packages_dir()
    assert str(pkg_dir) not in sys.path
    assert addon_manager.addons_outdated() is True

    frozen_bundle.write_text("numpy==2.4.0\n", encoding="utf-8")
    inject_packages_dir()
    assert str(pkg_dir) in sys.path
    assert addon_manager.addons_outdated() is False


def test_installed_addon_requirements_lists_what_is_in_the_folder(tmp_path: Path) -> None:
    """
    Given  a folder with spaCy and langchain-openai (and a language model)
    When   the installed add-on requirements are asked for
    Then   the add-ons' requirements come back, not the model or anything else
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0")
    _dist_info(pkg_dir, "langchain_openai", "1.0.0")
    _dist_info(pkg_dir, "en_core_web_sm", "3.8.0")
    _dist_info(pkg_dir, "numpy", "2.4.0")

    assert addon_manager.installed_addon_requirements(pkg_dir) == [
        "spacy>=3.7",
        "langchain-openai>=0.2",
    ]
    assert addon_manager.addon_in_folder("spaCy", pkg_dir)
    assert not addon_manager.addon_in_folder("pyamdgpuinfo", pkg_dir)


def test_drop_outdated_shared_packages_removes_by_record(
    tmp_path: Path, frozen_bundle: Path
) -> None:
    """
    Given  an outdated numpy copy, a current pydantic and spaCy in the folder
    When   the outdated copies are dropped
    Then   numpy's files are gone and everything else stays
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "numpy", "2.4.0", files=("numpy/core/a.py",))
    _dist_info(pkg_dir, "pydantic", "2.14.3")
    _dist_info(pkg_dir, "spacy", "3.8.0")

    removed = addon_manager.drop_outdated_shared_packages(pkg_dir, frozen_bundle)

    assert removed == ["numpy"]
    assert not (pkg_dir / "numpy").exists()
    assert not (pkg_dir / "numpy-2.4.0.dist-info").exists()
    assert (pkg_dir / "pydantic").is_dir()
    assert (pkg_dir / "spacy-3.8.0.dist-info").is_dir()


def test_update_installs_the_addons_again_and_makes_the_folder_usable(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a folder with spaCy and an outdated numpy
    When   the add-ons are updated and pip (faked) replaces numpy with the bundle's version
    Then   spaCy's requirement was installed with the policy, the folder is on sys.path and
           nothing is outdated
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0")
    _dist_info(pkg_dir, "numpy", "2.4.0")
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: pkg_dir)
    installs: list[tuple[list[str], Path]] = []

    def fake_install(packages: list[str], target: Path, _cb: object, **_kw: object) -> None:
        installs.append((packages, target))
        shutil.rmtree(pkg_dir / "numpy-2.4.0.dist-info")
        _dist_info(pkg_dir, "numpy", "2.5.4")

    monkeypatch.setattr(addon_manager, "install_packages_subprocess", fake_install)

    addon_manager.update_installed_addons(lambda _line: None)

    assert installs == [(["spacy>=3.7"], pkg_dir)]
    assert str(pkg_dir) in sys.path
    assert addon_manager.addons_outdated() is False


def test_update_removes_a_stale_copy_pip_did_not_replace(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an outdated numpy that none of the installed add-ons needs again
    When   the add-ons are updated
    Then   the stale copy is removed, so the folder can be used
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0")
    _dist_info(pkg_dir, "numpy", "2.4.0")
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: pkg_dir)
    monkeypatch.setattr(addon_manager, "install_packages_subprocess", lambda *_a, **_k: None)

    addon_manager.update_installed_addons(lambda _line: None)

    assert not (pkg_dir / "numpy-2.4.0.dist-info").exists()
    assert addon_manager.addons_outdated() is False
