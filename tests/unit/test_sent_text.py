"""Unit tests for what each outbound step receives, exactly as it is sent."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from teachers_teammate.application.sent_text import (
    SentText,
    SentTexts,
    describe_sent_texts,
)
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

_LOCAL = StageLocation(DataLocation.THIS_COMPUTER, "Ollama")
_EU = StageLocation(DataLocation.ONLINE, "Mistral (EU)", region=DataRegion.EU)
_NON_EU = StageLocation(DataLocation.ONLINE, "OpenAI", region=DataRegion.NON_EU)
_OWN = StageLocation(DataLocation.OTHER_COMPUTER, "Ollama", host="192.168.1.9")


def _flow(*stages: DataFlowStage) -> DataFlow:
    return DataFlow(stages=stages)


def _stage(location: StageLocation, stage: str = STAGE_CORRECTION, *, hidden: bool = False):
    return DataFlowStage(stage, location, "text", personal_info_hidden=hidden)


class _Counter:
    """An anonymizer that records every text it is asked about."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def __call__(self, text: str) -> tuple[str, Mapping[str, str]]:
        self.texts.append(text)
        return f"hidden({text})", {f"[PERSON_{len(self.texts)}]": text}


# ── Local runs ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_a_local_run_sends_nothing() -> None:
    """
    Given  a flow that only reads pages on this computer
    When   describe_sent_texts() is asked what is sent
    Then   there is no section, and the level stays local
    """
    flow = _flow(DataFlowStage(STAGE_OCR, _LOCAL, "page images"))

    report = describe_sent_texts(flow, raw_text="Anna wrote this.")

    assert report.sections == ()
    assert report.leaves_computer is False
    assert report.level is PrivacyLevel.LOCAL


# ── Texts each stage receives ───────────────────────────────────────────────


@pytest.mark.unit
def test_proofreading_receives_the_extracted_text() -> None:
    """
    Given  an online proofreading step that sends names as written
    When   describe_sent_texts() is asked what is sent
    Then   the section holds the extracted text unchanged, and no placeholders
    """
    flow = _flow(_stage(_EU))

    report = describe_sent_texts(flow, raw_text="Anna wrote this.", correction_prompt="Fix it.")

    assert len(report.sections) == 1
    section = report.sections[0]
    assert section.stage.stage == STAGE_CORRECTION
    assert section.text == "Anna wrote this."
    assert section.placeholders == ()
    assert section.prompt == "Fix it."
    assert section.names_hidden is False
    assert section.names_note == "Names are sent as written."


@pytest.mark.unit
def test_content_review_receives_the_proofread_text() -> None:
    """
    Given  a content review step after proofreading
    When   describe_sent_texts() is asked what is sent
    Then   it holds the proofread text, not the extracted one
    """
    flow = _flow(_stage(_EU, STAGE_CORRECTION), _stage(_NON_EU, STAGE_EVALUATION))

    report = describe_sent_texts(
        flow,
        raw_text="Anna wrotes this.",
        correction_text="Anna wrote this.",
        correction_prompt="Fix it.",
        evaluation_prompt="Review it.",
    )

    texts = [(s.stage.stage, s.text) for s in report.sections]
    assert texts == [
        (STAGE_CORRECTION, "Anna wrotes this."),
        (STAGE_EVALUATION, "Anna wrote this."),
    ]
    assert report.sections[1].prompt == "Review it."


@pytest.mark.unit
def test_hidden_names_replace_the_text_and_list_the_placeholders() -> None:
    """
    Given  a proofreading step that hides names first
    When   describe_sent_texts() is asked what is sent
    Then   the anonymized text is shown and its placeholder tokens are reported
    """
    flow = _flow(_stage(_EU, hidden=True))
    counter = _Counter()

    report = describe_sent_texts(flow, raw_text="Anna wrote this.", anonymize=counter)

    section = report.sections[0]
    assert section.text == "hidden(Anna wrote this.)"
    assert section.placeholders == ("[PERSON_1]",)
    assert section.names_hidden is True
    assert section.names_note == "Names are hidden before the text is sent."


@pytest.mark.unit
def test_a_text_is_anonymized_once_for_both_steps() -> None:
    """
    Given  proofreading and content review that both hide names
    When   both receive the same text
    Then   the anonymizer runs once, and both sections show the same hidden text
    """
    flow = _flow(
        _stage(_EU, STAGE_CORRECTION, hidden=True),
        _stage(_EU, STAGE_EVALUATION, hidden=True),
    )
    counter = _Counter()

    report = describe_sent_texts(flow, raw_text="same", correction_text="same", anonymize=counter)

    assert counter.texts == ["same"]
    assert [section.text for section in report.sections] == ["hidden(same)", "hidden(same)"]


@pytest.mark.unit
def test_without_an_anonymizer_the_text_is_reported_as_written() -> None:
    """
    Given  a step that hides names but no anonymizer to do it with
    When   describe_sent_texts() is asked what is sent
    Then   the text is reported as written and no placeholders are claimed
    """
    flow = _flow(_stage(_EU, hidden=True))

    report = describe_sent_texts(flow, raw_text="Anna wrote this.")

    assert report.sections[0].text == "Anna wrote this."
    assert report.sections[0].placeholders == ()


# ── The comparison view ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_preview_extracted_anonymizes_the_extracted_text() -> None:
    """
    Given  a request for the original-vs-anonymized comparison
    When   no step needs the hidden extracted text
    Then   it is anonymized anyway, once, and its placeholders are reported
    """
    flow = _flow(_stage(_NON_EU))
    counter = _Counter()

    report = describe_sent_texts(
        flow, raw_text="Anna wrote this.", anonymize=counter, preview_extracted=True
    )

    assert report.has_preview is True
    assert report.raw_text == "Anna wrote this."
    assert report.anonymized_raw == "hidden(Anna wrote this.)"
    assert report.preview_placeholders == ("[PERSON_1]",)


@pytest.mark.unit
def test_preview_off_has_no_comparison() -> None:
    """
    Given  a run without personal-info removal
    When   describe_sent_texts() is asked what is sent
    Then   the comparison view has nothing to show
    """
    report = describe_sent_texts(_flow(_stage(_EU)), raw_text="Anna wrote this.")

    assert report.has_preview is False
    assert report.anonymized_raw == ""


# ── Wording ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_section_wording_names_the_destination() -> None:
    """
    Given  an online step in the EU
    When   its title, badge and details are read
    Then   they name the step, the provider and the region
    """
    flow = _flow(_stage(_EU))

    section: SentText = describe_sent_texts(flow, raw_text="text").sections[0]

    assert section.title == "Proofreading"
    assert section.badge == "Online service (Mistral (EU)) · EU · names as written"
    assert "Online service (Mistral (EU)) · EU" in section.details


@pytest.mark.unit
def test_a_report_without_sections_reports_nothing_sent() -> None:
    """
    Given  an empty report
    When   its sections and preview are asked about
    Then   nothing is sent and nothing is compared
    """
    report = SentTexts()

    assert report.leaves_computer is False
    assert report.has_preview is False


@pytest.mark.unit
def test_page_images_sent_to_another_computer_leave_the_computer() -> None:
    """
    Given  text recognition on another computer and no text step that leaves
    When   the report is described
    Then   it has no sections but still says something leaves, and names that step
    """
    ocr = _stage(_OWN, STAGE_OCR)

    report = describe_sent_texts(_flow(ocr), raw_text="text")

    assert report.sections == ()
    assert report.page_stages == (ocr,)
    assert report.leaves_computer is True
