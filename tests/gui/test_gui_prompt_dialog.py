"""GUI tests for the instructions dialog of the main panel (prompt, answer formats, notes)."""
# pylint: disable=W0212,W0621  # protected-access — the tests drive the editor's widgets directly / redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog

from teachers_teammate.domain.answer_format import (
    ANSWER_FORMAT_VERSION,
    REVIEW_FORMAT,
    TEXT_FORMAT,
)
from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.notation import with_notation_hint
from teachers_teammate.gui import _user_text as txt
from teachers_teammate.gui._config_panel import ConfigPanel, _PromptDialog
from teachers_teammate.infrastructure.correction import PRESET_PROMPTS

from ._settings_stubs import DummyAppService

_GERMAN = PRESET_PROMPTS["german"]


def _dialog(qtbot, prompt: str = "", **kwargs) -> _PromptDialog:
    """Build the proofreading instructions dialog as the panel does, and show it."""
    kwargs.setdefault("level_aware", True)
    dlg = _PromptDialog(txt.CORRECTION_PROMPT_DIALOG_TITLE, prompt, **kwargs)
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


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
@pytest.mark.parametrize(
    ("prompt", "hint_visible"),
    [("", False), ("german", False), ("Use level {level}.", False), ("Fix spelling.", True)],
)
def test_hint_only_at_custom_prompts_without_level(qtbot, prompt: str, hint_visible: bool) -> None:
    """
    Given  the dialog opened with an empty prompt, a preset, or a custom prompt
    When   it is shown
    Then   the {level} hint is visible only for a custom prompt without the placeholder
    """
    dlg = _dialog(qtbot, prompt)

    assert dlg._editor._level_hint.isVisible() is hint_visible


@pytest.mark.gui
def test_hint_follows_the_edited_prompt(qtbot) -> None:
    """
    Given  the dialog showing a custom prompt without {level}
    When   the teacher adds the placeholder
    Then   the hint disappears
    """
    dlg = _dialog(qtbot, "Fix spelling.")
    assert dlg._editor._level_hint.isVisible()

    dlg._editor._edit.setPlainText("Fix spelling at {level}.")

    assert not dlg._editor._level_hint.isVisible()


@pytest.mark.gui
def test_the_review_dialog_has_no_level_hint(qtbot) -> None:
    """
    Given  the content review instructions (they do not use the level)
    When   the dialog is shown with a custom prompt
    Then   no {level} hint appears
    """
    dlg = _dialog(qtbot, "Review the content.", level_aware=False)

    assert not dlg._editor._level_hint.isVisible()


@pytest.mark.gui
def test_opens_with_the_editable_builtin_text(qtbot) -> None:
    """
    Given  the dialog opened with an empty prompt and the German built-in text
    When   it is shown
    Then   the one box holds the German text and can be edited, the status line says it is the
           built-in one, and nothing counts as customised ("" is returned)
    """
    dlg = _dialog(qtbot, "", builtin_text=_GERMAN)

    editor = dlg._editor
    assert editor._edit.toPlainText() == _GERMAN
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
    dlg = _dialog(qtbot, "", builtin_text=_GERMAN)
    editor = dlg._editor

    editor._edit.setPlainText("My own instructions at {level}.")
    assert editor.prompt() == "My own instructions at {level}."
    assert editor._status.text() == txt.CUSTOM_STATUS
    assert editor._reset_btn.isEnabled()

    editor._reset_btn.click()

    assert editor._edit.toPlainText() == _GERMAN
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
    dlg = _dialog(qtbot, "Fix it at {level}.", builtin_text=_GERMAN)

    editor = dlg._editor
    assert editor._edit.toPlainText() == "Fix it at {level}."
    assert editor._status.text() == txt.CUSTOM_STATUS
    assert editor._reset_btn.isEnabled()


@pytest.mark.gui
def test_answer_format_is_collapsed_editable_and_resettable(qtbot) -> None:
    """
    Given  the proofreading instructions with answer formats
    When   it is shown, the advanced section is opened and the review format is edited
    Then   the section starts collapsed, the dialog returns the edit (and a version), a reset
           gives back the built-in format, and an unchanged format is returned as ""
    """
    dlg = _dialog(qtbot, answer_formats=(REVIEW_FORMAT, TEXT_FORMAT))
    editor = dlg._editor
    assert editor._format_boxes is not None
    review_box, text_box = editor._format_boxes
    assert not editor._advanced.isVisibleTo(dlg)
    assert dlg.answer_format_values()["answer_format_review"] == ""

    editor._advanced_toggle.click()
    assert editor._advanced.isVisibleTo(dlg)
    assert review_box.edit().toPlainText() == REVIEW_FORMAT
    assert text_box.edit().toPlainText() == TEXT_FORMAT

    review_box.edit().setPlainText("Answer in JSON, please.")
    values = dlg.answer_format_values()
    assert values["answer_format_review"] == "Answer in JSON, please."
    assert values["answer_format_text"] == ""
    assert values["answer_format_version"] == ANSWER_FORMAT_VERSION

    review_box._reset.click()
    assert review_box.edit().toPlainText() == REVIEW_FORMAT
    assert dlg.answer_format_values()["answer_format_review"] == ""


