"""GUI tests for the Settings window: lazy pages, shared state, deep links, restore, closing."""
# pylint: disable=W0212,W0621,W0613  # protected-access — the tests drive the window's pages directly / redefined-outer-name, unused-argument — pytest fixtures are injected by parameter name

from __future__ import annotations

import os
import threading
from typing import Any, ClassVar

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QMessageBox
import pytest

from teachers_teammate.application.service import AnonymizerConfig
from teachers_teammate.config import DEFAULTS, OcrConfig
from teachers_teammate.gui import _model_fetch
from teachers_teammate.gui.settings import (
    PAGE_TITLES,
    SettingsPage,
    SettingsWindow,
    last_page,
)
from teachers_teammate.gui.settings import _window as window_module
from teachers_teammate.gui.settings._addons_page import AddonsPage
from teachers_teammate.gui.settings._page import (
    FOCUS_API_KEYS,
    FOCUS_DETECTION,
    FOCUS_GPU,
    FOCUS_PATTERNS,
    FOCUS_SENDING,
)

from ._gui_harness import settings_context
from ._settings_stubs import AddonsCapableService, ProviderAddonsService

_PRIVACY_TAB_INDEX = {FOCUS_SENDING: 0, FOCUS_DETECTION: 1, FOCUS_PATTERNS: 2}


class _MemoryQSettings:
    """QSettings stand-in whose store is shared by all instances of one test."""

    store: ClassVar[dict[str, object]] = {}

    def __init__(self, *_args: object) -> None:
        pass

    def value(self, key: str, default: object = None) -> object:
        return type(self).store.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        type(self).store[key] = value

    def beginGroup(self, _name: str) -> None:
        pass

    def endGroup(self) -> None:
        pass

    def childKeys(self) -> list[str]:
        return []

    def remove(self, key: str) -> None:
        type(self).store.pop(key, None)


class _Button:
    def __init__(self, label: str) -> None:
        self.label = label
        self._enabled = True

    def setEnabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def isEnabled(self) -> bool:
        return self._enabled


class _ScriptedBox:
    """Stands in for ``QMessageBox``: clicks the buttons named in ``answers``, one per box."""

    Icon = QMessageBox.Icon
    ButtonRole = QMessageBox.ButtonRole
    StandardButton = QMessageBox.StandardButton
    answers: ClassVar[list[str]] = []
    boxes: ClassVar[list[_ScriptedBox]] = []
    information_calls: ClassVar[list[str]] = []

    def __init__(self, _parent: object = None) -> None:
        self.buttons: dict[str, _Button] = {}
        self.text = ""
        self.informative = ""
        self._clicked: _Button | None = None

    def setIcon(self, _icon: object) -> None: ...
    def setWindowTitle(self, _title: str) -> None: ...
    def setInformativeText(self, text: str) -> None:
        self.informative = text

    def setDefaultButton(self, _button: object) -> None: ...

    def setText(self, text: str) -> None:
        self.text = text

    def addButton(self, what: object, _role: object = None) -> _Button:
        label = what if isinstance(what, str) else "Cancel"
        self.buttons[label] = _Button(label)
        return self.buttons[label]

    def exec(self) -> int:
        self._clicked = self.buttons[type(self).answers.pop(0)]
        type(self).boxes.append(self)
        return 0

    def clickedButton(self) -> _Button | None:
        return self._clicked

    @classmethod
    def information(cls, _parent: object, _title: str, text: str) -> None:
        cls.information_calls.append(text)


@pytest.fixture
def scripted_box(monkeypatch) -> type[_ScriptedBox]:
    """Replace the window's message boxes; set ``answers`` to the buttons to click."""
    box = type("_Box", (_ScriptedBox,), {"answers": [], "boxes": [], "information_calls": []})
    monkeypatch.setattr(window_module, "QMessageBox", box)
    return box


@pytest.fixture(autouse=True)
def _hermetic_window(monkeypatch) -> None:
    """Keep the window offline: in-memory QSettings, no spaCy discovery thread."""
    from teachers_teammate.gui._anonymizer_config_widget import (  # noqa: PLC0415
        _SpacyModelFetchThread,
    )

    _MemoryQSettings.store = {}
    monkeypatch.setattr(window_module, "QSettings", _MemoryQSettings)
    monkeypatch.setattr(_SpacyModelFetchThread, "start", lambda self: None)


def _window(
    qtbot,
    page: SettingsPage = SettingsPage.AI_MODELS,
    *,
    service: Any = None,
    settings: dict | None = None,
    focus: str = "",
    preselect=(),
) -> SettingsWindow:
    window = SettingsWindow(
        settings_context(service or AddonsCapableService(), settings=settings or {}),
        page=page,
        focus=focus,
        preselect=preselect,
    )
    qtbot.addWidget(window)
    return window


def _addons(window: SettingsWindow) -> AddonsPage:
    page = window.page(SettingsPage.ADDONS)
    assert isinstance(page, AddonsPage)
    return page


def _privacy_tab(window: SettingsWindow) -> int:
    return window.page(SettingsPage.PRIVACY).rules().tabs().currentIndex()


