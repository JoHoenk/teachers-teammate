"""GUI tests for the two options dialogs of the main panel (content review, proofreading)."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog

from teachers_teammate.domain.correction_prompts import NOTES_LIMIT
from teachers_teammate.texts import review as txt
from teachers_teammate.gui._config_panel import ConfigPanel, _ProofreadingDialog, _PromptDialog

from ._service_stubs import services_of
from ._settings_stubs import DummyAppService

_BUILTIN = "Review the content of the text."

_STEPS = (
    ("Correct the text", "CORRECT SYSTEM"),
    ("Explain the corrections", "EXPLAIN SYSTEM"),
    ("List unclear expressions", "UNCLEAR SYSTEM"),
)


def _dialog(qtbot, prompt: str = "", **kwargs) -> _PromptDialog:
    """Build the content review's instructions dialog as the panel does, and show it."""
    dlg = _PromptDialog(txt.EVALUATION_PROMPT_DIALOG_TITLE, prompt, **kwargs)
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


def _proofreading_dialog(
    qtbot, notes: str = "", prompts=_STEPS, explanations=("", "review")
) -> _ProofreadingDialog:
    """Build the proofreading options dialog as the panel does, and show it."""
    dlg = _ProofreadingDialog(
        txt.CORRECTION_PROMPT_DIALOG_TITLE,
        notes,
        lambda corrections_only: prompts[:1] if corrections_only else prompts,
        explanations=explanations,
    )
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


# ── the content review's instructions ─────────────────────────────────────────


@pytest.mark.gui
def test_the_dialog_shows_the_prompt(qtbot) -> None:
    """
    Given  the instructions dialog opened with a custom prompt
    When   it is created
    Then   the editor holds that prompt and returns it
    """
    dlg = _dialog(qtbot, "Fix spelling.")

    assert dlg.prompt() == "Fix spelling."


@pytest.mark.gui
def test_the_prompt_returns_the_edited_text(qtbot) -> None:
    """
    Given  the dialog
    When   the prompt is edited
    Then   prompt() returns the edited text
    """
    dlg = _dialog(qtbot, "Fix spelling.")

    dlg._editor._edit.setPlainText("My custom prompt.")

    assert dlg.prompt() == "My custom prompt."


@pytest.mark.gui
def test_opens_with_the_editable_builtin_text(qtbot) -> None:
    """
    Given  the dialog opened with an empty prompt and the built-in text
    When   it is shown
    Then   the one box holds the built-in text and can be edited, the status line says it is the
           built-in one, and nothing counts as customised ("" is returned)
    """
    dlg = _dialog(qtbot, "", builtin_text=_BUILTIN)

    editor = dlg._editor
    assert editor._edit.toPlainText() == _BUILTIN
    assert not editor._edit.isReadOnly()
    assert editor._status.text() == txt.BUILTIN_STATUS
    assert editor.prompt() == ""
    assert not editor._reset_btn.isEnabled()


@pytest.mark.gui
def test_edit_and_reset_to_default(qtbot) -> None:
    """
    Given  the dialog showing the built-in text
    When   the text is changed and then Reset to default is pressed
    Then   the changed text is returned and marked as the teacher's own, and the reset puts
           the built-in text back, so "" is returned again and the button is disabled
    """
    dlg = _dialog(qtbot, "", builtin_text=_BUILTIN)
    editor = dlg._editor

    editor._edit.setPlainText("My own instructions.")
    assert editor.prompt() == "My own instructions."
    assert editor._status.text() == txt.CUSTOM_STATUS
    assert editor._reset_btn.isEnabled()

    editor._reset_btn.click()

    assert editor._edit.toPlainText() == _BUILTIN
    assert editor.prompt() == ""
    assert editor._status.text() == txt.BUILTIN_STATUS
    assert not editor._reset_btn.isEnabled()


@pytest.mark.gui
def test_opens_a_saved_custom_prompt_as_it_is(qtbot) -> None:
    """
    Given  a saved custom prompt
    When   the dialog is shown
    Then   the box holds exactly that text, the status line says it is the teacher's own,
           and Reset to default is available
    """
    dlg = _dialog(qtbot, "Fix it.", builtin_text=_BUILTIN)

    editor = dlg._editor
    assert editor._edit.toPlainText() == "Fix it."
    assert editor._status.text() == txt.CUSTOM_STATUS
    assert editor._reset_btn.isEnabled()


@pytest.mark.gui
def test_conflict_hint_for_format_instructions(qtbot) -> None:
    """
    Given  a custom prompt that sets the answer format
    When   the dialog is shown
    Then   the conflict hint is visible; it disappears for a prompt without such a line
    """
    dlg = _dialog(qtbot, "Return only the corrected text.")

    assert dlg._editor._conflict_hint.isVisible()

    dlg._editor._edit.setPlainText("Fix my students' homework.")
    assert not dlg._editor._conflict_hint.isVisible()


# ── the proofreading options ──────────────────────────────────────────────────


