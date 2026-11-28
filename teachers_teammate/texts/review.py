"""What the window says about the review, a document and a run.

Every sentence a teacher reads about the proofreading review, the prompt dialogs, the queue,
the speed test and the way a stored result was sent is defined here, so the wording can be
reviewed, unit-tested and translated in one place.  The widgets import these names; they
never build such sentences themselves.  The Add-ons page has its own module,
:mod:`~teachers_teammate.texts.addons`.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.correction_prompts import NOTES_LIMIT
from ..domain.data_location import (
    DataLocation,
    SentRecord,
    SentStatus,
    StageLocation,
)
from ..domain.proofreading.review import CHANGE_CATEGORIES
from ..domain.stages import Stage
from .app import stage_title
from .check import FILTER_UNCLEAR
from .settings import PAGE_AI_MODELS, WHERE_ADDONS, WHERE_AI_MODELS

# ── The review's wording ──────────────────────────────────────────────────────

YOUR_CHANGE = "your change"

WHY_PREFIX = "Why:"
RECOMMENDED_PREFIX = "Recommended:"
SUGGESTION_PREFIX = "Suggestion:"

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

#: What a content review warns with when the text does not fit a local model's window.
TOO_LONG_FOR_REVIEW_WARNING = "The text is too long for a content review with a local model."

# ── What the proofreading exchanges warn about ────────────────────────────────

#: A step ① that twice answered with a lead-in or a closing remark around the text.
FRAMING_WARNING = "The model answered with a remark around the text; keeping original text."
#: A step ① that twice answered with much less of the text than it was sent.
LOST_TEXT_WARNING = "The model dropped part of the text; keeping original text."
#: A step ① that twice answered with much more text than it was sent.
ADDED_TEXT_WARNING = "The model added text of its own; keeping original text."
#: A Markdown text whose answer could not be restored everywhere.
UNSTYLED_WARNING = "Part of the text lost its formatting."
#: A file whose step ② left edits unexplained, or only some pieces of a long text answered.
EXPLANATIONS_MISSING = "Explanations are missing for part of the text."


#: A file whose step ① gave no usable answer.
CORRECT_FAILED = "Proofreading failed; keeping original text."
#: A file whose step ③ did not answer.
UNCLEAR_FAILED = "Unclear wording could not be checked for this text."
#: An exchange that failed without saying why, and a request no answer came back for.
PROOFREADING_FAILED = "Proofreading failed; keeping the original text."
PROOFREADING_NO_ANSWER = "Proofreading failed; the model did not answer."
#: A chunked file whose one piece failed: no half-corrected text is ever stored.
CHUNK_FAILURE = "Proofreading part of the text failed; keeping the original text."
#: The card and the mark tip of a stretch the answer did not proofread, which was put back.
#: Said here rather than stored with the result, so an older result never keeps older words.
NOT_PROOFREAD_WHY = (
    "The AI did not proofread this passage. It stands as the student wrote it: read it yourself."
)
#: A structured answer that does not have the shape the request asked for.
ANSWER_NOT_STRUCTURED = "the answer did not match the required structure"


def correction_failed(reason: BaseException) -> str:
    """Return the warning of a proofreading request that failed in transport."""
    return f"Correction failed ({reason})."


def evaluation_failed(reason: BaseException) -> str:
    """Return the warning of a content review request that failed in transport."""
    return f"Evaluation failed ({reason})."


def left_out_warning(count: int, words: int) -> str:
    """Return the warning about *count* passages of *words* words the AI did not proofread."""
    return (
        f"The AI did not proofread {count} passages ({words} words); they stand as the student "
        "wrote them."
    )


def left_out_warning_one(words: int) -> str:
    """Return the same warning for one passage."""
    return f"The AI did not proofread a passage ({words} words); it stands as the student wrote it."


#: What a run says about a stored proofreading whose words are no longer at the offsets it points
#: at: the corrected text stays, its explanations do not.  Only that run says it — a later window
#: drops the review again without a sentence, because nothing of the earlier run is left to say it.
REVIEW_DOES_NOT_FIT = (
    "The stored proofreading no longer matches the text; its explanations were left out."
)

#: What a run says about a review of an anonymized text it could not carry over to the real names:
#: the corrected text stays, the explanations do not.
REVIEW_UNMATCHED = (
    "The explanations could not be matched to the names in the text and were left out."
)


def personal_info_still_hidden(tokens: Sequence[str]) -> str:
    """Return the warning about stand-ins for personal data that are still in the text."""
    shown = ", ".join(tokens[:3])
    rest = f" and {len(tokens) - 3} more" if len(tokens) > 3 else ""
    return (
        f"Personal info is still hidden in the text ({shown}{rest}). "
        "The AI changed those stand-ins; check them before you use the text."
    )


# ── Prompt dialog wording ─────────────────────────────────────────────────────

YOUR_INSTRUCTIONS_HEADING = "Instructions"
BUILTIN_STATUS = "Built-in instructions. They follow the language of the document."
CUSTOM_STATUS = "Your own instructions. They are used for every language."
RESET_BUTTON = "Reset to default"
RESET_BUTTON_TOOLTIP = (
    "Put the built-in instructions of the document language back. Your changes in the box are lost."
)
FIXED_HEADING = "Always added by the app"

CORRECTION_PROMPT_DIALOG_TITLE = "Proofreading options"
EVALUATION_PROMPT_DIALOG_TITLE = "Content review options"

#: The proofreading dialog: what the teacher may add, and what the app sends regardless.
NOTES_HEADING = "Notes for the AI (optional)"
NOTES_PLACEHOLDER = (
    "For example: Year 9, second foreign language; ignore missing capital letters in headings."
)
NOTES_TOOLTIP = (
    "Anything the AI should know about this class or this text. Your notes are added to every "
    "request of the proofreading."
)
TASK_TEXT_HEADING = "Task text"
SKIP_TASK_TEXT = "Don't proofread the task text"
SKIP_TASK_TEXT_TOOLTIP = (
    "Leave out the printed text that repeats in most documents of a worksheet — the header, the "
    "tasks, the form labels. The student's own words are proofread as before. It is left out "
    "found only when a run works on at least three documents."
)

PROMPTS_HEADING = "What the AI is asked"
PROMPTS_TOOLTIP = (
    "The fixed instructions the app sends, one per step. They cannot be edited; your notes "
    "are added to each of them."
)

EXPLANATIONS_SUPPORTED = "explanations ✓"
EXPLANATIONS_SUPPORTED_TOOLTIP = (
    "This model was checked with the explanation format: proofreading with explanations "
    "works with it."
)

# ── Image preparation: the two choices, the page models, the preview caption ──

#: The image preparation choice, by the id stored in the settings.
PREPROCESS_LABELS = {
    "auto": "Automatic (recommended)",
    "none": "Original page",
}
PREPROCESS_TOOLTIP = (
    "How a page is prepared before it is read.\n\n"
    "Automatic — straightens and turns photographed pages, evens out shadows and\n"
    "  corrects a tilt. Clean scans pass unchanged.\n"
    "Original page — sends each page as scanned or photographed. For checking\n"
    "  what the preparation changes."
)
PREPROCESS_HINT = (
    "Straightens and turns photographed pages, evens out shadows and corrects a tilt. "
    "Clean scans pass unchanged."
)
#: What each step of the automatic preparation did, as the preview caption says it.  A step
#: that is not listed (the gray conversion) is not worth a word.
STEP_LABELS = {
    "page": "page cut out",
    "flatten": "page flattened",
    "turn": "page turned",
    "size": "scaled down",
    "light": "shadows evened out",
    "deskew": "tilt corrected",
    "turned by tag": "turned by the photo's rotation tag",
}


def preparation_changed_pages(steps: Sequence[str]) -> bool:
    """Return whether a step that is worth a word acted, so the original differs from the page."""
    return any(name in STEP_LABELS for name in steps)


def prepared_caption(steps: Sequence[str]) -> str:
    """Return the preview caption for the steps that acted on the page.

    "Fixed for reading: page turned, shadows evened out"; "As scanned — no fixes needed" when
    no step that is worth a word acted.  Under "Original page" the only step there can be is the
    rotation tag: "Original page (rotation tag applied)".  The steps' own reasons ("tilt 1.4°")
    go to the debug log only.
    """
    if list(steps) == ["turned by tag"]:
        return "Original page (rotation tag applied)"
    words = [STEP_LABELS[name] for name in steps if name in STEP_LABELS]
    if not words:
        return "As scanned — no fixes needed"
    return f"Fixed for reading: {', '.join(words)}"


#: The step that replaces names before text is sent anywhere: the checkbox in the main window
#: and the line on the AI models page that offers to turn it on must say the same words.
REMOVE_PERSONAL_INFO = "Remove personal info"
#: The line under it while it is on: the languages whose names are hidden.
NAMES_IN_LANGUAGES = "Names in {languages}"

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


def notes_counter(length: int) -> str:
    """Return the character counter under the notes box."""
    return f"{length} / {NOTES_LIMIT}"


def category_label(category: str) -> str:
    """Return the teacher-facing name of a change *category*."""
    return CHANGE_CATEGORY_LABELS.get(category, OTHER_CATEGORY)


def filter_unclear_label(count: int) -> str:
    """Return the *Unclear* filter button label for *count* unclear expressions."""
    return f"{FILTER_UNCLEAR} {count}"


def unclear_badge(count: int) -> str:
    """Return the short badge shown in the results table, e.g. ``"?2"``."""
    return f"?{count}" if count else ""


KEPT_NOTE = "kept"
"""The note beside the proofreading cell of a proofreading kept over other settings."""

KEPT_TOOLTIP = (
    "Made with other settings. Kept because you worked on it \u2014 "
    "right-click \u2192 Re-run Proofreading for a new one."
)
"""What the kept proofreading cell says when the teacher hovers over it."""


def recommendation_caption(is_suggestion: bool) -> str:
    """Return the caption in front of a recommended version."""
    return SUGGESTION_PREFIX if is_suggestion else RECOMMENDED_PREFIX


# ── What is sent for this file ────────────────────────────────────────────────
#
# The title and the "no text yet" line live in `texts/privacy.py`, because the dialog that
# shows them is described in `texts/dialogs.py`.  Import them from there.


# ── How a stored result was sent ──────────────────────────────────────────────

NAMES_AS_WRITTEN = "names as written"
NO_NAMES_FOUND = "no names found to hide"
NAMES_HIDDEN = "{count} names hidden"
ONE_NAME_HIDDEN = "1 name hidden"
OTHER_PRIVACY_SETTINGS = (
    "made with other privacy settings — {action} again to send with the current ones"
)
#: What each step of the proofreading exchange is called where a teacher reads it.
PROMPT_STEP_TITLES: dict[str, str] = {
    "correct": "Correct the text",
    "explain": "Explain the corrections",
    "unclear": "List unclear expressions",
}


def prompt_step_title(step: str) -> str:
    """Return the name of a proofreading step as a teacher knows it."""
    return PROMPT_STEP_TITLES.get(step, step)


def prompts_block(prompts: Sequence[tuple[str, str]], notes: str = "") -> str:
    """Return the read-only block of instructions: one heading per request, then the notes.

    *prompts* are ``(step, system prompt)`` as the exchange sends them; a content-review
    prompt has no step and so gets no heading.  *notes* are the teacher's own words, which
    every proofreading request carries.
    """
    parts = [
        f"{prompt_step_title(step)}\n\n{prompt}" if step else prompt for step, prompt in prompts
    ]
    if notes.strip():
        parts.append(notes.strip())
    return "\n\n".join(parts)


STORED_RESULT = "The stored {stage} was {how}."
#: What the "other privacy settings" line tells the teacher to do, per step.
_AGAIN = {Stage.CORRECTION: "proofread", Stage.EVALUATION: "review"}


def names_note(count: int, *, hiding_on: bool = False) -> str:
    """Return ``3 names hidden``, ``1 name hidden``, ``no names found to hide`` or ``names as written``.

    *hiding_on* tells a text where hiding found nothing from a text sent as written.
    """
    if count <= 0:
        return NO_NAMES_FOUND if hiding_on else NAMES_AS_WRITTEN
    return ONE_NAME_HIDDEN if count == 1 else NAMES_HIDDEN.format(count=count)


def _how_sent(record: SentRecord, location: StageLocation) -> tuple[str, str]:
    """Return ``(verb, how)`` for a stored result: ``("Sent", "to Mistral with 3 names hidden")``.

    The one place that words where a result was made and what it was made from; the tooltip
    and the dialog only put a frame around it.  *verb* is ``Sent`` when the text left this
    computer and ``Made`` when it did not.  *location* is where the result was made
    (:func:`~teachers_teammate.application.data_flow.sent_location`).
    """
    if location.location is DataLocation.THIS_COMPUTER:
        return "Made", f"on this computer ({location.service})"
    if location.location is DataLocation.OTHER_COMPUTER:
        host = f" ({location.host})" if location.host else ""
        place = f"to another computer{host}"
    else:
        place = f"to {location.service}"
    names = names_note(record.names_hidden, hiding_on=record.hiding_on)
    return "Sent", f"{place} with {names}"


def sent_lines(status: SentStatus, stage: str, location: StageLocation) -> tuple[str, ...]:
    """Return the tooltip lines saying how a stored result was sent.

    A result made on this computer needs no line.  *stage* only decides the wording of the
    "other privacy settings" line ("proofread again" / "review again"); *location* is where
    the stored result was made.
    """
    lines: list[str] = []
    if location.location is not DataLocation.THIS_COMPUTER:
        place = location.service
        if location.location is DataLocation.OTHER_COMPUTER:
            place = f"another computer ({location.host})" if location.host else "another computer"
        names = names_note(status.record.names_hidden, hiding_on=status.record.hiding_on)
        lines.append(f"{place}, {names}")
    if status.other_privacy_settings:
        lines.append(OTHER_PRIVACY_SETTINGS.format(action=_AGAIN.get(stage, "run")))
    return tuple(lines)


def stored_result_line(stage: str, status: SentStatus, location: StageLocation) -> str:
    """Return the line saying how a stored result was sent, for the "What is sent" dialog."""
    verb, how = _how_sent(status.record, location)
    return STORED_RESULT.format(stage=stage_title(stage).lower(), how=f"{verb.lower()} {how}")


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
    f"Measures the models chosen on {PAGE_AI_MODELS}. Runs only when you click the button; "
    "the result is kept for the time estimates."
)
SPEED_NOTHING_TO_MEASURE = (
    f"Nothing to measure: no step uses a model on this computer. The steps and their models "
    f"are chosen on {PAGE_AI_MODELS}."
)
SPEED_STOP_TOOLTIP = "Stop the test. Nothing is saved."
SPEED_STOPPING = "Stopping…"
SPEED_TESTING = "Testing… this takes up to a minute and a half per step."
SPEED_TESTING_STEP = (
    "Step {number} of {total}: {step} with {model}… (up to a minute and a half per step)"
)
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
    "The proofreading and review models don't fit in memory together: "
    "each file adds about {load} of loading."
)
SPEED_CLASS_SET = "{files} two-page essays: about {span}"
SPEED_CLASS_SET_TIME = "Time for a class set ({files} two-page essays): about {span}"
SPEED_CLASS_SET_UNKNOWN = "Time for a class set: not measured yet"
SPEED_CLASS_SET_TEST = "Test the speed to see how long a class set takes."
SPEED_PER_PAGE = "about {span} per page"
SPEED_MEASURED_NOTE = "about {span} per page (measured)"
SPEED_EXPECTED_LABEL = "{band} (expected)"
SPEED_NOT_MEASURED = "not measured"
SPEED_EXPECTED_NOT_MEASURED = "{band} (expected) — not measured"
SPEED_MEMORY = "needs ~{gb:.0f} GB"
SPEED_EXPECTED_NOTE = (
    "Expected speed on this computer: {band}. Worked out from the models measured here, "
    "not measured for this one."
)
SPEED_MEASURED_AT = "Measured {date}."
SPEED_MEASURED_AT_POWER = "Measured {date}, {power}."
SPEED_FROM_RUNS = "Based on your last {count} {unit}."
SPEED_FROM_RUNS_ONE = "Based on your last {unit}."
#: What the count in :data:`SPEED_FROM_RUNS` counts: a run that proofread nothing measured only
#: text recognition, which happens once per page.
SPEED_FROM_RUNS_DOCUMENT = "proofread document"
SPEED_FROM_RUNS_PAGE = "recognised page"
SPEED_PLUGGED_IN = "plugged in"
SPEED_ON_BATTERY = "on battery, so slower than usual"

#: Why a step of the speed test could not be turned into a figure: the answer carried no count.
SPEED_NO_TOKEN_COUNTS_PAGE = "the server reported no token counts for the page"
SPEED_NO_TOKEN_COUNTS_PARAGRAPH = "the server reported no token counts for the paragraph"

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


def speed_testing_step(step: str, model: str, number: int, total: int) -> str:
    """Return the line that says which step of the speed test is running now."""
    return SPEED_TESTING_STEP.format(
        step=speed_step_name(step).lower(), model=model, number=number, total=total
    )


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


def speed_unmeasured_label(band: str | None) -> str:
    """Return the per-page cell of a step that runs here but was never measured.

    *band* is what the model is expected to reach here; ``None`` when nothing says, and then
    the cell only says it was not measured.
    """
    if band is None:
        return SPEED_NOT_MEASURED
    return SPEED_EXPECTED_NOT_MEASURED.format(band=speed_band_name(band))


def speed_memory(gb: float) -> str:
    """Return the memory a model needs, e.g. ``"needs ~7 GB"``."""
    return SPEED_MEMORY.format(gb=gb)


def speed_class_set(files: int, low_s: float, high_s: float) -> str:
    """Return the example line for a class set of *files* two-page essays."""
    return SPEED_CLASS_SET.format(files=files, span=format_span(low_s, high_s))


def speed_class_set_time(files: int, low_s: float, high_s: float) -> str:
    """Return the labelled class-set line of the read-only "This computer" box."""
    return SPEED_CLASS_SET_TIME.format(files=files, span=format_span(low_s, high_s))


def speed_measured_at(stamp: str, *, on_battery: bool | None) -> str:
    """Return the date line under the rows, or ``""`` when nothing was measured.

    *stamp* is the stored ``YYYY-MM-DD HH:MM`` stamp; only its date is shown, because the
    figure is a property of the machine, not of the minute it was taken.  The power is named
    only when it was read: a computer without a readable battery says nothing about it.
    """
    if not stamp:
        return ""
    if on_battery is None:
        return SPEED_MEASURED_AT.format(date=stamp[:10])
    power = SPEED_ON_BATTERY if on_battery else SPEED_PLUGGED_IN
    return SPEED_MEASURED_AT_POWER.format(date=stamp[:10], power=power)


def speed_from_runs(count: int, *, pages: bool) -> str:
    """Return the line that says the figures come from real runs, or ``""`` without any.

    *pages* says the count is pages rather than documents: a run that proofread nothing
    measured only text recognition, which happens once per page.
    """
    if count <= 0:
        return ""
    unit = SPEED_FROM_RUNS_PAGE if pages else SPEED_FROM_RUNS_DOCUMENT
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
HIDE_PERSONAL_INFO_BUTTON = "👁"
HIDE_PERSONAL_INFO_BUTTON_WORKING = "👁…"
HIDE_PERSONAL_INFO_BUTTON_TIP = "Hide personal information in the texts shown here"
SHOW_PERSONAL_INFO_BUTTON_TIP = "Show names again in the texts shown here"


# ── The work a teacher did on a document ──────────────────────────────────────


def work_saved_by_newer_version(name: str) -> str:
    """Return why the saved work of *name* is left alone: a newer version of the app wrote it."""
    return f"the work of {name} was saved by a newer version of the app"


# ── The Check tab's side panel ────────────────────────────────────────────────

TAKE_OUT = "Take out"
TAKE_OUT_TOOLTIP = "Put the student's words back and take this correction out of the text"
PUT_BACK = "Put back"
PUT_BACK_TOOLTIP = "Put this correction back into the text"
USE_SUGGESTION = "Use suggestion"
USE_SUGGESTION_TOOLTIP = "Write the suggested wording into the text"
REPLACE_WITH = "Replace with"
REPLACE_TOOLTIP = "Write other words in place of these"
REPLACE_OK = "OK"
TAKE_OUT_ALL = "Take out all"
TAKE_OUT_ALL_TOOLTIP = "Put the student's words back for every one of these corrections"
NEXT = "Next \u203a"
NEXT_TOOLTIP = "The next correction you have not looked at"
ALL_LOOKED_AT = "All looked at"

OPTIONAL_CHANGE = "optional"
AI_NOT_SURE = "AI not sure"
NO_EXPLANATION_MADE = "No explanation given."
TAKEN_OUT = "taken out"
SHOW_NAMES_TO_CHANGE = "Show the names to change the text"
COULD_NOT_MARK = "The corrections could not be marked in this text."
NO_ITEMS = "Nothing to correct in this text."


def take_out_all_label(count: int) -> str:
    """Return the button that takes out every spelling and punctuation change, e.g. ``"Take out all 9"``."""
    return f"{TAKE_OUT_ALL} {count}"


def take_out_all_tooltip(count: int) -> str:
    """Return what the *Take out all* button does, naming how many changes it takes out."""
    noun = "correction" if count == 1 else "corrections"
    return f"{TAKE_OUT_ALL_TOOLTIP}: {count} spelling and punctuation {noun}"


def item_counts(corrections: int, unclear: int, left_out: int = 0) -> str:
    """Return the footer of the list: corrections, unclear passages and passages not proofread."""
    parts: list[str] = []
    if corrections:
        parts.append(
            f"{corrections} correction" if corrections == 1 else f"{corrections} corrections"
        )
    if unclear:
        parts.append(f"{unclear} unclear")
    if left_out:
        parts.append(f"{left_out} not proofread")
    return " · ".join(parts)


def looked_at_progress_text(looked_at: int, countable: int) -> str:
    """Return how far the teacher has read the list, e.g. ``"8 of 12 looked at"``."""
    if not countable:
        return ""
    return f"{looked_at} of {countable} looked at"


# ── The Check tab's header and banners ────────────────────────────────────────

VIEW_READ = "What was read"
VIEW_MARKED = "With corrections"
VIEW_FINAL = "Final text"

READING_FIX_WARNING = (
    "Changing what was read can remove the corrections of this document and your changes to them."
)
READING_FIX_FELL_BACK = (
    "The proofreading is gone: the change was in or next to a correction. Proofread this "
    "document again; a new proofreading starts without your changes."
)
READING_FIX_NOT_CARRIED = (
    "The proofreading is gone: it could not be kept over this change. Proofread this document "
    "again; a new proofreading starts without your changes."
)
EDIT_ANYWAY = "Edit anyway"
SHOW_NAMES = "Show names"
SHOW_SCAN = "Show scan"
PROOFREAD_THIS_DOCUMENT = "Proofread this document"
PROOFREAD_AGAIN = "Proofread again"
SHOW_MARKDOWN_SOURCE = "Show Markdown source"
SOURCE_DIALOG_TITLE = "The stored text of this document"
WAITING_FOR_RUN = "Waiting for this run"
BEING_PROOFREAD = "Being proofread — you can edit it when it is done"


# ── Checked ───────────────────────────────────────────────────────────────────

MARK_AS_CHECKED = "Mark as checked"
CHECKED_BUTTON = "\u2713 Checked"
TEXT_CHECKED_BUTTON = "\u2713 Text checked"
CHECKED_TOOLTIP = (
    "Mark this document as checked. With a proofreading it is the corrections that are "
    "checked; without one, the text that was read."
)
CHECKED_COLUMN = "Checked"
FILE_COLUMN = "File"
DURATION_COLUMN = "Duration"
OPEN_COLUMN = "Open"
#: The step columns of the results list, named like the steps under "What should happen".
RECOGNITION_COLUMN = "Recognition"
PROOFREADING_COLUMN = "Proofreading"
CONTENT_REVIEW_COLUMN = "Content review"
CHECKED_CELL = "Checked"
TEXT_CHECKED_CELL = "Text checked"
SELECT_CHECKED = "Select checked"
SELECT_CHECKED_TOOLTIP = "Select every document you marked as checked, so Export writes those"

#: The header toggle's text, by the ``checked_label`` of the document's work.
_CHECKED_BUTTONS = {"checked": CHECKED_BUTTON, "text": TEXT_CHECKED_BUTTON}

#: The queue cell's text, by the same label; an unchecked document shows nothing.
_CHECKED_CELLS = {"checked": CHECKED_CELL, "text": TEXT_CHECKED_CELL}


def checked_button_label(label: str) -> str:
    """Return the header toggle's text for a document whose work says *label*."""
    return _CHECKED_BUTTONS.get(label, MARK_AS_CHECKED)


def checked_cell_label(label: str) -> str:
    """Return the queue cell's text for a document whose work says *label*."""
    return _CHECKED_CELLS.get(label, "")


def hiding_personal_info_failed(reason: str) -> str:
    """Return why the names in the shown texts could not be hidden."""
    return HIDING_PERSONAL_INFO_FAILED.format(reason=reason)
