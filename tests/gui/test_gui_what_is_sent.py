"""GUI tests for the "What is sent for this file" dialog and the stage cell tooltips."""

from __future__ import annotations

import pytest

from teachers_teammate.application.sent_text import SentText, SentTexts, describe_sent_texts
from teachers_teammate.domain.correction_review import CorrectionReview, UnclearNote
from teachers_teammate.domain.data_location import (
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    STAGE_OCR,
    DataFlow,
    DataFlowStage,
    DataLocation,
    DataRegion,
    PrivacyLevel,
    StageLocation,
)
from teachers_teammate.gui._data_flow_summary import level_text, stage_tooltip
from teachers_teammate.gui._results_table import ResultsTable
from teachers_teammate.gui._what_is_sent_dialog import (
    NOTHING_LEAVES,
    TAB_PERSONAL,
    TAB_SENT,
    WhatIsSentDialog,
    highlighted_html,
)

_EU = StageLocation(DataLocation.ONLINE, "Mistral (EU)", region=DataRegion.EU)
_NON_EU = StageLocation(DataLocation.ONLINE, "OpenAI", region=DataRegion.NON_EU)
_OWN = StageLocation(DataLocation.OTHER_COMPUTER, "Ollama", host="192.168.1.9")


def _report(
    *stages: DataFlowStage,
    raw_text: str = "Anna wrote this.",
    correction_text: str = "",
    anonymized: str = "",
    placeholders: tuple[str, ...] = (),
) -> SentTexts:
    """Build a report from a flow, without spaCy: the text is anonymized the way we say."""
    flow = DataFlow(stages=stages)
    hide = (
        (lambda text: (anonymized or text, dict.fromkeys(placeholders, ""))) if anonymized else None
    )
    return describe_sent_texts(
        flow,
        raw_text=raw_text,
        correction_text=correction_text,
        anonymize=hide,
        preview_extracted=bool(anonymized),
        correction_prompt="Fix the spelling.",
        evaluation_prompt="Review the content.",
    )


# ── highlighted_html ────────────────────────────────────────────────────────


@pytest.mark.gui
def test_highlighted_html_marks_placeholders_and_escapes_the_text() -> None:
    """
    Given  a text with a placeholder and an HTML-significant character
    When   highlighted_html() renders it
    Then   the placeholder is wrapped in a marked span and the character is escaped
    """
    html = highlighted_html("Hallo [PERSON_1] & Co", ("[PERSON_1]",))

    assert (
        '<span style="background-color:#fde68a; color:#222222; font-weight:bold;">[PERSON_1]</span>'
        in html
    )
    assert "&amp;" in html
    assert "&lt;" not in html


@pytest.mark.gui
def test_highlighted_html_without_placeholders_keeps_the_text() -> None:
    """
    Given  a text and no placeholders
    When   highlighted_html() renders it
    Then   the text appears unmarked
    """
    html = highlighted_html("Anna wrote this.", ())

    assert "Anna wrote this." in html
    assert "fde68a" not in html


@pytest.mark.gui
def test_highlighted_html_prefers_the_longer_placeholder() -> None:
    """
    Given  two placeholders, one a prefix of the other
    When   highlighted_html() renders the longer one
    Then   it is marked as a whole
    """
    html = highlighted_html("[PERSON_NAME]", ("[PERSON]", "[PERSON_NAME]"))

    assert (
        '<span style="background-color:#fde68a; color:#222222; font-weight:bold;">[PERSON_NAME]</span>'
        in html
    )


# ── The dialog ──────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_lists_one_section_per_outbound_step(qtbot) -> None:
    """
    Given  a report for proofreading and content review that both leave the computer
    When   the dialog is opened
    Then   it has one section per step, each holding the text that step receives
    """
    report = _report(
        DataFlowStage(STAGE_CORRECTION, _EU, "text"),
        DataFlowStage(STAGE_EVALUATION, _NON_EU, "text"),
        correction_text="Anna wrote this.",
    )
    dialog = WhatIsSentDialog(report, file_name="essay.pdf")
    qtbot.addWidget(dialog)

    assert [section.title for section in dialog.sections()] == ["Proofreading", "Content review"]
    assert dialog.shown_texts() == ["Anna wrote this.", "Anna wrote this."]
    assert "essay.pdf" in dialog.windowTitle()


