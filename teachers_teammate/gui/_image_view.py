"""A QGraphicsView that shows a page image with interactive zoom and pan."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QWidget


class ZoomableImageView(QGraphicsView):
    """QGraphicsView for displaying a QPixmap with interactive zoom and pan."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setStyleSheet("background: #f0f0f0;")
        self._pix_item: QGraphicsPixmapItem | None = None
        self._fitted = True

    def set_pixmap(self, pix: QPixmap) -> None:
        self._scene.clear()
        self._pix_item = None
        if pix.isNull():
            self._show_text("(image unavailable)")
            return
        pix_item = self._scene.addPixmap(pix)
        assert pix_item is not None
        self._pix_item = pix_item
        self.setSceneRect(pix_item.boundingRect())
        self._fitted = True
        self.fitInView(pix_item, Qt.AspectRatioMode.KeepAspectRatio)

    def swap_pixmap(self, pix: QPixmap) -> None:
        """Replace the image with another rendering of the same page, keeping zoom and pan.

        The visible region is preserved relative to the image size, so a different
        resolution (e.g. before/after dewarping) still shows the same part of the page.
        Falls back to :meth:`set_pixmap` when fitted or when there is nothing to compare.
        """
        old_item = self._pix_item
        if old_item is None or pix.isNull() or self._fitted:
            self.set_pixmap(pix)
            return
        old_rect = old_item.boundingRect()
        center = self.mapToScene(self.viewport().rect().center())
        frac_x = center.x() / old_rect.width()
        frac_y = center.y() / old_rect.height()
        zoom = self.transform().m11()
        self._scene.clear()
        new_item = self._scene.addPixmap(pix)
        assert new_item is not None
        self._pix_item = new_item
        new_rect = new_item.boundingRect()
        self.setSceneRect(new_rect)
        self.resetTransform()
        factor = zoom * old_rect.width() / new_rect.width()
        self.scale(factor, factor)
        self.centerOn(frac_x * new_rect.width(), frac_y * new_rect.height())

    def show_message(self, text: str) -> None:
        self._scene.clear()
        self._pix_item = None
        self._show_text(text)

    def clear(self) -> None:
        self._scene.clear()
        self._pix_item = None

    def _show_text(self, text: str) -> None:
        item = self._scene.addText(text)
        if item is not None:
            item.setDefaultTextColor(QColor("#888888"))

    def zoom_in(self) -> None:
        self._fitted = False
        self.scale(1.25, 1.25)

    def zoom_out(self) -> None:
        self._fitted = False
        self.scale(0.8, 0.8)

    def fit_to_view(self) -> None:
        if self._pix_item is not None:
            self._fitted = True
            self.fitInView(self._pix_item, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event: Any) -> None:
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()

    def mouseDoubleClickEvent(self, event: Any) -> None:
        self.fit_to_view()
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self._pix_item is not None and self._fitted:
            self.fitInView(self._pix_item, Qt.AspectRatioMode.KeepAspectRatio)
