"""Unit tests for teachers_teammate.infrastructure.addon_manager."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.infrastructure import addon_manager
from teachers_teammate.domain.local_models import PRIVACY_ADDON_ID
from teachers_teammate.exceptions import AddonInstallError, InstallFailure
from teachers_teammate.infrastructure.addon_manager import (
    _packages_dir_readable,
    can_install_addons,
    clear_stale_modules,
    download_spacy_model_subprocess,
    get_packages_dir,
    inject_packages_dir,
    install_addon_subprocess,
    is_addon_importable,
    remove_retired_addons,
)
from teachers_teammate.infrastructure.spacy_models import spacy_model_url
from teachers_teammate.infrastructure.storage_root import app_data_folder
from teachers_teammate.messages import MessageKey

#: The add-on of the OpenAI provider package: an install that needs the network.
ADDON_LLM_OPENAI = "llm_openai"


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
    When   is_addon_importable(PRIVACY_ADDON_ID) is called
    Then   False is returned
    """
    with patch.dict(sys.modules, {"spacy": None}):
        assert is_addon_importable(PRIVACY_ADDON_ID) is False


def test_is_addon_importable_returns_true_when_spacy_present() -> None:
    """
    Given  spacy is importable (find_spec returns a non-None spec)
    When   is_addon_importable(PRIVACY_ADDON_ID) is called
    Then   True is returned
    """
    # Patch find_spec at the import site: inserting a MagicMock into sys.modules causes
    # importlib.util.find_spec to raise ValueError because __spec__ is not set on MagicMock.
    with patch(
        "teachers_teammate.infrastructure.addon_manager.importlib.util.find_spec",
        return_value=MagicMock(),
    ):
        assert is_addon_importable(PRIVACY_ADDON_ID) is True


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
    When   clear_stale_modules(PRIVACY_ADDON_ID) is called
    Then   all spacy-prefixed keys are removed from sys.modules
    """
    fake_modules = {"spacy": MagicMock(), "spacy.util": MagicMock(), "spacy.lang.en": MagicMock()}
    with patch.dict(sys.modules, fake_modules):
        clear_stale_modules(PRIVACY_ADDON_ID)
        assert "spacy" not in sys.modules
        assert "spacy.util" not in sys.modules
        assert "spacy.lang.en" not in sys.modules


def test_clear_stale_modules_leaves_unrelated_modules() -> None:
    """
    Given  unrelated modules (e.g. 'requests') are in sys.modules
    When   clear_stale_modules(PRIVACY_ADDON_ID) is called
    Then   the unrelated modules remain untouched
    """
    with patch.dict(sys.modules, {"spacy": MagicMock()}):
        requests_before = sys.modules.get("requests")
        clear_stale_modules(PRIVACY_ADDON_ID)
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
            install_addon_subprocess(PRIVACY_ADDON_ID, tmp_path / "pkg", lambda _: None)

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
        install_addon_subprocess(PRIVACY_ADDON_ID, tmp_path / "pkg", lines_received.append)

    assert "Collecting spacy\n" in lines_received
    assert "Installing...\n" in lines_received


def test_install_addon_subprocess_raises_on_nonzero_exit(tmp_path: Path) -> None:
    """
    Given  pip exits with a non-zero return code and an unknown reason
    When   install_addon_subprocess() is called
    Then   RuntimeError carries one sentence, not the exit code
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
        with pytest.raises(AddonInstallError) as raised:
            install_addon_subprocess(PRIVACY_ADDON_ID, tmp_path / "pkg", lambda _: None)
    assert raised.value.reason is InstallFailure.INSTALL_FAILED


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
                PRIVACY_ADDON_ID,
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
    Then   the subprocess argv includes --_spacy_download_mode and the pinned wheel URL
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
    assert spacy_model_url("en_core_web_sm") in captured[0]
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
    Then   RuntimeError carries one sentence, not the exit code
    """
    proc = _FakeProc(returncode=1, stdout_lines=["ERROR\n"])

    with (
        patch.object(sys, "frozen", True, create=True),
        patch("subprocess.Popen", return_value=proc),
    ):
        with pytest.raises(AddonInstallError) as raised:
            download_spacy_model_subprocess("en_core_web_sm", tmp_path / "pkg", lambda _: None)
    assert raised.value.reason is InstallFailure.DOWNLOAD_FAILED


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
            install_addon_subprocess(PRIVACY_ADDON_ID, tmp_path / "pkg", lambda _: None)

    assert captured_kwargs[0]["creationflags"] == 0x08000000


# ── where the packages live ──────────────────────────────────────────────────


def test_the_packages_folder_sits_in_the_user_data_folder() -> None:
    """
    Given  the per-user data folder of the app
    When   get_packages_dir() is called
    Then   it is the "packages" folder in there, beside the models
    """
    assert get_packages_dir() == app_data_folder() / "packages"


def test_a_folder_that_did_not_change_is_listed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a packages folder that holds one package
    When   its distributions are asked for twice in a row
    Then   the folder is listed once, and a package added afterwards is seen all the same
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.8.0")
    _age(live, hours=1)
    listings: list[Path] = []
    read = addon_manager._read_distributions

    def counting(pkg_dir: Path) -> list[tuple[str, str, Path]]:
        listings.append(pkg_dir)
        return read(pkg_dir)

    monkeypatch.setattr(addon_manager, "_read_distributions", counting)

    first = addon_manager._distributions(live)
    second = addon_manager._distributions(live)
    _dist_info(live, "langchain_core", "1.4.7")
    third = addon_manager._distributions(live)

    assert first == second
    assert len(listings) == 2
    assert {name for name, _version, _folder in third} == {"spacy", "langchain-core"}


def test_a_folder_changed_a_moment_ago_is_listed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a packages folder whose stamp is as new as the clock can tell
    When   its distributions are asked for, a package is added within the same tick, and they
           are asked for again
    Then   the second answer holds the new package: a listing that fresh is never kept
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.8.0")
    stamp = live.stat().st_mtime_ns
    first = addon_manager._distributions(live)
    _dist_info(live, "langchain_core", "1.4.7")
    os.utime(live, ns=(stamp, stamp))  # the same stamp: the change fell into one tick

    second = addon_manager._distributions(live)

    assert {name for name, _version, _folder in first} == {"spacy"}
    assert {name for name, _version, _folder in second} == {"spacy", "langchain-core"}


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


# ── can_install_addons ────────────────────────────────────────────────────────


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
        install_addon_subprocess(
            ADDON_LLM_OPENAI, tmp_path / "pkg", lambda _: None, network_timeout=15
        )

    cmd = captured[0]
    assert cmd[cmd.index("--timeout") + 1] == "15"
    assert cmd[cmd.index("--retries") + 1] == "1"
    assert cmd.index("--timeout") < cmd.index("langchain-openai>=0.2")


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
        install_addon_subprocess(ADDON_LLM_OPENAI, tmp_path / "pkg", lambda _: None)

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
        install_addon_subprocess(ADDON_LLM_OPENAI, tmp_path / "pkg", lambda _: None)

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


def test_remove_retired_addons_deletes_the_gpu_monitoring_packages(tmp_path: Path) -> None:
    """
    Given  a packages folder with the GPU-monitoring packages an earlier version installed
    When   remove_retired_addons() runs
    Then   both go: the app reads the card itself now (NVML is bundled, AMD comes from sysfs)
    """
    _plant_package(tmp_path, "nvidia_ml_py-12.560.30", ["pynvml.py"])
    _plant_package(tmp_path, "pyamdgpuinfo-2.1.7", ["pyamdgpuinfo/__init__.py"])

    removed = remove_retired_addons(tmp_path)

    assert sorted(removed) == ["nvidia-ml-py", "pyamdgpuinfo"]
    assert not (tmp_path / "pynvml.py").exists()
    assert not (tmp_path / "pyamdgpuinfo").exists()


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


def _dist_info(
    pkg_dir: Path,
    name: str,
    version: str,
    files: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> None:
    """Plant what pip leaves for a package: ``<name>-<version>.dist-info`` with a RECORD.

    *tags* become the ``Tag:`` lines of the ``WHEEL`` file pip writes beside the RECORD; without
    them the distribution has no WHEEL file at all.
    """
    info = pkg_dir / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    record = [f"{name}/__init__.py,sha256=x,1", *(f"{file},sha256=x,1" for file in files)]
    (pkg_dir / name).mkdir(exist_ok=True)
    (pkg_dir / name / "__init__.py").write_text("", encoding="utf-8")
    for file in files:
        (pkg_dir / file).parent.mkdir(parents=True, exist_ok=True)
        (pkg_dir / file).write_text("", encoding="utf-8")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    if tags:
        wheel = ["Wheel-Version: 1.0", "Generator: bdist_wheel (0.43.0)", "Root-Is-Purelib: false"]
        wheel += [f"Tag: {tag}" for tag in tags]
        (info / "WHEEL").write_text("\n".join(wheel) + "\n", encoding="utf-8")


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
    Then   it takes wheels only, skips the cache, asks pip for no version notice
           ("run … -m pip install --upgrade pip") and passes the bundle's constraints; it does
           not upgrade, because pip installs into an empty staging folder
    """
    arguments = addon_manager.pip_policy_arguments()

    assert arguments == [
        "--only-binary",
        ":all:",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "-c",
        str(frozen_bundle),
    ]
    assert "--upgrade" not in arguments


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
    Then   both pip commands carry the constraints, wheels-only and no cache, and neither
           upgrades what is there (both install into an empty staging folder)
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

    install_addon_subprocess(PRIVACY_ADDON_ID, tmp_path / "pkg", lambda _: None)
    download_spacy_model_subprocess("en_core_web_sm", tmp_path / "pkg", lambda _: None)

    assert len(captured) == 2
    for command in captured:
        assert command[command.index("-c") + 1] == str(frozen_bundle)
        assert "--only-binary" in command
        assert "--no-cache-dir" in command
        assert "--upgrade" not in command


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
    assert not addon_manager.addon_in_folder("langchain-cohere", pkg_dir)


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

    # The update evicts the add-ons' modules (clear_stale_modules); restore them afterwards so
    # a later test file still sees the real langchain_core (one pytest process for everything).
    with patch.dict(sys.modules):
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

    with patch.dict(sys.modules):
        addon_manager.update_installed_addons(lambda _line: None)

    assert not (pkg_dir / "numpy-2.4.0.dist-info").exists()
    assert addon_manager.addons_outdated() is False


