"""What the app says about the data that leaves the computer.

The "what is sent for this file" dialog, the data-flow summary and the privacy page all
read their wording from here, so one sentence about a step reads the same everywhere.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.anonymization import PatternProblem, PatternProblemKind
from ..domain.data_location import (
    Advice,
    DataFlow,
    DataFlowStage,
    DataLocation,
    DataRegion,
    FingerprintPart,
    PrivacyLevel,
    StageLocation,
    fingerprint_parts,
)
from ..domain.language import language_name
from ..domain.model_catalog import short_model_name
from ._types import Notice, NoticeKind
from .app import stage_title
from .review import NAMES_AS_WRITTEN, NAMES_IN_LANGUAGES, REMOVE_PERSONAL_INFO
from .settings import WHERE_ADDONS

WHAT_IS_SENT_TITLE = "What is sent for this file"
WHAT_IS_SENT_WORKING = "Working out what is sent…"
NO_TEXT_YET = "No text recognized for this file yet."
RESULTS_UNREADABLE = "Could not read the stored results of this file."
LOG_NOTE = (
    "What goes wrong is written to a log file on this computer (Help → Open Log Folder). "
    "It holds file names and error messages, never the text of a document."
)


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


# ── The anonymizer's models and patterns: the widget on the Privacy page ──────
#
# The teacher picks the spaCy passes and the regex patterns here; the widget only puts its
# labels around them.

ANONYMISATION_NOTE = (
    "<b>Note:</b> Anonymization is a detection aid, not a guarantee. "
    "NER models and regex patterns may miss PII — always review the output "
    "before uploading documents to a cloud provider. "
    "You remain responsible for compliance with data-protection regulations."
)

DETECTION_TAB = "Detection"
PATTERNS_TAB = "Regex Patterns"

MODEL_DETECTING = "Detecting…"
MODEL_AUTO_PRIMARY = "Auto (language-specific)"
MODEL_AUTO_SECONDARY = "Auto (system language)"
MODEL_NO_SECONDARY = "(none — no extra pass)"

PRIMARY_MODEL_LABEL = "<b>Primary detection model:</b>"
PRIMARY_MODEL_HINT = (
    "Primary spaCy NER model for person-name detection.  "
    "<i>Auto</i> picks the language-specific model automatically; "
    "choose a specific model to override (e.g. force the multilingual model)."
)
EXTRA_MODEL_LABEL = "<b>Extra detection model (optional):</b>"
EXTRA_MODEL_HINT = (
    "An extra spaCy model run after the primary one to catch names it misses "
    "(e.g. German names in English text). <i>Auto</i> uses the model for the "
    "language this computer is set to, and nothing when that is the same model as "
    "the primary one. Choose <i>(none)</i> to skip the second pass entirely."
)

PATTERNS_INTRO = "Patterns applied after NER (clear all rows to disable regex anonymization):"
PATTERNS_ADD_ROW = "Add row"
PATTERNS_REMOVE_SELECTED = "Remove selected"
PATTERNS_RESTORE_DEFAULTS = "Restore defaults"
PATTERNS_RESTORE_TOOLTIP = (
    "Reset patterns to the built-in defaults "
    "(IBAN, email, phone, dates, and German street and postcode formats)."
)

HIDE_LABEL = "Always hidden, wherever they stand:"
HIDE_INTRO = (
    "A text listed here is replaced by a placeholder wherever it stands, even when no model or "
    "pattern finds it; the Check tab adds one when you choose Hide this."
)
HIDE_PLACEHOLDER = "e.g. Anna"
KEEP_LABEL = "Never hidden, whatever is found:"
KEEP_INTRO = (
    "A text listed here is sent as written, even when a model or a pattern finds it, and wins "
    "over the list above; the Check tab adds one when you choose Don't hide this."
)
KEEP_PLACEHOLDER = "e.g. Anna Müller"
TEXT_LIST_ADD = "Add text"
TEXT_LIST_REMOVE_SELECTED = "Remove selected"

PREVIEW_INTRO = "<b>Preview</b> — paste sample OCR text to test all active patterns:"
PREVIEW_SAMPLE_LABEL = "Sample text:"
PREVIEW_SAMPLE_PLACEHOLDER = (
    "Paste text here, e.g.:\nAlice sent an invoice to bob@example.com (STU-123456)."
)
PREVIEW_OUTPUT_LABEL = "Anonymized output:"
PREVIEW_RUN = "Run preview"
PREVIEW_MAP_LABEL = "Replacement map:"
PATTERN_COLUMNS = ["Tag", "Regex pattern"]
MAP_COLUMNS = ["Placeholder", "Original value"]


def model_auto_primary(model: str) -> str:
    """Return the primary combo's auto entry, naming the model when it is known."""
    return f"{MODEL_AUTO_PRIMARY}: {model}" if model else MODEL_AUTO_PRIMARY


