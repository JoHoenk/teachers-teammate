"""Comparing writing models on one text: what the window says and what a failure says.

One text, several models: the window shows what each of them changed, where they agree and what
each explained, so a teacher can see which of them suits their class.  It does not name a winner —
one text answered with temperatures above 0 shows differences, not which model is better.
"""

from __future__ import annotations

from .review import format_span

#: The window's title, as the Tools entry and the window carry it.
TITLE = "Compare proofreading models"

#: What a model's row says when it could not answer this text.
MODEL_NOT_COMPARED = "Could not compare {model}: {reason}"

#: Why no model can answer: there is no proofreading to compare.
PROOFREADING_NOT_COMPARED = "Proofreading is switched off in the settings."


def marks_not_replaced(*, locked: bool) -> str:
    """Return why the verdicts of a text are left as they are when the teacher marks one more."""
    why = "cannot be read now" if locked else "were written by a newer version of the app"
    return f"The marks of this text {why}, so they are left as they are."


def model_not_compared(model: str, reason: str) -> str:
    """Return what a comparison says about *model*, which could not answer *reason*'s text."""
    return MODEL_NOT_COMPARED.format(model=model, reason=reason)


# ── The window ───────────────────────────────────────────────────────────────

#: The Check tab's button beside *Proofread again*, and its tip.
COMPARE_MODELS = "Compare models\\u2026"
COMPARE_MODELS_TIP = (
    "Ask several writing models about this document's text and see what each of them changed."
)

#: The label above the list of the documents that can be compared.
DOCUMENT_LABEL = "Document"

#: What the header says when the chosen document has no text yet.
NOT_READ = "This document has not been read yet, so there is no text to compare."

#: What the header says when the store holds no answer yet.
NOTHING_STORED = "No model has answered this text yet."

#: The document's own request beside the models: ``"1 request"`` or ``"3 requests"``.
REQUESTS = "1 request"
REQUESTS_MANY = "{count} requests"


def summary(name: str, words: int, language: str, requests: int) -> str:
    """Return the header's one line: the document, its size, its language and its cost."""
    cost = REQUESTS if requests <= 1 else REQUESTS_MANY.format(count=requests)
    return f"{name} \u00b7 {words} words \u00b7 {language} \u00b7 {cost}"


MODELS = "Models"
RUN_SELECTED = "Run selected"
RUN_AGAIN = "Run again"
STOP = "Stop"

#: What the window's progress line says when nothing is ticked.
TICK_MODELS = "Tick at least one model to compare."

TAB_CHANGES = "Changes"
TAB_SIDE_BY_SIDE = "Side by side"
TAB_OVERVIEW = "Overview"

STUDENT_WROTE = "Student wrote"
AGREE = "Agree"
UNCHANGED = "\u2014"
DELETED = "(deleted)"
NO_PROBLEMS = "none"

#: Between the wordings one model wrote at one place.
OPTION_SEPARATOR = " / "

#: What the *Changes* tab says about a row before one is chosen, and about a model that
#: explained nothing.
WHY_TOOLTIP = "Choose a row to read what the models said about it."
NO_EXPLANATION = "{model}: no explanation"


def option_wording(replacement: str) -> str:
    """Return one option's wording, as the line the teacher judges it on shows it."""
    return f"\u2192 {replacement or DELETED}"


def once_wording(replacement: str) -> str:
    """Return one option's wording, saying so when only one of a model's runs wrote it."""
    return f"{replacement} ({ONLY_ONCE.lower()})"


def cell_wording(replacement: str, *, once: bool) -> str:
    """Return what a model wrote at one place, as a cell of the *Changes* tab shows it."""
    wording = replacement or DELETED
    return once_wording(wording) if once else wording


def explained_by(model: str, explanation: str, category: str) -> str:
    """Return one line about a row: what *model* said there, and what kind of change it is."""
    if not explanation:
        return NO_EXPLANATION.format(model=model)
    return f"{model} ({category}): {explanation}"


#: The filter over the change sites, by the id of what it keeps (``Sites`` in the window).
FILTERS = {"all": "All", "disagree": "Models disagree", "one": "Only one model"}

