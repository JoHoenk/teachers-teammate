"""GUI tests for Privacy Settings: remembered "don't ask again" answers and the combined dialog."""

# pylint: disable=W0212,W0621  # protected-access — the tests drive the tab's buttons directly / redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QDialogButtonBox

from teachers_teammate.domain.data_location import (
    DataFlow,
    DataFlowStage,
    DataLocation,
    StageLocation,
    describe_fingerprint,
)
from teachers_teammate.gui._privacy_settings import (
    DISPLAY_ANONYMIZED_KEY,
    SECTION_DETECTION,
    SECTION_SENDING,
    SETTINGS_KEY,
    FlowAcknowledgements,
    display_anonymized,
    set_display_anonymized,
)
from teachers_teammate.gui._privacy_settings_dialog import (
    NOTHING_REMEMBERED,
    PrivacySettingsDialog,
)
from teachers_teammate.infrastructure.anonymizer import AnonymizerConfig


class _MemorySettings:
    """Dict-backed stand-in for QSettings (value / setValue / remove)."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    def value(self, key: str, default: object = None) -> object:
        return self.store.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.store[key] = value

    def remove(self, key: str) -> None:
        self.store.pop(key, None)


def _flow(service: str = "Mistral", *, hidden: bool = False, stage: str = "correction") -> DataFlow:
    """A run whose one step goes to an online service."""
    online = StageLocation(DataLocation.ONLINE, service)
    return DataFlow((DataFlowStage(stage, online, "text", personal_info_hidden=hidden),))


@pytest.fixture
def settings() -> _MemorySettings:
    return _MemorySettings()


@pytest.fixture
def acks(settings: _MemorySettings) -> FlowAcknowledgements:
    return FlowAcknowledgements(lambda: settings)


# ── the readable line of a remembered answer ───────────────────────────────


def test_a_fingerprint_without_summary_is_described_in_words() -> None:
    """
    Given  the fingerprint an earlier version stored (nothing readable next to it)
    When   describe_fingerprint() is called
    Then   it says the step, where it runs and whether names were replaced
    """
    flow = DataFlow(
        (
            DataFlowStage("correction", StageLocation(DataLocation.ONLINE, "Mistral"), "text"),
            DataFlowStage(
                "evaluation",
                StageLocation(DataLocation.OTHER_COMPUTER, "Ollama", "school-server"),
                "text",
                personal_info_hidden=True,
            ),
        )
    )

    assert describe_fingerprint(flow.fingerprint) == (
        "Proofreading: Online service (Mistral) · outside the EU — names sent as written; "
        "Content review: On another computer (school-server) — names replaced where detected"
    )


@pytest.mark.parametrize("fingerprint", ["garbage", "a:b", "correction:unknown_place:X::0"])
def test_a_fingerprint_that_does_not_parse_is_shown_as_it_is(fingerprint: str) -> None:
    """
    Given  a stored value that is not a fingerprint this version understands
    When   describe_fingerprint() is called
    Then   it is returned unchanged, so nothing remembered is ever hidden from the list
    """
    assert describe_fingerprint(fingerprint) == fingerprint


# ── storage ─────────────────────────────────────────────────────────────────


def test_a_remembered_answer_is_stored_with_a_readable_summary(acks, settings) -> None:
    """
    Given  an online proofreading flow
    When   "don't ask again" is remembered for it
    Then   the list shows what was agreed to, and the question is skipped for that flow only
    """
    flow = _flow("Mistral")

    acks.remember(flow)

    [entry] = acks.remembered()
    assert entry.fingerprint == flow.fingerprint
    assert entry.summary == (
        "Proofreading: Online service (Mistral) · outside the EU — receives text, "
        "names sent as written"
    )
    assert flow.fingerprint in acks.fingerprints()
    assert _flow("OpenAI").fingerprint not in acks.fingerprints()
    assert "\t" in str(settings.store[SETTINGS_KEY])


def test_answers_stored_by_an_earlier_version_are_still_listed_and_honoured(acks, settings) -> None:
    """
    Given  a settings value written by an earlier version (fingerprints only, one per line)
    When   the answers are read
    Then   both are remembered and each is listed in words
    """
    first, second = _flow("Mistral"), _flow("OpenAI")
    settings.store[SETTINGS_KEY] = f"{first.fingerprint}\n{second.fingerprint}"

    assert acks.fingerprints() == {first.fingerprint, second.fingerprint}
    summaries = [entry.summary for entry in acks.remembered()]
    assert (
        "Proofreading: Online service (Mistral) · outside the EU — names sent as written"
        in summaries
    )
    assert (
        "Proofreading: Online service (OpenAI) · outside the EU — names sent as written"
        in summaries
    )


def test_keeping_only_some_answers_forgets_the_rest_and_the_last_removes_the_key(
    acks, settings
) -> None:
    """
    Given  two remembered answers
    When   only one is kept, then none
    Then   the other is asked about again, and with none left the setting disappears
    """
    mistral, openai = _flow("Mistral"), _flow("OpenAI")
    acks.remember(mistral)
    acks.remember(openai)

    acks.keep_only([mistral.fingerprint])
    assert acks.fingerprints() == {mistral.fingerprint}

    acks.keep_only([])
    assert acks.fingerprints() == set()
    assert SETTINGS_KEY not in settings.store


def test_forgetting_all_asks_again_for_everything(acks, settings) -> None:
    """
    Given  remembered answers
    When   all are forgotten
    Then   nothing is remembered and the setting is removed
    """
    acks.remember(_flow("Mistral"))

    acks.forget_all()

    assert acks.remembered() == []
    assert SETTINGS_KEY not in settings.store


# ── the dialog ──────────────────────────────────────────────────────────────


def _make_dialog(qtbot, acks, *, section: str = SECTION_DETECTION) -> PrivacySettingsDialog:
    """Build the dialog with spaCy model fetching suppressed (as the anonymizer tests do)."""
    with patch(
        "teachers_teammate.gui._anonymizer_config_dialog.ProcessingApplicationService"
    ) as service:
        service.return_value.list_installed_spacy_models.return_value = []
        dlg = PrivacySettingsDialog(
            "English", AnonymizerConfig(), MagicMock(), acknowledgements=acks, section=section
        )
    dlg._spacy_fetch_thread.quit()
    dlg._spacy_fetch_thread.wait(500)
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


def _listed(dlg: PrivacySettingsDialog) -> list[str]:
    tab = dlg.sending_tab()
    return [tab._list.item(i).text() for i in range(tab._list.count())]


@pytest.mark.gui
def test_privacy_settings_combines_sending_and_personal_info_in_one_window(qtbot, acks) -> None:
    """
    Given  the Privacy Settings dialog
    When   it is opened
    Then   it is titled Privacy Settings and has the sending tab first, followed by the two
           personal-info tabs of the anonymizer dialog
    """
    dlg = _make_dialog(qtbot, acks)

    titles = [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]
    assert dlg.windowTitle() == "Privacy Settings"
    assert titles == [
        "Sending data away",
        "Personal info: detection",
        "Personal info: patterns",
    ]


@pytest.mark.gui
@pytest.mark.parametrize(
    ("section", "expected_tab"),
    [(SECTION_SENDING, "Sending data away"), (SECTION_DETECTION, "Personal info: detection")],
)
def test_the_dialog_opens_on_the_requested_section(
    qtbot, acks, section: str, expected_tab: str
) -> None:
    """
    Given  the menu asks for the sending section and the personal-info button for detection
    When   the dialog opens
    Then   each lands on its own tab
    """
    dlg = _make_dialog(qtbot, acks, section=section)

    assert dlg._tabs.tabText(dlg._tabs.currentIndex()) == expected_tab


@pytest.mark.gui
def test_the_sending_tab_lists_remembered_answers_in_words(qtbot, acks) -> None:
    """
    Given  one remembered answer
    When   the dialog is shown
    Then   the list names the step, the service and what is sent, not a fingerprint
    """
    acks.remember(_flow("Mistral"))

    dlg = _make_dialog(qtbot, acks, section=SECTION_SENDING)

    assert _listed(dlg) == [
        "Proofreading: Online service (Mistral) · outside the EU — receives text, "
        "names sent as written"
    ]
    assert dlg.sending_tab()._empty.isHidden()


@pytest.mark.gui
def test_with_nothing_remembered_the_tab_says_you_are_always_asked(qtbot, acks) -> None:
    """
    Given  no remembered answers
    When   the sending tab is shown
    Then   it says so (no empty list), and nothing can be taken back
    """
    dlg = _make_dialog(qtbot, acks, section=SECTION_SENDING)
    tab = dlg.sending_tab()

    assert not tab._empty.isHidden()
    assert tab._empty.text() == NOTHING_REMEMBERED
    assert tab._list.isHidden()
    assert not tab._forget_all_btn.isEnabled()
    assert not tab._forget_selected_btn.isEnabled()


@pytest.mark.gui
def test_taking_back_an_answer_is_applied_only_with_ok(qtbot, acks) -> None:
    """
    Given  two remembered answers
    When   one is selected and taken back, first cancelling, then again and pressing OK
    Then   cancelling changes nothing; OK forgets exactly the selected answer
    """
    mistral, openai = _flow("Mistral"), _flow("OpenAI")
    acks.remember(mistral)
    acks.remember(openai)

    dlg = _make_dialog(qtbot, acks, section=SECTION_SENDING)
    tab = dlg.sending_tab()
    tab._list.setCurrentRow(0)  # sorted by summary: Mistral first
    assert tab._forget_selected_btn.isEnabled()
    tab._forget_selected_btn.click()
    assert len(_listed(dlg)) == 1
    dlg.reject()
    assert acks.fingerprints() == {mistral.fingerprint, openai.fingerprint}

    dlg = _make_dialog(qtbot, acks, section=SECTION_SENDING)
    tab = dlg.sending_tab()
    tab._list.setCurrentRow(0)
    tab._forget_selected_btn.click()
    dlg._buttons.button(QDialogButtonBox.StandardButton.Ok).click()

    assert acks.fingerprints() == {openai.fingerprint}


@pytest.mark.gui
def test_ask_again_for_all_clears_the_list_and_is_applied_with_ok(qtbot, acks) -> None:
    """
    Given  two remembered answers
    When   "Ask again for all" is pressed and OK confirmed
    Then   the list is empty at once and nothing is remembered afterwards
    """
    acks.remember(_flow("Mistral"))
    acks.remember(_flow("OpenAI"))
    dlg = _make_dialog(qtbot, acks, section=SECTION_SENDING)
    tab = dlg.sending_tab()

    tab._forget_all_btn.click()

    assert _listed(dlg) == []
    assert not tab._empty.isHidden()
    dlg._buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    assert acks.remembered() == []


# ── the "hide personal information" view setting ─────────────────────────────


@pytest.mark.parametrize(
    ("stored", "expected"),
    [(None, True), ("", True), ("yes", True), ("true", True), ("no", False), ("off", False)],
)
def test_display_anonymized_defaults_to_hidden(stored, expected) -> None:
    """
    Given  a settings store with no answer, or with a stored answer
    When   the view setting is read
    Then   names are hidden unless the user stored an answer that shows them
    """
    settings = _MemorySettings()
    if stored is not None:
        settings.setValue(DISPLAY_ANONYMIZED_KEY, stored)

    assert display_anonymized(lambda: settings) is expected


def test_display_anonymized_remembers_showing_the_names() -> None:
    """
    Given  the default of hiding names
    When   the user turns hiding off and the setting is read again
    Then   the stored "no" wins over the default
    """
    settings = _MemorySettings()

    set_display_anonymized(lambda: settings, False)

    assert display_anonymized(lambda: settings) is False