# ── structure ──────────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_sidebar_lists_the_five_pages_in_order(qtbot) -> None:
    """
    Given  the Settings window
    When   it is opened
    Then   the sidebar lists AI models, Where the AI runs, Scan quality, Privacy and Add-ons, and
           selects the page it was opened on
    """
    window = _window(qtbot, SettingsPage.PRIVACY)

    titles = [window._sidebar.item(i).text() for i in range(window._sidebar.count())]
    assert titles == [
        "AI models",
        "Where the AI runs",
        "Scan quality",
        "Privacy",
        "Add-ons",
    ]
    assert titles == [PAGE_TITLES[page] for page in SettingsPage]
    assert window._sidebar.currentRow() == 3
    assert window.current_page() is SettingsPage.PRIVACY


@pytest.mark.gui
def test_only_the_opened_page_is_built(qtbot, monkeypatch) -> None:
    """
    Given  the Settings window opened on Privacy
    When   nothing else is visited
    Then   only the Privacy page was built, no model-list fetch started, and the result holds
           no AI-model key (an unvisited page changes nothing)
    """
    started: list[int] = []
    monkeypatch.setattr(_model_fetch._ModelFetchThread, "start", lambda self: started.append(1))

    window = _window(qtbot, SettingsPage.PRIVACY)

    assert window.built_pages() == [SettingsPage.PRIVACY]
    assert started == []
    result = window.values()
    assert result.settings == {}
    assert result.anonymizer_config is not None
    assert result.display_anonymized is not None


@pytest.mark.gui
def test_a_page_is_built_the_first_time_it_is_shown(qtbot, monkeypatch) -> None:
    """
    Given  the Settings window opened on Privacy
    When   the sidebar is used to go to AI models
    Then   that page is built then (its model lists are asked for), and the window shows it
    """
    started: list[int] = []
    monkeypatch.setattr(_model_fetch._ModelFetchThread, "start", lambda self: started.append(1))
    window = _window(qtbot, SettingsPage.PRIVACY)
    assert started == []

    window._sidebar.setCurrentRow(0)

    assert window.built_pages() == [SettingsPage.AI_MODELS, SettingsPage.PRIVACY]
    assert started
    assert window.current_page() is SettingsPage.AI_MODELS


@pytest.mark.gui
def test_the_page_header_names_the_page(qtbot) -> None:
    """
    Given  a built page
    When   its container is inspected
    Then   the title and the one-line description sit above the scrolled page
    """
    from PySide6.QtWidgets import QLabel, QScrollArea  # noqa: PLC0415

    window = _window(qtbot, SettingsPage.SCAN_QUALITY)

    container = window._containers[SettingsPage.SCAN_QUALITY]
    texts = [label.text() for label in container.findChildren(QLabel)]
    assert "Scan quality" in texts
    assert window.page(SettingsPage.SCAN_QUALITY).description in texts
    assert container.findChild(QScrollArea) is not None


# ── OK merges what was visited ─────────────────────────────────────────────────


@pytest.mark.gui
def test_ok_merges_the_values_of_the_pages_that_were_visited(qtbot) -> None:
    """
    Given  AI models, Scan quality and Where the AI runs were visited and changed
    When   the result is read
    Then   it holds each page's changes under their keys, and no key of an unvisited page
    """
    window = _window(qtbot, SettingsPage.AI_MODELS)
    window.page(SettingsPage.AI_MODELS)._ocr_temperature.setValue(0.5)
    window.show_page(SettingsPage.SCAN_QUALITY)
    window.page(SettingsPage.SCAN_QUALITY)._controls._pdf_dpi.setValue(144)
    window.show_page(SettingsPage.AI_SETUP)
    window.page(SettingsPage.AI_SETUP)._ollama_url.setText("http://box:11434")

    settings = window.values().settings

    assert settings["ocr_temperature"] == pytest.approx(0.5)
    assert settings["pdf_render_dpi"] == 144
    assert settings["ollama_url"] == "http://box:11434"
    assert "output_dir" not in settings
    assert "anonymizer_patterns" not in settings


@pytest.mark.gui
def test_the_result_has_the_privacy_choices_once_privacy_was_visited(qtbot) -> None:
    """
    Given  the Privacy page was visited and the hiding choice changed
    When   the result is read
    Then   it carries the anonymizer rules and the hiding choice
    """
    window = _window(qtbot, SettingsPage.PRIVACY)
    window.page(SettingsPage.PRIVACY).hide_personal_checkbox().setChecked(False)

    result = window.values()

    assert result.display_anonymized is False
    assert result.anonymizer_config is not None
    assert result.restore_all is False


