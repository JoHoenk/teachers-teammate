"""GUI tests for Settings → Privacy: remembered "don't ask again" answers and the combined page."""

# pylint: disable=W0212,W0621  # protected-access — the tests drive the tab's buttons directly / redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from teachers_teammate.domain.data_location import (
    DataFlow,
    DataFlowStage,
    DataLocation,
    StageLocation,
)
from teachers_teammate.domain.anonymization import DEFAULT_PATTERNS, AnonymizerConfig
from teachers_teammate.texts import review as txt
from teachers_teammate.application.stored_results import RunInProgressError, StoredSummary
from teachers_teammate.gui import _dialogs
from teachers_teammate.gui._privacy_settings import (
    DISPLAY_ANONYMIZED_KEY,
    SETTINGS_KEY,
    FlowAcknowledgements,
    display_anonymized,
    set_display_anonymized,
)
from teachers_teammate.gui.settings._page import (
    FOCUS_DETECTION,
    FOCUS_PATTERNS,
    FOCUS_SENDING,
)
from teachers_teammate.gui.settings._privacy_page import PrivacyPage
from teachers_teammate.texts.privacy import NOTHING_REMEMBERED
from teachers_teammate.texts.privacy import STORED_GROUP, stored_size
from teachers_teammate.texts._types import Answer

from ._gui_harness import settings_context
from teachers_teammate.texts.settings import WHERE_ADDONS


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


# ── storage ─────────────────────────────────────────────────────────────────


def test_a_remembered_answer_is_stored_as_its_fingerprint(acks, settings) -> None:
    """
    Given  an online proofreading flow
    When   "don't ask again" is remembered for it
    Then   the list shows what was agreed to, the settings hold the fingerprint only, and the
           question is skipped for that flow only
    """
    flow = _flow("Mistral")

    acks.remember(flow)

    [entry] = acks.remembered()
    assert entry.fingerprint == flow.fingerprint
    assert entry.summary == (
        "Proofreading: Online service (Mistral) · outside the EU — names sent as written"
    )
    assert flow.fingerprint in acks.fingerprints()
    assert _flow("OpenAI").fingerprint not in acks.fingerprints()
    assert str(settings.store[SETTINGS_KEY]) == flow.fingerprint


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


# ── the page ───────────────────────────────────────────────────────────────


class _Stored:
    """Stands in for the results kept for earlier runs: what the page reports and deletes."""

    def __init__(self, documents: int = 2, size: int = 3_000_000) -> None:
        self.summary_now = StoredSummary(documents=documents, bytes_taken=size)
        self.deleted: list[bool] = []

    def summary(self) -> StoredSummary:
        """Answer how much is stored."""
        return self.summary_now

    def delete(self, *, include_work: bool = False) -> None:
        """Forget everything, and remember what the page asked to be deleted."""
        self.deleted.append(include_work)
        self.summary_now = StoredSummary(documents=0, bytes_taken=0)


def _make_page(
    qtbot,
    acks,
    *,
    focus: str = "",
    config: AnonymizerConfig | None = None,
    display_anonymized: bool = False,
    privacy_available: bool = True,
    stored: _Stored | None = None,
    busy: bool = False,
) -> PrivacyPage:
    """Build the page with no spaCy model discovery (as the anonymizer tests do)."""
    kept = stored if stored is not None else _Stored()
    service = MagicMock()
    service.list_installed_spacy_models.return_value = []  # no spaCy discovery in this test
    # The flat stub answers for every group of services, so the box reaches these here.
    service.summary = kept.summary
    service.delete = kept.delete
    service.busy.return_value = busy
    context = settings_context(
        service,
        acknowledgements=acks,
        anonymizer_config=config if config is not None else AnonymizerConfig(),
        display_anonymized=display_anonymized,
        privacy_available=privacy_available,
    )
    page = PrivacyPage(context)
    qtbot.addWidget(page)
    if focus:
        page.focus(focus)
    page.show()
    # The box measures what is stored off the GUI thread.
    qtbot.waitUntil(lambda: page.stored_results().size_text() != "")
    return page


