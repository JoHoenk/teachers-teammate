"""GUI tests for showing placeholders instead of names: the View setting and the panel."""
# pylint: disable=W0212,W0613,W0621  # protected-access — the tests drive the panel and the menu directly / unused-argument, redefined-outer-name — pytest fixtures are injected by parameter name

from __future__ import annotations

import threading

import pytest
from PySide6.QtGui import QPixmap

from teachers_teammate.application.service import ProcessingApplicationService
from teachers_teammate.gui import _main_window as main_window_module
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._preview_panel import PreviewPanel
from teachers_teammate.gui._privacy_settings import DISPLAY_ANONYMIZED_KEY

RAW = "Anna Müller schreibt an Bernd."
CORRECTED = "Anna Müller schreibt einen Brief an Bernd."
ANONYMIZED = "[PERSON_1] schreibt an [PERSON_2]."
MAPPING = {"[PERSON_1]": "Anna Müller", "[PERSON_2]": "Bernd"}


def _pixmap() -> QPixmap:
    pix = QPixmap(20, 20)
    pix.fill()
    return pix


def _loaded_panel(qtbot) -> PreviewPanel:
    """A shown panel holding one file with an extracted and a proofread text."""
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.load(_pixmap(), RAW, CORRECTED, "Review notes for Anna Müller.")
    return panel


# ── the panel ─────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_turning_the_setting_on_masks_every_tab(qtbot) -> None:
    """
    Given  a panel showing a text with names
    When   the placeholders arrive and the setting is switched on
    Then   the extracted text, the proofread text and the review show placeholders
    """
    panel = _loaded_panel(qtbot)
    panel.set_anonymization(ANONYMIZED, MAPPING, raw_text=RAW)

    panel.set_display_anonymized(True)

    assert panel.display_anonymized()
    assert panel._ocr_text.toPlainText() == ANONYMIZED
    assert "Anna Müller" not in panel._correction_text.toPlainText()
    assert "Bernd" not in panel._correction_text.toPlainText()
    assert "[PERSON_1]" in panel._correction_text.toPlainText()
    assert "Anna Müller" not in panel._evaluation_text.toPlainText()


@pytest.mark.gui
def test_turning_the_setting_off_shows_the_names_again(qtbot) -> None:
    """
    Given  a panel showing placeholders
    When   the setting is switched off
    Then   the stored texts are back unchanged
    """
    panel = _loaded_panel(qtbot)
    panel.set_anonymization(ANONYMIZED, MAPPING, raw_text=RAW)
    panel.set_display_anonymized(True)

    panel.set_display_anonymized(False)

    assert panel._ocr_text.toPlainText() == RAW
    assert panel._correction_text.toPlainText() == CORRECTED


@pytest.mark.gui
def test_the_texts_cannot_be_edited_while_names_are_hidden(qtbot) -> None:
    """
    Given  a panel showing placeholders
    When   the text views are asked whether they are editable
    Then   they are read-only and no edit signal is emitted
    """
    panel = _loaded_panel(qtbot)
    panel.set_anonymization(ANONYMIZED, MAPPING, raw_text=RAW)
    edited: list[str] = []
    panel.ocr_text_edited.connect(edited.append)

    panel.set_display_anonymized(True)
    panel._ocr_text.setPlainText("Ein ganz anderer Text.")

    assert panel._ocr_text.isReadOnly()
    assert panel._correction_text.isReadOnly()
    assert edited == []
    assert panel._stored_raw == RAW


@pytest.mark.gui
def test_the_setting_asks_for_the_placeholders_once(qtbot) -> None:
    """
    Given  a panel with a text and no placeholders yet
    When   the setting is switched on twice
    Then   the panel asks for the placeholders only once
    """
    panel = _loaded_panel(qtbot)
    asked: list[str] = []
    panel.anonymization_needed.connect(asked.append)

    panel.set_display_anonymized(True)
    panel.set_display_anonymized(True)

    assert asked == [RAW]


@pytest.mark.gui
def test_loading_a_file_asks_for_its_placeholders(qtbot) -> None:
    """
    Given  a panel whose setting is on and whose file has just been replaced
    When   a new file is loaded
    Then   the panel asks for the placeholders of the new text and shows a note meanwhile
    """
    panel = _loaded_panel(qtbot)
    panel.set_anonymization(ANONYMIZED, MAPPING, raw_text=RAW)
    panel.set_display_anonymized(True)
    asked: list[str] = []
    panel.anonymization_needed.connect(asked.append)

    panel.load(_pixmap(), "Clara West trägt vor.", "", "")

    assert asked == ["Clara West trägt vor."]
    assert "Clara West" not in panel._ocr_text.toPlainText()
    assert panel._privacy_note.isVisible()


