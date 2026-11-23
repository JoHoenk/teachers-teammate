"""Every message box, question and file picker a teacher sees.

Each entry is a named constant or a function of the values that vary, so a call site never
builds a sentence.  ``gui/_dialogs.py`` shows them; nothing here imports Qt.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.correction_prompts import NOTES_LIMIT
from ..domain.data_location import OCR_ENGINE_NAMES, RETIRED_ENGINE_NAMES
from ..domain.local_models import READER_SIZE_LABEL
from ..domain.output_files import FolderProblem
from ..domain.settings_notice import SettingNotice, SettingNoticeKind
from ._types import (
    Choice,
    ChoiceRole,
    FilePrompt,
    Notice,
    NoticeKind,
    Question,
    QuestionKind,
    cancel,
    yes_no,
)
from .app import NAME
from .settings import WHERE_ADDONS, WHERE_PRIVACY, WHERE_READING_MODELS, WHERE_RESULTS

#: The name filter of the main window's input picker.
SUPPORTED_FILES_FILTER = (
    "Supported files (*.pdf *.PDF *.png *.PNG *.jpg *.JPG *.jpeg *.JPEG "
    "*.tiff *.TIFF *.tif *.TIF *.txt *.TXT);;All files (*)"
)

#: What a result says when the reason a step failed is not known.
UNKNOWN_ERROR = "unknown error"

#: The label above the message of a step that failed, inside a dialog.
ERROR_HEAD = "Error:"

#: Why a window stays open while a package is being installed.
INSTALLATION_RUNNING = (
    "An installation is still running. Please wait until it has finished — the new package "
    "must be set up before the app can use it."
)

# ── Notices: what the app tells a teacher ─────────────────────────────────────


def text_recognition_changed_online(old: str, engine: str, available: Sequence[str]) -> Notice:
    """Tell the teacher that a saved online engine was replaced by one on this computer."""
    return Notice(
        "Text recognition changed",
        f"Your saved settings used “{old}” for text recognition. Reading pages "
        "online is no longer offered, because a page cannot be anonymised before it "
        f"is read, so the app now uses {engine} on your computer instead "
        f"({' and '.join(available)} are available).\n\n"
        "Proofreading and content review are not affected.",
    )


def text_recognition_changed_retired(name: str) -> Notice:
    """Tell the teacher that a saved engine was replaced by the built-in reader."""
    return Notice(
        "Text recognition changed",
        f"Your saved settings used {name} for text recognition. It was replaced by "
        "the built-in reader, which also reads pages on this computer without a "
        f"server. Download it once under {WHERE_READING_MODELS} "
        f"({READER_SIZE_LABEL}).",
    )


def setting_notice(notice: SettingNotice) -> Notice:
    """Tell the teacher what the app changed about a value of their settings file."""
    if notice.kind is SettingNoticeKind.ENGINE_REPLACED:
        if notice.online:
            return text_recognition_changed_online(
                notice.detail, notice.replacement, list(OCR_ENGINE_NAMES.values())
            )
        name = RETIRED_ENGINE_NAMES.get(notice.detail.strip().lower(), notice.detail)
        return text_recognition_changed_retired(name)
    if notice.kind is SettingNoticeKind.NOTES_CUT:
        return Notice(
            "Notes shortened",
            f"The notes for the AI are longer than {NOTES_LIMIT} characters and were cut.",
            NoticeKind.WARNING,
        )
    return Notice(
        "Setting ignored",
        f"The saved setting “{notice.key}” has a value the app cannot use ({notice.detail}), "
        "so it uses its default instead.",
        NoticeKind.WARNING,
    )


def folder_problem_text(problem: FolderProblem, *, input_text: str = "") -> str:
    """Return why a run cannot start with the folders it was given, in a teacher's words."""
    if problem is FolderProblem.NO_INPUT:
        return "Input folder is required."
    if problem is FolderProblem.NO_OUTPUT:
        return "Output folder is required."
    return f"Input folder does not exist: {input_text}"


