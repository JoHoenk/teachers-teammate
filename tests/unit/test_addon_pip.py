"""Unit tests for teachers_teammate.infrastructure.addon_pip."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import logging
import shutil
import subprocess
import sys

import pytest

from teachers_teammate.exceptions import AddonInstallError, AddonUpdateError, InstallFailure
from teachers_teammate.domain.local_models import PRIVACY_ADDON_ID
from teachers_teammate.infrastructure import addon_folder, addon_list, addon_pip
from teachers_teammate.infrastructure.addon_pip import (
    download_spacy_model_subprocess,
    install_addon_subprocess,
)
from teachers_teammate.messages import MessageKey
from teachers_teammate.infrastructure.spacy_models import spacy_model_url
from teachers_teammate.infrastructure.storage_root import app_data_folder


#: The add-on of the OpenAI provider package: an install that needs the network.
ADDON_LLM_OPENAI = "llm_openai"


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

    with patch.object(sys, "frozen", True, create=True):
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


def test_a_failed_pip_run_writes_its_last_lines_to_the_log(tmp_path: Path, caplog) -> None:
    """
    Given  pip that stops with a non-zero code after printing more lines than the log keeps
    When   install_addon_subprocess() is called
    Then   the log holds the command and the last twenty lines, since the installer's pane closes
    """

    class _FakeProc:
        returncode = 1
        stdout = iter([f"line {number}\n" for number in range(30)])

        def wait(self) -> None:
            pass

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    with caplog.at_level(logging.WARNING, logger=addon_pip.__name__):
        with patch("subprocess.Popen", return_value=_FakeProc()):
            with pytest.raises(AddonInstallError):
                install_addon_subprocess(PRIVACY_ADDON_ID, tmp_path / "pkg", lambda _: None)

    written = [record.getMessage() for record in caplog.records]
    assert written[0].startswith("pip failed: ")
    assert written[1:] == [f"line {number}" for number in range(10, 30)]


def test_install_addon_subprocess_raises_oserror_on_bad_target(tmp_path: Path) -> None:
    """
    Given  the target packages directory cannot be created (its parent is a file)
    When   install_addon_subprocess() is called
    Then   OSError or PermissionError propagates
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("")

    with pytest.raises((OSError, PermissionError)):
        install_addon_subprocess(PRIVACY_ADDON_ID, blocker / "sub" / "pkg", lambda _: None)


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
    Given  the target packages directory cannot be created (its parent is a file)
    When   download_spacy_model_subprocess() is called
    Then   OSError or PermissionError propagates
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("")

    with pytest.raises((OSError, PermissionError)):
        download_spacy_model_subprocess("en_core_web_sm", blocker / "sub" / "pkg", lambda _: None)


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

    with patch.object(sys, "platform", "win32"):
        with patch("subprocess.Popen", side_effect=fake_popen):
            install_addon_subprocess(PRIVACY_ADDON_ID, tmp_path / "pkg", lambda _: None)

    assert captured_kwargs[0]["creationflags"] == 0x08000000


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
    pins = bundle / addon_list.ADDON_CONSTRAINTS_RELATIVE
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

    assert addon_pip.pip_policy_arguments() == []


def test_pip_policy_of_the_installed_app(frozen_bundle: Path) -> None:
    """
    Given  the installed app
    When   the pip policy is asked for
    Then   it takes wheels only, skips the cache, asks pip for no version notice
           ("run … -m pip install --upgrade pip") and passes the bundle's constraints; it does
           not upgrade, because pip installs into an empty staging folder
    """
    arguments = addon_pip.pip_policy_arguments()

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

    arguments = addon_pip.pip_policy_arguments()

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

    addon_pip.install_packages_subprocess(["six"], tmp_path / "pkg", lambda _: None)

    assert "-c" not in captured[0]
    assert "--only-binary" not in captured[0]


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
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    installs: list[tuple[list[str], Path]] = []

    def fake_install(packages: list[str], target: Path, _cb: object, **_kw: object) -> None:
        installs.append((packages, target))
        shutil.rmtree(pkg_dir / "numpy-2.4.0.dist-info")
        _dist_info(pkg_dir, "numpy", "2.5.4")

    monkeypatch.setattr(addon_pip, "install_packages_subprocess", fake_install)

    # The update evicts the add-ons' modules (clear_stale_modules); restore them afterwards so
    # a later test file still sees the real langchain_core (one pytest process for everything).
    with patch.dict(sys.modules):
        addon_pip.update_installed_addons(lambda _line: None)

    assert installs == [(["spacy>=3.7"], pkg_dir)]
    assert str(pkg_dir) in sys.path
    assert addon_folder.addons_outdated() is False


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
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_pip, "install_packages_subprocess", lambda *_a, **_k: None)

    with patch.dict(sys.modules):
        addon_pip.update_installed_addons(lambda _line: None)

    assert not (pkg_dir / "numpy-2.4.0.dist-info").exists()
    assert addon_folder.addons_outdated() is False


