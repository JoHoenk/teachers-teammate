"""Every stored page of a document in one scroll, fitted to the width of the pane."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...texts import check as check_text
from .. import _user_text as txt
from .._image_utils import load_pixmap
from .._image_view import ZoomableImageView

#: Blank space around and between the pages.
_MARGIN = 8
_GAP = 12


class ScanView(ZoomableImageView):
    """The pages of one document, stacked in a single scene.

    The pages are laid out at the width of the pane, so a teacher reads the whole document by
    scrolling; the wheel zooms, a double click fits again.  *Original* swaps every page that has
    one for the page as it was scanned and leaves the others prepared, keeping the place the
    teacher was reading.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prepared: tuple[Path, ...] = ()
        self._originals: tuple[Path | None, ...] = ()
        self._showing_originals = False
        self._pixmaps: list[QPixmap] = []
        self._items: list[QGraphicsPixmapItem | None] = []
        self._layout_width = 0

    @property
    def showing_originals(self) -> bool:
        """Whether the pages are shown as they were scanned."""
        return self._showing_originals

    @property
    def page_count(self) -> int:
        """How many pages are shown."""
        return len(self._prepared)

    def set_pages(
        self,
        prepared: Sequence[Path],
        originals: Sequence[Path | None] = (),
    ) -> None:
        """Show *prepared*, and *originals* where the page as scanned was kept."""
        self._prepared = tuple(prepared)
        self._originals = tuple(originals)
        self._reload()
        self._layout()

    def show_originals(self, on: bool) -> None:
        """Swap every page that has an original for the page as it was scanned."""
        if on == self._showing_originals:
            return
        self._showing_originals = on
        self._reload()
        self._layout(keep_scroll=True)

    def clear(self) -> None:
        """Forget the pages: no document is open."""
        super().clear()
        self._prepared = ()
        self._originals = ()
        self._pixmaps = []
        self._items = []
        self._layout_width = 0

    # ── Layout ────────────────────────────────────────────────────────────

    def _sources(self) -> list[Path]:
        sources: list[Path] = []
        for index, prepared in enumerate(self._prepared):
            original = self._originals[index] if index < len(self._originals) else None
            sources.append(original if self._showing_originals and original else prepared)
        return sources

    def _reload(self) -> None:
        self._pixmaps = [load_pixmap(path) for path in self._sources()]

    def _layout(self, *, keep_scroll: bool = False) -> None:
        fraction = self._scroll_fraction() if keep_scroll else 0.0
        self._scene.clear()
        self._pix_item = None
        self._items = []
        width = max(self.viewport().width() - 2 * _MARGIN, 1)
        self._layout_width = width
        if not self._pixmaps:
            self._show_text("(no page image)")
            self.setSceneRect(0, 0, width + 2 * _MARGIN, 1)
            return
        y = float(_MARGIN)
        for pix in self._pixmaps:
            if pix.isNull():
                self._items.append(None)
                y += self._add_message(y) + _GAP
                continue
            scaled = pix.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
            item = self._scene.addPixmap(scaled)
            self._items.append(item)
            if item is None:
                continue
            item.setPos(_MARGIN, y)
            y += scaled.height() + _GAP
        self.setSceneRect(0, 0, width + 2 * _MARGIN, max(y, 1))
        self.resetTransform()
        self._fitted = True
        self._set_scroll_fraction(fraction)

    def _add_message(self, y: float) -> float:
        """Write "(image unavailable)" where a page is missing; return the height it took."""
        item = self._scene.addText("(image unavailable)")
        if item is None:
            return 0.0
        item.setDefaultTextColor(QColor("#888888"))
        item.setPos(_MARGIN, y)
        return item.boundingRect().height()

    def _scroll_fraction(self) -> float:
        bar = self.verticalScrollBar()
        span = bar.maximum() - bar.minimum()
        return (bar.value() - bar.minimum()) / span if span else 0.0

    def _set_scroll_fraction(self, fraction: float) -> None:
        bar = self.verticalScrollBar()
        span = bar.maximum() - bar.minimum()
        bar.setValue(bar.minimum() + round(span * fraction))

    # ── Zoom ──────────────────────────────────────────────────────────────

    def fit_to_view(self) -> None:
        """Lay the pages out at the width of the pane again."""
        if self._layout_width != max(self.viewport().width() - 2 * _MARGIN, 1):
            self._layout()
            return
        self.resetTransform()
        self._fitted = True

    def resizeEvent(self, event: Any) -> None:
        QGraphicsView.resizeEvent(self, event)
        if self._fitted and self._layout_width != max(self.viewport().width() - 2 * _MARGIN, 1):
            self._layout()


class ScanPane(QWidget):
    """The scan with the strip above it: how the pages were prepared, and *Original*.

    *Original* shows the pages as they were scanned, so the teacher can see what the automatic
    preparation did to them.  It is offered only where a page has an original, and it stays on
    while the teacher steps from one document to the next.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = ScanView()
        self._steps: Sequence[str] | None = None

        self._original_btn = QToolButton()
        self._original_btn.setText(check_text.SCAN_ORIGINAL)
        self._original_btn.setToolTip(check_text.SCAN_ORIGINAL_TIP)
        self._original_btn.setCheckable(True)
        self._original_btn.toggled.connect(self._on_original_toggled)
        self._original_btn.setVisible(False)

        self._caption = QLabel()
        self._caption.setStyleSheet("color: #888;")
        self._caption.setVisible(False)

        strip = QHBoxLayout()
        strip.setContentsMargins(4, 0, 4, 0)
        strip.addWidget(self._original_btn)
        strip.addWidget(self._caption, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(strip)
        layout.addWidget(self.view, 1)

    @property
    def page_count(self) -> int:
        """How many pages are shown."""
        return self.view.page_count

    @property
    def showing_originals(self) -> bool:
        """Whether the pages are shown as they were scanned."""
        return self.view.showing_originals

    @property
    def original_offered(self) -> bool:
        """Whether the *Original* toggle is on offer (a page has an original to show)."""
        return not self._original_btn.isHidden()

    def set_pages(
        self,
        prepared: Sequence[Path],
        originals: Sequence[Path | None] = (),
        steps: Sequence[str] | None = None,
    ) -> None:
        """Show *prepared*, *originals* where the page as scanned was kept, and the *steps*.

        *steps* are the preparation steps that were applied; ``None`` when they are unknown.
        """
        has_original = any(path is not None and path.is_file() for path in originals)
        self._steps = steps
        self._original_btn.setVisible(has_original)
        if not has_original and self._original_btn.isChecked():
            self._original_btn.blockSignals(True)
            self._original_btn.setChecked(False)
            self._original_btn.blockSignals(False)
            self.view.show_originals(False)
        self.view.set_pages(prepared, originals)
        self._refresh_caption()

    def clear(self) -> None:
        """Forget the pages: no document is open."""
        self.view.clear()
        self._steps = None
        self._original_btn.setVisible(False)
        self._caption.setVisible(False)

    def _on_original_toggled(self, checked: bool) -> None:
        self.view.show_originals(checked)
        self._refresh_caption()

    def _refresh_caption(self) -> None:
        if self._original_btn.isChecked() and not self._original_btn.isHidden():
            text = check_text.SCAN_SHOWING_ORIGINAL
        elif self._steps is not None:
            text = txt.prepared_caption(self._steps)
        else:
            self._caption.setVisible(False)
            return
        self._caption.setText(text)
        self._caption.setVisible(True)
