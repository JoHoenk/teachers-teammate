"""GUI tests for the runtime addon installer dialogs."""
# pylint: disable=W0212  # accessing protected slots/state by design — these are the behaviours under test

from __future__ import annotations

from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.application.service import (
    ADDON_AMD,
    ADDON_NVIDIA,
    ADDON_PRIVACY,
    GpuInfo,
)
from teachers_teammate.exceptions import ModelDownloadCancelledError, ModelDownloadError
from teachers_teammate.gui import _addon_installer_dialog as mod
from teachers_teammate.gui._addon_installer_dialog import (
    PageModelsDownloadDialog,
    ReaderDownloadDialog,
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


# ── worker threads + pip-missing ────────────────────────────


def _all_label_texts(dlg) -> list[str]:
    from PySide6.QtWidgets import QLabel  # noqa: PLC0415

    return [lbl.text() for lbl in dlg.findChildren(QLabel)]


@pytest.mark.gui
@pytest.mark.use_case("Runtime_Addon_Installation")
def test_install_thread_run_emits_finished_ok() -> None:
    """Given a successful install / When _InstallThread.run executes / Then finished_ok is emitted."""
    thread = mod._InstallThread(ADDON_PRIVACY, Path("/tmp/pkg"))
    ok = MagicMock()
    thread.finished_ok.connect(ok)

    with patch(_SVC):
        thread.run()

    ok.assert_called_once()


@pytest.mark.gui
def test_install_thread_run_emits_finished_err_on_failure() -> None:
    """Given install raises / When _InstallThread.run executes / Then finished_err carries the message."""
    thread = mod._InstallThread(ADDON_PRIVACY, Path("/tmp/pkg"))
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


# ── the built-in reader's download ───────────────────────────────────────────


class _FakeReaderService:
    """Downloads nothing: reports progress, optionally fails, and notes whether it was cancelled."""

    def __init__(self, *, error: Exception | None = None, wait_for_cancel: bool = False) -> None:
        self.error = error
        self.wait_for_cancel = wait_for_cancel
        self.installed = False
        self.page_models_calls = 0
        self.started = threading.Event()

    def models_dir(self) -> Path:
        return Path("/tmp/models")

    def download_page_models(self, on_progress=None, cancel=None) -> None:
        self.page_models_calls += 1
        self.download_reader(on_progress, cancel)

    def download_reader(self, on_progress=None, cancel=None) -> None:
        self.started.set()
        if on_progress:
            on_progress(1_000_000, 4_000_000)
        if self.wait_for_cancel and cancel is not None:
            assert cancel.wait(5), "the dialog never cancelled"
            raise ModelDownloadCancelledError("cancelled")
        if self.error is not None:
            raise self.error
        self.installed = True


@pytest.mark.gui
def test_reader_dialog_downloads_and_reports_progress_in_mb(qtbot) -> None:
    """
    Given  the reader download dialog with a service that downloads fine
    When   Download is pressed
    Then   the bar fills, the status shows MB, and "downloaded" is emitted
    """
    service = _FakeReaderService()
    dlg = ReaderDownloadDialog(service)
    qtbot.addWidget(dlg)

    with qtbot.waitSignal(dlg.downloaded, timeout=5000):
        dlg._start_btn.click()

    assert service.installed
    assert dlg._bar.value() == 100
    assert "Downloaded" in dlg._status.text()
    assert "/tmp/models" in dlg.findChildren(mod.QLabel)[0].text()


@pytest.mark.gui
def test_reader_dialog_shows_the_error_sentence_and_offers_retry(qtbot) -> None:
    """
    Given  a download that fails with a sentence for the teacher
    When   Download is pressed
    Then   the sentence is shown and the button becomes Retry
    """
    service = _FakeReaderService(error=ModelDownloadError("Could not reach huggingface.co."))
    dlg = ReaderDownloadDialog(service)
    qtbot.addWidget(dlg)

    dlg._start_btn.click()
    qtbot.waitUntil(lambda: dlg._start_btn.text() == "Retry", timeout=5000)

    assert "Could not reach huggingface.co." in dlg._status.text()
    assert service.installed is False


@pytest.mark.gui
def test_reader_dialog_cancel_stops_the_thread_and_installs_nothing(qtbot) -> None:
    """
    Given  a download that is running
    When   Cancel is pressed
    Then   the thread is told to stop, nothing is installed and the dialog says so
    """
    service = _FakeReaderService(wait_for_cancel=True)
    dlg = ReaderDownloadDialog(service)
    qtbot.addWidget(dlg)
    dlg._start_btn.click()
    qtbot.waitUntil(service.started.is_set, timeout=5000)

    dlg._cancel_btn.click()
    qtbot.waitUntil(lambda: dlg._start_btn.text() == "Retry", timeout=5000)

    assert service.installed is False
    assert "cancelled" in dlg._status.text()


@pytest.mark.gui
def test_page_models_dialog_downloads_the_page_models_and_names_them(qtbot) -> None:
    """
    Given  the page models download dialog with a service that downloads fine
    When   Download is pressed
    Then   the service's page-models download runs (not the reader's), the dialog says what
           the models do and where they are kept, and "downloaded" is emitted
    """
    service = _FakeReaderService()
    dlg = PageModelsDownloadDialog(service)
    qtbot.addWidget(dlg)
    text = dlg.findChildren(mod.QLabel)[0].text()

    with qtbot.waitSignal(dlg.downloaded, timeout=5000):
        dlg._start_btn.click()

    assert dlg.windowTitle() == "Download the page models"
    assert "turns sideways pages" in text
    assert "/tmp/models" in text
    assert service.page_models_calls == 1
    assert dlg._bar.value() == 100