# ── Add-ons built for another Python ─────────────────────────────────────────


@pytest.fixture(name="python_314")
def _python_314(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the installed app runs CPython 3.14 (the version is patched, not read)."""
    monkeypatch.setattr(addon_manager, "_running_python", lambda: (3, 14))
    monkeypatch.setattr(sys, "frozen", True, raising=False)


@pytest.mark.parametrize(
    ("tags", "foreign"),
    [
        (("cp312-cp312-manylinux_2_35_x86_64",), True),
        (("cp38-abi3-manylinux_2_35_x86_64",), False),
        (("py3-none-any",), False),
        (("cp314-cp314-manylinux_2_35_x86_64",), False),
        (("py313-none-any",), False),
        (("py315-none-any",), True),
        (("cp314-none-manylinux_2_35_x86_64",), False),
        (("cp313-none-any",), True),
        (("cp315-abi3-manylinux_2_35_x86_64",), True),
    ],
)
def test_foreign_python_packages_reads_the_wheel_tags(
    tmp_path: Path, python_314: None, tags: tuple[str, ...], foreign: bool
) -> None:
    """
    Given  a distribution whose WHEEL names *tags*
    When   the packages built for another Python are asked for
    Then   only a tag the running 3.14 cannot import counts as foreign
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0", tags=tags)

    assert addon_manager.foreign_python_packages(pkg_dir) == (["spacy"] if foreign else [])


def test_a_distribution_without_a_wheel_file_is_not_foreign(
    tmp_path: Path, python_314: None
) -> None:
    """
    Given  a distribution whose dist-info has no WHEEL file
    When   the packages built for another Python are asked for
    Then   it is left alone: nothing says which interpreter built it
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0")

    assert addon_manager.foreign_python_packages(pkg_dir) == []


def test_a_language_model_stays(tmp_path: Path, python_314: None) -> None:
    """
    Given  a spaCy language model, which is a py3-none-any wheel
    When   the packages built for another Python are asked for
    Then   the model is not named, so an app update keeps it
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "en_core_web_sm", "3.8.0", tags=("py3-none-any",))

    assert addon_manager.foreign_python_packages(pkg_dir) == []