@pytest.mark.gui
def test_dialog_shows_hidden_names_and_the_prompt_on_request(qtbot) -> None:
    """
    Given  a report whose only step hides names first
    When   the dialog is opened and the instructions are shown
    Then   the text is the anonymized one and the prompt becomes visible
    """
    report = _report(
        DataFlowStage(STAGE_CORRECTION, _EU, "text", personal_info_hidden=True),
        anonymized="[PERSON_1] wrote this.",
        placeholders=("[PERSON_1]",),
    )
    dialog = WhatIsSentDialog(report)
    qtbot.addWidget(dialog)

    assert dialog.shown_texts() == ["[PERSON_1] wrote this."]
    assert "Names are hidden before the text is sent." in dialog.section_texts()

    section = dialog.section_widgets()[0]
    prompt_view = section.prompt_view()
    assert prompt_view.isHidden() is True
    section.prompt_button().click()
    assert prompt_view.isHidden() is False
    assert "Fix the spelling." in prompt_view.toPlainText()


@pytest.mark.gui
def test_dialog_offers_the_personal_information_comparison(qtbot) -> None:
    """
    Given  a report with an anonymized extracted text
    When   the dialog is opened
    Then   it has a second tab comparing the original with the anonymized text
    """
    report = _report(
        DataFlowStage(STAGE_CORRECTION, _EU, "text", personal_info_hidden=True),
        anonymized="[PERSON_1] wrote this.",
        placeholders=("[PERSON_1]",),
    )
    dialog = WhatIsSentDialog(report)
    qtbot.addWidget(dialog)

    assert dialog.tabs() == [TAB_SENT, TAB_PERSONAL]
    assert "[PERSON_1] wrote this." in dialog.personal_info_text()


@pytest.mark.gui
def test_dialog_without_anonymization_has_only_the_sent_tab(qtbot) -> None:
    """
    Given  a report from a run that sends names as written
    When   the dialog is opened
    Then   there is no comparison tab
    """
    report = _report(DataFlowStage(STAGE_CORRECTION, _EU, "text"))
    dialog = WhatIsSentDialog(report)
    qtbot.addWidget(dialog)

    assert dialog.tabs() == [TAB_SENT]


@pytest.mark.gui
def test_dialog_says_everything_stays_here_for_a_local_run(qtbot) -> None:
    """
    Given  a flow that reads pages only on this computer
    When   the dialog is opened
    Then   it says everything stays here and lists no section
    """
    report = _report(
        DataFlowStage(STAGE_OCR, StageLocation(DataLocation.THIS_COMPUTER, "Ollama"), "pages")
    )
    dialog = WhatIsSentDialog(report)
    qtbot.addWidget(dialog)

    assert dialog.sections() == ()
    assert NOTHING_LEAVES in dialog.tab_text()
    assert dialog.headline() == level_text(PrivacyLevel.LOCAL)


@pytest.mark.gui
def test_dialog_headline_states_the_privacy_level(qtbot) -> None:
    """
    Given  a report that sends names to a service outside the EU
    When   the dialog is opened
    Then   its headline names that consequence
    """
    report = _report(DataFlowStage(STAGE_CORRECTION, _NON_EU, "text"))
    dialog = WhatIsSentDialog(report)
    qtbot.addWidget(dialog)

    assert dialog.headline() == "Text with names is sent to a service outside the EU."


@pytest.mark.gui
def test_section_details_name_the_destination(qtbot) -> None:
    """
    Given  a report for an own server and one for an online service
    When   the sections are read
    Then   each says where its text goes and how names are handled
    """
    own = SentText(DataFlowStage(STAGE_CORRECTION, _OWN, "text"), "text")
    online = SentText(
        DataFlowStage(STAGE_CORRECTION, _EU, "text", personal_info_hidden=True), "text"
    )

    assert own.badge == "On another computer (192.168.1.9) · names as written"
    assert own.names_hidden is False
    assert online.names_note == "Names are hidden before the text is sent."


# ── Stage tooltips in the results table ─────────────────────────────────────


@pytest.mark.gui
def test_stage_tooltip_says_where_the_text_goes() -> None:
    """
    Given  steps that stay here, go to an own server, or go online in and outside the EU
    When   stage_tooltip() describes each of them
    Then   the line names the destination and how names are handled
    """
    local = DataFlowStage(
        STAGE_CORRECTION, StageLocation(DataLocation.THIS_COMPUTER, "Ollama"), "text"
    )
    own = DataFlowStage(STAGE_CORRECTION, _OWN, "text", personal_info_hidden=True)
    eu = DataFlowStage(STAGE_CORRECTION, _EU, "text", personal_info_hidden=True)
    non_eu = DataFlowStage(STAGE_CORRECTION, _NON_EU, "text")

    assert stage_tooltip(local) == "Stays on this computer (Ollama)"
    assert stage_tooltip(own) == "Sent to another computer (192.168.1.9), names hidden"
    assert stage_tooltip(eu) == "Sent to Mistral (EU), names hidden"
    assert stage_tooltip(non_eu) == "Sent to OpenAI, outside the EU, names as written"