def _listed(page: PrivacyPage) -> list[str]:
    tab = page.sending_tab()
    return [tab._list.item(i).text() for i in range(tab._list.count())]


@pytest.mark.gui
def test_privacy_page_combines_sending_and_personal_info(qtbot, acks) -> None:
    """
    Given  the Privacy page
    When   it is opened
    Then   it has the sending tab first, followed by the two personal-info tabs of the
           anonymizer rules
    """
    page = _make_page(qtbot, acks)

    tabs = page.rules().tabs()
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    assert page.title == "Privacy"
    assert titles == [
        "Sending data away",
        "Personal info: detection",
        "Personal info: patterns",
    ]


@pytest.mark.gui
@pytest.mark.parametrize(
    ("focus", "expected_tab"),
    [
        (FOCUS_SENDING, "Sending data away"),
        (FOCUS_DETECTION, "Personal info: detection"),
        (FOCUS_PATTERNS, "Personal info: patterns"),
    ],
)
def test_the_page_opens_on_the_requested_section(
    qtbot, acks, focus: str, expected_tab: str
) -> None:
    """
    Given  a deep link asking for the sending, detection or pattern section
    When   the page is focused
    Then   each lands on its own tab
    """
    page = _make_page(qtbot, acks, focus=focus)

    tabs = page.rules().tabs()
    assert tabs.tabText(tabs.currentIndex()) == expected_tab


@pytest.mark.gui
def test_the_detection_tab_offers_hiding_personal_information(qtbot, acks) -> None:
    """
    Given  the Privacy page opened with the hiding switched on
    When   the detection tab is shown
    Then   its first group holds a ticked checkbox with the page's own wording
    """
    page = _make_page(qtbot, acks, display_anonymized=True)

    checkbox = page.hide_personal_checkbox()
    group = page.rules().detection_layout().itemAt(0).widget()
    assert group.title() == "Showing texts"
    assert checkbox.text() == txt.HIDE_PERSONAL_INFO_CHECKBOX
    assert checkbox.isChecked()
    assert checkbox.isEnabled()
    assert checkbox.toolTip() == txt.HIDE_PERSONAL_INFO_TIP
    assert page.display_anonymized()


@pytest.mark.gui
def test_the_checkbox_follows_the_window_state(qtbot, acks) -> None:
    """
    Given  the hiding switched off
    When   the page is opened
    Then   the checkbox is unticked, and what it returns follows a click
    """
    page = _make_page(qtbot, acks, display_anonymized=False)
    checkbox = page.hide_personal_checkbox()
    assert not checkbox.isChecked()
    assert not page.display_anonymized()

    checkbox.setChecked(True)

    assert page.display_anonymized()


@pytest.mark.gui
def test_the_checkbox_is_disabled_without_the_privacy_addon(qtbot, acks) -> None:
    """
    Given  a computer where personal information cannot be hidden
    When   the page is opened with the hiding switched on
    Then   the checkbox is unticked and disabled, and its tooltip points at Add-ons
    """
    page = _make_page(qtbot, acks, display_anonymized=True, privacy_available=False)

    checkbox = page.hide_personal_checkbox()
    assert not checkbox.isEnabled()
    assert not checkbox.isChecked()
    assert WHERE_ADDONS in checkbox.toolTip()


@pytest.mark.gui
def test_the_sending_tab_lists_remembered_answers_in_words(qtbot, acks) -> None:
    """
    Given  one remembered answer
    When   the page is shown
    Then   the list names the step, the service and how names are handled, not a fingerprint
    """
    acks.remember(_flow("Mistral"))

    page = _make_page(qtbot, acks, focus=FOCUS_SENDING)

    assert _listed(page) == [
        "Proofreading: Online service (Mistral) · outside the EU — names sent as written"
    ]
    assert page.sending_tab()._empty.isHidden()


@pytest.mark.gui
def test_with_nothing_remembered_the_tab_says_you_are_always_asked(qtbot, acks) -> None:
    """
    Given  no remembered answers
    When   the sending tab is shown
    Then   it says so (no empty list), and nothing can be taken back
    """
    page = _make_page(qtbot, acks, focus=FOCUS_SENDING)
    tab = page.sending_tab()

    assert not tab._empty.isHidden()
    assert tab._empty.text() == NOTHING_REMEMBERED
    assert tab._list.isHidden()
    assert not tab._forget_all_btn.isEnabled()
    assert not tab._forget_selected_btn.isEnabled()