def cli_folder_problem_text(problem: FolderProblem, *, input_text: str = "") -> str:
    """Return why a run cannot start with the folders it was given, with the option to fix it."""
    if problem is FolderProblem.NO_INPUT:
        return "No input folder specified. Use -i/--input or set 'input' in the config file."
    if problem is FolderProblem.NO_OUTPUT:
        return (
            "No output folder specified. Use -o/--output or set 'output' in the config file. "
            "(Output can be omitted only when no output files are written.)"
        )
    return folder_problem_text(problem, input_text=input_text)


def cli_warning(text: str) -> str:
    """Return *text* as a warning line of the command line."""
    return f"WARNING: {text}"


def cli_error(text: str) -> str:
    """Return *text* as an error line of the command line."""
    return f"ERROR: {text}"


def save_failed(reason: str) -> Notice:
    """Tell the teacher that the settings file could not be written."""
    return Notice("Save failed", reason, NoticeKind.WARNING)


def settings_file_broken(name: str, backup: str) -> Notice:
    """Tell the teacher that a settings file could not be read, and where the copy is."""
    return Notice(
        "Settings file not readable",
        f"{name} could not be read, so the app uses default settings. Your settings are kept in "
        f"{backup}.",
        NoticeKind.WARNING,
    )


def settings_file_not_replaced(name: str) -> Notice:
    """Tell the teacher that a settings file that could not be read is not written over."""
    return Notice(
        "Settings file kept",
        f"{name} could not be read and no copy of it could be made, so it was left as it is. "
        "Free the file, or remove it, and save again.",
        NoticeKind.WARNING,
    )


def busy_with_run() -> Notice:
    """Tell the teacher that a run or a requirement check is already going."""
    return Notice(
        "Please wait",
        "A run or requirement check is still in progress. Try again when it has finished.",
    )


def busy_with_text_export() -> Notice:
    """Tell the teacher that a text export is already going."""
    return Notice("Please wait", "A text export is still running.")


def configuration_error(reason: str) -> Notice:
    """Tell the teacher that the choices in the main window cannot be used as they are."""
    return Notice("Configuration error", reason, NoticeKind.WARNING)


def output_folder_refused(refusal: str) -> Notice:
    """Tell the teacher why the output folder cannot be used, and where to change it."""
    return Notice(
        "Choose another output folder",
        f"{refusal}\n\nChange it under {WHERE_RESULTS}.",
        NoticeKind.WARNING,
    )


def no_files_found(folder: str) -> Notice:
    """Tell the teacher that the input folder holds nothing the app can read."""
    return Notice(
        "No files found",
        "No supported files found (PDF, images, or text files) in:\n"
        f"{folder}\n\n"
        "TIP: Plain text files (.txt) are read directly — no text extraction needed.",
    )


def no_files_selected() -> Notice:
    """Tell the teacher that no queue row is selected."""
    return Notice("No files selected", "Select one or more rows in the queue, then press Run.")


def selection_mismatch() -> Notice:
    """Tell the teacher that the selected rows no longer match the input files."""
    return Notice(
        "Selection mismatch",
        "The selected rows are no longer in the queue.",
    )


#: Why a file's cache stayed as it was when the teacher's own work could not be dropped.
CHANGES_NOT_REMOVED = "your changes could not be removed"


def cache_invalidation_failed(failures: Sequence[tuple[str, str]]) -> Notice:
    """Tell the teacher which files could not have their cache reset, and why."""
    lines = [f"{name}: {reason or CHANGES_NOT_REMOVED}" for name, reason in failures]
    return Notice(
        "Cache invalidation failed",
        "Could not reset cache for one or more files:\n\n" + "\n".join(lines),
        NoticeKind.WARNING,
    )


def _export_failed(title: str, errors: Sequence[str]) -> Notice:
    """Return the notice that lists the documents an export could not write."""
    return Notice(
        title,
        "Could not export the following file(s):\n\n" + "\n".join(errors),
        NoticeKind.WARNING,
    )