def test_inject_skips_a_folder_built_for_another_python(
    tmp_path: Path, python_314: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an add-on folder holding a distribution compiled for 3.12
    When   inject_packages_dir() runs
    Then   the folder is not put on sys.path, and it is once the copy is for 3.14
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0", tags=("cp312-cp312-manylinux_2_35_x86_64",))
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: pkg_dir)

    inject_packages_dir()
    assert str(pkg_dir) not in sys.path
    assert addon_manager.addons_outdated() is True

    shutil.rmtree(pkg_dir / "spacy-3.8.0.dist-info")
    _dist_info(pkg_dir, "spacy", "3.8.0", tags=("cp314-cp314-manylinux_2_35_x86_64",))
    inject_packages_dir()
    assert str(pkg_dir) in sys.path
    assert addon_manager.addons_outdated() is False


def test_update_drops_a_leftover_built_for_another_python(
    tmp_path: Path, python_314: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a folder with spaCy for 3.14 and a numpy left over from a 3.12 install
    When   the add-ons are updated
    Then   the 3.12 copy is removed, so the folder can be used
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0", tags=("cp314-cp314-manylinux_2_35_x86_64",))
    _dist_info(pkg_dir, "numpy", "2.4.0", tags=("cp312-cp312-manylinux_2_35_x86_64",))
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: pkg_dir)
    monkeypatch.setattr(addon_manager, "install_packages_subprocess", lambda *_a, **_k: None)

    with patch.dict(sys.modules):
        addon_manager.update_installed_addons(lambda _line: None)

    assert not (pkg_dir / "numpy-2.4.0.dist-info").exists()
    assert addon_manager.addons_outdated() is False


