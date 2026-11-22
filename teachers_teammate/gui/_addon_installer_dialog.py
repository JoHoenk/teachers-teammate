"""In-app addon installer dialog for frozen builds."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..application.composition import AppServices, inject_addon_packages_dir
from ..domain.local_models import (
    HUGGING_FACE_HOST,
    PAGE_MODELS_SIZE_LABEL,
    PRIVACY_ADDON_ID,
    READER_SIZE_LABEL,
)
from ..texts.settings import WHERE_ADDONS
from . import _user_text as txt
from ._constants import _SPACY_MODEL_CHOICES
from ._long_job import ABORTED, LongJobThread


def _model_for_system_language(service: AppServices) -> str:
    """Return the spaCy model of the system language, or the multilingual fallback."""
    app = service.anonymization
    return app.system_spacy_model() or app.spacy_model_for_language("")


_ADDON_META: dict[str, dict] = {
    PRIVACY_ADDON_ID: {
        "title": "Install Privacy Add-on",
        "description": (
            "This installs <b>spaCy</b>, the NLP library used for PII anonymization.<br><br>"
            "After installation, download a name detection model for your language under "
            f"<i>{WHERE_ADDONS}</i> before using anonymization."
        ),
        "size_warning": "",
    },
}


class _InstallThread(LongJobThread):
    """Installs one add-on's pip packages and reports the log as it arrives."""

    def __init__(self, service: Any, addon: str, target: Path) -> None:
        super().__init__()
        self._service = service
        self._addon = addon
        self._target = target

    def work(self) -> None:
        self._service.addons.install_addon(self._addon, self._target, self.log_line.emit)

    def error_text(self, exc: BaseException) -> str:
        return txt.addon_error_text(exc)


class AddonInstallerDialog(QDialog):
    """Modal dialog that installs an optional addon package group at runtime.

    Emits ``installed(addon_name)`` on success so callers can refresh their UI.
    """

    installed = Signal(str)

    def __init__(self, addon: str, parent: QWidget | None = None, *, service: Any) -> None:
        super().__init__(parent)
        meta = _ADDON_META.get(addon, _ADDON_META[PRIVACY_ADDON_ID])
        self._service = service
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

        target = self._service.addons.addon_packages_dir()
        self._thread = _InstallThread(self._service, self._addon, target)
        self._thread.log_line.connect(self._append_log)
        self._thread.finished_ok.connect(self._on_install_ok)
        self._thread.finished_err.connect(self._on_install_err)
        self._thread.start()

    def _append_log(self, line: str) -> None:
        self._log.appendPlainText(line.rstrip("\n"))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_install_ok(self) -> None:
        self._service.addons.clear_stale_addon_modules(self._addon)
        inject_addon_packages_dir()
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


class _ReaderDownloadThread(LongJobThread):
    """Downloads a set of model files.

    The built-in reader's by default; ``method`` names another download of the reader
    (``"download_page_models"``).
    """

    def __init__(self, service: AppServices, method: str = "download_reader") -> None:
        super().__init__()
        self._service = service
        self._method = method

    def work(self) -> None:
        getattr(self._service.reader, self._method)(self._report, self.stop_event)

    def _report(self, done: int, total: int) -> None:
        self.progress.emit((done, total))


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

    def __init__(self, service: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._thread: _ReaderDownloadThread | None = None

        self.setWindowTitle(self._TITLE)
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        desc = QLabel(f"{self._INTRO}<br><tt>{self._service.reader.models_dir()}</tt>")
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
            self._thread.stop()
            return
        self.reject()

    def _on_progress(self, payload: object) -> None:
        done, total = cast("tuple[int, int]", payload)
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
        text = "The download was cancelled." if message == ABORTED else message
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


class _ModelDownloadThread(LongJobThread):
    """Downloads a spaCy model package and reports the log as it arrives."""

    def __init__(self, service: Any, model: str, target: Path) -> None:
        super().__init__()
        self._service = service
        self._model = model
        self._target = target

    def work(self) -> None:
        self._service.anonymization.download_spacy_model(
            self._model, self._target, self.log_line.emit
        )

    def error_text(self, exc: BaseException) -> str:
        return txt.addon_error_text(exc)


class SpacyModelDownloadDialog(QDialog):
    """Modal dialog that downloads a spaCy model package at runtime.

    Emits ``model_downloaded(package_name)`` on success so callers can refresh
    their model list.
    """

    model_downloaded = Signal(str)

    def __init__(
        self,
        default_model: str = "",
        parent: QWidget | None = None,
        *,
        service: Any,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._thread: _ModelDownloadThread | None = None
        # No model given: preselect the one for the language this computer is set to.
        default_model = default_model or _model_for_system_language(self._service)

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
        target = self._service.addons.addon_packages_dir()
        self._thread = _ModelDownloadThread(self._service, model, target)
        self._thread.log_line.connect(self._append_log)
        self._thread.finished_ok.connect(self._on_download_ok)
        self._thread.finished_err.connect(self._on_download_err)
        self._thread.start()

    def _append_log(self, line: str) -> None:
        self._log.appendPlainText(line.rstrip("\n"))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_download_ok(self) -> None:
        inject_addon_packages_dir()
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


# ── Ollama model pull dialog ───────────────────────────────────────────────────


class _OllamaPullThread(LongJobThread):
    """Pulls one Ollama model, reporting the stream as it arrives."""

    def __init__(self, service: Any, url: str, model_name: str) -> None:
        super().__init__()
        self._service = service
        self._url = url
        self._model_name = model_name

    def work(self) -> str:
        self._service.ollama.pull_ollama_model(
            self._url, self._model_name, self._report, self.stop_event
        )
        return self._model_name

    def _report(self, status: str, done: float, total: float) -> None:
        # Bytes travel as floats: a Qt ``int`` is 32 bits and a model is often over 2 GB.
        self.progress.emit((status, done, total))
