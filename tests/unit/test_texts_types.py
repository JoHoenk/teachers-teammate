"""The shapes of teacher-facing text: which button goes ahead, and what the texts carry."""

from __future__ import annotations

import pytest

from teachers_teammate.texts import dialogs as dlg
from teachers_teammate.texts import privacy
from teachers_teammate.texts._types import (
    Answer,
    Choice,
    ChoiceRole,
    NoticeKind,
    StandardButton,
    cancel,
    yes_no,
)


@pytest.mark.parametrize("standard", [StandardButton.NO, StandardButton.CANCEL])
def test_a_hand_built_no_or_cancel_button_does_not_go_ahead(standard: StandardButton) -> None:
    """
    Given  a No or Cancel button built without naming its role
    When   the teacher clicks it
    Then   the answer is not accepted, so a destructive action is not confirmed by it
    """
    assert not Answer(Choice(standard=standard)).accepted


@pytest.mark.parametrize(
    "choice",
    [
        Choice("Run anyway"),
        Choice("Stop and close", role=ChoiceRole.DESTRUCTIVE),
        Choice(standard=StandardButton.YES),
        Choice(standard=StandardButton.OK),
    ],
)
def test_every_other_button_goes_ahead(choice: Choice) -> None:
    """
    Given  an accepting, a destructive, a Yes or an Ok button
    When   the teacher clicks it
    Then   the answer is accepted
    """
    assert Answer(choice).accepted


def test_a_question_closed_without_a_click_does_not_go_ahead() -> None:
    """
    Given  a question closed with the window's close button
    When   its answer is read
    Then   no button was clicked and the answer is not accepted
    """
    assert not Answer(None).accepted


def test_the_ready_made_buttons_decline_by_role() -> None:
    """
    Given  the yes/no pair and the cancel button
    When   they are asked whether they decline
    Then   Yes goes ahead, No and Cancel call the action off
    """
    yes, no = yes_no()

    assert (yes.declines, no.declines, cancel().declines) == (False, True, True)


def test_the_leave_computer_question_lists_the_steps_and_the_advice() -> None:
    """
    Given  a privacy level, two steps that send content away and one tip
    When   the question is built
    Then   the body names the level, each step with its badge, the tip and the reminder
    """
    question = dlg.documents_leave_computer(
        "Level text", [("Proofreading", "online"), ("Review", "online")], ["A tip"]
    )

    assert question.body == (
        "Level text\n\nThese steps send content away from this computer:\n\n"
        "  • Proofreading — online\n  • Review — online\n\nA tip\n\n"
        "Make sure your school's data protection rules allow this."
    )


def test_the_export_pickers_start_where_the_caller_says() -> None:
    """
    Given  an export folder and a suggested file name
    When   the Save As and the folder prompts are built
    Then   each carries its start, so the dialog opens there
    """
    assert dlg.save_export("Word Document…", "*.docx", "/out/a.docx").start == "/out/a.docx"
    assert dlg.save_export_to_folder("Word Document…", "/out").start == "/out"


def test_the_text_export_error_names_the_files_left_out_before() -> None:
    """
    Given  an export that stopped after two files were already skipped
    When   the notice is built
    Then   the body says why it stopped and lists the skipped files
    """
    notice = dlg.text_export_error("It broke", ["a.png: no text", "b.png: no text"])

    assert notice.body == "It broke\n\nAlso skipped:\na.png: no text\nb.png: no text"
    assert dlg.text_export_error("It broke").body == "It broke"


def test_the_two_export_failures_differ_only_in_their_title() -> None:
    """
    Given  the same list of failed documents
    When   the Word and the text failure notices are built
    Then   they list the same files under their own title, as a warning
    """
    word, text = dlg.word_export_failed(["a: x"]), dlg.text_export_failed(["a: x"])

    assert (word.title, text.title) == ("Export Word Document", "Export Text")
    assert word.body == text.body
    assert word.kind is text.kind is NoticeKind.WARNING


def test_the_installation_notice_is_the_please_wait_notice() -> None:
    """
    Given  the notice that a window stays open during an installation
    When   it is compared with the general please-wait notice
    Then   it is that notice with the installation sentence
    """
    assert dlg.installation_running() == dlg.cannot_close(dlg.INSTALLATION_RUNNING)


def test_an_unreadable_sent_result_is_a_warning_and_a_missing_text_is_not() -> None:
    """
    Given  the two notices of "What is sent for this file" that say there is no text
    When   their kinds are compared
    Then   the failure is a warning and the ordinary case is information
    """
    assert privacy.what_is_sent_unreadable().kind is NoticeKind.WARNING
    assert privacy.what_is_sent_without_text().kind is NoticeKind.INFORMATION


def test_the_spacy_explanations_differ_for_the_installed_app_and_a_checkout() -> None:
    """
    Given  a missing spaCy
    When   it is explained for the installed app and for a source checkout
    Then   the app points at the Add-ons page and the checkout at pip
    """
    assert "Add-ons" in privacy.spacy_missing("x", installed_app=True)
    assert "pip install" in privacy.spacy_missing("x", installed_app=False)
    assert "spacy download" in privacy.spacy_model_missing("no model")
