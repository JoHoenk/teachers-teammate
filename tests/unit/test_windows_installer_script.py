"""The Windows installer's scope mapping, uninstaller defaults and Welcome page, checked as text.

Three things a silent run cannot show: which folder a scope means (the radio buttons are never
clicked), which boxes the uninstaller's data page starts with (the page is never shown), and what
the Welcome page says and how its button is labelled (no page is shown at all). All are read out
of ``tools/packaging/windows.nsi`` here, so a change that breaks them fails on every pull request
rather than only in the Windows install job, which runs the silent paths themselves
(``tools/packaging/test_windows_installer.ps1``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def _script() -> str:
    """Return windows.nsi from the Bazel runfiles, or from the source checkout."""
    root = os.environ.get("TEST_SRCDIR", "")
    relative = os.environ.get("WINDOWS_NSI", "")
    if root and relative:
        return (Path(root) / relative).read_text(encoding="utf-8")
    path = Path(__file__).resolve().parents[2] / "tools" / "packaging" / "windows.nsi"
    return path.read_text(encoding="utf-8")


def _privacy_page() -> str:
    """Return docs/privacy.md from the Bazel runfiles, or from the checkout."""
    root = os.environ.get("TEST_SRCDIR", "")
    relative = os.environ.get("PRIVACY_PAGE", "")
    if root and relative:
        return (Path(root) / relative).read_text(encoding="utf-8")
    path = Path(__file__).resolve().parents[2] / "docs" / "privacy.md"
    return path.read_text(encoding="utf-8")


def _function(script: str, name: str) -> str:
    """Return the body of the NSIS function *name*, up to its FunctionEnd."""
    start = script.index(f"Function {name}\n")
    return script[start : script.index("FunctionEnd", start)]


def test_apply_scope_is_the_one_place_that_maps_a_scope_to_a_folder() -> None:
    """
    Given  the installer script
    When   ApplyScope and its two callers are read
    Then   the function sets both shell contexts and both default folders, and .onInit and
           ScopePageLeave both call it instead of carrying their own copy
    """
    script = _script()
    apply_scope = _function(script, "ApplyScope")

    assert "SetShellVarContext all" in apply_scope
    assert "SetShellVarContext current" in apply_scope
    assert '$INSTDIR "$PROGRAMFILES64\\TeachersTeammate"' in apply_scope
    assert '$INSTDIR "$LOCALAPPDATA\\Programs\\TeachersTeammate"' in apply_scope
    assert '${If} $DirGiven == "0"' in apply_scope

    assert "Call ApplyScope" in _function(script, ".onInit")
    assert "Call ApplyScope" in _function(script, "ScopePageLeave")

    # One copy of each default folder: a second one would be the copy this refactor removed.
    assert script.count('StrCpy $INSTDIR "$PROGRAMFILES64\\TeachersTeammate"') == 1
    assert script.count('StrCpy $INSTDIR "$LOCALAPPDATA\\Programs\\TeachersTeammate"') == 1


def test_scope_page_leave_keeps_only_the_two_dialog_reads() -> None:
    """
    Given  the installer script
    When   ScopePageLeave is read
    Then   it reads the two controls and hands the scope to ApplyScope, and the relaunch for
           administrator rights still passes /D= on
    """
    leave = _function(_script(), "ScopePageLeave")

    assert "${NSD_GetState} $ScopeDesktop $0" in leave
    assert "${NSD_GetState} $ScopeAllUsers $0" in leave
    assert 'StrCpy $InstallScope "all"' in leave
    assert 'StrCpy $InstallScope "user"' in leave
    assert "!insertmacro RelaunchElevated" in leave
    assert 'StrCpy $R1 "$R1 /D=$INSTDIR"' in leave
    assert "SetShellVarContext" not in leave
    assert "PROGRAMFILES64" not in leave
    # The install folder is ApplyScope's; the settings folder handed to the elevated copy is not.
    assert "$LOCALAPPDATA\\Programs" not in leave


def test_uninstaller_page_takes_its_defaults_from_the_defines() -> None:
    """
    Given  the installer script
    When   the two defines and the data page are read
    Then   the add-ons box is ticked and the settings box is not, and the page sets both from
           the defines rather than from a literal
    """
    script = _script()

    assert "!define UN_DEFAULT_PACKAGES ${BST_CHECKED}" in script
    assert "!define UN_DEFAULT_SETTINGS ${BST_UNCHECKED}" in script

    page = _function(script, "un.UnDataPageCreate")
    assert "${NSD_SetState} $UnDelPackages ${UN_DEFAULT_PACKAGES}" in page
    assert "${NSD_SetState} $UnDelCache ${UN_DEFAULT_SETTINGS}" in page
    # A literal state in the page would let the defines and the dialog drift apart.
    assert "${NSD_Check} $UnDelPackages" not in page
    assert "${NSD_Check} $UnDelCache" not in page


def test_silent_uninstall_asks_for_nothing() -> None:
    """
    Given  the installer script
    When   un.onInit is read
    Then   a silent run starts with both boxes unchecked, whatever the dialog's defaults are,
           and only /DELDATA and /DELSETTINGS tick one
    """
    on_init = _function(_script(), "un.onInit")

    assert "StrCpy $DoDelPackages ${BST_UNCHECKED}" in on_init
    assert "StrCpy $DoDelCache    ${BST_UNCHECKED}" in on_init
    assert '${GetOptions} $R0 "/DELDATA" $R1' in on_init
    assert '${GetOptions} $R0 "/DELSETTINGS" $R1' in on_init
    assert on_init.count("${BST_CHECKED}") == 2


def test_the_settings_box_removes_the_teachers_own_work() -> None:
    """
    Given  the installer script
    When   the uninstaller's data page and its Uninstall section are read
    Then   the box names the corrections and checks, and ticking it removes work\\ beside the
           results cache, the speed data, the marks on compared changes and what an older
           version left behind
    """
    script = _script()

    page = _function(script, "un.UnDataPageCreate")
    assert "your corrections and checks" in page

    start = script.index('Section "Uninstall"')
    uninstall = script[start : script.index("SectionEnd", start)]
    settings = uninstall[uninstall.index("$DoDelCache == ${BST_CHECKED}") :]
    for folder in ("Cache", "benchmark", "speed", "work", "compare_marks"):
        assert f'RMDir /r "$LOCALAPPDATA\\teachers_teammate\\{folder}"' in settings


def test_welcome_page_show_labels_the_button_with_what_it_does() -> None:
    """
    Given  the installer script
    When   the Welcome page's show callback and its registration are read
    Then   the callback is registered before the page, and it renames control 1 of the installer
           window to $WelcomeButton only when the run is not a fresh install
    """
    script = _script()

    registration = script.index("!define MUI_PAGE_CUSTOMFUNCTION_SHOW WelcomePageShow")
    assert registration < script.index("!insertmacro MUI_PAGE_WELCOME")

    show = _function(script, "WelcomePageShow")
    assert "GetDlgItem $0 $HWNDPARENT 1" in show
    assert 'SendMessage $0 ${WM_SETTEXT} 0 "STR:$WelcomeButton"' in show

    # A fresh install keeps "Next", because the Scope page follows: the rename is guarded.
    assert '${If} $Mode != "fresh"' in show
    assert '${If} $Mode == "fresh"' not in show
    assert show.index('${If} $Mode != "fresh"') < show.index("${WM_SETTEXT}")

    # The shield is the one Windows puts on a button that raises a UAC prompt.
    assert "!define BCM_SETSHIELD 0x160C" in script
    assert "SendMessage $0 ${BCM_SETSHIELD} 0 1" in show
    assert show.index('${If} $NeedElevation == "1"') < show.index("${BCM_SETSHIELD}")


def test_the_elevation_sentence_names_the_button_the_page_shows() -> None:
    """
    Given  the installer script, whose Welcome button says Update, Reinstall or Install
    When   the sentence about administrator rights is read
    Then   it names the button by $WelcomeButton, not by a fixed word that may not be on the page
    """
    script = _script()

    assert "Windows asks for administrator rights when you click $WelcomeButton." in script
    assert "when you click Update" not in script


def test_update_check_is_asked_on_the_scope_page_not_on_the_welcome_page() -> None:
    """
    Given  the installer script
    When   the Welcome text, the scope page and the install section are read
    Then   the Welcome text carries no update-check note, the scope page has the check box and
           the link to the policy, "off" travels to the elevated copy together with the folder of
           the teacher's settings, the app writes the box either way, and /NOUPDATECHECK and
           /SETTINGSDIR= are read on the command line
    """
    script = _script()

    assert "PRIVACY_NOTE" not in script
    assert "GitHub" not in _function(script, ".onInit")

    page = _function(script, "ScopePageCreate")
    assert "Check for updates when the app starts" in page
    assert "Nothing about you or your documents is sent." in page
    assert "${NSD_OnClick} $PrivacyLink OpenPrivacyPage" in page
    assert 'ExecShell "open" "${PRIVACY_URL}"' in _function(script, "OpenPrivacyPage")

    leave = _function(script, "ScopePageLeave")
    assert "${NSD_GetState} $ScopeUpdates $0" in leave

    init = _function(script, ".onInit")
    assert '${GetOptions} $R0 "/NOUPDATECHECK" $R1' in init
    assert '${GetOptions} $R0 "/SETTINGSDIR=" $R1' in init

    install = script[script.index('Section "Core (required)"') :]
    assert '--_set_update_check $R0 "$CmdSettingsDir"' in install
    assert 'StrCpy $R0 "on"' in install
    assert 'StrCpy $R0 "off"' in install
    assert "FileWrite" not in install


def test_the_elevated_copy_is_told_the_settings_folder_whatever_the_box_says() -> None:
    """
    Given  the installer script
    When   the relaunch of ScopePageLeave is read
    Then   it always names the teacher's settings folder, and only a box that is off adds
           /NOUPDATECHECK: otherwise an administrator's account writes "on" into its own folder,
           and a kept "off" stays off although the box was ticked
    """
    leave = _function(_script(), "ScopePageLeave")

    folder_line = next(line for line in leave.splitlines() if "/SETTINGSDIR=" in line)
    assert "$LOCALAPPDATA" in folder_line
    assert "/NOUPDATECHECK" not in folder_line
    assert leave.index(folder_line) < leave.index("!insertmacro RelaunchElevated")

    # "off" travels behind the folder, so the elevated copy reads both.
    off = leave.index('${If} $CheckUpdates == "0"')
    assert off > leave.index(folder_line)
    assert 'StrCpy $R1 "$R1 /NOUPDATECHECK"' in leave[off:]


def test_the_privacy_link_opens_a_page_that_exists() -> None:
    """
    Given  the installer script and the privacy page
    When   PRIVACY_URL is read
    Then   it points at docs/privacy.md and the page says what the update check sends
    """
    url = re.search(r'!define PRIVACY_URL "([^"]+)"', _script())
    assert url is not None
    assert url.group(1).endswith("/docs/privacy.md")
    assert "**Update check:**" in _privacy_page()


def test_the_relaunch_passes_its_arguments_as_a_register() -> None:
    """
    Given  the installer script
    When   the RelaunchElevated macro and its callers are read
    Then   the arguments reach ShellExecuteW as the register $R1 and never inside a quoted
           System string, whose first inner quote would end it (/SETTINGSDIR="..." has two)
    """
    script = _script()
    start = script.index("!macro RelaunchElevated")
    macro = script[start : script.index("!macroend", start)]

    assert "w R1," in macro
    assert '"${ARGS}"' not in macro
    assert "ARGS" not in macro
    assert '!insertmacro RelaunchElevated "' not in script
