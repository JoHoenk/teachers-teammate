"""Zoomable image view, inline word-level diff widget, and preview panel."""

from __future__ import annotations

from collections.abc import Callable
import difflib
import html
import re
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ._types import StageStatus


class _StageNavBar(QWidget):
    """Footer bar below a text tab showing the current stage status and a run button."""

    run_requested = Signal()

    def __init__(self, next_stage: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._next_stage = next_stage
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("color: #888; font-size: 9pt;")
        self._run_btn = QPushButton(f"▶ Run {next_stage.capitalize()}")
        self._run_btn.setMinimumHeight(28)
        _btn_font = self._run_btn.font()
        _btn_font.setPointSize(10)
        _btn_font.setBold(True)
        self._run_btn.setFont(_btn_font)
        self._run_btn.setToolTip(f"Run {next_stage} on this document again.")
        self._run_btn.clicked.connect(self.run_requested)
        layout.addWidget(self._status_lbl)
        layout.addStretch()
        layout.addWidget(self._run_btn)

    def update_status(
        self,
        _current_status: StageStatus,
        next_enabled: bool,
        next_status: StageStatus,
        next_invalidated: bool = False,
    ) -> None:
        """Refresh the coloured status label and enable/disable the run button."""
        stage = self._next_stage.capitalize()
        prev = "Extracted text" if stage in ("Proofreading", "Content review") else "Proofread text"
        if next_status == StageStatus.DONE:
            text, color = f"{stage} up to date", "#27ae60"
        elif next_status == StageStatus.ERROR:
            text, color = f"{stage} failed", "#c0392b"
        elif next_invalidated:
            text, color = f"{prev} changed — {stage.lower()} needs rerun", "#3498db"
        else:
            text, color = f"{stage} not run yet", "#f39c12"
        if not next_enabled:
            text, color = f"{stage} is off — click ▶ to run once", "#888"
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 9pt;")
        can_run = next_status in (StageStatus.PENDING, StageStatus.ERROR)
        self._run_btn.setVisible(True)
        self._run_btn.setEnabled(can_run)


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


class DiffWidget(QTextEdit):
    """Inline word-level diff: deleted in red/strikethrough, added in green."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        font = QFont()
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSize(9)
        self.setFont(font)

    def set_texts(self, original: str, corrected: str) -> None:
        if not original:
            self.setPlainText("(no OCR text available for comparison)")
            return
        if not corrected:
            self.setPlainText("(no correction available - skipped or not yet generated)")
            return

        orig_tokens = self._tokenize(original)
        corr_tokens = self._tokenize(corrected)
        matcher = difflib.SequenceMatcher(None, orig_tokens, corr_tokens, autojunk=False)

        parts = [
            "<html><body>"
            "<pre style='white-space: pre-wrap; font-family: monospace; font-size: 9pt;'>"
        ]
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                parts.append(self._escape_tokens(orig_tokens[i1:i2]))
            elif tag == "replace":
                old = self._escape_tokens(orig_tokens[i1:i2])
                new = self._escape_tokens(corr_tokens[j1:j2])
                parts.append(
                    f'<span style="background:#ffd7d7;text-decoration:line-through;'
                    f'color:#c0392b;">{old}</span>'
                    f'<span style="background:#d7ffd7;color:#27ae60;">{new}</span>'
                )
            elif tag == "delete":
                old = self._escape_tokens(orig_tokens[i1:i2])
                parts.append(
                    f'<span style="background:#ffd7d7;text-decoration:line-through;'
                    f'color:#c0392b;">{old}</span>'
                )
            elif tag == "insert":
                new = self._escape_tokens(corr_tokens[j1:j2])
                parts.append(f'<span style="background:#d7ffd7;color:#27ae60;">{new}</span>')
        parts.append("</pre></body></html>")
        self.setHtml("".join(parts))

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Split text into diff tokens while preserving whitespace and punctuation."""
        return re.findall(r"\w+|[^\w\s]|\s+", text, flags=re.UNICODE)

    @staticmethod
    def _escape_tokens(tokens: list[str]) -> str:
        return "".join(html.escape(token) for token in tokens)

    def clear_diff(self) -> None:
        self.clear()


class _ZoomScrollFilter(QObject):
    """Event filter: Ctrl+Scroll on a QTextEdit delegates to a zoom callback."""

    def __init__(self, zoom_cb: Callable[[int], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._zoom_cb = zoom_cb

    def eventFilter(self, _obj: QObject | None, event: QEvent | None) -> bool:
        if event is not None and event.type() == QEvent.Type.Wheel:
            from PySide6.QtGui import QWheelEvent  # noqa: PLC0415

            we: QWheelEvent = event  # ty: ignore[invalid-assignment]  # event.type()==Wheel guarantees a QWheelEvent; PySide6 has no typed narrowing
            if we.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._zoom_cb(1 if we.angleDelta().y() > 0 else -1)
                return True
        return False


class PreviewPanel(QWidget):
    """Zoomable preprocessed image + OCR/correction/evaluation + diff."""

    ocr_text_edited = Signal(str)
    correction_text_edited = Signal(str)
    run_stage_requested = Signal(str, str)  # source_id, stage_name

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._base_font_pt = 9
        self._text_zoom = 0
        self._font_size_lbl: QLabel  # assigned in _build_text_zoom_bar

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_image_panel())
        splitter.addWidget(self._build_text_panel())
        splitter.setSizes([300, 600])
        layout.addWidget(splitter)

        self._loading = False
        self._last_raw_text = ""
        self._last_correction_text = ""
        self._current_source_id: str = ""
        self._ocr_text.textChanged.connect(self._on_ocr_text_changed)
        self._correction_text.textChanged.connect(self._on_correction_text_changed)

    def _build_image_panel(self) -> QWidget:
        self._image_view = ZoomableImageView()
        self._image_view.setMinimumWidth(150)

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        toolbar = QWidget()
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(4, 2, 4, 2)
        tb_layout.setSpacing(4)
        for _label, _tip, _slot in (
            ("+", "Zoom in", self._image_view.zoom_in),
            ("-", "Zoom out", self._image_view.zoom_out),
            ("Fit", "Fit to view (or double-click image)", self._image_view.fit_to_view),
        ):
            _btn = QToolButton()
            _btn.setText(_label)
            _btn.setToolTip(_tip)
            _btn.clicked.connect(_slot)
            tb_layout.addWidget(_btn)
        tb_layout.addStretch()
        _hint_lbl = QLabel("Scroll: zoom · Drag: pan")
        _hint_lbl.setStyleSheet("color: #888; font-size: 8pt;")
        tb_layout.addWidget(_hint_lbl)

        vbox.addWidget(toolbar)
        vbox.addWidget(self._image_view, stretch=1)
        return container

    def _build_text_panel(self) -> QWidget:
        self._text_tabs = QTabWidget()
        _inner_tab_font = self._text_tabs.tabBar().font()
        _inner_tab_font.setPointSize(13)
        self._text_tabs.tabBar().setFont(_inner_tab_font)

        font = QFont()
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSize(self._base_font_pt)

        self._ocr_text = QTextEdit()
        self._ocr_text.setReadOnly(False)
        self._ocr_text.setFont(font)

        self._correction_text = QTextEdit()
        self._correction_text.setReadOnly(False)
        self._correction_text.setFont(font)

        self._evaluation_text = QTextEdit()
        self._evaluation_text.setReadOnly(True)
        self._evaluation_text.setFont(font)

        self._diff_widget = DiffWidget()

        self._ocr_nav = _StageNavBar("proofreading")
        self._ocr_nav.run_requested.connect(lambda: self._on_run_stage("correction"))
        self._correction_nav = _StageNavBar("content review")
        self._correction_nav.run_requested.connect(lambda: self._on_run_stage("evaluation"))

        self._text_tabs.addTab(self._make_nav_tab(self._ocr_text, self._ocr_nav), "Extracted Text")
        self._text_tabs.addTab(
            self._make_nav_tab(self._correction_text, self._correction_nav), "Proofread Text"
        )
        self._text_tabs.addTab(self._diff_widget, "± Changes")
        self._text_tabs.addTab(self._evaluation_text, "Content Review")

        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(self._build_text_zoom_bar())
        vbox.addWidget(self._text_tabs, stretch=1)

        scroll_filter = _ZoomScrollFilter(self._change_text_zoom, self)
        for _te in (self._ocr_text, self._correction_text, self._evaluation_text):
            _te.installEventFilter(scroll_filter)

        return container

    def _build_text_zoom_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        layout.addStretch()
        zoom_out_btn = QPushButton("A-")
        zoom_out_btn.setFixedWidth(32)
        zoom_out_btn.setToolTip("Make text smaller (Ctrl + scroll down)")
        zoom_out_btn.clicked.connect(lambda: self._change_text_zoom(-1))
        self._font_size_lbl = QLabel(f"{self._base_font_pt} pt")
        self._font_size_lbl.setStyleSheet("color: #888; font-size: 9pt;")
        zoom_in_btn = QPushButton("A+")
        zoom_in_btn.setFixedWidth(32)
        zoom_in_btn.setToolTip("Make text larger (Ctrl + scroll up)")
        zoom_in_btn.clicked.connect(lambda: self._change_text_zoom(1))
        layout.addWidget(zoom_out_btn)
        layout.addWidget(self._font_size_lbl)
        layout.addWidget(zoom_in_btn)
        return bar

    # ── text zoom ─────────────────────────────────────────────────────────────────

    def _change_text_zoom(self, delta: int) -> None:
        new_zoom = max(-5, min(10, self._text_zoom + delta))
        if new_zoom == self._text_zoom:
            return
        self._text_zoom = new_zoom
        new_pt = self._base_font_pt + self._text_zoom
        font = QFont()
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSize(new_pt)
        for widget in (self._ocr_text, self._correction_text, self._evaluation_text):
            widget.setFont(font)
        self._font_size_lbl.setText(f"{new_pt} pt")

    # ── stage navigation ──────────────────────────────────────────────────────────

    @staticmethod
    def _make_nav_tab(text_widget: QTextEdit, nav_bar: _StageNavBar) -> QWidget:
        """Wrap a text editor and a nav bar into a single tab widget."""
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)
        tab_layout.addWidget(text_widget)
        tab_layout.addWidget(nav_bar)
        return tab

    def update_stage_statuses(
        self,
        source_id: str,
        ocr: StageStatus,
        correction: StageStatus,
        evaluation: StageStatus,
        correction_enabled: bool,
        evaluation_enabled: bool,
        correction_invalidated: bool = False,
        evaluation_invalidated: bool = False,
    ) -> None:
        """Refresh stage-status badges and run-button visibility."""
        self._current_source_id = source_id
        self._ocr_nav.update_status(
            ocr, correction_enabled, correction, next_invalidated=correction_invalidated
        )
        self._correction_nav.update_status(
            correction, evaluation_enabled, evaluation, next_invalidated=evaluation_invalidated
        )
        self._text_tabs.setTabText(0, f"Extracted Text  {ocr.value}")
        self._text_tabs.setTabText(1, f"Proofread Text  {correction.value}")
        self._text_tabs.setTabText(3, f"Content Review  {evaluation.value}")

    def _on_run_stage(self, stage: str) -> None:
        if self._current_source_id:
            self.run_stage_requested.emit(self._current_source_id, stage)

    def load(
        self,
        pixmap: QPixmap,
        raw_text: str,
        correction_text: str,
        evaluation_text: str,
    ) -> None:
        """Show the selected result's image and text payloads."""
        if not pixmap.isNull():
            self._image_view.set_pixmap(pixmap)
        else:
            self._image_view.show_message("Preprocessed image not found.")

        self._loading = True
        if self._ocr_text.toPlainText() != (raw_text or ""):
            self._ocr_text.setPlainText(raw_text or "")
        if self._correction_text.toPlainText() != (correction_text or ""):
            self._correction_text.setPlainText(correction_text or "")
        if evaluation_text:
            try:
                self._evaluation_text.setMarkdown(evaluation_text)
            except Exception:  # noqa: BLE001  # malformed markdown can raise in Qt; fall back to plain text
                self._evaluation_text.setPlainText(evaluation_text)
        else:
            self._evaluation_text.setPlainText("")
        self._loading = False

        self._last_raw_text = self._ocr_text.toPlainText()
        self._last_correction_text = self._correction_text.toPlainText()
        self._diff_widget.set_texts(self._last_raw_text, self._last_correction_text)

    def _on_ocr_text_changed(self) -> None:
        if self._loading:
            return
        text = self._ocr_text.toPlainText()
        if text == self._last_raw_text:
            return
        self._last_raw_text = text
        self._diff_widget.set_texts(self._last_raw_text, self._last_correction_text)
        self.ocr_text_edited.emit(text)

    def _on_correction_text_changed(self) -> None:
        if self._loading:
            return
        text = self._correction_text.toPlainText()
        if text == self._last_correction_text:
            return
        self._last_correction_text = text
        self._diff_widget.set_texts(self._last_raw_text, self._last_correction_text)
        self.correction_text_edited.emit(text)

    def clear_preview(self) -> None:
        self._image_view.clear()
        self._loading = True
        self._ocr_text.clear()
        self._correction_text.clear()
        self._evaluation_text.clear()
        self._loading = False
        self._last_raw_text = ""
        self._last_correction_text = ""
        self._diff_widget.clear_diff()
