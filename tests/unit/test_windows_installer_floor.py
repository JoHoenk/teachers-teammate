"""The Windows installer's version gate, checked as text (no CI runner is old enough to run it)."""

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


def test_oninit_refuses_windows_before_1809_and_exits_15_when_silent() -> None:
    """
    Given  the installer script
    When   its .onInit is read
    Then   it includes WinVer.nsh, tests build 17763 and sets error level 15 on both paths
    """
    script = _script()
    on_init = script[script.index("Function .onInit") :]
    on_init = on_init[: on_init.index("FunctionEnd")]

    assert '!include "WinVer.nsh"' in script
    assert "${IfNot} ${AtLeastBuild} 17763" in on_init
    assert "${If} ${Silent}" in on_init
    assert on_init.count("SetErrorLevel 15") == 2
    assert "MessageBox" in on_init
