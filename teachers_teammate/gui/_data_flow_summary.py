"""Plain-language "where is my data processed?" widgets.

Presentation only: the places come from the application service
(:meth:`~teachers_teammate.application.service.ProcessingApplicationService.describe_data_flow`),
so the colours and wording here never decide anything.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..domain.data_location import (
    Advice,
    DataFlow,
    DataFlowStage,
    DataLocation,
    DataRegion,
    PrivacyLevel,
    StageLocation,
)
from . import _user_text as txt

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


#: Where a step runs, in the few words the panel has room for.  The service list on the AI
#: models page uses the same words, so the two never describe one place differently.
PLACE_THIS_COMPUTER = "this computer"
PLACE_OWN_SERVER = "own server"
PLACE_ONLINE = "online"

_GREY = "#888"


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
        f"Names in the text are sent as written. Turn on “{txt.REMOVE_PERSONAL_INFO}” "
        "to hide them first, or choose a model that runs on this computer."
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


def short_place(location: StageLocation) -> str:
    """Return where a step runs in a few words, e.g. ``Mistral (EU) · EU``.

    The host of another computer and the vendor's country go into the tooltip
    (:attr:`StageLocation.details`): the panel is narrow.
    """
    if location.location is DataLocation.THIS_COMPUTER:
        return PLACE_THIS_COMPUTER
    if location.location is DataLocation.OTHER_COMPUTER:
        return PLACE_OWN_SERVER
    return f"{location.service} · {location.data_region.label}"


def _marked(stage: DataFlowStage, text: str, *, enabled: bool) -> str:
    """Return *text* (already escaped) behind the step's dot; grey all over while unticked."""
    if not enabled:
        return f'<span style="color:{_GREY};">● {text}</span>'
    return f"{_dot(stage.privacy_level)} {text}"


def uses_line(stage: DataFlowStage, model: str, *, enabled: bool = True) -> str:
    """Return the "uses" line under a step as rich text: ``● <model> · <place>``.

    Without a model (the built-in reader has none) the engine's name stands in.  The dot has the colour
    of the step's privacy level while the step is on; an unticked step is grey all over, but
    still names what ticking it would use.
    """
    name = model or stage.location.service
    return _marked(
        stage, f"{escape(name)} · {escape(short_place(stage.location))}", enabled=enabled
    )


def same_model_line(stage: DataFlowStage, *, enabled: bool = True) -> str:
    """Return the "uses" line of a step that runs the same model as the step before it."""
    return _marked(stage, escape(txt.SAME_MODEL_AS_PROOFREADING), enabled=enabled)


def detail_line(stage: DataFlowStage) -> str:
    """Return the "uses" line of a step that has no place of its own (personal-info removal)."""
    return f'<span style="color:{_GREY};">{escape(stage.detail)}</span>'


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


def uses_tooltip(stage: DataFlowStage) -> str:
    """Tooltip of a "uses" line: where its text goes, whether names are hidden, the details."""
    return f"{stage_tooltip(stage)}\n{stage.location.details}"


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


class UsesLine(QWidget):
    """The grey line under a step in the main window: what it uses, and a "change…" link.

    The text comes from :func:`uses_line`; the link (when the step has a place in Settings)
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