def test_update_keeps_the_fresh_copy_beside_a_stale_dist_info(
    tmp_path: Path, python_314: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  spaCy 3.8.0 for 3.12 and the spaCy 3.8.7 for 3.14 pip installed beside it
    When   the add-ons are updated
    Then   only the stale dist-info and its own files go; the fresh copy's files stay
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(
        pkg_dir, "spacy", "3.8.0", files=("spacy/old.py",), tags=("cp312-cp312-linux_x86_64",)
    )
    _dist_info(pkg_dir, "spacy", "3.8.7", tags=("cp314-cp314-linux_x86_64",))
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: pkg_dir)
    monkeypatch.setattr(addon_manager, "install_packages_subprocess", lambda *_a, **_k: None)

    with patch.dict(sys.modules):
        addon_manager.update_installed_addons(lambda _line: None)

    assert not (pkg_dir / "spacy-3.8.0.dist-info").exists()
    assert not (pkg_dir / "spacy" / "old.py").exists()
    assert (pkg_dir / "spacy-3.8.7.dist-info").is_dir()
    assert (pkg_dir / "spacy" / "__init__.py").is_file()


def test_foreign_check_is_off_outside_the_installed_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a source run and a folder holding a package built for another Python
    When   the add-ons are checked and an update is asked for
    Then   nothing is outdated, and the update refuses before it touches pip or the folder
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0", tags=("cp312-cp312-linux_x86_64",))
    monkeypatch.setattr(addon_manager, "_running_python", lambda: (3, 14))
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: pkg_dir)
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert addon_manager.foreign_python_packages(pkg_dir) == []
    assert addon_manager.addons_outdated() is False
    with pytest.raises(RuntimeError) as raised:
        addon_manager.update_installed_addons(lambda _line: None)

    assert raised.value.args[0].key is MessageKey.ADDONS_UPDATE_NEEDS_INSTALL
    assert (pkg_dir / "spacy-3.8.0.dist-info").is_dir()


