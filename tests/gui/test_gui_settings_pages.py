"""GUI tests for the Settings pages: AI models, Scan quality, Where the AI runs, API keys."""
# pylint: disable=W0212,W0621,W0613  # protected-access — the tests drive the pages' widgets directly / redefined-outer-name, unused-argument — pytest fixtures are injected by parameter name

from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton
import pytest

from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain.model_catalog import DEFAULT_WRITING_MODEL
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.gui import _model_fetch
from teachers_teammate.gui._model_item_delegate import ITEM_LABEL_ROLE
from teachers_teammate.gui._ocr_config_selector import OcrConfigSelector
from teachers_teammate.gui.settings._ai_models_page import AiModelsPage
from teachers_teammate.gui.settings._ai_service_section import (
    CONTENT_REVIEW,
    PROOFREADING,
    AiServiceSection,
    AiServiceSpec,
)
from teachers_teammate.gui.settings._credentials import CredentialsGroup
from teachers_teammate.gui.settings._page import (
    FOCUS_LOCAL_MODELS,
    SettingsPage,
    SettingsPageBase,
)
from teachers_teammate.gui.settings._privacy_page import PrivacyPage
from teachers_teammate.gui.settings._scan_quality_page import ScanQualityPage
from teachers_teammate.gui.settings._ai_setup_page import AiSetupPage

from ._gui_harness import settings_context
from ._settings_stubs import (
    CachingDummyAppService,
    CatalogAppService,
    DummyAppService,
    FakeKeyring,
    NonListableService,
    ProviderAppService,
    SlowMistralService,
    curated_listing,
)

_SPECS = [PROOFREADING, CONTENT_REVIEW]


def _section(
    qtbot,
    spec: AiServiceSpec,
    service,
    values: dict | None = None,
    *,
    hardware_profile: str = "auto",
    open_downloads=None,
) -> AiServiceSection:
    """Build a section for *spec* and load *values* into it, as a page does."""
    values = values or {}
    section = AiServiceSection(
        spec,
        app_service=service,
        hardware_profile=hardware_profile,
        ollama_url=str(values.get("ollama_url", DEFAULTS["ollama_url"])),
        open_downloads=open_downloads,
    )
    qtbot.addWidget(section)
    section.load(values)
    return section


def _proofreading(qtbot, service=None, values=None, **kwargs) -> AiServiceSection:
    return _section(qtbot, PROOFREADING, service or DummyAppService(), values, **kwargs)


def _items(section: AiServiceSection) -> list[str]:
    return [section._model.itemText(i) for i in range(section._model.count())]


def _wait_for_fetch(qtbot, section, svc: ProviderAppService, calls: int) -> None:
    """Wait until the section asked the service *calls* times and showed the answer."""
    qtbot.waitUntil(lambda: len(svc.checked_calls) >= calls, timeout=5000)
    thread = section._fetch_thread
    if thread is not None:
        thread.wait(5000)
    # The busy bar is hidden again once the answer is in.
    qtbot.waitUntil(lambda: section._busy.isHidden(), timeout=5000)


@pytest.fixture(autouse=True)
def _no_spacy_discovery(monkeypatch) -> None:
    """The Privacy page lists spaCy models on a thread; these tests never need it."""
    from teachers_teammate.gui._anonymizer_config_widget import (  # noqa: PLC0415
        _SpacyModelFetchThread,
    )

    monkeypatch.setattr(_SpacyModelFetchThread, "start", lambda self: None)


# ── page protocol ──────────────────────────────────────────────────────────────


def _all_pages(qtbot) -> list[SettingsPageBase]:
    pages: list[SettingsPageBase] = []
    for cls in (AiModelsPage, ScanQualityPage, PrivacyPage, AiSetupPage):
        page = cls(settings_context(DummyAppService(), anonymizer_config=None))
        qtbot.addWidget(page)
        pages.append(page)
    return pages


@pytest.mark.gui
def test_every_page_returns_exactly_the_keys_it_owns(qtbot) -> None:
    """
    Given  the pages that hold settings
    When   their values are read
    Then   each returns only keys it declares in owned_keys() (and Privacy hands back its rules
           through anonymizer_config(), not the dict), so "Restore defaults → This page" and the
           merge on OK never touch another page's keys
    """
    for page in _all_pages(qtbot):
        values = page.values()
        assert set(values) <= page.owned_keys(), page.title
        if page.page is not SettingsPage.PRIVACY:
            assert set(values) == page.owned_keys(), page.title


@pytest.mark.gui
def test_no_two_pages_own_the_same_key(qtbot) -> None:
    """
    Given  the pages that hold settings
    When   their owned keys are compared
    Then   no key is owned by two pages (each setting lives in exactly one place)
    """
    seen: dict[str, str] = {}
    for page in _all_pages(qtbot):
        for key in page.owned_keys():
            assert key not in seen, f"{key} is owned by {seen[key]} and {page.title}"
            seen[key] = page.title


@pytest.mark.gui
def test_restore_defaults_resets_only_the_pages_own_keys(qtbot) -> None:
    """
    Given  a Scan quality page with changed values, and the same values on AI models
    When   "Restore defaults → This page" resets Scan quality
    Then   the scan values are the defaults, and AI models still shows what was loaded
    """
    context = settings_context(DummyAppService())
    scan, models = ScanQualityPage(context), AiModelsPage(context)
    qtbot.addWidget(scan)
    qtbot.addWidget(models)
    changed = {
        "preprocess_method": "none",
        "pdf_render_dpi": 600,
        "debug": True,
        "correction_temperature": 0.9,
    }
    scan.load(changed)
    models.load(changed)

    scan.restore_defaults()

    values = scan.values()
    assert values["preprocess_method"] == DEFAULTS["preprocess_method"]
    assert values["pdf_render_dpi"] == DEFAULTS["pdf_render_dpi"]
    assert values["debug"] == DEFAULTS["debug"]
    assert models.values()["correction_temperature"] == pytest.approx(0.9)


# ── AI models: reading handwriting ─────────────────────────────────────────────


@pytest.fixture
def models_page(qtbot) -> AiModelsPage:
    page = AiModelsPage(settings_context(DummyAppService(), settings={"ocr_engine": "tesseract"}))
    qtbot.addWidget(page)
    page.load({"ocr_engine": "tesseract"})
    page.show()
    return page


@pytest.mark.gui
def test_ai_models_page_offers_the_recognition_methods(models_page) -> None:
    """
    Given  the AI models page
    When   it is created
    Then   the recognition picker is there with its methods, and the chosen one is returned
    """
    assert models_page._ocr_picker._ocr_engine.count() > 0
    assert models_page.values()["ocr_engine"] == "tesseract"
    assert models_page.values()["ocr_model"]


@pytest.mark.gui
def test_the_page_says_which_computer_the_suggestions_are_for_only_once(models_page) -> None:
    """
    Given  the AI models page with the recognition picker and two AI sections
    When   it is shown
    Then   the "Suggestions are for …" line appears once (under Reading handwriting); the
           sections keep their selectors hidden, so the model hints still follow the profile
    """
    assert not models_page._ocr_picker._hw_selector.isHidden()
    assert models_page._proofreading._hw_selector.isHidden()
    assert models_page._review._hw_selector.isHidden()


