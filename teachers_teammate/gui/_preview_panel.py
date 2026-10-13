"""Zoomable image view, inline word-level diff widget, and preview panel."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import difflib
import html
from pathlib import Path
import re
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..application.formatting import TextFormatter
from ._formatted_text import (
    FormattedTextEdit,
    document_from_qt,
    install_formatting_shortcuts,
    render_html,
)
from ._image_utils import load_pixmap
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


class DiffWidget(QTextEdit):
    """Inline word-level diff: deleted in red/strikethrough, added in green."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        font = QFont()
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSize(9)
        self.setFont(font)
        self._last_key: tuple[str, str, int] | None = None

    def set_texts(self, original: str, corrected: str) -> None:
        key = (original, corrected, self.font().pointSize())
        if key == self._last_key:
            return  # same comparison at the same size: keep the rendered diff (and scroll)
        self._last_key = key
        if not original:
            self.setPlainText("(no OCR text available for comparison)")
            return
        if not corrected:
            self.setPlainText("(no correction available - skipped or not yet generated)")
            return

        # Get current font size from widget
        current_font = self.font()
        font_size_pt = current_font.pointSize()

        orig_tokens = self._tokenize(original)
        corr_tokens = self._tokenize(corrected)
        matcher = difflib.SequenceMatcher(None, orig_tokens, corr_tokens, autojunk=False)

        parts = [
            "<html><body>"
            f"<pre style='white-space: pre-wrap; font-family: monospace; font-size: {font_size_pt}pt;'>"
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
        self._last_key = None
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


# Pause after the last keystroke in a formatted view before the edit is converted back to
# Markdown, stored and re-diffed (each of those passes covers the whole document).
_EDIT_COMMIT_DELAY_MS = 400


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
        self._source_btn: QPushButton  # assigned in _build_text_zoom_bar
        self._formatter = TextFormatter()
        # What each formatted view currently shows, as ``(source text, format)``: typing in one
        # view must not re-render the other, and a view that is not shown is not rendered.
        self._rendered: dict[QTextEdit, tuple[str, str]] = {}
        # Markup-free text per slot ("raw" / "correction"), keyed by the text and format.
        self._plain_cache: dict[str, tuple[str, str, str]] = {}

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_image_panel())
        splitter.addWidget(self._build_text_panel())
        splitter.setSizes([300, 600])
        layout.addWidget(splitter)

        self._loading = False
        self._last_raw_text = ""
        self._last_correction_text = ""
        self._text_format = "plain"  # notation of the OCR text and of its correction
        self._rendering = False  # True while a formatted view is being filled from source
        self._editing_view: QTextEdit | None = None  # formatted view the user is typing in
        # Formatted view with typed text not yet converted to source, and the timer that
        # commits it once typing pauses (or earlier: focus loss, mode switch, load).
        self._pending_view: QTextEdit | None = None
        self._edit_timer = QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(_EDIT_COMMIT_DELAY_MS)
        self._edit_timer.timeout.connect(self.flush_pending_edit)
        self._current_source_id: str = ""
        self._processed_pixmap: QPixmap | None = None
        self._original_path: str = ""
        self._original_pixmap: QPixmap | None = None  # loaded lazily on first toggle
        self._preprocess_steps: Sequence[str] | None = None
        self._ocr_text.textChanged.connect(self._on_ocr_text_changed)
        self._correction_text.textChanged.connect(self._on_correction_text_changed)
        self._ocr_view.textChanged.connect(lambda: self._on_view_edited(self._ocr_view))
        self._correction_view.textChanged.connect(
            lambda: self._on_view_edited(self._correction_view)
        )
        self._ocr_view.focus_lost.connect(self.flush_pending_edit)
        self._correction_view.focus_lost.connect(self.flush_pending_edit)

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
        self._original_btn = QToolButton()
        self._original_btn.setText("Original")
        self._original_btn.setCheckable(True)
        self._original_btn.setToolTip("Show the page as scanned, before preprocessing")
        self._original_btn.toggled.connect(self._on_original_toggled)
        self._original_btn.setVisible(False)
        tb_layout.addWidget(self._original_btn)
        tb_layout.addStretch()
        _hint_lbl = QLabel("Scroll: zoom · Drag: pan")
        _hint_lbl.setStyleSheet("color: #888; font-size: 8pt;")
        tb_layout.addWidget(_hint_lbl)

        self._prep_lbl = QLabel()
        self._prep_lbl.setStyleSheet("color: #888; font-size: 8pt; padding: 0 6px 2px 6px;")
        self._prep_lbl.setWordWrap(True)
        self._prep_lbl.setVisible(False)

        vbox.addWidget(toolbar)
        vbox.addWidget(self._prep_lbl)
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

        # ``_ocr_text`` / ``_correction_text`` hold the stored text (plain or Markdown source)
        # and are the editable source of truth.  For Markdown text the ``*_view`` widgets show
        # it formatted; a stack per stage switches between the two.
        self._ocr_text = QTextEdit()
        self._ocr_text.setReadOnly(False)
        self._ocr_text.setFont(font)
        self._ocr_view = self._make_formatted_view(font)
        self._ocr_stack = self._make_stack(self._ocr_view, self._ocr_text)

        self._correction_text = QTextEdit()
        self._correction_text.setReadOnly(False)
        self._correction_text.setFont(font)
        self._correction_view = self._make_formatted_view(font)
        self._correction_stack = self._make_stack(self._correction_view, self._correction_text)

        self._evaluation_text = QTextEdit()
        self._evaluation_text.setReadOnly(True)
        self._evaluation_text.setFont(font)

        self._diff_widget = DiffWidget()

        self._ocr_nav = _StageNavBar("proofreading")
        self._ocr_nav.run_requested.connect(lambda: self._on_run_stage("correction"))
        self._correction_nav = _StageNavBar("content review")
        self._correction_nav.run_requested.connect(lambda: self._on_run_stage("evaluation"))

        self._text_tabs.addTab(self._make_nav_tab(self._ocr_stack, self._ocr_nav), "Extracted Text")
        self._text_tabs.addTab(
            self._make_nav_tab(self._correction_stack, self._correction_nav), "Proofread Text"
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
        for _te in (
            self._ocr_text,
            self._correction_text,
            self._ocr_view,
            self._correction_view,
            self._evaluation_text,
        ):
            _te.installEventFilter(scroll_filter)

        return container

    def _build_text_zoom_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        self._source_btn = QPushButton("Source")
        self._source_btn.setCheckable(True)
        self._source_btn.setToolTip(
            "Show the stored text with its formatting marks (Markdown) instead of the "
            "formatted view. Edits made there are saved as written."
        )
        self._source_btn.toggled.connect(self._apply_view_modes)
        self._source_btn.setVisible(False)
        layout.addWidget(self._source_btn)
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
        for widget in (
            self._ocr_text,
            self._correction_text,
            self._ocr_view,
            self._correction_view,
            self._evaluation_text,
            self._diff_widget,
        ):
            widget.setFont(font)
        self._font_size_lbl.setText(f"{new_pt} pt")

    # ── formatted / source views ──────────────────────────────────────────────────

    @staticmethod
    def _make_formatted_view(font: QFont) -> FormattedTextEdit:
        """Rich-text editor for Markdown text; edits are converted back to Markdown."""
        view = FormattedTextEdit()
        view.setReadOnly(False)
        view.setFont(font)
        view.setToolTip(
            "Ctrl+B bold · Ctrl+I italic · Ctrl+U underline · "
            "Ctrl+Shift+X crossed out · Ctrl+Shift+= inserted above the line"
        )
        install_formatting_shortcuts(view)
        return view

    @staticmethod
    def _make_stack(formatted: QWidget, source: QWidget) -> QStackedWidget:
        stack = QStackedWidget()
        stack.addWidget(formatted)  # index 0
        stack.addWidget(source)  # index 1
        stack.setCurrentIndex(1)
        return stack

    def _refresh_formatted_views(self) -> None:
        """Re-render the formatted views whose stored (source) text changed and are shown."""
        text_format = self._text_format
        for view, source, stack in (
            (self._ocr_view, self._ocr_text, self._ocr_stack),
            (self._correction_view, self._correction_text, self._correction_stack),
        ):
            if view is self._editing_view or view is self._pending_view:
                continue  # the user is typing here; the source is derived from it
            if text_format != "markdown":
                if self._rendered.pop(view, None) is not None:
                    self._set_view_html(view, None)
                continue
            if stack.currentIndex() != 0:
                continue  # showing the source; rendered when the toggle switches back
            text = source.toPlainText()
            if self._rendered.get(view) == (text, text_format):
                continue
            self._rendered[view] = (text, text_format)
            self._set_view_html(view, render_html(self._formatter.document(text, text_format)))

    def _set_view_html(self, view: QTextEdit, html_text: str | None) -> None:
        """Fill *view* (or clear it for ``None``) without treating it as a user edit."""
        self._rendering = True
        try:
            if html_text is None:
                view.clear()
            else:
                view.setHtml(html_text)
        finally:
            self._rendering = False

    def _apply_view_modes(self) -> None:
        """Show the formatted view for Markdown text unless "Source" is toggled on."""
        self.flush_pending_edit()  # the source must hold the typed text before it is shown
        is_markdown = self._text_format == "markdown"
        stack_index = 1 if self._source_btn.isChecked() or not is_markdown else 0
        self._ocr_stack.setCurrentIndex(stack_index)
        self._correction_stack.setCurrentIndex(stack_index)
        self._source_btn.setVisible(is_markdown)
        self._refresh_formatted_views()

    def _on_view_edited(self, view: QTextEdit) -> None:
        """Note a keystroke in a formatted view; the conversion waits for a typing pause."""
        if self._rendering or self._loading or self._editing_view is not None:
            return
        if self._pending_view is not None and self._pending_view is not view:
            self.flush_pending_edit()
        self._pending_view = view
        self._edit_timer.start()

    def flush_pending_edit(self) -> None:
        """Store typed-but-uncommitted text of a formatted view as source text right now."""
        self._edit_timer.stop()
        view, self._pending_view = self._pending_view, None
        if view is not None:
            self._commit_view_edit(view)

    def _commit_view_edit(self, view: QTextEdit) -> None:
        """Convert an edit in a formatted view back to Markdown and store it as source."""
        source = self._ocr_text if view is self._ocr_view else self._correction_text
        markdown = self._formatter.markdown(document_from_qt(view.document()))
        if markdown == source.toPlainText():
            return
        text_format = self._text_format
        self._editing_view = view
        try:
            source.setPlainText(markdown)  # fires the source handler, which emits the edit signal
        finally:
            self._editing_view = None
        self._rendered[view] = (markdown, text_format)  # the view already shows this text

    def _diff_texts(self) -> tuple[str, str]:
        """Return the stored OCR and correction text without markup, for diffing."""
        return (
            self._plain("raw", self._last_raw_text, self._text_format),
            self._plain("correction", self._last_correction_text, self._text_format),
        )

    def _plain(self, slot: str, text: str, text_format: str) -> str:
        """Return *text* without markup, re-parsing only when the slot's text changed."""
        cached = self._plain_cache.get(slot)
        if cached is not None and cached[:2] == (text, text_format):
            return cached[2]
        plain = self._formatter.plain_text(text, text_format)
        self._plain_cache[slot] = (text, text_format, plain)
        return plain

    # ── stage navigation ──────────────────────────────────────────────────────────

    @staticmethod
    def _make_nav_tab(text_widget: QWidget, nav_bar: _StageNavBar) -> QWidget:
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
        *,
        original_path: str | None = None,
        preprocess_steps: Sequence[str] | None = None,
        text_format: str = "plain",
    ) -> None:
        """Show the selected result's image and text payloads.

        *original_path* and *preprocess_steps* describe how the image was produced (see
        :meth:`set_preprocessing`); leaving them unset hides the original toggle and caption.
        *text_format* says whether the OCR and correction texts are ``"plain"`` or
        ``"markdown"``; Markdown is shown formatted (switch to the source with the toggle).
        """
        self.flush_pending_edit()  # typed text must not be lost to the replaced content
        self._processed_pixmap = pixmap
        self.set_preprocessing(original_path, preprocess_steps)
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
        self._text_format = self._formatter.normalize(text_format)
        self._apply_view_modes()  # also renders the formatted views that are shown
        self._diff_widget.set_texts(*self._diff_texts())

    def _on_ocr_text_changed(self) -> None:
        if self._loading:
            return
        text = self._ocr_text.toPlainText()
        if text == self._last_raw_text:
            return
        self._last_raw_text = text
        self._refresh_formatted_views()
        self._diff_widget.set_texts(*self._diff_texts())
        self.ocr_text_edited.emit(text)

    def _on_correction_text_changed(self) -> None:
        if self._loading:
            return
        text = self._correction_text.toPlainText()
        if text == self._last_correction_text:
            return
        self._last_correction_text = text
        self._refresh_formatted_views()
        self._diff_widget.set_texts(*self._diff_texts())
        self.correction_text_edited.emit(text)

    def set_preprocessing(self, original_path: str | None, steps: Sequence[str] | None) -> None:
        """Describe how the shown image was produced and enable the original toggle.

        *steps* lists the applied preprocessing steps (``None`` = unknown, hides the
        caption; empty = the image is unprocessed).  *original_path* is the page before
        preprocessing; when it points to an existing file an "Original" toggle lets the
        user compare it with the processed image.  The file is only loaded on first toggle.
        """
        self._original_path = original_path or ""
        self._original_pixmap = None
        self._preprocess_steps = steps
        has_processed = self._processed_pixmap is not None and not self._processed_pixmap.isNull()
        was_showing_original = self._original_btn.isChecked()
        self._original_btn.blockSignals(True)
        self._original_btn.setChecked(False)
        self._original_btn.blockSignals(False)
        self._original_btn.setVisible(
            bool(self._original_path) and Path(self._original_path).is_file() and has_processed
        )
        self._refresh_preprocess_caption()
        if was_showing_original and has_processed:
            assert self._processed_pixmap is not None
            self._image_view.swap_pixmap(self._processed_pixmap)

    def _on_original_toggled(self, checked: bool) -> None:
        if checked and self._original_pixmap is None:
            self._original_pixmap = load_pixmap(self._original_path)
        target = self._original_pixmap if checked else self._processed_pixmap
        if target is not None and not target.isNull():
            self._image_view.swap_pixmap(target)
        elif checked:
            # Unreadable original: undo the toggle so the caption matches the image shown.
            self._original_btn.blockSignals(True)
            self._original_btn.setChecked(False)
            self._original_btn.blockSignals(False)
        self._refresh_preprocess_caption()

    def _refresh_preprocess_caption(self) -> None:
        steps = self._preprocess_steps
        if steps is None:
            self._prep_lbl.setVisible(False)
            return
        if self._original_btn.isChecked():
            text = "Showing the original page (before preprocessing)"
        elif steps:
            text = f"Preprocessed: {', '.join(steps)}"
        else:
            text = "No preprocessing applied — showing the original image"
        self._prep_lbl.setText(text)
        self._prep_lbl.setVisible(True)

    def clear_preview(self) -> None:
        self.flush_pending_edit()
        self._image_view.clear()
        self._processed_pixmap = None
        self.set_preprocessing(None, None)
        self._loading = True
        self._ocr_text.clear()
        self._correction_text.clear()
        self._ocr_view.clear()
        self._correction_view.clear()
        self._evaluation_text.clear()
        self._loading = False
        self._last_raw_text = ""
        self._last_correction_text = ""
        self._text_format = "plain"
        self._apply_view_modes()
        self._diff_widget.clear_diff()
