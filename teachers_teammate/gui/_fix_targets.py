"""Where each unmet requirement is fixed: one table for every place that offers the fix.

The readiness line above Run, the requirement guard that unticks a step, and the dialog that
stops a run all ask :func:`fix_for`, so they say the same thing and open the same place.  The
rows are matched on :class:`~teachers_teammate.application.service.IssueKind`, never on the
sentence, so rewording a message can't break a fix.  Pure functions: no widgets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..application.service import IssueKind, RequirementIssue
from ..domain.data_location import OCR_ENGINE_NAMES
from . import _user_text as txt
from ._model_guidance import PROVIDER_PREFIX, SPACY_PREFIX
from .settings._page import (
    FOCUS_FIND_SERVER,
    FOCUS_LOCAL_MODELS,
    FOCUS_NAME_DETECTION,
    FOCUS_OCR,
    FOCUS_OCR_ENGINES,
    FOCUS_ONLINE_SERVICES,
    FOCUS_PROOFREADING_KEY,
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
    preselect: tuple[str, ...] = ()
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


def found_server_fix(where: str, url: str) -> Fix:
    """Return the fix for an unreachable Ollama when a silent search found one at *url*.

    *where* says whose computer it is ("this computer", "the Windows computer").  The server
    is never taken over by itself: the button asks.
    """
    return Fix(
        f"Ollama isn't reachable at the saved address, but one answers on {where}",
        (
            FixAction("Use it", command=COMMAND_USE_SERVER, argument=url),
            FixAction("Find server…", SettingsPage.AI_SETUP, FOCUS_FIND_SERVER),
        ),
    )


def _models_line(models: tuple[str, ...]) -> str:
    if len(models) == 1:
        return f"{models[0]} isn't downloaded yet"
    return f"{len(models)} AI models aren't downloaded yet"


def _ollama_fix(issue: RequirementIssue) -> Fix | None:
    """Return the fix for an Ollama that does not answer, or ``None`` for another kind.

    A local address that does not answer is told apart by whether the program is there: the
    teacher then either installs Ollama or starts it.  Where the app may not start it (Linux),
    the tooltip names the service command instead of offering a button that would fail.
    """
    kind = issue.kind
    if kind is IssueKind.OLLAMA_NOT_FOUND:
        return Fix(
            "Ollama wasn't found on this computer",
            (
                FixAction("Get Ollama…", command=COMMAND_GET_OLLAMA),
                FixAction("Check again", command=COMMAND_RETRY),
            ),
        )
    if kind is IssueKind.OLLAMA_NOT_RUNNING:
        if issue.startable:
            return Fix(
                "Ollama isn't running",
                (
                    FixAction("Start Ollama", command=COMMAND_START_OLLAMA),
                    FixAction("Check again", command=COMMAND_RETRY),
                ),
            )
        return Fix(
            "Ollama isn't running",
            (FixAction("Check again", command=COMMAND_RETRY, tooltip=txt.OLLAMA_SERVICE_HINT),),
        )
    if kind is IssueKind.OLLAMA_UNREACHABLE:
        return Fix(
            "Ollama isn't reachable",
            (
                FixAction("Check again", command=COMMAND_RETRY),
                FixAction("Find server…", SettingsPage.AI_SETUP, FOCUS_FIND_SERVER),
            ),
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
        stage: The step the issue belongs to (``"correction"``, ``"evaluation"``, …); it decides
            which key row ``Enter key…`` opens.
        issue: What is missing.
        display_name: Turns a provider id into the name the teacher knows.
        language: The document language, to name the language of a missing name detection.
    """
    kind = issue.kind
    if kind is IssueKind.NOTHING_TO_READ:
        return Fix(
            txt.NOTHING_TO_READ_LINE,
            (FixAction(txt.TURN_ON_RECOGNITION, command=COMMAND_TURN_ON_RECOGNITION),),
        )
    if kind is IssueKind.OCR_NOT_ALLOWED:
        return Fix(
            "Text recognition must run on this computer",
            (FixAction("Choose a model…", SettingsPage.AI_MODELS, FOCUS_OCR),),
        )
    if kind is IssueKind.ENGINE_MISSING:
        name = OCR_ENGINE_NAMES.get(issue.engine, issue.engine)
        return Fix(
            f"{name} isn't installed",
            (FixAction("Install…", SettingsPage.ADDONS, FOCUS_OCR_ENGINES),),
        )
    ollama = _ollama_fix(issue)
    if ollama is not None:
        return ollama
    if kind is IssueKind.NO_OLLAMA_MODEL:
        return Fix(
            "No AI model is downloaded in Ollama yet",
            (FixAction("Download…", SettingsPage.ADDONS, FOCUS_LOCAL_MODELS, issue.models),),
        )
    if kind is IssueKind.MODEL_MISSING:
        return Fix(
            _models_line(issue.models),
            (FixAction("Download…", SettingsPage.ADDONS, FOCUS_LOCAL_MODELS, issue.models),),
        )
    if kind is IssueKind.PACKAGE_MISSING:
        return Fix(
            f"{display_name(issue.provider)} support isn't installed",
            (
                FixAction(
                    "Install…",
                    SettingsPage.ADDONS,
                    FOCUS_ONLINE_SERVICES,
                    (f"{PROVIDER_PREFIX}{issue.provider}",),
                ),
            ),
        )
    if kind is IssueKind.KEY_MISSING:
        focus = FOCUS_REVIEW_KEY if stage == "evaluation" else FOCUS_PROOFREADING_KEY
        return Fix(
            f"No API key for {display_name(issue.provider)} yet",
            (FixAction("Enter key…", SettingsPage.AI_MODELS, focus),),
        )
    # NAME_DETECTION_MISSING
    what = f"Name detection for {language}" if language else "Name detection"
    return Fix(
        f"{what} isn't downloaded",
        (
            FixAction(
                "Download…",
                SettingsPage.ADDONS,
                FOCUS_NAME_DETECTION,
                tuple(f"{SPACY_PREFIX}{model}" for model in issue.models),
            ),
        ),
    )


def results_fix(problem: str) -> Fix:
    """Return the line and button for a problem with the Results step.

    Args:
        problem: :data:`RESULTS_NO_FOLDER` or :data:`RESULTS_TEXT_IN_INPUT`.
    """
    choose = (FixAction("Choose…", command=COMMAND_CHOOSE_FOLDER),)
    if problem == RESULTS_TEXT_IN_INPUT:
        return Fix("Choose a results folder outside the documents folder", choose)
    return Fix("Choose where to save the results", choose)
