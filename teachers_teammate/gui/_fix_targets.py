"""Where each unmet requirement is fixed: one table for every place that offers the fix.

The readiness line above Run, the requirement guard that unticks a step, and the dialog that
stops a run all ask :func:`fix_for`, so they say the same thing and open the same place.  The
rows are matched on :class:`~teachers_teammate.domain.requirements.IssueKind`, never on the
sentence, so rewording a message can't break a fix.  Pure functions: no widgets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..domain.install_item import READER_ITEM, SPACY_ITEM, InstallItem, InstallKind
from ..domain.requirements import IssueKind, RequirementIssue
from ..domain.stages import Stage
from ..texts import addons, fixes
from ..texts import main_window as win
from ..texts import review as txt
from ..texts.issues import issue_text
from .settings._page import (
    FOCUS_ADDON_UPDATE,
    FOCUS_FIND_SERVER,
    FOCUS_LOCAL_MODELS,
    FOCUS_NAME_DETECTION,
    FOCUS_OCR,
    FOCUS_ONLINE_SERVICES,
    FOCUS_PROOFREADING_KEY,
    FOCUS_READING_MODELS,
    FOCUS_REVIEW_KEY,
    SettingsPage,
)

#: ``FixAction.command`` values for fixes that are not a Settings page.
COMMAND_RETRY = "retry"
COMMAND_CHOOSE_FOLDER = "choose_folder"
COMMAND_TURN_ON_RECOGNITION = "turn_on_recognition"
COMMAND_USE_SERVER = "use_server"
COMMAND_GET_OLLAMA = "get_ollama"
COMMAND_START_OLLAMA = "start_ollama"

#: What is wrong with the Results step (the keys of :func:`results_fix`).
RESULTS_NO_FOLDER = "no_folder"
RESULTS_TEXT_IN_INPUT = "text_in_input"


@dataclass(frozen=True)
class FixAction:
    """One button: its label and what it does."""

    #: The button text, naming the action ("Download…", "Enter key…").
    label: str
    #: The Settings page to open, or ``None`` when the action is a :attr:`command`.
    page: SettingsPage | None = None
    focus: str = ""
    preselect: tuple[InstallItem, ...] = ()
    #: ``"retry"``, ``"choose_folder"``, ``"turn_on_recognition"``, ``"use_server"``,
    #: ``"get_ollama"`` or ``"start_ollama"`` when there is no page.
    command: str = ""
    #: What the command works on (the server address of ``"use_server"``).
    argument: str = ""
    #: A longer explanation for the button's tooltip (``""`` = none).
    tooltip: str = ""


@dataclass(frozen=True)
class Fix:
    """What to tell the teacher, and the buttons that fix it."""

    #: A short sentence for the readiness line.
    line: str
    actions: tuple[FixAction, ...]


def _reader_fix() -> Fix:
    """Return the fix for missing reader model files: download them on the Add-ons page."""
    download = FixAction(fixes.DOWNLOAD, SettingsPage.ADDONS, FOCUS_READING_MODELS, (READER_ITEM,))
    return Fix(fixes.READER_MISSING, (download,))


def found_server_fix(where: str, url: str) -> Fix:
    """Return the fix for an unreachable Ollama when a silent search found one at *url*.

    *where* says whose computer it is ("this computer", "the Windows computer").  The server
    is never taken over by itself: the button asks.
    """
    return Fix(
        fixes.found_server(where),
        (
            FixAction(fixes.USE_IT, command=COMMAND_USE_SERVER, argument=url),
            FixAction(fixes.FIND_SERVER, SettingsPage.AI_SETUP, FOCUS_FIND_SERVER),
        ),
    )


def _ollama_fix(issue: RequirementIssue) -> Fix | None:
    """Return the fix for an Ollama that does not answer, or ``None`` for another kind.

    A local address that does not answer is told apart by whether the program is there: the
    teacher then either installs Ollama or starts it.  Where the app may not start it (Linux),
    the tooltip names the service command instead of offering a button that would fail.
    """
    kind = issue.kind
    if kind is IssueKind.OLLAMA_NOT_FOUND:
        return Fix(
            fixes.OLLAMA_NOT_FOUND,
            (
                FixAction(fixes.GET_OLLAMA, command=COMMAND_GET_OLLAMA),
                FixAction(fixes.CHECK_AGAIN, command=COMMAND_RETRY),
            ),
        )
    if kind is IssueKind.OLLAMA_NOT_RUNNING:
        if issue.startable:
            return Fix(
                fixes.OLLAMA_NOT_RUNNING,
                (
                    FixAction(fixes.START_OLLAMA, command=COMMAND_START_OLLAMA),
                    FixAction(fixes.CHECK_AGAIN, command=COMMAND_RETRY),
                ),
            )
        return Fix(
            fixes.OLLAMA_NOT_RUNNING,
            (FixAction(fixes.CHECK_AGAIN, command=COMMAND_RETRY, tooltip=txt.OLLAMA_SERVICE_HINT),),
        )
    if kind is IssueKind.OLLAMA_UNREACHABLE:
        return Fix(
            fixes.OLLAMA_UNREACHABLE,
            (
                FixAction(fixes.CHECK_AGAIN, command=COMMAND_RETRY),
                FixAction(fixes.FIND_SERVER, SettingsPage.AI_SETUP, FOCUS_FIND_SERVER),
            ),
        )
    return None


def _general_fix(issue: RequirementIssue) -> Fix | None:
    """Return the fix for a requirement that is not about one model or package, else ``None``."""
    kind = issue.kind
    if kind is IssueKind.ADDON_RESTART_NEEDED:
        # Nothing to press: the install is done, the app has to start again.
        return Fix(issue_text(issue), ())
    if kind is IssueKind.NOTHING_TO_READ:
        return Fix(
            win.NOTHING_TO_READ_LINE,
            (FixAction(win.TURN_ON_RECOGNITION, command=COMMAND_TURN_ON_RECOGNITION),),
        )
    if kind is IssueKind.ADDONS_OUTDATED:
        return Fix(
            addons.ADDONS_OUTDATED_LINE,
            (
                FixAction(
                    fixes.UPDATE_ADDONS_BUTTON,
                    SettingsPage.ADDONS,
                    FOCUS_ADDON_UPDATE,
                    tooltip=addons.ADDONS_OUTDATED_NOTE,
                ),
            ),
        )
    if kind is IssueKind.OUTPUT_IN_INPUT_FOLDER:
        # The same fix the Results step offers by itself: choose another output folder.
        return results_fix(RESULTS_TEXT_IN_INPUT)
    if kind is IssueKind.OCR_NOT_ALLOWED:
        return Fix(
            fixes.OCR_NOT_ALLOWED,
            (FixAction(fixes.CHOOSE_MODEL, SettingsPage.AI_MODELS, FOCUS_OCR),),
        )
    return None


def fix_for(
    stage: str,
    issue: RequirementIssue,
    display_name: Callable[[str], str],
    *,
    language: str = "",
) -> Fix:
    """Return the line and the buttons for *issue*, found in *stage*.

    Args:
        stage: The step the issue belongs to (a :class:`~teachers_teammate.domain.stages.Stage`);
            it decides which key row ``Enter key…`` opens.
        issue: What is missing.
        display_name: Turns a provider id into the name the teacher knows.
        language: The document language, to name the language of a missing name detection.
    """
    kind = issue.kind
    general = _general_fix(issue)
    if general is not None:
        return general
    if kind is IssueKind.READER_MODEL_MISSING:
        return _reader_fix()
    ollama = _ollama_fix(issue)
    if ollama is not None:
        return ollama
    models = tuple(InstallItem(InstallKind.OLLAMA, model) for model in issue.models)
    if kind is IssueKind.NO_OLLAMA_MODEL:
        return Fix(
            fixes.NO_OLLAMA_MODEL,
            (FixAction(fixes.DOWNLOAD, SettingsPage.ADDONS, FOCUS_LOCAL_MODELS, models),),
        )
    if kind is IssueKind.MODEL_MISSING:
        return Fix(
            fixes.models_missing(issue.models),
            (FixAction(fixes.DOWNLOAD, SettingsPage.ADDONS, FOCUS_LOCAL_MODELS, models),),
        )
    if kind is IssueKind.PACKAGE_MISSING:
        return Fix(
            fixes.package_missing(display_name(issue.provider)),
            (
                FixAction(
                    fixes.INSTALL,
                    SettingsPage.ADDONS,
                    FOCUS_ONLINE_SERVICES,
                    (InstallItem(InstallKind.PROVIDER, issue.provider),),
                ),
            ),
        )
    if kind is IssueKind.KEY_MISSING:
        focus = FOCUS_REVIEW_KEY if stage == Stage.EVALUATION else FOCUS_PROOFREADING_KEY
        return Fix(
            fixes.key_missing(display_name(issue.provider)),
            (FixAction(fixes.ENTER_KEY, SettingsPage.AI_MODELS, focus),),
        )
    models = tuple(InstallItem(InstallKind.SPACY, model) for model in issue.models)
    if kind is IssueKind.PRIVACY_ADDON_MISSING:
        # spaCy itself is missing: installing it brings the model along, so the fix ticks both.
        return Fix(
            fixes.name_detection_missing(language),
            (
                FixAction(
                    fixes.INSTALL,
                    SettingsPage.ADDONS,
                    FOCUS_NAME_DETECTION,
                    (SPACY_ITEM, *models),
                ),
            ),
        )
    # NAME_DETECTION_MISSING: spaCy is there, the model of this language is not.
    return Fix(
        fixes.name_model_missing(language),
        (FixAction(fixes.DOWNLOAD, SettingsPage.ADDONS, FOCUS_NAME_DETECTION, models),),
    )


def results_fix(problem: str) -> Fix:
    """Return the line and button for a problem with the Results step.

    Args:
        problem: :data:`RESULTS_NO_FOLDER` or :data:`RESULTS_TEXT_IN_INPUT`.
    """
    choose = (FixAction(fixes.CHOOSE_FOLDER, command=COMMAND_CHOOSE_FOLDER),)
    if problem == RESULTS_TEXT_IN_INPUT:
        return Fix(fixes.RESULTS_INSIDE_DOCUMENTS, choose)
    return Fix(fixes.RESULTS_NO_FOLDER, choose)
