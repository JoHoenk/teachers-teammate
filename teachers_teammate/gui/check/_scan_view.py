"""Every stored page of a document in one scroll, fitted to the pane."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QGraphicsPixmapItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
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

    A page opens *fitted to the pane*: the whole page is in view, and the pages below it follow
    by scrolling.  A double click switches to *fit width* (a page as wide as the pane, read by
    scrolling) and back; the wheel zooms, and a double click after zooming fits again in the
    mode that was on.  The mode stays while the teacher steps through the documents.
    *Original* swaps every page that has one for the page as it was scanned and leaves the
    others prepared, keeping the place the teacher was reading.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prepared: tuple[Path, ...] = ()
        self._originals: tuple[Path | None, ...] = ()
        self._showing_originals = False
        self._pixmaps: list[QPixmap] = []
        self._items: list[QGraphicsPixmapItem | None] = []
        self._layout_key: tuple[int, int] = (0, 0)
        self._fit_page = True

    @property
    def fit_page(self) -> bool:
        """Whether a page is fitted whole to the pane (otherwise to its width)."""
        return self._fit_page

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
        self._layout_key = (0, 0)

    # ── Layout ────────────────────────────────────────────────────────────

    def _sources(self) -> list[Path]:
        sources: list[Path] = []
        for index, prepared in enumerate(self._prepared):
            original = self._originals[index] if index < len(self._originals) else None
            sources.append(original if self._showing_originals and original else prepared)
        return sources

    def _reload(self) -> None:
        self._pixmaps = [load_pixmap(path) for path in self._sources()]

    def _key(self) -> tuple[int, int]:
        """The size the layout depends on: the width, and the height only when a page is fitted."""
        viewport = self.viewport()
        return (viewport.width(), viewport.height() if self._fit_page else 0)

    def _page_width(self, pix: QPixmap, available_width: int, available_height: int) -> int:
        """Return the width a page is drawn at, in the fit mode that is on."""
        if not self._fit_page or pix.height() <= 0:
            return available_width
        return max(1, min(available_width, round(available_height * pix.width() / pix.height())))

    def _layout(self, *, keep_scroll: bool = False) -> None:
        fraction = self._scroll_fraction() if keep_scroll else 0.0
        self._scene.clear()
        self._pix_item = None
        self._items = []
        self._layout_key = self._key()
        available_width = max(self.viewport().width() - 2 * _MARGIN, 1)
        available_height = max(self.viewport().height() - 2 * _MARGIN, 1)
        if not self._pixmaps:
            self._show_text("(no page image)")
            self.setSceneRect(0, 0, available_width + 2 * _MARGIN, 1)
            return
        y = float(_MARGIN)
        for pix in self._pixmaps:
            if pix.isNull():
                self._items.append(None)
                y += self._add_message(y) + _GAP
                continue
            width = self._page_width(pix, available_width, available_height)
            scaled = pix.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
            item = self._scene.addPixmap(scaled)
            self._items.append(item)
            if item is None:
                continue
            # A page narrower than the pane sits in its middle.
            item.setPos(_MARGIN + (available_width - width) / 2, y)
            y += scaled.height() + _GAP
        self.setSceneRect(0, 0, available_width + 2 * _MARGIN, max(y, 1))
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

    def set_fit_page(self, on: bool) -> None:
        """Fit each page whole to the pane (*on*), or to the width of the pane."""
        self._fit_page = on
        self._layout(keep_scroll=True)

    def fit_to_view(self) -> None:
        """Lay the pages out again in the fit mode that is on."""
        if self._layout_key != self._key():
            self._layout()
            return
        self.resetTransform()
        self._fitted = True

    def mouseDoubleClickEvent(self, event: Any) -> None:
        """Fit again after a zoom; on a fitted scan switch between fit page and fit width."""
        if self._fitted:
            self.set_fit_page(not self._fit_page)
        else:
            self.fit_to_view()
        QGraphicsView.mouseDoubleClickEvent(self, event)

    def resizeEvent(self, event: Any) -> None:
        QGraphicsView.resizeEvent(self, event)
        if self._fitted and self._layout_key != self._key():
            self._layout()


class ScanPane(QWidget):
    """The scan with the strip above it: *Prepared · Original*, and how the pages were prepared.

    The two-part switch says which pages are shown: the prepared ones, or the pages as they
    were scanned, so the teacher can see what the automatic preparation did.  It is offered only
    where there is something to compare: a page has an original, and a preparation step acted
    on the pages (without one the original is the same picture).  The choice stays while the
    teacher steps from one document to the next.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = ScanView()
        self._steps: Sequence[str] | None = None

        self._prepared_btn = QPushButton(check_text.SCAN_PREPARED)
        self._prepared_btn.setToolTip(check_text.SCAN_PREPARED_TIP)
        self._original_btn = QPushButton(check_text.SCAN_ORIGINAL)
        self._original_btn.setToolTip(check_text.SCAN_ORIGINAL_TIP)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for button in (self._prepared_btn, self._original_btn):
            button.setCheckable(True)
            button.setFlat(True)
            group.addButton(button)
        self._prepared_btn.setChecked(True)
        self._original_btn.toggled.connect(self._on_original_toggled)
        self._switch = QWidget()
        switch = QHBoxLayout(self._switch)
        switch.setContentsMargins(0, 0, 0, 0)
        switch.setSpacing(2)
        switch.addWidget(self._prepared_btn)
        switch.addWidget(self._original_btn)
        self._switch.setVisible(False)

        self._caption = QLabel()
        self._caption.setStyleSheet("color: #888;")
        self._caption.setVisible(False)

        strip = QHBoxLayout()
        strip.setContentsMargins(4, 0, 4, 0)
        strip.addWidget(self._switch)
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
        """Whether the *Prepared · Original* switch is on offer."""
        return not self._switch.isHidden()

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
        offered = has_original and (steps is None or txt.preparation_changed_pages(steps))
        self._switch.setVisible(offered)
        if not offered and self._original_btn.isChecked():
            self._prepared_btn.setChecked(True)  # toggled() takes the originals off the view
        self.view.set_pages(prepared, originals)
        self._refresh_caption()

    def clear(self) -> None:
        """Forget the pages: no document is open."""
        self.view.clear()
        self._steps = None
        self._switch.setVisible(False)
        self._caption.setVisible(False)

    def _on_original_toggled(self, checked: bool) -> None:
        self.view.show_originals(checked)
        self._refresh_caption()

    def _refresh_caption(self) -> None:
        if self._original_btn.isChecked() and not self._switch.isHidden():
            text = check_text.SCAN_SHOWING_ORIGINAL
        elif self._steps is not None:
            text = txt.prepared_caption(self._steps)
        else:
            self._caption.setVisible(False)
            return
        self._caption.setText(text)
        self._caption.setVisible(True)
