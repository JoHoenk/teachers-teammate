"""User-facing wording for the proofreading review (explanations and unclear expressions).

Qt-free on purpose, like ``_model_wording``: every text a teacher reads about the review
lives here, so the wording can be reviewed, unit-tested and translated in one place.  The
widgets import these names; they never build such sentences themselves.
"""

from __future__ import annotations

from ..domain.correction_review import CHANGE_CATEGORIES, AlignedReview

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
LEVEL_PLACEHOLDER_TOOLTIP = "{level} is replaced by the Level setting of the proofreading."
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

CORRECTION_PROMPT_DIALOG_TITLE = "Proofreading Instructions"
EVALUATION_PROMPT_DIALOG_TITLE = "Content Review Instructions"

EXPLANATIONS_SUPPORTED = "explanations ✓"
EXPLANATIONS_SUPPORTED_TOOLTIP = (
    "This model was checked with the explanation format: proofreading with explanations "
    "works with it."
)

# ── Settings wording ──────────────────────────────────────────────────────────

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
    "student's wording and only changes what is wrong at this level."
)


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


# ── Hiding personal information in the previews ───────────────────────────────

HIDE_PERSONAL_INFO_ACTION = "Hide personal information in texts"
HIDE_PERSONAL_INFO_TIP = (
    "Show placeholder tokens such as [PERSON_1] instead of names and other personal\n"
    "information in the extracted text, the proofread text, the changes and the content\n"
    "review. A GUI preference, not part of the processing settings."
)
HIDE_PERSONAL_INFO_UNAVAILABLE_TIP = (
    "Hiding personal information needs the Privacy add-on and a spaCy model for the\n"
    "document language. Open Advanced Settings → Downloads to install them."
)
HIDING_PERSONAL_INFO = "Hiding personal information…"
HIDING_PERSONAL_INFO_FAILED = "Personal information could not be hidden: {reason}"
TURN_OFF_HIDING_TO_EDIT = (
    "Personal information is hidden, so the texts can't be edited. Click here to show it and edit."
)
HIDE_PERSONAL_INFO_BUTTON = "👁"
HIDE_PERSONAL_INFO_BUTTON_TIP = "Hide personal information in the texts shown here"
SHOW_PERSONAL_INFO_BUTTON_TIP = "Show names again in the texts shown here"