def export_no_text(name: str) -> str:
    """Return the line that says a document has no text to export yet."""
    return f"{name}: No OCR text — run the pipeline first."


def export_would_overwrite_input(name: str, target: str) -> str:
    """Return the line that says an export would replace an input document."""
    return f"{name}: {target} is an input document and would be overwritten; choose another name or folder."


def word_export_failed(errors: Sequence[str]) -> Notice:
    """Tell the teacher which documents could not be written as a Word file."""
    return _export_failed("Export Word Document", errors)


def text_export_failed(errors: Sequence[str]) -> Notice:
    """Tell the teacher which documents could not be written as a text file."""
    return _export_failed("Export Text", errors)


def text_export_error(message: str, skipped: Sequence[str] = ()) -> Notice:
    """Tell the teacher why the text export stopped, and which files it left out before."""
    if skipped:
        message += "\n\nAlso skipped:\n" + "\n".join(skipped)
    return Notice("Export Text", message, NoticeKind.WARNING)


def processing_error(name: str, message: str) -> Notice:
    """Tell the teacher that one document could not be processed."""
    return Notice("Processing error", f"{name}:\n\n{message}", NoticeKind.WARNING)


def model_download_failed(message: str) -> Notice:
    """Tell the teacher that a model could not be downloaded."""
    return Notice("Model download failed", message, NoticeKind.WARNING)


def check_without_state(name: str) -> Notice:
    """Tell the teacher that nothing usable is stored for a document the Check tab was asked for."""
    return Notice(
        "Check",
        f"Nothing usable is stored for “{name}”.\n"
        "The file may have changed since it was processed. Process it again to see it here.",
        NoticeKind.WARNING,
    )


def checked_not_saved() -> Notice:
    """Tell the teacher that the *Checked* mark could not be stored."""
    return Notice(
        "Not saved",
        "Your change could not be saved. The document is as it was before.",
        NoticeKind.WARNING,
    )


def work_not_saved(name: str, *, too_new: bool) -> Notice:
    """Tell the teacher that what they did to a document could not be stored.

    *too_new* says the stored work was written by a newer version of the app, which this one
    leaves alone rather than write over what it does not understand.
    """
    if too_new:
        return Notice(
            "Not saved",
            f"Your work on {name} was saved by a newer version of the app, so this version does "
            "not change it. Your change was not saved; update the app to go on working on it.",
            NoticeKind.WARNING,
        )
    return Notice(
        "Not saved",
        f"Your change to {name} could not be saved. Check that the folder for your work can be "
        "written to; the text stays in the window until you close the document.",
        NoticeKind.WARNING,
    )


def could_not_check_work() -> Notice:
    """Tell the teacher that nothing was re-run because what they worked on is not known."""
    return Notice(
        "Nothing was re-run",
        "The app could not find out which of these documents you worked on, so it did not "
        "remove anything. Try again.",
        NoticeKind.WARNING,
    )


def preprocessing_preview_failed(reason: str) -> Notice:
    """Tell the teacher that the image-preparation preview could not be made."""
    return Notice("Preview failed", reason, NoticeKind.WARNING)


def copy_failed(reason: str) -> Notice:
    """Tell the teacher that the text could not be copied."""
    return Notice("Copy", reason, NoticeKind.WARNING)


def privacy_addon_installed() -> Notice:
    """Tell the teacher that the Privacy add-on arrived and still needs a model."""
    return Notice(
        "Privacy Addon Installed",
        "The Privacy add-on has been installed.\n\n"
        f"Download a name detection model under {WHERE_ADDONS}\n"
        "before using personal information removal.",
    )


def spacy_not_installed() -> Notice:
    """Tell the teacher that a name detection model needs spaCy first."""
    return Notice(
        "spaCy not installed",
        "spaCy must be installed before you can download a model.\n\n"
        f"Install it under {WHERE_ADDONS} (Name detection) first.",
    )