# ── Add-ons built for another Python ─────────────────────────────────────────


@pytest.fixture(name="python_314")
def _python_314(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the installed app runs CPython 3.14 (the version is patched, not read)."""
    monkeypatch.setattr(addon_folder, "_running_python", lambda: (3, 14))
    monkeypatch.setattr(sys, "frozen", True, raising=False)


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
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_pip, "install_packages_subprocess", lambda *_a, **_k: None)

    with patch.dict(sys.modules):
        addon_pip.update_installed_addons(lambda _line: None)

    assert not (pkg_dir / "numpy-2.4.0.dist-info").exists()
    assert addon_folder.addons_outdated() is False


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
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_pip, "install_packages_subprocess", lambda *_a, **_k: None)

    with patch.dict(sys.modules):
        addon_pip.update_installed_addons(lambda _line: None)

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
    monkeypatch.setattr(addon_folder, "_running_python", lambda: (3, 14))
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert addon_folder.foreign_python_packages(pkg_dir) == []
    assert addon_folder.addons_outdated() is False
    with pytest.raises(RuntimeError) as raised:
        addon_pip.update_installed_addons(lambda _line: None)

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
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_pip, "inject_packages_dir", lambda: None)

    def _install(packages: list[str], _target: Path, _cb: object, **_kw: object) -> None:
        if packages[0].startswith("langchain-core"):
            raise RuntimeError("no matching distribution")

    monkeypatch.setattr(addon_pip, "install_packages_subprocess", _install)

    with pytest.raises(AddonUpdateError, match="no matching distribution") as raised:
        addon_pip.update_installed_addons(lambda _line: None)

    assert [requirement for requirement, _error in raised.value.errors] == ["langchain-core>=1.4.7"]

    assert addon_folder.foreign_python_packages(pkg_dir) == []


def test_a_failed_update_removes_the_add_on_it_could_not_reinstall(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a folder with an outdated numpy (the bundle pins another one) and spaCy, whose
           reinstall fails
    When   the add-ons are updated
    Then   spaCy is removed by its RECORD together with the outdated numpy, so the folder is not
           outdated and holds no old spaCy that would run against the bundle's numpy; the failure
           names the requirement
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "numpy", "2.4.0", files=("numpy/core/a.py",))
    _dist_info(pkg_dir, "spacy", "3.8.0", files=("spacy/pipeline.py",))
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_pip, "inject_packages_dir", lambda: None)
    monkeypatch.setattr(addon_pip, "addon_constraints_path", lambda: frozen_bundle)

    def _install(packages: list[str], _target: Path, _cb: object, **_kw: object) -> None:
        if packages[0].startswith("spacy"):
            raise RuntimeError("no matching distribution")
        _dist_info(pkg_dir, "numpy", "2.5.4", files=("numpy/core/a.py",))

    monkeypatch.setattr(addon_pip, "install_packages_subprocess", _install)

    with pytest.raises(AddonUpdateError) as raised:
        addon_pip.update_installed_addons(lambda _line: None)

    assert [requirement for requirement, _error in raised.value.errors] == ["spacy>=3.7"]
    assert raised.value.gone == ["spacy>=3.7"]
    assert not (pkg_dir / "spacy-3.8.0.dist-info").exists()
    assert not (pkg_dir / "spacy" / "pipeline.py").exists()
    assert addon_folder.addon_in_folder("spacy", pkg_dir) is False
    assert addon_folder.addons_outdated() is False


def test_an_update_that_failed_for_a_reason_that_can_pass_keeps_the_old_copy(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a folder with an outdated numpy (the bundle pins another one) and spaCy, whose
           reinstall fails because the computer is offline
    When   the add-ons are updated
    Then   spaCy and the outdated numpy it needs stay, nothing is reported as gone, and the
           folder is still outdated so the update can be repeated
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "numpy", "2.4.0", files=("numpy/core/a.py",))
    _dist_info(pkg_dir, "spacy", "3.8.0", files=("spacy/pipeline.py",))
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_pip, "inject_packages_dir", lambda: None)
    monkeypatch.setattr(addon_pip, "addon_constraints_path", lambda: frozen_bundle)

    def _install(_packages: list[str], _target: Path, _cb: object, **_kw: object) -> None:
        raise AddonInstallError(InstallFailure.OFFLINE)

    monkeypatch.setattr(addon_pip, "install_packages_subprocess", _install)

    with pytest.raises(AddonUpdateError) as raised:
        addon_pip.update_installed_addons(lambda _line: None)

    assert raised.value.gone == []
    assert (pkg_dir / "spacy-3.8.0.dist-info").is_dir()
    assert (pkg_dir / "numpy-2.4.0.dist-info").is_dir()
    assert addon_folder.addons_outdated() is True


def test_a_failed_update_does_not_remove_a_copy_another_requirement_of_the_name_installed(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  two add-on requirements for spaCy, where the first reinstall succeeds and the second
           fails for good
    When   the add-ons are updated
    Then   spaCy stays in the folder: the copy the first install wrote is not removed by the
           clean-up of the second
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0", files=("spacy/pipeline.py",))
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_pip, "inject_packages_dir", lambda: None)
    monkeypatch.setattr(addon_pip, "addon_constraints_path", lambda: frozen_bundle)
    monkeypatch.setattr(
        addon_pip,
        "installed_addon_requirements",
        lambda _pkg_dir=None: ["spacy>=3.7", "spacy>=3.7,<4"],
    )

    def _install(packages: list[str], _target: Path, _cb: object, **_kw: object) -> None:
        if packages[0] != "spacy>=3.7":
            raise RuntimeError("no matching distribution")

    monkeypatch.setattr(addon_pip, "install_packages_subprocess", _install)

    with pytest.raises(AddonUpdateError) as raised:
        addon_pip.update_installed_addons(lambda _line: None)

    assert raised.value.gone == []
    assert (pkg_dir / "spacy-3.8.0.dist-info").is_dir()
    assert (pkg_dir / "spacy" / "pipeline.py").is_file()


def test_a_failed_update_keeps_the_fresh_copy_another_add_on_brought(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a folder with langchain-core 1.4.7 and langchain-openai, where the reinstall of
           langchain-core fails and the one of langchain-openai brings langchain-core 1.5.0
    When   the add-ons are updated
    Then   the old langchain-core goes, the fresh one stays with its files, and the provider that
           updated is not broken by the clean-up of the one that failed
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "langchain_core", "1.4.7", files=("langchain_core/old.py",))
    _dist_info(pkg_dir, "langchain_openai", "0.2.0")
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_pip, "inject_packages_dir", lambda: None)
    monkeypatch.setattr(addon_pip, "addon_constraints_path", lambda: frozen_bundle)

    def _install(packages: list[str], _target: Path, _cb: object, **_kw: object) -> None:
        if packages[0].startswith("langchain-core"):
            raise RuntimeError("no matching distribution")
        _dist_info(pkg_dir, "langchain_core", "1.5.0", files=("langchain_core/new.py",))
        _dist_info(pkg_dir, "langchain_openai", "0.3.0")

    monkeypatch.setattr(addon_pip, "install_packages_subprocess", _install)

    with pytest.raises(AddonUpdateError) as raised:
        addon_pip.update_installed_addons(lambda _line: None)

    assert [requirement for requirement, _error in raised.value.errors] == ["langchain-core>=1.4.7"]
    assert not (pkg_dir / "langchain_core-1.4.7.dist-info").exists()
    assert not (pkg_dir / "langchain_core" / "old.py").exists()
    assert (pkg_dir / "langchain_core-1.5.0.dist-info").is_dir()
    assert (pkg_dir / "langchain_core" / "new.py").is_file()
    assert (pkg_dir / "langchain_openai-0.3.0.dist-info").is_dir()


def test_a_failed_update_that_could_not_remove_the_copy_does_not_claim_it_is_gone(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a folder whose spaCy has no readable RECORD, and a reinstall that fails
    When   the add-ons are updated
    Then   the old copy stays and the error does not list spaCy as gone, so the teacher is not
           told something false
    """
    pkg_dir = tmp_path / "packages"
    (pkg_dir / "spacy-3.8.0.dist-info").mkdir(parents=True)
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_pip, "inject_packages_dir", lambda: None)
    monkeypatch.setattr(addon_pip, "addon_constraints_path", lambda: frozen_bundle)

    def _install(_packages: list[str], _target: Path, _cb: object, **_kw: object) -> None:
        raise RuntimeError("offline")

    monkeypatch.setattr(addon_pip, "install_packages_subprocess", _install)

    with pytest.raises(AddonUpdateError) as raised:
        addon_pip.update_installed_addons(lambda _line: None)

    assert raised.value.gone == []
    assert (pkg_dir / "spacy-3.8.0.dist-info").is_dir()


def test_an_add_on_that_updates_stays_in_the_folder(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a folder with spaCy, whose reinstall succeeds with a new version
    When   the add-ons are updated
    Then   the fresh copy stays and no failure is raised, so only what failed is removed
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0")
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_pip, "inject_packages_dir", lambda: None)
    monkeypatch.setattr(addon_pip, "addon_constraints_path", lambda: frozen_bundle)

    def _install(_packages: list[str], _target: Path, _cb: object, **_kw: object) -> None:
        _dist_info(pkg_dir, "spacy", "3.8.7")

    monkeypatch.setattr(addon_pip, "install_packages_subprocess", _install)

    addon_pip.update_installed_addons(lambda _line: None)

    assert addon_folder.addon_in_folder("spacy", pkg_dir) is True
    assert addon_folder.addons_outdated() is False


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