#: What a row's state says.
NOT_RUN = "not run"
RUNNING = "running\u2026"
STOPPED = "stopped"
ANSWERED = "answered"
YOUR_PROOFREADING = "your proofreading"


def asking(model: str) -> str:
    """Return the progress line while *model* is being asked: ``"qwen3:8b: running…"``."""
    return f"{model}: {RUNNING}"


def done_in(seconds: float) -> str:
    """Return what a finished row says: how long the answer took, loading the model included."""
    return f"done in {format_span(seconds, seconds)}"


def estimate(low_s: float, high_s: float) -> str:
    """Return the time a row estimates before the model is asked: ``"~1-2 min"``."""
    return f"~{format_span(low_s, high_s)}"


#: What the row of an online model says about it; a local one says its catalog rating.
ONLINE = "online"

#: How long an answer took, and how long this document's text says that is per 100 words.
TIME = "Time"
PER_HUNDRED_WORDS = "Per 100 words"

#: The score of an answer against the teacher's marks, shown once every change is marked.
F_BETA = "Score (F0.5)"

#: What the teacher says about one change: it is right, or it is not.
MARK_RIGHT = "\u2713"
MARK_WRONG = "\u2717"
MARK_RIGHT_TIP = "This change is right: the student was wrong here."
MARK_WRONG_TIP = "This change is wrong: the student's own words are correct."
CLEAR_MARK_TIP = "Take the verdict back, so the change counts for nothing again."

#: What a model's changes come to when it answered twice: not chance, or unsure.
IN_EVERY_RUN = "In both runs"
ONLY_ONCE = "Only once"

#: The Overview's row of what went wrong with an answer.
PROBLEMS_ROW = "Problems"

#: What the teacher's verdicts come to, per version, in the Overview.
RIGHT = "Right"
WRONG = "Wrong"
UNMARKED = "Unmarked"
MISSED = "Missed right changes"

#: What the Overview lists per version, in its order.
OVERVIEW_ROWS = (
    TIME,
    PER_HUNDRED_WORDS,
    "Changes",
    "Shared with another model",
    "Only this model",
    IN_EVERY_RUN,
    ONLY_ONCE,
    "Explained",
    "Unclear",
    PROBLEMS_ROW,
    RIGHT,
    WRONG,
    UNMARKED,
    MISSED,
    F_BETA,
)

#: What the Problems column says about one answer, by its id.
PROBLEMS = {
    "failed": "failed",
    "fell_back": "part missing",
    "left_out": "text put back",
    "text_lost": "text lost",
    "text_added": "text added",
    "marks_added": "marks added",
}

#: One text is not a measurement: the window says so once, above the figures.
NO_WINNER = (
    "One text shows what these models do differently, not which of them is better. "
    "Ask the same text twice and a model may answer differently."
)

#: What decides instead: the teacher's own verdicts on the changes they read.
MARKS_DECIDE = (
    "Mark a change right or wrong in the *Changes* tab: the score is read once every change of "
    "that model is marked."
)


def overview_note() -> str:
    """Return what the *Overview* says above the figures."""
    return f"{NO_WINNER} {MARKS_DECIDE}"


def use_model(model: str) -> str:
    """Return the footer's button: make *model* the model that proofreads from now on."""
    return f"Use {model} for proofreading"


def own_version(model: str) -> str:
    """Return the column name of the document's own proofreading, made by *model*."""
    return f"{model} ({YOUR_PROOFREADING})"


#: The column of the document's own proofreading when its model is not one of the list.
OWN_VERSION = "Your proofreading"


def explained(explained: int, needed: int) -> str:
    """Return how many of the changes a model explained, against how many needed one."""
    return f"{explained} of {needed}"


def compare_done(name: str) -> str:
    """Return the status line once a comparison has asked every model it was going to ask."""
    return f"Compared the proofreading models on {name}."


def compare_stopped(name: str) -> str:
    """Return the status line after the teacher stopped a comparison before it was through."""
    return f"Stopped comparing the proofreading models on {name}."


def now_proofreading(model: str) -> str:
    """Return the status line after the teacher kept *model* for proofreading."""
    return f"{model} proofreads your documents from now on."