@pytest.mark.gui
def test_placeholders_of_another_text_are_dropped(qtbot) -> None:
    """
    Given  a panel showing one text while its placeholders are worked out
    When   the result belongs to a text that is no longer shown
    Then   it is dropped and the panel keeps waiting
    """
    panel = _loaded_panel(qtbot)
    panel.set_display_anonymized(True)

    panel.set_anonymization(ANONYMIZED, MAPPING, raw_text="Ein anderer Text.")

    assert panel._anon_ready is False


@pytest.mark.gui
def test_a_failed_anonymization_keeps_the_names_hidden(qtbot) -> None:
    """
    Given  a panel whose setting is on
    When   hiding the names fails
    Then   the note says why and the names are still not shown
    """
    panel = _loaded_panel(qtbot)
    panel.set_display_anonymized(True)

    panel.set_anonymization("", {}, raw_text=RAW, error="spaCy model missing: de_core_news_sm")

    assert "Anna Müller" not in panel._ocr_text.toPlainText()
    assert "spacy" in panel._privacy_note.text().lower()
    assert panel._privacy_note.isVisible()


@pytest.mark.gui
def test_the_placeholders_are_highlighted_not_the_names(qtbot) -> None:
    """
    Given  a panel with the placeholder map {placeholder: original name}
    When   the setting is switched on
    Then   the plain views mark the placeholders and nothing else
    """
    panel = _loaded_panel(qtbot)
    panel.set_anonymization(ANONYMIZED, MAPPING, raw_text=RAW)

    panel.set_display_anonymized(True)

    marked = [sel.cursor.selectedText() for sel in panel._ocr_text.extraSelections()]
    assert marked == ["[PERSON_1]", "[PERSON_2]"]


@pytest.mark.gui
def test_typing_just_before_hiding_is_saved_not_dropped(qtbot) -> None:
    """
    Given  uncommitted typing in the formatted view of a Markdown file
    When   the setting is switched on before the typing pause has ended
    Then   the typed text is stored and emitted once, and survives the masking
    """
    from PySide6.QtGui import QTextCursor  # noqa: PLC0415

    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.load(_pixmap(), "Anna Müller schrieb", "", "", text_format="markdown")
    emitted: list[str] = []
    panel.ocr_text_edited.connect(emitted.append)
    cursor = panel._ocr_view.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    cursor.insertText("Heute: ")

    panel.set_display_anonymized(True)

    assert emitted == ["Heute: Anna Müller schrieb"]
    assert panel._stored_raw == "Heute: Anna Müller schrieb"


@pytest.mark.gui
def test_an_edited_text_gets_new_placeholders(qtbot) -> None:
    """
    Given  placeholders were worked out, then hiding was turned off and the text edited
    When   hiding is turned on again
    Then   the old placeholders are not used: the panel asks for the edited text
    """
    panel = _loaded_panel(qtbot)
    panel.set_anonymization(ANONYMIZED, MAPPING, raw_text=RAW)
    panel.set_display_anonymized(True)
    panel.set_display_anonymized(False)
    panel._ocr_text.setPlainText(RAW + " Clara West kam dazu.")
    asked: list[str] = []
    panel.anonymization_needed.connect(asked.append)

    panel.set_display_anonymized(True)

    assert asked == [RAW + " Clara West kam dazu."]
    assert "Clara West" not in panel._ocr_text.toPlainText()


@pytest.mark.gui
def test_a_text_without_content_does_not_wait_for_placeholders(qtbot) -> None:
    """
    Given  a file that has no extracted text yet
    When   the setting is switched on
    Then   the panel does not show "Hiding personal information…" forever
    """
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.load(_pixmap(), "", "", "")

    panel.set_display_anonymized(True)

    assert panel._anon_ready is True
    assert "Hiding personal information" not in panel._ocr_text.toPlainText()


@pytest.mark.gui
def test_a_name_the_detector_missed_is_hidden_in_every_tab(qtbot) -> None:
    """
    Given  a detector result that hid only the first of two mentions of a name
    When   the setting is switched on
    Then   the extracted and the proofread text hide both, and the Changes tab shows no
           change that would reveal the name
    """
    raw = "Anna Müller kam. Später ging Anna Müller."
    corrected = "Anna Müller kam. Später ging Anna Müller heim."
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.load(_pixmap(), raw, corrected, "")
    panel.set_anonymization(
        "[PERSON_1] kam. Später ging Anna Müller.", {"[PERSON_1]": "Anna Müller"}, raw_text=raw
    )

    panel.set_display_anonymized(True)

    assert "Anna" not in panel._ocr_text.toPlainText()
    assert "Anna" not in panel._correction_text.toPlainText()
    assert "Anna" not in panel._review_diff.toPlainText()


