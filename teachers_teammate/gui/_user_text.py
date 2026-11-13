"""User-facing wording for the proofreading review (explanations and unclear expressions).

Qt-free on purpose, like ``_model_wording``: every text a teacher reads about the review
lives here, so the wording can be reviewed, unit-tested and translated in one place.  The
widgets import these names; they never build such sentences themselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..domain.correction_review import CHANGE_CATEGORIES, AlignedReview
from ..exceptions import RECOGNITION_OFF_MESSAGE

if TYPE_CHECKING:
    from ..application.text_size import TextSizeEstimate

# ── Tab title and filters ─────────────────────────────────────────────────────

CHANGES_TAB = "Changes"

FILTER_ALL = "All"
FILTER_CHANGES = "Changes"
FILTER_UNCLEAR = "Unclear"

# ── Diff / notes wording ──────────────────────────────────────────────────────

NO_OCR_TEXT = "(no OCR text available for comparison)"
NO_CORRECTION_TEXT = "(no correction available - skipped or not yet generated)"

NO_EXPLANATION = "no explanation"
YOUR_CHANGE = "your change"

ORPHAN_HEADING = "Not in the text"
ORPHAN_HINT = "The AI suggested these changes, but they are not in the text."
UNPLACED_HEADING = "Could not be placed in the text"
UNPLACED_HINT = "The AI changed these sentences, but they could not be found in the text."

COLUMN_EXPRESSION = "Original expression"
COLUMN_WHY = "Why it is unclear"
COLUMN_RECOMMENDED = "Recommended version"

WHY_PREFIX = "Why:"
RECOMMENDED_PREFIX = "Recommended:"
SUGGESTION_PREFIX = "Suggestion:"
MEANING_NOT_CERTAIN = "meaning not certain"

NOT_IN_TEXT = "not in the text"

NO_NOTES = "No changes and no unclear expressions for this file."
NO_CHANGE_NOTES = "No changes for this file."
NO_UNCLEAR_NOTES = "No unclear expressions for this file."

CHANGE_CATEGORY_LABELS: dict[str, str] = {
    "grammar": "Grammar",
    "spelling": "Spelling",
    "punctuation": "Punctuation",
    "word_choice": "Word choice",
    "word_order": "Word order",
    "other": "Other",
}

#: Fallback label for a category the catalog does not name.
OTHER_CATEGORY = CHANGE_CATEGORY_LABELS[CHANGE_CATEGORIES[-1]]

LEVEL_HINT = "These instructions don't use the level setting ({level})."

CONFLICT_HINT = (
    'Your instructions seem to set the answer format ("return only", "JSON", "[unclear]"). '
    "The app already sets the format; such a line may confuse the model."
)
CONFLICT_MARKERS = (
    "return only",
    "json",
    "[unclear]",
    "gib ausschließlich",
    "gib nur",
    "renvoie uniquement",
    "devuelve únicamente",
    "restituisci solo",
    "devolva apenas",
    "geef alleen",
)

PROMPT_DIALOG_FIXED_TEXT = (
    "Always added by the app (read-only): for Markdown texts, a note about the writing marks. "
    "This part cannot be edited."
)

# ── Prompt dialog wording ─────────────────────────────────────────────────────

YOUR_INSTRUCTIONS_HEADING = "Instructions"
BUILTIN_STATUS = "Built-in instructions. They follow the language of the document."
CUSTOM_STATUS = "Your own instructions. They are used for every language."
CUSTOM_PROMPT_NOTE = CUSTOM_STATUS
RESET_BUTTON = "Reset to default"
RESET_BUTTON_TOOLTIP = (
    "Put the built-in instructions of the document language back. Your changes in the box are lost."
)
LEVEL_PLACEHOLDER_TOOLTIP = "{level} is replaced by the Level next to the document language."
FIXED_HEADING = "Always added by the app"
ANSWER_FORMAT_HEADING = "Answer format (advanced)"
ANSWER_FORMAT_TOOLTIP = (
    "How the model must fill in its reply. The app reads the reply in this format, so change "
    "it only if you know what you are doing. The notation note for Markdown texts is added "
    "after it and cannot be edited."
)
ANSWER_FORMAT_WARNING = (
    "The app reads the model's reply in this format. If the model no longer follows it, "
    "the files fall back to corrections only."
)
ANSWER_FORMAT_REVIEW_TITLE = "With explanations"
ANSWER_FORMAT_TEXT_TITLE = "Corrections only"
ANSWER_FORMAT_RESET_TOOLTIP = "Put the built-in answer format back."

CORRECTION_PROMPT_DIALOG_TITLE = "Proofreading options"
EVALUATION_PROMPT_DIALOG_TITLE = "Content review options"

EXPLANATIONS_SUPPORTED = "explanations ✓"
EXPLANATIONS_SUPPORTED_TOOLTIP = (
    "This model was checked with the explanation format: proofreading with explanations "
    "works with it."
)

# ── Settings window: page names and "where to change it" ──────────────────────

PAGE_GENERAL = "General"
PAGE_AI_MODELS = "AI models"
PAGE_SCAN_QUALITY = "Scan quality"
PAGE_PRIVACY = "Privacy"
PAGE_AI_SETUP = "This computer"
PAGE_ONLINE_SERVICES = "Online services"
PAGE_ADDONS = "Add-ons"

#: The one place every message names when it says where a setting is changed.
WHERE_AI_MODELS = f"Settings → {PAGE_AI_MODELS}"
WHERE_SCAN_QUALITY = f"Settings → {PAGE_SCAN_QUALITY}"
WHERE_PRIVACY = f"Settings → {PAGE_PRIVACY}"
WHERE_AI_SETUP = f"Settings → {PAGE_AI_SETUP}"
WHERE_ONLINE_SERVICES = f"Settings → {PAGE_ONLINE_SERVICES}"
WHERE_ADDONS = f"Settings → {PAGE_ADDONS}"
WHERE_RESULTS = "Results, in the main window"
WHERE_STEPS = "What should happen, in the main window"
WHERE_SETUP = "Tools → Setup Assistant…"
OPEN_ADDONS = "Open Add-ons…"

#: The step that replaces names before text is sent anywhere: the checkbox in the main window
#: and the line on the AI models page that offers to turn it on must say the same words.
REMOVE_PERSONAL_INFO = "Remove personal info"

# ── Main window: the steps ────────────────────────────────────────────────────

RECOGNIZE_TEXT = "Recognize text"
RECOGNIZE_TEXT_TOOLTIP = (
    "Read the text from scans (photos and scanned pages).\n"
    "Text files and PDFs that already contain their text are used as they are.\n"
    "Switched off, only that existing text is used and scans are skipped."
)
RECOGNIZE_NOT_NEEDED = "Not needed here: the text of these documents is already available."


def scans_skipped_note(count: int) -> str:
    """Return the note under a switched-off "Recognize text" when *count* scans are queued."""
    noun = "scan" if count == 1 else "scans"
    return f"{count} {noun} will be skipped because text recognition is off."


#: The tooltip of a "skipped" cell in the queue (the same sentence the pipeline reports).
RECOGNITION_OFF_REASON = RECOGNITION_OFF_MESSAGE
NOTHING_TO_READ_LINE = "Nothing to read: no document has text to use without recognition"
TURN_ON_RECOGNITION = "Turn on"
PROOFREAD = "Proofread"
REVIEW_CONTENT = "Review content"
RULES_BUTTON = "Rules…"
OPTIONS_BUTTON = "Options…"
GUARD_TITLE = "{step} can't be turned on"
RUN_BLOCKED_TITLE = "The run can't start"
SAME_MODEL_AS_PROOFREADING = "Same model as proofreading"
REVIEW_NEEDS_PROOFREADING = (
    "Content review reads the proofread text, so it runs only together with proofreading."
)

# ── Ollama ────────────────────────────────────────────────────────────────────

#: How to start Ollama where the app can't do it: on Linux and in WSL the service needs admin
#: rights, and a second server started as the user would use another models folder.
OLLAMA_SERVICE_HINT = "Start the Ollama service, for example with `sudo systemctl start ollama`."

# ── Settings wording ──────────────────────────────────────────────────────────

EXPLANATIONS_GROUP = "Explanations"
SAME_AS_TEXT = "Same as the text"
EXPLANATION_LANGUAGE_TOOLTIP = (
    "Language of the explanations and of the unclear-expression notes. The corrections "
    "themselves stay in the language of the student text."
)
CORRECTIONS_ONLY = "Corrections only (faster)"
CORRECTIONS_ONLY_TOOLTIP = (
    "Only rewrite the text, without explanations and without the unclear-expression list. "
    "Faster, and works with every model."
)
LEVEL_TOOLTIP = (
    "Target language level for the corrections (CEFR, A1 to C2). The proofreading keeps the "
    "student's wording and only changes what is wrong at this level. Used by proofreading."
)


def context_auto_review(tokens: int) -> str:
    """Return the "Automatic" entry for a content review that gets *tokens* tokens."""
    return f"Automatic (now: {tokens:,} tokens for the content review)"


FORMATTED_TEXT_NOTE = (
    "If the text has formatting marks (bold, crossed out, ...), this is added as well:"
)


def formatted_text_note(hint: str) -> str:
    """Return the read-only note about formatted (Markdown) texts, or ``""`` without a *hint*."""
    return f"{FORMATTED_TEXT_NOTE}\n{hint}" if hint else ""


def answer_format_missing(fields: list[str]) -> str:
    """Return the warning for an edited answer format that no longer names some *fields*."""
    return (
        f"This format no longer mentions: {', '.join(fields)}. "
        "The app may not be able to read the model's reply."
    )


def category_label(category: str) -> str:
    """Return the teacher-facing name of a change *category*."""
    return CHANGE_CATEGORY_LABELS.get(category, OTHER_CATEGORY)


def trailing_toggle(count: int, *, expanded: bool) -> str:
    """Return the link text that opens or closes the section of notes not in the text."""
    return f"{'▾' if expanded else '▸'} {ORPHAN_HEADING} ({count})"


def minor_toggle(count: int, *, expanded: bool) -> str:
    """Return the link text that opens or closes the spelling and punctuation changes."""
    noun = "correction" if count == 1 else "corrections"
    return (
        f"{'▾' if expanded else '▸'} {count} spelling and punctuation {noun} (marked in the text)"
    )


def changes_tab_title(aligned: AlignedReview | None) -> str:
    """Return the tab title, with the change and unclear counts when there are any."""
    if aligned is None:
        return CHANGES_TAB
    changes = aligned.listed_count
    unclear = aligned.unclear_count
    if not changes and not unclear:
        return CHANGES_TAB
    if unclear:
        return f"{CHANGES_TAB} ({changes} · {unclear} ?)"
    return f"{CHANGES_TAB} ({changes})"


def filter_changes_label(count: int) -> str:
    """Return the *Changes* filter button label for *count* changes."""
    return f"{FILTER_CHANGES} {count}"


def filter_unclear_label(count: int) -> str:
    """Return the *Unclear* filter button label for *count* unclear expressions."""
    return f"{FILTER_UNCLEAR} {count}"


def unclear_badge(count: int) -> str:
    """Return the short badge shown in the results table, e.g. ``"?2"``."""
    return f"?{count}" if count else ""


def recommendation_caption(is_suggestion: bool) -> str:
    """Return the caption in front of a recommended version."""
    return SUGGESTION_PREFIX if is_suggestion else RECOMMENDED_PREFIX


def suggestion_note(is_suggestion: bool) -> str:
    """Return the parenthetical shown under a suggested version, or ``""``."""
    return MEANING_NOT_CERTAIN if is_suggestion else ""


# ── What is sent for this file ────────────────────────────────────────────────

WHAT_IS_SENT_TITLE = "What is sent for this file"
WHAT_IS_SENT_WORKING = "Working out what is sent…"
NO_TEXT_YET = "No text recognized for this file yet."


# ── Pieces counter after OCR ──────────────────────────────────────────────────

TEXT_SIZE_PARTS = "{words} words · proofread in {pieces} parts"
TEXT_SIZE_TOO_LONG = "too long for content review"
TEXT_SIZE_TOOLTIP = (
    "Your model can read about {words_per_piece} words at once, so this text is proofread "
    "in {pieces} parts. A change that spans two parts cannot be made."
)
TEXT_SIZE_TOO_LONG_TOOLTIP = (
    "The content review cannot be split and needs the whole text at once; this text is "
    "longer than the model can read."
)


def text_size_line(estimate: TextSizeEstimate | None) -> tuple[str, str, str]:
    """Return ``(line, warning, tooltip)`` for the footer of the Extracted Text tab.

    All three are empty when nothing is shown: the counter is only worth showing when the
    text is proofread in several parts, because a change across a part boundary cannot be
    made.  *warning* is the amber part, shown only together with the parts: a content review
    reserves a fixed answer allowance, so a text that goes out whole for proofreading always
    fits the review.
    """
    if estimate is None or estimate.pieces is None or estimate.pieces <= 1:
        return "", "", ""
    line = TEXT_SIZE_PARTS.format(words=f"{estimate.words:,}", pieces=estimate.pieces)
    per_piece = f"{estimate.words_per_piece:,}" if estimate.words_per_piece else "?"
    tooltip = TEXT_SIZE_TOOLTIP.format(words_per_piece=per_piece, pieces=estimate.pieces)
    if estimate.content_review_fits is False:
        return line, TEXT_SIZE_TOO_LONG, f"{tooltip}\n\n{TEXT_SIZE_TOO_LONG_TOOLTIP}"
    return line, "", tooltip


# ── Speed test and time estimates ─────────────────────────────────────────────

SPEED_HEADING = "Speed on this computer"
SPEED_TEST = "Test speed"
SPEED_STOP = "Stop"
SPEED_TEST_TOOLTIP = (
    "Measures how fast the chosen models write on this computer.\n"
    "It uses the graphics card for up to a minute and a half per step, so it runs only\n"
    "when you ask for it. Nothing is sent anywhere else."
)
SPEED_NOTE = (
    f"Measures the models chosen on {PAGE_AI_MODELS}. "
    "It runs only when you press the button, never by itself."
)
SPEED_NOTHING_TO_MEASURE = (
    f"Nothing to measure: no step uses a model on this computer. The steps and their models "
    f"are chosen on {PAGE_AI_MODELS}."
)
SPEED_CHANGE_MODELS = "Change models…"
SPEED_CHANGE_MODELS_TOOLTIP = f"Opens {PAGE_AI_MODELS}, where the models are chosen."
SPEED_HINT_MEASURE = "Measure…"
SPEED_HINT_DETAILS = "Details…"
SPEED_HINT_TOOLTIP = f"Opens {PAGE_AI_SETUP}, where the speed of this computer is measured."
SPEED_STOP_TOOLTIP = "Stop the test. Nothing is saved."
SPEED_STOPPING = "Stopping…"
SPEED_TESTING = "Testing… this takes up to a minute and a half per step."
SPEED_TEST_FAILED = "The test could not run: {reason}"
SPEED_NEVER_MEASURED = "Not measured on this computer yet."
SPEED_ON_CARD = "on the card ✓"
SPEED_ON_PROCESSOR = "on the processor"
SPEED_TOO_SLOW = "very slow (no answer in time)"
SPEED_BUSY = "The server was busy with another model during the test; the times may be too long."
SPEED_OUT_OF_DATE = (
    "Measured {date}, but the model, the Ollama version or the graphics card has changed "
    "since. Test again for a figure that fits."
)
SPEED_REMOTE = "Tested on the server at {host}; other people may be using it."
SPEED_CARD_NOT_USED = (
    "⚠ Ollama runs {model} on the processor, although {card} was found. "
    "A smaller model fits on the card: {where}."
)
SPEED_SWAPS = (
    "The reading and proofreading models don't fit in memory together: "
    "each file adds about {load} of loading."
)
SPEED_CLASS_SET = "{files} two-page essays: about {span}"
SPEED_PER_PAGE = "about {span} per page"
SPEED_MEASURED_NOTE = "about {span} per page (measured)"
SPEED_EXPECTED_LABEL = "{band} (expected)"
SPEED_EXPECTED_NOTE = (
    "Expected speed on this computer: {band}. Worked out from the models measured here, "
    "not measured for this one."
)
SPEED_MEASURED_AT = "Measured {date}, {power}."
SPEED_FROM_RUNS = "From your last {count} {unit}."
SPEED_FROM_RUNS_ONE = "From your last {unit}."
SPEED_ON_MAINS = "on mains power"
SPEED_ON_BATTERY = "on battery power, so slower than usual"

#: Teacher-facing names of the steps the test measures.
SPEED_STEP_NAMES: dict[str, str] = {
    "reading": "Reading",
    "proofreading": "Proofreading",
    "review": "Content review",
}

#: Teacher-facing names of the speed bands.
SPEED_BAND_NAMES: dict[str, str] = {
    "fast": "fast",
    "ok": "ok",
    "slow": "slow",
    "very_slow": "very slow",
}


def speed_step_name(step: str) -> str:
    """Return the teacher-facing name of a measured *step*."""
    return SPEED_STEP_NAMES.get(step, step)


def speed_band_name(band: str) -> str:
    """Return the teacher-facing name of a speed *band*."""
    return SPEED_BAND_NAMES.get(band, band)


def format_duration(seconds: float) -> str:
    """Return a span of *seconds* the way a teacher reads it: ``"40 s"``, ``"7 min"``, ``"2 h"``.

    Rounded to whole seconds below a minute and to whole minutes above it: an estimate that
    claims a second of precision would be a lie.
    """
    total = max(0, round(seconds))
    if total < 60:
        return f"{total} s"
    if total < 3600:
        return f"{round(total / 60)} min"
    return _hours_text(total)


def _hours_text(seconds: float) -> str:
    """Return whole *seconds* as hours and minutes: ``"1 h 30 min"``, ``"3 h"``."""
    hours, rest = divmod(round(seconds / 60), 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def format_span(low_s: float, high_s: float) -> str:
    """Return a range of seconds as one phrase, both ends in the same unit.

    The unit comes from the high end, so the two ends can be compared at a glance: ``(20, 30)``
    gives ``"20-30 s"``, ``(420, 720)`` gives ``"7-12 min"`` and ``(5400, 10800)`` gives
    ``"1 h 30 min - 3 h"``.  Seconds are kept up to two minutes, because a low end of 25 s would
    otherwise round to ``"0 min"``.
    """
    low, high = max(0, round(low_s)), max(0, round(high_s))
    if low == high:
        return format_duration(low)
    if high < 120:
        return f"{low}-{high} s"
    if high < 3600:
        return f"{round(low / 60)}-{round(high / 60)} min"
    return f"{_hours_text(low)} - {_hours_text(high)}"


def speed_per_page(low_s: float, high_s: float) -> str:
    """Return the per-page figure of one step, e.g. ``"about 20-30 s per page"``."""
    return SPEED_PER_PAGE.format(span=format_span(low_s, high_s))


def speed_measured_note(low_s: float, high_s: float) -> str:
    """Return the tooltip note of a model whose speed was measured on this computer."""
    return SPEED_MEASURED_NOTE.format(span=format_span(low_s, high_s))


def speed_expected_label(band: str) -> str:
    """Return the compact label of a model whose speed was not measured here."""
    return SPEED_EXPECTED_LABEL.format(band=speed_band_name(band))


def speed_expected_note(band: str) -> str:
    """Return the tooltip line behind that label."""
    return SPEED_EXPECTED_NOTE.format(band=speed_band_name(band))


def speed_class_set(files: int, low_s: float, high_s: float) -> str:
    """Return the example line for a class set of *files* two-page essays."""
    return SPEED_CLASS_SET.format(files=files, span=format_span(low_s, high_s))


def speed_measured_at(stamp: str, *, on_battery: bool | None) -> str:
    """Return the date line under the rows, or ``""`` when nothing was measured.

    *stamp* is the stored ``YYYY-MM-DD HH:MM`` stamp; only its date is shown, because the
    figure is a property of the machine, not of the minute it was taken.
    """
    if not stamp:
        return ""
    power = SPEED_ON_BATTERY if on_battery else SPEED_ON_MAINS
    return SPEED_MEASURED_AT.format(date=stamp[:10], power=power)


def speed_from_runs(count: int, *, pages: bool) -> str:
    """Return the line that says the figures come from real runs, or ``""`` without any.

    *pages* says the count is pages rather than documents: a run that proofread nothing
    measured only text recognition, which happens once per page.
    """
    if count <= 0:
        return ""
    unit = "page" if pages else "document"
    if count == 1:
        return SPEED_FROM_RUNS_ONE.format(unit=unit)
    return SPEED_FROM_RUNS.format(count=count, unit=unit + "s")


def speed_card_not_used(model: str, card: str) -> str:
    """Return the warning that Ollama ran *model* on the processor although *card* was found."""
    return SPEED_CARD_NOT_USED.format(model=model, card=card, where=WHERE_AI_MODELS)


def speed_swaps(load_s: float) -> str:
    """Return the warning that the models evict each other, with *load_s* seconds per file."""
    return SPEED_SWAPS.format(load=format_duration(load_s))


def speed_out_of_date(stamp: str) -> str:
    """Return the warning that a measurement from *stamp* no longer fits this machine."""
    return SPEED_OUT_OF_DATE.format(date=stamp[:10])


def speed_remote(host: str) -> str:
    """Return the note that the test ran on the server at *host*."""
    return SPEED_REMOTE.format(host=host)


def speed_test_failed(reason: str) -> str:
    """Return the line shown when the test could not run at all."""
    return SPEED_TEST_FAILED.format(reason=reason)


# ── Hiding personal information in the previews ───────────────────────────────

HIDE_PERSONAL_INFO_CHECKBOX = "Hide personal information in the texts shown in this app"
HIDE_PERSONAL_INFO_TIP = (
    "Show placeholder tokens such as [PERSON_1] instead of names and other personal\n"
    "information in the extracted text, the proofread text, the changes and the content\n"
    "review. A GUI preference, not part of the processing settings."
)
HIDE_PERSONAL_INFO_UNAVAILABLE_TIP = (
    "Hiding personal information needs the Privacy add-on and a spaCy model for the\n"
    f"document language. Install them under {WHERE_ADDONS} (Name detection)."
)
HIDING_PERSONAL_INFO = "Hiding personal information…"
HIDING_PERSONAL_INFO_FAILED = "Personal information could not be hidden: {reason}"
TURN_OFF_HIDING_TO_EDIT = (
    "Personal information is hidden, so the texts can't be edited. Click here to show it and edit."
)
HIDE_PERSONAL_INFO_BUTTON = "👁"
HIDE_PERSONAL_INFO_BUTTON_TIP = "Hide personal information in the texts shown here"
SHOW_PERSONAL_INFO_BUTTON_TIP = "Show names again in the texts shown here"
