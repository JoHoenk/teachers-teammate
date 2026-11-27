"""GUI tests for showing placeholders instead of names: the View setting and the Check tab."""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path

import pytest
from teachers_teammate.application.queue import QueueRow, StageState
from teachers_teammate.application.anonymization import AnonymizationService
from teachers_teammate.application.providers import ProviderService
from teachers_teammate.application.run import RunService
from teachers_teammate.gui import _main_window as main_window_module
from teachers_teammate.domain.data_location import SentRecord
from teachers_teammate.texts import check as check_text
from teachers_teammate.texts import review as txt
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._privacy_view_controller import PrivacyHooks
from teachers_teammate.gui._readiness_controller import ReadinessController
from teachers_teammate.gui._privacy_settings import DISPLAY_ANONYMIZED_KEY
from teachers_teammate.gui.settings import SettingsPage, SettingsResult
from teachers_teammate.gui.settings._page import FOCUS_DETECTION
from teachers_teammate.gui.settings._privacy_page import PrivacyPage
from teachers_teammate.infrastructure.state_repository import (
    StateRepository,
    compute_file_hash,
)
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from tests.conftest import make_config

from ._gui_harness import install_scripted_settings_window, real_service
from teachers_teammate.texts.settings import WHERE_ADDONS

RAW = "Anna Müller schreibt an Bernd."
CORRECTED = "Anna Müller schreibt einen Brief an Bernd."
ANONYMIZED = "[PERSON_1] schreibt an [PERSON_2]."
MAPPING = {"[PERSON_1]": "Anna Müller", "[PERSON_2]": "Bernd"}

# ── where the setting is chosen: Privacy Settings and the eye button ─────


class _DummySignal:
    def __init__(self) -> None:
        self._subs: list = []

    def connect(self, fn) -> None:
        self._subs.append(fn)

    def emit(self, *args) -> None:
        for fn in self._subs:
            fn(*args)


class _DummyWorker:
    def __init__(self, _config, files=None, app_service=None) -> None:
        self.log_line = _DummySignal()
        self.file_started = _DummySignal()
        self.stage_started = _DummySignal()
        self.ocr_done = _DummySignal()
        self.stage_detail = _DummySignal()
        self.first_token = _DummySignal()
        self.file_done = _DummySignal()
        self.keep_awake_changed = _DummySignal()
        self.run_finished = _DummySignal()
        self.stop_event = threading.Event()
        self.stop_after_file_event = threading.Event()
        self._running = False

    def start(self) -> None:
        self._running = True

    def isRunning(self) -> bool:
        return self._running

    def request_stop(self, *, now: bool) -> None:
        self.stop_after_file_event.set()
        if now:
            self.stop_event.set()

    def wait(self, _msecs: int) -> bool:
        self._running = False
        return True


def _no_update_check(_newer_release, _on_available, *, owner=None) -> None:
    """Never ask GitHub for a newer release."""