@pytest.mark.gui
def test_ai_models_visibility_reacts_to_engine(qtbot, monkeypatch) -> None:
    """
    Given  the AI models page with OPENAI_API_KEY available
    When   the method is set to tesseract (native, no model row), then to ollama
    Then   the model row is hidden, then visible again
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    page = AiModelsPage(settings_context(DummyAppService()))
    qtbot.addWidget(page)
    page.show()
    picker = page._ocr_picker

    picker._ocr_engine.setCurrentText("tesseract")
    assert picker._ocr_model_label.isVisible() is False
    assert picker._ocr_model_row.isVisible() is False

    picker._ocr_engine.setCurrentText("ollama")
    assert picker._ocr_model_label.isVisible() is True
    assert picker._ocr_model_row.isVisible() is True


@pytest.mark.gui
def test_choosing_a_method_is_reported_but_loading_is_not(qtbot) -> None:
    """
    Given  the AI models page
    When   settings with another method are loaded, and then the user picks a method
    Then   ocr_engine_changed is emitted only for the user's choice
    """
    page = AiModelsPage(settings_context(DummyAppService()))
    qtbot.addWidget(page)
    engines: list[str] = []
    page.ocr_engine_changed.connect(engines.append)

    page.load({"ocr_engine": "tesseract"})
    assert engines == []

    page._ocr_picker._ocr_engine.setCurrentText("ollama")
    assert engines == ["ollama"]


@pytest.mark.gui
def test_the_pages_values_round_trip(qtbot) -> None:
    """
    Given  saved AI settings for all three steps
    When   they are loaded and read back
    Then   the page returns the same engine, services, models and temperatures
    """
    page = AiModelsPage(settings_context(CachingDummyAppService()))
    qtbot.addWidget(page)
    saved = {
        "ocr_engine": "tesseract",
        "ocr_temperature": 0.4,
        "correction_provider": "ollama",
        "correction_model": "my-writer:7b",
        "correction_temperature": 0.3,
        "evaluate_provider": "ollama",
        "evaluate_model": "my-reviewer:7b",
        "evaluate_temperature": 1.0,
    }

    page.load(saved)

    values = page.values()
    for key, value in saved.items():
        if isinstance(value, float):
            assert values[key] == pytest.approx(value), key
        else:
            assert values[key] == value, key
    assert values["correction_model"] == "my-writer:7b"
    assert values["evaluate_model"] == "my-reviewer:7b"


@pytest.mark.gui
def test_the_same_model_tip_follows_the_other_step_live(qtbot) -> None:
    """
    Given  an 8 GB computer with proofreading and review both on the same Ollama model
    When   the teacher picks another model for review (not saved yet)
    Then   the same-model tip appears on proofreading at once, and disappears when the models
           are the same again — it reads the other step's current choice, not a snapshot
    """
    page = AiModelsPage(
        settings_context(
            DummyAppService(),
            settings={
                "hardware_profile": "8gb",
                "correction_provider": "ollama",
                "correction_model": "qwen3.5:9b",
                "evaluate_provider": "ollama",
                "evaluate_model": "qwen3.5:9b",
            },
        )
    )
    qtbot.addWidget(page)
    page.load(page._context.settings)
    with qtbot.waitSignal(page._proofreading._hw_selector.resolved_changed, timeout=5000):
        pass
    assert page._proofreading._same_model_tip_lbl.isHidden()

    page._review._model.setCurrentText("mistral:latest")
    assert not page._proofreading._same_model_tip_lbl.isHidden()

    page._review._model.setCurrentText("qwen3.5:9b")
    assert page._proofreading._same_model_tip_lbl.isHidden()


@pytest.mark.gui
def test_a_missing_package_sends_the_install_to_add_ons(qtbot, monkeypatch) -> None:
    """
    Given  Mistral chosen for proofreading while its package is not installed
    When   the hint's "Install…" is pressed
    Then   the page asks the window for Add-ons with the Mistral package preselected, and no
           dialog is opened; after an install the hint is checked again
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    svc.missing = {"mistral": "langchain-mistralai"}
    page = AiModelsPage(settings_context(svc, settings={"correction_provider": "mistral"}))
    qtbot.addWidget(page)
    page.load({"correction_provider": "mistral"})
    page.show()
    hint = page._proofreading._package_hint
    assert "Mistral support is not installed yet." in hint.message()
    requests: list[tuple] = []
    page.navigate_requested.connect(lambda *args: requests.append(args))

    hint._button.click()

    assert requests == [(SettingsPage.ADDONS, FOCUS_LOCAL_MODELS, ["provider:mistral"])]

    svc.missing = {}  # the user installed it
    page.on_packages_changed()
    assert hint.isHidden()