@pytest.mark.gui
def test_the_eye_button_mirrors_the_setting(qtbot) -> None:
    """
    Given  a panel with an enabled eye button
    When   the button is clicked
    Then   the panel announces the new value instead of applying it itself
    """
    panel = _loaded_panel(qtbot)
    announced: list[bool] = []
    panel.display_anonymized_changed.connect(announced.append)

    panel._hide_btn.click()

    assert announced == [True]
    assert panel.display_anonymized() is False


@pytest.mark.gui
def test_without_the_add_on_the_setting_stays_off(qtbot) -> None:
    """
    Given  a panel whose personal-info removal is not installed
    When   the setting is switched on
    Then   the panel stays unmasked and the eye button is disabled
    """
    panel = _loaded_panel(qtbot)

    panel.set_display_anonymized_available(False)
    panel.set_display_anonymized(True)

    assert panel.display_anonymized() is False
    assert not panel._hide_btn.isEnabled()
    assert panel._ocr_text.toPlainText() == RAW


@pytest.mark.gui
def test_changed_settings_produce_new_placeholders(qtbot) -> None:
    """
    Given  a panel showing placeholders from the current anonymizer settings
    When   the settings change
    Then   the old placeholders are dropped and the text is asked for again
    """
    panel = _loaded_panel(qtbot)
    panel.set_anonymization(ANONYMIZED, MAPPING, raw_text=RAW)
    panel.set_display_anonymized(True)
    asked: list[str] = []
    panel.anonymization_needed.connect(asked.append)

    panel.invalidate_anonymization()

    assert asked == [RAW]
    assert panel._anon_ready is False


# ── the View menu ─────────────────────────────────────────────────────────


class _DummySignal:
    def __init__(self) -> None:
        self._subs: list = []

    def connect(self, fn) -> None:
        self._subs.append(fn)

    def emit(self, *args) -> None:
        for fn in self._subs:
            fn(*args)


class _DummyWorker:
    def __init__(self, _config, selected_source_paths=None, app_service=None) -> None:
        self.log_line = _DummySignal()
        self.file_started = _DummySignal()
        self.stage_started = _DummySignal()
        self.ocr_done = _DummySignal()
        self.file_done = _DummySignal()
        self.finished_with_code = _DummySignal()
        self.stop_event = threading.Event()
        self._running = False

    def start(self) -> None:
        self._running = True

    def isRunning(self) -> bool:
        return self._running


class _DummyUpdateThread:
    def __init__(self, _parent=None) -> None:
        self.update_available = _DummySignal()

    def start(self) -> None:
        pass


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


@pytest.fixture(autouse=True)
def _no_network(monkeypatch) -> None:
    """Never probe a real service: the status row and the menu ask in the background."""
    monkeypatch.setattr(ProcessingApplicationService, "check_stage_requirements", lambda *_: [])
    monkeypatch.setattr(
        ProcessingApplicationService,
        "check_connection",
        lambda self, **_kw: (False, False, "offline"),
    )
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(main_window_module, "UpdateCheckThread", _DummyUpdateThread)


@pytest.fixture
def settings() -> _MemorySettings:
    return _MemorySettings()


@pytest.fixture
def main_window(qtbot, monkeypatch, settings) -> MainWindow:
    """A window whose View setting lives in memory and whose service can hide names."""
    monkeypatch.setattr(MainWindow, "_settings_store", lambda self: settings)
    monkeypatch.setattr(
        ProcessingApplicationService, "anonymizer_available", lambda self, _lang: True
    )
    win = MainWindow(worker_factory=_DummyWorker)
    qtbot.addWidget(win)
    return win


@pytest.mark.gui
def test_the_view_menu_switches_the_setting(main_window, settings) -> None:
    """
    Given  a window with the "Hide personal information in texts" entry
    When   the entry is checked
    Then   the preview shows placeholders and the choice is stored
    """
    assert main_window._hide_personal_act.isCheckable()
    assert main_window._hide_personal_act.isEnabled()

    main_window._hide_personal_act.trigger()

    assert main_window._preview.display_anonymized()
    assert settings.value(DISPLAY_ANONYMIZED_KEY) == "yes"


