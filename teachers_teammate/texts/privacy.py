"""What the app says about the data that leaves the computer.

The "what is sent for this file" dialog, the data-flow summary and the privacy page all
read their wording from here, so one sentence about a step reads the same everywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from ..domain.data_location import (
    Advice,
    DataFlow,
    DataFlowStage,
    DataLocation,
    DataRegion,
    PrivacyLevel,
    StageLocation,
)
from ..domain.language import language_name
from ..domain.model_catalog import short_model_name
from ._types import Notice, NoticeKind
from .review import NAMES_IN_LANGUAGES, REMOVE_PERSONAL_INFO
from .settings import WHERE_ADDONS

#: The grey of everything that is not a warning: a step that is off, a detail line.
_GREY = "#888"

WHAT_IS_SENT_TITLE = "What is sent for this file"
WHAT_IS_SENT_WORKING = "Working out what is sent…"
NO_TEXT_YET = "No text recognized for this file yet."
RESULTS_UNREADABLE = "Could not read the stored results of this file."


def what_is_sent_title(name: str) -> str:
    """Return the caption of the busy dialog that describes one file."""
    return f"{WHAT_IS_SENT_TITLE} — {name}"


def what_is_sent_failed(reason: str) -> Notice:
    """Tell the teacher that the sent text could not be worked out."""
    return Notice(WHAT_IS_SENT_TITLE, reason, NoticeKind.WARNING)


def what_is_sent_without_text() -> Notice:
    """Tell the teacher that this file has no text to describe yet."""
    return Notice(WHAT_IS_SENT_TITLE, NO_TEXT_YET)


def what_is_sent_unreadable() -> Notice:
    """Tell the teacher that the stored results of this file could not be read."""
    return Notice(WHAT_IS_SENT_TITLE, RESULTS_UNREADABLE, NoticeKind.WARNING)


def spacy_missing(detail: str, *, installed_app: bool) -> str:
    """Explain that name detection needs spaCy, which is not installed.

    The installed app points at the Add-ons page; a source checkout at ``pip``.
    """
    if installed_app:
        return f"spaCy is not installed.\n\nInstall it under {WHERE_ADDONS} (Name detection)."
    return f'spaCy not installed: {detail}\n\nInstall: pip install "teachers-teammate[privacy]"'


def spacy_model_missing(detail: str) -> str:
    """Explain that the name detection model is not installed."""
    return f"spaCy model missing: {detail}\n\nRun: python -m spacy download <model>"


def unencrypted_note(host: str) -> str:
    """Return the note for data sent to another computer over plain ``http://``.

    A note, not a refusal: a school server often has no certificate, so the teacher decides.
    What it says is that the connection is not encrypted.
    """
    return f"unencrypted (http://) — anyone on the network can read what goes to {host}"


# ── Where the documents go: the levels, the places and the advice ─────────────
#
# The main window's summary box, the "uses" lines under its steps and the dialogs that name a
# place read their sentences here; the widgets only put a dot and a colour around them.

#: What each level means, one sentence each; the box's last line.
LEVEL_TEXT = {
    PrivacyLevel.LOCAL: "Everything stays on this computer.",
    PrivacyLevel.OWN_SERVER: "Data goes to another computer you control.",
    PrivacyLevel.ANON_EU: "Anonymized text is sent to a service in the EU.",
    PrivacyLevel.ANON_NON_EU: "Anonymized text is sent to a service outside the EU.",
    PrivacyLevel.NAMES_EU: "Text with names is sent to a service in the EU.",
    PrivacyLevel.NAMES_NON_EU: "Text with names is sent to a service outside the EU.",
}

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

#: Where a step runs, in the few words the panel has room for.  The service list on the AI
#: models page uses the same words, so the two never describe one place differently.
PLACE_THIS_COMPUTER = "this computer"
PLACE_OWN_SERVER = "own server"
PLACE_ONLINE = "online"

#: The dot and the words of a step that runs the same model as the one before it.
SAME_MODEL_AS_PROOFREADING = "Same model as proofreading"

ADVICE_TEXT = {
    Advice.PAGES_TO_OTHER_COMPUTER: (
        "Page images are sent to another computer. Only use one you trust and control."
    ),
    Advice.NAMES_SENT_AS_WRITTEN: (
        f"Names in the text are sent as written. Turn on “{REMOVE_PERSONAL_INFO}” "
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


def level_text(level: PrivacyLevel) -> str:
    """Return the sentence that tells the teacher what *level* means."""
    return LEVEL_TEXT[level]


def advice_text(advice: Advice) -> str:
    """Return the sentence that tells the teacher what *advice* means for them."""
    return ADVICE_TEXT[advice]


def level_color(level: PrivacyLevel) -> str:
    """Return the colour that stands for *level*."""
    return _LEVEL_COLORS[level]


def level_dot(level: PrivacyLevel) -> str:
    """Return the coloured dot that stands for *level*, as rich text."""
    return f'<span style="color:{level_color(level)};">●</span>'


def location_level(location: StageLocation) -> PrivacyLevel:
    """Return the worst level *location* can stand for, for hints that know nothing of names."""
    if location.location is DataLocation.THIS_COMPUTER:
        return PrivacyLevel.LOCAL
    if location.location is DataLocation.OTHER_COMPUTER:
        return PrivacyLevel.OWN_SERVER
    return PrivacyLevel.NAMES_NON_EU


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


def and_list(items: Sequence[str]) -> str:
    """Return ``"German and English"``, ``"German, English and French"`` or ``""``."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _marked(stage: DataFlowStage, text: str, *, enabled: bool) -> str:
    """Return *text* (already escaped) behind the step's dot; grey all over while unticked."""
    if not enabled:
        return f'<span style="color:{_GREY};">● {text}</span>'
    return f"{level_dot(stage.privacy_level)} {text}"