class _MemorySettings:
    """Dict-backed stand-in for QSettings."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    def value(self, key: str, default: object = None) -> object:
        return self.store.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.store[key] = value

    def remove(self, key: str) -> None:
        self.store.pop(key, None)


def _open_document(
    main_window,
    monkeypatch,
    tmp_path,
    raw: str,
    corrected: str = "",
    *,
    sent: SentRecord | None = None,
    sent_text: str = "",
) -> None:
    """Store a document with these texts, sent like *sent*, and open it in the Check tab."""
    cfg = make_config(tmp_path)
    cfg.input_dir.mkdir(parents=True, exist_ok=True)
    source = cfg.input_dir / "page.png"
    source.write_text("page", encoding="utf-8")
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    repo = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state")
    state = repo.create(source, compute_file_hash(source))
    repo.save(
        source,
        dataclasses.replace(
            state,
            raw_text=raw,
            correction_text=corrected,
            ocr_done=bool(raw),
            correction_done=bool(corrected),
            correction_sent=sent,
            correction_sent_text=sent_text,
        ),
    )
    main_window._results_table.set_queue(
        [
            QueueRow(
                source_id=str(source),
                name="page.png",
                ocr=StageState.DONE if raw else StageState.NONE,
                correction=StageState.DONE if corrected else StageState.NONE,
                has_text=bool(raw),
            )
        ]
    )
    main_window._check_controller.update_tab()
    main_window._check_controller.load(str(source))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch) -> None:
    """Never probe a real service: the status row and the menu ask in the background."""
    monkeypatch.setattr(RunService, "check_stage_requirements", lambda *_: [])
    monkeypatch.setattr(
        ProviderService, "check_connection", lambda self, **_kw: (False, False, "offline")
    )
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(main_window_module, "check_for_update", _no_update_check)


@pytest.fixture
def settings() -> _MemorySettings:
    return _MemorySettings()


@pytest.fixture
def main_window(qtbot, monkeypatch, settings) -> MainWindow:
    """A window whose View setting lives in memory and whose service can hide names."""
    monkeypatch.setattr(MainWindow, "_settings_store", lambda self: settings)
    monkeypatch.setattr(
        AnonymizationService, "anonymizer_available", lambda self, _lang, _config=None: True
    )
    win = MainWindow(app_service=real_service(), worker_factory=_DummyWorker)
    qtbot.addWidget(win)
    # The Check tab is disabled while no document can be opened, and these tests drive the
    # preview panel that lives in it, so they open the tab themselves.
    win._tabs.setTabEnabled(1, True)
    win._tabs.setCurrentIndex(1)
    return win


@pytest.mark.gui
def test_the_menu_bar_has_no_view_menu(main_window) -> None:
    """
    Given  a main window
    When   its menu bar is read
    Then   there is no "View" menu: the setting lives in Privacy Settings
    """
    titles = [action.text() for action in main_window.menuBar().actions()]
    assert "View" not in titles


@pytest.mark.gui
def test_the_eye_button_switches_the_setting(main_window, settings) -> None:
    """
    Given  a window whose preview panel shows an eye button, with the names hidden
    When   the button is clicked
    Then   the preview shows the names and the choice is stored
    """
    main_window._check_controller.page._hide_names_box.click()

    assert not main_window._check_controller.page._masking.enabled
    assert not main_window._privacy_controller.display_anonymized
    assert settings.value(DISPLAY_ANONYMIZED_KEY) == "no"


@pytest.fixture
def open_privacy(main_window, monkeypatch):
    """Open Settings → Privacy with a scripted window; return the scripted class."""
    monkeypatch.setattr(MainWindow, "_save_settings", lambda self, **_kw: None)

    def _open(*, accepted: bool, choice: bool | None):
        scripted = install_scripted_settings_window(
            monkeypatch,
            accepted=accepted,
            result=SettingsResult(settings={}, display_anonymized=choice),
        )
        main_window.open_settings(SettingsPage.PRIVACY, focus=FOCUS_DETECTION)
        return scripted

    return _open


@pytest.mark.gui
def test_ok_in_privacy_settings_applies_the_checkbox(main_window, settings, open_privacy) -> None:
    """
    Given  a window whose names are hidden and whose Settings window opens with that state
    When   the window closes with OK and an unticked checkbox
    Then   the preview shows the names and the choice is stored
    """
    scripted = open_privacy(accepted=True, choice=False)

    [window] = scripted.instances
    assert window.page is SettingsPage.PRIVACY
    assert window.focus == FOCUS_DETECTION
    assert window.context.display_anonymized is True
    assert window.context.privacy_available is True
    assert not main_window._check_controller.page._masking.enabled
    assert settings.value(DISPLAY_ANONYMIZED_KEY) == "no"


@pytest.mark.gui
def test_ok_in_privacy_settings_keeps_the_saved_choice_while_hiding_is_unavailable(
    main_window, monkeypatch, settings, open_privacy
) -> None:
    """
    Given  a window whose names are hidden, but whose spaCy model has gone missing
    When   the Settings window closes with OK (its disabled checkbox reads "off")
    Then   the saved choice is not overwritten, so hiding comes back once the model does
    """
    monkeypatch.setattr(main_window._privacy_controller, "_available", False)

    open_privacy(accepted=True, choice=False)

    assert main_window._privacy_controller.display_anonymized is True
    assert settings.value(DISPLAY_ANONYMIZED_KEY) is None


@pytest.mark.gui
def test_cancel_in_privacy_settings_leaves_the_setting_alone(
    main_window, settings, open_privacy
) -> None:
    """
    Given  a window whose names are hidden
    When   the Settings window is cancelled although its checkbox was unticked
    Then   the preview still shows placeholders and the stored choice is unchanged
    """
    open_privacy(accepted=False, choice=False)

    assert main_window._check_controller.page._masking.enabled
    assert settings.value(DISPLAY_ANONYMIZED_KEY) is None


@pytest.mark.gui
def test_settings_without_the_privacy_page_leave_the_setting_alone(
    main_window, settings, open_privacy
) -> None:
    """
    Given  a window whose names are hidden
    When   Settings closes with OK without the Privacy page ever having been opened
    Then   the hiding choice is not touched (the page contributes nothing)
    """
    open_privacy(accepted=True, choice=None)

    assert main_window._privacy_controller.display_anonymized is True
    assert settings.value(DISPLAY_ANONYMIZED_KEY) is None


@pytest.mark.gui
def test_the_privacy_checkbox_reflects_a_stored_choice(qtbot, monkeypatch, settings) -> None:
    """
    Given  a stored choice to show the names
    When   Settings → Privacy is opened
    Then   its checkbox is cleared
    """
    settings.setValue(DISPLAY_ANONYMIZED_KEY, "no")
    monkeypatch.setattr(MainWindow, "_settings_store", lambda self: settings)
    monkeypatch.setattr(
        AnonymizationService, "anonymizer_available", lambda self, _lang, _config=None: True
    )
    win = MainWindow(app_service=real_service(), worker_factory=_DummyWorker)
    qtbot.addWidget(win)

    page = _privacy_page(qtbot, win, monkeypatch)

    assert not page.hide_personal_checkbox().isChecked()


@pytest.mark.gui
def test_the_eye_button_and_the_privacy_page_stay_in_step(qtbot, main_window, monkeypatch) -> None:
    """
    Given  a window whose names are hidden
    When   the eye button shows them
    Then   the Privacy page opened afterwards shows a cleared checkbox
    """
    main_window._check_controller.page._hide_names_box.click()

    page = _privacy_page(qtbot, main_window, monkeypatch)

    assert not page.hide_personal_checkbox().isChecked()


def _privacy_page(qtbot, win: MainWindow, monkeypatch: pytest.MonkeyPatch) -> PrivacyPage:
    """Return the Privacy page for *win*'s current state (not shown, no spaCy discovery)."""
    context = win._settings_launcher.context()
    monkeypatch.setattr(
        context.app_service.anonymization, "list_installed_spacy_models", lambda: []
    )  # no spaCy here
    page = PrivacyPage(context)
    qtbot.addWidget(page)
    page.shutdown()
    return page


