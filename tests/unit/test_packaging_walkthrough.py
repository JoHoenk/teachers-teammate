"""Every packaging test walks through the installed app, after that copy's smoke test.

The walkthrough (``tests/e2e/walkthrough.py``) is the only check that opens a bundle's real
window and reads a page with the models the bundle carries, so an install job that skipped it
would pass on an app nobody started. The three scripts are read as text here; the job itself
proves the rest.
"""

from __future__ import annotations

import os
from pathlib import Path


def _script(env: str, name: str) -> str:
    """Return a packaging script from the Bazel runfiles, or from the source checkout."""
    root = os.environ.get("TEST_SRCDIR", "")
    relative = os.environ.get(env, "")
    if root and relative:
        return (Path(root) / relative).read_text(encoding="utf-8")
    path = Path(__file__).resolve().parents[2] / "tools" / "packaging" / name
    return path.read_text(encoding="utf-8")


def test_the_deb_container_walks_through_the_installed_app() -> None:
    """
    Given  the .deb install test, which installs the built package in a clean container
    When   its mounts, its packages and the order of its two app runs are read
    Then   the walkthrough is mounted in, xvfb with its xauth and a font are installed, and the
           walkthrough runs after the smoke test, the platform plugin check and the launcher
           check, its result beside the smoke result
    """
    script = _script("DEB_INSTALL_SCRIPT", "test_deb_install.sh")

    assert "${ROOT}/tests/e2e/walkthrough.py:/walkthrough.py:ro" in script
    for package in ("xvfb", "xauth", "fonts-dejavu-core"):
        assert package in script

    smoke = script.index("--_smoke_test /smoke/smoke.json")
    walk = script.index("--_run_script /walkthrough.py /smoke/e2e.json")
    assert smoke < walk
    # A missing xcb library stops Qt before the script writes any result: the two checks that
    # name the cause run first, so the walk's bare exit code is not all the log says.
    assert script.index("libqxcb.so") < walk
    assert script.index("/check_desktop_entry.py") < walk
    # The pictures go beside the result, which is what the job uploads.
    assert "--screenshots /smoke/e2e-shots" in script[walk:]


def test_the_macos_install_walks_through_the_replaced_bundle() -> None:
    """
    Given  the dmg install test, which replaces the installed bundle with the built one
    When   the runs of the installed app are read
    Then   the walkthrough runs after the smoke test, through the same bundle's executable and
           with the script from the repository, which stays outside the .app
    """
    script = _script("MACOS_INSTALL_SCRIPT", "test_macos_install.sh")

    exe = '"${APP}/Contents/MacOS/teachers-teammate"'
    smoke = script.index(f"{exe} --_smoke_test")
    walk = script.index(f"{exe} --_run_script")
    assert smoke < walk
    assert '"${ROOT}/tests/e2e/walkthrough.py"' in script[walk:]
    assert '--screenshots "${E2E_SHOTS}"' in script[walk:]


def test_the_windows_installer_walks_through_the_installed_copy() -> None:
    """
    Given  the installer test, which installs, updates and uninstalls the setup
    When   the calls of its smoke test and its walkthrough are read
    Then   the fresh per-user copy is walked through right after its smoke test and no other
           copy is, the walkthrough runs the repository's script with the installed exe, and it
           passes only on the result's top-level "ok", which it reads as JSON
    """
    script = _script("WINDOWS_INSTALLER_SCRIPT", "test_windows_installer.ps1")

    assert "Assert-SmokeTestPasses $UserDir\nAssert-WalkthroughPasses $UserDir" in script
    assert script.count("Assert-WalkthroughPasses") == 2  # the definition and the one call

    body = script[script.index("function Assert-WalkthroughPasses") :]
    body = body[: body.index("\n}\n")]
    assert "'--_run_script'" in body
    assert "--screenshots" in body
    assert "ConvertFrom-Json" in body
    assert "Join-Path $PSScriptRoot '..\\..\\tests\\e2e\\walkthrough.py'" in script