@pytest.mark.gui
def test_api_keys_are_saved_on_ok_and_not_on_cancel(qtbot, monkeypatch, credential_file) -> None:
    """
    Given  a key typed on the Where the AI runs page
    When   the window is cancelled, and in another window the same key is entered and OK pressed
    Then   Cancel stores nothing; OK stores the key
    """
    monkeypatch.setattr(
        os, "environ", {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    )

    def enter_key(window: SettingsWindow) -> None:
        credentials = window.page(SettingsPage.AI_SETUP)._credentials
        credentials._add_combo.setCurrentIndex(credentials._add_combo.findData("OPENAI_API_KEY"))
        credentials._add_btn.click()
        credentials.field("OPENAI_API_KEY").setText("sk-typed")

    cancelled = _window(qtbot, SettingsPage.AI_SETUP)
    enter_key(cancelled)
    cancelled.reject()
    assert "credentials/OPENAI_API_KEY" not in credential_file

    accepted = _window(qtbot, SettingsPage.AI_SETUP)
    enter_key(accepted)
    accepted.accept()
    assert credential_file["credentials/OPENAI_API_KEY"] == "sk-typed"


@pytest.mark.gui
def test_remembered_answers_are_written_back_on_ok_only(qtbot) -> None:
    """
    Given  one remembered "don't ask again" answer taken back on the Privacy page
    When   the window is cancelled, and in another window the answer is taken back and OK pressed
    Then   the answer stays after Cancel and is gone after OK
    """
    from teachers_teammate.domain.data_location import (  # noqa: PLC0415
        DataFlow,
        DataFlowStage,
        DataLocation,
        StageLocation,
    )
    from teachers_teammate.gui._privacy_settings import FlowAcknowledgements  # noqa: PLC0415

    from ._gui_harness import MemorySettings  # noqa: PLC0415

    store = MemorySettings()
    acks = FlowAcknowledgements(lambda: store)
    online = StageLocation(DataLocation.ONLINE, "Mistral")
    acks.remember(DataFlow((DataFlowStage("correction", online, "text"),)))

    def build() -> SettingsWindow:
        window = SettingsWindow(
            settings_context(AddonsCapableService(), acknowledgements=acks),
            page=SettingsPage.PRIVACY,
        )
        qtbot.addWidget(window)
        window.page(SettingsPage.PRIVACY).sending_tab()._forget_all_btn.click()
        return window

    build().reject()
    assert len(acks.remembered()) == 1

    build().accept()
    assert acks.remembered() == []


# ── shared state between pages ─────────────────────────────────────────────────


@pytest.mark.use_case("Hardware_Profile_Override")
def test_the_profile_chosen_on_this_computer_reaches_the_other_pages(qtbot) -> None:
    """
    Given  AI models, Where the AI runs and Add-ons built, all on the "auto" profile
    When   "My computer" is changed to a basic computer on Where the AI runs
    Then   the read-only profile lines of the recognition picker, both AI sections and Add-ons
           follow, and a page built afterwards starts with it
    """
    window = _window(qtbot, SettingsPage.AI_MODELS)
    window.show_page(SettingsPage.ADDONS)
    window.show_page(SettingsPage.AI_SETUP)
    computer = window.page(SettingsPage.AI_SETUP)

    with qtbot.waitSignal(computer.hardware_setting_changed, timeout=5000):
        computer._hardware_selector._combo.setCurrentText(
            "Basic computer or small graphics card (under 8 GB)"
        )

    models = window.page(SettingsPage.AI_MODELS)
    assert models._ocr_picker._hw_selector.setting() == "cpu"
    assert models._proofreading._hw_selector.setting() == "cpu"
    assert models._review._hw_selector.setting() == "cpu"
    assert window.page(SettingsPage.ADDONS)._hardware_selector.setting() == "cpu"
    assert window.values().settings["hardware_profile"] == "cpu"

    window.show_page(SettingsPage.SCAN_QUALITY)  # built later, not affected
    assert window._working["hardware_profile"] == "cpu"


@pytest.mark.use_case("Hardware_Profile_Override")
def test_a_page_built_after_a_profile_change_starts_with_it(qtbot) -> None:
    """
    Given  the profile changed on Where the AI runs before Add-ons was ever opened
    When   Add-ons is opened
    Then   its read-only profile already shows the new choice
    """
    window = _window(qtbot, SettingsPage.AI_SETUP)
    computer = window.page(SettingsPage.AI_SETUP)
    with qtbot.waitSignal(computer.hardware_setting_changed, timeout=5000):
        computer._hardware_selector._combo.setCurrentText("Graphics card with 16 GB memory or more")

    window.show_page(SettingsPage.ADDONS)

    assert window.page(SettingsPage.ADDONS)._hardware_selector.setting() == "16gb"


@pytest.mark.gui
def test_the_server_address_reaches_the_ai_model_sections(qtbot) -> None:
    """
    Given  the AI models page that asked the default server for its models
    When   another server address is entered on Where the AI runs (editing finished)
    Then   the recognition picker, both sections and Add-ons use the new address, and the
           sections ask the new server for its models
    """
    asked: list[str] = []

    class _Service(AddonsCapableService):
        def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
            asked.append(base_url)
            return super().list_provider_models(provider, base_url=base_url)

    window = _window(
        qtbot,
        SettingsPage.AI_MODELS,
        service=_Service(),
        settings={"correction_provider": "ollama", "evaluate_provider": "ollama"},
    )
    window.show_page(SettingsPage.ADDONS)
    window.show_page(SettingsPage.AI_SETUP)
    qtbot.waitUntil(lambda: len(asked) >= 2, timeout=5000)
    asked.clear()
    computer = window.page(SettingsPage.AI_SETUP)

    computer._ollama_url.setText("http://192.168.1.50:11434")
    computer._ollama_url.editingFinished.emit()

    models = window.page(SettingsPage.AI_MODELS)
    url = "http://192.168.1.50:11434"
    assert models._ocr_picker._ollama_url == url
    assert models._proofreading._ollama_url == url
    assert models._review._ollama_url == url
    assert window.page(SettingsPage.ADDONS)._ollama_url == url
    qtbot.waitUntil(lambda: asked.count(url) >= 2, timeout=5000)  # proofreading and review


@pytest.mark.gui
def test_choosing_a_method_applies_its_image_preparation_on_scan_quality(qtbot) -> None:
    """
    Given  AI models and Scan quality built
    When   the teacher picks tesseract as the recognition method
    Then   Scan quality shows the preparation tesseract works best with
    """
    window = _window(qtbot, SettingsPage.SCAN_QUALITY)
    window.show_page(SettingsPage.AI_MODELS)

    window.page(SettingsPage.AI_MODELS)._ocr_picker._ocr_engine.setCurrentText("tesseract")

    assert window.page(SettingsPage.SCAN_QUALITY).values()["preprocess_method"] == "clahe"


@pytest.mark.gui
def test_the_image_preparation_follows_the_method_even_if_scan_quality_is_unbuilt(qtbot) -> None:
    """
    Given  AI models opened first, Scan quality never opened
    When   the teacher picks tesseract
    Then   the result carries tesseract's preparation, and Scan quality shows it when it is
           opened later
    """
    window = _window(qtbot, SettingsPage.AI_MODELS)
    window.page(SettingsPage.AI_MODELS)._ocr_picker._ocr_engine.setCurrentText("tesseract")

    assert window.values().settings["preprocess_method"] == "clahe"

    window.show_page(SettingsPage.SCAN_QUALITY)
    assert window.page(SettingsPage.SCAN_QUALITY).values()["preprocess_method"] == "clahe"


# ── preview ────────────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_preview_builds_the_whole_config_from_both_ocr_parts(qtbot) -> None:
    """
    Given  AI models and Scan quality built, with another method, model, temperature and preset
    When   Preview… is pressed on Scan quality
    Then   the window emits an OcrConfig holding the method, model and temperature from AI
           models and the preparation from Scan quality
    """
    window = _window(qtbot, SettingsPage.AI_MODELS)
    models = window.page(SettingsPage.AI_MODELS)
    models._ocr_picker._ocr_engine.setCurrentText("tesseract")
    models._ocr_temperature.setValue(0.6)
    window.show_page(SettingsPage.SCAN_QUALITY)
    scan = window.page(SettingsPage.SCAN_QUALITY)
    scan._controls._pdf_dpi.setValue(144)

    with qtbot.waitSignal(window.preprocess_preview_requested, timeout=2000) as blocker:
        scan.preview_requested.emit()

    config = blocker.args[0]
    assert isinstance(config, OcrConfig)
    assert config.engine == "tesseract"
    assert config.temperature == pytest.approx(0.6)
    assert config.preprocess_method == "clahe"  # tesseract's default, applied on the change
    assert config.pdf_render_dpi == 144


@pytest.mark.gui
def test_the_preview_uses_the_saved_recognition_choice_when_ai_models_is_unbuilt(qtbot) -> None:
    """
    Given  Scan quality opened first and AI models never opened, with a saved method
    When   Preview… is pressed
    Then   the OcrConfig uses the saved method and temperature
    """
    window = _window(
        qtbot,
        SettingsPage.SCAN_QUALITY,
        settings={"ocr_engine": "tesseract", "ocr_temperature": 0.7},
    )
    scan = window.page(SettingsPage.SCAN_QUALITY)

    with qtbot.waitSignal(window.preprocess_preview_requested, timeout=2000) as blocker:
        scan.preview_requested.emit()

    config = blocker.args[0]
    assert config.engine == "tesseract"
    assert config.temperature == pytest.approx(0.7)


# ── deep links ─────────────────────────────────────────────────────────────────


@pytest.mark.gui
@pytest.mark.parametrize("focus", [FOCUS_SENDING, FOCUS_DETECTION, FOCUS_PATTERNS])
def test_privacy_deep_links_land_on_their_tab(qtbot, focus: str) -> None:
    """
    Given  a caller asking for the sending, detection or patterns part of Privacy
    When   the window is opened with that focus
    Then   the Privacy page shows the matching tab
    """
    window = _window(qtbot, SettingsPage.PRIVACY, focus=focus)

    assert _privacy_tab(window) == _PRIVACY_TAB_INDEX[focus]


@pytest.mark.gui
def test_a_deep_link_into_an_open_window_switches_page_and_tab(qtbot) -> None:
    """
    Given  the window open on AI models
    When   show_page is called for Add-ons with the GPU focus
    Then   Add-ons is built and shown on the performance-monitoring tab
    """
    window = _window(qtbot, SettingsPage.AI_MODELS)

    window.show_page(SettingsPage.ADDONS, FOCUS_GPU)

    assert window.current_page() is SettingsPage.ADDONS
    assert window.page(SettingsPage.ADDONS)._tabs.currentIndex() == AddonsPage.TAB_GPU
    assert window._sidebar.currentRow() == 4


@pytest.mark.gui
def test_a_provider_preselect_opens_online_services_with_the_package_ticked(qtbot) -> None:
    """
    Given  a caller asking for Add-ons with provider:mistral preselected
    When   the window is opened and what is installed has been detected
    Then   the Online AI services tab is shown with the Mistral package ticked
    """
    service = ProviderAddonsService()
    service.missing = {"mistral": "langchain-mistralai", "openai": "langchain-openai"}
    window = _window(qtbot, SettingsPage.ADDONS, service=service, preselect=["provider:mistral"])
    addons = window.page(SettingsPage.ADDONS)

    qtbot.waitUntil(lambda: addons._dl_checkboxes["provider:mistral"].isChecked(), timeout=5000)

    assert addons._tabs.currentIndex() == AddonsPage.TAB_ONLINE_SERVICES
    assert not addons._dl_checkboxes["provider:openai"].isChecked()


@pytest.mark.gui
def test_the_api_keys_focus_brings_the_keys_into_view(qtbot, monkeypatch) -> None:
    """
    Given  a caller asking for the API keys on AI models
    When   the window is opened with that focus
    Then   the page scrolls the keys group into view
    """
    from PySide6.QtWidgets import QScrollArea  # noqa: PLC0415

    seen: list[object] = []
    monkeypatch.setattr(
        QScrollArea, "ensureWidgetVisible", lambda self, widget, *a, **k: seen.append(widget)
    )

    window = _window(qtbot, SettingsPage.AI_SETUP, focus=FOCUS_API_KEYS)
    window.show()
    qtbot.waitUntil(lambda: bool(seen), timeout=2000)

    assert seen[-1] is window.page(SettingsPage.AI_SETUP)._credentials


@pytest.mark.gui
def test_download_a_model_on_this_computer_opens_add_ons(qtbot) -> None:
    """
    Given  the Where the AI runs page
    When   "Download a model…" is pressed
    Then   the window shows Add-ons on the local models tab
    """
    window = _window(qtbot, SettingsPage.AI_SETUP)

    window.page(SettingsPage.AI_SETUP)._download_model_btn.click()

    assert window.current_page() is SettingsPage.ADDONS
    assert window.page(SettingsPage.ADDONS)._tabs.currentIndex() == AddonsPage.TAB_LOCAL_MODELS


@pytest.mark.gui
def test_the_change_link_on_add_ons_opens_this_computer(qtbot) -> None:
    """
    Given  the Add-ons page with its read-only "My computer" line
    When   "change…" is clicked
    Then   the window shows Where the AI runs
    """
    window = _window(qtbot, SettingsPage.ADDONS)

    window.page(SettingsPage.ADDONS)._change_profile_lbl.linkActivated.emit("change")

    assert window.current_page() is SettingsPage.AI_SETUP


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_a_package_hint_install_switches_to_add_ons_and_the_hint_goes_after_the_install(
    qtbot, monkeypatch
) -> None:
    """
    Given  Mistral chosen for proofreading with its package missing, on the AI models page
    When   the hint's "Install…" is pressed, and the install later finishes
    Then   the window switches to Add-ons with the Mistral package preselected (no dialog), and
           after packages_changed the hint is gone and the main window is told
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    service = ProviderAddonsService()
    service.missing = {"mistral": "langchain-mistralai"}
    window = _window(
        qtbot,
        SettingsPage.AI_MODELS,
        service=service,
        settings={"correction_provider": "mistral"},
    )
    models = window.page(SettingsPage.AI_MODELS)
    hint = models._proofreading._package_hint
    assert not hint.isHidden()

    hint._button.click()

    assert window.current_page() is SettingsPage.ADDONS
    addons = window.page(SettingsPage.ADDONS)
    assert addons._tabs.currentIndex() == AddonsPage.TAB_ONLINE_SERVICES
    qtbot.waitUntil(lambda: addons._dl_checkboxes["provider:mistral"].isChecked(), timeout=5000)

    service.missing = {}  # the install finished
    with qtbot.waitSignal(window.packages_changed, timeout=2000):
        addons.packages_changed.emit()

    assert hint.isHidden()


# ── restore defaults ───────────────────────────────────────────────────────────


@pytest.mark.gui
def test_restore_this_page_resets_only_that_page(qtbot, scripted_box) -> None:
    """
    Given  changed values on Scan quality and AI models
    When   "Restore defaults…" is answered with "This page" on Scan quality
    Then   Scan quality shows the defaults, AI models keeps its values, and the window stays open
    """
    window = _window(qtbot, SettingsPage.AI_MODELS)
    window.page(SettingsPage.AI_MODELS)._correction_temperature.setValue(0.9)
    window.show_page(SettingsPage.SCAN_QUALITY)
    scan = window.page(SettingsPage.SCAN_QUALITY)
    scan._controls._pdf_dpi.setValue(600)
    scripted_box.answers = ["This page"]

    window._restore_btn.click()

    assert scan.values()["pdf_render_dpi"] == DEFAULTS["pdf_render_dpi"]
    assert window.page(SettingsPage.AI_MODELS)._correction_temperature.value() == pytest.approx(0.9)
    assert window.values().restore_all is False
    assert window.result() == 0


@pytest.mark.gui
def test_restore_this_page_on_privacy_keeps_the_remembered_answers(qtbot, scripted_box) -> None:
    """
    Given  the Privacy page with the hiding choice switched off
    When   "Restore defaults…" is answered with "This page"
    Then   the hiding choice is back to its default; the remembered answers are untouched
    """
    window = _window(qtbot, SettingsPage.PRIVACY)
    privacy = window.page(SettingsPage.PRIVACY)
    privacy.hide_personal_checkbox().setChecked(False)
    scripted_box.answers = ["This page"]

    window._restore_btn.click()

    assert privacy.display_anonymized() is True


@pytest.mark.gui
def test_restore_this_page_is_disabled_on_add_ons(qtbot, scripted_box) -> None:
    """
    Given  the Add-ons page, where installs take effect at once
    When   "Restore defaults…" is pressed
    Then   the "This page" choice is disabled (there is nothing to restore)
    """
    window = _window(qtbot, SettingsPage.ADDONS)
    scripted_box.answers = ["Cancel"]

    window._restore_btn.click()

    [box] = scripted_box.boxes
    assert box.buttons["This page"].isEnabled() is False
    assert box.buttons["All settings"].isEnabled() is True


@pytest.mark.gui
def test_restore_on_add_ons_does_not_promise_a_change_on_ok(qtbot, scripted_box) -> None:
    """
    Given  the Add-ons page and, for comparison, Scan quality
    When   "Restore defaults…" is pressed on each
    Then   Scan quality says its values are saved with OK; Add-ons says it has nothing to
           restore instead of promising a change
    """
    window = _window(qtbot, SettingsPage.ADDONS)
    scripted_box.answers = ["Cancel", "Cancel"]

    window._restore_btn.click()
    window.show_page(SettingsPage.SCAN_QUALITY)
    window._restore_btn.click()

    addons_box, scan_box = scripted_box.boxes
    assert "saved when you press OK" not in addons_box.informative
    assert "not undone" in addons_box.informative
    assert "saved when you press OK" in scan_box.informative


@pytest.mark.gui
def test_restoring_scan_quality_keeps_the_preset_of_the_chosen_method(qtbot, scripted_box) -> None:
    """
    Given  tesseract chosen as the recognition method and a changed image preparation
    When   "Restore defaults…" is answered with "This page" on Scan quality
    Then   the preset is the one tesseract works best with (clahe), not the global default
    """
    window = _window(
        qtbot,
        SettingsPage.SCAN_QUALITY,
        settings={"ocr_engine": "tesseract", "preprocess_method": "none"},
    )
    scan = window.page(SettingsPage.SCAN_QUALITY)
    scripted_box.answers = ["This page"]

    window._restore_btn.click()

    assert scan.values()["preprocess_method"] == "clahe"
    assert scan.values()["pdf_render_dpi"] == DEFAULTS["pdf_render_dpi"]


@pytest.mark.gui
def test_restoring_ai_models_applies_the_default_methods_preparation(qtbot, scripted_box) -> None:
    """
    Given  both pages open with tesseract chosen
    When   "Restore defaults…" is answered with "This page" on AI models
    Then   the recognition method is back to the default one and Scan quality follows with that
           method's image preparation, as choosing the method by hand does
    """
    window = _window(
        qtbot,
        SettingsPage.SCAN_QUALITY,
        settings={"ocr_engine": "tesseract", "preprocess_method": "clahe"},
    )
    window.show_page(SettingsPage.AI_MODELS)
    scripted_box.answers = ["This page"]

    window._restore_btn.click()

    engine = window.page(SettingsPage.AI_MODELS).ocr_choice()[0]
    assert engine == DEFAULTS["ocr_engine"]
    expected = window._context.app_service.default_preprocess_for_engine(engine)
    assert window.page(SettingsPage.SCAN_QUALITY).values()["preprocess_method"] == expected


@pytest.mark.gui
def test_restore_all_needs_a_second_confirmation_and_closes_with_a_special_result(
    qtbot, scripted_box
) -> None:
    """
    Given  the window open on any page
    When   "Restore defaults…" is answered with "All settings" and the second question with
           "Reset everything"
    Then   the window closes with the restore-all result, asks the second question with the
           plan's wording, and the result says restore_all
    """
    window = _window(qtbot, SettingsPage.AI_MODELS)
    scripted_box.answers = ["All settings", "Reset everything"]

    window._restore_btn.click()

    assert window.result() == SettingsWindow.RESTORE_ALL_RESULT
    assert window.values().restore_all is True
    confirm = scripted_box.boxes[-1]
    assert "including the choices in the main window" in confirm.text
    assert "saved API keys are kept" in confirm.text


@pytest.mark.gui
def test_cancelling_the_second_confirmation_resets_nothing(qtbot, scripted_box) -> None:
    """
    Given  "All settings" chosen
    When   the second question is answered with Cancel
    Then   the window stays open and the result does not ask for a reset
    """
    window = _window(qtbot, SettingsPage.AI_MODELS)
    scripted_box.answers = ["All settings", "Cancel"]

    window._restore_btn.click()

    assert window.result() == 0
    assert window.values().restore_all is False


@pytest.mark.gui
def test_cancelling_restore_defaults_changes_nothing(qtbot, scripted_box) -> None:
    """
    Given  a changed value
    When   "Restore defaults…" is cancelled
    Then   the value is unchanged
    """
    window = _window(qtbot, SettingsPage.SCAN_QUALITY)
    scan = window.page(SettingsPage.SCAN_QUALITY)
    scan._controls._pdf_dpi.setValue(600)
    scripted_box.answers = ["Cancel"]

    window._restore_btn.click()

    assert scan.values()["pdf_render_dpi"] == 600


# ── footer ─────────────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_add_ons_note_shows_only_on_add_ons(qtbot) -> None:
    """
    Given  the Settings window
    When   Add-ons is shown, and then another page
    Then   the note "Downloads and installs start right away…" is visible only on Add-ons
    """
    window = _window(qtbot, SettingsPage.AI_MODELS)
    window.show()
    assert not window._addons_note.isVisible()

    window.show_page(SettingsPage.ADDONS)
    assert window._addons_note.isVisible()
    assert "not undone by Cancel" in window._addons_note.text()

    window.show_page(SettingsPage.PRIVACY)
    assert not window._addons_note.isVisible()


@pytest.mark.gui
def test_ok_is_not_the_default_button_on_add_ons(qtbot) -> None:
    """
    Given  the Settings window
    When   Add-ons is shown (where Enter in "Add a model by name" must only add the model)
    Then   OK is not the default button there, and is again on the other pages
    """
    from PySide6.QtWidgets import QDialogButtonBox  # noqa: PLC0415

    window = _window(qtbot, SettingsPage.ADDONS)
    ok = window._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isDefault() and not ok.autoDefault()

    window.show_page(SettingsPage.PRIVACY)
    assert ok.autoDefault()


@pytest.mark.gui
def test_enter_in_a_field_on_add_ons_does_not_close_the_window(qtbot, monkeypatch) -> None:
    """
    Given  the Settings window opened straight on Add-ons and really shown (Qt then picks a
           default button for the dialog)
    When   Enter is pressed in "Add a model by name"
    Then   no button of the window is the default, and the window stays open
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415
    from PySide6.QtTest import QTest  # noqa: PLC0415
    from PySide6.QtWidgets import QPushButton  # noqa: PLC0415

    monkeypatch.setattr(_model_fetch._ModelFetchThread, "start", lambda self: None)
    window = _window(qtbot, SettingsPage.ADDONS)
    window.show()
    qtbot.waitExposed(window)

    assert [b.text() for b in window.findChildren(QPushButton) if b.isDefault()] == []
    field = _addons(window)._ollama_custom_input
    field.setFocus()
    QTest.keyClick(field, Qt.Key.Key_Return)

    assert window.isVisible()


@pytest.mark.gui
def test_ok_is_the_default_button_again_after_leaving_add_ons(qtbot, monkeypatch) -> None:
    """
    Given  a shown Settings window that was on Add-ons
    When   another page is shown
    Then   OK is the default button again
    """
    from PySide6.QtWidgets import QDialogButtonBox  # noqa: PLC0415

    monkeypatch.setattr(_model_fetch._ModelFetchThread, "start", lambda self: None)
    window = _window(qtbot, SettingsPage.ADDONS)
    window.show()
    qtbot.waitExposed(window)

    window.show_page(SettingsPage.PRIVACY)

    ok = window._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isDefault()


@pytest.mark.gui
def test_a_finished_install_does_not_reset_the_image_preparation(qtbot, monkeypatch) -> None:
    """
    Given  saved settings with Tesseract and the image preparation "none", both pages built
    When   an install finishes and every page looks again (the recognition method itself
           did not change)
    Then   the image preparation is still "none", not the method's default
    """
    monkeypatch.setattr(_model_fetch._ModelFetchThread, "start", lambda self: None)
    window = _window(
        qtbot,
        SettingsPage.SCAN_QUALITY,
        settings={"ocr_engine": "tesseract", "preprocess_method": "none"},
    )
    window.show_page(SettingsPage.AI_MODELS)
    scan = window.page(SettingsPage.SCAN_QUALITY)
    assert scan.values()["preprocess_method"] == "none"

    window._on_packages_changed()

    assert scan.values()["preprocess_method"] == "none"


@pytest.mark.gui
def test_a_finished_install_leaves_an_unbuilt_scan_page_alone(qtbot, monkeypatch) -> None:
    """
    Given  saved settings with Tesseract and the image preparation "none", Scan quality
           never opened
    When   an install finishes and every built page looks again
    Then   no image preparation is carried into the result (an unvisited page changes nothing)
    """
    monkeypatch.setattr(_model_fetch._ModelFetchThread, "start", lambda self: None)
    window = _window(
        qtbot,
        SettingsPage.AI_MODELS,
        settings={"ocr_engine": "tesseract", "preprocess_method": "none"},
    )

    window._on_packages_changed()

    assert "preprocess_method" not in window.values().settings


# ── validity ───────────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_an_invalid_pattern_disables_ok_and_blocks_accepting(qtbot) -> None:
    """
    Given  the Privacy page with a valid pattern
    When   the pattern is broken, then fixed
    Then   OK is disabled and accept() does nothing while it is broken, and works again after
    """
    from PySide6.QtWidgets import QDialogButtonBox  # noqa: PLC0415

    window = _window(qtbot, SettingsPage.PRIVACY)
    rules = window.page(SettingsPage.PRIVACY).rules()
    ok = window._buttons.button(QDialogButtonBox.StandardButton.Ok)
    rules.set_config(AnonymizerConfig(patterns=(("ID", r"\d+"),)))
    assert ok.isEnabled()

    rules._table.item(0, 1).setText("[open")
    assert not ok.isEnabled()
    window.accept()
    assert window.result() == 0

    rules._table.item(0, 1).setText(r"\d+")
    assert ok.isEnabled()
    window.accept()
    assert window.result() == 1


# ── closing ────────────────────────────────────────────────────────────────────


class _Busy(QThread):
    """A thread that stands in for a running pip install."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def run(self) -> None:
        self.release.wait(5)


@pytest.mark.gui
def test_closing_waits_while_an_install_runs(qtbot, scripted_box) -> None:
    """
    Given  a pip install or language-model download running on Add-ons
    When   OK, Cancel and Restore → All are tried
    Then   the window stays open and says why, nothing is shut down, and once the install has
           finished the window closes
    """
    window = _window(qtbot, SettingsPage.ADDONS)
    addons = _addons(window)
    shut_down: list[bool] = []
    addons.shutdown = lambda: shut_down.append(True)  # ty: ignore[invalid-assignment]
    busy = _Busy()
    addons._dl_active_thread = busy
    busy.start()
    try:
        window.accept()
        window.reject()
        scripted_box.answers = ["All settings", "Reset everything"]
        window._restore_btn.click()

        assert window.result() == 0
        assert window.values().restore_all is False
        assert len(scripted_box.information_calls) == 3
        assert "installation is still running" in scripted_box.information_calls[0]
        assert shut_down == []
    finally:
        busy.release.set()
        busy.wait(5000)

    window.reject()
    assert shut_down == [True]


@pytest.mark.gui
def test_closing_shuts_down_every_built_page(qtbot) -> None:
    """
    Given  a window with several pages built
    When   it is closed
    Then   shutdown() is called on each built page and on no unbuilt one
    """
    window = _window(qtbot, SettingsPage.AI_MODELS)
    window.show_page(SettingsPage.AI_SETUP)
    window.show_page(SettingsPage.ADDONS)
    shut_down: list[SettingsPage] = []
    for page in window.built_pages():
        built = window.page(page)
        built.shutdown = lambda p=page: shut_down.append(p)  # ty: ignore[invalid-assignment]

    window.reject()

    assert sorted(shut_down) == sorted(
        [SettingsPage.AI_MODELS, SettingsPage.AI_SETUP, SettingsPage.ADDONS]
    )


@pytest.mark.gui
def test_closing_parks_a_running_model_fetch(qtbot) -> None:
    """
    Given  an AI models page whose model list is still being fetched
    When   the window is closed
    Then   closing does not wait for the thread; it is parked so it is never destroyed while
           running
    """
    release = threading.Event()

    class _Slow(AddonsCapableService):
        def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
            release.wait(10)
            return [f"{provider}-model"]

    window = _window(
        qtbot,
        SettingsPage.AI_MODELS,
        service=_Slow(),
        settings={"correction_provider": "ollama"},
    )
    thread = window.page(SettingsPage.AI_MODELS)._proofreading._fetch_thread
    assert thread is not None and thread.isRunning()

    window.reject()

    assert thread in _model_fetch._RETIRED_THREADS
    release.set()
    qtbot.waitUntil(thread.isFinished, timeout=5000)


# ── remembered window state ────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_window_remembers_the_last_page_and_its_size(qtbot) -> None:
    """
    Given  a window closed on Where the AI runs
    When   a new window is built, and "All settings…" asks for the last page
    Then   last_page() names Where the AI runs and the geometry was stored
    """
    assert last_page() is SettingsPage.AI_MODELS  # nothing stored yet

    window = _window(qtbot, SettingsPage.AI_SETUP)
    window.reject()

    assert last_page() is SettingsPage.AI_SETUP
    assert _MemoryQSettings.store[window_module.GEOMETRY_KEY] is not None


@pytest.mark.gui
def test_an_unknown_stored_page_falls_back_to_ai_models(qtbot) -> None:
    """
    Given  a stored page name from a different version
    When   last_page() is asked
    Then   AI models is returned
    """
    _MemoryQSettings.store[window_module.LAST_PAGE_KEY] = "no_such_page"

    assert last_page() is SettingsPage.AI_MODELS


@pytest.mark.gui
def test_the_window_is_sized_for_a_screen(qtbot) -> None:
    """
    Given  the Settings window
    When   it is created
    Then   it is about 960 x 680 (or smaller on a small screen)
    """
    window = _window(qtbot)

    assert window.width() <= 960
    assert window.height() <= 680
    assert window.width() >= 600


def test_every_page_class_declares_its_enum_value(qtbot) -> None:
    """
    Given  the page classes the window builds
    When   their page and title are read
    Then   each declares the enum value it is registered under and the sidebar title
    """
    for page, cls in window_module._PAGE_CLASSES.items():
        assert cls.page is page
        assert cls.title == PAGE_TITLES[page]
        assert cls.description