def test_update_cleans_up_when_one_add_on_fails_to_install(
    tmp_path: Path, python_314: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  two add-ons built for 3.12, one of which has no wheel for 3.14
    When   the add-ons are updated
    Then   the other is installed, the folder is cleaned and usable, and the failure is raised
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0", tags=("cp312-cp312-linux_x86_64",))
    _dist_info(pkg_dir, "langchain_core", "1.4.7", tags=("cp312-cp312-linux_x86_64",))
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: pkg_dir)
    monkeypatch.setattr(addon_manager, "inject_packages_dir", lambda: None)

    def _install(packages: list[str], _target: Path, _cb: object, **_kw: object) -> None:
        if packages[0].startswith("langchain-core"):
            raise RuntimeError("no matching distribution")

    monkeypatch.setattr(addon_manager, "install_packages_subprocess", _install)

    with pytest.raises(addon_manager.AddonUpdateError, match="no matching distribution") as raised:
        addon_manager.update_installed_addons(lambda _line: None)

    assert [requirement for requirement, _error in raised.value.errors] == ["langchain-core>=1.4.7"]

    assert addon_manager.foreign_python_packages(pkg_dir) == []


def test_a_wheel_file_that_is_not_text_is_left_alone(tmp_path: Path, python_314: None) -> None:
    """
    Given  a distribution whose WHEEL file is not valid UTF-8
    When   the packages built for another Python are asked for
    Then   nothing raises and the distribution is not named
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0")
    (pkg_dir / "spacy-3.8.0.dist-info" / "WHEEL").write_bytes(b"\xff\xfe\x00Tag: cp312")

    assert addon_manager.foreign_python_packages(pkg_dir) == []


# ── Staging: pip never writes into the live folder ───────────────────────────


def test_an_install_never_writes_into_the_live_folder(tmp_path: Path) -> None:
    """
    Given  the installed app and a live folder the app has imported from
    When   an add-on is installed
    Then   pip's --target is an empty folder beside the live one, and it is gone afterwards
    """
    live = tmp_path / "packages"
    live.mkdir()
    targets: list[Path] = []

    def fake_popen(cmd: list[str], **_kwargs: object) -> object:
        target = Path(cmd[cmd.index("--target") + 1])
        targets.append(target)
        assert target != live
        assert target.parent == tmp_path / "packages.staging"
        assert list(target.iterdir()) == []  # empty: nothing to replace
        return _FakeProc()

    with (
        patch.object(sys, "frozen", True, create=True),
        patch("subprocess.Popen", side_effect=fake_popen),
    ):
        install_addon_subprocess(PRIVACY_ADDON_ID, live, lambda _: None)

    assert len(targets) == 1
    assert not targets[0].exists()
    assert not (tmp_path / "packages.staging").exists()


def test_merge_does_not_copy_a_package_the_bundle_has(tmp_path: Path, frozen_bundle: Path) -> None:
    """
    Given  a staging folder with numpy at the version the bundle pins, and spaCy
    When   the staged packages are merged into the live folder
    Then   numpy is not copied (the bundle has it) and spaCy moves in
    """
    live = tmp_path / "packages"
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "numpy", "2.5.4")
    _dist_info(staging, "spacy", "3.8.0")

    pending = addon_manager.merge_staged_packages(staging, live, frozen_bundle)

    assert pending == []
    assert not (live / "numpy").exists()
    assert not (live / "numpy-2.5.4.dist-info").exists()
    assert (live / "spacy" / "__init__.py").is_file()
    assert (live / "spacy-3.8.0.dist-info" / "RECORD").is_file()


def test_merge_skips_a_version_that_is_already_live(tmp_path: Path) -> None:
    """
    Given  a live folder that already holds spaCy 3.8.0, and a staging folder with the same
    When   the staged packages are merged
    Then   nothing is copied over the live copy
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.8.0")
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "spacy", "3.8.0", files=("spacy/extra.py",))

    assert addon_manager.merge_staged_packages(staging, live, None) == []
    assert not (live / "spacy" / "extra.py").exists()


