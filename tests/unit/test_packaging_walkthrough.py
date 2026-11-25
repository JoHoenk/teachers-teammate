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


def test_the_deb_container_walks_on_wayland_when_the_bundle_carries_it() -> None:
    """
    Given  the .deb install test, which walks the installed app on the xcb platform plugin
    When   its Wayland part is read
    Then   a second walk runs under a headless compositor, after the xcb one, with its own
           result, and only when the bundle carries the Wayland platform plugin
    """
    script = _script("DEB_INSTALL_SCRIPT", "test_deb_install.sh")

    xcb_walk = script.index("--_run_script /walkthrough.py /smoke/e2e.json")
    wayland_walk = script.index("--_run_script /walkthrough.py /smoke/e2e-wayland.json")
    assert xcb_walk < wayland_walk
    assert "--screenshots /smoke/e2e-wayland-shots" in script[wayland_walk:]
    # The plugin is what the .deb claims by carrying it; without it there is nothing to walk.
    claim = script.index("libqwayland")
    assert claim < wayland_walk
    assert "the Wayland walk is skipped" in script
    assert "weston" in script
    assert "QT_QPA_PLATFORM=wayland" in script
    # The window must have been on Wayland, not on a plugin Qt fell back to.
    assert r"result[\"platform\"] == \"wayland\"" in script[wayland_walk:]


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


def test_the_deb_container_opens_the_old_versions_data() -> None:
    """
    Given  the .deb install test, which installs the newest published release over its own files
    When   the old version's data and the walk over it are read
    Then   the old version leaves settings and an add-on folder its own pip filled before the
           update, and the new app is walked through that folder after it, with its own result
    """
    script = _script("DEB_INSTALL_SCRIPT", "test_deb_install.sh")

    fixture = script.index("/tmp/old-data/ocr.toml")
    addon = script.index("--_pip_install_mode install")
    update = script.index("--allow-downgrades")
    walk = script.index("--after-upgrade /tmp/old-data --screenshots /smoke/e2e-upgrade-shots")
    assert fixture < addon < update < walk
    # The add-on goes where the app looks for it, not into the old folder.
    assert '/teachers_teammate/packages" --prefer-binary spacy' in script[addon:]
    # A failed install of the old add-on stops the script (`set -e`): no warning, no skipped walk.
    assert "set -euo pipefail" in script[script.index("bash -c") : fixture]
    assert "installed no add-on" not in script


def test_the_macos_install_opens_the_old_versions_data() -> None:
    """
    Given  the dmg install test, which replaces an installed release with the built bundle
    When   the old version's data and the walk over it are read
    Then   the old bundle leaves settings and an add-on folder its own pip filled before the
           replacement, and the new bundle is walked through that folder after its own walk
    """
    script = _script("MACOS_INSTALL_SCRIPT", "test_macos_install.sh")

    old_app = script.index('cp -R "${old_app}" "${APP}"')
    fixture = script.index('"${OLD_DATA}/ocr.toml"')
    addon = script.index("--_pip_install_mode install")
    walk = script.index('--after-upgrade "${OLD_DATA}" --screenshots "${E2E_UPGRADE_SHOTS}"')
    assert old_app < fixture < addon < walk
    assert '--target "${HOME}/Library/Application Support/teachers_teammate/packages"' in script
    assert "set -euo pipefail" in script[:fixture]
    assert "installed no add-on" not in script
    # After the ordinary walk, so a failure in either names its own result.
    assert script.index('"${E2E_OUT}" --screenshots "${E2E_SHOTS}"') < walk


def test_the_windows_installer_opens_the_old_versions_data() -> None:
    """
    Given  the installer test, which updates over v0.5.6 and over the newest published release
    When   both update steps and the upgrade walk are read
    Then   each step lets the version it replaces leave settings and an add-on folder, the new
           copy is walked through that folder before the uninstaller, and the settings file is
           written as ASCII, which keeps the byte order mark away
    """
    script = _script("WINDOWS_INSTALLER_SCRIPT", "test_windows_installer.ps1")

    # The definition and one call per update step.
    assert script.count("Assert-UpgradeWalkthroughPasses") == 3
    body = script[script.index("function Assert-UpgradeWalkthroughPasses") :]
    body = body[: body.index("\n}\n")]
    assert "'--after-upgrade'" in body
    assert "'--_run_script'" in body

    assert script.count("Write-OldVersionData $OldData") == 2
    assert script.count("Install-OldVersionAddon $AllUsersDir") == 2
    assert "'--_pip_install_mode', 'install', '--target'" in script
    assert "'--prefer-binary', 'spacy'" in script
    assert "'the old version installs the name-detection add-on'" in script
    assert "installed no add-on" not in script
    assert (
        "Set-Content -LiteralPath (Join-Path $Root 'ocr.toml') -Value $lines -Encoding ascii"
        in (script)
    )
    # Each call comes before the uninstaller that follows it, which removes the data.
    call = "Assert-UpgradeWalkthroughPasses $AllUsersDir $OldData"
    start = 0
    while (found := script.find(call, start)) != -1:
        assert script.index("Invoke-Uninstaller", found) > found
        start = found + len(call)


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
