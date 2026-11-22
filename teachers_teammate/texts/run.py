"""What a run says about itself: the notes a teacher sees while it works.

The pipeline reports *what* happened (a file was saved, a run is on battery power); the
sentence is here, so the wording is reviewable in one place.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.stages import Stage

#: What the banner and the main window say when a run starts on battery power.
BATTERY_WARNING = "Running on battery: plug in the charger, and keep the lid open."

#: What a row says about a file a stop cut short, by the step it did not get to.
_STOPPED: dict[str, str] = {
    Stage.OCR: "Stopped before the text was read.",
    Stage.CORRECTION: "Stopped before proofreading.",
    Stage.EVALUATION: "Stopped before the review.",
}

#: What a row says about a file the stop reached before any step of it ran.
STOPPED_BY_USER = "Stopped by user."

# ── The run's own controls and notes ──────────────────────────────────────────

STOP = "Stop"
STOP_TOOLTIP = "Finish the document being processed, then stop."
STOP_NOW = "Stop now"
STOP_NOW_TOOLTIP = (
    "Stop at once. The document being processed is left unfinished; what is already done "
    "is kept and reused next time."
)
STOPPING = "Stopping…"
#: What the progress line adds once the run was asked to stop.
STOPPING_SUFFIX = " — stopping after this document"

#: The note the progress bar carries while a run is going, and the tooltip behind it.


def time_left(left: str) -> str:
    """Return the note the progress bar carries: ``"about 40 min left"``."""
    return f"about {left} left"


def time_left_range(low: str, high: str) -> str:
    """Return the tooltip behind that note: the range the figure comes from."""
    return f"Between {low} and {high} left, at the pace of this run."


#: What the line under the progress bar says about keeping the computer awake.
AWAKE_HELD = "Keeping the computer awake while documents are processed. The screen may turn off."
AWAKE_REFUSED = "This computer did not allow staying awake; it may go to sleep during the run."
AWAKE_UNAVAILABLE = (
    "The app cannot keep this computer awake (the system decides); "
    "check its power settings if a long run must not be interrupted."
)


def stopped_message(stage: str) -> str:
    """Return what a row says for a file a stop cut short at *stage*."""
    return _STOPPED.get(stage, STOPPED_BY_USER)


# ── A file the run cannot use ─────────────────────────────────────────────────


def unsupported_input_suffix(suffix: str, supported: str) -> str:
    """Return what a run says about an input file whose type no provider reads."""
    return f"Unsupported input suffix '{suffix}'. Supported: {supported}"


def input_without_units(name: str) -> str:
    """Return what a run says about an input file that yielded no unit at all."""
    return f"Input '{name}' produced no units."


def input_without_content(name: str) -> str:
    """Return what a run says about an input file that had neither an image nor text."""
    return f"Input '{name}' produced no usable image or text content."


#: What a run says when the built-in reader failed on a page it was given.  What the library
#: answered goes to the log: it names a file and a line of OpenCV, which no teacher can act on.
READER_PAGE_FAILED = "The built-in reader could not process this page."


def image_unreadable(path: Path) -> str:
    """Return what a run says about a page image it could not load."""
    return f"Cannot read image: {path}"


def image_not_encoded(path: Path) -> str:
    """Return what a run says about a page image that could not be encoded."""
    return f"Cannot encode image: {path}"


def image_not_written(path: Path, reason: str) -> str:
    """Return what a run says about a page image that could not be written to disk."""
    return f"Cannot write image: {path}: {reason}"


def preview_needs_images(name: str) -> str:
    """Return what a preview says about a file whose pages are not images."""
    return f"Preview is only available for image-based inputs: '{name}'."


def config_file_missing(path: Path) -> str:
    """Return what a run says about a settings file that is not there."""
    return f"Config file not found: {path}"


def storage_root_unresolved(reasons: str) -> str:
    """Return what the app says when it found no folder it may write to, *reasons* one per line."""
    return f"Could not resolve writable storage root:\n{reasons}"


# ── A stage that failed ───────────────────────────────────────────────────────


def input_read_failed(reason: str) -> str:
    """Return what a run says about an input file it could not read from disk."""
    return f"Could not read input file: {reason}"


def preprocessing_failed(reason: str) -> str:
    """Return what a run says about a page it could not prepare."""
    return f"Preprocessing failed: {reason}"


def preview_save_failed(reason: str) -> str:
    """Return what a run says about a preview image it could not store."""
    return f"Could not persist preview image: {reason}"


def cache_save_failed(reason: str) -> str:
    """Return what a run says about the text of a file it read but could not keep."""
    return f"Could not persist OCR cache state: {reason}"


#: What a run says about a page the model answered with nothing at all.
EMPTY_OCR_ANSWER = "Empty response from OCR model."


def ocr_failed(page: int | None, reason: str) -> str:
    """Return what a run says about one page, or the one image, the reader could not read."""
    what = "image" if page is None else f"page {page}"
    return f"OCR failed ({what}): {reason}"


def personal_info_setup_failed(reason: str) -> str:
    """Return what a run says about the anonymizer it could not build."""
    return f"Cannot initialise anonymizer: {reason}"


def personal_info_removal_failed(reason: str) -> str:
    """Return what a run says about a file whose personal data could not be hidden."""
    return f"Personal info removal failed: {reason}"


def personal_info_restore_failed(reason: str) -> str:
    """Return what a run says about a file whose personal data could not be put back."""
    return f"PII restore failed: {reason}"


def personal_info_pattern_invalid(tag: str, reason: BaseException) -> str:
    """Return what a run says about a pattern of the personal-info list that does not compile."""
    return f"Personal info pattern '{tag}' is not valid: {reason}"


def output_not_saved(reason: BaseException) -> str:
    """Return what a run says about output files it refused to write."""
    return f"Output files not saved: {reason}"


def docx_failed(reason: BaseException) -> str:
    """Return what a run says about a Word document it could not create."""
    return f"DOCX creation failed: {reason}"


def text_files_failed(reason: BaseException, saved: list[str]) -> str:
    """Return what a run says about text files it could not write, naming what was saved."""
    already = f" (already saved: {', '.join(saved)})" if saved else ""
    return f"Text file creation failed: {reason}{already}"


def personal_info_not_removed(reason: BaseException) -> str:
    """Return what a run says when personal information is not removed, and nothing is sent."""
    return f"{reason}; personal info is not removed in this run."


# ── Where the output files go ─────────────────────────────────────────────────


def output_overwrites_input(name: str) -> str:
    """Return what a run says about the text file that would replace its own input document."""
    return f"{name} is the input document and would be overwritten"


def output_read_back_as_input(shown: str) -> str:
    """Return what a run says about an output file the next run would read as an input."""
    return (
        f"{shown} would be read as an input document by the next run; "
        "choose an output folder outside the input folder."
    )


def output_replaces_another(shown: str, owner: str) -> str:
    """Return what a run says about two documents that would write the same output file."""
    return f"{shown} would replace the output of {owner}; rename one of the two documents."


def fatal_error(reason: BaseException) -> str:
    """Return the log line for a run that ended on an error nothing handled."""
    return f"FATAL ERROR: {reason}\n"


# ── How a finished run is announced ───────────────────────────────────────────

RUN_DONE = "Done \u2713"
RUN_FAILED = "Finished with errors"


def run_failed_count(failed: int) -> str:
    """Return the announcement of a run with *failed* files that failed."""
    return f"{RUN_FAILED} — {failed} failed"


def run_stopped(
    done: int, total: int, *, skipped: int = 0, unfinished: int = 0, not_started: int = 0
) -> str:
    """Return the announcement of a stopped run: how far it got, and what was left."""
    parts = [f"{done} of {total} done"]
    if skipped:
        parts.append(f"{skipped} skipped")
    if unfinished:
        parts.append(f"{unfinished} unfinished")
    if not_started:
        parts.append(f"{not_started} not started")
    return "Stopped — " + ", ".join(parts)


def run_done_tray(done: int, skipped: int = 0) -> str:
    """Return the notification of a run that finished: *done* files, and any skipped."""
    text = f"Done — {done} file(s) processed"
    return f"{text}, {skipped} skipped" if skipped else text


# ── What an input file says about its own text layer, and a cached result ─────

PDF_NO_PAGES = "PDF has no pages"
NO_EMBEDDED_TEXT = "no embedded text"
RESULTS_CACHED = "results cached (no output files)"


def text_layer_page_note(page: int, reason: str) -> str:
    """Return why the text layer of a PDF is not used: the *page* that ruled it out."""
    return f"page {page}: {reason or NO_EMBEDDED_TEXT}"