@pytest.mark.gui
def test_the_proofreading_dialog_round_trips_the_notes(qtbot) -> None:
    """
    Given  the proofreading options opened with saved notes
    When   the notes are edited
    Then   the box holds the saved text and notes() returns the edit, stripped
    """
    dlg = _proofreading_dialog(qtbot, "Year 9, second foreign language.")

    assert dlg._notes.toPlainText() == "Year 9, second foreign language."

    dlg._notes.setPlainText("  Ignore missing capital letters in headings.  ")

    assert dlg.notes() == "Ignore missing capital letters in headings."


@pytest.mark.gui
def test_the_notes_stop_at_the_limit_and_the_counter_follows(qtbot) -> None:
    """
    Given  the proofreading options with an empty notes box
    When   more than the limit is typed in
    Then   the box keeps the first limit characters and the counter shows the limit
    """
    dlg = _proofreading_dialog(qtbot)

    assert dlg._notes_counter.text() == txt.notes_counter(0)

    dlg._notes.setPlainText("x" * (NOTES_LIMIT + 25))

    assert len(dlg._notes.toPlainText()) == NOTES_LIMIT
    assert dlg._notes_counter.text() == txt.notes_counter(NOTES_LIMIT)
    assert dlg.notes() == "x" * NOTES_LIMIT


@pytest.mark.gui
def test_the_prompts_section_is_collapsed_and_read_only(qtbot) -> None:
    """
    Given  the proofreading options
    When   it is shown and the prompts section is opened
    Then   the section starts collapsed, holds one heading per step, and cannot be edited
    """
    dlg = _proofreading_dialog(qtbot)

    assert not dlg._prompts_view.isVisibleTo(dlg)
    assert dlg._prompts_view.isReadOnly()

    dlg._prompts_toggle.click()

    assert dlg._prompts_view.isVisibleTo(dlg)
    shown = dlg._prompts_view.toPlainText()
    for title, prompt in _STEPS:
        assert title in shown
        assert prompt in shown


@pytest.mark.gui
def test_the_prompts_section_follows_corrections_only(qtbot) -> None:
    """
    Given  the proofreading options showing all three steps
    When   "Corrections only" is ticked
    Then   only step one's prompt is shown, and unticking brings the others back
    """
    dlg = _proofreading_dialog(qtbot)
    assert "EXPLAIN SYSTEM" in dlg._prompts_view.toPlainText()

    dlg._corrections_only.setChecked(True)

    shown = dlg._prompts_view.toPlainText()
    assert "CORRECT SYSTEM" in shown
    assert "EXPLAIN SYSTEM" not in shown
    assert "UNCLEAR SYSTEM" not in shown

    dlg._corrections_only.setChecked(False)
    assert "EXPLAIN SYSTEM" in dlg._prompts_view.toPlainText()


@pytest.mark.gui
def test_the_proofreading_dialog_offers_no_editable_prompt(qtbot) -> None:
    """
    Given  the proofreading options
    When   it is shown
    Then   the notes are the only editable text: there is no prompt editor and no answer-format
           box, because the prompts are fixed
    """
    dlg = _proofreading_dialog(qtbot)

    assert not hasattr(dlg, "_editor")
    assert not hasattr(dlg, "answer_format_values")
    assert dlg._notes.isReadOnly() is False
    assert dlg._prompts_view.isReadOnly()


@pytest.mark.gui
def test_explanations_options_round_trip_in_the_dialog(qtbot) -> None:
    """
    Given  the proofreading options opened with German explanations in "corrections only" mode
    When   the values are read, then the box is cleared
    Then   it returns "text" while checked and "review" once cleared, with the language kept
    """
    dlg = _proofreading_dialog(qtbot, explanations=("German", "text"))

    assert dlg.explanation_values() == {
        "explanation_language": "German",
        "correction_output": "text",
    }

    dlg._corrections_only.setChecked(False)
    assert dlg.explanation_values()["correction_output"] == "review"


# ── the panel hands the dialog what the app always adds ────────────────────────


def _capture_fixed_text(qtbot, monkeypatch, open_dialog) -> str:
    """Open a dialog from the panel; return what its "always added" box shows."""
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._service = services_of(DummyAppService())
    shown: list[str] = []

    def fake_exec(dlg: _PromptDialog) -> QDialog.DialogCode:
        shown.append(dlg._editor._fixed_view.toPlainText())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(_PromptDialog, "exec", fake_exec)
    open_dialog(panel)
    return shown[0]


@pytest.mark.gui
def test_the_review_dialog_lists_the_notation_hint(qtbot, monkeypatch) -> None:
    """
    Given  the panel's content review instructions (the old Content Review settings showed
           this note; it must not be lost with them)
    When   the dialog is opened
    Then   the always-added part lists what the app adds for a text with formatting marks
    """
    fixed = _capture_fixed_text(
        qtbot, monkeypatch, lambda panel: panel._on_configure_evaluation_prompt()
    )

    assert txt.FORMATTED_TEXT_NOTE in fixed
    assert fixed.index(txt.FORMATTED_TEXT_NOTE) < len(fixed) - len(txt.FORMATTED_TEXT_NOTE)


@pytest.mark.gui
def test_the_review_note_needs_no_service(qtbot) -> None:
    """
    Given  a panel without an application service
    When   the review note is asked for
    Then   it still names the notation the review asks for (a fixed text, not a live query)
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    assert txt.FORMATTED_TEXT_NOTE in panel._evaluation_fixed_text()
