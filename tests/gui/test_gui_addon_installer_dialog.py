"""GUI tests for the runtime addon installer dialogs."""
# pylint: disable=W0212  # accessing protected slots/state by design — these are the behaviours under test

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.application.service import (
    ADDON_AMD,
    ADDON_NVIDIA,
    ADDON_PADDLE,
    GpuInfo,
)
from teachers_teammate.gui import _addon_installer_dialog as mod
from teachers_teammate.gui._addon_installer_dialog import (
    AddonInstallerDialog,
    GpuAddonDialog,
    SpacyModelDownloadDialog,
)

_SVC = "teachers_teammate.gui._addon_installer_dialog.ProcessingApplicationService"


class _FakeThread:
    """Stand-in for the install/download QThread that records start() and never runs."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.log_line = MagicMock()
        self.finished_ok = MagicMock()
        self.finished_err = MagicMock()
        self.started = False
        self._running = False

    def start(self) -> None:
        self.started = True

    def isRunning(self) -> bool:  # Qt naming: mirrors QThread.isRunning
        return self._running


@pytest.mark.gui
def test_addon_installer_is_available_false_when_not_frozen() -> None:
    """Given a non-frozen build / When is_available is checked / Then it returns False."""
    with patch.object(mod.sys, "frozen", False, create=True):
        assert AddonInstallerDialog.is_available() is False


@pytest.mark.gui
def test_addon_installer_is_available_true_when_frozen_with_pip() -> None:
    """Given a frozen build with pip importable / When is_available is checked / Then True."""
    with patch.object(mod.sys, "frozen", True, create=True):
        # pip is importable in the dev env; no need to fake the import.
        assert AddonInstallerDialog.is_available() is True


# ── SpacyModelDownloadDialog.is_available ─────────────────────────────────────


@pytest.mark.gui
def test_spacy_download_available_in_dev_when_spacy_installed() -> None:
    """
    Given  a non-frozen environment with spaCy importable
    When   SpacyModelDownloadDialog.is_available() is checked
    Then   it returns True
    """
    import sys as _sys  # noqa: PLC0415

    with (
        patch.object(mod.sys, "frozen", False, create=True),
        patch.dict(_sys.modules, {"spacy": MagicMock()}),
    ):
        assert SpacyModelDownloadDialog.is_available() is True


@pytest.mark.gui
def test_spacy_download_not_available_when_spacy_missing() -> None:
    """
    Given  a non-frozen environment without spaCy installed
    When   SpacyModelDownloadDialog.is_available() is checked
    Then   it returns False
    """
    import sys as _sys  # noqa: PLC0415

    with (
        patch.object(mod.sys, "frozen", False, create=True),
        patch.dict(_sys.modules, {"spacy": None}),
    ):
        assert SpacyModelDownloadDialog.is_available() is False


# ── SpacyModelDownloadDialog ─────────────────────────────────────────────────


@pytest.mark.gui
def test_spacy_dialog_preselects_default_model(qtbot) -> None:
    """
    Given  a SpacyModelDownloadDialog created with a default model
    When   the dialog is built
    Then   the combo box pre-selects that model
    """
    dlg = SpacyModelDownloadDialog(default_model="de_core_news_sm")
    qtbot.addWidget(dlg)

    assert dlg._model_combo.currentData() == "de_core_news_sm"


@pytest.mark.gui
@pytest.mark.use_case("Language_Model_Download")
def test_spacy_dialog_on_ok_emits_model_downloaded(qtbot) -> None:
    """
    Given  a SpacyModelDownloadDialog
    When   _on_download_ok fires
    Then   model_downloaded is emitted with the selected package name
    """
    dlg = SpacyModelDownloadDialog(default_model="fr_core_news_sm")
    qtbot.addWidget(dlg)
    received: list[str] = []
    dlg.model_downloaded.connect(received.append)

    with patch(_SVC):
        dlg._on_download_ok()

    assert received == ["fr_core_news_sm"]
    assert dlg._download_btn.text() == "Downloaded ✓"


@pytest.mark.gui
def test_spacy_dialog_on_err_reenables_controls(qtbot) -> None:
    """
    Given  a SpacyModelDownloadDialog
    When   _on_download_err fires
    Then   the download button and model combo are re-enabled
    """
    dlg = SpacyModelDownloadDialog()
    qtbot.addWidget(dlg)
    dlg._download_btn.setEnabled(False)
    dlg._model_combo.setEnabled(False)

    dlg._on_download_err("boom")

    assert "boom" in dlg._log.toPlainText()
    assert dlg._download_btn.isEnabled() is True
    assert dlg._model_combo.isEnabled() is True


# ── GpuAddonDialog._build_header ─────────────────────────────────────────────


@pytest.mark.gui
def test_gpu_dialog_selects_nvidia_addon(qtbot) -> None:
    """Given an NVIDIA GpuInfo / When the dialog is built / Then it targets the nvidia addon."""
    dlg = GpuAddonDialog([GpuInfo("nvidia", "RTX 4090")])
    qtbot.addWidget(dlg)
    assert dlg._addon == ADDON_NVIDIA


@pytest.mark.gui
def test_gpu_dialog_selects_amd_addon(qtbot) -> None:
    """Given an AMD GpuInfo / When the dialog is built / Then it targets the amd addon."""
    dlg = GpuAddonDialog([GpuInfo("amd", "RX 7900")])
    qtbot.addWidget(dlg)
    assert dlg._addon == ADDON_AMD


@pytest.mark.gui
def test_gpu_dialog_no_gpu_disables_install(qtbot) -> None:
    """Given an empty GPU list / When the dialog is built / Then no addon is set and Install is disabled."""
    dlg = GpuAddonDialog([])
    qtbot.addWidget(dlg)
    assert dlg._addon is None
    assert dlg._install_btn.isEnabled() is False


@pytest.mark.gui
def test_gpu_dialog_start_install_noop_without_addon(qtbot) -> None:
    """Given a GpuAddonDialog with no detected GPU / When _start_install runs / Then no thread starts."""
    dlg = GpuAddonDialog([])
    qtbot.addWidget(dlg)

    dlg._start_install()

    assert dlg._thread is None


@pytest.mark.gui
def test_gpu_dialog_start_install_launches_thread(qtbot) -> None:
    """Given a GpuAddonDialog with a detected GPU / When _start_install runs / Then a thread starts."""
    dlg = GpuAddonDialog([GpuInfo("nvidia", "RTX 4090")])
    qtbot.addWidget(dlg)

    with patch(_SVC) as svc, patch.object(mod, "_InstallThread", _FakeThread):
        svc.return_value.addon_packages_dir.return_value = "/tmp/pkg"
        dlg._start_install()

    assert isinstance(dlg._thread, _FakeThread)
    assert dlg._thread.started is True


@pytest.mark.gui
def test_gpu_dialog_on_ok_emits_installed(qtbot) -> None:
    """Given a GpuAddonDialog targeting an addon / When _on_install_ok fires / Then 'installed' is emitted."""
    dlg = GpuAddonDialog([GpuInfo("amd", "RX 7900")])
    qtbot.addWidget(dlg)
    received: list[str] = []
    dlg.installed.connect(received.append)

    with patch(_SVC):
        dlg._on_install_ok()

    assert received == [ADDON_AMD]
    assert dlg._install_btn.text() == "Installed ✓"


@pytest.mark.gui
def test_gpu_dialog_on_err_offers_retry(qtbot) -> None:
    """Given a GpuAddonDialog / When _on_install_err fires / Then the button becomes 'Retry'."""
    dlg = GpuAddonDialog([GpuInfo("nvidia", "RTX 4090")])
    qtbot.addWidget(dlg)

    dlg._on_install_err("disk full")

    assert "disk full" in dlg._log.toPlainText()
    assert dlg._install_btn.text() == "Retry"


# ── paddle warning + worker threads + pip-missing ────────────────────────────


def _all_label_texts(dlg) -> list[str]:
    from PySide6.QtWidgets import QLabel  # noqa: PLC0415

    return [lbl.text() for lbl in dlg.findChildren(QLabel)]


@pytest.mark.gui
def test_addon_installer_shows_paddle_size_warning(qtbot) -> None:
    """Given the PaddleOCR addon / When the dialog is built / Then the size warning is present."""
    dlg = AddonInstallerDialog(ADDON_PADDLE)
    qtbot.addWidget(dlg)

    # The size warning text mentions the approximate download size.
    assert any("400 MB" in t for t in _all_label_texts(dlg))


@pytest.mark.gui
@pytest.mark.use_case("Runtime_Addon_Installation")
def test_install_thread_run_emits_finished_ok() -> None:
    """Given a successful install / When _InstallThread.run executes / Then finished_ok is emitted."""
    thread = mod._InstallThread(ADDON_PADDLE, Path("/tmp/pkg"))
    ok = MagicMock()
    thread.finished_ok.connect(ok)

    with patch(_SVC):
        thread.run()

    ok.assert_called_once()


@pytest.mark.gui
def test_install_thread_run_emits_finished_err_on_failure() -> None:
    """Given install raises / When _InstallThread.run executes / Then finished_err carries the message."""
    thread = mod._InstallThread(ADDON_PADDLE, Path("/tmp/pkg"))
    err: list[str] = []
    thread.finished_err.connect(err.append)

    with patch(_SVC) as svc:
        svc.return_value.install_addon.side_effect = RuntimeError("pip failed")
        thread.run()

    assert err == ["pip failed"]


@pytest.mark.gui
@pytest.mark.use_case("Language_Model_Download")
def test_model_download_thread_run_emits_finished_ok() -> None:
    """Given a successful download / When _ModelDownloadThread.run executes / Then finished_ok is emitted."""
    thread = mod._ModelDownloadThread("en_core_web_sm", Path("/tmp/pkg"))
    ok = MagicMock()
    thread.finished_ok.connect(ok)

    with patch(_SVC):
        thread.run()

    ok.assert_called_once()


@pytest.mark.gui
def test_addon_installer_is_available_false_when_pip_missing() -> None:
    """Given a frozen build without pip / When is_available is checked / Then it returns False."""
    import sys as _sys  # noqa: PLC0415

    with (
        patch.object(mod.sys, "frozen", True, create=True),
        patch.dict(_sys.modules, {"pip": None}),
    ):
        assert AddonInstallerDialog.is_available() is False