def model_auto_secondary(system: str | None, primary: str) -> str:
    """Return the secondary combo's auto entry: the system's model, or a note that says why not.

    An unknown system language and a model that would repeat the primary pass both mean that
    no extra pass happens.
    """
    if system and system == primary:
        return f"{MODEL_AUTO_SECONDARY}: none, same as document language"
    if system:
        return f"{MODEL_AUTO_SECONDARY}: {system}"
    return f"{MODEL_AUTO_SECONDARY}: none, the language is unknown"


def spacy_fetch_failed(reason: str) -> str:
    """Explain why the installed spaCy models could not be listed."""
    return f"Could not detect installed spaCy models: {reason}"


def spacy_model_not_downloaded(model: str) -> str:
    """Explain that this spaCy model is missing, with the command that fetches it."""
    return f"spaCy model '{model}' is not downloaded.\nRun: python -m spacy download {model}"


def invalid_pattern_row(problem: PatternProblem) -> str:
    """Return the tooltip of a pattern row: why it cannot be saved."""
    tag = problem.tag
    if problem.kind is PatternProblemKind.TAG_INVALID:
        reason = f"'{tag}' is not a tag: letters, digits and '_', starting with a letter"
    elif problem.kind is PatternProblemKind.TAG_TWICE:
        reason = f"the tag '{tag}' is used twice"
    else:
        reason = f"the pattern of '{tag}' does not compile: {problem.detail}"
    return f"Invalid pattern: {reason}"


def invalid_patterns(count: int) -> str:
    """Return the line above the patterns table: how many rows cannot be saved."""
    plural = "s" if count > 1 else ""
    verb = "have" if count > 1 else "has"
    return f"{count} pattern{plural} {verb} errors — fix before saving."


def preview_failed(reason: str) -> str:
    """Explain why the anonymisation preview could not run."""
    return f"Preview failed: {reason}"


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

#: Where a step runs, in the few words the panel has room for.  The service list on the AI
#: models page uses the same words, so the two never describe one place differently.
PLACE_THIS_COMPUTER = "this computer"
PLACE_OWN_SERVER = "own server"
PLACE_ONLINE = "online"

#: The words of a step that runs the same model as the one before it.
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


#: How a step's row words the names: short, because it shares the line with the place.
NAMES_HIDDEN_SHORT = "names hidden"
#: How a stored answer words them, where there is room for the full sentence.
NAMES_REPLACED_WHERE_DETECTED = "names replaced where detected"
NAMES_SENT_AS_WRITTEN = "names sent as written"


def stage_badge(stage: DataFlowStage) -> str:
    """Return the short line for a step: where it runs and how names are handled."""
    if stage.detail:
        return stage.detail
    parts = [stage.location.label]
    if stage.leaves_computer:
        parts.append(NAMES_HIDDEN_SHORT if stage.personal_info_hidden else NAMES_AS_WRITTEN)
    return " · ".join(parts)


def stage_summary(stage: DataFlowStage) -> str:
    """Return one line: the step, where it runs, and (when it leaves) what it receives."""
    if stage.detail:
        return f"{stage_title(stage.stage)}: {stage.detail}"
    line = f"{stage_title(stage.stage)}: {stage.location.label}"
    if not stage.leaves_computer:
        return line
    names = NAMES_REPLACED_WHERE_DETECTED if stage.personal_info_hidden else NAMES_SENT_AS_WRITTEN
    return f"{line} — receives {stage.receives}, {names}"


def outbound_summary(flow: DataFlow) -> str:
    """Return the steps that leave this computer as one line, for a remembered answer."""
    return "; ".join(stage_summary(stage) for stage in flow.outbound)


def describe_fingerprint(fingerprint: str) -> str:
    """Return a readable line for a stored data-flow fingerprint.

    A remembered answer keeps the fingerprint of what was agreed to; nobody can read it
    (``correction:online:Mistral::0``), so the list shows this line instead.  A part that does
    not parse is shown as it is, so nothing is ever hidden from the list.
    """
    lines = [
        part if isinstance(part, str) else f"{stage_title(part.stage)}: {_part_place(part)}"
        for part in fingerprint_parts(fingerprint)
    ]
    return "; ".join(line for line in lines if line)


def _part_place(part: FingerprintPart) -> str:
    """Return the place and the names of one parsed part of a stored fingerprint."""
    names = NAMES_REPLACED_WHERE_DETECTED if part.names_hidden else NAMES_SENT_AS_WRITTEN
    return f"{part.location.label} — {names}"


def advice_text(advice: Advice) -> str:
    """Return the sentence that tells the teacher what *advice* means for them."""
    return ADVICE_TEXT[advice]


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


def uses_text(stage: DataFlowStage, model: str) -> str:
    """Return what a step uses, as plain words: ``<model> · <place>``.

    Without a model (the built-in reader has none) the engine's name stands in.  The model is
    the short name (:func:`short_model_name`); the full tag belongs in the tooltip.  The place
    is added only when it is not this computer, because that is the case the line warns about.
    """
    text = short_model_name(model) if model else stage.location.service
    if stage.location.location is not DataLocation.THIS_COMPUTER:
        text = f"{text} · {short_place(stage.location)}"
    return text


