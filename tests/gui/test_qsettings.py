"""GUI tests for ``gui/_qsettings.py``: where the window state of the app is kept."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from teachers_teammate.gui import _qsettings


@pytest.mark.gui
def test_the_override_folder_keeps_the_settings_as_an_ini_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  the app started under an override folder, as a walkthrough or a test starts it
    When   the GUI asks for its settings and stores a value
    Then   the file is an INI file in that folder, and a second store reads the value back
    """
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path))

    store = _qsettings.app_settings()
    store.setValue("gui/geometry", "saved")
    store.sync()

    path = tmp_path / _qsettings.FILE_NAME
    assert store.format() == QSettings.Format.IniFormat
    assert Path(store.fileName()) == path
    assert "saved" in path.read_text(encoding="utf-8")
    assert _qsettings.app_settings().value("gui/geometry") == "saved"


@pytest.mark.gui
def test_without_the_override_folder_the_native_store_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  a teacher's own start of the app, with no override folder
    When   the GUI asks for its settings
    Then   it gets the platform's own store, under the app's organisation and application name
    """
    monkeypatch.delenv("TEACHERS_TEAMMATE_TMPDIR", raising=False)

    store = _qsettings.app_settings()

    assert store.format() == QSettings.Format.NativeFormat
    assert store.organizationName() == _qsettings.ORGANIZATION
    assert store.applicationName() == _qsettings.APPLICATION