def stage_warning(message: str) -> Notice:
    """Tell the teacher what a step warned about for one document."""
    return Notice("Stage Warning", message, NoticeKind.WARNING)


def stage_error(message: str) -> Notice:
    """Tell the teacher what a step failed on for one document."""
    return Notice("Stage Error", message, NoticeKind.WARNING)


def cannot_close(reason: str) -> Notice:
    """Tell the teacher why the window stays open."""
    return Notice("Please wait", reason)


def installation_running() -> Notice:
    """Tell the teacher that the window cannot close while a package is being installed."""
    return cannot_close(INSTALLATION_RUNNING)


def key_not_in_keychain(services: str) -> Notice:
    """Tell the teacher that a key had to be kept in the settings file instead."""
    return Notice(
        "Key not saved in the keychain",
        f"The system keychain did not accept the key for {services} (it may be locked). It was "
        "kept in this app's settings file instead, in plain text.",
        NoticeKind.WARNING,
    )


def no_document() -> Notice:
    """Tell the teacher that the Benchmark window has no document yet."""
    return Notice("No document", "Choose a document first.")


# ── Questions: what the app asks a teacher ────────────────────────────────────


def replace_existing_files(count: int, shown: str) -> Question:
    """Ask before a folder export replaces files that are already there."""
    return Question(
        "Replace existing files?",
        f"{count} file(s) already exist and will be replaced:\n\n{shown}",
        yes_no(),
    )


def save_into_input_folder() -> Question:
    """Ask before text files are written where the next run reads them."""
    return Question(
        "Save into the input folder?",
        "Text files in the input folder are read as new documents the next time you run.\n\n"
        "Save here anyway?",
        yes_no(),
    )


def not_enough_memory(warning: str) -> Question:
    """Ask before a run whose models do not fit the memory that is free now."""
    return Question(
        "Not enough free memory",
        warning,
        (Choice("Run anyway"), cancel()),
        kind=QuestionKind.WARNING,
    )


def documents_leave_computer(
    level: str, steps: Sequence[tuple[str, str]], advice: Sequence[str]
) -> Question:
    """Ask before a run that sends content away from this computer.

    *level* is the sentence for the privacy level, *steps* the ``(title, badge)`` of every step
    that sends something away, and *advice* the tips that go with them.
    """
    lines = "\n".join(f"  • {title} — {badge}" for title, badge in steps)
    tips = "".join(f"\n\n{tip}" for tip in advice)
    return Question(
        "Your documents will leave this computer",
        f"{level}\n\nThese steps send content away from this computer:\n\n{lines}{tips}\n\n"
        "Make sure your school's data protection rules allow this.",
        (Choice("Continue"), cancel()),
        kind=QuestionKind.WARNING,
        checkbox=f"Don't ask again for these settings (change under {WHERE_PRIVACY})",
    )


#: How many document names a question lists before it says how many more there are.
_LISTED_NAMES = 10

#: The answers of :func:`worked_on_before_run`, :func:`proofread_again` and :func:`read_again`.
KEEP_MY_CHANGES = "keep"
PROOFREAD_AGAIN = "proofread-again"
READ_AGAIN = "read-again"
CONTINUE = "continue"


def _bullets(names: Sequence[str]) -> str:
    """Return *names* as a bullet list, at most ten of them, then how many more there are."""
    shown = list(names[:_LISTED_NAMES])
    rest = len(names) - len(shown)
    lines = [f"  \u2022 {name}" for name in shown]
    if rest:
        lines.append(f"  \u2022 and {rest} more")
    return "\n".join(lines)