@pytest.mark.gui
def test_the_api_keys_are_saved_only_when_the_window_applies(
    qtbot, monkeypatch, credential_file
) -> None:
    """
    Given  a key typed on the Where the AI runs page
    When   the window has not applied the page yet, and then applies it (OK)
    Then   nothing is stored before, and the key is stored afterwards
    """
    monkeypatch.setattr(
        os, "environ", {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    )
    page = AiSetupPage(settings_context(ProviderAppService()))
    qtbot.addWidget(page)
    page._credentials._add_combo.setCurrentIndex(
        page._credentials._add_combo.findData("OPENAI_API_KEY")
    )
    page._credentials._add_btn.click()
    page._credentials.field("OPENAI_API_KEY").setText("sk-typed")
    assert credential_file == {}

    page.apply()

    assert credential_file == {"credentials/OPENAI_API_KEY": "sk-typed"}


@pytest.mark.gui
def test_ai_models_points_to_online_services_while_a_key_is_missing(qtbot, monkeypatch) -> None:
    """
    Given  an online AI service without a key (OpenAI) and the AI models page
    When   the page is created, and the hint's link is followed
    Then   a hint explains why the service is not offered, and the link opens Where the AI runs
           on its API keys; with every key set the hint is not there
    """
    monkeypatch.setattr(
        os, "environ", {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    )
    page = AiModelsPage(settings_context(ProviderAppService()))
    qtbot.addWidget(page)
    requested: list[tuple] = []
    page.navigate_requested.connect(lambda *args: requested.append(args))

    hint = next(label for label in page.findChildren(QLabel) if "Where the AI runs" in label.text())
    hint.linkActivated.emit("keys")

    assert requested == [(SettingsPage.AI_SETUP, "api_keys", [])]
    assert not hasattr(page, "_credentials")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("MISTRAL_API_KEY", "m-x")
    complete = AiModelsPage(settings_context(ProviderAppService()))
    qtbot.addWidget(complete)
    assert not any("Where the AI runs" in label.text() for label in complete.findChildren(QLabel))


# ── AI models: temperatures behind "Expert settings" ──────────────────────────


@pytest.mark.gui
def test_the_temperatures_sit_in_one_collapsed_expert_section(qtbot) -> None:
    """
    Given  the AI models page with default temperatures
    When   it is created
    Then   all three temperatures are inside one "Expert settings" section, which is collapsed
    """
    page = AiModelsPage(settings_context(DummyAppService()))
    qtbot.addWidget(page)
    page.load({})

    section = page._expert_section
    assert section._toggle.text() == "Expert settings"
    assert not section.expanded
    for box in (page._ocr_temperature, page._correction_temperature, page._evaluate_temperature):
        assert box.parent() is section.content_widget
    values = page.values()
    assert values["ocr_temperature"] == DEFAULTS["ocr_temperature"]
    assert values["correction_temperature"] == DEFAULTS["correction_temperature"]
    assert values["evaluate_temperature"] == DEFAULTS["evaluate_temperature"]


@pytest.mark.gui
@pytest.mark.parametrize(
    "key", ["ocr_temperature", "correction_temperature", "evaluate_temperature"]
)
def test_expert_settings_open_when_a_saved_temperature_is_not_the_default(qtbot, key) -> None:
    """
    Given  a saved temperature other than the default (any of the three)
    When   the page is created
    Then   "Expert settings" is open, so the chosen value is visible, and it is returned
    """
    page = AiModelsPage(settings_context(DummyAppService()))
    qtbot.addWidget(page)

    page.load({key: 0.9})

    assert page._expert_section.expanded
    assert page.values()[key] == pytest.approx(0.9)


@pytest.mark.gui
def test_changed_temperatures_are_returned(qtbot) -> None:
    """
    Given  the AI models page
    When   the three temperatures are changed
    Then   values() returns each under its own key
    """
    page = AiModelsPage(settings_context(DummyAppService()))
    qtbot.addWidget(page)
    page._ocr_temperature.setValue(0.5)
    page._correction_temperature.setValue(0.3)
    page._evaluate_temperature.setValue(1.0)

    values = page.values()

    assert values["ocr_temperature"] == pytest.approx(0.5)
    assert values["correction_temperature"] == pytest.approx(0.3)
    assert values["evaluate_temperature"] == pytest.approx(1.0)


@pytest.mark.gui
def test_ocr_selector_temperature_is_collapsed_but_still_saved(qtbot) -> None:
    """
    Given  the shared OCR selector (the Benchmark app's)
    When   a temperature is set while the section is collapsed
    Then   it is returned by get_ocr_config()
    """
    selector = OcrConfigSelector(app_service=DummyAppService())
    qtbot.addWidget(selector)
    assert not selector._scan._expert_section.expanded
    selector._scan._ocr_temperature.setValue(0.4)
    assert selector.get_ocr_config().temperature == pytest.approx(0.4)


@pytest.mark.gui
def test_benchmark_selector_shows_the_temperature_without_expanding(qtbot) -> None:
    """
    Given  the OCR selector as the Benchmark window builds it
    When   it is created
    Then   "Expert settings" is open, so a benchmark run can set the temperature directly
    """
    selector = OcrConfigSelector(app_service=DummyAppService(), expert_collapsed=False)
    qtbot.addWidget(selector)
    assert selector._scan._expert_section.expanded


# ── Scan quality ───────────────────────────────────────────────────────────────


@pytest.fixture
def scan_page(qtbot) -> ScanQualityPage:
    page = ScanQualityPage(settings_context(DummyAppService()))
    qtbot.addWidget(page)
    page.show()
    return page


@pytest.mark.gui
def test_scan_quality_has_the_preparation_options(scan_page) -> None:
    """
    Given  the Scan quality page
    When   it is created
    Then   it has the corrections, enhancements and PDF options, and no temperature of its own
           (that sits in the Expert section of AI models); the PDF options are in its own
           collapsed Expert section, not among the everyday options
    """
    scan = scan_page._controls
    for box in (scan._dewarp, scan._deskew, scan._border_crop, scan._denoise, scan._gamma):
        assert box is not None
    assert scan._pdf_dpi is not None
    assert scan._pdf_text_layer is not None
    assert not scan._ocr_temperature.isVisible()
    assert scan._expert_section is None
    expert = scan_page._expert_section
    assert not expert.expanded
    assert expert.content_widget.isAncestorOf(scan._pdf_dpi)
    assert expert.content_widget.isAncestorOf(scan._pdf_text_layer)


@pytest.mark.gui
def test_scan_quality_returns_its_keys_with_the_right_types(scan_page) -> None:
    """
    Given  the Scan quality page
    When   values() is called
    Then   it returns the preparation keys including debug, and nothing of the AI models page
    """
    values = scan_page.values()

    for key in (
        "preprocess_method",
        "preprocess_dewarp",
        "preprocess_deskew",
        "preprocess_border_crop",
        "preprocess_denoise",
        "preprocess_gamma",
        "pdf_render_dpi",
        "pdf_text_layer",
        "debug",
    ):
        assert key in values
    assert isinstance(values["preprocess_dewarp"], bool)
    assert isinstance(values["pdf_render_dpi"], int)
    assert "ocr_engine" not in values
    assert "ocr_temperature" not in values
    assert "correction_provider" not in values


@pytest.mark.gui
def test_scan_quality_checkbox_defaults(scan_page) -> None:
    """
    Given  the Scan quality page
    When   it is created
    Then   dewarp, deskew and denoise are pre-selected; border crop and gamma are not
    """
    values = scan_page.values()
    assert values["preprocess_dewarp"] is True
    assert values["preprocess_deskew"] is True
    assert values["preprocess_border_crop"] is False
    assert values["preprocess_denoise"] is True
    assert values["preprocess_gamma"] is False


@pytest.mark.gui
def test_scan_quality_round_trips_saved_values(scan_page) -> None:
    """
    Given  saved preparation settings
    When   they are loaded and read back
    Then   the page returns them unchanged, and opens "Expert settings" for a debug choice
    """
    saved = {
        "preprocess_method": "clahe",
        "preprocess_dewarp": False,
        "preprocess_border_crop": True,
        "pdf_render_dpi": 144,
        "pdf_text_layer": "never",
        "debug": True,
    }

    scan_page.load(saved)

    values = scan_page.values()
    for key, value in saved.items():
        assert values[key] == value
    assert scan_page._expert_section.expanded


@pytest.mark.gui
def test_preview_is_requested_through_the_window(scan_page) -> None:
    """
    Given  the Scan quality page
    When   Preview… is pressed
    Then   preview_requested is emitted (the window adds the recognition choice)
    """
    button = next(b for b in scan_page.findChildren(QPushButton) if b.text() == "Preview…")

    seen: list[bool] = []
    scan_page.preview_requested.connect(lambda: seen.append(True))
    button.click()

    assert seen == [True]


@pytest.mark.gui
def test_ocr_config_combines_the_recognition_choice_with_the_scan_choices(scan_page) -> None:
    """
    Given  the Scan quality page with another preset and DPI
    When   an OcrConfig is built for a method, a model and a temperature from AI models
    Then   it holds all of them
    """
    scan_page.load({"preprocess_method": "none", "pdf_render_dpi": 144})

    config = scan_page.ocr_config("tesseract", "some-model", 0.7)

    assert (config.engine, config.model) == ("tesseract", "some-model")
    assert config.temperature == pytest.approx(0.7)
    assert config.preprocess_method == "none"
    assert config.pdf_render_dpi == 144


@pytest.mark.gui
def test_the_default_preparation_follows_the_engine(scan_page) -> None:
    """
    Given  the Scan quality page
    When   the window applies the default preparation of tesseract
    Then   the preset is the one tesseract works best with
    """
    scan_page.set_engine_default("tesseract")

    assert scan_page.values()["preprocess_method"] == "clahe"


# ── Where the AI runs ──────────────────────────────────────────────────────────────


@pytest.fixture
def computer_page(qtbot) -> AiSetupPage:
    page = AiSetupPage(settings_context(DummyAppService()))
    qtbot.addWidget(page)
    page.show()
    return page


@pytest.mark.gui
def test_this_computer_owns_only_the_server_and_the_profile(computer_page) -> None:
    """
    Given  the Where the AI runs page
    When   it is created
    Then   it has a server field, a "Download a model…" button and the editable profile; it
           owns exactly ollama_url and hardware_profile
    """
    assert computer_page._ollama_url is not None
    assert computer_page._download_model_btn is not None
    assert computer_page._hardware_selector._combo is not None
    assert computer_page.owned_keys() == {"ollama_url", "hardware_profile"}


@pytest.mark.gui
def test_download_a_model_goes_to_add_ons(computer_page) -> None:
    """
    Given  the Where the AI runs page
    When   "Download a model…" is pressed
    Then   the page asks the window for Add-ons on the local models, without a dialog
    """
    requests: list[tuple] = []
    computer_page.navigate_requested.connect(lambda *args: requests.append(args))

    computer_page._download_model_btn.click()

    assert requests == [(SettingsPage.ADDONS, FOCUS_LOCAL_MODELS, [])]


@pytest.mark.gui
def test_this_computer_returns_the_server_address(computer_page) -> None:
    """
    Given  a custom server address typed in
    When   values() is called
    Then   it returns that address and the profile, and nothing else
    """
    computer_page._ollama_url.setText("http://custom:11434")

    values = computer_page.values()

    assert values == {"ollama_url": "http://custom:11434", "hardware_profile": "auto"}


@pytest.mark.gui
def test_a_blank_server_address_means_the_default(computer_page) -> None:
    """
    Given  a blank server address
    When   values() is called
    Then   the default address is returned
    """
    computer_page._ollama_url.setText("")

    assert computer_page.values()["ollama_url"] == DEFAULTS["ollama_url"]


@pytest.mark.use_case("Hardware_Profile_Override")
def test_this_computer_returns_the_profile(computer_page) -> None:
    """
    Given  the Where the AI runs page with its "My computer" selector
    When   values() is called
    Then   hardware_profile is one of the four profile settings
    """
    assert computer_page.values()["hardware_profile"] in ("auto", "cpu", "8gb", "16gb")


@pytest.mark.use_case("Hardware_Profile_Override")
def test_typing_the_ollama_url_does_not_redetect_until_editing_finished(qtbot) -> None:
    """
    Given  the Where the AI runs page in automatic hardware mode
    When   the user types a new server address character by character
    Then   no detection runs per keystroke, and the address is announced once when editing
           finished (a half-typed address used to re-probe the GPU)
    """
    svc = DummyAppService()
    page = AiSetupPage(
        settings_context(
            svc, settings={"ollama_url": DEFAULTS["ollama_url"], "hardware_profile": "auto"}
        )
    )
    qtbot.addWidget(page)
    announced: list[str] = []
    page.ollama_url_changed.connect(announced.append)
    with qtbot.waitSignal(page._hardware_selector.resolved_changed, timeout=5000):
        pass
    calls_before = len(svc.detect_calls)

    for partial in ("h", "ht", "http", "http://192.168", "http://192.168.1.50:11434"):
        page._ollama_url.setText(partial)
    qtbot.wait(100)
    assert len(svc.detect_calls) == calls_before
    assert announced == []

    with qtbot.waitSignal(page._hardware_selector.resolved_changed, timeout=5000):
        page._ollama_url.editingFinished.emit()
    assert svc.detect_calls[-1][0] == "http://192.168.1.50:11434"
    assert len(svc.detect_calls) == calls_before + 1
    assert announced == ["http://192.168.1.50:11434"]

    page._ollama_url.editingFinished.emit()  # focus left again without a change
    assert announced == ["http://192.168.1.50:11434"]


@pytest.mark.use_case("Hardware_Profile_Override")
def test_changing_the_profile_is_announced(computer_page, qtbot) -> None:
    """
    Given  the Where the AI runs page
    When   the user picks another "My computer" choice
    Then   hardware_setting_changed carries the internal value
    """
    with qtbot.waitSignal(computer_page.hardware_setting_changed, timeout=5000) as blocker:
        computer_page._hardware_selector._combo.setCurrentText(
            "Basic computer or small graphics card (under 8 GB)"
        )
    assert blocker.args == ["cpu"]


@pytest.mark.use_case("Hardware_Profile_Override")
def test_detect_gpu_button_uses_the_shared_service(qtbot) -> None:
    """
    Given  the Where the AI runs page with an injected service
    When   the "Detect GPU" button runs
    Then   the injected service answers (so its cached probe is reused) instead of a freshly
           constructed one probing the machine again
    """

    class _Gpu:
        name = "RTX 4060"
        vendor = "nvidia"

    class _GpuService(DummyAppService):
        def detect_gpus(self, *, refresh: bool = False) -> list:
            super().detect_gpus(refresh=refresh)
            return [_Gpu()]

    page = AiSetupPage(settings_context(_GpuService(), settings={}))
    qtbot.addWidget(page)

    page._on_detect_gpu()
    qtbot.waitUntil(lambda: "RTX 4060" in page._gpu_status_lbl.text(), timeout=5000)
    assert page._gpu_detect_thread is not None
    page._gpu_detect_thread.wait(5000)


@pytest.mark.use_case("Hardware_Profile_Override")
def test_detect_gpu_button_probes_again_and_refreshes_my_computer(qtbot) -> None:
    """
    Given  the Where the AI runs page whose first probe already ran
    When   the user clicks "Detect GPU"
    Then   the service is asked to probe again (refresh=True — a driver may have been
           installed since) and "My computer" resolves again from that fresh probe
    """
    svc = DummyAppService()
    page = AiSetupPage(
        settings_context(
            svc, settings={"ollama_url": DEFAULTS["ollama_url"], "hardware_profile": "auto"}
        )
    )
    qtbot.addWidget(page)
    qtbot.waitUntil(lambda: len(svc.detect_calls) == 1, timeout=5000)

    page._on_detect_gpu()
    qtbot.waitUntil(
        lambda: page._gpu_status_lbl.text() == "No NVIDIA or AMD GPU detected.", timeout=5000
    )
    assert svc.gpu_calls == [True]
    qtbot.waitUntil(lambda: len(svc.detect_calls) == 2, timeout=5000)
    assert svc.detect_calls[-1][1] is False  # re-resolved from the probe, no second probe
    assert page._gpu_detect_thread is not None
    page._gpu_detect_thread.wait(5000)


@pytest.mark.use_case("Hardware_Profile_Override")
def test_this_computer_detects_hardware_for_the_configured_url(qtbot) -> None:
    """
    Given  saved settings with Ollama on another computer
    When   the Where the AI runs page opens
    Then   the first "My computer" detection already uses that URL (not the default), so the
           status text describes the right machine
    """
    url = "http://192.168.1.50:11434"
    svc = DummyAppService()
    page = AiSetupPage(
        settings_context(svc, settings={"ollama_url": url, "hardware_profile": "auto"})
    )
    qtbot.addWidget(page)
    qtbot.waitUntil(lambda: len(svc.detect_calls) >= 1, timeout=5000)
    assert svc.detect_calls[0][0] == url
    assert page._ollama_url.text() == url


@pytest.mark.gui
def test_restoring_this_computer_lets_the_other_pages_follow(computer_page, qtbot) -> None:
    """
    Given  a page showing a custom server and the 16 GB profile
    When   "Restore defaults → This page" resets it
    Then   it announces the default address and profile, as after an edit, so the other pages
           follow
    """
    computer_page.load({"ollama_url": "http://custom:11434", "hardware_profile": "16gb"})
    urls: list[str] = []
    profiles: list[str] = []
    computer_page.ollama_url_changed.connect(urls.append)
    computer_page.hardware_setting_changed.connect(profiles.append)

    computer_page.restore_defaults()

    assert urls == [DEFAULTS["ollama_url"]]
    assert profiles == [DEFAULTS["hardware_profile"]]


@pytest.mark.gui
def test_shutdown_parks_a_running_gpu_check(qtbot) -> None:
    """
    Given  a GPU check that is still running
    When   the page shuts down
    Then   the thread is parked (never destroyed while running) and not waited for
    """
    release = threading.Event()

    class _SlowGpu(DummyAppService):
        def detect_gpus(self, *, refresh: bool = False) -> list:
            release.wait(10)
            return []

    page = AiSetupPage(settings_context(_SlowGpu(), settings={}))
    qtbot.addWidget(page)
    page._on_detect_gpu()
    thread = page._gpu_detect_thread
    assert thread is not None and thread.isRunning()

    page.shutdown()

    assert thread in _model_fetch._RETIRED_THREADS
    release.set()
    qtbot.waitUntil(thread.isFinished, timeout=5000)


# ── API keys ───────────────────────────────────────────────────────────────────


def _credentials(qtbot, monkeypatch, service=None) -> CredentialsGroup:
    for name in ("OPENAI_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    group = CredentialsGroup(service or ProviderAppService())
    qtbot.addWidget(group)
    group.resize(520, 700)
    group.show()
    return group


@pytest.mark.gui
def test_credentials_group_has_a_row_for_a_key_that_is_already_set(qtbot, monkeypatch) -> None:
    """
    Given  a provider that declares an env_key, which is already set in the environment
    When   the group is created
    Then   an input field exists for that key
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-pre-existing")
    group = CredentialsGroup(DummyAppService())
    qtbot.addWidget(group)

    assert group.field("OPENAI_API_KEY") is not None


@pytest.mark.gui
def test_applying_saves_the_key_to_environment_and_store(
    qtbot, monkeypatch, credential_file
) -> None:
    """
    Given  a credentials row for OPENAI_API_KEY with a new value entered
    When   the group is applied
    Then   os.environ is updated with the key and the store holds it
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old")
    group = CredentialsGroup(DummyAppService())
    qtbot.addWidget(group)
    group.field("OPENAI_API_KEY").setText("sk-test-key")

    group.apply()

    assert credential_file == {"credentials/OPENAI_API_KEY": "sk-test-key"}
    assert os.environ.get("OPENAI_API_KEY") == "sk-test-key"


@pytest.mark.gui
def test_applying_leaves_a_key_nobody_touched_alone(qtbot, monkeypatch, credential_file) -> None:
    """
    Given  an API key that is only set in the environment for this session
    When   the group is applied without the field being edited
    Then   the key is not stored (it must not be written just because OK was pressed), and it
           stays in the environment
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-session-only")
    group = CredentialsGroup(DummyAppService())
    qtbot.addWidget(group)

    group.apply()

    assert credential_file == {}
    assert os.environ.get("OPENAI_API_KEY") == "sk-session-only"


@pytest.mark.gui
def test_applying_keeps_a_stored_key_that_was_not_edited(
    qtbot, monkeypatch, credential_file
) -> None:
    """
    Given  an API key stored by an earlier session (and not in the environment)
    When   the group is applied without the field being edited
    Then   the row shows the stored key, and the stored key is neither rewritten nor removed
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    credential_file["credentials/OPENAI_API_KEY"] = "sk-stored"
    group = CredentialsGroup(DummyAppService())
    qtbot.addWidget(group)
    assert group.field("OPENAI_API_KEY").text() == "sk-stored"

    group.apply()

    assert credential_file == {"credentials/OPENAI_API_KEY": "sk-stored"}


@pytest.mark.gui
def test_applying_an_emptied_key_removes_it(qtbot, monkeypatch, credential_file) -> None:
    """
    Given  an API key that is stored and set in the environment
    When   its field is emptied and the group is applied
    Then   os.environ no longer contains the key and the store forgets it
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old")
    credential_file["credentials/OPENAI_API_KEY"] = "sk-old"
    group = CredentialsGroup(DummyAppService())
    qtbot.addWidget(group)
    group.field("OPENAI_API_KEY").setText("")

    group.apply()

    assert "OPENAI_API_KEY" not in os.environ
    assert credential_file == {}


@pytest.mark.gui
def test_the_note_says_where_the_keys_are_kept(qtbot, monkeypatch) -> None:
    """
    Given  a computer without a system keychain, and one with a keychain
    When   the credentials group is created on each
    Then   the note admits the plain-text settings file in the first case, and names the
           keychain in the second
    """
    from teachers_teammate.gui import _credential_store  # noqa: PLC0415

    plain = CredentialsGroup(DummyAppService())
    qtbot.addWidget(plain)
    plain_text = " ".join(label.text() for label in plain.findChildren(QLabel))
    assert "not encrypted" in plain_text

    monkeypatch.setattr(_credential_store, "_keyring", lambda: FakeKeyring())
    safe = CredentialsGroup(DummyAppService())
    qtbot.addWidget(safe)
    safe_text = " ".join(label.text() for label in safe.findChildren(QLabel))
    assert "keychain" in safe_text
    assert "not encrypted" not in safe_text


@pytest.mark.gui
def test_adding_a_key_row_gives_a_field_wide_enough_to_paste_into(qtbot, monkeypatch) -> None:
    """
    Given  a credentials group with no key saved yet
    When   a key row is added for Mistral
    Then   the field is wide (the old table squeezed it to a sliver), the service is named in
           full above it, and the Remove button is not cut off
    """
    group = _credentials(qtbot, monkeypatch)
    group._add_combo.setCurrentIndex(group._add_combo.findData("MISTRAL_API_KEY"))

    group._add_btn.click()

    row = group._rows[0]
    assert row.field.width() >= 200
    assert row.field.placeholderText() == "Paste your API key here"
    labels = " ".join(label.text() for label in row.findChildren(QLabel))
    assert "Mistral · Mistral (EU)" in labels
    remove = next(b for b in row.findChildren(QPushButton) if b.text() == "Remove")
    assert remove.width() >= remove.sizeHint().width()


@pytest.mark.gui
def test_providers_sharing_a_key_get_one_row_and_a_used_key_leaves_the_picker(
    qtbot, monkeypatch
) -> None:
    """
    Given  Mistral and Mistral (EU), which share MISTRAL_API_KEY
    When   the key is added
    Then   one row serves both and the picker no longer offers that key; removing the row
           offers it again
    """
    group = _credentials(qtbot, monkeypatch)
    keys = [group._add_combo.itemData(i) for i in range(group._add_combo.count())]
    assert keys.count("MISTRAL_API_KEY") == 1

    group._add_combo.setCurrentIndex(keys.index("MISTRAL_API_KEY"))
    group._add_btn.click()

    assert [row.env_key for row in group._rows] == ["MISTRAL_API_KEY"]
    assert group._add_combo.findData("MISTRAL_API_KEY") < 0

    group._remove_row(group._rows[0])

    assert group._rows == []
    assert group._add_combo.findData("MISTRAL_API_KEY") >= 0


@pytest.mark.gui
def test_the_key_can_be_shown_to_check_what_was_pasted(qtbot, monkeypatch) -> None:
    """
    Given  a key row with a pasted key
    When   Show is pressed and then Hide
    Then   the key is readable while shown and masked again afterwards
    """
    from PySide6.QtWidgets import QLineEdit  # noqa: PLC0415

    group = _credentials(qtbot, monkeypatch)
    group._add_btn.click()
    row = group._rows[0]
    row.field.setText("sk-abc")
    assert row.field.echoMode() == QLineEdit.EchoMode.Password

    row._reveal.click()
    assert row.field.echoMode() == QLineEdit.EchoMode.Normal
    assert row._reveal.text() == "Hide"

    row._reveal.click()
    assert row.field.echoMode() == QLineEdit.EchoMode.Password
    assert row._reveal.text() == "Show"


@pytest.mark.gui
def test_the_picker_disappears_when_every_service_has_a_key(qtbot, monkeypatch) -> None:
    """
    Given  a credentials group
    When   a row is added for every service that needs a key
    Then   the picker is hidden (there is nothing left to add)
    """
    group = _credentials(qtbot, monkeypatch)
    while group._add_combo.count():
        group._add_btn.click()

    assert group._add_row.isHidden()


# ── AI service section: provider switch, model list source, install hint ─────


@pytest.mark.gui
def test_section_get_values_returns_provider_and_model(qtbot) -> None:
    """
    Given  a proofreading section and a content review section
    When   values() is called
    Then   each returns only its own provider and model keys
    """
    proofreading = _proofreading(qtbot, values={"correction_provider": "ollama"})
    review = _section(qtbot, CONTENT_REVIEW, DummyAppService(), {"evaluate_provider": "ollama"})

    assert set(proofreading.values()) == {"correction_provider", "correction_model"}
    assert set(review.values()) == {"evaluate_provider", "evaluate_model"}


@pytest.mark.gui
def test_on_provider_changed_seeds_the_model(qtbot, monkeypatch) -> None:
    """
    Given  a section
    When   the provider changes
    Then   the model combo is seeded with the provider's models
    """
    section = _proofreading(qtbot, values={"correction_provider": "ollama"})
    monkeypatch.setattr(
        section._app_service,
        "get_provider_info",
        lambda p: {
            "models": [f"{p}-model-1", f"{p}-model-2"],
            "default_model": f"{p}-model-1",
            "needs_api_key": False,
            "env_key": "",
        },
    )

    section._on_provider_changed("openai")

    assert "openai-model-1" in _items(section)


@pytest.mark.gui
def test_a_fetched_listing_is_shown_in_the_combo(qtbot) -> None:
    """
    Given  a section
    When   a live listing is shown
    Then   the model combo is updated and the status shows green
    """
    section = _proofreading(qtbot, values={"correction_provider": "ollama"})

    section._show_listing(ModelListing(["gpt-4o", "gpt-3.5-turbo"]))

    assert "gpt-4o" in _items(section)
    assert "#27ae60" in section._status_lbl.text()


@pytest.mark.gui
def test_a_saved_service_without_a_key_stays_chosen(qtbot, monkeypatch) -> None:
    """
    Given  proofreading saved with Mistral and a model, but no Mistral API key is set (so the
           service is not offered for a new choice)
    When   the section loads the saved settings
    Then   Mistral and its model stay chosen, instead of a model name of Mistral being
           saved for the first service in the list
    """
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    section = _proofreading(
        qtbot,
        ProviderAppService(),
        values={"correction_provider": "mistral", "correction_model": "open-mistral-nemo"},
    )

    assert section.values() == {
        "correction_provider": "mistral",
        "correction_model": "open-mistral-nemo",
    }


@pytest.mark.gui
def test_an_unknown_saved_service_does_not_lend_its_model_to_another(qtbot) -> None:
    """
    Given  proofreading saved with a service this version does not know, and a model of it
    When   the section loads the saved settings
    Then   the first service is shown with its own default model, not the unknown one's model
    """
    section = _proofreading(
        qtbot,
        DummyAppService(),
        values={"correction_provider": "gone-service", "correction_model": "gone-model"},
    )

    assert section.values()["correction_provider"] == "ollama"
    assert section.values()["correction_model"] != "gone-model"


@pytest.mark.gui
def test_the_status_is_red_on_an_empty_fetch(qtbot) -> None:
    """
    Given  a section
    When   an empty model list is shown
    Then   the status label shows a red error indicator
    """
    section = _proofreading(qtbot, values={"correction_provider": "ollama"})

    section._show_listing(ModelListing([]))

    assert "#c0392b" in section._status_lbl.text()


@pytest.mark.gui
def test_models_fetched_has_no_placeholder(qtbot) -> None:
    """
    Given  a section
    When   a list of models is shown
    Then   the combo lists only real models, sorted by name, with no default placeholder row
    """
    section = _proofreading(qtbot, values={"correction_provider": "ollama"})

    section._show_listing(ModelListing(["gpt-4o", "gpt-3.5"]))

    assert "— default —" not in _items(section)
    assert _items(section) == ["gpt-3.5", "gpt-4o"]  # unknown models: stable, name-sorted order


@pytest.mark.gui
def test_the_selected_model_is_returned(qtbot) -> None:
    """
    Given  a section with a fetched list
    When   the first model is selected, and then another typed
    Then   values() returns the selected name unchanged (no placeholder-to-empty mapping)
    """
    section = _proofreading(qtbot, values={"correction_provider": "ollama"})
    section._show_listing(ModelListing(["gpt-4o", "gpt-3.5"]))

    section._model.setCurrentIndex(0)
    assert section.values()["correction_model"] == "gpt-3.5"

    section._model.setCurrentText("gpt-4o")
    assert section.values()["correction_model"] == "gpt-4o"


@pytest.mark.gui
def test_changing_the_provider_preselects_its_default_model(qtbot, monkeypatch) -> None:
    """
    Given  a section with no prior model selection
    When   the provider changes
    Then   the provider's default model is pre-selected in the combo
    """
    section = _proofreading(qtbot, values={"correction_provider": "ollama"})
    monkeypatch.setattr(
        section._app_service,
        "get_provider_info",
        lambda p: {
            "models": [f"{p}-model-1", f"{p}-model-2"],
            "default_model": f"{p}-model-2",
            "needs_api_key": False,
            "env_key": "",
        },
    )
    section._model.setCurrentText("")

    section._on_provider_changed("openai")

    assert section._model.currentText() == "openai-model-2"


@pytest.mark.gui
def test_cache_hit_skips_a_new_thread_for_a_cloud_provider(qtbot, monkeypatch) -> None:
    """
    Given  models are already cached for a cloud provider (openai)
    When   _auto_fetch is called
    Then   the cached list is used (no new thread is started)
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    svc = CachingDummyAppService()
    section = _proofreading(
        qtbot, svc, {"correction_provider": "openai", "ollama_url": DEFAULTS["ollama_url"]}
    )
    if section._fetch_thread is not None:
        section._fetch_thread.wait(5000)
    svc.seed_cache("openai", ["gpt-4o"], "")
    old_thread = section._fetch_thread

    section._auto_fetch()

    assert section._fetch_thread is old_thread


@pytest.mark.gui
def test_ollama_always_fetches_in_a_thread(qtbot) -> None:
    """
    Given  Ollama models are already cached
    When   _auto_fetch is called
    Then   a thread is started anyway: classifying custom models must never run on the GUI
           thread, so Ollama has no synchronous cache shortcut
    """
    svc = CachingDummyAppService()
    url = DEFAULTS["ollama_url"]
    section = _proofreading(qtbot, svc, {"correction_provider": "ollama", "ollama_url": url})
    first = section._fetch_thread
    assert first is not None
    first.wait(5000)
    svc.seed_cache("ollama", ["llama3:latest"], url)

    section._auto_fetch()

    second = section._fetch_thread
    assert second is not None
    assert second is not first
    second.wait(5000)


@pytest.mark.gui
def test_provider_change_triggers_cache_invalidation(qtbot) -> None:
    """
    Given  a provider's models are cached
    When   _on_provider_changed is called for that provider
    Then   the cache entry is invalidated
    """
    svc = CachingDummyAppService()
    svc.seed_cache("ollama", ["old-model"], DEFAULTS["ollama_url"])
    section = _proofreading(qtbot, svc, {"correction_provider": "ollama"})

    section._on_provider_changed("ollama")

    assert svc.get_cached_models("ollama", base_url=DEFAULTS["ollama_url"]) is None


@pytest.mark.gui
@pytest.mark.parametrize("spec", _SPECS, ids=["proofreading", "review"])
def test_switching_provider_asks_the_new_service_and_drops_the_old_model_name(
    qtbot, monkeypatch, spec
) -> None:
    """
    Given  a section on Ollama with an Ollama model typed in, and a Mistral key set
    When   the user picks Mistral as the AI service
    Then   Mistral is asked for its models (the live list replaces the built-in one) and the
           Ollama tag does not stay in the model field
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    section = _section(
        qtbot,
        spec,
        svc,
        {spec.provider_key: "ollama", spec.model_key: "qwen3.5:9b"},
    )
    assert section._model.currentText() == "qwen3.5:9b"

    section._provider.setCurrentText("mistral")
    _wait_for_fetch(qtbot, section, svc, calls=1)

    assert svc.checked_calls == ["mistral"]
    assert _items(section) == ["glm-live", "mistral-large-latest"]
    assert section._model.currentText() == "mistral-small-latest"  # the default, not the tag
    assert "#27ae60" in section._status_lbl.text()


@pytest.mark.gui
def test_a_failed_listing_without_any_models_still_says_why(qtbot, monkeypatch) -> None:
    """
    Given  a service whose answer failed and carries no built-in models to fall back on
    When   the section shows it
    Then   the status line gives the reason and "Try again", not just a dash
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    svc.listing = ModelListing([], "failed", "the model list could not be read")
    section = _proofreading(qtbot, svc, {"correction_provider": "mistral"})
    _wait_for_fetch(qtbot, section, svc, calls=1)

    status = section._status_lbl.text()
    assert "no models to show" in status
    assert "the model list could not be read" in status
    assert "Try again" in status


@pytest.mark.gui
def test_picking_an_online_provider_does_not_throw_away_its_remembered_model_list(
    qtbot, monkeypatch
) -> None:
    """
    Given  a section on Ollama and a Mistral key
    When   the user picks Mistral
    Then   the remembered Mistral list is not dropped (only "Try again" does that), so a list
           fetched a moment ago is shown again without asking the online service once more
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    section = _proofreading(qtbot, svc, {"correction_provider": "ollama"})

    section._provider.setCurrentText("mistral")
    _wait_for_fetch(qtbot, section, svc, calls=1)

    assert "mistral" not in svc.invalidated


@pytest.mark.gui
def test_loading_asks_the_service_once(qtbot, monkeypatch) -> None:
    """
    Given  a saved provider that is not the first in the list
    When   the section loads it
    Then   the service is asked exactly once, for the saved provider (selecting it while
           loading must not start a fetch of its own)
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    section = _proofreading(
        qtbot, svc, {"correction_provider": "mistral_eu", "correction_model": "glm-live"}
    )

    _wait_for_fetch(qtbot, section, svc, calls=1)

    assert svc.checked_calls == ["mistral_eu"]
    assert section._model.currentText() == "glm-live"


@pytest.mark.gui
def test_a_late_answer_for_the_previous_service_is_ignored(qtbot, monkeypatch) -> None:
    """
    Given  a section that has moved from OpenAI to Mistral
    When   the answer of the earlier OpenAI request arrives afterwards
    Then   it does not touch the model list or the status of Mistral
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    section = _proofreading(qtbot, svc, {"correction_provider": "mistral"})
    _wait_for_fetch(qtbot, section, svc, calls=1)
    before = _items(section)
    status = section._status_lbl.text()

    section._on_listing_ready("openai", ModelListing(["gpt-late"]))

    assert _items(section) == before
    assert section._status_lbl.text() == status


@pytest.mark.gui
def test_a_failed_listing_is_shown_as_the_built_in_list_with_the_reason(qtbot, monkeypatch) -> None:
    """
    Given  Mistral cannot be asked because the key is refused
    When   the section shows the models
    Then   the status says it is the built-in list and why (not a green "N models"), and
           offers to try again, which asks the service again
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-wrong")
    svc = ProviderAppService()
    svc.listing = ModelListing(
        ["mistral-small-latest", "open-mistral-nemo"], "failed", "Mistral did not accept the key"
    )
    section = _proofreading(qtbot, svc, {"correction_provider": "mistral"})
    _wait_for_fetch(qtbot, section, svc, calls=1)

    status = section._status_lbl.text()
    assert "built-in list of 2 models" in status
    assert "Mistral did not accept the key" in status
    assert "Try again" in status
    assert "#27ae60" not in status

    section._status_lbl.linkActivated.emit("retry")
    _wait_for_fetch(qtbot, section, svc, calls=2)

    assert svc.checked_calls == ["mistral", "mistral"]
    assert svc.invalidated[-1] == "mistral"  # the provider asked, not always "ollama"


@pytest.mark.gui
@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        (ModelListing(["a", "b"], "live"), "2 models"),
        (ModelListing(["a"], "builtin"), "1 model · built-in list"),
    ],
)
def test_live_and_by_design_built_in_lists_are_told_apart(
    qtbot, monkeypatch, listing: ModelListing, expected: str
) -> None:
    """
    Given  a list the service returned, or the built-in list of a service that cannot be asked
    When   the section shows it
    Then   the first is a green count and the second says "built-in list" in grey
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    section = _proofreading(qtbot, svc, {"correction_provider": "mistral"})
    _wait_for_fetch(qtbot, section, svc, calls=1)

    section._show_listing(listing)

    assert expected in section._status_lbl.text()


@pytest.mark.gui
def test_switching_provider_shows_a_busy_bar_and_names_the_service(qtbot, monkeypatch) -> None:
    """
    Given  a section on Ollama and a Mistral whose answer is held back
    When   the user picks Mistral
    Then   a busy bar appears, the status names Mistral, the Ollama hints are hidden, and the
           field holds only the model to keep (the built-in names are not passed off as the
           service's answer)
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = SlowMistralService()
    section = _proofreading(
        qtbot, svc, {"correction_provider": "ollama", "correction_model": "qwen3.5:9b"}
    )

    section._provider.setCurrentText("mistral")

    assert section._busy.isHidden() is False
    assert "Asking Mistral which models are installed…" in section._status_lbl.text()
    assert section._guidance.isHidden() is True
    assert _items(section) == ["mistral-small-latest"]

    svc.release.set()
    qtbot.waitUntil(lambda: section._busy.isHidden(), timeout=5000)

    assert "✓ 2 models from Mistral" in section._status_lbl.text()


@pytest.mark.gui
def test_a_saved_model_the_service_no_longer_offers_is_called_out(qtbot, monkeypatch) -> None:
    """
    Given  a saved Mistral model that Mistral no longer offers
    When   the live list arrives
    Then   the saved name stays in the field (not silently swapped) and the status says why
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    section = _proofreading(
        qtbot, svc, {"correction_provider": "mistral", "correction_model": "mistral-old-model"}
    )
    _wait_for_fetch(qtbot, section, svc, calls=1)

    assert section._model.currentText() == "mistral-old-model"
    status = section._status_lbl.text()
    assert "Your saved model mistral-old-model is not offered by Mistral." in status
    assert "#e67e22" in status  # called out in orange, not as a plain green count


@pytest.mark.gui
def test_a_built_in_model_is_never_listed_twice(qtbot, monkeypatch) -> None:
    """
    Given  a provider that cannot be asked for its models and whose default is in its
           built-in list
    When   the section loads that default
    Then   the model combo lists each model exactly once
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    section = _proofreading(
        qtbot,
        NonListableService(),
        {"correction_provider": "anthropic", "correction_model": "claude-haiku-4-5"},
    )

    items = _items(section)
    assert len(items) == len(set(items)), items
    assert section._model.currentText() == "claude-haiku-4-5"


@pytest.mark.gui
def test_switching_provider_does_not_call_the_old_saved_model_out(qtbot, monkeypatch) -> None:
    """
    Given  a section on Mistral with a saved model the live list still offers, and Mistral (EU)
           whose live list no longer offers it
    When   the user switches to Mistral (EU) and its live list arrives
    Then   the name stays in the field, but no "saved model not offered" note appears: the
           saved choice belonged to Mistral, not to the service just switched to
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    svc.listing = ModelListing(["glm-live", "open-mistral-nemo"], "live")
    section = _proofreading(
        qtbot,
        svc,
        {"correction_provider": "mistral", "correction_model": "open-mistral-nemo"},
    )
    _wait_for_fetch(qtbot, section, svc, calls=1)
    assert "not offered" not in section._status_lbl.text()

    svc.listing = ModelListing(["glm-live"], "live")
    section._provider.setCurrentText("mistral_eu")
    _wait_for_fetch(qtbot, section, svc, calls=2)

    assert section._model.currentText() == "open-mistral-nemo"
    assert "not offered" not in section._status_lbl.text()


@pytest.mark.gui
def test_a_curated_list_shows_labels_groups_and_raw_ids(qtbot, monkeypatch) -> None:
    """
    Given  a Mistral answer curated into current, pinned and fine-tuned models, with labels
    When   the section shows it
    Then   the field holds the raw model IDs in that order, a separator stands between the
           groups, and each entry carries its label and the service's description as tooltip
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    section = _proofreading(qtbot, svc, {"correction_provider": "mistral"})
    _wait_for_fetch(qtbot, section, svc, calls=1)

    section._show_listing(curated_listing())

    combo = section._model
    items = [
        (combo.itemText(i), combo.itemData(i, ITEM_LABEL_ROLE) or "") for i in range(combo.count())
    ]
    assert items == [
        ("mistral-small-latest", "★ suggested"),
        ("mistral-large-latest", ""),
        ("", ""),  # separator between current and pinned
        ("mistral-small-2506", "pinned version"),
        ("", ""),  # separator between pinned and the fine-tuned model
        ("ft:mistral-small-latest:5f1c", "your model"),
    ]
    assert combo.itemData(0, Qt.ItemDataRole.ToolTipRole) == "Small and fast."
    assert "✓ 4 models from Mistral" in section._status_lbl.text()


@pytest.mark.gui
def test_a_saved_model_that_is_being_retired_is_warned_about_and_use_it_switches(
    qtbot, monkeypatch
) -> None:
    """
    Given  a saved Mistral model that Mistral still serves but will retire, and a suggestion
    When   the live list arrives
    Then   the model stays chosen, the status names the date and the suggested model in
           orange, and its "Use it" link switches the field to the suggestion
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    svc.listing = curated_listing()
    section = _proofreading(
        qtbot,
        svc,
        {"correction_provider": "mistral", "correction_model": "mistral-large-2407"},
    )
    _wait_for_fetch(qtbot, section, svc, calls=1)

    assert section._model.currentText() == "mistral-large-2407"  # never swapped silently
    status = section._status_lbl.text()
    assert "mistral-large-2407 will be retired on 2026-11-30" in status
    assert "Mistral suggests mistral-large-latest." in status
    assert "Use it" in status
    assert "#e67e22" in status

    section._status_lbl.linkActivated.emit("use:mistral-large-latest")

    assert section._model.currentText() == "mistral-large-latest"
    assert "retired on" not in section._status_lbl.text()
    assert "#27ae60" in section._status_lbl.text()


@pytest.mark.gui
def test_a_saved_alias_the_service_still_serves_is_not_called_out(qtbot, monkeypatch) -> None:
    """
    Given  a saved Mistral model that is only an alias of a listed model, which Mistral still
           answers to
    When   the curated live list arrives
    Then   the name stays in the field and no "not offered" note appears
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    curated = curated_listing()
    svc.listing = ModelListing(
        curated.models,
        "live",
        details=curated.details,
        also_served=("mistral-medium-2508",),
    )
    section = _proofreading(
        qtbot,
        svc,
        {"correction_provider": "mistral", "correction_model": "mistral-medium-2508"},
    )
    _wait_for_fetch(qtbot, section, svc, calls=1)

    assert section._model.currentText() == "mistral-medium-2508"
    assert "not offered" not in section._status_lbl.text()


@pytest.mark.gui
def test_a_saved_model_that_is_really_gone_is_still_called_out(qtbot, monkeypatch) -> None:
    """
    Given  a saved Mistral model that is neither listed, retiring nor an alias of a listed one
    When   the curated live list arrives
    Then   the status says the model is not offered by Mistral
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    svc.listing = curated_listing()
    section = _proofreading(
        qtbot,
        svc,
        {"correction_provider": "mistral", "correction_model": "mistral-vanished"},
    )
    _wait_for_fetch(qtbot, section, svc, calls=1)

    assert "not offered" in section._status_lbl.text()


@pytest.mark.gui
def test_a_curated_list_refreshes_the_location_hint_for_an_empty_field(qtbot, monkeypatch) -> None:
    """
    Given  a Mistral section whose model field is empty
    When   a curated list arrives and the field moves to its first entry
    Then   the location hint and the same-model tip are refreshed for that model
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    section = _proofreading(qtbot, svc, {"correction_provider": "mistral", "correction_model": ""})
    _wait_for_fetch(qtbot, section, svc, calls=1)
    section._model.setCurrentText("")
    refreshed: list[str] = []
    monkeypatch.setattr(
        section, "_update_where_hint", lambda: refreshed.append(section._model.currentText())
    )

    section._show_listing(curated_listing())

    assert refreshed
    assert refreshed[-1] == section._model.currentText() == "mistral-small-latest"


@pytest.mark.gui
def test_the_choice_is_announced_when_the_curated_field_moves(qtbot, monkeypatch) -> None:
    """
    Given  a Mistral section with an empty model field
    When   a curated list arrives and the field moves to its first entry
    Then   choice_changed announces the model the field now holds, so the other step's
           same-model tip is not left on a stale name
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    section = _proofreading(qtbot, svc, {"correction_provider": "mistral"})
    _wait_for_fetch(qtbot, section, svc, calls=1)
    section._model.setCurrentText("")
    choices: list[tuple[str, str]] = []
    section.choice_changed.connect(lambda provider, model: choices.append((provider, model)))

    section._show_listing(curated_listing())

    assert choices[-1] == ("mistral", "mistral-small-latest")


@pytest.mark.gui
def test_the_ollama_hint_comes_back_when_ollama_does_not_answer(qtbot) -> None:
    """
    Given  an Ollama section that hid its hint line while it waited for the model list
    When   the answer is an empty list (Ollama is not running)
    Then   the hint line is shown again, so its download button is still there
    """
    section = _proofreading(
        qtbot,
        CachingDummyAppService(),
        {"correction_provider": "ollama", "ollama_url": DEFAULTS["ollama_url"]},
    )
    section._set_busy(True, "Asking Ollama…")
    assert section._guidance.isHidden()

    section._show_listing(ModelListing([], "failed", "no connection to Ollama"))

    assert not section._guidance.isHidden()


@pytest.mark.gui
def test_a_missing_provider_package_offers_the_install_in_add_ons(qtbot, monkeypatch) -> None:
    """
    Given  Mistral is chosen while its package is not installed, and Add-ons can be reached
    When   the section is shown and the Install button is pressed
    Then   the hint says Mistral support is not installed yet (no "download the full release"),
           Add-ons is asked for with the Mistral package, and once the package is installed
           and the window reports it, the hint is gone
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = ProviderAppService()
    svc.missing = {"mistral": "langchain-mistralai"}
    opened: list[list[str]] = []
    section = _proofreading(
        qtbot,
        svc,
        {"correction_provider": "mistral"},
        open_downloads=lambda preselect, parent: opened.append(list(preselect)),
    )
    section.show()
    hint = section._package_hint
    assert "Mistral support is not installed yet." in hint.message()
    assert "full-featured release" not in hint.message()

    hint._button.click()
    assert opened == [["provider:mistral"]]

    svc.missing = {}  # the user installed it
    section.on_packages_changed()
    assert hint.isHidden()


@pytest.mark.gui
def test_no_install_hint_when_the_package_is_installed(qtbot, monkeypatch) -> None:
    """
    Given  a provider whose package is installed
    When   the section is shown
    Then   no install hint appears
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    section = _proofreading(qtbot, ProviderAppService(), {"correction_provider": "mistral"})
    section.show()

    assert section._package_hint.isHidden()


# ── model guidance in the section ──────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("memory_gb", "fragment", "warning"),
    [
        (32.0, "very slowly", False),  # fits with the computer's memory: informative, no alarm
        (None, "14 gb of graphics memory", True),  # memory unknown: the warning as before
    ],
)
def test_hint_for_a_model_too_big_for_the_card_depends_on_the_computers_memory(
    qtbot, memory_gb: float | None, fragment: str, warning: bool
) -> None:
    """
    Given  an 8 GB graphics profile and an installed gpt-oss:20b (about 14 GB)
    When   the model is selected in the proofreading section
    Then   with 32 GB of computer memory the hint explains that it works but very slowly (no
           warning, under 70 % of the model fits the card); without that figure it warns that
           the model is larger than the card
    """
    service = CatalogAppService(["gpt-oss:20b", "qwen3.5:9b"])
    service.system_memory_gb = memory_gb
    section = _proofreading(
        qtbot,
        service,
        {"correction_provider": "ollama", "correction_model": "qwen3.5:9b"},
        hardware_profile="8gb",
    )
    qtbot.waitUntil(lambda: len(section._guidance._models) == 2, timeout=5000)
    with qtbot.waitSignal(section._hw_selector.resolved_changed, timeout=5000):
        section._hw_selector.set_setting("8gb")  # resolve again, now with the memory known

    section._model.setCurrentText("gpt-oss:20b")
    hint = section._guidance._hint_lbl.text()

    assert fragment in hint.lower()
    assert hint.startswith("⚠") is warning


@pytest.mark.use_case("Model_Category_Guidance")
def test_reading_model_shows_a_warning_hint(qtbot) -> None:
    """
    Given  the proofreading section with a reading model installed
    When   the reading model is selected
    Then   the hint is a warning that it is a reading model and names a writing model;
           the item is flagged in the list as "Reading model only"
    """
    service = CatalogAppService(["qwen3-vl:8b", "qwen3.5:9b"])
    section = _proofreading(
        qtbot,
        service,
        {"correction_provider": "ollama", "correction_model": "qwen3.5:9b"},
        hardware_profile="8gb",
    )
    qtbot.waitUntil(lambda: len(section._guidance._models) == 2, timeout=5000)

    section._model.setCurrentText("qwen3-vl:8b")

    hint = section._guidance._hint_lbl.text()
    assert hint.startswith("⚠")
    assert "reading model" in hint
    assert DEFAULT_WRITING_MODEL in hint  # the BEST writing model on 8 GB
    assert "cannot read scans" not in hint
    index = section._model.findText("qwen3-vl:8b")
    assert "Reading model only" in section._model.itemData(index, Qt.ItemDataRole.ToolTipRole)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize("spec", _SPECS, ids=["proofreading", "review"])
@pytest.mark.parametrize(
    ("profile", "same_models", "expected_visible"),
    [
        ("8gb", False, True),
        ("8gb", True, False),
        ("16gb", False, False),
    ],
)
def test_same_model_tip_visibility(
    qtbot, spec, profile: str, same_models: bool, expected_visible: bool
) -> None:
    """
    Given  a proofreading or content review section on a cpu/8gb profile
    When   both steps use Ollama
    Then   the same-model tip is shown only when the two models differ and hidden on larger
           profiles
    """
    own_model = "qwen3.5:9b"
    other_model = own_model if same_models else "mistral:latest"
    section = _section(
        qtbot,
        spec,
        DummyAppService(),
        {
            spec.provider_key: "ollama",
            spec.model_key: own_model,
            spec.other_provider_key: "ollama",
            spec.other_model_key: other_model,
        },
        hardware_profile=profile,
    )
    with qtbot.waitSignal(section._hw_selector.resolved_changed, timeout=5000):
        pass
    # The section is not shown in tests, so query the explicit hide flag.
    assert section._same_model_tip_lbl.isHidden() is not expected_visible


@pytest.mark.use_case("Model_Category_Guidance")
def test_closing_parks_a_running_fetch_thread_without_waiting(qtbot) -> None:
    """
    Given  a proofreading section whose model list is still being fetched
    When   the section shuts down
    Then   it does not block on the thread, the thread is parked (so it is never destroyed
           while running) and its late answer no longer reaches the section
    """
    release = threading.Event()

    class _SlowService(DummyAppService):
        def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
            _ = base_url
            release.wait(10)
            return [f"{provider}-model"]

    section = _proofreading(
        qtbot,
        _SlowService(),
        {"correction_provider": "ollama", "ollama_url": DEFAULTS["ollama_url"]},
        hardware_profile="8gb",
    )
    thread = section._fetch_thread
    assert thread is not None and thread.isRunning()

    section.shutdown()
    assert thread in _model_fetch._RETIRED_THREADS

    release.set()
    qtbot.waitUntil(thread.isFinished, timeout=5000)
    qtbot.wait(50)  # let a (disconnected) late result be delivered, if it were
    assert section._guidance._models == []
    _model_fetch._purge_retired_threads()
    assert thread not in _model_fetch._RETIRED_THREADS


@pytest.mark.use_case("Model_Category_Guidance")
def test_typed_model_without_tag_counts_as_installed_latest(qtbot) -> None:
    """
    Given  a proofreading section whose server lists "mistral:latest"
    When   the user types "mistral" (the tag Ollama would resolve to :latest)
    Then   the hint does not claim the model is not downloaded
    """
    section = _proofreading(
        qtbot,
        CatalogAppService(["mistral:latest"]),
        {"correction_provider": "ollama", "ollama_url": DEFAULTS["ollama_url"]},
        hardware_profile="8gb",
        open_downloads=lambda tags, parent: None,
    )
    qtbot.waitUntil(lambda: section._guidance._models == ["mistral:latest"], timeout=5000)

    section._model.setCurrentText("mistral")
    assert "not downloaded" not in section._guidance._hint_lbl.text()
    section._model.setCurrentText("mistral-nemo:12b")
    assert "not downloaded" in section._guidance._hint_lbl.text()


@pytest.mark.use_case("Hardware_Profile_Override")
def test_download_best_choice_asks_for_add_ons_and_hints_follow_the_profile(qtbot) -> None:
    """
    Given  a proofreading section whose best model is not installed
    When   "Download best choice…" is pressed, and afterwards the window passes on that the
           teacher changed "Where the AI runs" to 16 GB
    Then   Add-ons was asked for the best model on top of the section (no modal round trip),
           and the section's hints now follow the 16 GB profile
    """
    received: list[tuple[list[str], object]] = []

    def open_downloads(tags: list[str], parent) -> None:
        received.append((tags, parent))

    section = _proofreading(
        qtbot,
        DummyAppService(),
        {"correction_provider": "ollama", "ollama_url": DEFAULTS["ollama_url"]},
        hardware_profile="8gb",
        open_downloads=open_downloads,
    )
    qtbot.waitUntil(lambda: section._guidance._models == ["ollama-model"], timeout=5000)
    assert section._fetch_thread is not None
    section._fetch_thread.wait(5000)
    assert section._guidance._best_btn.text() == "Download best choice…"

    section._guidance._best_btn.click()

    assert received == [([DEFAULT_WRITING_MODEL], section.window())]  # best writing model, 8 GB
    section.set_hardware_setting("16gb")
    assert section._hw_selector.setting() == "16gb"
    qtbot.waitUntil(lambda: section._guidance.active_profile() == "16gb", timeout=5000)


@pytest.mark.gui
def test_a_new_server_address_asks_the_new_server_for_models(qtbot) -> None:
    """
    Given  an Ollama section that listed the models of one server
    When   the window passes on another server address
    Then   the section asks the new server for its models; the same address does nothing
    """
    asked: list[str] = []

    class _Service(DummyAppService):
        def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
            asked.append(base_url)
            return [f"{provider}-model"]

    section = _proofreading(
        qtbot, _Service(), {"correction_provider": "ollama", "ollama_url": DEFAULTS["ollama_url"]}
    )
    qtbot.waitUntil(lambda: len(asked) == 1, timeout=5000)

    section.set_ollama_url(DEFAULTS["ollama_url"])
    qtbot.wait(50)
    assert len(asked) == 1

    section.set_ollama_url("http://192.168.1.50:11434")
    qtbot.waitUntil(lambda: asked[-1:] == ["http://192.168.1.50:11434"], timeout=5000)


def test_the_two_steps_have_their_own_keys() -> None:
    """
    Given  the specs of the two AI steps
    When   their keys are compared
    Then   each step's "other" keys are the other step's own keys, and no key is shared
    """
    assert PROOFREADING.other_provider_key == CONTENT_REVIEW.provider_key
    assert PROOFREADING.other_model_key == CONTENT_REVIEW.model_key
    assert CONTENT_REVIEW.other_provider_key == PROOFREADING.provider_key
    assert CONTENT_REVIEW.other_model_key == PROOFREADING.model_key
    own = {s.provider_key for s in _SPECS} | {s.model_key for s in _SPECS}
    assert len(own) == 4


@pytest.mark.gui
def test_keys_the_keychain_refused_are_reported_by_apply(qtbot, monkeypatch) -> None:
    """
    Given  a working-looking keychain that refuses writes, and a key typed for OpenAI
    When   the credentials group is applied
    Then   it reports that key as kept in the settings file (the note promised the keychain)
    """
    from teachers_teammate.gui import _credential_store  # noqa: PLC0415

    monkeypatch.setattr(_credential_store, "_keyring", lambda: FakeKeyring(refuses_writes=True))
    group = _credentials(qtbot, monkeypatch)
    group._add_combo.setCurrentIndex(group._add_combo.findData("OPENAI_API_KEY"))
    group._add_btn.click()
    group.field("OPENAI_API_KEY").setText("sk-typed")

    assert group.apply() == ["OPENAI_API_KEY"]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.mark.gui
def test_keys_in_a_working_keychain_and_plain_file_setups_are_not_reported(
    qtbot, monkeypatch
) -> None:
    """
    Given  a working keychain, and separately a computer with no keychain at all
    When   a key is typed and the group applied
    Then   nothing is reported in either case (the note already told the truth)
    """
    from teachers_teammate.gui import _credential_store  # noqa: PLC0415

    monkeypatch.setattr(_credential_store, "_keyring", lambda: FakeKeyring())
    safe = _credentials(qtbot, monkeypatch)
    safe._add_combo.setCurrentIndex(safe._add_combo.findData("OPENAI_API_KEY"))
    safe._add_btn.click()
    safe.field("OPENAI_API_KEY").setText("sk-1")
    assert safe.apply() == []

    monkeypatch.setattr(_credential_store, "_keyring", lambda: None)
    plain = _credentials(qtbot, monkeypatch)
    plain._add_combo.setCurrentIndex(plain._add_combo.findData("OPENAI_API_KEY"))
    plain._add_btn.click()
    plain.field("OPENAI_API_KEY").setText("sk-2")
    assert plain.apply() == []
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.mark.gui
def test_the_setup_page_tells_the_user_when_a_key_fell_back_to_the_file(qtbot, monkeypatch) -> None:
    """
    Given  the Where the AI runs page with a key typed while the keychain refuses writes
    When   the window applies the page (OK)
    Then   a warning names the service and says the key was kept in the settings file
    """
    from teachers_teammate.gui import _credential_store  # noqa: PLC0415
    from teachers_teammate.gui.settings import _ai_setup_page  # noqa: PLC0415

    monkeypatch.setattr(_credential_store, "_keyring", lambda: FakeKeyring(refuses_writes=True))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        _ai_setup_page.QMessageBox,
        "warning",
        lambda _parent, title, text: warnings.append((title, text)),
    )
    page = AiSetupPage(settings_context(DummyAppService()))
    qtbot.addWidget(page)
    group = page._credentials
    group._add_combo.setCurrentIndex(group._add_combo.findData("OPENAI_API_KEY"))
    group._add_btn.click()
    group.field("OPENAI_API_KEY").setText("sk-typed")

    page.apply()

    assert len(warnings) == 1
    title, text = warnings[0]
    assert "keychain" in title.lower()
    assert "settings file" in text
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
