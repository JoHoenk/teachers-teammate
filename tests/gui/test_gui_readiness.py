"""Tests of the fix table, the readiness line and the dialog that stops a run."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QPushButton

from teachers_teammate.application.service import IssueKind, RequirementIssue
from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog
from teachers_teammate.gui._fix_targets import (
    COMMAND_CHOOSE_FOLDER,
    COMMAND_GET_OLLAMA,
    COMMAND_RETRY,
    COMMAND_START_OLLAMA,
    COMMAND_TURN_ON_RECOGNITION,
    COMMAND_USE_SERVER,
    RESULTS_NO_FOLDER,
    RESULTS_TEXT_IN_INPUT,
    Fix,
    FixAction,
    fix_for,
    found_server_fix,
    results_fix,
)
from teachers_teammate.gui._readiness_line import ReadinessLine, Tone, readiness
from teachers_teammate.gui.settings._page import (
    FOCUS_FIND_SERVER,
    FOCUS_LOCAL_MODELS,
    FOCUS_NAME_DETECTION,
    FOCUS_OCR,
    FOCUS_ONLINE_SERVICES,
    FOCUS_PROOFREADING_KEY,
    FOCUS_READING_MODELS,
    FOCUS_REVIEW_KEY,
    FOCUS_SERVER,
    SettingsPage,
)


def _name(provider: str) -> str:
    return {"mistral_eu": "Mistral (EU)", "mistral": "Mistral"}.get(provider, provider)


def _fix(stage: str, issue: RequirementIssue, **kw) -> Fix:
    return fix_for(stage, issue, _name, **kw)


# ── fix_for: one case per row of the table ───────────────────────────────────


@pytest.mark.unit
def test_fix_for_nothing_to_read_offers_to_turn_recognition_on() -> None:
    """
    Given  text recognition switched off and no queued file with text of its own
    When   its fix is asked
    Then   the line says there is nothing to read and the button turns recognition on
    """
    fix = _fix("ocr", RequirementIssue("nothing", IssueKind.NOTHING_TO_READ))

    assert fix.line.startswith("Nothing to read")
    assert [(a.label, a.command, a.page) for a in fix.actions] == [
        ("Turn on", COMMAND_TURN_ON_RECOGNITION, None)
    ]


@pytest.mark.unit
def test_fix_for_a_refused_recognition_offers_the_model_choice() -> None:
    """
    Given  a refused text recognition
    When   its fix is asked
    Then   the line says it must run here and the button opens AI models on the reading step
    """
    fix = _fix("ocr", RequirementIssue("Cloud.", IssueKind.OCR_NOT_ALLOWED))

    assert fix.line == "Text recognition must run on this computer"
    assert fix.actions == (FixAction("Choose a model…", SettingsPage.AI_MODELS, FOCUS_OCR),)


@pytest.mark.unit
def test_fix_for_a_missing_reader_offers_the_download() -> None:
    """
    Given  the built-in reader whose model files are not downloaded
    When   its fix is asked
    Then   the line says so and "Download…" opens the Reading models tab with the reader ticked
    """
    fix = _fix("ocr", RequirementIssue("x", IssueKind.READER_MODEL_MISSING))

    assert fix.line == "The built-in reader isn't downloaded yet"
    assert fix.actions == (
        FixAction(
            "Download…", SettingsPage.ADDONS, FOCUS_READING_MODELS, preselect=("reader:ppocr",)
        ),
    )


@pytest.mark.unit
def test_fix_for_an_unreachable_ollama_offers_retry_and_finding_the_server() -> None:
    """
    Given  an Ollama that cannot be reached
    When   its fix is asked
    Then   there is a "Check again" command and a button that opens the server search
    """
    fix = _fix("", RequirementIssue("down", IssueKind.OLLAMA_UNREACHABLE))

    assert fix.line == "Ollama isn't reachable"
    assert fix.actions == (
        FixAction("Check again", command=COMMAND_RETRY),
        FixAction("Find server…", SettingsPage.AI_SETUP, FOCUS_FIND_SERVER),
    )


@pytest.mark.unit
def test_fix_for_a_missing_ollama_offers_the_download_page_and_a_new_check() -> None:
    """
    Given  a local address that does not answer and no Ollama program on the computer
    When   its fix is asked
    Then   the line says Ollama wasn't found, and the buttons open the download page and
           check again
    """
    fix = _fix("", RequirementIssue("down", IssueKind.OLLAMA_NOT_FOUND))

    assert fix.line == "Ollama wasn't found on this computer"
    assert fix.actions == (
        FixAction("Get Ollama…", command=COMMAND_GET_OLLAMA),
        FixAction("Check again", command=COMMAND_RETRY),
    )


@pytest.mark.unit
def test_fix_for_a_stopped_ollama_offers_to_start_it_where_the_app_can() -> None:
    """
    Given  an installed Ollama that isn't running, on a platform where the app may start it
    When   its fix is asked
    Then   the line says it isn't running, and the buttons start it and check again
    """
    fix = _fix("", RequirementIssue("down", IssueKind.OLLAMA_NOT_RUNNING, startable=True))

    assert fix.line == "Ollama isn't running"
    assert fix.actions == (
        FixAction("Start Ollama", command=COMMAND_START_OLLAMA),
        FixAction("Check again", command=COMMAND_RETRY),
    )


@pytest.mark.unit
def test_fix_for_a_stopped_ollama_on_linux_names_the_service_command() -> None:
    """
    Given  an installed Ollama that isn't running, on a platform where the app may not start it
    When   its fix is asked
    Then   there is only "Check again", and its tooltip says how to start the service
    """
    fix = _fix("", RequirementIssue("down", IssueKind.OLLAMA_NOT_RUNNING))

    assert fix.line == "Ollama isn't running"
    assert [a.label for a in fix.actions] == ["Check again"]
    assert fix.actions[0].command == COMMAND_RETRY
    assert "systemctl start ollama" in fix.actions[0].tooltip


@pytest.mark.unit
def test_found_server_fix_offers_to_use_it_and_never_switches_by_itself() -> None:
    """
    Given  a silent search that found Ollama on the Windows computer
    When   the fix for the unreachable saved address is built
    Then   the line names the place, "Use it" carries the address as a command, and the
           search stays available
    """
    fix = found_server_fix("the Windows computer", "http://172.20.0.1:11434")

    assert "the Windows computer" in fix.line
    assert fix.actions[0] == FixAction(
        "Use it", command=COMMAND_USE_SERVER, argument="http://172.20.0.1:11434"
    )
    assert fix.actions[1].focus == FOCUS_FIND_SERVER


@pytest.mark.unit
def test_fix_for_missing_models_says_how_many_and_preselects_them() -> None:
    """
    Given  no model at all, one missing model, and two missing models
    When   their fixes are asked
    Then   each offers "Download…" with those models ticked, and the line counts them
    """
    none = _fix("", RequirementIssue("x", IssueKind.NO_OLLAMA_MODEL, models=("qwen3:8b",)))
    one = _fix("", RequirementIssue("x", IssueKind.MODEL_MISSING, models=("qwen3:8b",)))
    two = _fix("", RequirementIssue("x", IssueKind.MODEL_MISSING, models=("a:1", "b:2")))

    assert none.line == "No AI model is downloaded in Ollama yet"
    assert one.line == "qwen3:8b isn't downloaded yet"
    assert two.line == "2 AI models aren't downloaded yet"
    assert two.actions == (
        FixAction("Download…", SettingsPage.ADDONS, FOCUS_LOCAL_MODELS, ("a:1", "b:2")),
    )
    assert none.actions[0].preselect == ("qwen3:8b",)


@pytest.mark.unit
def test_fix_for_a_missing_package_ticks_the_provider_in_add_ons() -> None:
    """
    Given  an online service whose package is not installed
    When   its fix is asked
    Then   "Install…" opens the online-services tab with that provider ticked
    """
    fix = _fix("correction", RequirementIssue("x", IssueKind.PACKAGE_MISSING, provider="mistral"))

    assert fix.line == "Mistral support isn't installed"
    assert fix.actions == (
        FixAction("Install…", SettingsPage.ADDONS, FOCUS_ONLINE_SERVICES, ("provider:mistral",)),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stage", "focus"),
    [("correction", FOCUS_PROOFREADING_KEY), ("evaluation", FOCUS_REVIEW_KEY)],
)
def test_fix_for_a_missing_key_picks_the_key_row_of_the_step(stage: str, focus: str) -> None:
    """
    Given  a missing API key for proofreading or for the review
    When   its fix is asked
    Then   "Enter key…" opens AI models on the key row of that step
    """
    fix = _fix(stage, RequirementIssue("x", IssueKind.KEY_MISSING, provider="mistral_eu"))

    assert fix.line == "No API key for Mistral (EU) yet"
    assert fix.actions == (FixAction("Enter key…", SettingsPage.AI_MODELS, focus),)


@pytest.mark.unit
def test_fix_for_missing_name_detection_ticks_the_spacy_model() -> None:
    """
    Given  name detection that is not downloaded for German
    When   its fix is asked
    Then   the line names the language and "Download…" ticks the spaCy model
    """
    issue = RequirementIssue("x", IssueKind.NAME_DETECTION_MISSING, models=("de_core_news_sm",))

    fix = _fix("anonymizer", issue, language="German")

    assert fix.line == "Name detection for German isn't downloaded"
    assert fix.actions == (
        FixAction(
            "Download…", SettingsPage.ADDONS, FOCUS_NAME_DETECTION, ("spacy:de_core_news_sm",)
        ),
    )
    assert _fix("anonymizer", issue).line == "Name detection isn't downloaded"


@pytest.mark.unit
def test_results_fix_offers_to_choose_a_folder() -> None:
    """
    Given  a Results step without a folder, and one whose text files go into the input folder
    When   their fixes are asked
    Then   both offer "Choose…" as a command, with a line for each problem
    """
    missing = results_fix(RESULTS_NO_FOLDER)
    inside = results_fix(RESULTS_TEXT_IN_INPUT)

    assert missing.line == "Choose where to save the results"
    assert inside.line == "Choose a results folder outside the documents folder"
    assert (
        missing.actions == inside.actions == (FixAction("Choose…", command=COMMAND_CHOOSE_FOLDER),)
    )


# ── readiness(): one case per row, in order ──────────────────────────────────

_FIX = Fix("Ollama isn't reachable", (FixAction("Check again", command=COMMAND_RETRY),))
_RESULTS = results_fix(RESULTS_NO_FOLDER)


def _state(
    *,
    blocker: Fix | None = None,
    results: Fix | None = None,
    inputs_chosen: bool = True,
    files: int = 12,
    selected: int = 12,
    checking: bool = False,
):
    """Return the readiness of a window with 12 selected documents unless told otherwise."""
    return readiness(
        blocker=blocker,
        results=results,
        inputs_chosen=inputs_chosen,
        files=files,
        selected=selected,
        checking=checking,
    )


@pytest.mark.unit
def test_readiness_puts_a_blocker_before_everything_else() -> None:
    """
    Given  a blocker, a results problem and no documents at all
    When   the state is computed
    Then   the blocker's sentence and buttons win, in orange
    """
    state = _state(blocker=_FIX, results=_RESULTS, inputs_chosen=False, files=0, selected=0)

    assert state.text == "⚠ Ollama isn't reachable"
    assert state.tone is Tone.WARNING
    assert state.actions == _FIX.actions


@pytest.mark.unit
def test_readiness_names_a_results_problem_before_the_documents() -> None:
    """
    Given  a problem with the Results step and no documents chosen
    When   the state is computed
    Then   the results fix shows
    """
    state = _state(results=_RESULTS, inputs_chosen=False, files=0, selected=0)

    assert state.text == "⚠ Choose where to save the results"
    assert state.actions == _RESULTS.actions


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "text"),
    [
        ({"inputs_chosen": False, "files": 0, "selected": 0}, "Select documents to start"),
        ({"files": 0, "selected": 0}, "No documents found there"),
        ({"selected": 0}, "Select the documents to run in the queue"),
        ({"checking": True}, "Checking…"),
    ],
)
def test_readiness_walks_the_teacher_towards_the_run(kwargs, text: str) -> None:
    """
    Given  no blocker, and a queue that is missing, empty, unselected or still being checked
    When   the state is computed
    Then   a grey sentence names the next step
    """
    state = _state(**kwargs)

    assert (state.text, state.tone, state.actions) == (text, Tone.HINT, ())


@pytest.mark.unit
def test_readiness_counts_the_documents_when_ready() -> None:
    """
    Given  nothing in the way
    When   all, one or some of the documents are selected
    Then   the green line counts them, with singular and "x of y"
    """
    assert _state().text == "✓ Ready · 12 documents"
    assert _state(files=1, selected=1).text == "✓ Ready · 1 document"
    assert _state(selected=3).text == "✓ Ready · 3 of 12 documents"
    assert _state().tone is Tone.READY


# ── ReadinessLine ────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_line_shows_buttons_and_reports_the_pressed_action(qtbot) -> None:
    """
    Given  a line showing a blocker with two buttons
    When   the second one is pressed
    Then   action_chosen carries that action, and a later state with fewer buttons hides one
    """
    line = ReadinessLine()
    qtbot.addWidget(line)
    two = Fix("x", (FixAction("A"), FixAction("B", SettingsPage.AI_SETUP, FOCUS_SERVER)))
    line.show_state(
        readiness(
            blocker=two, results=None, inputs_chosen=True, files=1, selected=1, checking=False
        )
    )
    chosen: list[FixAction] = []
    line.action_chosen.connect(chosen.append)

    line._buttons[1].click()

    assert chosen == [two.actions[1]]
    line.show_state(_state())
    assert [b.isHidden() for b in line._buttons] == [True, True]


@pytest.mark.gui
def test_a_download_takes_the_line_over_and_hands_it_back(qtbot) -> None:
    """
    Given  a line showing "Ready"
    When   a download starts, makes progress, a new state arrives meanwhile, and it ends
    Then   the line shows the download, keeps the newest state for afterwards, and returns it
    """
    line = ReadinessLine()
    qtbot.addWidget(line)
    line.show_state(_state())

    line.show_download("qwen3:8b")
    line.set_download_progress(40, "40 / 100 MB")
    line.show_state(_state(selected=3))

    assert line.label.text() == "Downloading qwen3:8b"
    assert line.downloading is True
    assert not line._download_bar.isHidden()
    assert line._download_bar.value() == 40
    line.end_download()
    assert line.label.text() == "✓ Ready · 3 of 12 documents"
    assert line._download_bar.isHidden()


# ── DependencyGuardDialog ────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_dialog_offers_the_key_button_and_emits_it(qtbot) -> None:
    """
    Given  a dialog for a missing key
    When   "Enter key…" is pressed
    Then   the dialog emits that action and closes
    """
    issue = RequirementIssue(
        "No API key for Mistral (EU) is set.", IssueKind.KEY_MISSING, provider="mistral_eu"
    )
    fix = _fix("correction", issue)
    dlg = DependencyGuardDialog("Proofread can't be turned on", [issue], fix.actions)
    qtbot.addWidget(dlg)
    chosen: list[FixAction] = []
    dlg.action_chosen.connect(chosen.append)

    button = next(b for b in dlg.findChildren(QPushButton) if b.text() == "Enter key…")
    button.click()

    assert chosen == [fix.actions[0]]
    assert dlg.result() == dlg.DialogCode.Accepted


@pytest.mark.gui
def test_the_dialog_gives_a_retry_no_button(qtbot) -> None:
    """
    Given  the fix of an unreachable Ollama: a retry command and a server address button
    When   the dialog is built
    Then   only the server address gets a button (the caller checks again by itself)
    """
    issue = RequirementIssue("down", IssueKind.OLLAMA_UNREACHABLE)
    dlg = DependencyGuardDialog("The run can't start", [issue], _fix("", issue).actions)
    qtbot.addWidget(dlg)

    assert [a.label for a in dlg.fix_actions] == ["Find server…"]