def test_merge_replaces_an_older_copy_by_its_record(tmp_path: Path) -> None:
    """
    Given  spaCy 3.7.0 live and 3.8.0 staged
    When   the staged packages are merged
    Then   the old copy's files and dist-info go, and the new ones are in place
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.7.0", files=("spacy/old.py",))
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "spacy", "3.8.0", files=("spacy/new.py",))

    assert addon_manager.merge_staged_packages(staging, live, None) == []
    assert not (live / "spacy-3.7.0.dist-info").exists()
    assert not (live / "spacy" / "old.py").exists()
    assert (live / "spacy" / "new.py").is_file()
    assert (live / "spacy-3.8.0.dist-info").is_dir()


def _lock_file(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Make Windows refuse to move the file called *name* (it is loaded by the running app)."""
    real_replace = os.replace

    def locked(source: object, destination: object, *args: object, **kwargs: object) -> None:
        if Path(str(source)).name == name:
            raise PermissionError(13, "Zugriff verweigert", str(source))
        real_replace(source, destination, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(os, "replace", locked)


def test_a_locked_file_parks_the_distribution_in_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a live pydantic_core with a compiled module Windows has locked, and a newer one staged
    When   the staged packages are merged
    Then   pydantic_core waits in packages.pending/, the other package is merged anyway, and the
           old copy is still whole: no file of it was deleted before the locked one was found
    """
    live = tmp_path / "packages"
    old_files = ("pydantic_core/other.py", "pydantic_core/_core.pyd")
    _dist_info(live, "pydantic_core", "2.0.0", files=old_files)
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "pydantic_core", "2.1.0", files=("pydantic_core/_core.pyd",))
    _dist_info(staging, "spacy", "3.8.0")
    _lock_file(monkeypatch, "_core.pyd")

    pending = addon_manager.merge_staged_packages(staging, live, None)

    assert pending == ["pydantic-core"]
    assert (tmp_path / "packages.pending" / "pydantic_core-2.1.0.dist-info").is_dir()
    assert (tmp_path / "packages.pending" / "pydantic_core" / "_core.pyd").is_file()
    assert (live / "spacy-3.8.0.dist-info").is_dir()  # the merge went on
    for file in ("pydantic_core/__init__.py", *old_files):
        assert (live / file).is_file(), file  # the old copy is whole
    assert (live / "pydantic_core-2.0.0.dist-info" / "RECORD").is_file()
    assert not (tmp_path / "packages.trash").exists()


def test_a_replaced_copy_leaves_nothing_in_the_trash(tmp_path: Path) -> None:
    """
    Given  spaCy 3.7.0 live and 3.8.0 staged
    When   the old copy is replaced
    Then   the trash folder the old files passed through is gone again
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.7.0", files=("spacy/old.py",))
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "spacy", "3.8.0")

    addon_manager.merge_staged_packages(staging, live, None)

    assert not (tmp_path / "packages.trash").exists()


def _fail_moves(monkeypatch: pytest.MonkeyPatch, name: str, *, only_into: Path | None) -> None:
    """Make moving the file *name* fail like a full disk, into *only_into* or anywhere."""
    real_move = shutil.move

    def flaky(source: object, destination: object, *args: object, **kwargs: object) -> object:
        wanted = only_into is None or only_into in Path(str(destination)).parents
        if Path(str(source)).name == name and wanted:
            raise OSError(28, "No space left on device", str(destination))
        return real_move(source, destination, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(shutil, "move", flaky)


def test_a_package_that_cannot_be_put_anywhere_is_an_error_and_leaves_no_half_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a staged spaCy whose second file cannot be moved, into the live and the pending folder
    When   the staged packages are merged
    Then   the merge raises "merge failed" instead of reporting success, and neither folder
           keeps a half package: what moved was put back
    """
    live = tmp_path / "packages"
    live.mkdir()
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "spacy", "3.8.0", files=("spacy/new.py",))
    _fail_moves(monkeypatch, "new.py", only_into=None)

    with pytest.raises(AddonInstallError) as raised:
        addon_manager.merge_staged_packages(staging, live, None)

    assert raised.value.reason is InstallFailure.MERGE_FAILED
    assert not any(live.rglob("*.py"))
    assert not (live / "spacy-3.8.0.dist-info").exists()
    assert not (tmp_path / "packages.pending" / "spacy-3.8.0.dist-info").exists()
    assert (staging / "spacy" / "__init__.py").is_file()  # still whole where pip put it
    assert (staging / "spacy" / "new.py").is_file()


def test_a_failed_move_into_the_live_folder_falls_back_to_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a staged spaCy that cannot be moved into the live folder, but into the pending one
    When   the staged packages are merged
    Then   spaCy is reported pending and complete there, and the live folder holds no half copy
    """
    live = tmp_path / "packages"
    live.mkdir()
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "spacy", "3.8.0", files=("spacy/new.py",))
    _fail_moves(monkeypatch, "new.py", only_into=live)

    pending = addon_manager.merge_staged_packages(staging, live, None)

    assert pending == ["spacy"]
    assert not any(live.rglob("*.py"))
    assert (tmp_path / "packages.pending" / "spacy" / "new.py").is_file()
    assert (tmp_path / "packages.pending" / "spacy-3.8.0.dist-info").is_dir()