def names_text(stage: DataFlowStage) -> str:
    """Return the words under personal-info removal: the languages whose names are hidden.

    Empty while the step is off — the checkbox already says so.  The languages come from the
    models that will be loaded.
    """
    if not stage.personal_info_hidden:
        return ""
    names = [language_name(code) for code in stage.languages]
    return NAMES_IN_LANGUAGES.format(languages=and_list(names)) if names else stage.detail


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


# ── The "What is sent for this file" dialog and the data-flow box ────────────
#
# The dialog only highlights the placeholders of the text it is given; every word around that
# text is here.  The box under the steps of the main window reads its title and tooltip here.

TAB_SENT = "What is sent"
TAB_PERSONAL = "Personal information"

#: The dialog's opening line: the same sentence as the local level below it.
NOTHING_LEAVES = LEVEL_TEXT[PrivacyLevel.LOCAL]
NO_SECTION = "No step sends this file's text away."
PAGES_SENT = "The page images of this file are sent to another computer you control."
SHOW_PROMPT = "Show the instructions sent"
HIDE_PROMPT = "Hide the instructions sent"
EMPTY_TEXT = "(the text is empty)"
#: What the instructions block says for a step that sends none.
NO_INSTRUCTIONS = "(no instructions are sent with this step)"
#: The note above the text: whether the step sees the names or placeholders.
NAMES_HIDDEN_NOTE = "Names are hidden before the text is sent."
NAMES_AS_WRITTEN_NOTE = "Names are sent as written."
PERSONAL_COMPARE_NOTE = (
    "Left: the extracted text. Right: what a service sees when names are hidden."
)

#: The box under the steps: what it is called, and what the dots under the steps mean.
DATA_FLOW_TITLE = "WHERE YOUR DOCUMENTS ARE PROCESSED"
DATA_FLOW_TOOLTIP = (
    "Where your documents go with these settings. The dot next to each step under "
    "\u201cWhat should happen\u201d says where that step runs."
)
CHANGE_LINK = "change…"

# ── Settings → Privacy: the page's three tabs and its list of answers ────────

PAGE_TAB_SENDING = "Sending data away"
PAGE_TAB_DETECTION = "Personal info: detection"
PAGE_TAB_PATTERNS = "Personal info: patterns"
SHOWING_TEXTS_GROUP = "Showing texts"
SENDING_INTRO = (
    "Before a run sends text or pages away from this computer, you are asked first. If you "
    "tick “Don't ask again”, the answer is remembered for exactly those settings and listed "
    "here. Take an answer back and you are asked again the next time."
)
LIST_HEADING = "You are not asked again for:"
NOTHING_REMEMBERED = (
    "No answers are remembered. You are asked before every run that sends documents away "
    "from this computer."
)
ASK_AGAIN_SELECTED = "Ask again for selected"
ASK_AGAIN_ALL = "Ask again for all"
CHANGES_ON_OK = "Changes are saved when you press OK."

# ── Settings → Privacy: what the app stored for earlier runs ─────────────────

STORED_GROUP = "Stored results"
STORED_EXPLAIN = (
    "The app keeps what it recognized and proofread for every document, so a second run does "
    "not read the pages again. After deleting it the next run reads and proofreads them again, "
    "which takes time and, with an online service, is paid for again."
)
STORED_NOTHING = "Nothing is stored for earlier runs."
STORED_ONE_DOCUMENT = "1 document"
STORED_DOCUMENTS = "{count} documents"
STORED_DELETE = "Delete stored results…"
STORED_INCLUDE_WORK = "Also delete my corrections, marks and edited texts"
STORED_INCLUDE_WORK_TIP = (
    "What you corrected by hand, the marks you set and the texts you edited. They cannot be "
    "brought back; your Word and text files stay."
)
STORED_KEEP = "Delete stored results older than"
STORED_KEEP_SUFFIX = " days"


def stored_size(documents: int, size: int) -> str:
    """Return how much the app keeps: ``"12 documents · 48.0 MB"``, or that it keeps nothing."""
    if documents <= 0:
        return STORED_NOTHING
    count = STORED_ONE_DOCUMENT if documents == 1 else STORED_DOCUMENTS.format(count=documents)
    return f"{count} · {_size_text(size)}"


def _size_text(size: int) -> str:
    """Return *size* in bytes in the largest unit that still reads as a number: kB, MB or GB."""
    kilobyte = 1_000
    megabyte = 1_000 * kilobyte
    gigabyte = 1_000 * megabyte
    if size < megabyte:
        return f"{max(size, 0) / kilobyte:.0f} kB"
    if size < gigabyte:
        return f"{size / megabyte:.1f} MB"
    return f"{size / gigabyte:.1f} GB"


def names_in_text_note(hidden: bool) -> str:
    """Return the one sentence that says whether a step sees the real names."""
    return NAMES_HIDDEN_NOTE if hidden else NAMES_AS_WRITTEN_NOTE


def pages_line(title: str, badge: str) -> str:
    """Return the line above the page sections: the step, its badge, where its pages go."""
    return f"{title} — {badge}\n{PAGES_SENT}"
