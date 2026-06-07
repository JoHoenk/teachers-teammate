"""Side-by-side comparison view: image · text A · text B · diff + similarity."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...domain.benchmark import PairComparison
from ...infrastructure.benchmark.run_store import StoredRun
from .._image_utils import load_pixmap
from .._preview_panel import DiffWidget, ZoomableImageView


def _text_pane(title: str) -> tuple[QWidget, QPlainTextEdit]:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(title)
    label.setStyleSheet("font-weight: bold;")
    editor = QPlainTextEdit()
    editor.setReadOnly(True)
    layout.addWidget(label)
    layout.addWidget(editor)
    return box, editor


class CompareView(QWidget):
    """Renders the document image and two runs' text plus their diff."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)

        self._similarity = QLabel("Select run A and run B to compare.")
        self._similarity.setStyleSheet("font-size: 11pt; padding: 2px;")
        outer.addWidget(self._similarity)

        splitter = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(splitter, stretch=1)

        self._image = ZoomableImageView()
        splitter.addWidget(self._image)

        texts = QSplitter(Qt.Orientation.Horizontal)
        box_a, self._text_a = _text_pane("Run A")
        box_b, self._text_b = _text_pane("Run B")
        texts.addWidget(box_a)
        texts.addWidget(box_b)
        splitter.addWidget(texts)

        diff_box = QWidget()
        diff_layout = QVBoxLayout(diff_box)
        diff_layout.setContentsMargins(0, 0, 0, 0)
        diff_label = QLabel("Diff (A → B)")
        diff_label.setStyleSheet("font-weight: bold;")
        self._diff = DiffWidget()
        diff_layout.addWidget(diff_label)
        diff_layout.addWidget(self._diff)
        splitter.addWidget(diff_box)
        splitter.setSizes([300, 250, 250])

    def clear(self) -> None:
        """Reset all panes."""
        self._image.clear()
        self._text_a.clear()
        self._text_b.clear()
        self._diff.clear_diff()
        self._similarity.setText("Select run A and run B to compare.")

    def show_comparison(
        self, run_a: StoredRun, run_b: StoredRun, comparison: PairComparison
    ) -> None:
        """Display two runs side-by-side with their diff and similarity score."""
        self._text_a.setPlainText(run_a.raw_text)
        self._text_b.setPlainText(run_b.raw_text)
        self._diff.set_texts(run_a.raw_text, run_b.raw_text)
        self._set_image(run_a.preview_img or run_b.preview_img)
        pct = comparison.similarity * 100.0
        self._similarity.setText(
            f"Similarity: {pct:.1f}%   ·   "
            f"A: {comparison.stats_a.words} words / {comparison.stats_a.chars} chars   ·   "
            f"B: {comparison.stats_b.words} words / {comparison.stats_b.chars} chars"
        )

    def _set_image(self, preview_img: str) -> None:
        if preview_img and Path(preview_img).is_file():
            pix = load_pixmap(preview_img)
            if not pix.isNull():
                self._image.set_pixmap(pix)
                return
        self._image.show_message("No preview image for this run.")