def test_a_start_up_with_nothing_redundant_reads_no_record(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an add-on folder without any copy of a bundled package
    When   the redundant copies are dropped (which every start does)
    Then   no RECORD file is read
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.8.0")

    def must_not_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a RECORD was read")

    monkeypatch.setattr(addon_manager, "_record_targets", must_not_read)

    assert addon_manager.drop_redundant_shared_packages(live, frozen_bundle) == []


def _age(folder: Path, *, hours: int) -> None:
    """Make *folder* look *hours* old."""
    then = folder.stat().st_mtime - hours * 3600
    os.utime(folder, (then, then))


def test_prepare_applies_pending_and_drops_redundant_copies(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a pending pydantic_core, a redundant numpy copy, a leftover staging folder
    When   prepare_packages_dir() runs at start-up
    Then   the pending package is in place, the redundant copy and the staging folder are gone
    """
    live = tmp_path / "packages"
    _dist_info(live, "numpy", "2.5.4")  # the bundle has it at this version
    _dist_info(live, "spacy", "3.8.0")
    _dist_info(tmp_path / "packages.pending", "pydantic_core", "2.1.0")
    _dist_info(tmp_path / "packages.staging" / "1", "spacy", "3.9.0")
    _age(tmp_path / "packages.staging" / "1", hours=24)  # a crash left it, a day ago
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: live)

    addon_manager.prepare_packages_dir()

    assert (live / "pydantic_core-2.1.0.dist-info").is_dir()
    assert not (tmp_path / "packages.pending").exists()
    assert not (live / "numpy").exists()
    assert not (live / "numpy-2.5.4.dist-info").exists()
    assert (live / "spacy-3.8.0.dist-info").is_dir()
    assert not (tmp_path / "packages.staging").exists()


def test_prepare_once_cleans_a_folder_a_single_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a pip install's entry point, which starts with no earlier cleanup
    When   the cleanup is asked for twice in one process
    Then   the retired add-on is removed by the first, and the folder is not walked again
    """
    live = tmp_path / "packages"
    _plant_package(live, "paddleocr-3.0.0", ["paddleocr/__init__.py"])
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: live)
    monkeypatch.setattr(addon_manager, "_prepared", set())
    retired = addon_manager.remove_retired_addons
    calls: list[Path] = []
    monkeypatch.setattr(
        addon_manager, "remove_retired_addons", lambda path: calls.append(path) or retired(path)
    )

    addon_manager.prepare_packages_dir_once()
    addon_manager.prepare_packages_dir_once()

    assert calls == [live]
    assert not (live / "paddleocr").exists()


def test_prepare_removes_a_retired_addon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  the packages folder holding an add-on that is no longer installed
    When   prepare_packages_dir() runs at start-up
    Then   its files are gone before anything is imported from the folder
    """
    live = tmp_path / "packages"
    _plant_package(live, "paddleocr-3.0.0", ["paddleocr/__init__.py"])
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: live)

    addon_manager.prepare_packages_dir()

    assert not (live / "paddleocr").exists()


def test_prepare_keeps_the_staging_folder_of_an_install_that_may_still_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a staging folder just made, as a second app instance's install would have, and an
           old one a crash left, and a trash folder
    When   prepare_packages_dir() runs at start-up
    Then   only the old staging folder and the trash go
    """
    live = tmp_path / "packages"
    live.mkdir()
    (tmp_path / "packages.staging" / "fresh").mkdir(parents=True)
    (tmp_path / "packages.staging" / "old").mkdir()
    _age(tmp_path / "packages.staging" / "old", hours=24)
    (tmp_path / "packages.trash" / "1").mkdir(parents=True)
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: live)

    addon_manager.prepare_packages_dir()

    assert (tmp_path / "packages.staging" / "fresh").is_dir()
    assert not (tmp_path / "packages.staging" / "old").exists()
    assert not (tmp_path / "packages.trash").exists()