def uses_line(stage: DataFlowStage, model: str, *, enabled: bool = True) -> str:
    """Return the "uses" line under a step as rich text: ``● <model> · <place>``.

    Without a model (the built-in reader has none) the engine's name stands in.  The model is
    the short name (:func:`short_model_name`); the full tag belongs in the tooltip.  The place
    is shown only when it is not this computer, because that is the case the line warns about.
    The dot has the colour of the step's privacy level while the step is on; an unticked step
    is grey all over, but still names what ticking it would use.
    """
    name = short_model_name(model) if model else stage.location.service
    text = escape(name)
    if stage.location.location is not DataLocation.THIS_COMPUTER:
        text = f"{text} · {escape(short_place(stage.location))}"
    return _marked(stage, text, enabled=enabled)


def same_model_line(stage: DataFlowStage, *, enabled: bool = True) -> str:
    """Return the "uses" line of a step that runs the same model as the step before it."""
    return _marked(stage, escape(SAME_MODEL_AS_PROOFREADING), enabled=enabled)


def detail_line(stage: DataFlowStage) -> str:
    """Return the "uses" line of a step that has no place of its own (personal-info removal)."""
    return f'<span style="color:{_GREY};">{escape(stage.detail)}</span>'


def names_line(stage: DataFlowStage) -> str:
    """Return the line under personal-info removal: the languages whose names are hidden.

    Empty while the step is off — the checkbox already says so, and "Off" under it only
    repeated that.  The languages come from the models that will be loaded.
    """
    if not stage.personal_info_hidden:
        return ""
    names = [language_name(code) for code in stage.languages]
    text = NAMES_IN_LANGUAGES.format(languages=and_list(names)) if names else stage.detail
    return f'<span style="color:{_GREY};">{escape(text)}</span>'


def stage_tooltip(stage: DataFlowStage) -> str:
    """Return the tooltip of a stage cell: where its text goes and how names are handled."""
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


def stage_tooltips(flow: DataFlow) -> dict[str, str]:
    """Return the tooltip of each stage cell that sends text away from this computer."""
    return {
        stage.stage: stage_tooltip(stage)
        for stage in flow.stages
        if not stage.detail and stage.location.location is not DataLocation.THIS_COMPUTER
    }


def uses_tooltip(stage: DataFlowStage) -> str:
    """Return the tooltip of a "uses" line: where its text goes, names, the details."""
    return f"{stage_tooltip(stage)}\n{stage.location.details}"


def text_note(location: StageLocation) -> str:
    """Return the short consequence of *location* for a step that works on text."""
    if location.location is DataLocation.THIS_COMPUTER:
        return "text never leaves it"
    if location.location is DataLocation.OTHER_COMPUTER:
        return "text is sent there"
    return "text is sent over the internet"


def plain_http_note(location: StageLocation, url: str) -> str:
    """Return the note for a step on an Ollama server of the teacher's choosing over ``http://``.

    That is another computer, or a server on the internet; both have a host.  Empty for this
    computer and for a vendor's service (no host): a vendor's address is not the teacher's to
    change, and the badge already says that the data leaves.  A note, not a refusal — a school
    server often has no certificate.
    """
    if location.location is DataLocation.THIS_COMPUTER or not location.host:
        return ""
    if url.strip().lower().startswith("https://"):
        return ""
    return unencrypted_note(location.host)
