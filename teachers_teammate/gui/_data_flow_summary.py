"""Plain-language "where is my data processed?" widgets.

Presentation only: the sentences come from :mod:`teachers_teammate.texts.privacy` and the
places from the application service
(:meth:`~teachers_teammate.application.data_flow.DataFlowService.describe_data_flow`), so
the colours and the dots here never decide anything.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..domain.data_location import DataFlow, PrivacyLevel, StageLocation
from ..texts import privacy


class LocationHint(QLabel):
    """One line under an engine / AI-service picker: where the chosen option runs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setStyleSheet("font-size: 11px;")

    def set_location(self, location: StageLocation, note: str = "", details: str = "") -> None:
        """Show *location* with a short *note* (one line); *details* go into the tooltip."""
        text = f"{privacy.level_dot(privacy.location_level(location))} "
        text += f"<b>{escape(location.label)}</b>"
        if note:
            text += f" — {escape(note)}"
        self.setText(text)
        self.setToolTip(details or location.details)


class UsesLine(QWidget):
    """The grey line under a step in the main window: what it uses, and a "change…" link.

    The text comes from :func:`~teachers_teammate.texts.privacy.uses_line`; the link (when the step has a place in Settings)
    emits :attr:`change_requested`.
    """

    #: "change…" was clicked.
    change_requested = Signal()

    def __init__(self, *, with_link: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 0, 0, 0)  # under the text of the checkbox above
        layout.setSpacing(6)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        self.label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.label, stretch=1)
        self.change_link = QLabel('<a href="change">change…</a>')
        self.change_link.setStyleSheet("font-size: 11px;")
        self.change_link.linkActivated.connect(lambda _link: self.change_requested.emit())
        self.change_link.setVisible(with_link)
        layout.addWidget(self.change_link)

    def set_line(self, text: str, tooltip: str = "") -> None:
        """Show *text* (rich text) with *tooltip*; an empty text hides the whole line."""
        self.label.setText(text)
        self.label.setToolTip(tooltip)
        self.setVisible(bool(text))

    def text(self) -> str:
        """Return the rich text shown."""
        return self.label.text()


class DataFlowSummary(QFrame):
    """Compact box with the verdict on where the documents go, and what to do about it.

    The step-by-step lines ("uses: …") sit under the steps in the main window; the box keeps
    the headline and the advice.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setToolTip(
            "Where your documents go with these settings. The dot next to each step under "
            "\u201cWhat should happen\u201d says where that step runs."
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)
        self._title = QLabel("WHERE YOUR DOCUMENTS ARE PROCESSED")
        self._title.setStyleSheet("QLabel { font-size: 9pt; font-weight: bold; color: #aaa; }")
        self._body = QLabel()
        self._body.setTextFormat(Qt.TextFormat.RichText)
        self._body.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addWidget(self._body)

    def set_flow(self, flow: DataFlow) -> None:
        """Re-render for *flow*."""
        level = flow.level
        rows = [
            f'<span style="color:{privacy.level_color(level)};">'
            f"<b>{'●' if level is PrivacyLevel.LOCAL else '▲'} {escape(privacy.level_text(level))}"
            "</b></span>"
        ]
        rows.extend(
            f'<span style="color:#888;">{escape(privacy.advice_text(tip))}</span>'
            for tip in flow.advice
        )
        self._body.setText("<br>".join(rows))
        self._body.updateGeometry()  # the advice lines come and go: re-measure the height
        self.updateGeometry()