@pytest.mark.gui
def test_taking_back_an_answer_is_applied_only_with_ok(qtbot, acks) -> None:
    """
    Given  two remembered answers
    When   one is selected and taken back, first without the window's OK, then again with it
           (the window calls apply() on OK)
    Then   nothing changes without OK; OK forgets exactly the selected answer
    """
    mistral, openai = _flow("Mistral"), _flow("OpenAI")
    acks.remember(mistral)
    acks.remember(openai)

    page = _make_page(qtbot, acks, focus=FOCUS_SENDING)
    tab = page.sending_tab()
    tab._list.setCurrentRow(0)  # sorted by summary: Mistral first
    assert tab._forget_selected_btn.isEnabled()
    tab._forget_selected_btn.click()
    assert len(_listed(page)) == 1
    assert acks.fingerprints() == {mistral.fingerprint, openai.fingerprint}  # Cancel: no apply()

    page = _make_page(qtbot, acks, focus=FOCUS_SENDING)
    tab = page.sending_tab()
    tab._list.setCurrentRow(0)
    tab._forget_selected_btn.click()
    page.apply()

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
    page = _make_page(qtbot, acks, focus=FOCUS_SENDING)
    tab = page.sending_tab()

    tab._forget_all_btn.click()

    assert _listed(page) == []
    assert not tab._empty.isHidden()
    assert len(acks.remembered()) == 2  # not yet
    page.apply()
    assert acks.remembered() == []


@pytest.mark.gui
def test_restore_defaults_resets_the_rules_but_not_the_remembered_answers(qtbot, acks) -> None:
    """
    Given  a page with a remembered answer, a custom pattern and the hiding switched off
    When   "Restore defaults → This page" resets it
    Then   the patterns and the hiding choice are back to their defaults while the remembered
           answer stays (it has its own "Ask again" buttons)
    """
    acks.remember(_flow("Mistral"))
    page = _make_page(
        qtbot,
        acks,
        config=AnonymizerConfig(patterns=(("MINE", r"\d+"),)),
        display_anonymized=False,
    )
    assert page.anonymizer_config().patterns == (("MINE", r"\d+"),)

    page.restore_defaults()

    assert page.anonymizer_config().patterns == tuple(DEFAULT_PATTERNS)
    assert page.display_anonymized()
    assert len(_listed(page)) == 1


@pytest.mark.gui
def test_an_invalid_pattern_makes_the_page_unsaveable(qtbot, acks) -> None:
    """
    Given  a page with a valid pattern
    When   the pattern is broken, then fixed
    Then   the page reports itself invalid (so the window disables OK) and valid again
    """
    page = _make_page(qtbot, acks, config=AnonymizerConfig(patterns=(("ID", r"\d+"),)))
    changes: list[bool] = []
    page.validity_changed.connect(changes.append)
    assert page.is_valid()

    page.rules()._table.item(0, 1).setText("[open")
    assert not page.is_valid()
    page.rules()._table.item(0, 1).setText(r"\d+")

    assert page.is_valid()
    assert changes == [False, True]


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


# ── stored results: what the app keeps, deleting it, and how long it stays ───


@pytest.mark.gui
def test_the_page_says_how_much_is_stored(qtbot, acks) -> None:
    """
    Given  the app keeps two documents of three megabytes
    When   the Privacy page is opened
    Then   the box names what is stored in the page's own words, and offers to delete it
    """
    page = _make_page(qtbot, acks)
    box = page.stored_results()

    assert box.title() == STORED_GROUP
    assert box.size_text() == stored_size(2, 3_000_000)
    assert box.delete_button().isEnabled()


