"""The shapes of teacher-facing text: which button goes ahead, and what the texts carry."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.anonymization import pattern_problem
from teachers_teammate.domain.data_location import (
    DataFlow,
    DataFlowStage,
    DataLocation,
    DataRegion,
    StageLocation,
)
from teachers_teammate.domain.requirements import IssueKind, RequirementIssue
from teachers_teammate.domain.stages import Stage
from teachers_teammate.texts import dialogs as dlg
from teachers_teammate.texts import app as app_text
from teachers_teammate.texts import menus
from teachers_teammate.texts import privacy
from teachers_teammate.texts import review as txt
from teachers_teammate.texts import settings as st
from teachers_teammate.texts.issues import issue_line, issue_text
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


def test_a_platform_button_carries_its_own_id() -> None:
    """
    Given  the question a folder export asks before it replaces files that already exist
    When   its buttons are read
    Then   each carries the id of the platform button, so a caller branches without the label
    """
    question = dlg.replace_existing_files(1, "Anna.docx")

    assert [choice.id for choice in question.choices] == [StandardButton.YES, StandardButton.NO]


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


@pytest.mark.parametrize("kind", list(IssueKind))
def test_every_requirement_issue_has_a_sentence_with_nothing_left_unfilled(
    kind: IssueKind,
) -> None:
    """
    Given  an issue of each kind, with and without the facts a sentence may name
    When   the sentence for it is asked
    Then   it is a real sentence: not empty, no placeholder left, no gap where a name belongs
    """
    bare = RequirementIssue(kind)
    named = RequirementIssue(kind, provider="mistral", name="Mistral", models=("m:1b", "n:2b"))

    for issue in (bare, named):
        sentence = issue_text(issue)
        assert sentence.strip()
        assert "{" not in sentence
        assert "  " not in sentence
        assert "''" not in sentence
        assert issue_line(issue) == f"• {sentence}"


def test_an_issue_quotes_what_the_service_answered_when_there_is_an_answer() -> None:
    """
    Given  a refusal and a connection error that carry the service's own words
    When   the sentence for each is asked
    Then   the sentence is those words, not the fallback
    """
    assert issue_text(RequirementIssue(IssueKind.OCR_NOT_ALLOWED, reason="Cloud model.")) == (
        "Cloud model."
    )
    unreachable = RequirementIssue(IssueKind.OLLAMA_UNREACHABLE, reason="✗ Cannot connect")
    assert issue_text(unreachable) == "✗ Cannot connect"


@pytest.mark.parametrize(
    ("models", "sentence"),
    [
        (("a:1b",), "Model 'a:1b' is not downloaded."),
        (("a:1b", "b:2b"), "Models 'a:1b', 'b:2b' are not downloaded."),
        ((), "A model is not downloaded."),
    ],
)
def test_the_models_that_are_not_downloaded_are_named_in_the_right_number(
    models: tuple[str, ...], sentence: str
) -> None:
    """
    Given  one model, several models and none named
    When   the sentence for a missing model is asked
    Then   it is singular, plural or general
    """
    assert issue_text(RequirementIssue(IssueKind.MODEL_MISSING, models=models)) == sentence


@pytest.mark.parametrize(
    ("tag", "pattern", "taken", "sentence"),
    [
        ("1st", "x", (), "Invalid pattern: '1st' is not a tag"),
        ("CITY", "x", ("CITY",), "Invalid pattern: the tag 'CITY' is used twice"),
        ("CITY", "(", (), "Invalid pattern: the pattern of 'CITY' does not compile: "),
    ],
)
def test_a_pattern_row_says_what_is_wrong_with_it(
    tag: str, pattern: str, taken: tuple[str, ...], sentence: str
) -> None:
    """
    Given  an anonymizer pattern with a bad tag, a tag used twice, or no valid expression
    When   the tooltip of its row is asked for
    Then   it starts with the sentence for that problem
    """
    problem = pattern_problem(tag, pattern, taken)

    assert problem is not None
    assert privacy.invalid_pattern_row(problem).startswith(sentence)


@pytest.mark.parametrize(
    ("count", "sentence"),
    [
        (1, "1 pattern has errors — fix before saving."),
        (3, "3 patterns have errors — fix before saving."),
    ],
)
def test_the_invalid_patterns_are_counted_in_the_right_number(count: int, sentence: str) -> None:
    """
    Given  one and three patterns that do not compile
    When   the line above the patterns table is asked for
    Then   it is singular or plural
    """
    assert privacy.invalid_patterns(count) == sentence


def test_the_secondary_pass_says_why_it_adds_nothing() -> None:
    """
    Given  a system language whose model repeats the primary one, and an unknown one
    When   the secondary combo's auto entry is asked for
    Then   it names the model it would add, or says why no second pass happens
    """
    assert privacy.model_auto_secondary("en_core_web_sm", "en_core_web_sm") == (
        "Auto (system language): none, same as document language"
    )
    assert privacy.model_auto_secondary("de_core_news_sm", "en_core_web_sm") == (
        "Auto (system language): de_core_news_sm"
    )
    assert privacy.model_auto_secondary(None, "en_core_web_sm") == (
        "Auto (system language): none, the language is unknown"
    )


def test_the_primary_auto_entry_names_the_model_only_when_it_is_known() -> None:
    """
    Given  a known and an unknown language-specific model
    When   the primary combo's auto entry is asked for
    Then   the known one is named and the unknown one leaves the generic label
    """
    assert privacy.model_auto_primary("de_core_news_sm") == (
        "Auto (language-specific): de_core_news_sm"
    )
    assert privacy.model_auto_primary("") == "Auto (language-specific)"


def test_a_menu_entry_that_opens_a_window_ends_in_an_ellipsis() -> None:
    """
    Given  the name of a Settings page
    When   its entry in the main window's menu is built
    Then   it offers a window: the page's name and an ellipsis
    """
    assert menus.entry(st.PAGE_PRIVACY) == "Privacy…"


def test_the_about_entry_keeps_the_typographic_apostrophe_of_the_name() -> None:
    """
    Given  the app's name and the Help menu's About entry
    When   the entry is built from the name
    Then   it reads with the typographic apostrophe, and the name elsewhere stays plain
    """
    assert menus.ABOUT == "About Teacher’s Teammate…"  # noqa: RUF001
    assert "'" in app_text.NAME


# ── The names of the steps, and the line that describes a step ────────────────

_CLOUD = StageLocation(DataLocation.ONLINE, "OpenAI", region=DataRegion.NON_EU)


def test_a_step_is_named_by_texts_whatever_the_layer_calls_it() -> None:
    """
    Given  the keys the layers use for a step
    When   texts names them
    Then   every step has the one name the teacher reads, and an unknown key speaks for itself
    """
    assert [app_text.stage_title(stage) for stage in app_text.STAGE_TITLES] == [
        "Text recognition",
        "Personal info removal",
        "Proofreading",
        "Content review",
    ]
    assert app_text.stage_title("something-else") == "something-else"


def test_stage_summary_for_leaving_step_says_what_is_received() -> None:
    """
    Given  an online step that receives text with names visible
    When   its summary is built
    Then   the line says where it runs and that names are sent as written
    """
    stage = DataFlowStage(Stage.EVALUATION, _CLOUD, "text", False)
    assert privacy.stage_summary(stage) == (
        "Content review: Online service (OpenAI) · outside the EU — receives text, "
        "names sent as written"
    )


def test_stage_summary_does_not_promise_names_are_hidden() -> None:
    """
    Given  an online step whose names are replaced automatically
    When   its summary is built
    Then   it says "where detected": detection can miss a name
    """
    stage = DataFlowStage(Stage.CORRECTION, _CLOUD, "text", True)
    assert privacy.stage_summary(stage).endswith("names replaced where detected")


def test_the_line_of_a_remembered_answer_names_every_step_that_left() -> None:
    """
    Given  two steps that leave this computer and one that does not
    When   the line is built for the list of remembered answers
    Then   only the two are in it, each with its own summary
    """
    flow = DataFlow(
        (
            DataFlowStage(Stage.OCR, StageLocation(DataLocation.THIS_COMPUTER, "Ollama"), "pages"),
            DataFlowStage(Stage.CORRECTION, _CLOUD, "text"),
            DataFlowStage(
                Stage.EVALUATION,
                StageLocation(DataLocation.ONLINE, "Mistral", region=DataRegion.EU),
                "text",
            ),
        )
    )

    assert privacy.outbound_summary(flow) == (
        "Proofreading: Online service (OpenAI) · outside the EU — receives text, "
        "names sent as written; "
        "Content review: Online service (Mistral) · EU — receives text, names sent as written"
    )


def test_describe_fingerprint_reads_a_v2_fingerprint() -> None:
    """
    Given  a stored v2 fingerprint with a region
    When   it is turned into a readable line
    Then   the line names the place, including the region
    """
    line = privacy.describe_fingerprint("v2|correction:online:eu:Mistral (EU)::1")
    assert line == (
        "Proofreading: Online service (Mistral (EU)) · EU — names replaced where detected"
    )


def test_describe_fingerprint_still_reads_a_v1_fingerprint() -> None:
    """
    Given  an answer stored before the region existed
    When   it is turned into a readable line
    Then   it is understood, without a region in the line
    """
    line = privacy.describe_fingerprint("correction:online:OpenAI::0")
    assert line == "Proofreading: Online service (OpenAI) · outside the EU — names sent as written"


def test_describe_fingerprint_keeps_a_part_it_cannot_parse() -> None:
    """
    Given  a stored part that does not parse (unknown region or place)
    When   it is turned into a readable line
    Then   the part is shown as it is instead of being dropped
    """
    assert privacy.describe_fingerprint("v2|correction:online:mars:OpenAI::1") == (
        "correction:online:mars:OpenAI::1"
    )
    assert privacy.describe_fingerprint("garbage") == "garbage"


def test_describe_fingerprint_names_every_step_of_an_answer() -> None:
    """
    Given  a remembered answer with two steps, one of them on another computer
    When   it is turned into a readable line
    Then   both steps are in it, each with its place and how names are handled
    """
    flow = DataFlow(
        (
            DataFlowStage(Stage.CORRECTION, StageLocation(DataLocation.ONLINE, "Mistral"), "text"),
            DataFlowStage(
                Stage.EVALUATION,
                StageLocation(DataLocation.OTHER_COMPUTER, "Ollama", "school-server"),
                "text",
                personal_info_hidden=True,
            ),
        )
    )

    assert privacy.describe_fingerprint(flow.fingerprint) == (
        "Proofreading: Online service (Mistral) · outside the EU — names sent as written; "
        "Content review: On another computer (school-server) — names replaced where detected"
    )


# ── The instructions a teacher is shown ─────────────────────────────────────


def test_the_instructions_block_puts_a_heading_over_every_step() -> None:
    """
    Given  two proofreading steps and the teacher's notes
    When   the block is built for the read-only view
    Then   each prompt stands under the name of its step, and the notes come last
    """
    block = txt.prompts_block(
        (("correct", "Fix the spelling."), ("explain", "Say why.")), notes="Year 9."
    )

    assert block == (
        "Correct the text\n\nFix the spelling.\n\nExplain the corrections\n\nSay why.\n\nYear 9."
    )


def test_the_instructions_block_gives_a_step_without_a_name_no_heading() -> None:
    """
    Given  a content review, which is one request and has no step, and a step that sends none
    When   the block is built
    Then   the prompt stands alone, and nothing is shown at all for no prompts
    """
    assert txt.prompts_block((("", "Review the content."),)) == "Review the content."
    assert txt.prompts_block(()) == ""