@pytest.mark.gui
def test_answer_format_warns_when_a_field_name_is_missing(qtbot) -> None:
    """
    Given  the advanced answer-format editor
    When   the review format loses the name of a field of the answer
    Then   an orange hint names the missing field; it goes away when the format is complete
    """
    dlg = _dialog(qtbot, answer_formats=(REVIEW_FORMAT, TEXT_FORMAT))
    dlg._editor._advanced_toggle.click()
    assert dlg._editor._format_boxes is not None
    review_box = dlg._editor._format_boxes[0]
    assert not review_box._missing.isVisibleTo(dlg)

    review_box.edit().setPlainText(REVIEW_FORMAT.replace("why_unclear", "reason"))

    assert review_box._missing.isVisibleTo(dlg)
    assert "why_unclear" in review_box._missing.text()

    review_box.edit().setPlainText(REVIEW_FORMAT)
    assert not review_box._missing.isVisibleTo(dlg)


@pytest.mark.gui
def test_a_saved_answer_format_opens_in_the_editor(qtbot) -> None:
    """
    Given  saved answer formats of the teacher
    When   the dialog is shown
    Then   the editors hold them, and they are returned unchanged
    """
    dlg = _dialog(
        qtbot,
        answer_formats=(REVIEW_FORMAT, TEXT_FORMAT),
        saved_formats=("R format", "T format"),
    )

    assert dlg._editor._format_boxes is not None
    review_box, text_box = dlg._editor._format_boxes
    assert review_box.edit().toPlainText() == "R format"
    assert text_box.edit().toPlainText() == "T format"
    values = dlg.answer_format_values()
    assert (values["answer_format_review"], values["answer_format_text"]) == (
        "R format",
        "T format",
    )


@pytest.mark.gui
def test_conflict_hint_for_format_instructions(qtbot) -> None:
    """
    Given  a custom prompt that sets the answer format
    When   the dialog is shown
    Then   the conflict hint is visible; it disappears for a prompt without such a line
    """
    dlg = _dialog(qtbot, "Return only the corrected text.")

    assert dlg._editor._conflict_hint.isVisible()

    dlg._editor._edit.setPlainText("Fix my students' homework at {level}.")
    assert not dlg._editor._conflict_hint.isVisible()


# ── the panel hands the dialog what the app always adds ────────────────────────


class _NotationService(DummyAppService):
    def evaluation_fixed_instructions(self, text_format: TextFormat = "plain") -> str:
        return with_notation_hint("", text_format, task="evaluation").strip()


def _capture_fixed_text(qtbot, monkeypatch, open_dialog) -> str:
    """Open a dialog from the panel; return what its "always added" box shows."""
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._service = _NotationService()
    shown: list[str] = []

    def fake_exec(dlg: _PromptDialog) -> QDialog.DialogCode:
        shown.append(dlg._editor._fixed_view.toPlainText())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(_PromptDialog, "exec", fake_exec)
    open_dialog(panel)
    return shown[0]


@pytest.mark.gui
def test_the_proofreading_dialog_lists_the_notation_hint(qtbot, monkeypatch) -> None:
    """
    Given  the panel's proofreading instructions
    When   the dialog is opened
    Then   the always-added part lists what the app adds for a text with formatting marks, under
           its own heading, so the dialog does not claim less than is sent
    """
    fixed = _capture_fixed_text(
        qtbot, monkeypatch, lambda panel: panel._on_configure_correction_prompt()
    )

    assert txt.FORMATTED_TEXT_NOTE in fixed
    assert fixed.index(txt.FORMATTED_TEXT_NOTE) < len(fixed) - len(txt.FORMATTED_TEXT_NOTE)


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
def test_the_review_dialog_has_no_note_without_a_service(qtbot) -> None:
    """
    Given  a panel without an application service
    When   the review note is asked for
    Then   it is empty (nothing to claim)
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    assert panel._evaluation_fixed_text() == ""


@pytest.mark.gui
def test_explanations_options_round_trip_in_the_dialog(qtbot) -> None:
    """
    Given  the proofreading dialog opened with German explanations in "corrections only" mode
    When   the values are read, then the box is cleared
    Then   it returns "text" while checked and "review" once cleared, with the language kept
    """
    dlg = _dialog(qtbot, explanations=("German", "text"))

    assert dlg.explanation_values() == {
        "explanation_language": "German",
        "correction_output": "text",
    }

    assert dlg._corrections_only is not None
    dlg._corrections_only.setChecked(False)
    assert dlg.explanation_values()["correction_output"] == "review"