@pytest.mark.gui
def test_deleting_asks_first_and_says_what_stays(qtbot, acks) -> None:
    """
    Given  stored results
    When   "Delete stored results…" is pressed and the teacher agrees
    Then   the question names the Word files, the results are deleted without the work, and
           the box says that nothing is stored any more
    """
    kept = _Stored()
    page = _make_page(qtbot, acks, stored=kept)

    with _dialogs.answering(lambda asked: Answer(asked.item.choices[0])) as asked:
        page.stored_results().delete_button().click()

    question = asked[0]
    assert "Word" in question.item.body
    assert "corrections" not in question.item.body
    qtbot.waitUntil(lambda: page.stored_results().size_text() == stored_size(0, 0))
    assert kept.deleted == [False]


@pytest.mark.gui
def test_the_work_choice_is_what_the_question_warns_about(qtbot, acks) -> None:
    """
    Given  the "also delete my corrections and marks" choice ticked
    When   the stored results are deleted
    Then   the question warns that they cannot be brought back, and the work goes too
    """
    kept = _Stored()
    page = _make_page(qtbot, acks, stored=kept)
    page.stored_results().include_work_checkbox().setChecked(True)

    with _dialogs.answering(lambda asked: Answer(asked.item.choices[0])) as asked:
        page.stored_results().delete_button().click()

    question = asked[0]
    assert "corrections" in question.item.body
    qtbot.waitUntil(lambda: kept.deleted == [True])


@pytest.mark.gui
def test_a_refused_question_deletes_nothing(qtbot, acks) -> None:
    """
    Given  a teacher who keeps the results
    When   the question is answered with the default
    Then   nothing is deleted
    """
    kept = _Stored()
    page = _make_page(qtbot, acks, stored=kept)

    with _dialogs.answering(lambda asked: Answer(asked.item.choices[1])):
        page.stored_results().delete_button().click()

    assert kept.deleted == []
    assert page.stored_results().delete_button().isEnabled()
    assert page.stored_results().size_text() == stored_size(2, 3_000_000)


@pytest.mark.gui
def test_a_run_that_started_before_the_click_stops_the_deletion_with_a_notice(qtbot, acks) -> None:
    """
    Given  a run that started after the page was shown, so the deletion is refused
    When   the teacher confirms the deletion
    Then   the teacher is told to wait and the stored results are still there
    """
    kept = _Stored()

    def refuse(*, include_work: bool = False) -> None:
        raise RunInProgressError

    kept.delete = refuse  # ty: ignore[invalid-assignment]
    page = _make_page(qtbot, acks, stored=kept)

    with _dialogs.answering(
        lambda asked: Answer(asked.item.choices[0]) if asked.call == "choose" else None
    ) as asked:
        page.stored_results().delete_button().click()
        qtbot.waitUntil(lambda: any(a.call == "notify" for a in asked))

    assert page.stored_results().size_text() == stored_size(2, 3_000_000)


@pytest.mark.gui
def test_the_button_waits_while_a_run_is_going(qtbot, acks) -> None:
    """
    Given  a run that is writing the stored results right now
    When   the Privacy page is opened
    Then   the deletion is offered but not pressable
    """
    page = _make_page(qtbot, acks, busy=True)

    assert not page.stored_results().delete_button().isEnabled()


@pytest.mark.gui
@pytest.mark.parametrize(("shown", "expected"), [(0, 0), (30, 30), (365, 365)])
def test_the_retention_round_trips_through_the_page(qtbot, acks, shown: int, expected: int) -> None:
    """
    Given  a retention of no days, thirty days or a year
    When   the page shows it and hands its keys back
    Then   the same number comes back, and no days means the results are kept
    """
    page = _make_page(qtbot, acks)

    page.load({"results_keep_days": shown})

    assert page.values()["results_keep_days"] == expected
    assert page.stored_results().keep_checkbox().isChecked() is (shown > 0)
    assert "results_keep_days" in page.owned_keys()


@pytest.mark.gui
def test_restore_defaults_keeps_the_results_again(qtbot, acks) -> None:
    """
    Given  a page showing a retention of thirty days
    When   the defaults of the page are restored
    Then   the retention is off, so nothing expires by itself
    """
    page = _make_page(qtbot, acks)
    page.load({"results_keep_days": 30})

    page.restore_defaults()

    assert page.values()["results_keep_days"] == 0
    assert not page.stored_results().keep_checkbox().isChecked()
