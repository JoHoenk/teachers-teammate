"""The heading of a numbered step and the line between two steps.

The panel's four steps are read as one workflow, so their headings and separators look the same
everywhere; keeping them here means the look is set once and the panel only names the step.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


def step_header(text: str, tooltip: str) -> QWidget:
    """Return the heading of a step: its name, and an "i" that explains it."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 4, 0, 2)
    lbl = QLabel(text.upper())
    lbl.setStyleSheet("QLabel { font-size: 11pt; font-weight: bold; color: #aaa; }")
    info = QLabel("i")
    info.setStyleSheet(
        "QLabel { color: white; background: #2980b9; border-radius: 9px; "
        "padding: 1px 5px; font-size: 11pt; font-weight: normal; }"
    )
    info.setToolTip(tooltip)
    h.addWidget(lbl)
    h.addStretch()
    h.addWidget(info)
    return row


def separator() -> QWidget:
    """Return a thin line between two steps."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 6, 0, 6)
    layout.setSpacing(0)
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #444;")
    layout.addWidget(line)
    return container
