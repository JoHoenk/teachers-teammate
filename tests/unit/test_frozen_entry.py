"""The entry point of the installed app: which start-ups clean the add-on folder."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate import frozen_entry


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
    monkeypatch.setattr(sys, "argv", ["teachers-teammate", mode, str(tmp_path / "arg"), "--extra"])

    with (
        patch("teachers_teammate.infrastructure.addon_staging.prepare_packages_dir", prepare),
        patch("teachers_teammate.infrastructure.addon_imports.inject_packages_dir", inject),
        patch.dict(sys.modules, {"teachers_teammate.frozen_modes": frozen_modes}),
        patch.dict(sys.modules, _pip_modules(pip_main)),
    ):
        assert frozen_entry.main() == 0

    prepare.assert_not_called()
    inject.assert_called_once()


def test_the_script_mode_does_not_touch_the_add_on_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  the app started with a script to run
    When   the entry point dispatches it
    Then   the add-on folder is neither prepared nor injected: the script runs on the bundle
           alone, as its own isolation does not reach the teacher's folder
    """
    prepare, inject = MagicMock(), MagicMock()
    frozen_modes = MagicMock()
    frozen_modes.run_script.return_value = 0
    monkeypatch.setattr(sys, "argv", ["teachers-teammate", "--_run_script", "walk.py", "e2e.json"])

    with (
        patch("teachers_teammate.infrastructure.addon_staging.prepare_packages_dir", prepare),
        patch("teachers_teammate.infrastructure.addon_imports.inject_packages_dir", inject),
        patch.dict(sys.modules, {"teachers_teammate.frozen_modes": frozen_modes}),
    ):
        assert frozen_entry.main() == 0

    prepare.assert_not_called()
    inject.assert_not_called()


def test_the_script_mode_hands_its_arguments_to_the_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  the app started with a script, a result file and two arguments of the script's own
    When   the entry point dispatches it
    Then   the runner gets the script and the result file as paths and the rest as a list
    """
    frozen_modes = MagicMock()
    frozen_modes.run_script.return_value = 0
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "teachers-teammate",
            "--_run_script",
            "walk.py",
            "e2e.json",
            "--screenshots",
            "shots",
        ],
    )

    with (
        patch("teachers_teammate.infrastructure.addon_staging.prepare_packages_dir", MagicMock()),
        patch("teachers_teammate.infrastructure.addon_imports.inject_packages_dir", MagicMock()),
        patch.dict(sys.modules, {"teachers_teammate.frozen_modes": frozen_modes}),
    ):
        assert frozen_entry.main() == 0

    frozen_modes.run_script.assert_called_once_with(
        Path("walk.py"), Path("e2e.json"), ["--screenshots", "shots"]
    )


def test_the_script_mode_without_a_result_file_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  the app started with a script but no result file to write
    When   the entry point dispatches it
    Then   it stops with 2 without running anything
    """
    frozen_modes = MagicMock()
    monkeypatch.setattr(sys, "argv", ["teachers-teammate", "--_run_script", "walk.py"])

    with (
        patch("teachers_teammate.infrastructure.addon_staging.prepare_packages_dir", MagicMock()),
        patch("teachers_teammate.infrastructure.addon_imports.inject_packages_dir", MagicMock()),
        patch.dict(sys.modules, {"teachers_teammate.frozen_modes": frozen_modes}),
    ):
        assert frozen_entry.main() == 2

    frozen_modes.run_script.assert_not_called()


def _gui_start(order: list[str], *, import_fails: bool = False) -> dict[str, object]:
    """Return the modules a normal start imports, as stand-ins that note when they are used."""
    import types  # noqa: PLC0415

    widgets = MagicMock()
    widgets.QApplication.instance.return_value = None
    widgets.QApplication.side_effect = lambda *_args: order.append("QApplication")
    modules: dict[str, object] = {
        "PySide6.QtCore": MagicMock(),
        "PySide6.QtGui": MagicMock(),
        "PySide6.QtWidgets": widgets,
    }
    if not import_fails:
        gui = types.ModuleType("teachers_teammate.gui")
        gui.main_gui = lambda **_kw: order.append("main_gui")
        modules["teachers_teammate.gui"] = gui
    else:
        modules["teachers_teammate.gui"] = None  # an import of it raises ImportError
    return modules


def test_a_normal_start_logs_before_it_imports_the_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  the installed app, started normally
    When   the entry point shows its splash and starts the GUI
    Then   Qt's messages are caught before the QApplication exists, and the log file is started
           before the GUI's heavy imports, so an error in either reaches the file
    """
    order: list[str] = []
    monkeypatch.setattr(sys, "argv", ["teachers-teammate"])

    with (
        patch.dict(sys.modules, _gui_start(order)),
        patch("teachers_teammate.infrastructure.qt_log.install", lambda: order.append("qt_log")),
        patch(
            "teachers_teammate.infrastructure.logging_setup.start_logging",
            lambda: order.append("start_logging"),
        ),
        patch("teachers_teammate.infrastructure.addon_staging.prepare_packages_dir", MagicMock()),
        patch("teachers_teammate.infrastructure.addon_imports.inject_packages_dir", MagicMock()),
    ):
        assert frozen_entry.main() == 0

    assert order == ["qt_log", "QApplication", "start_logging", "main_gui"]


def test_a_gui_that_cannot_be_imported_is_still_after_the_log_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  a bundle whose GUI import fails
    When   the entry point starts
    Then   the log file was started first, and the error goes on to the hook that writes it
    """
    order: list[str] = []
    monkeypatch.setattr(sys, "argv", ["teachers-teammate"])

    with (
        patch.dict(sys.modules, _gui_start(order, import_fails=True)),
        patch("teachers_teammate.infrastructure.qt_log.install", lambda: order.append("qt_log")),
        patch(
            "teachers_teammate.infrastructure.logging_setup.start_logging",
            lambda: order.append("start_logging"),
        ),
        patch("teachers_teammate.infrastructure.addon_staging.prepare_packages_dir", MagicMock()),
        patch("teachers_teammate.infrastructure.addon_imports.inject_packages_dir", MagicMock()),
        pytest.raises(ImportError),
    ):
        frozen_entry.main()

    assert order == ["qt_log", "QApplication", "start_logging"]