def test_prepare_never_stops_the_app_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a pending-package step that fails with a permission error
    When   prepare_packages_dir() runs at start-up
    Then   it does not raise, and the clean-up steps after it still run
    """
    live = tmp_path / "packages"
    live.mkdir()
    (tmp_path / "packages.staging" / "old").mkdir(parents=True)
    _age(tmp_path / "packages.staging" / "old", hours=24)
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: live)

    def denied(*_args: object, **_kwargs: object) -> list[str]:
        raise PermissionError(13, "Zugriff verweigert")

    monkeypatch.setattr(addon_manager, "apply_pending_packages", denied)

    addon_manager.prepare_packages_dir()

    assert not (tmp_path / "packages.staging").exists()


def test_a_pending_package_is_reported_until_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a package waiting in packages.pending/
    When   the app asks whether a restart is needed
    Then   it says so, names the package, and stops saying so once the folder is empty
    """
    live = tmp_path / "packages"
    _dist_info(tmp_path / "packages.pending", "spacy", "3.8.0")
    monkeypatch.setattr(addon_manager, "get_packages_dir", lambda: live)

    assert addon_manager.addons_restart_needed() is True
    assert addon_manager.pending_addon_packages() == ["spacy"]
    assert addon_manager.addon_waits_for_restart("spaCy") is True
    assert addon_manager.addon_waits_for_restart("langchain-openai") is False

    shutil.rmtree(tmp_path / "packages.pending")
    assert addon_manager.addons_restart_needed() is False


@pytest.mark.parametrize(
    ("output", "reason"),
    [
        (
            "ERROR: Could not find a version that satisfies the requirement spacy\n"
            "ERROR: No matching distribution found for spacy\n",
            InstallFailure.NO_VERSION,
        ),
        (
            "WARNING: Retrying after connection broken by 'NewConnectionError(Failed to "
            "establish a new connection)'\nERROR: No matching distribution found for spacy\n",
            InstallFailure.OFFLINE,
        ),
        ("OSError: [Errno 28] No space left on device\n", InstallFailure.DISK_FULL),
        ("PermissionError: [WinError 5] Access is denied: 'x.pyd'\n", InstallFailure.FILE_IN_USE),
        (
            "ERROR: these package versions have conflicting dependencies (connection pool)\n",
            InstallFailure.INSTALL_FAILED,
        ),
    ],
)
def test_a_pip_failure_is_told_apart_by_what_pip_printed(
    tmp_path: Path, output: str, reason: InstallFailure
) -> None:
    """
    Given  pip failing with a typical message
    When   an add-on is installed in the installed app
    Then   the error carries the reason: a package without a wheel is not "no internet", and an
           unrelated message with the word "connection" in it is not either
    """
    proc = _FakeProc(returncode=1, stdout_lines=[output])

    with (
        patch.object(sys, "frozen", True, create=True),
        patch("subprocess.Popen", return_value=proc),
    ):
        with pytest.raises(AddonInstallError) as raised:
            install_addon_subprocess(PRIVACY_ADDON_ID, tmp_path / "pkg", lambda _: None)

    assert raised.value.reason is reason
