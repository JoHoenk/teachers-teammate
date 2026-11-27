"""The preprocessing preview: how a scan looks before and after the image preparation.

Preparing a full-resolution scan (denoise, deskew, …) takes seconds, so it runs on the shared
background pool; the dialog shows the two pictures side by side.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import cast

from PySide6.QtCore import QObject, Qt
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
from ..interfaces import PreparedPreview
from ..messages import message_of
from ..texts import dialogs as dlg
from ..texts.models import PREVIEW_ZOOM_HINT, preview_title
from . import _dialogs
from ._background import run_in_background
from ._config_panel import ConfigPanel
from ._errors import show_failure
from ._image_utils import load_pixmap
from ._image_view import ZoomableImageView
from ._results_table import ResultsTable


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
        #: True while a preview is being made: a second click would start a second one.
        self._busy = False

    def request(self, ocr_config: OcrConfig, *, parent: QWidget | None = None) -> None:
        """Preview *ocr_config*'s image preparation; dialogs open on top of *parent*."""
        owner = parent or self._window
        if self._busy:
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

        def prepare() -> PreparedPreview:
            return self._services.reader.preprocess_preview(source_path, ocr_config, tmp_dir)

        def arrived(result: object, error: BaseException | None) -> None:
            preview = cast("PreparedPreview", result) if error is None else None
            self._on_done(
                preview.original if preview else None,
                preview.preprocessed if preview else None,
                preview.steps if preview else None,
                error,
                ocr_config,
                tmp_dir,
                parent=owner,
            )

        self._busy = True
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        # *self* outlives the Settings window that asked: the picture then opens over the window.
        run_in_background(prepare, arrived, owner=self)

    def _on_done(
        self,
        orig_path: Path | None,
        proc_path: Path | None,
        steps: list[str] | None,
        error: BaseException | None,
        ocr_config: OcrConfig,
        tmp_dir: Path,
        *,
        parent: QWidget | None = None,
    ) -> None:
        self._busy = False
        QApplication.restoreOverrideCursor()
        # The Settings window may have been closed while the preview was being made.
        owner = parent if parent is not None and _is_shown(parent) else self._window
        if error is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            show_failure(owner, dlg.preprocessing_preview_failed(str(message_of(error))), error)
            return
        if orig_path is None or proc_path is None or steps is None:
            raise RuntimeError("a preview that did not fail has both pages and its steps")

        orig_pix = load_pixmap(orig_path)
        proc_pix = load_pixmap(proc_path)

        preview = QDialog(owner)
        preview.setWindowTitle(preview_title(ocr_config.preprocess_method))
        preview.resize(1000, 620)

        vbox = QVBoxLayout(preview)
        hint = QLabel(PREVIEW_ZOOM_HINT)
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
