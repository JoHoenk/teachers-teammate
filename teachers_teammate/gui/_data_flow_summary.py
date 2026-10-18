"""Plain-language "where is my data processed?" widgets.

Presentation only: the places come from the application service
(:meth:`~teachers_teammate.application.service.ProcessingApplicationService.describe_data_flow`),
so the colours and wording here never decide anything.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..domain.data_location import Advice, DataFlow, DataLocation, StageLocation

_COLORS = {
    DataLocation.THIS_COMPUTER: "#27ae60",
    DataLocation.OTHER_COMPUTER: "#e67e22",
    DataLocation.ONLINE: "#c0392b",
}


def _dot(location: DataLocation) -> str:
    return f'<span style="color:{_COLORS[location]};">●</span>'


_ADVICE_TEXT = {
    Advice.PAGES_TO_OTHER_COMPUTER: (
        "Page images are sent to another computer. Only use one you trust and control."
    ),
    Advice.NAMES_SENT_AS_WRITTEN: (
        "Names in the text are sent as written. Turn on “Remove personal info” "
        "(Step 3) to hide them first, or choose a model that runs on this computer."
    ),
    Advice.NAMES_MAY_BE_MISSED: (
        "Names are replaced automatically, so an unusual or misspelled name can be missed. "
        "Check that sending the text is allowed for your documents."
    ),
}


def advice_text(advice: Advice) -> str:
    """Return the sentence that tells the teacher what *advice* means for them."""
    return _ADVICE_TEXT[advice]


def text_note(location: StageLocation) -> str:
    """Short consequence of *location* for a step that works on text."""
    if location.location is DataLocation.THIS_COMPUTER:
        return "text never leaves it"
    if location.location is DataLocation.OTHER_COMPUTER:
        return "text is sent there"
    return "text is sent over the internet"


class LocationHint(QLabel):
    """One line under an engine / AI-service picker: where the chosen option runs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setStyleSheet("font-size: 11px;")

    def set_location(self, location: StageLocation, note: str = "", details: str = "") -> None:
        """Show *location* with a short *note* (one line); *details* go into the tooltip."""
        text = f"{_dot(location.location)} <b>{escape(location.label)}</b>"
        if note:
            text += f" — {escape(note)}"
        self.setText(text)
        self.setToolTip(details)


class DataFlowSummary(QFrame):
    """Compact box listing where every enabled step of the current settings runs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setToolTip(
            "Shows where your documents are read and proofread. A green dot means the step "
            "runs on this computer; orange or red means data is sent elsewhere."
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
        rows: list[str] = []
        for stage in flow.stages:
            rows.append(
                f"{_dot(stage.location.location)} <b>{escape(stage.title)}</b> — "
                f"{escape(stage.location.label)}"
            )
        if flow.leaves_computer:
            verdict = '<span style="color:#c0392b;"><b>Some data leaves this computer.</b></span>'
        else:
            verdict = (
                '<span style="color:#27ae60;"><b>Everything stays on this computer.</b></span>'
            )
        rows.append(verdict)
        rows.extend(
            f'<span style="color:#888;">{escape(advice_text(tip))}</span>' for tip in flow.advice
        )
        self._body.setText("<br>".join(rows))
        self._body.updateGeometry()  # the advice lines come and go: re-measure the height
        self.updateGeometry()