@pytest.mark.gui
def test_results_table_puts_the_stage_tooltip_on_the_stage_cells(qtbot) -> None:
    """
    Given  a queued file and the tooltips of the current settings
    When   set_stage_tooltips() is called
    Then   the proofreading and content-review cells say where their text goes
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["a.pdf"])

    table.set_stage_tooltips(
        {"correction": "Sent to Mistral (EU), names hidden", "evaluation": "Stays here"}
    )

    correction = table.item(0, ResultsTable._COL_CORRECTION)
    evaluation = table.item(0, ResultsTable._COL_EVALUATION)
    assert correction is not None and evaluation is not None
    assert correction.toolTip() == "Sent to Mistral (EU), names hidden"
    assert evaluation.toolTip() == "Stays here"


@pytest.mark.gui
def test_results_table_keeps_its_own_message_below_the_stage_line(qtbot) -> None:
    """
    Given  a file whose proofreading found unclear expressions
    When   the stage tooltip is set afterwards
    Then   the cell shows the stage line and the unclear count, neither replacing the other
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["a.pdf"])
    table.mark_cached("a.pdf", "", "", "", "", ocr_done=True, correction_done=True)
    table.set_correction_review(
        "a.pdf",
        CorrectionReview(unclear=(UnclearNote("weil", "why", "because"),)),
        "",
    )

    table.set_stage_tooltips({"correction": "Sent to Mistral (EU), names hidden"})

    correction = table.item(0, ResultsTable._COL_CORRECTION)
    assert correction is not None
    tooltip = correction.toolTip()
    assert tooltip.startswith("Sent to Mistral (EU), names hidden")
    assert "unclear" in tooltip.lower()


# ── The context menu entry ──────────────────────────────────────────────────


@pytest.mark.gui
def test_the_context_menu_asks_what_is_sent(qtbot, monkeypatch) -> None:
    """
    Given  a results row for a recognized file
    When   the context menu is opened on it
    Then   the entry says "What is sent for this file…" and the preview entry is gone
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["a.pdf"], ["src-a"])
    entries: list[str] = []

    class _FakeMenu:
        """A menu that records what was added instead of opening a modal loop."""

        def __init__(self, *_args) -> None:
            self._entries: list[str] = []

        def addAction(self, text: str) -> object:
            self._entries.append(text)
            return text

        def addMenu(self, menu: object) -> None:
            self._entries.append(getattr(menu, "title", lambda: "")())

        def addSeparator(self) -> None:
            return None

        def exec(self, *_args) -> None:
            entries.extend(self._entries)

    monkeypatch.setattr("teachers_teammate.gui._results_table.QMenu", _FakeMenu)
    monkeypatch.setattr(
        "teachers_teammate.gui._results_table.build_export_menu", lambda _menu: ("Export", {})
    )
    table.selectRow(0)
    first = table.item(0, 0)
    assert first is not None
    table._on_context_menu_requested(table.visualItemRect(first).center())

    assert "What is sent for this file…" in entries
    assert not any("Personal Information Preview" in entry for entry in entries)


@pytest.mark.gui
def test_every_sent_text_of_a_report_names_its_stage() -> None:
    """
    Given  a report built from a flow with one step
    When   its section is inspected
    Then   the section is a SentText with the stage's own title
    """
    report = _report(DataFlowStage(STAGE_CORRECTION, _EU, "text"))

    assert all(isinstance(section, SentText) for section in report.sections)
    assert report.sections[0].title == "Proofreading"


@pytest.mark.gui
def test_dialog_names_a_step_that_sends_page_images(qtbot) -> None:
    """
    Given  text recognition on another computer and proofreading off
    When   the dialog is opened
    Then   it does not claim that everything stays here, and says the pages are sent
    """
    report = _report(DataFlowStage(STAGE_OCR, _OWN, "page images"))
    dialog = WhatIsSentDialog(report)
    qtbot.addWidget(dialog)

    text = dialog.tab_text()

    assert NOTHING_LEAVES not in text
    assert "page images" in text
    assert "Text recognition" in text