def worked_on_before_run(unanswered: Sequence[str], unbound: Sequence[str]) -> Question:
    """Ask what to do with corrections the teacher worked on before a run replaces them.

    *unanswered* are the documents whose proofreading is stale with these settings and whose
    corrections the teacher changed or checked; *unbound* are the ones whose corrections belong
    to no proofreading stored here, which a new one cannot contain.
    """
    parts: list[str] = []
    if unanswered:
        parts.append(
            "These documents were proofread with other settings:\n\n"
            f"{_bullets(unanswered)}\n\n"
            "Proofreading them again makes new corrections and removes your changes to them."
        )
    if unbound:
        parts.append(
            "Your changes to the corrections of these documents belong to a proofreading that "
            f"is not stored here; a new proofreading will not contain them:\n\n{_bullets(unbound)}"
        )
    if unanswered:
        choices = (
            Choice("Keep my changes", default=True, id=KEEP_MY_CHANGES),
            Choice("Proofread again", role=ChoiceRole.DESTRUCTIVE, id=PROOFREAD_AGAIN),
            cancel(default=False),
        )
    else:
        choices = (Choice("Continue", default=True, id=CONTINUE), cancel(default=False))
    return Question(
        "You worked on some of these corrections",
        "\n\n".join(parts),
        choices,
        kind=QuestionKind.WARNING,
    )


def proofread_again(names: Sequence[str]) -> Question:
    """Ask before an explicit re-run of the proofreading drops the teacher's corrections."""
    return Question(
        "Proofread again?",
        "You changed or checked the corrections of:\n\n"
        f"{_bullets(names)}\n\n"
        "A new proofreading makes new corrections and removes those changes.",
        (
            Choice("Proofread again", role=ChoiceRole.DESTRUCTIVE, id=PROOFREAD_AGAIN),
            cancel(),
        ),
        kind=QuestionKind.WARNING,
    )


def read_again(names: Sequence[str]) -> Question:
    """Ask before an explicit re-read replaces the teacher's transcription."""
    return Question(
        "Read again?",
        "Reading these documents again replaces what you did to them: your corrected "
        "transcription, if you made one, and your changes to the corrections. The "
        f"proofreading made from the old reading is removed:\n\n{_bullets(names)}",
        (Choice("Read again", role=ChoiceRole.DESTRUCTIVE, id=READ_AGAIN), cancel()),
        kind=QuestionKind.WARNING,
    )


def delete_stored_results(*, include_work: bool) -> Question:
    """Ask before the stored results of earlier runs are deleted.

    It names what stays, because "delete the stored results" sounds like it takes the Word
    files and the teacher's own work with it.  *include_work* adds what that choice takes.
    """
    body = (
        "The app will forget what it recognized and proofread for every document, so the next "
        "run reads and proofreads the pages again, which an online service charges for again.\n\n"
        "Your Word and text files in the output folder stay, and so do the documents "
        "themselves."
    )
    if include_work:
        body += (
            "\n\nYour own corrections, marks and edited texts are deleted too. They cannot be "
            "brought back."
        )
    return Question(
        "Delete stored results",
        body,
        (
            Choice("Delete", role=ChoiceRole.DESTRUCTIVE),
            Choice("Keep", role=ChoiceRole.REJECT, default=True),
        ),
        kind=QuestionKind.WARNING,
    )


def close_during_run() -> Question:
    """Ask whether to stop the run and close; the default answer keeps the run going."""
    return Question(
        "Documents are still being processed",
        f"Stop now and close {NAME}?\n\n"
        "Finished documents are kept. The document being processed is left unfinished; what is "
        "already done is reused next time.",
        (
            Choice("Stop and close", role=ChoiceRole.DESTRUCTIVE),
            Choice("Keep running", role=ChoiceRole.REJECT, default=True),
        ),
    )


#: The two answers of :func:`restore_settings` that do something.
RESTORE_PAGE = "page"
RESTORE_EVERYTHING = "everything"


