"""The preprocessing preview: how a scan looks before and after the image preparation.

Preparing a full-resolution scan (denoise, deskew, …) takes seconds, so it runs in a thread; the
dialog shows the two pictures side by side.
"""

from __future__ import annotations

from pathlib import Path
import shutil

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..application.composition import AppServices
from ..config import OcrConfig
from ..texts import dialogs as dlg
from . import _dialogs
from ._config_panel import ConfigPanel
from ._image_utils import load_pixmap
from ._image_view import ZoomableImageView
from ._results_table import ResultsTable


class _PreprocessPreviewThread(QThread):
    """Run preprocess_preview() in a background thread for the preprocessing preview dialog.

    Preprocessing a full-resolution scan (denoise, deskew, ...) can take several
    seconds; running it on the GUI thread would freeze the window until it returns.
    """

    done = Signal(object, object, object, str)  # original_path, processed_path, steps, error

    def __init__(
        self,
        app_service: AppServices,
        source_path: Path,
        ocr_config: OcrConfig,
        tmp_dir: Path,
    ) -> None:
        super().__init__()
        self._svc = app_service
        self._source_path = source_path
        self._ocr_config = ocr_config
        self._tmp_dir = tmp_dir

    def run(self) -> None:
        try:
            orig, proc, steps = self._svc.reader.preprocess_preview(
                self._source_path, self._ocr_config, self._tmp_dir
            )
            self.done.emit(orig, proc, steps, "")
        except Exception as exc:  # noqa: BLE001  # preprocess_preview raises for unsupported files, missing deps, or I/O errors; report via the done signal
            self.done.emit(None, None, None, str(exc))


def _is_shown(widget: QWidget) -> bool:
    """Return whether *widget* still exists and is visible (a closed window is not)."""
    try:
        return widget.isVisible()
    except RuntimeError:  # its C++ object was deleted
        return False


class PreprocessPreview(QObject):
    """Makes the preview of one image preparation and shows it."""

    def __init__(
        self,
        window: QWidget,
        *,
        services: AppServices,
        panel: ConfigPanel,
        table: ResultsTable,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._services = services
        self._panel = panel
        self._table = table
        self._thread: _PreprocessPreviewThread | None = None

    def request(self, ocr_config: OcrConfig, *, parent: QWidget | None = None) -> None:
        """Preview *ocr_config*'s image preparation; dialogs open on top of *parent*."""
        owner = parent or self._window
        thread = self._thread
        if thread is not None and thread.isRunning():
            return  # a preview is already in flight; ignore the extra click

        image_suffixes = self._services.run.list_supported_suffixes() - {".txt"}
        # Prefer the file the user selected in the queue so the preview matches the
        # document they are looking at, not just whichever file sorts first.
        source_path: Path | None = next(
            (
                Path(sid)
                for sid in self._table.selected_source_ids()
                if Path(sid).suffix.lower() in image_suffixes and Path(sid).is_file()
            ),
            None,
        )
        input_dir_text = self._panel.get_input_dir()
        if source_path is None and input_dir_text:
            input_dir = Path(input_dir_text)
            if input_dir.is_dir():
                source_path = next(
                    (
                        f
                        for f in sorted(input_dir.iterdir())
                        if f.is_file() and f.suffix.lower() in image_suffixes
                    ),
                    None,
                )
        if source_path is None:
            supported_glob = " ".join(
                f"*{s}" for s in sorted(self._services.run.list_supported_suffixes() - {".txt"})
            )
            chosen = _dialogs.pick_file(owner, dlg.select_image_or_pdf(supported_glob))
            if chosen is None:
                return
            source_path = chosen

        tmp_dir = self._services.reader.resolve_preview_tmp_dir()

        thread = _PreprocessPreviewThread(self._services, source_path, ocr_config, tmp_dir)
        self._thread = thread
        # QueuedConnection: guarantees the callback (which builds widgets) runs on the
        # GUI thread even though the lambda itself isn't a QObject the auto-connection
        # logic can use to infer that.
        thread.done.connect(
            lambda orig, proc, steps, error: self._on_done(
                orig, proc, steps, error, ocr_config, tmp_dir, parent=owner
            ),
            Qt.ConnectionType.QueuedConnection,
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        thread.start()

    def _on_done(
        self,
        orig_path: Path | None,
        proc_path: Path | None,
        steps: list[str] | None,
        error: str,
        ocr_config: OcrConfig,
        tmp_dir: Path,
        *,
        parent: QWidget | None = None,
    ) -> None:
        QApplication.restoreOverrideCursor()
        # The Settings window may have been closed while the preview was being made.
        owner = parent if parent is not None and _is_shown(parent) else self._window
        if error:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            _dialogs.notify(owner, dlg.preprocessing_preview_failed(error))
            return
        assert orig_path is not None
        assert proc_path is not None
        assert steps is not None

        orig_pix = load_pixmap(orig_path)
        proc_pix = load_pixmap(proc_path)

        preview = QDialog(owner)
        preview.setWindowTitle(f"Preprocessing Preview — {ocr_config.preprocess_method}")
        preview.resize(1000, 620)

        vbox = QVBoxLayout(preview)
        hint = QLabel("Ctrl + scroll wheel to zoom")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #888; font-size: 8pt;")
        vbox.addWidget(hint)

        img_row = QHBoxLayout()
        step_label = ", ".join(steps) if steps else "unchanged"
        for title, pix in [("Original", orig_pix), (f"Processed ({step_label})", proc_pix)]:
            col = QVBoxLayout()
            hdr = QLabel(title)
            hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            zoom_view = ZoomableImageView()
            zoom_view.set_pixmap(pix)
            col.addWidget(hdr)
            col.addWidget(zoom_view, stretch=1)
            img_row.addLayout(col)
        vbox.addLayout(img_row, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(preview.reject)
        vbox.addWidget(buttons)
        preview.finished.connect(lambda _: shutil.rmtree(tmp_dir, ignore_errors=True))
        preview.exec()