@pytest.mark.gui
def test_the_eye_button_checks_the_menu_entry(main_window, settings) -> None:
    """
    Given  a window whose preview panel shows an eye button
    When   the button is clicked
    Then   the menu entry follows and the choice is stored
    """
    main_window._preview._hide_btn.click()

    assert main_window._hide_personal_act.isChecked()
    assert settings.value(DISPLAY_ANONYMIZED_KEY) == "yes"


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
        ProcessingApplicationService, "anonymizer_available", lambda self, _lang: True
    )

    win = MainWindow(worker_factory=_DummyWorker)
    qtbot.addWidget(win)

    assert win._hide_personal_act.isChecked()
    assert win._preview.display_anonymized()


@pytest.mark.gui
def test_the_string_language_without_spacy_disables_the_entry(qtbot, monkeypatch, settings) -> None:
    """
    Given  a document language whose spaCy model is not installed
    When   the window is built
    Then   the menu entry is disabled and its tooltip points at the Downloads dialog
    """
    monkeypatch.setattr(MainWindow, "_settings_store", lambda self: settings)
    monkeypatch.setattr(
        ProcessingApplicationService, "anonymizer_available", lambda self, _lang: False
    )

    win = MainWindow(worker_factory=_DummyWorker)
    qtbot.addWidget(win)

    assert not win._hide_personal_act.isEnabled()
    assert "Downloads" in win._hide_personal_act.toolTip()
    assert win._preview.display_anonymized() is False


@pytest.mark.gui
def test_the_placeholders_arrive_from_the_service(main_window, monkeypatch) -> None:
    """
    Given  a window whose preview shows a text
    When   the panel asks for placeholders
    Then   the service is asked once and the preview shows its result
    """
    calls: list[tuple[str, str]] = []

    def _fake_anonymize(_self, text, language, _config=None):
        calls.append((text, language))
        return ANONYMIZED, MAPPING

    monkeypatch.setattr(ProcessingApplicationService, "anonymize_preview", _fake_anonymize)
    main_window._preview.load(_pixmap(), RAW, CORRECTED, "")
    main_window._apply_display_anonymized(True)

    _wait_for(lambda: main_window._preview._anon_ready)

    assert calls == [(RAW, main_window._config_panel.lookup_language())]
    assert main_window._preview._ocr_text.toPlainText() == ANONYMIZED


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
    Then   the menu entry is disabled for it, and enabled again for English
    """
    monkeypatch.setattr(
        ProcessingApplicationService,
        "anonymizer_available",
        lambda self, language: language == "English",
    )
    main_window._privacy_availability.clear()
    main_window._config_panel._language.setCurrentText("English")
    main_window._refresh_privacy_availability()
    assert main_window._hide_personal_act.isEnabled()

    main_window._config_panel._language.setCurrentText("German")

    assert not main_window._hide_personal_act.isEnabled()

    main_window._config_panel._language.setCurrentText("English")

    assert main_window._hide_personal_act.isEnabled()


@pytest.mark.gui
def test_a_finished_download_enables_the_setting(main_window, monkeypatch) -> None:
    """
    Given  a window whose model for the document language is not downloaded
    When   a download finishes (packages_changed) and the model is there now
    Then   the setting is available without a restart
    """
    installed = {"yes": False}
    monkeypatch.setattr(
        ProcessingApplicationService, "anonymizer_available", lambda self, _l: installed["yes"]
    )
    main_window._privacy_availability.clear()
    main_window._refresh_privacy_availability()
    assert not main_window._hide_personal_act.isEnabled()

    installed["yes"] = True
    main_window._on_packages_changed()

    assert main_window._hide_personal_act.isEnabled()


@pytest.mark.gui
def test_only_the_newest_anonymization_reaches_the_preview(main_window, monkeypatch) -> None:
    """
    Given  two files asked for placeholders one after the other, the first one slower
    When   both results arrive
    Then   only the second one is applied, and the first cannot reset the second's state
    """
    release_first = threading.Event()

    def _fake_anonymize(_self, text, _language, _config=None):
        if text == RAW:
            release_first.wait(5)
            return ANONYMIZED, MAPPING
        return "[PERSON_1] ist hier.", {"[PERSON_1]": "Clara West"}

    monkeypatch.setattr(ProcessingApplicationService, "anonymize_preview", _fake_anonymize)
    main_window._preview.load(_pixmap(), RAW, "", "")
    main_window._apply_display_anonymized(True)
    main_window._preview.load(_pixmap(), "Clara West ist hier.", "", "")

    _wait_for(lambda: main_window._preview._anon_ready)
    release_first.set()
    from PySide6.QtTest import QTest  # noqa: PLC0415

    QTest.qWait(100)

    assert main_window._preview._ocr_text.toPlainText() == "[PERSON_1] ist hier."
