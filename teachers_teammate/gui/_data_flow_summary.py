"""Plain-language "where is my data processed?" widgets.

Presentation only: the places come from the application service
(:meth:`~teachers_teammate.application.service.ProcessingApplicationService.describe_data_flow`),
so the colours and wording here never decide anything.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..domain.data_location import (
    Advice,
    DataFlow,
    DataFlowStage,
    DataLocation,
    DataRegion,
    PrivacyLevel,
    StageLocation,
)

#: One colour per level: green keeps everything here, yellow sends anonymized text into the
#: EU, orange sends it out of it, red sends names.
_LEVEL_COLORS = {
    PrivacyLevel.LOCAL: "#27ae60",
    PrivacyLevel.OWN_SERVER: "#e67e22",
    PrivacyLevel.ANON_EU: "#c9a227",
    PrivacyLevel.ANON_NON_EU: "#e67e22",
    PrivacyLevel.NAMES_EU: "#c0392b",
    PrivacyLevel.NAMES_NON_EU: "#c0392b",
}

#: What each level means, one sentence each; the box's last line.
_LEVEL_TEXT = {
    PrivacyLevel.LOCAL: "Everything stays on this computer.",
    PrivacyLevel.OWN_SERVER: "Data goes to another computer you control.",
    PrivacyLevel.ANON_EU: "Anonymized text is sent to a service in the EU.",
    PrivacyLevel.ANON_NON_EU: "Anonymized text is sent to a service outside the EU.",
    PrivacyLevel.NAMES_EU: "Text with names is sent to a service in the EU.",
    PrivacyLevel.NAMES_NON_EU: "Text with names is sent to a service outside the EU.",
}


def level_color(level: PrivacyLevel) -> str:
    """Return the colour that stands for *level*."""
    return _LEVEL_COLORS[level]


def level_text(level: PrivacyLevel) -> str:
    """Return the sentence that tells the teacher what *level* means."""
    return _LEVEL_TEXT[level]


def _location_level(location: StageLocation) -> PrivacyLevel:
    """Worst level *location* can stand for, for hints that know nothing about hiding names."""
    if location.location is DataLocation.THIS_COMPUTER:
        return PrivacyLevel.LOCAL
    if location.location is DataLocation.OTHER_COMPUTER:
        return PrivacyLevel.OWN_SERVER
    return PrivacyLevel.NAMES_NON_EU


def _dot(level: PrivacyLevel) -> str:
    return f'<span style="color:{level_color(level)};">●</span>'


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
    Advice.SERVICE_OUTSIDE_EU: (
        "The service processes the data outside the European Union. Choose a service in "
        "the EU (for example “Mistral (EU)”) if your school requires it."
    ),
}


def advice_text(advice: Advice) -> str:
    """Return the sentence that tells the teacher what *advice* means for them."""
    return _ADVICE_TEXT[advice]


def stage_row(stage: DataFlowStage) -> str:
    """Return one row of the status box as rich text, e.g. ``● Proofreading — Online …``."""
    return f"{_dot(stage.privacy_level)} <b>{escape(stage.title)}</b> — {escape(stage.badge)}"


def stage_tooltip(stage: DataFlowStage) -> str:
    """Short tooltip for a stage cell: where its text goes and how names are handled."""
    location = stage.location
    if location.location is DataLocation.THIS_COMPUTER:
        return f"Stays on this computer ({location.service})"
    names = "names hidden" if stage.personal_info_hidden else "names as written"
    if location.location is DataLocation.OTHER_COMPUTER:
        place = f"another computer ({location.host})" if location.host else "another computer"
        return f"Sent to {place}, {names}"
    if location.data_region is DataRegion.EU:
        return f"Sent to {location.service}, {names}"
    return f"Sent to {location.service}, outside the EU, {names}"


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
        text = f"{_dot(_location_level(location))} <b>{escape(location.label)}</b>"
        if note:
            text += f" — {escape(note)}"
        self.setText(text)
        self.setToolTip(details or location.details)


class DataFlowSummary(QFrame):
    """Compact box listing where every enabled step of the current settings runs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setToolTip(
            "Shows where your documents are read and proofread. A green dot means the step "
            "runs on this computer; yellow, orange or red means data is sent elsewhere."
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)
        self._title = QLabel("WHERE YOUR DOCUMENTS ARE PROCESSED")
        self._title.setStyleSheet("QLabel { font-size: 9pt; font-weight: bold; color: #aaa; }")
        # One label per row: the badge stays short and the tooltip holds the details.
        self._rows = QVBoxLayout()
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(2)
        self._row_labels: list[QLabel] = []
        self._body = QLabel()
        self._body.setTextFormat(Qt.TextFormat.RichText)
        self._body.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addLayout(self._rows)
        layout.addWidget(self._body)

    def row_texts(self) -> list[str]:
        """Return the rich text of every step row, top to bottom."""
        return [label.text() for label in self._row_labels]

    def set_flow(self, flow: DataFlow) -> None:
        """Re-render for *flow*."""
        for label in self._row_labels:
            label.setParent(None)
            label.deleteLater()
        self._row_labels.clear()
        for stage in flow.stages:
            label = QLabel(stage_row(stage))
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setToolTip(stage.location.details)
            self._rows.addWidget(label)
            self._row_labels.append(label)
        level = flow.level
        rows = [
            f'<span style="color:{level_color(level)};">'
            f"<b>{'●' if level is PrivacyLevel.LOCAL else '▲'} {escape(level_text(level))}"
            "</b></span>"
        ]
        rows.extend(
            f'<span style="color:#888;">{escape(advice_text(tip))}</span>' for tip in flow.advice
        )
        self._body.setText("<br>".join(rows))
        self._body.updateGeometry()  # the advice lines come and go: re-measure the height
        self.updateGeometry()
