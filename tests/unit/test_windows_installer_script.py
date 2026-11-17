"""The Windows installer's scope mapping and uninstaller defaults, checked as text.

Two things a silent run cannot show: which folder a scope means (the radio buttons are never
clicked) and which boxes the uninstaller's data page starts with (the page is never shown).
Both are read out of ``tools/packaging/windows.nsi`` here, so a change that breaks them fails
on every pull request rather than only in the Windows install job, which runs the silent paths
themselves (``tools/packaging/test_windows_installer.ps1``).
"""

from __future__ import annotations

import os
from pathlib import Path


def _script() -> str:
    """Return windows.nsi from the Bazel runfiles, or from the source checkout."""
    root = os.environ.get("TEST_SRCDIR", "")
    relative = os.environ.get("WINDOWS_NSI", "")
    if root and relative:
        return (Path(root) / relative).read_text(encoding="utf-8")
    path = Path(__file__).resolve().parents[2] / "tools" / "packaging" / "windows.nsi"
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
    assert "LOCALAPPDATA" not in leave


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
