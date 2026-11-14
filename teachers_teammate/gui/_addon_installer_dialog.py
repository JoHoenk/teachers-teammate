"""In-app addon installer dialog for frozen builds."""

from __future__ import annotations

from pathlib import Path
import sys
import threading
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..application.service import (
    ADDON_AMD,
    ADDON_NVIDIA,
    ADDON_PRIVACY,
    GpuInfo,
    ProcessingApplicationService,
)
from ..domain.local_models import HUGGING_FACE_HOST, PAGE_MODELS_SIZE_LABEL, READER_SIZE_LABEL
from ..exceptions import ModelDownloadCancelledError
from . import _user_text as txt
from ._constants import _SPACY_MODEL_CHOICES


def _model_for_system_language() -> str:
    """Return the spaCy model of the system language, or the multilingual fallback."""
    service = ProcessingApplicationService()
    return service.system_spacy_model() or service.spacy_model_for_language("")


_ADDON_META: dict[str, dict] = {
    ADDON_PRIVACY: {
        "title": "Install Privacy Addon",
        "description": (
            "This installs <b>spaCy</b>, the NLP library used for PII anonymization.<br><br>"
            "After installation, download a name detection model for your language under "
            f"<i>{txt.WHERE_ADDONS}</i> before using anonymization."
        ),
        "size_warning": "",
    },
}


class _InstallThread(QThread):
    log_line = Signal(str)
    finished_ok = Signal()
    finished_err = Signal(str)

    def __init__(self, addon: str, target: Path) -> None:
        super().__init__()
        self._addon = addon
        self._target = target

    def run(self) -> None:
        try:
            ProcessingApplicationService().install_addon(
                self._addon, self._target, self.log_line.emit
            )
            self.finished_ok.emit()
        except Exception as exc:  # noqa: BLE001  # surface any pip/subprocess failure to the UI via the error signal
            self.finished_err.emit(str(exc))


