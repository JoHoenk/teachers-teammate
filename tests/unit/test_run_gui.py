"""The entry point of the installed app: which start-ups clean the add-on folder."""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
from unittest.mock import MagicMock, patch

import pytest


def _script() -> Path:
    """Return run_gui.py from the Bazel runfiles, or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace / "tools" / "build" / "run_gui.py"
    return Path(__file__).resolve().parents[2] / "tools" / "build" / "run_gui.py"


def _pip_modules(pip_main: MagicMock) -> dict[str, MagicMock]:
    """Return stand-ins for the pip modules the spaCy download mode imports."""
    names = ("pip", "pip._internal", "pip._internal.cli", "pip._internal.cli.main")
    modules = {name: MagicMock() for name in names}
    modules["pip._internal.cli.main"].main = pip_main
    return modules


@pytest.mark.parametrize(
    "mode",
    [
        "--_smoke_test",
        "--_addon_smoke_test",
        "--_forget_keys",
        "--_set_update_check",
        "--_spacy_download_mode",
    ],
)
def test_a_helper_mode_leaves_the_add_on_folder_alone(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  the app started as a helper: a smoke test, the key cleaner of the uninstaller, the
           update-check switch of the installer, or the spaCy model download that an install
           runs with its own staging folder
    When   the entry point dispatches it
    Then   the folder is not prepared (a helper must not delete the staging folder of the
           install that started it, nor apply packages the app has loaded), but it is injected
    """
    prepare, inject = MagicMock(), MagicMock()
    frozen_modes = MagicMock()
    frozen_modes.run_smoke_test.return_value = 0
    frozen_modes.run_addon_smoke_test.return_value = 0
    frozen_modes.forget_keys.return_value = 0
    frozen_modes.set_update_check.return_value = 0
    pip_main = MagicMock(return_value=0)
    monkeypatch.setattr(sys, "argv", ["run_gui.py", mode, str(tmp_path / "arg"), "--extra"])

    with (
        patch("teachers_teammate.infrastructure.addon_manager.prepare_packages_dir", prepare),
        patch("teachers_teammate.infrastructure.addon_manager.inject_packages_dir", inject),
        patch.dict(sys.modules, {"teachers_teammate.frozen_modes": frozen_modes}),
        patch.dict(sys.modules, _pip_modules(pip_main)),
        pytest.raises(SystemExit) as exited,
    ):
        runpy.run_path(str(_script()), run_name="__main__")

    assert exited.value.code == 0
    prepare.assert_not_called()
    inject.assert_called_once()