@pytest.mark.gui
def test_the_eye_button_says_while_the_names_are_being_hidden(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a document whose placeholders are still being worked out
    When   the names are hidden, and then the placeholders arrive
    Then   the toggle says it is working, and says nothing of the kind afterwards
    """
    release = threading.Event()

    def _slow_anonymize(_self, _text, _language, _config=None):
        release.wait(5)
        return ANONYMIZED, MAPPING

    monkeypatch.setattr(AnonymizationService, "anonymize_text", _slow_anonymize)
    main_window.show()
    _open_document(main_window, monkeypatch, tmp_path, RAW)
    box = main_window._check_controller.page._hide_names_box
    banner = main_window._check_controller.page.body.banner

    assert box.text() == txt.HIDE_PERSONAL_INFO_BUTTON_WORKING
    assert box.toolTip() == txt.HIDING_PERSONAL_INFO
    assert banner.isHidden()

    release.set()
    _wait_for(lambda: not main_window._check_controller.page._masking.pending)

    assert box.text() == txt.HIDE_PERSONAL_INFO_BUTTON
    assert box.toolTip() == txt.HIDE_PERSONAL_INFO_BUTTON_TIP


@pytest.mark.gui
def test_the_eye_button_makes_the_texts_editable(
    main_window, settings, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a window whose texts are locked because the names are hidden
    When   the eye button shows the names
    Then   the texts are editable and the menu entry and setting follow
    """
    monkeypatch.setattr(
        AnonymizationService,
        "anonymize_text",
        lambda _self, _text, _language, _config=None: (ANONYMIZED, MAPPING),
    )
    main_window.show()
    _open_document(main_window, monkeypatch, tmp_path, RAW)  # names are hidden by default
    body = main_window._check_controller.page.body
    _wait_for(lambda: not main_window._check_controller.page._masking.pending)
    assert body.read_view.isReadOnly()
    assert body.read_view.toolTip() != txt.SHOW_NAMES_TO_CHANGE

    main_window._check_controller.page._hide_names_box.click()

    assert not main_window._check_controller.page._masking.enabled
    assert not body.read_view.isReadOnly()
    assert body.read_view.toPlainText() == RAW
    assert not main_window._privacy_controller.display_anonymized
    assert settings.value(DISPLAY_ANONYMIZED_KEY) == "no"


@pytest.mark.gui
def test_personal_information_is_hidden_on_the_first_start(main_window, settings) -> None:
    """
    Given  a window started without any stored choice
    When   it is built
    Then   the names are hidden, in the window state and in the preview
    """
    assert settings.value(DISPLAY_ANONYMIZED_KEY) is None
    assert main_window._privacy_controller.display_anonymized
    assert main_window._check_controller.page._masking.enabled


@pytest.mark.gui
def test_a_stored_choice_to_show_the_names_is_kept(qtbot, monkeypatch, settings) -> None:
    """
    Given  a stored choice to show the names
    When   the window is built
    Then   the names are shown: the default only applies without a stored choice
    """
    settings.setValue(DISPLAY_ANONYMIZED_KEY, "no")
    monkeypatch.setattr(MainWindow, "_settings_store", lambda self: settings)
    monkeypatch.setattr(
        AnonymizationService, "anonymizer_available", lambda self, _lang, _config=None: True
    )

    win = MainWindow(app_service=real_service(), worker_factory=_DummyWorker)
    qtbot.addWidget(win)

    assert not win._privacy_controller.display_anonymized
    assert not win._check_controller.page._masking.enabled


@pytest.mark.gui
def test_the_setting_is_restored_from_the_last_session(qtbot, monkeypatch, settings) -> None:
    """
    Given  a stored choice to hide personal information
    When   the window is built
    Then   the preview already shows placeholders
    """
    settings.setValue(DISPLAY_ANONYMIZED_KEY, "yes")
    monkeypatch.setattr(MainWindow, "_settings_store", lambda self: settings)
    monkeypatch.setattr(
        AnonymizationService, "anonymizer_available", lambda self, _lang, _config=None: True
    )

    win = MainWindow(app_service=real_service(), worker_factory=_DummyWorker)
    qtbot.addWidget(win)

    assert win._privacy_controller.display_anonymized
    assert win._check_controller.page._masking.enabled


@pytest.mark.gui
def test_the_string_language_without_spacy_disables_the_choice(
    qtbot, monkeypatch, settings
) -> None:
    """
    Given  a document language whose spaCy model is not installed
    When   the window is built and Settings → Privacy is opened
    Then   the checkbox is disabled and its tooltip points at Settings → Add-ons
    """
    monkeypatch.setattr(MainWindow, "_settings_store", lambda self: settings)
    monkeypatch.setattr(
        AnonymizationService,
        "anonymizer_available",
        lambda self, _lang, _config=None: False,
    )

    win = MainWindow(app_service=real_service(), worker_factory=_DummyWorker)
    qtbot.addWidget(win)

    assert not win._check_controller.page._hide_names_box.isEnabled()
    page = _privacy_page(qtbot, win, monkeypatch)
    checkbox = page.hide_personal_checkbox()
    assert not checkbox.isEnabled()
    assert not checkbox.isChecked()
    assert WHERE_ADDONS in checkbox.toolTip()
    assert win._check_controller.page._masking.enabled is False


@pytest.mark.gui
def test_the_placeholders_arrive_from_the_service(main_window, monkeypatch, tmp_path: Path) -> None:
    """
    Given  a window whose Check tab shows a text that is not proofread yet
    When   the page asks for placeholders
    Then   the service is asked once and the tab shows its result
    """
    calls: list[tuple[str, str]] = []

    def _fake_anonymize(_self, text, language, _config=None):
        calls.append((text, language))
        return ANONYMIZED, MAPPING

    monkeypatch.setattr(AnonymizationService, "anonymize_text", _fake_anonymize)
    _open_document(main_window, monkeypatch, tmp_path, RAW)
    main_window._privacy_controller.apply_display_anonymized(True)

    _wait_for(lambda: not main_window._check_controller.page._masking.pending)

    assert calls == [(RAW, main_window._config_panel.lookup_language())]
    assert main_window._check_controller.page.body.read_view.toPlainText() == ANONYMIZED


def _wait_for(predicate, timeout_ms: int = 5000) -> None:
    """Spin the Qt event loop until *predicate* holds."""
    from PySide6.QtTest import QTest  # noqa: PLC0415

    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(10)
        waited += 10
    assert predicate(), "condition not reached in time"


@pytest.mark.gui
def test_changing_the_language_updates_the_privacy_availability(main_window, monkeypatch) -> None:
    """
    Given  a window whose English model is installed and whose German model is not
    When   the document language is switched to German
    Then   the choice is disabled for it, and enabled again for English
    """
    monkeypatch.setattr(
        AnonymizationService,
        "anonymizer_available",
        lambda self, language, _config=None: language == "English",
    )
    main_window._config_panel._language.setCurrentText("English")
    main_window._privacy_controller.refresh_availability()
    assert main_window._privacy_controller.privacy_available
    assert main_window._check_controller.page._hide_names_box.isEnabled()

    main_window._config_panel._language.setCurrentText("German")

    assert not main_window._privacy_controller.privacy_available
    assert not main_window._check_controller.page._hide_names_box.isEnabled()

    main_window._config_panel._language.setCurrentText("English")

    assert main_window._privacy_controller.privacy_available
    assert main_window._check_controller.page._hide_names_box.isEnabled()


@pytest.mark.gui
def test_a_finished_download_enables_the_setting(main_window, monkeypatch) -> None:
    """
    Given  a window whose model for the document language is not downloaded
    When   a download finishes (packages_changed) and the model is there now
    Then   the setting is available without a restart
    """
    installed = {"yes": False}
    monkeypatch.setattr(
        AnonymizationService,
        "anonymizer_available",
        lambda self, _l, _config=None: installed["yes"],
    )
    main_window._privacy_controller.refresh_availability()
    assert not main_window._check_controller.page._hide_names_box.isEnabled()

    installed["yes"] = True
    main_window._on_packages_changed()

    assert main_window._check_controller.page._hide_names_box.isEnabled()


@pytest.mark.gui
def test_a_round_of_installs_that_outlives_its_window_enables_the_setting(
    main_window, monkeypatch
) -> None:
    """
    Given  a window whose model for the document language is not downloaded, and a round of
           installs that belongs to the window rather than to the assistant that started it
    When   that round finishes with something installed, after its window is gone
    Then   the window looks again and the setting is available, without a restart
    """
    from teachers_teammate.gui._install_round import InstallRound  # noqa: PLC0415

    installed = {"yes": False}
    monkeypatch.setattr(
        AnonymizationService,
        "anonymizer_available",
        lambda self, _l, _config=None: installed["yes"],
    )
    main_window._privacy_controller.refresh_availability()
    assert not main_window._check_controller.page._hide_names_box.isEnabled()

    round_ = InstallRound(main_window._app_service, None, main_window)
    round_.finished.connect(main_window._on_install_round_finished)
    installed["yes"] = True
    round_.finished.emit(True)

    assert main_window._check_controller.page._hide_names_box.isEnabled()


@pytest.mark.gui
def test_only_the_newest_anonymization_reaches_the_page(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  two documents asked for placeholders one after the other, the first one slower
    When   both results arrive
    Then   only the second one is applied, and the first cannot reset the second's state
    """
    release_first = threading.Event()

    def _fake_anonymize(_self, text, _language, _config=None):
        if text == RAW:
            release_first.wait(5)
            return ANONYMIZED, MAPPING
        return "[PERSON_1] ist hier.", {"[PERSON_1]": "Clara West"}

    monkeypatch.setattr(AnonymizationService, "anonymize_text", _fake_anonymize)
    _open_document(main_window, monkeypatch, tmp_path, RAW)
    main_window._privacy_controller.apply_display_anonymized(True)
    _open_document(main_window, monkeypatch, tmp_path, "Clara West ist hier.")

    _wait_for(lambda: not main_window._check_controller.page._masking.pending)
    release_first.set()
    from PySide6.QtTest import QTest  # noqa: PLC0415

    QTest.qWait(100)

    assert main_window._check_controller.page.body.read_view.toPlainText() == "[PERSON_1] ist hier."


# ── Hide this / Don't hide this: the rule the Check tab writes ────────────


def _record_anonymize(monkeypatch) -> list:
    """Patch the anonymizer to answer at once, and return the settings it was asked with."""
    asked: list = []

    def _fake_anonymize(_self, _text, _language, config=None):
        asked.append(config)
        return ANONYMIZED, MAPPING

    monkeypatch.setattr(AnonymizationService, "anonymize_text", _fake_anonymize)
    return asked


def _record_hooks(main_window) -> tuple[list[str], list[bool], list[bool]]:
    """Send the controller's status lines, saves and rule changes to lists, and return them."""
    said: list[str] = []
    saved: list[bool] = []
    changed: list[bool] = []
    main_window._privacy_controller._hooks = PrivacyHooks(
        say=said.append,
        save_settings=lambda: saved.append(True),
        rules_changed=lambda: changed.append(True),
    )
    return said, saved, changed


@pytest.mark.gui
def test_hide_this_writes_the_rule_and_hides_the_text_again(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  an open document whose names are hidden
    When   the Check tab asks for a selected word to be hidden from now on
    Then   the word joins the hide list, which is saved and announced, and the placeholders are
           worked out again with it
    """
    asked = _record_anonymize(monkeypatch)
    said, saved, changed = _record_hooks(main_window)
    _open_document(main_window, monkeypatch, tmp_path, RAW)
    _wait_for(lambda: not main_window._check_controller.page._masking.pending)

    main_window._check_controller.page.hide_requested.emit("Bernd")
    _wait_for(lambda: len(asked) == 2)

    assert asked[-1].hide == ("Bernd",)
    assert asked[-1].patterns == asked[0].patterns
    assert saved == [True]
    assert changed == [True]
    assert said == [check_text.hiding_from_now_on("Bernd")]


@pytest.mark.gui
def test_dont_hide_this_keeps_the_name_in_what_is_sent(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  an open document whose names are hidden
    When   the Check tab asks for a name to be sent as written from now on
    Then   the name joins the keep list and the next anonymizer call is given it
    """
    asked = _record_anonymize(monkeypatch)
    said, saved, changed = _record_hooks(main_window)
    _open_document(main_window, monkeypatch, tmp_path, RAW)
    _wait_for(lambda: not main_window._check_controller.page._masking.pending)

    main_window._check_controller.page.keep_requested.emit("Bernd")
    _wait_for(lambda: len(asked) == 2)

    assert asked[-1].keep == ("Bernd",)
    assert saved == [True]
    assert changed == [True]
    assert said == [check_text.keeping_from_now_on("Bernd")]


@pytest.mark.gui
def test_the_line_says_a_rule_applies_to_the_next_proofreading(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a document whose proofreading was stored with its names hidden
    When   the teacher hides another word
    Then   the line says the rule applies to the next proofreading, and the box keeps the
           string that was sent
    """
    _record_anonymize(monkeypatch)
    said, _saved, _changed = _record_hooks(main_window)
    sent = SentRecord(provider="ollama", names_hidden=1, hiding_on=True)
    _open_document(
        main_window,
        monkeypatch,
        tmp_path,
        RAW,
        CORRECTED,
        sent=sent,
        sent_text=ANONYMIZED,
    )
    _wait_for(lambda: not main_window._check_controller.page._masking.pending)

    main_window._check_controller.page.hide_requested.emit("Bernd")

    assert said == [f"{check_text.hiding_from_now_on('Bernd')} {check_text.NEXT_PROOFREADING_ONLY}"]
    assert main_window._check_controller.page.body.read_view.toPlainText() == ANONYMIZED


@pytest.mark.gui
def test_a_rule_written_while_the_placeholders_are_worked_out_asks_again(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  placeholders for the open text that are still being worked out
    When   the teacher hides another word meanwhile
    Then   they are asked for again with the new rule, and the answer made with the old one is
           not the last word
    """
    release = threading.Event()
    asked: list = []

    def _fake_anonymize(_self, _text, _language, config=None):
        asked.append(config)
        if len(asked) == 1:
            release.wait(5)
        return ANONYMIZED, MAPPING

    monkeypatch.setattr(AnonymizationService, "anonymize_text", _fake_anonymize)
    _record_hooks(main_window)
    _open_document(main_window, monkeypatch, tmp_path, RAW)
    _wait_for(lambda: len(asked) == 1)

    main_window._check_controller.page.hide_requested.emit("Bernd")
    release.set()
    _wait_for(lambda: len(asked) == 2)

    assert asked[0].hide == ()
    assert asked[1].hide == ("Bernd",)


@pytest.mark.gui
def test_the_run_setting_reaches_the_check_tab(main_window) -> None:
    """
    Given  a window whose Check tab holds the run's hiding setting
    When   the setting changes and the availability is looked at again
    Then   the tab knows, so the box of *What was read* never claims to show what will be sent
           while the run sends the names as written
    """
    controller = main_window._privacy_controller
    masking = main_window._check_controller.page._masking

    controller.refresh_availability()
    before = masking.run_hides
    main_window._config_panel.set_anonymization_enabled(not before)
    after = main_window._config_panel.lookup_config().anonymization_enabled
    controller.refresh_availability()

    assert after is not before
    assert masking.run_hides is after
