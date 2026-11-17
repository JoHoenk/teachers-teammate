"""Every message box, question and file picker a teacher sees.

Each entry is a named constant or a function of the values that vary, so a call site never
builds a sentence.  ``gui/_dialogs.py`` shows them; nothing here imports Qt.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.local_models import READER_SIZE_LABEL
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
from .settings import WHERE_ADDONS, WHERE_PRIVACY, WHERE_READING_MODELS, WHERE_RESULTS

#: The name filter of the main window's input picker.
SUPPORTED_FILES_FILTER = (
    "Supported files (*.pdf *.PDF *.png *.PNG *.jpg *.JPG *.jpeg *.JPEG "
    "*.tiff *.TIFF *.tif *.TIF *.txt *.TXT);;All files (*)"
)

#: What a result says when the reason a step failed is not known.
UNKNOWN_ERROR = "unknown error"

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


def save_failed(reason: str) -> Notice:
    """Tell the teacher that the settings file could not be written."""
    return Notice("Save failed", reason, NoticeKind.WARNING)


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


def nothing_to_rerun() -> Notice:
    """Tell the teacher that the chosen stage has no file left to run on."""
    return Notice("No files found", "No files are available to re-run.")


def selection_mismatch() -> Notice:
    """Tell the teacher that the selected rows no longer match the input files."""
    return Notice(
        "Selection mismatch",
        "Could not map selected queue rows to current input files.",
    )


def cache_invalidation_failed(failures: Sequence[str]) -> Notice:
    """Tell the teacher which files could not have their cache reset."""
    return Notice(
        "Cache invalidation failed",
        "Could not reset cache for one or more files:\n\n" + "\n".join(failures),
        NoticeKind.WARNING,
    )


def _export_failed(title: str, errors: Sequence[str]) -> Notice:
    """Return the notice that lists the documents an export could not write."""
    return Notice(
        title,
        "Could not export the following file(s):\n\n" + "\n".join(errors),
        NoticeKind.WARNING,
    )


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


def close_during_run() -> Question:
    """Ask whether to stop the run and close; the default answer keeps the run going."""
    return Question(
        "Documents are still being processed",
        "Stop now and close Teacher's Teammate?\n\n"
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
    return FilePrompt("Select output folder", start=start)


def select_input_folder(start: str = "") -> FilePrompt:
    """The picker for the folder the documents are read from."""
    return FilePrompt("Select Input Folder", start=start)


def select_input_files(start: str = "") -> FilePrompt:
    """The picker for the documents themselves, instead of a whole folder."""
    return FilePrompt("Select Input Files", name_filter=SUPPORTED_FILES_FILTER, start=start)


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