def restore_settings(*, applies_immediately: bool) -> Question:
    """Ask which settings should go back to their built-in values."""
    return Question(
        "Restore defaults",
        "Which settings should go back to their built-in values?",
        (
            Choice("This page", id=RESTORE_PAGE, enabled=not applies_immediately),
            Choice("All settings", role=ChoiceRole.DESTRUCTIVE, id=RESTORE_EVERYTHING),
            cancel(),
        ),
        informative=(
            "Add-ons has no settings to restore: downloads and installs are not undone."
            if applies_immediately
            else "The values on this page change now and are saved when you press OK."
        ),
    )


def restore_all_settings() -> Question:
    """Ask once more before every setting is reset."""
    return Question(
        "Restore defaults",
        "This resets every setting, including the choices in the main window and the answers "
        "you asked not to be asked about again. "
        "Downloaded models and saved API keys are kept.",
        (Choice("Reset everything", role=ChoiceRole.DESTRUCTIVE), cancel()),
        kind=QuestionKind.WARNING,
    )


def clear_all_runs(name: str) -> Question:
    """Ask before the Benchmark window deletes every stored run of a document."""
    return Question("Clear all runs", f"Delete all stored runs for {name}?", yes_no())


def benchmark_still_running() -> Question:
    """Ask before the Benchmark window closes while a run is going."""
    return Question(
        "Benchmark still running",
        "A benchmark run is still going. Closing this window stops it.\n\n"
        "Stop the run and close the window?",
        yes_no(),
    )


def search_network() -> Question:
    """Ask before the app asks every computer in the network whether it runs Ollama."""
    return Question(
        "Search the network?",
        "The app will ask every computer in your network range (up to 254 addresses per "
        "network connection) whether it runs Ollama. No documents or settings are sent.\n\n"
        "Some school networks notice such a search. Only continue if your IT department allows "
        "it.",
        yes_no(),
    )


def model_does_not_fit(messages: Sequence[str]) -> Question:
    """Ask before a model that is too large for this computer is downloaded anyway."""
    return Question(
        "Model does not fit",
        "\n\n".join(messages) + "\n\nDownload anyway?",
        (Choice("Download anyway"), cancel()),
        kind=QuestionKind.WARNING,
    )


# ── File prompts: where a teacher picks a file or a folder ────────────────────


def select_output_folder(start: str = "") -> FilePrompt:
    """The picker for the folder the results are written to."""
    return FilePrompt("Choose the results folder", start=start)


def select_input_folder(start: str = "") -> FilePrompt:
    """The picker for the folder the documents are read from."""
    return FilePrompt("Choose the documents folder", start=start)


def select_input_files(start: str = "") -> FilePrompt:
    """The picker for the documents themselves, instead of a whole folder."""
    return FilePrompt("Choose documents", name_filter=SUPPORTED_FILES_FILTER, start=start)


def save_export(label: str, name_filter: str, suggested: str = "") -> FilePrompt:
    """The Save dialog for one exported document.

    *label* is the export kind's name, *suggested* the path the dialog proposes.
    """
    return FilePrompt(f"Save {label.rstrip('…')}", name_filter=name_filter, start=suggested)


def save_export_to_folder(label: str, start: str = "") -> FilePrompt:
    """The folder picker for exporting several documents at once, opened at *start*."""
    return FilePrompt(f"Save {label.rstrip('…')}s to folder", start=start)


def select_image_or_pdf(supported_glob: str) -> FilePrompt:
    """The picker for a page image or a PDF, in the image-preparation preview."""
    return FilePrompt("Select image or PDF", name_filter=f"Images & PDFs ({supported_glob})")


def choose_document(pattern: str) -> FilePrompt:
    """The picker for the document the Benchmark window runs."""
    return FilePrompt("Choose document", name_filter=f"Supported files ({pattern})")


def answerer_gave_no_answer(type_name: str) -> str:
    """The error for an answerer that answered a question with something but an ``Answer``."""
    return f"An answerer must return an Answer or None for a question, not {type_name}."


def answerer_gave_foreign_button(button: str, title: str) -> str:
    """The error for an answerer that clicked a *button* the question *title* does not have."""
    return f"{button} is not a button of the question {title!r}."
