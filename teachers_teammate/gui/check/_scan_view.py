"""Every stored page of a document in one scroll, fitted to the pane."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
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
#: How far the buttons and Ctrl + wheel zoom out of the fitted page, and the least they zoom in.
_ZOOM_MIN = 0.5
_ZOOM_MAX = 4.0
_ZOOM_STEP = 1.25
#: The most a page is enlarged: twice its own pixels, however far the pane is zoomed.
_ZOOM_NATIVE = 2.0
#: The width a page is kept at while it is not in view.  A page fitted to a pane this wide is
#: sharp; a wider pane is read by zooming, which brings the page's own image back.
_THUMBNAIL_WIDTH = 700


class ScanView(ZoomableImageView):
    """The pages of one document, stacked in a single scene.

    A page opens *fitted to the pane*: the whole page is in view, and the pages below it follow
    by scrolling.  A double click switches to *fit width* (a page as wide as the pane, read by
    scrolling) and back; zoom in and out (buttons, Ctrl + wheel) stay between :data:`_ZOOM_MIN`
    times the fitted page and :meth:`_max_zoom` (twice the page's own pixels, or
    :data:`_ZOOM_MAX` times the fitted page where that is more), and a double click after
    zooming fits again in the mode that was on.  The mode stays while the teacher steps through
    the documents.

    A page is drawn from its own image, scaled down to the pane, so zooming in shows the pixels
    that were scanned instead of an enlarged thumbnail.  Only the pages in view keep that image:
    the others hold a copy at :data:`_THUMBNAIL_WIDTH`, which is swapped for the real one when
    the teacher zooms or scrolls to them.

    The vertical scroll bar is always there and the horizontal one only while zoomed: a fitted
    layout is built from the size of the viewport, so a bar that came and went with the content
    would change that size and start the layout again, for ever.
    *Original* swaps every page that has one for the page as it was scanned and leaves the
    others prepared, keeping the place the teacher was reading.
    """

    #: The fit mode or the zoom changed (the zoom buttons follow it).
    view_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._prepared: tuple[Path, ...] = ()
        self._originals: tuple[Path | None, ...] = ()
        self._showing_originals = False
        self._sources: list[Path] = []
        self._sizes: list[tuple[int, int]] = []
        self._thumbs: list[QPixmap] = []
        self._full: dict[int, QPixmap] = {}
        self._full_pages: set[int] = set()
        self._items: list[QGraphicsPixmapItem | None] = []
        self._widths: list[int] = []
        self._layout_key: tuple[int, int] = (0, 0)
        self._fit_page = True
        self.verticalScrollBar().valueChanged.connect(self._refresh_full_pages)

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
        self._sources = []
        self._sizes = []
        self._thumbs = []
        self._full = {}
        self._full_pages = set()
        self._items = []
        self._widths = []
        self._layout_key = (0, 0)

    # ── Layout ────────────────────────────────────────────────────────────

    def _sources_of(self) -> list[Path]:
        """Return the image each page shows: the prepared one, or the original where asked."""
        sources: list[Path] = []
        for index, prepared in enumerate(self._prepared):
            original = self._originals[index] if index < len(self._originals) else None
            sources.append(original if self._showing_originals and original else prepared)
        return sources

    def _reload(self) -> None:
        """Read every page once: its own size, and a copy small enough to keep them all."""
        self._sources = self._sources_of()
        self._sizes = []
        self._thumbs = []
        self._full = {}
        self._full_pages = set()
        for path in self._sources:
            pix = load_pixmap(path)
            self._sizes.append((pix.width(), pix.height()))
            self._thumbs.append(_thumbnail(pix, self.devicePixelRatioF()))

    def _key(self) -> tuple[int, int]:
        """The size the layout depends on: the width, and the height only when a page is fitted."""
        viewport = self.viewport()
        return (viewport.width(), viewport.height() if self._fit_page else 0)

    def _page_width(self, index: int, available_width: int, available_height: int) -> int:
        """Return the width a page is drawn at, in the fit mode that is on."""
        width, height = self._sizes[index]
        if not self._fit_page or height <= 0:
            return available_width
        return max(1, min(available_width, round(available_height * width / height)))

    def _layout(self, *, keep_scroll: bool = False) -> None:
        fraction = self._scroll_fraction() if keep_scroll else 0.0
        self._scene.clear()
        self._pix_item = None
        self._items = []
        self._widths = []
        self._layout_key = self._key()
        available_width = max(self.viewport().width() - 2 * _MARGIN, 1)
        available_height = max(self.viewport().height() - 2 * _MARGIN, 1)
        if not self._sources:
            self._show_text("(no page image)")
            self.setSceneRect(0, 0, available_width + 2 * _MARGIN, 1)
            return
        y = float(_MARGIN)
        for index in range(len(self._sources)):
            width, height = self._sizes[index]
            if width <= 0 or height <= 0:
                self._items.append(None)
                self._widths.append(0)
                y += self._add_message(y) + _GAP
                continue
            drawn = self._page_width(index, available_width, available_height)
            item = self._scene.addPixmap(self._page_pixmap(index))
            self._items.append(item)
            self._widths.append(drawn)
            if item is None:
                continue
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            item.setScale(drawn / max(item.pixmap().width(), 1))
            # A page narrower than the pane sits in its middle.
            item.setPos(_MARGIN + (available_width - drawn) / 2, y)
            y += drawn * height / width + _GAP
        # No gap after the last page: a page fitted to the pane then fills it exactly.
        self.setSceneRect(0, 0, available_width + 2 * _MARGIN, max(y - _GAP + _MARGIN, 1))
        self.resetTransform()
        self._set_fitted(True)
        self._set_scroll_fraction(fraction)
        self._refresh_full_pages()

    # ── The pages in view keep their own image ────────────────────────────

    def _page_pixmap(self, index: int) -> QPixmap:
        """Return the image of page *index*: its own when it is in memory, else its copy."""
        full = self._full.get(index)
        return full if full is not None else self._thumbs[index]

    def _pages_in_view(self) -> set[int]:
        """Return the pages the teacher can see, and the one before and after them."""
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        shown = [
            index
            for index, item in enumerate(self._items)
            if item is not None and item.sceneBoundingRect().intersects(visible)
        ]
        if not shown:
            return set()
        first, last = max(shown[0] - 1, 0), min(shown[-1] + 1, len(self._items) - 1)
        return {index for index in range(first, last + 1) if not self._thumbs[index].isNull()}

    def _refresh_full_pages(self, *_args: Any) -> None:
        """Keep the pages in view at their own size, and the others as their copies."""
        wanted = self._pages_in_view()
        if wanted == self._full_pages:
            return
        self._full_pages = wanted
        self._full = {
            index: self._full[index] if index in self._full else load_pixmap(self._sources[index])
            for index in wanted
        }
        for index in range(len(self._items)):
            self._show_page(index)

    def _show_page(self, index: int) -> None:
        """Put the image page *index* has now into its item, at the width the layout gave it."""
        item = self._items[index]
        drawn = self._widths[index]
        if item is None or drawn <= 0:
            return
        pix = self._page_pixmap(index)
        if pix.isNull():
            return
        item.setPixmap(pix)
        item.setScale(drawn / pix.width())

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

    def _set_fitted(self, fitted: bool) -> None:
        """Remember whether the pages are fitted; only a zoomed scan scrolls sideways."""
        self._fitted = fitted
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if fitted
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.view_changed.emit()

    @property
    def zoomed(self) -> bool:
        """Whether the teacher zoomed away from the fitted pages."""
        return not self._fitted

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
        self._set_fitted(True)

    def _fitted_scale(self) -> float:
        """Return how large a page is drawn at zoom 1, as a share of its own pixels."""
        for index, item in enumerate(self._items):
            if item is not None and self._sizes[index][0] > 0:
                return self._widths[index] / self._sizes[index][0]
        return 0.0

    def _max_zoom(self) -> float:
        """Return the largest zoom: twice the page's own pixels, and never less than 4x fitted."""
        scale = self._fitted_scale()
        return max(_ZOOM_MAX, _ZOOM_NATIVE / scale) if scale > 0 else _ZOOM_MAX

    def _zoom_by(self, factor: float) -> None:
        """Zoom by *factor*, keeping within the limits around the fitted pages."""
        current = self.transform().m11()
        target = min(max(current * factor, _ZOOM_MIN), self._max_zoom())
        if target == current:
            return
        self._set_fitted(False)
        self.scale(target / current, target / current)
        self._refresh_full_pages()

    def zoom_in(self) -> None:
        """Make the pages larger."""
        self._zoom_by(_ZOOM_STEP)

    def zoom_out(self) -> None:
        """Make the pages smaller."""
        self._zoom_by(1 / _ZOOM_STEP)

    def wheelEvent(self, event: Any) -> None:
        """Ctrl + wheel zooms; the plain wheel scrolls."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            return
        QGraphicsView.wheelEvent(self, event)

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


def _thumbnail(pix: QPixmap, ratio: float) -> QPixmap:
    """Return *pix* at the width a page is kept at while it is not in view.

    *ratio* is the screen's device pixel ratio: the copy is that much wider than the pane, so a
    page fitted to it is drawn from its own pixels and not from an enlarged thumbnail.
    """
    if pix.isNull():
        return pix
    width = round(_THUMBNAIL_WIDTH * max(ratio, 1.0))
    if pix.width() <= width:
        return pix
    return pix.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)


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

        self._build_switch()
        self._build_zoom()

        self._caption = QLabel()
        self._caption.setStyleSheet("color: #888;")
        self._caption.setVisible(False)

        strip = QHBoxLayout()
        strip.setContentsMargins(4, 0, 4, 0)
        strip.addWidget(self._switch)
        strip.addWidget(self._caption, 1)
        strip.addWidget(self._zoom)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(strip)
        layout.addWidget(self.view, 1)

    def _build_switch(self) -> None:
        """Build the *Prepared · Original* switch, hidden until a page has an original."""
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

    def _build_zoom(self) -> None:
        """Build the zoom buttons and tie them to the view."""
        self._zoom_out_btn = QPushButton(check_text.SCAN_ZOOM_OUT)
        self._zoom_out_btn.setToolTip(check_text.SCAN_ZOOM_OUT_TIP)
        self._zoom_in_btn = QPushButton(check_text.SCAN_ZOOM_IN)
        self._zoom_in_btn.setToolTip(check_text.SCAN_ZOOM_IN_TIP)
        self._fit_page_btn = QPushButton(check_text.SCAN_FIT_PAGE)
        self._fit_page_btn.setToolTip(check_text.SCAN_FIT_PAGE_TIP)
        self._fit_width_btn = QPushButton(check_text.SCAN_FIT_WIDTH)
        self._fit_width_btn.setToolTip(check_text.SCAN_FIT_WIDTH_TIP)
        self._zoom = QWidget()
        zoom = QHBoxLayout(self._zoom)
        zoom.setContentsMargins(0, 0, 0, 0)
        zoom.setSpacing(2)
        for button in (
            self._zoom_out_btn,
            self._zoom_in_btn,
            self._fit_page_btn,
            self._fit_width_btn,
        ):
            button.setFlat(True)
            zoom.addWidget(button)
        for button in (self._fit_page_btn, self._fit_width_btn):
            button.setCheckable(True)
        self._zoom_out_btn.clicked.connect(self.view.zoom_out)
        self._zoom_in_btn.clicked.connect(self.view.zoom_in)
        self._fit_page_btn.clicked.connect(lambda: self.view.set_fit_page(True))
        self._fit_width_btn.clicked.connect(lambda: self.view.set_fit_page(False))
        self.view.view_changed.connect(self._sync_zoom_buttons)
        self._sync_zoom_buttons()

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

    def _sync_zoom_buttons(self) -> None:
        """Press the fit button that is on; a zoomed scan has none pressed."""
        zoomed = self.view.zoomed
        self._fit_page_btn.setChecked(not zoomed and self.view.fit_page)
        self._fit_width_btn.setChecked(not zoomed and not self.view.fit_page)

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