class AddonInstallerDialog(QDialog):
    """Modal dialog that installs an optional addon package group at runtime.

    Emits ``installed(addon_name)`` on success so callers can refresh their UI.
    """

    installed = Signal(str)

    def __init__(self, addon: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        meta = _ADDON_META.get(addon, _ADDON_META[ADDON_PRIVACY])
        self._addon = addon
        self._thread: _InstallThread | None = None

        self.setWindowTitle(meta["title"])
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        desc = QLabel(meta["description"])
        desc.setWordWrap(True)
        desc.setOpenExternalLinks(False)
        layout.addWidget(desc)

        if meta["size_warning"]:
            warn = QLabel(meta["size_warning"])
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #c0392b; font-weight: bold;")
            layout.addWidget(warn)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(160)
        self._log.setPlaceholderText("Installation log will appear here…")
        layout.addWidget(self._log)

        buttons = QDialogButtonBox()
        self._install_btn = QPushButton("Install")
        self._install_btn.setDefault(True)
        buttons.addButton(self._install_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self._close_btn = QPushButton("Close")
        buttons.addButton(self._close_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        self._install_btn.clicked.connect(self._start_install)
        self._close_btn.clicked.connect(self.reject)

    # ── slots ──────────────────────────────────────────────────────────────────

    def _start_install(self) -> None:
        self._install_btn.setEnabled(False)
        self._install_btn.setText("Installing…")
        self._log.clear()

        target = ProcessingApplicationService().addon_packages_dir()
        self._thread = _InstallThread(self._addon, target)
        self._thread.log_line.connect(self._append_log)
        self._thread.finished_ok.connect(self._on_install_ok)
        self._thread.finished_err.connect(self._on_install_err)
        self._thread.start()

    def _append_log(self, line: str) -> None:
        self._log.appendPlainText(line.rstrip("\n"))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_install_ok(self) -> None:
        svc = ProcessingApplicationService()
        svc.clear_stale_addon_modules(self._addon)
        svc.inject_addon_packages_dir()
        self._append_log("\n✓ Installation complete.")
        self._install_btn.setText("Installed ✓")
        self._close_btn.setText("Close")
        self.installed.emit(self._addon)

    def _on_install_err(self, msg: str) -> None:
        self._log.appendHtml(f'<span style="color:#c0392b;"><b>Error:</b> {msg}</span>')
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._install_btn.setEnabled(True)
        self._install_btn.setText("Retry")

    # ── guard against closing while installing ─────────────────────────────────

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            # Don't close mid-install; let the thread finish.
            return
        super().reject()

    def closeEvent(self, event: object) -> None:
        from PySide6.QtGui import QCloseEvent  # noqa: PLC0415

        if self._thread is not None and self._thread.isRunning():
            if isinstance(event, QCloseEvent):
                event.ignore()
            return
        super().closeEvent(event)  # ty: ignore[invalid-argument-type]  # event is typed object to match the overridden signature; QDialog.closeEvent wants QCloseEvent

    # ── non-frozen builds: show a pip command instead ─────────────────────────

    @staticmethod
    def is_available() -> bool:
        """Return True when the installer can actually run (frozen build with pip bundled)."""
        if not getattr(sys, "frozen", False):
            return False
        try:
            import pip  # noqa: PLC0415, F401

            return True
        except ImportError:
            return False


# ── The built-in reader's model files ──────────────────────────────────────────


class _ReaderDownloadThread(QThread):
    """Downloads a set of model files in a background thread.

    The built-in reader's by default; ``method`` names another download of the service
    (``"download_page_models"``).
    """

    progress = Signal(int, int)  # bytes done, bytes total
    finished_ok = Signal()
    finished_err = Signal(str)

    def __init__(self, service: Any, method: str = "download_reader") -> None:
        super().__init__()
        self._service = service
        self._method = method
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Stop after the chunk that is arriving; no partial file is kept."""
        self._cancel.set()

    def run(self) -> None:
        try:
            getattr(self._service, self._method)(self.progress.emit, self._cancel)
            self.finished_ok.emit()
        except ModelDownloadCancelledError:
            self.finished_err.emit("aborted")
        except Exception as exc:  # noqa: BLE001  # any failure reaches the UI as a sentence
            self.finished_err.emit(str(exc))


class ReaderDownloadDialog(QDialog):
    """Modal dialog that downloads the built-in reader with a progress bar in MB.

    Emits ``downloaded()`` on success so callers can refresh what depends on it.  A subclass
    downloads another set of model files by changing the class attributes below.
    """

    downloaded = Signal()

    _TITLE = "Download the built-in reader"
    _INTRO = (
        "The built-in reader reads pages on this computer, without a server. Its model "
        f"files ({READER_SIZE_LABEL}, Apache-2.0, from PaddlePaddle) are downloaded once "
        f"from {HUGGING_FACE_HOST} and kept in:"
    )
    _METHOD = "download_reader"

    def __init__(self, service: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service or ProcessingApplicationService()
        self._thread: _ReaderDownloadThread | None = None

        self.setWindowTitle(self._TITLE)
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        desc = QLabel(f"{self._INTRO}<br><tt>{self._service.models_dir()}</tt>")
        desc.setWordWrap(True)
        desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(desc)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        layout.addWidget(self._bar)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QDialogButtonBox()
        self._start_btn = QPushButton("Download")
        self._start_btn.setDefault(True)
        buttons.addButton(self._start_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self._cancel_btn = QPushButton("Cancel")
        buttons.addButton(self._cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)
        self._start_btn.clicked.connect(self._start)
        self._cancel_btn.clicked.connect(self._on_cancel)

    def _start(self) -> None:
        self._start_btn.setEnabled(False)
        self._start_btn.setText("Downloading…")
        self._status.setText("Starting download…")
        self._thread = _ReaderDownloadThread(self._service, self._METHOD)
        self._thread.progress.connect(self._on_progress)
        self._thread.finished_ok.connect(self._on_ok)
        self._thread.finished_err.connect(self._on_err)
        self._thread.start()

    def _on_cancel(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._cancel_btn.setEnabled(False)
            self._thread.cancel()
            return
        self.reject()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._bar.setValue(int(100 * done / total))
            self._status.setText(f"{done / 1_000_000:.1f} / {total / 1_000_000:.1f} MB")

    def _on_ok(self) -> None:
        self._bar.setValue(100)
        self._status.setText(f'<span style="color:#27ae60;">{txt.READER_DOWNLOADED}</span>')
        self._start_btn.setText("Done ✓")
        self._cancel_btn.setText("Close")
        self._cancel_btn.setEnabled(True)
        self.downloaded.emit()

    def _on_err(self, message: str) -> None:
        text = "The download was cancelled." if message == "aborted" else message
        self._status.setText(f'<span style="color:#c0392b;"><b>Error:</b> {text}</span>')
        self._start_btn.setEnabled(True)
        self._start_btn.setText("Retry")
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setText("Close")

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        super().reject()

    def closeEvent(self, event: object) -> None:
        from PySide6.QtGui import QCloseEvent  # noqa: PLC0415

        if self._thread is not None and self._thread.isRunning():
            if isinstance(event, QCloseEvent):
                event.ignore()
            return
        super().closeEvent(event)  # ty: ignore[invalid-argument-type]  # event is typed object to match the overridden signature; QDialog.closeEvent wants QCloseEvent


class PageModelsDownloadDialog(ReaderDownloadDialog):
    """Modal dialog that downloads the page models of the automatic image preparation."""

    _TITLE = "Download the page models"
    _INTRO = (
        "The automatic image preparation turns sideways pages and flattens curved ones with two "
        f"small models ({PAGE_MODELS_SIZE_LABEL}, Apache-2.0, from PaddlePaddle). They are "
        f"downloaded once from {HUGGING_FACE_HOST} and kept in:"
    )
    _METHOD = "download_page_models"


# ── spaCy model download ───────────────────────────────────────────────────────


class _ModelDownloadThread(QThread):
    log_line = Signal(str)
    finished_ok = Signal()
    finished_err = Signal(str)

    def __init__(self, model: str, target: Path) -> None:
        super().__init__()
        self._model = model
        self._target = target

    def run(self) -> None:
        try:
            ProcessingApplicationService().download_spacy_model(
                self._model, self._target, self.log_line.emit
            )
            self.finished_ok.emit()
        except Exception as exc:  # noqa: BLE001  # surface any download/subprocess failure to the UI via the error signal
            self.finished_err.emit(str(exc))


class SpacyModelDownloadDialog(QDialog):
    """Modal dialog that downloads a spaCy model package at runtime.

    Emits ``model_downloaded(package_name)`` on success so callers can refresh
    their model list.
    """

    model_downloaded = Signal(str)

    def __init__(self, default_model: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thread: _ModelDownloadThread | None = None
        # No model given: preselect the one for the language this computer is set to.
        default_model = default_model or _model_for_system_language()

        self.setWindowTitle("Download spaCy Model")
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        desc = QLabel(
            "Select a spaCy NER model to download. The model will be used for "
            "anonymizing person names in OCR output.<br><br>"
            "The <i>Multilingual</i> model works as a secondary pass across all "
            "supported languages and is a good default choice."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        from PySide6.QtWidgets import QComboBox  # noqa: PLC0415

        form = QFormLayout()
        self._model_combo = QComboBox()
        for label, pkg in _SPACY_MODEL_CHOICES:
            self._model_combo.addItem(label, userData=pkg)
        idx = self._model_combo.findData(default_model)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        form.addRow("Model:", self._model_combo)
        layout.addLayout(form)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(160)
        self._log.setPlaceholderText("Download log will appear here…")
        layout.addWidget(self._log)

        buttons = QDialogButtonBox()
        self._download_btn = QPushButton("Download")
        self._download_btn.setDefault(True)
        buttons.addButton(self._download_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self._close_btn = QPushButton("Close")
        buttons.addButton(self._close_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        self._download_btn.clicked.connect(self._start_download)
        self._close_btn.clicked.connect(self.reject)

    def _start_download(self) -> None:
        self._download_btn.setEnabled(False)
        self._download_btn.setText("Downloading…")
        self._log.clear()
        self._model_combo.setEnabled(False)

        model = self._model_combo.currentData()
        target = ProcessingApplicationService().addon_packages_dir()
        self._thread = _ModelDownloadThread(model, target)
        self._thread.log_line.connect(self._append_log)
        self._thread.finished_ok.connect(self._on_download_ok)
        self._thread.finished_err.connect(self._on_download_err)
        self._thread.start()

    def _append_log(self, line: str) -> None:
        self._log.appendPlainText(line.rstrip("\n"))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_download_ok(self) -> None:
        ProcessingApplicationService().inject_addon_packages_dir()
        model = self._model_combo.currentData()
        self._append_log("\n✓ Model downloaded.")
        self._download_btn.setText("Downloaded ✓")
        self._close_btn.setText("Close")
        self.model_downloaded.emit(model)

    def _on_download_err(self, msg: str) -> None:
        self._log.appendHtml(f'<span style="color:#c0392b;"><b>Error:</b> {msg}</span>')
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._download_btn.setEnabled(True)
        self._download_btn.setText("Retry")
        self._model_combo.setEnabled(True)

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        super().reject()

    def closeEvent(self, event: object) -> None:
        from PySide6.QtGui import QCloseEvent  # noqa: PLC0415

        if self._thread is not None and self._thread.isRunning():
            if isinstance(event, QCloseEvent):
                event.ignore()
            return
        super().closeEvent(event)  # ty: ignore[invalid-argument-type]  # event is typed object to match the overridden signature; QDialog.closeEvent wants QCloseEvent

    @staticmethod
    def is_available() -> bool:
        """Return True when ``python -m spacy download`` can be run.

        In non-frozen environments this is possible whenever spaCy is already
        installed (pip is available by definition in a dev environment).  In
        frozen builds the check delegates to :class:`AddonInstallerDialog`
        which verifies a bundled pip is present.
        """
        if not getattr(sys, "frozen", False):
            try:
                import spacy  # noqa: PLC0415, F401

                return True
            except ImportError:
                return False
        return AddonInstallerDialog.is_available()


# ── GPU addon installer ────────────────────────────────────────────────────────

_GPU_ADDON_FOR_VENDOR: dict[str, str] = {
    "nvidia": ADDON_NVIDIA,
    "amd": ADDON_AMD,
}

_GPU_PACKAGE_DESCRIPTION: dict[str, str] = {
    ADDON_NVIDIA: (
        "Installs <b>nvidia-ml-py</b> — Python bindings for the NVIDIA Management "
        "Library (NVML).<br><br>"
        "This enables GPU monitoring (utilisation, VRAM, temperature) and is required "
        "for GPU-accelerated inference with compatible NVIDIA hardware."
    ),
    ADDON_AMD: (
        "Installs <b>pyamdgpuinfo</b> — Python bindings for AMD GPU monitoring via "
        "the Linux DRM/sysfs interface.<br><br>"
        "This enables GPU monitoring (utilisation, VRAM, temperature) for AMD GPUs "
        "on Linux systems with the AMDGPU kernel driver."
    ),
}


class GpuAddonDialog(QDialog):
    """Modal dialog that installs the GPU monitoring addon for the detected GPU vendor.

    Accepts a pre-detected list of ``GpuInfo`` objects so detection (which may
    involve subprocess calls) can be done once by the caller.

    Emits ``installed(addon_name)`` on success.
    """

    installed = Signal(str)

    def __init__(self, gpus: list, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GPU Addon")
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)
        self._thread: _InstallThread | None = None
        self._addon: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self._build_header(gpus, layout)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(140)
        self._log.setPlaceholderText("Installation log will appear here…")
        layout.addWidget(self._log)

        buttons = QDialogButtonBox()
        self._install_btn = QPushButton("Install")
        self._install_btn.setDefault(True)
        self._install_btn.setEnabled(
            self._addon is not None and AddonInstallerDialog.is_available()
        )
        buttons.addButton(self._install_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self._close_btn = QPushButton("Close")
        buttons.addButton(self._close_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        if not AddonInstallerDialog.is_available() and self._addon is not None:
            hint = QLabel(
                f"Install via pip:  <tt>pip install {_GPU_PACKAGE_DESCRIPTION[self._addon].split('<b>')[1].split('</b>')[0]}</tt>"
            )
            hint.setWordWrap(True)
            layout.addWidget(hint)

        self._install_btn.clicked.connect(self._start_install)
        self._close_btn.clicked.connect(self.reject)

    def _build_header(self, gpus: list, layout: QVBoxLayout) -> None:
        nvidia_gpus = [g for g in gpus if isinstance(g, GpuInfo) and g.vendor == "nvidia"]
        amd_gpus = [g for g in gpus if isinstance(g, GpuInfo) and g.vendor == "amd"]

        if nvidia_gpus:
            self._addon = ADDON_NVIDIA
            gpu_summary = "NVIDIA: " + ", ".join(g.name for g in nvidia_gpus)
        elif amd_gpus:
            self._addon = ADDON_AMD
            gpu_summary = "AMD: " + ", ".join(g.name for g in amd_gpus)
        else:
            gpu_summary = None

        if gpu_summary:
            detected_lbl = QLabel(f"<b>Detected GPU:</b> {gpu_summary}")
            detected_lbl.setWordWrap(True)
            layout.addWidget(detected_lbl)

        if self._addon and self._addon in _GPU_PACKAGE_DESCRIPTION:
            desc = QLabel(_GPU_PACKAGE_DESCRIPTION[self._addon])
            desc.setWordWrap(True)
            desc.setOpenExternalLinks(False)
            layout.addWidget(desc)

            if self._addon == ADDON_AMD and sys.platform != "linux":
                warn = QLabel(
                    "⚠  <b>pyamdgpuinfo</b> only works on Linux with the AMDGPU kernel "
                    "driver. Installation on this platform will have no effect."
                )
                warn.setWordWrap(True)
                warn.setStyleSheet("color: #c0392b; font-weight: bold;")
                layout.addWidget(warn)
        else:
            no_gpu_lbl = QLabel(
                "No compatible GPU was detected. If a GPU is present, make sure "
                "the appropriate driver and command-line tools (nvidia-smi / rocm-smi) "
                "are installed and accessible."
            )
            no_gpu_lbl.setWordWrap(True)
            layout.addWidget(no_gpu_lbl)

    def _start_install(self) -> None:
        if self._addon is None:
            return
        self._install_btn.setEnabled(False)
        self._install_btn.setText("Installing…")
        self._log.clear()

        target = ProcessingApplicationService().addon_packages_dir()
        self._thread = _InstallThread(self._addon, target)
        self._thread.log_line.connect(self._append_log)
        self._thread.finished_ok.connect(self._on_install_ok)
        self._thread.finished_err.connect(self._on_install_err)
        self._thread.start()

    def _append_log(self, line: str) -> None:
        self._log.appendPlainText(line.rstrip("\n"))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_install_ok(self) -> None:
        if self._addon:
            svc = ProcessingApplicationService()
            svc.clear_stale_addon_modules(self._addon)
            svc.inject_addon_packages_dir()
            self.installed.emit(self._addon)
        self._append_log("\n✓ Installation complete.")
        self._install_btn.setText("Installed ✓")
        self._close_btn.setText("Close")

    def _on_install_err(self, msg: str) -> None:
        self._log.appendHtml(f'<span style="color:#c0392b;"><b>Error:</b> {msg}</span>')
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._install_btn.setEnabled(True)
        self._install_btn.setText("Retry")

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        super().reject()

    def closeEvent(self, event: object) -> None:
        from PySide6.QtGui import QCloseEvent  # noqa: PLC0415

        if self._thread is not None and self._thread.isRunning():
            if isinstance(event, QCloseEvent):
                event.ignore()
            return
        super().closeEvent(event)  # ty: ignore[invalid-argument-type]  # event is typed object to match the overridden signature; QDialog.closeEvent wants QCloseEvent


# ── Ollama model pull dialog ───────────────────────────────────────────────────


class _OllamaPullThread(QThread):
    """Downloads an Ollama model in a background thread, reporting streamed progress."""

    progress = Signal(str, float, float)  # (status, completed_bytes, total_bytes)
    finished_ok = Signal(str)  # model_name
    finished_err = Signal(str)  # error message

    def __init__(self, url: str, model_name: str) -> None:
        super().__init__()
        self._url = url
        self._model_name = model_name
        self._abort_event = threading.Event()

    def request_abort(self) -> None:
        self._abort_event.set()

    def run(self) -> None:
        try:
            svc = ProcessingApplicationService()
            svc.pull_ollama_model(
                self._url, self._model_name, self.progress.emit, self._abort_event
            )
            if self._abort_event.is_set():
                self.finished_err.emit("aborted")
            else:
                self.finished_ok.emit(self._model_name)
        except Exception as exc:  # noqa: BLE001  # surface any pull/subprocess failure to the UI via the error signal
            self.finished_err.emit(str(exc))


class OllamaModelPullDialog(QDialog):
    """Modal dialog that pulls (downloads) an Ollama model with a live progress bar.

    Emits ``model_pulled(model_name)`` on success so callers can refresh model dropdowns.
    """

    model_pulled = Signal(str)

    def __init__(
        self,
        url: str = "http://127.0.0.1:11434",
        model_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._url = url
        self._thread: _OllamaPullThread | None = None

        self.setWindowTitle("Pull Ollama Model")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        desc = QLabel(
            "Enter an Ollama model tag to download from the registry.<br>"
            "Examples: <tt>llama3.2:latest</tt>, <tt>mistral:7b</tt>, <tt>tinyllama</tt>"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        form = QFormLayout()
        self._model_input = QLineEdit(model_name)
        self._model_input.setPlaceholderText("e.g. llama3.2:latest")
        form.addRow("Model:", self._model_input)
        layout.addLayout(form)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)

        buttons = QDialogButtonBox()
        self._pull_btn = QPushButton("Pull Model")
        self._pull_btn.setDefault(True)
        buttons.addButton(self._pull_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self._close_btn = QPushButton("Close")
        buttons.addButton(self._close_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        self._pull_btn.clicked.connect(self._start_pull)
        self._close_btn.clicked.connect(self.reject)

    def _start_pull(self) -> None:
        model_name = self._model_input.text().strip()
        if not model_name:
            self._status_lbl.setText(
                '<span style="color:#c0392b;">Please enter a model name.</span>'
            )
            return
        self._pull_btn.setEnabled(False)
        self._pull_btn.setText("Pulling…")
        self._model_input.setEnabled(False)
        self._progress_bar.setValue(0)
        self._status_lbl.setText("Starting download…")

        self._thread = _OllamaPullThread(self._url, model_name)
        self._thread.progress.connect(self._on_progress)
        self._thread.finished_ok.connect(self._on_pull_ok)
        self._thread.finished_err.connect(self._on_pull_err)
        self._thread.start()

    def _on_progress(self, status: str, completed: int, total: int) -> None:
        if total > 0:
            pct = int(completed / total * 100)
            self._progress_bar.setValue(pct)
            mb_done = completed / 1_048_576
            mb_total = total / 1_048_576
            self._status_lbl.setText(f"{status} — {mb_done:.1f} / {mb_total:.1f} MB")
        else:
            self._status_lbl.setText(status)

    def _on_pull_ok(self, model_name: str) -> None:
        self._progress_bar.setValue(100)
        self._status_lbl.setText(
            '<span style="color:#27ae60;">✓ Model downloaded successfully.</span>'
        )
        self._pull_btn.setText("Done ✓")
        self._close_btn.setText("Close")
        self.model_pulled.emit(model_name)

    def _on_pull_err(self, msg: str) -> None:
        self._status_lbl.setText(f'<span style="color:#c0392b;"><b>Error:</b> {msg}</span>')
        self._pull_btn.setEnabled(True)
        self._pull_btn.setText("Retry")
        self._model_input.setEnabled(True)

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        super().reject()

    def closeEvent(self, event: object) -> None:
        from PySide6.QtGui import QCloseEvent  # noqa: PLC0415

        if self._thread is not None and self._thread.isRunning():
            if isinstance(event, QCloseEvent):
                event.ignore()
            return
        super().closeEvent(event)  # ty: ignore[invalid-argument-type]  # event is typed object to match the overridden signature; QDialog.closeEvent wants QCloseEvent
