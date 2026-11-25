"""GUI tests for Settings → Add-ons (AI models for this computer: groups & suitability)."""

from __future__ import annotations

from collections.abc import Iterator
import threading
from typing import Any, ClassVar
from unittest.mock import MagicMock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea
import pytest

from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    DEFAULT_WRITING_MODEL,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    HardwareProfileSetting,
    ModelUses,
    OLLAMA_CATALOG,
)
from teachers_teammate.domain.providers import AddonPackage, ProviderInfo
from teachers_teammate.gui import _dialogs
from teachers_teammate.texts import addons as txt
from teachers_teammate.gui._constants import _SPACY_MODEL_CHOICES
from teachers_teammate.domain.install_item import (
    PAGE_MODELS_ITEM,
    READER_ITEM,
    SPACY_ITEM,
    InstallItem,
    InstallKind,
)
from teachers_teammate.gui.settings.addons import DetectedAddons, _install_status
from teachers_teammate.gui.settings._addons_page import AddonsPage
from teachers_teammate.domain.name_models import FALLBACK_SPACY_MODEL, SPACY_MODELS
from teachers_teammate.gui.settings._page import (
    FOCUS_ADDON_UPDATE,
    FOCUS_LOCAL_MODELS,
    FOCUS_NAME_DETECTION,
    FOCUS_READING_MODELS,
    SettingsPage,
)
from teachers_teammate.exceptions import AddonInstallError, AddonUpdateError, InstallFailure
from teachers_teammate.texts._types import Question

from ..fakes.fake_ollama import FakeOllama
from ._gui_harness import settings_context
from ._service_stubs import FinishedThreadStub, RecommendationServiceStub

#: The items these tests hand to the page, and the German name-detection model.
MISTRAL = InstallItem(InstallKind.PROVIDER, "mistral")
MISTRAL_EU = InstallItem(InstallKind.PROVIDER, "mistral_eu")
OPENAI = InstallItem(InstallKind.PROVIDER, "openai")
GERMAN_NAMES = InstallItem(InstallKind.SPACY, "de_core_news_sm")
OPENAI_PACKAGE = InstallItem(InstallKind.PACKAGE, "langchain-openai")

_USER_ROLE = 0x0100  # Qt.ItemDataRole.UserRole


class _StubService(RecommendationServiceStub):
    """Stub application service for Add-ons page tests."""

    def __init__(self, installed: set[str] | None = None) -> None:
        super().__init__()
        self.installed = installed or set()
        # Tests that need to control *when* hardware detection finishes set this.
        self.detection_gate: threading.Event | None = None
        # Likewise for the installed-model check.
        self.status_gate: threading.Event | None = None
        self.status_calls = 0
        # When set, the installed-model check fails with this error.
        self.status_error: Exception | None = None
        # Uses the (fake) server reports for models that are not in the catalog.
        self.custom_uses: dict[str, ModelUses] = {}

    def classify_ollama_models(self, tags, ollama_url, *, cached_only=False):
        result = super().classify_ollama_models(tags, ollama_url, cached_only=cached_only)
        for tag in tags:
            if result.get(tag) is None:
                result[tag] = self.custom_uses.get(tag)
        return result

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        if self.detection_gate is not None:
            assert self.detection_gate.wait(timeout=5), "test never released the detection"
        return super().detect_hardware(ollama_url, refresh=refresh)

    # ── installation probing ──────────────────────────────────────────────
    def get_installation_status(self, *, ollama_models=None, **_kwargs) -> dict[InstallItem, bool]:
        self.status_calls += 1
        if self.status_error is not None:
            raise self.status_error
        if self.status_gate is not None and self.status_calls == 1:
            assert self.status_gate.wait(timeout=5), "test never released the check"
        return {
            InstallItem(InstallKind.OLLAMA, m): (m in self.installed) for m in (ollama_models or [])
        }

    def is_module_importable(self, module: str) -> bool:
        _ = module
        return False

    def pending_addon_packages(self) -> list[str]:
        return []

    def list_ollama_model_sizes(self, url: str) -> dict[str, int]:
        _ = url
        return {}

    # ── unused in these tests ─────────────────────────────────────────────
    def pull_ollama_model(
        self, url: str, model_name: str, progress_cb=None, abort_event=None
    ) -> None:
        _ = url, model_name, progress_cb, abort_event


def _make_dialog(
    qtbot,
    installed: set[str] | None = None,
    hardware_profile: str = "8gb",
    preselect=(),
    service: Any = None,
    wait_for_profile: bool = True,
    pull_manager: Any = None,
    ollama_url: str = "http://127.0.0.1:11434",
) -> AddonsPage:
    """Build the page; *preselect* arrives as a deep link (``focus``) would bring it."""
    dlg = AddonsPage(
        settings_context(
            service or _StubService(installed),
            settings={
                "ollama_url": ollama_url,
                "hardware_profile": hardware_profile,
            },
            pull_manager=pull_manager,
        )
    )
    qtbot.addWidget(dlg)
    if preselect:
        dlg.focus(FOCUS_LOCAL_MODELS, preselect)
    if wait_for_profile:
        with qtbot.waitSignal(dlg.ollama_models._hardware_selector.resolved_changed, timeout=5000):
            pass
    return dlg


def _detection_results(dlg: AddonsPage, installed: set[str]) -> DetectedAddons:
    """Build the answer ``detect_installed`` gives for the models of the tree."""
    tags = [tag for tag, _item in dlg.ollama_models._iter_model_items()]
    return DetectedAddons(
        installed={InstallItem(InstallKind.OLLAMA, t): (t in installed) for t in tags}
    )


def _group_tags(dlg: AddonsPage, key: str) -> list[str]:
    group = dlg.ollama_models._group_items[key]
    return [
        str(group.child(i).data(0, _USER_ROLE) or group.child(i).text(0))
        for i in range(group.childCount())
    ]


# ── grouping ────────────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Category_Guidance")
def test_groups_contain_catalog_models_and_other_is_hidden(qtbot) -> None:
    """
    Given  the Add-ons page
    When   the local-models tab is built
    Then   reading/writing groups hold exactly their catalog models and
           "Other models" is hidden while empty
    """
    dlg = _make_dialog(qtbot)
    reading = {spec.tag for spec in OLLAMA_CATALOG if spec.category == "ocr"}
    writing = {spec.tag for spec in OLLAMA_CATALOG if spec.category == "general"}
    assert set(_group_tags(dlg, "ocr")) == reading
    assert set(_group_tags(dlg, "general")) == writing
    assert dlg.ollama_models._group_items["other"].isHidden() is True
    assert dlg.ollama_models._group_items["other"].childCount() == 0


# ── licence column ──────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Category_Guidance")
def test_only_a_model_whose_licence_restricts_use_says_so_in_its_row(qtbot) -> None:
    """
    Given  the Add-ons page with the catalog
    When   the licence column is inspected
    Then   the default reading model names its terms with the sentence as tooltip,
           and a model under Apache-2.0 leaves the column empty
    """
    dlg = _make_dialog(qtbot)
    reading = dlg.ollama_models._model_items[DEFAULT_OCR_MODEL]
    assert reading.text(4) == "Modified OpenRAIL-M"
    assert "check the licence" in reading.toolTip(4)
    assert "https://" in reading.toolTip(4)
    assert dlg.ollama_models._model_items[DEFAULT_WRITING_MODEL].text(4) == ""
    # Every model's terms are one hover away, even when the row says nothing.
    assert "Apache-2.0" in dlg.ollama_models._model_items[DEFAULT_WRITING_MODEL].toolTip(0)


# ── suitability column ──────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_suitability_column_and_sort_order_follow_profile(qtbot) -> None:
    """
    Given  the dialog resolved to the 8gb profile
    When   the suitability column is inspected
    Then   the BEST models come first with the "★ Best" label
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    reading = dlg.ollama_models._group_items["ocr"]
    assert reading.child(0).text(0) == DEFAULT_OCR_MODEL
    assert reading.child(0).text(1) == "★ Best"
    writing = dlg.ollama_models._group_items["general"]
    assert writing.child(0).text(0) == DEFAULT_WRITING_MODEL
    gpt_oss = dlg.ollama_models._model_items["gpt-oss:20b"]
    assert gpt_oss.text(1) == "Won't fit"


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_switching_profile_updates_suitability(qtbot) -> None:
    """
    Given  the page resolved to 8gb
    When   the window passes on that "This computer" was switched to "16 GB or more"
    Then   suitability labels and sorting update for the new profile
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    with qtbot.waitSignal(dlg.ollama_models._hardware_selector.resolved_changed, timeout=5000):
        dlg.set_hardware_setting(HardwareProfileSetting.CARD_16GB)
    writing = dlg.ollama_models._group_items["general"]
    # The first entry of every profile is a writing model that supports explanations.
    assert writing.child(0).text(0) == "gemma4:12b"
    assert writing.child(0).text(1) == "★ Best"


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_model_that_was_not_measured_shows_the_speed_it_is_expected_to_reach(qtbot) -> None:
    """
    Given  a computer whose measured models give a speed factor of 0.5
    When   the suitability column is inspected
    Then   a catalogued model that was never measured here carries the expected band,
           and a model that does not fit the card carries none
    """
    service = _StubService()
    service.machine_factor = 0.5
    dlg = _make_dialog(qtbot, service=service)
    dlg._on_detection_done(_detection_results(dlg, set()))
    row = dlg.ollama_models._model_items["gemma4:e4b"]
    assert row.text(1) == "★ Best · ok (expected)"
    assert "Expected speed on this computer: ok." in row.toolTip(1)
    # A model that does not fit the card has no page time worth naming.
    assert "(expected)" not in dlg.ollama_models._model_items["gpt-oss:20b"].text(1)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_rows_are_grey_but_checkable(qtbot) -> None:
    """
    Given  a TOO_LARGE model row on the 8gb profile
    When   the row is inspected and ticked
    Then   it is drawn grey but stays checkable (AC4)
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    gpt_oss = dlg.ollama_models._model_items["gpt-oss:20b"]
    assert gpt_oss.foreground(0).color().name().lower() == "#888888"
    assert bool(gpt_oss.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    gpt_oss.setCheckState(0, Qt.CheckState.Checked)
    assert gpt_oss.checkState(0) is Qt.CheckState.Checked


# ── select best & pre-ticking ───────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_select_best_ticks_undownloaded_best_models(qtbot) -> None:
    """
    Given  the dialog with nothing installed and deepseek-ocr already ticked
    When   "Select the best models for my computer" is clicked
    Then   the BEST reading and writing models are ticked, other ticks stay,
           and downloaded models are skipped
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    deepseek = dlg.ollama_models._model_items["deepseek-ocr:latest"]
    deepseek.setCheckState(0, Qt.CheckState.Checked)
    dlg.ollama_models._on_select_best_models()
    assert dlg.ollama_models._model_items[DEFAULT_OCR_MODEL].checkState(0) is Qt.CheckState.Checked
    assert (
        dlg.ollama_models._model_items[DEFAULT_WRITING_MODEL].checkState(0) is Qt.CheckState.Checked
    )
    assert deepseek.checkState(0) is Qt.CheckState.Checked


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_select_best_reports_when_already_downloaded(qtbot) -> None:
    """
    Given  the dialog with both BEST models already installed
    When   "Select the best models for my computer" is clicked
    Then   the status line says the best models are already downloaded
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, {DEFAULT_OCR_MODEL, DEFAULT_WRITING_MODEL}))
    dlg.ollama_models._on_select_best_models()
    assert dlg.ollama_models._ollama_hint_lbl.text() == (
        "The best models for your computer are already downloaded."
    )


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_pre_ticking_when_no_model_of_category_downloaded(qtbot) -> None:
    """
    Given  no catalogued reading model installed
    When   install detection finishes
    Then   the BEST reading model is pre-ticked (AC14)
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    assert dlg.ollama_models._model_items[DEFAULT_OCR_MODEL].checkState(0) is Qt.CheckState.Checked
    assert (
        dlg.ollama_models._model_items[DEFAULT_WRITING_MODEL].checkState(0) is Qt.CheckState.Checked
    )


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_no_pre_ticking_when_category_already_downloaded(qtbot) -> None:
    """
    Given  glm-ocr (a reading model) already installed
    When   install detection finishes
    Then   the BEST reading model is not pre-ticked
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, {"glm-ocr:q8_0", "qwen3.5:9b"}))
    assert (
        dlg.ollama_models._model_items[DEFAULT_OCR_MODEL].checkState(0) is Qt.CheckState.Unchecked
    )


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_preselect_ticks_given_tags(qtbot) -> None:
    """
    Given  the dialog opened with an Ollama item for the tag glm-ocr:q8_0
    When   install detection finishes
    Then   the given tag is ticked
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot, preselect=[InstallItem(InstallKind.OLLAMA, "glm-ocr:q8_0")])
    dlg._on_detection_done(_detection_results(dlg, set()))
    assert dlg.ollama_models._model_items["glm-ocr:q8_0"].checkState(0) is Qt.CheckState.Checked


class _ProviderPackagesService(_StubService):
    """Stub that knows two LLM provider packages and which of them are installed."""

    def __init__(self, installed_providers: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self.installed_providers = installed_providers

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        return {
            "mistral": ("langchain_mistralai", "langchain-mistralai"),
            "openai": ("langchain_openai", "langchain-openai"),
        }

    def list_providers(self) -> list[str]:
        return ["mistral", "mistral_eu", "openai"]

    def get_provider_info(self, provider: str) -> ProviderInfo:
        package = (
            AddonPackage(module="langchain_mistralai", pip="langchain-mistralai")
            if provider in ("mistral", "mistral_eu")  # one package serves both
            else AddonPackage(module="langchain_openai", pip="langchain-openai")
        )
        return ProviderInfo(name=provider, display_name=provider.title(), package=package)

    def get_installation_status(self, *, ollama_models=None, **kwargs) -> dict[InstallItem, bool]:
        status = super().get_installation_status(ollama_models=ollama_models, **kwargs)
        for name in self.list_provider_packages():
            status[InstallItem(InstallKind.PROVIDER, name)] = name in self.installed_providers
        return status


def test_provider_preselect_opens_the_providers_tab_and_ticks_the_package(qtbot) -> None:
    """
    Given  the page focused with Mistral among the preselected items (the Install button of an AI
           service hint), and Mistral not installed
    When   install detection finishes
    Then   the Online AI services tab is shown, the Mistral box is ticked and the pip package
           would be installed, while OpenAI stays unticked
    """
    from PySide6.QtWidgets import QTabWidget  # noqa: PLC0415

    dlg = _make_dialog(qtbot, service=_ProviderPackagesService(), preselect=[MISTRAL])
    dlg._on_detection_done(_detection_results(dlg, set()))

    assert dlg.findChild(QTabWidget).currentIndex() == AddonsPage.TAB_ONLINE_SERVICES
    assert dlg.providers.checkboxes[MISTRAL].isChecked() is True
    assert dlg.providers.checkboxes[OPENAI].isChecked() is False
    assert dlg._collect_pip_packages() == ["langchain-mistralai"]


def test_provider_preselect_for_a_provider_sharing_a_package_ticks_the_shared_box(qtbot) -> None:
    """
    Given  the dialog opened with Mistral (EU) preselected, while the list offers the
           package shared by Mistral and Mistral (EU) once, under "mistral"
    When   install detection finishes
    Then   the shared Mistral box is ticked, so the Install button of the EU service works
    """
    dlg = _make_dialog(qtbot, service=_ProviderPackagesService(), preselect=[MISTRAL_EU])
    dlg._on_detection_done(_detection_results(dlg, set()))

    assert dlg.providers.checkboxes[MISTRAL].isChecked() is True
    assert dlg._collect_pip_packages() == ["langchain-mistralai"]


def test_provider_preselect_leaves_an_installed_package_alone(qtbot) -> None:
    """
    Given  a preselected provider whose package is already installed
    When   install detection finishes
    Then   its box shows "Installed" and is disabled, so nothing is installed again
    """
    service = _ProviderPackagesService(installed_providers=frozenset({"mistral"}))
    dlg = _make_dialog(qtbot, service=service, preselect=[MISTRAL])
    detected = _detection_results(dlg, set())
    detected.installed.update({MISTRAL: True, OPENAI: False})

    dlg._on_detection_done(detected)

    box = dlg.providers.checkboxes[MISTRAL]
    assert "Installed" in box.text()
    assert not box.isEnabled()
    assert dlg._collect_pip_packages() == []


def test_provider_preselect_is_not_mistaken_for_an_ollama_model(qtbot) -> None:
    """
    Given  a provider key in preselect
    When   the dialog opens
    Then   no Ollama model row is added for it
    """
    dlg = _make_dialog(qtbot, service=_ProviderPackagesService(), preselect=[MISTRAL])
    dlg._on_detection_done(_detection_results(dlg, set()))

    assert MISTRAL.name not in dlg.ollama_models._model_items
    assert dlg.ollama_models._group_items["other"].childCount() == 0


def test_the_name_detection_models_are_the_ones_the_app_offers() -> None:
    """
    Given  the model table and the label list the Add-ons page shows
    When   both are read
    Then   the labels are exactly the table's models plus the multilingual fallback, in order
    """
    assert [model for _label, model in _SPACY_MODEL_CHOICES] == [
        *SPACY_MODELS.values(),
        FALLBACK_SPACY_MODEL,
    ]


def test_spacy_preselect_ticks_the_model_and_spacy_itself(qtbot) -> None:
    """
    Given  the page focused on name detection with spaCy and the German model
           preselected, while spaCy is not installed
    When   install detection finishes
    Then   the name detection tab is shown, the model's box and the spaCy box are ticked, and
           the entries are no Ollama models
    """
    from PySide6.QtWidgets import QTabWidget  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg.focus(FOCUS_NAME_DETECTION, [SPACY_ITEM, GERMAN_NAMES])
    dlg._on_detection_done(_detection_results(dlg, set()))

    assert dlg.findChild(QTabWidget).currentIndex() == AddonsPage.TAB_NAME_DETECTION
    assert dlg.name_detection.checkboxes[GERMAN_NAMES].isChecked() is True
    assert dlg.name_detection.checkboxes[SPACY_ITEM].isChecked() is True
    assert GERMAN_NAMES.name not in dlg.ollama_models._model_items
    assert SPACY_ITEM not in dlg.ollama_models._model_items


def test_a_model_alone_does_not_tick_spacy(qtbot) -> None:
    """
    Given  the page focused on name detection with only the model as preselect (spaCy is
           installed, the model is not)
    When   install detection finishes
    Then   only the model's box is ticked: a tick always means "will be installed"
    """
    dlg = _make_dialog(qtbot)
    dlg.focus(FOCUS_NAME_DETECTION, [GERMAN_NAMES])
    dlg._on_detection_done(_detection_results(dlg, set()))

    assert dlg.name_detection.checkboxes[GERMAN_NAMES].isChecked() is True
    assert dlg.name_detection.checkboxes[SPACY_ITEM].isChecked() is False


def test_an_installed_row_is_disabled_and_unticked(qtbot) -> None:
    """
    Given  a page where spaCy and a model are already installed
    When   install detection finishes
    Then   both boxes are disabled and unticked, and the text still says they are installed
    """
    dlg = _make_dialog(qtbot)

    detected = _detection_results(dlg, set())
    detected.installed.update({SPACY_ITEM: True, GERMAN_NAMES: True})
    dlg._on_detection_done(detected)

    for item in (SPACY_ITEM, GERMAN_NAMES):
        box = dlg.name_detection.checkboxes[item]
        assert not box.isEnabled()
        assert box.isChecked() is False
        assert "✓" in box.text()


def test_name_detection_fix_button_ticks_the_boxes_through_the_settings_window(qtbot) -> None:
    """
    Given  the fix button "Download…" of a missing name detection model, which opens the
           Settings window on Add-ons with the model as preselect (the real path: the page
           is built by the window and detects what is installed on a background thread)
    When   the detection has finished
    Then   the name detection tab is shown and both the model's box and spaCy's box are ticked
    """
    from PySide6.QtWidgets import QTabWidget  # noqa: PLC0415

    from teachers_teammate.gui.settings import SettingsWindow  # noqa: PLC0415

    window = SettingsWindow(
        settings_context(
            _StubService(set()),
            settings={
                "ollama_url": "http://127.0.0.1:11434",
                "hardware_profile": "8gb",
            },
        ),
        page=SettingsPage.ADDONS,
        focus=FOCUS_NAME_DETECTION,
        preselect=[SPACY_ITEM, GERMAN_NAMES],
    )
    qtbot.addWidget(window)
    page = window.page(SettingsPage.ADDONS)

    qtbot.waitUntil(lambda: page._installed_ready, timeout=5000)

    assert page.findChild(QTabWidget).currentIndex() == AddonsPage.TAB_NAME_DETECTION
    assert page.name_detection.checkboxes[GERMAN_NAMES].isChecked() is True
    assert page.name_detection.checkboxes[SPACY_ITEM].isChecked() is True


def test_name_detection_boxes_are_ticked_before_the_detection_has_answered(qtbot) -> None:
    """
    Given  a page whose detection is still running (it asks the Ollama server too, which can
           take long when Ollama is not there)
    When   it is focused on name detection with spaCy and the German model preselected
    Then   the model's box and spaCy's box are ticked at once, and still are after the
           detection has answered
    """
    dlg = _make_dialog(qtbot)
    assert dlg._installed_ready is False

    dlg.focus(FOCUS_NAME_DETECTION, [SPACY_ITEM, GERMAN_NAMES])

    assert dlg.name_detection.checkboxes[GERMAN_NAMES].isChecked() is True
    assert dlg.name_detection.checkboxes[SPACY_ITEM].isChecked() is True

    dlg._on_detection_done(_detection_results(dlg, set()))

    assert dlg.name_detection.checkboxes[GERMAN_NAMES].isChecked() is True
    assert dlg.name_detection.checkboxes[SPACY_ITEM].isChecked() is True


def test_reader_preselect_opens_the_reading_models_tab_and_ticks_the_reader(qtbot) -> None:
    """
    Given  the page focused on the reading models with the reader item preselected (the fix
           button of a reader that is not downloaded)
    When   install detection finishes
    Then   the Reading models tab is shown, the reader's box is ticked, and the entry is
           no Ollama model
    """
    from PySide6.QtWidgets import QTabWidget  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg.focus(FOCUS_READING_MODELS, [READER_ITEM])
    dlg._on_detection_done(_detection_results(dlg, set()))

    assert dlg.findChild(QTabWidget).currentIndex() == AddonsPage.TAB_READING_MODELS
    assert dlg.reading_models.checkboxes[READER_ITEM].isChecked() is True
    assert READER_ITEM not in dlg.ollama_models._model_items
    assert dlg._collect_pip_packages() == []


def test_a_downloaded_reader_shows_downloaded_and_is_not_ticked_again(qtbot) -> None:
    """
    Given  the built-in reader already downloaded
    When   install detection finishes
    Then   its box says "✓ Downloaded" and is disabled, so nothing is fetched again
    """
    dlg = _make_dialog(qtbot)

    detected = _detection_results(dlg, set())
    detected.installed[READER_ITEM] = True
    dlg._on_detection_done(detected)

    box = dlg.reading_models.checkboxes[READER_ITEM]
    assert "✓ Downloaded" in box.text()
    assert "31 MB" in box.text()
    assert not box.isEnabled()


def test_the_reader_box_starts_a_round_with_the_reader_only(qtbot) -> None:
    """
    Given  the reader's box ticked and nothing else
    When   _on_download_install runs with the install round patched
    Then   the round is started with reader=True and no packages, models or spaCy models
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    for (
        _tag,
        row,
    ) in dlg.ollama_models._iter_model_items():  # undo the pre-ticking of the best models
        row.setCheckState(0, Qt.CheckState.Unchecked)
    dlg.reading_models.checkboxes[READER_ITEM].setChecked(True)
    started: list[dict] = []

    def start(**kwargs) -> bool:
        started.append(kwargs)
        return True

    dlg._round.start = start  # ty: ignore[invalid-assignment]
    dlg._on_download_install()

    assert len(started) == 1
    assert started[0]["reader"] is True
    assert not started[0]["packages"] and not started[0]["ollama_models"]
    assert not started[0]["spacy_models"]


def test_the_page_models_have_their_own_box_that_shows_downloaded_when_present(qtbot) -> None:
    """
    Given  the Reading models tab
    When   install detection reports the page models as downloaded
    Then   their box (next to the reader's) says "✓ Downloaded" and is disabled, and the
           reader's box is not touched
    """
    dlg = _make_dialog(qtbot)

    detected = _detection_results(dlg, set())
    detected.installed[PAGE_MODELS_ITEM] = True
    dlg._on_detection_done(detected)

    box = dlg.reading_models.checkboxes[PAGE_MODELS_ITEM]
    assert "Page models" in box.text()
    assert "39 MB" in box.text()
    assert "✓ Downloaded" in box.text()
    assert not box.isEnabled()
    assert dlg.reading_models.checkboxes[READER_ITEM].isEnabled()


def test_the_page_models_box_starts_a_round_with_the_page_models_only(qtbot) -> None:
    """
    Given  the page models' box ticked and nothing else
    When   _on_download_install runs with the install round patched
    Then   the round is started with page_models=True, reader=False and nothing else
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    for _tag, row in dlg.ollama_models._iter_model_items():
        row.setCheckState(0, Qt.CheckState.Unchecked)
    dlg.reading_models.checkboxes[PAGE_MODELS_ITEM].setChecked(True)
    started: list[dict] = []

    def start(**kwargs) -> bool:
        started.append(kwargs)
        return True

    dlg._round.start = start  # ty: ignore[invalid-assignment]
    dlg._on_download_install()

    assert len(started) == 1
    assert started[0]["page_models"] is True
    assert started[0]["reader"] is False
    assert not started[0]["packages"] and not started[0]["ollama_models"]


def test_a_page_models_preselect_opens_the_reading_models_tab_and_ticks_the_box(qtbot) -> None:
    """
    Given  the Add-ons page asked to focus with the PAGE_MODELS_ITEM preselect entry
    When   install detection has finished
    Then   the Reading models tab is shown and the page models' box is ticked
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))

    dlg.focus("", [PAGE_MODELS_ITEM])

    assert dlg._tabs.currentIndex() == dlg.TAB_READING_MODELS
    assert dlg.reading_models.checkboxes[PAGE_MODELS_ITEM].isChecked()


def test_the_tab_names_where_the_models_are_saved(qtbot) -> None:
    """
    Given  the Reading models tab
    When   its texts are read
    Then   it names the folder the model files are saved in
    """
    from PySide6.QtWidgets import QLabel  # noqa: PLC0415

    dlg = _make_dialog(qtbot)

    texts = [label.text() for label in dlg.findChildren(QLabel)]
    assert any(
        text.startswith("Saved in: ") and "teachers-teammate-models" in text for text in texts
    )


def test_spacy_preselect_leaves_an_installed_spacy_alone(qtbot) -> None:
    """
    Given  spaCy already installed but not the preselected model
    When   install detection finishes
    Then   the model is ticked and the installed spaCy box stays as it is (disabled, installed)
    """
    dlg = _make_dialog(qtbot)
    dlg.focus(FOCUS_NAME_DETECTION, [GERMAN_NAMES])

    detected = _detection_results(dlg, set())
    detected.installed[SPACY_ITEM] = True
    dlg._on_detection_done(detected)

    assert dlg.name_detection.checkboxes[GERMAN_NAMES].isChecked() is True
    assert not dlg.name_detection.checkboxes[SPACY_ITEM].isEnabled()
    assert "Installed" in dlg.name_detection.checkboxes[SPACY_ITEM].text()


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_slow_models_are_listed_as_slower_not_greyed_out_and_need_no_confirmation(
    qtbot, monkeypatch
) -> None:
    """
    Given  a computer with an 8 GB card and 32 GB of memory, where gpt-oss:20b does not fit the
           card but fits with the computer's memory
    When   the model list is rated and the model is ticked for download
    Then   its row says "Very slow" in the normal text colour with an explanatory tooltip (it is
           not greyed out like a too-large model) and no "download anyway?" question is asked
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    service = _StubService()
    service.system_memory_gb = 32.0
    dlg = _make_dialog(qtbot, service=service)
    dlg._on_detection_done(_detection_results(dlg, set()))
    row = dlg.ollama_models._model_items["gpt-oss:20b"]

    assert row.text(1) == "Very slow"  # under 70 % of the model fits the card
    assert "processor" in row.toolTip(1)
    assert row.foreground(1).style() == Qt.BrushStyle.NoBrush

    def no_question(*_a, **_k):
        raise AssertionError("a slow model must not ask for confirmation")

    monkeypatch.setattr(_dialogs, "ask", no_question)
    assert dlg.ollama_models.confirm_too_large(["gpt-oss:20b"]) is True


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_without_the_computers_memory_the_same_model_is_still_too_large(qtbot) -> None:
    """
    Given  the same 8 GB computer but its memory unknown (for example a remote server)
    When   the model list is rated
    Then   gpt-oss:20b does not fit, as before
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))

    assert dlg.ollama_models._model_items["gpt-oss:20b"].text(1) == "Won't fit"


# ── detection regression ────────────────────────────────────────────────────


def test_detection_marks_rows_downloaded_and_disables_them(qtbot) -> None:
    """
    Given  a detection result with glm-ocr installed
    When   _on_detection_done runs
    Then   the row shows a downloaded mark, is disabled and is not ticked
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, {"glm-ocr:q8_0"}))
    row = dlg.ollama_models._model_items["glm-ocr:q8_0"]
    assert row.text(3).startswith("✓")
    assert not bool(row.flags() & Qt.ItemFlag.ItemIsEnabled)
    assert row.checkState(0) is Qt.CheckState.Unchecked


def test_download_install_collects_ticked_child_rows(qtbot) -> None:
    """
    Given  two ticked model rows
    When   _on_download_install runs with the install round patched
    Then   exactly those two tags are handed to the round (regression: flat-list loop)
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    # Undo the pre-ticking so the test controls exactly what is ticked.
    for _tag, row in dlg.ollama_models._iter_model_items():
        row.setCheckState(0, Qt.CheckState.Unchecked)
    dlg.ollama_models._model_items[DEFAULT_OCR_MODEL].setCheckState(0, Qt.CheckState.Checked)
    dlg.ollama_models._model_items["deepseek-ocr:latest"].setCheckState(0, Qt.CheckState.Checked)
    start = MagicMock()
    dlg._round.start = start
    dlg._on_download_install()
    assert start.call_args.kwargs["ollama_models"] == [DEFAULT_OCR_MODEL, "deepseek-ocr:latest"]


# ── too-large confirmation ──────────────────────────────────────────────────


def _answer(monkeypatch, *, download_anyway: bool) -> list[Question]:
    """Answer the too-large question with *download_anyway*; return the questions asked."""
    asked: list[Question] = []

    def ask(_parent: object, question: Question) -> bool:
        asked.append(question)
        return download_anyway

    monkeypatch.setattr(_dialogs, "ask", ask)
    return asked


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_selection_cancel_aborts(qtbot, monkeypatch) -> None:
    """
    Given  a ticked TOO_LARGE model and a confirmation answered with Cancel
    When   _on_download_install runs
    Then   no pull happens
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    # Undo the pre-ticking so only the TOO_LARGE model is ticked.
    for _tag, row in dlg.ollama_models._iter_model_items():
        row.setCheckState(0, Qt.CheckState.Unchecked)
    dlg.ollama_models._model_items["gpt-oss:20b"].setCheckState(0, Qt.CheckState.Checked)
    start = MagicMock()
    dlg._round.start = start
    asked = _answer(monkeypatch, download_anyway=False)
    dlg._on_download_install()
    start.assert_not_called()
    assert "needs about 14 GB of graphics memory" in asked[0].body
    assert "computers of this type have about 8 GB" in asked[0].body


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_selection_download_anyway_proceeds(qtbot, monkeypatch) -> None:
    """
    Given  a ticked TOO_LARGE model and a confirmation answered with Download anyway
    When   _on_download_install runs
    Then   the pull happens
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    # Undo the pre-ticking so only the TOO_LARGE model is ticked.
    for _tag, row in dlg.ollama_models._iter_model_items():
        row.setCheckState(0, Qt.CheckState.Unchecked)
    dlg.ollama_models._model_items["gpt-oss:20b"].setCheckState(0, Qt.CheckState.Checked)
    start = MagicMock()
    dlg._round.start = start
    _answer(monkeypatch, download_anyway=True)
    dlg._on_download_install()
    assert start.call_args.kwargs["ollama_models"] == ["gpt-oss:20b"]


# ── custom models ────────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Category_Guidance")
def test_custom_model_goes_into_other_group(qtbot) -> None:
    """
    Given  a custom model tag added by name
    When   the row is placed
    Then   it lives in the visible "Other models" group and is ticked
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg.ollama_models._ollama_custom_input.setText("llama3.2:latest")
    dlg.ollama_models._on_add_custom_ollama_model()
    other = dlg.ollama_models._group_items["other"]
    assert other.childCount() == 1
    assert other.isHidden() is False
    row = dlg.ollama_models._model_items["llama3.2:latest"]
    assert row.checkState(0) is Qt.CheckState.Checked


@pytest.mark.use_case("Model_Category_Guidance")
def test_custom_model_duplicates_are_ignored(qtbot) -> None:
    """
    Given  a custom model already in the list
    When   the same tag is added again
    Then   no duplicate row is created
    """
    dlg = _make_dialog(qtbot)
    dlg.ollama_models._ollama_custom_input.setText("llama3.2:latest")
    dlg.ollama_models._on_add_custom_ollama_model()
    dlg.ollama_models._ollama_custom_input.setText("llama3.2:latest")
    dlg.ollama_models._on_add_custom_ollama_model()
    assert dlg.ollama_models._group_items["other"].childCount() == 1


@pytest.mark.use_case("Model_Category_Guidance")
def test_pulled_custom_model_stays_in_other_and_shows_abilities(qtbot) -> None:
    """
    Given  a custom model that was pulled and that the server reports as able to read images
    When   detection finishes
    Then   its row stays in "Other models" and says what it can do ("Reads & writes")
    """
    service = _StubService()
    service.custom_uses = {"my-vision-model:latest": frozenset({"ocr", "general"})}
    dlg = _make_dialog(qtbot, service=service)
    dlg.ollama_models._ollama_custom_input.setText("my-vision-model:latest")
    dlg.ollama_models._on_add_custom_ollama_model()
    dlg._on_detection_done(_detection_results(dlg, {"my-vision-model:latest"}))
    assert "my-vision-model:latest" in _group_tags(dlg, "other")
    assert "my-vision-model:latest" not in _group_tags(dlg, "ocr")
    assert dlg.ollama_models._model_items["my-vision-model:latest"].text(1) == "Reads & writes"


@pytest.mark.use_case("Model_Category_Guidance")
def test_failed_installed_check_still_ticks_best_models_and_preselect(qtbot) -> None:
    """
    Given  an installed-model check that fails (for example the server is unreachable)
    When   the dialog opens with a preselected model
    Then   the failure is shown, and the best models and the preselected model are
           still ticked — nothing is downloaded until the user clicks Install
    """
    service = _StubService()
    service.status_error = RuntimeError("Ollama is unreachable")
    dlg = _make_dialog(
        qtbot, service=service, preselect=[InstallItem(InstallKind.OLLAMA, "llama3.2:latest")]
    )
    dlg.on_shown()
    qtbot.waitUntil(lambda: "Detection failed" in dlg.status._status.text(), timeout=5000)
    assert _ticked(dlg) == {DEFAULT_OCR_MODEL, DEFAULT_WRITING_MODEL, "llama3.2:latest"}


# ── the profile is read-only here ──────────────────────────────────────────


@pytest.mark.use_case("Hardware_Profile_Override")
def test_the_computer_profile_is_shown_read_only_with_a_change_link(qtbot) -> None:
    """
    Given  the Add-ons page
    When   it is built
    Then   "My computer" is a read-only line (no combo), with a "change…" link that asks the
           window for the This computer page
    """
    dlg = _make_dialog(qtbot)
    assert dlg.ollama_models._hardware_selector._combo is None

    with qtbot.waitSignal(dlg.navigate_requested, timeout=2000) as blocker:
        dlg.ollama_models._profile_line.change_link.linkActivated.emit("change")

    assert blocker.args == [SettingsPage.AI_SETUP, "", []]


@pytest.mark.use_case("Hardware_Profile_Override")
def test_the_page_follows_a_profile_chosen_on_this_computer(qtbot) -> None:
    """
    Given  the Add-ons page on the 8 GB profile
    When   the window passes on a change to a basic computer
    Then   the page re-rates its rows for that profile and moves the automatic ticks
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    assert _ticked(dlg) == {DEFAULT_OCR_MODEL, DEFAULT_WRITING_MODEL}

    with qtbot.waitSignal(dlg.ollama_models._hardware_selector.resolved_changed, timeout=5000):
        dlg.set_hardware_setting(HardwareProfileSetting.CPU)

    assert _ticked(dlg) == {"glm-ocr:q8_0", "gemma4:e4b"}


@pytest.mark.use_case("Hardware_Profile_Override")
def test_a_new_server_address_starts_a_new_detection(qtbot) -> None:
    """
    Given  an Add-ons page that already detected what is installed
    When   the window passes on another server address
    Then   what is installed is asked again for the new server; the same address does nothing
    """
    service = _StubService()
    dlg = _make_dialog(qtbot, service=service)
    dlg.on_shown()
    qtbot.waitUntil(lambda: dlg._installed_ready, timeout=5000)
    calls = service.status_calls

    dlg.set_ollama_url("http://127.0.0.1:11434")
    qtbot.wait(50)
    assert service.status_calls == calls

    dlg.set_ollama_url("http://192.168.1.50:11434")
    qtbot.waitUntil(lambda: service.status_calls == calls + 1, timeout=5000)


# ── what the app carries itself ─────────────────────────────────────────────


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_a_model_the_app_carries_itself_says_included(qtbot) -> None:
    """
    Given  a detection that finds the reader and the page models, carried by the app
    When   the reading-models tab is shown
    Then   both rows say "included" and cannot be ticked: there is nothing to download
    """
    dlg = _make_dialog(qtbot)
    detected = _detection_results(dlg, set())
    detected.installed.update(
        {
            READER_ITEM: True,
            PAGE_MODELS_ITEM: True,
            READER_ITEM.bundled: True,
            PAGE_MODELS_ITEM.bundled: True,
        }
    )

    dlg._on_detection_done(detected)

    for item in (READER_ITEM, PAGE_MODELS_ITEM):
        assert dlg.reading_models.checkboxes[item].text().endswith(txt.INCLUDED)
        assert dlg.reading_models.checkboxes[item].isEnabled() is False


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_a_model_that_had_to_be_downloaded_still_says_downloaded(qtbot) -> None:
    """
    Given  a detection that finds the reader, in a build that does not carry it
    When   the reading-models tab is shown
    Then   the row says "downloaded", not "included"
    """
    dlg = _make_dialog(qtbot)
    detected = _detection_results(dlg, set())
    detected.installed.update({READER_ITEM: True, READER_ITEM.bundled: False})

    dlg._on_detection_done(detected)

    assert dlg.reading_models.checkboxes[READER_ITEM].text().endswith(txt.READER_DOWNLOADED)


def test_the_install_items_keep_their_stored_keys() -> None:
    """
    Given  the items the Add-ons rows are keyed by, in the service's status map and in the log
    When   their keys are read
    Then   each key still names its kind and its name, and the bundled item is one kind further
    """
    assert READER_ITEM.key == "reader:ppocr"
    assert PAGE_MODELS_ITEM.key == "page_models:all"
    assert READER_ITEM.bundled.key == "bundled:reader:ppocr"
    assert PAGE_MODELS_ITEM.bundled.key == "bundled:page_models:all"


# ── deep links and closing ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("focus", "tab"),
    [
        ("local_models", AddonsPage.TAB_LOCAL_MODELS),
        ("reading_models", AddonsPage.TAB_READING_MODELS),
        ("online_services", AddonsPage.TAB_ONLINE_SERVICES),
        ("name_detection", AddonsPage.TAB_NAME_DETECTION),
    ],
)
def test_focus_shows_the_matching_tab(qtbot, focus: str, tab: int) -> None:
    """
    Given  the Add-ons page
    When   a deep link names one of its sections
    Then   that tab is shown
    """
    dlg = _make_dialog(qtbot)

    dlg.focus(focus)

    assert dlg._tabs.currentIndex() == tab


def test_a_preselect_after_detection_ticks_at_once(qtbot) -> None:
    """
    Given  an Add-ons page that finished detecting what is installed
    When   a deep link brings a model to tick (the window was already open)
    Then   it is ticked straight away, not only after the next detection
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    assert dlg.ollama_models._model_items["mistral:latest"].checkState(0) is Qt.CheckState.Unchecked

    dlg.focus(FOCUS_LOCAL_MODELS, [InstallItem(InstallKind.OLLAMA, "mistral:latest")])

    assert dlg.ollama_models._model_items["mistral:latest"].checkState(0) is Qt.CheckState.Checked


def test_the_page_applies_immediately_and_blocks_closing_only_while_installing(
    qtbot, monkeypatch
) -> None:
    """
    Given  the Add-ons page
    When   a pip install is running, and when it is not
    Then   the page says it applies immediately, and close_blocker() explains why the window
           cannot close only while the install runs
    """
    from PySide6.QtCore import QThread, Signal  # noqa: PLC0415

    import teachers_teammate.gui._install_round as install_round  # noqa: PLC0415

    class _Busy(QThread):
        log_line = Signal(str)
        finished_ok = Signal(list)
        finished_err = Signal(str)

        def __init__(self, _service, _packages) -> None:
            super().__init__()
            self.release = threading.Event()

        def run(self) -> None:
            self.release.wait(5)

    monkeypatch.setattr(install_round, "_PipInstallThread", _Busy)
    dlg = _make_dialog(qtbot)
    assert dlg.applies_immediately is True
    assert dlg.close_blocker() == ""

    dlg._collect_pip_packages = lambda: ["spacy"]  # ty: ignore[invalid-assignment]
    dlg._on_download_install()
    busy = dlg._round._thread
    assert isinstance(busy, _Busy)
    try:
        assert "installation is still running" in dlg.close_blocker()
    finally:
        busy.release.set()
        busy.wait(5000)

    assert dlg.close_blocker() == ""


def test_a_running_pull_keeps_going_when_the_page_shuts_down(qtbot) -> None:
    """
    Given  a pull started through the main window's pull manager
    When   the page shuts down (the window closed)
    Then   the page no longer follows the manager, and the manager is not asked to abort
    """
    from PySide6.QtCore import QObject, Signal  # noqa: PLC0415

    class _Manager(QObject):
        progress = Signal(str, float, float)
        finished = Signal(str)
        error = Signal(str)
        is_active = False
        aborted = False
        started: ClassVar[list[tuple[str, str]]] = []
        queued: ClassVar[list[str]] = []

        def start(self, url: str, model: str, queue=()) -> None:
            self.started.append((url, model))

        def abort(self) -> None:
            self.aborted = True

    manager = _Manager()
    dlg = _make_dialog(qtbot, pull_manager=manager)
    dlg._round.start(ollama_models=[DEFAULT_OCR_MODEL], ollama_url="http://127.0.0.1:11434")
    assert manager.started == [("http://127.0.0.1:11434", DEFAULT_OCR_MODEL)]
    assert dlg._round._pull_connected

    dlg.shutdown()

    assert not dlg._round._pull_connected
    assert manager.aborted is False
    manager.finished.emit(DEFAULT_OCR_MODEL)  # a late answer must not reach the closed page
    assert dlg.status._round_result == ""


def test_a_page_opened_during_a_download_marks_the_models_that_are_coming(qtbot) -> None:
    """
    Given  a main-window pull manager that is downloading one model with another queued
    When   the page is opened (a new page, as after closing the window) and shows the models
    Then   the Size column says "downloading…" for the first and "queued" for the second, and
           the first shows its progress when the manager reports it
    """
    from PySide6.QtCore import QObject, Signal  # noqa: PLC0415

    class _Manager(QObject):
        progress = Signal(str, float, float)
        finished = Signal(str)
        error = Signal(str)
        is_active = True
        current_model = ""
        queued: ClassVar[list[str]] = []

        def start(self, url: str, model: str, queue=()) -> None:
            pass

        def abort(self) -> None:
            pass

    manager = _Manager()
    dlg = _make_dialog(qtbot, pull_manager=manager)
    first, second = [tag for tag, _item in dlg.ollama_models._iter_model_items()][:2]
    manager.current_model = first
    type(manager).queued = [second]

    dlg.on_shown()
    dlg._on_detection_done(_detection_results(dlg, set()))

    assert dlg.ollama_models._model_items[first].text(3) == "downloading…"
    assert dlg.ollama_models._model_items[second].text(3) == "queued"

    manager.progress.emit("pulling", 25.0, 100.0)

    assert dlg.ollama_models._model_items[first].text(3) == "downloading… 25%"


def test_the_size_column_is_named_for_what_it_shows(qtbot) -> None:
    """
    Given  the model list of the page
    When   its header is read
    Then   the column says "Download size" and has a tooltip that explains the ✓ and the marks
    """
    dlg = _make_dialog(qtbot)
    tree = dlg.ollama_models._ollama_model_list

    assert tree.headerItem().text(3) == "Download size"
    assert "✓" in tree.headerItem().toolTip(3)


def test_the_queue_of_models_is_handed_to_the_manager(qtbot) -> None:
    """
    Given  an install round of two models through the main window's pull manager
    When   the page starts it
    Then   the manager gets the whole queue, so the models after the first are still pulled
           when the window is closed; the page keeps following until the queue is empty
    """
    from PySide6.QtCore import QObject, Signal  # noqa: PLC0415

    class _Manager(QObject):
        progress = Signal(str, float, float)
        finished = Signal(str)
        error = Signal(str)
        is_active = False
        queued: ClassVar[list[str]] = []
        calls: ClassVar[list[tuple[str, str, list[str]]]] = []

        def start(self, url: str, model: str, queue=()) -> None:
            self.calls.append((url, model, list(queue)))
            type(self).queued = list(queue)

        def abort(self) -> None:
            pass

    manager = _Manager()
    dlg = _make_dialog(qtbot, pull_manager=manager)

    dlg._round.start(ollama_models=["a:1b", "b:1b"], ollama_url="http://127.0.0.1:11434")
    assert manager.calls == [("http://127.0.0.1:11434", "a:1b", ["b:1b"])]

    changed: list[bool] = []
    dlg.packages_changed.connect(lambda: changed.append(True))

    manager.finished.emit("a:1b")  # the manager goes on with b:1b by itself
    assert dlg._round._pull_connected
    assert changed == []

    type(manager).queued = []
    manager.finished.emit("b:1b")
    assert not dlg._round._pull_connected
    assert changed == [True]


# ── pre-ticking waits for BOTH detections (review finding #4) ───────────────


def _ticked(dlg: AddonsPage) -> set[str]:
    """Return the tags of all enabled rows that are currently ticked."""
    from PySide6.QtCore import Qt  # noqa: PLC0415

    return {
        tag
        for tag, row in dlg.ollama_models._iter_model_items()
        if row.checkState(0) is Qt.CheckState.Checked
        and bool(row.flags() & Qt.ItemFlag.ItemIsEnabled)
    }


def _choose_computer(qtbot, dlg: AddonsPage, text: str) -> None:
    with qtbot.waitSignal(dlg.ollama_models._hardware_selector.resolved_changed, timeout=5000):
        dlg.set_hardware_setting(_SETTING_OF_TEXT[text])


_CPU_TEXT = "Basic computer or small graphics card (under 8 GB)"
_16GB_TEXT = "Graphics card with 16 GB memory or more"
#: The page follows the profile chosen on This computer, passed on as the setting value.
_SETTING_OF_TEXT = {
    _CPU_TEXT: HardwareProfileSetting.CPU,
    _16GB_TEXT: HardwareProfileSetting.CARD_16GB,
}


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_pre_ticking_waits_for_hardware_detection(qtbot) -> None:
    """
    Given  the installed-model check finishes BEFORE the hardware detection and
           the computer turns out to be a basic one
    When   the hardware detection finishes later
    Then   nothing was ticked in between, and afterwards the basic-computer
           models are pre-ticked — not the 8 GB ones assumed while unknown
    """
    service = _StubService()
    service.detection_gate = threading.Event()
    service.detection = HardwareDetection(
        profile=HardwareProfile.CPU, reason=DetectionReason.NO_GPU_FOUND
    )
    dlg = _make_dialog(qtbot, hardware_profile="auto", service=service, wait_for_profile=False)

    dlg._on_detection_done(_detection_results(dlg, set()))
    assert _ticked(dlg) == set()

    with qtbot.waitSignal(dlg.ollama_models._hardware_selector.resolved_changed, timeout=5000):
        service.detection_gate.set()
    assert _ticked(dlg) == {"glm-ocr:q8_0", "gemma4:e4b"}


@pytest.mark.use_case("Hardware_Profile_Override")
def test_profile_change_replaces_untouched_auto_ticks_and_keeps_user_ticks(qtbot) -> None:
    """
    Given  automatic ticks for 8 GB and one model the user ticked by hand
    When   the user switches "My computer" to a basic computer
    Then   the automatic ticks move to the basic-computer models and the
           user's own tick stays
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    assert _ticked(dlg) == {DEFAULT_OCR_MODEL, DEFAULT_WRITING_MODEL}
    dlg.ollama_models._model_items["mistral:latest"].setCheckState(0, Qt.CheckState.Checked)

    _choose_computer(qtbot, dlg, _CPU_TEXT)

    assert _ticked(dlg) == {"glm-ocr:q8_0", "gemma4:e4b", "mistral:latest"}


@pytest.mark.use_case("Hardware_Profile_Override")
def test_user_unticking_an_auto_tick_is_respected_after_profile_change(qtbot) -> None:
    """
    Given  an automatic tick the user removed again
    When   the profile changes to one that recommends the same model
    Then   the model is not ticked again
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    dlg.ollama_models._model_items[DEFAULT_OCR_MODEL].setCheckState(0, Qt.CheckState.Unchecked)

    _choose_computer(qtbot, dlg, _16GB_TEXT)

    assert DEFAULT_OCR_MODEL not in _ticked(dlg)  # 16 GB recommends it as well
    assert "gemma4:12b" in _ticked(dlg)  # the 16 GB best writing model


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_tooltip_is_cleared_when_model_fits_again(qtbot) -> None:
    """
    Given  gpt-oss shown as too large on 8 GB (with an explanatory tooltip)
    When   the profile changes to 16 GB, where it fits
    Then   the tooltip of the suitability column is gone and the row is rated "Works"
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    row = dlg.ollama_models._model_items["gpt-oss:20b"]
    assert "14 GB" in row.toolTip(1)

    _choose_computer(qtbot, dlg, _16GB_TEXT)

    assert "14 GB" not in row.toolTip(1)  # the "too large" explanation is gone
    assert row.text(1) == "Works"  # 14 GB are within the 16 GB card


@pytest.mark.use_case("Model_Category_Guidance")
def test_custom_tag_without_suffix_maps_onto_the_catalog_row(qtbot) -> None:
    """
    Given  the catalog row "deepseek-ocr:latest"
    When   the user adds "deepseek-ocr" by name
    Then   no duplicate appears in "Other models"
    """
    dlg = _make_dialog(qtbot)
    before = len(dlg.ollama_models._model_items)
    dlg.ollama_models._ollama_custom_input.setText("deepseek-ocr")
    dlg.ollama_models._on_add_custom_ollama_model()
    assert len(dlg.ollama_models._model_items) == before
    assert dlg.ollama_models._group_items["other"].childCount() == 0


@pytest.mark.use_case("Model_Category_Guidance")
def test_detection_runs_again_when_requested_during_a_running_check(qtbot) -> None:
    """
    Given  an installed-model check that is still running
    When   another check is requested (a pull just finished)
    Then   a second check runs afterwards instead of being dropped
    """
    service = _StubService()
    service.status_gate = threading.Event()
    dlg = _make_dialog(qtbot, service=service)

    dlg._start_detection()
    qtbot.waitUntil(lambda: service.status_calls == 1, timeout=5000)
    dlg._start_detection()  # requested while the first check is blocked
    assert dlg._detect_again is True

    service.status_gate.set()
    qtbot.waitUntil(lambda: service.status_calls == 2, timeout=5000)
    qtbot.waitUntil(lambda: not dlg._detect_running, timeout=5000)


# ── real service + fake Ollama server: the download → re-check → show flow ──


@pytest.fixture
def fake_ollama() -> Iterator[FakeOllama]:
    """The shared fake Ollama server: the *real* service and client talk to it over HTTP."""
    with FakeOllama() as server:
        yield server


def _real_service():
    from teachers_teammate.application.composition import build_app_services  # noqa: PLC0415

    return build_app_services()


@pytest.mark.use_case("Model_Category_Guidance")
def test_finished_pull_shows_model_as_downloaded_without_waiting_for_cache(
    qtbot, monkeypatch, fake_ollama
) -> None:
    """
    Given  the real service that cached an empty installed-model list
    When   a pull finishes and the dialog checks again
    Then   the model shows as downloaded at once (review finding #1: the stale
           5-minute cache used to hide it, so it was offered for download again)
    """
    import teachers_teammate.gui._addon_installer_dialog as installer  # noqa: PLC0415
    from teachers_teammate.gui._long_job import LongJobThread  # noqa: PLC0415

    class _FakePullThread(LongJobThread):
        """Pulls the model without a server: it is installed at once."""

        def __init__(self, _service: Any, _url: str, model: str) -> None:
            super().__init__()
            self._model = model

        def work(self) -> str:
            return self._model

    monkeypatch.setattr(installer, "_OllamaPullThread", _FakePullThread)
    dlg = _make_dialog(qtbot, service=_real_service(), ollama_url=fake_ollama.url)
    dlg._start_detection()
    qtbot.waitUntil(lambda: dlg._installed_ready, timeout=5000)
    assert not dlg.ollama_models._model_items[DEFAULT_OCR_MODEL].text(3).startswith("✓")

    fake_ollama.install(DEFAULT_OCR_MODEL, size=6_000_000_000)  # the pull arrived
    dlg._round.start(ollama_models=[DEFAULT_OCR_MODEL], ollama_url=fake_ollama.url)

    # Decimal GB, like the catalog sizes shown before the download; "✓" marks it downloaded.
    qtbot.waitUntil(
        lambda: dlg.ollama_models._model_items[DEFAULT_OCR_MODEL].text(3) == "✓ 6.0 GB",
        timeout=5000,
    )


@pytest.mark.use_case("Model_Category_Guidance")
def test_pulled_custom_models_stay_in_other_with_their_abilities(
    qtbot, monkeypatch, fake_ollama
) -> None:
    """
    Given  three custom models ticked for download (two that read images, one text-only)
    When   the pulls run one after the other through the real pull pipeline
    Then   all three stay in "Other models" and each row says what the model can do
    """
    import teachers_teammate.gui._addon_installer_dialog as installer  # noqa: PLC0415
    from teachers_teammate.gui._long_job import LongJobThread  # noqa: PLC0415

    class _FakePullThread(LongJobThread):
        """Puts the model into the fake server and reports it pulled."""

        def __init__(self, _service: Any, _url: str, model: str) -> None:
            super().__init__()
            self._model = model

        def work(self) -> str:
            fake_ollama.install(self._model, capabilities[self._model], size=1_000_000)
            return self._model

    monkeypatch.setattr(installer, "_OllamaPullThread", _FakePullThread)
    capabilities = {
        "reader-a:latest": ("vision", "completion"),
        "writer-b:latest": ("completion",),
        "reader-c:latest": ("vision",),
    }
    dlg = _make_dialog(qtbot, service=_real_service(), ollama_url=fake_ollama.url)
    for tag in capabilities:
        dlg.ollama_models._ollama_custom_input.setText(tag)
        dlg.ollama_models._on_add_custom_ollama_model()

    dlg._on_download_install()

    qtbot.waitUntil(
        lambda: (
            dlg.ollama_models._model_items["reader-a:latest"].text(1) == "Reads & writes"
            and dlg.ollama_models._model_items["reader-c:latest"].text(1) == "Reads & writes"
            and dlg.ollama_models._model_items["writer-b:latest"].text(1) == "Writes only"
        ),
        timeout=5000,
    )
    assert dlg.ollama_models._group_items["other"].childCount() == 3


# ── tab constants ───────────────────────────────────────────────────────────


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_tab_constants_point_at_the_matching_tabs(qtbot) -> None:
    """
    Given  the Add-ons page (the local-models tab is in front)
    When   the tab constants are used to look up tab titles
    Then   each constant names the tab it claims (callers open tabs by constant)
    """
    from PySide6.QtWidgets import QTabWidget  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    tabs = dlg.findChild(QTabWidget)
    assert tabs is not None
    assert tabs.tabText(AddonsPage.TAB_LOCAL_MODELS) == "AI models for this computer"
    assert tabs.tabText(AddonsPage.TAB_ONLINE_SERVICES) == "Online AI services"
    assert tabs.tabText(AddonsPage.TAB_NAME_DETECTION) == "Name detection"
    assert tabs.tabText(AddonsPage.TAB_READING_MODELS) == "Reading models"


@pytest.mark.use_case("Model_Category_Guidance")
def test_rated_rows_use_the_theme_text_colour(qtbot) -> None:
    """
    Given  the model tree after the suitability labels were computed
    When   the foreground of a row that is not "too large" is read
    Then   no explicit (invalid, black) colour is set: the theme's text colour applies
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg.ollama_models._update_model_suitability()

    checked = 0
    for _tag, item in dlg.ollama_models._iter_model_items():
        for column in (0, 1):
            brush = item.foreground(column)
            if item.text(1) == "Won't fit":
                continue
            # An empty QBrush has no style; a QColor() brush would be a solid black.
            assert brush.style() == Qt.BrushStyle.NoBrush
            checked += 1
    assert checked > 0


@pytest.mark.use_case("Model_Category_Guidance")
def test_adding_a_listed_model_selects_it_and_says_so(qtbot) -> None:
    """
    Given  a catalog model that is already a row (no duplicate may be created)
    When   its name is added again by hand
    Then   the existing row is ticked and shown, and a hint says it is already listed
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    tag = OLLAMA_CATALOG[0].tag
    before = len(dlg.ollama_models._model_items)

    dlg.ollama_models._ollama_custom_input.setText(tag)
    dlg.ollama_models._on_add_custom_ollama_model()

    assert len(dlg.ollama_models._model_items) == before
    assert dlg.ollama_models._model_items[tag].checkState(0) is Qt.CheckState.Checked
    assert dlg.ollama_models._ollama_hint_lbl.text() == "That model is already in the list."
    assert dlg.ollama_models._ollama_custom_input.text() == ""


@pytest.mark.use_case("Model_Category_Guidance")
def test_enter_in_the_name_field_adds_the_model(qtbot) -> None:
    """
    Given  a model name typed into the "Add a model by name" field
    When   the user presses Enter
    Then   the model is added exactly like with the Add button
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg.ollama_models._ollama_custom_input.setText("llama3.2:latest")

    dlg.ollama_models._ollama_custom_input.returnPressed.emit()

    assert dlg.ollama_models._model_items["llama3.2:latest"].checkState(0) is Qt.CheckState.Checked


def test_enter_in_the_name_field_does_not_press_a_button(qtbot, monkeypatch) -> None:
    """
    Given  a model name typed into the "Add a model by name" field of the shown page
    When   the user presses the real Enter key
    Then   only the model is added: no button (Install, Add, …) is activated, and none of the
           page's buttons is an auto-default button that a window's Enter handling could press
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415
    from PySide6.QtTest import QTest  # noqa: PLC0415
    from PySide6.QtWidgets import QPushButton  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    pressed: list[str] = []
    for button in dlg.findChildren(QPushButton):
        button.clicked.connect(lambda _=False, b=button: pressed.append(b.text()))
    dlg.show()
    qtbot.waitExposed(dlg)
    dlg.ollama_models._ollama_custom_input.setFocus()
    dlg.ollama_models._ollama_custom_input.setText("llama3.2:latest")

    QTest.keyClick(dlg.ollama_models._ollama_custom_input, Qt.Key.Key_Return)

    assert "llama3.2:latest" in dlg.ollama_models._model_items
    assert pressed == []
    assert not any(button.autoDefault() for button in dlg.findChildren(QPushButton))
    assert dlg.isVisible() is True


# ── After an app update: the banner offers "Update add-ons" ──────────────────


def test_the_update_banner_shows_only_while_add_ons_are_outdated(qtbot) -> None:
    """
    Given  the Add-ons page, once with current add-ons and once with outdated ones
    When   the page is shown
    Then   the banner and its button appear only for the outdated ones
    """
    current = _make_dialog(qtbot)
    stale_service = _StubService()
    stale_service.outdated_addons = True
    stale = _make_dialog(qtbot, service=stale_service)
    stale.show()

    assert current.update_banner.isHidden()
    assert stale.update_banner.isVisible()
    assert stale.update_banner.button.text() == "Update add-ons"


def test_update_add_ons_installs_only_after_the_click_and_then_hides_the_banner(
    qtbot,
) -> None:
    """
    Given  outdated add-ons and the Add-ons page (a deep link focused the update)
    When   the page is built, and then the teacher clicks "Update add-ons"
    Then   nothing was installed before the click; the click updates once and the banner goes
    """
    service = _StubService()
    service.outdated_addons = True
    page = _make_dialog(qtbot, service=service)
    page.show()
    page.focus(FOCUS_ADDON_UPDATE)
    assert service.addon_updates == 0

    with qtbot.waitSignal(page._round.finished, timeout=5000):
        page.update_banner.button.click()

    assert service.addon_updates == 1
    assert page.update_banner.isHidden()
    assert page.update_banner.button.isEnabled()
    assert "installing the add-ons again" in page.status._dl_log.toPlainText()


# ── The status line, the bar and the details ─────────────────────────────────


def _start_round(dlg: AddonsPage, steps: list[InstallItem]) -> None:
    """Put the page into the state a running round leaves it in, without a real install."""
    dlg.status.begin_round(steps)
    dlg._round._running = True
    dlg.status.round_started()


def test_the_status_line_counts_the_steps_of_a_round(qtbot) -> None:
    """
    Given  a round that installs spaCy and then the German model
    When   the first step starts and the second follows
    Then   the line names the item and how many steps there are, in the teacher's words
    """
    dlg = _make_dialog(qtbot)
    _start_round(dlg, [SPACY_ITEM, GERMAN_NAMES])

    dlg._round._phase = InstallKind.PACKAGE
    dlg._round.item_started.emit(SPACY_ITEM)
    assert dlg.status._status.text() == "Installing spaCy (step 1 of 2)…"

    dlg._round._phase = InstallKind.SPACY
    dlg._round.item_started.emit(GERMAN_NAMES)
    assert dlg.status._status.text() == "Installing the German model (step 2 of 2)…"


def test_the_running_time_stays_on_the_line_when_it_is_written_again(qtbot) -> None:
    """
    Given  a pip step that has run for five seconds, its line showing the running time
    When   the line is written again (as every progress or refresh does)
    Then   the running time is still on it, so the line does not flicker between two texts, and
           the timer is a precise one, so its first tick is not early enough to miss the first second
    """
    dlg = _make_dialog(qtbot)
    assert dlg.status._elapsed_timer.timerType() is Qt.TimerType.PreciseTimer
    _start_round(dlg, [SPACY_ITEM])
    dlg._round._phase = InstallKind.PACKAGE
    dlg._round.item_started.emit(SPACY_ITEM)
    dlg.status._status_started -= 5

    dlg.status._tick_elapsed()
    assert dlg.status._status.text() == "Installing spaCy (step 1 of 1)… 0:05"
    dlg.status.refresh()

    assert dlg.status._status.text() == "Installing spaCy (step 1 of 1)… 0:05"


def test_the_steps_of_a_round_count_pip_once_however_many_packages_it_installs(qtbot) -> None:
    """
    Given  a round that installs two pip packages and then the German model
    When   pip's step starts and then the model's
    Then   pip is step 1 of 2 and the model step 2 of 2: no number is skipped
    """
    dlg = _make_dialog(qtbot)
    _start_round(dlg, [SPACY_ITEM, OPENAI_PACKAGE, GERMAN_NAMES])

    dlg._round._phase = InstallKind.PACKAGE
    dlg._round.item_started.emit(SPACY_ITEM)
    assert dlg.status._status.text() == "Installing spaCy (step 1 of 2)…"
    dlg._round.item_started.emit(OPENAI_PACKAGE)  # the same pip command
    assert dlg.status._status.text() == "Installing spaCy (step 1 of 2)…"

    dlg._round._phase = InstallKind.SPACY
    dlg._round.item_started.emit(GERMAN_NAMES)
    assert dlg.status._status.text() == "Installing the German model (step 2 of 2)…"


@pytest.mark.parametrize(
    ("line", "hint"),
    [
        ("Collecting spacy>=3.8,<3.9", "spacy"),
        ("Collecting langchain-openai>=0.2", "langchain-openai"),
        ("Downloading spacy-3.8.2-cp314-cp314-win_amd64.whl (12 MB)", "spacy"),
        (
            "Collecting https://github.com/explosion/spacy-models/releases/download/"
            "de_core_news_sm-3.8.0/de_core_news_sm-3.8.0-py3-none-any.whl",
            "de_core_news_sm",
        ),
        ("Installing collected packages: numpy, spacy", "numpy"),
        ("Using cached spacy-3.8.2-py3-none-any.whl (12 MB)", "spacy"),
        ("Successfully installed spacy-3.8.2", ""),
    ],
)
def test_the_hint_names_the_package_pip_is_busy_with(line: str, hint: str) -> None:
    """
    Given  a line pip prints (a requirement, a wheel file, a model URL)
    When   the page reads the package out of it
    Then   it is the plain package name, without specifier, version or address
    """
    assert _install_status.pip_hint(line) == hint


def test_a_failed_install_is_told_in_the_words_of_its_reason() -> None:
    """
    Given  install errors with a known reason, an update error naming two add-ons, and an
           error the app does not know
    When   the page asks for the sentence
    Then   each known reason has its own, the update names the requirements and says the add-ons
           are gone from the folder, and the unknown one says what it says
    """
    offline = AddonInstallError(InstallFailure.OFFLINE)
    no_version = AddonInstallError(InstallFailure.NO_VERSION)

    assert txt.addon_error_text(offline) == "There is no internet connection."
    assert "no internet" not in txt.addon_error_text(no_version)
    assert {txt.ADDONS_FAILURE[reason] for reason in InstallFailure} and len(
        txt.ADDONS_FAILURE
    ) == len(InstallFailure)

    failures = (
        f"spacy>=3.7: {txt.addon_error_text(offline)}; "
        f"langchain-core>=1: {txt.addon_error_text(no_version)}"
    )
    failed: list[tuple[str, RuntimeError]] = [
        ("spacy>=3.7", offline),
        ("langchain-core>=1", no_version),
    ]
    both = AddonUpdateError(failed, gone=["spacy>=3.7", "langchain-core>=1"])
    one = AddonUpdateError(failed[:1], gone=["spacy>=3.7"])
    some = AddonUpdateError(failed, gone=["spacy>=3.7"])
    none = AddonUpdateError(failed[:1])

    assert txt.addon_error_text(both) == f"{failures} {txt.ADDONS_GONE_MANY}"
    assert txt.addon_error_text(one).endswith(txt.ADDONS_GONE_ONE)
    assert "Settings → Add-ons" in txt.addon_error_text(one)
    assert txt.addon_error_text(some) == f"{failures} {txt.ADDONS_GONE_SOME}"
    assert txt.addon_error_text(none) == f"spacy>=3.7: {txt.addon_error_text(offline)}"
    assert txt.addon_error_text(RuntimeError("boom")) == "boom"


def test_a_pip_step_shows_an_indeterminate_bar_and_the_elapsed_time(qtbot) -> None:
    """
    Given  a round whose only step is a pip install (pip reports no percentage)
    When   the step starts and time passes
    Then   the bar is indeterminate and the line shows how long the step has been running
    """
    dlg = _make_dialog(qtbot)
    _start_round(dlg, [SPACY_ITEM])

    dlg._round._phase = InstallKind.PACKAGE
    dlg._round.item_started.emit(SPACY_ITEM)

    assert (dlg.status._dl_progress.minimum(), dlg.status._dl_progress.maximum()) == (0, 0)
    assert dlg.status._status.text() == "Installing spaCy (step 1 of 1)…"

    dlg.status._status_started -= 102  # the step has been running for 1:42
    dlg.status._tick_elapsed()

    assert dlg.status._status.text() == "Installing spaCy (step 1 of 1)… 1:42"


def test_a_model_pull_shows_its_percentage(qtbot) -> None:
    """
    Given  a round that downloads a model
    When   the manager reports 45 % of the bytes
    Then   the line and the bar show 45 %
    """
    dlg = _make_dialog(qtbot)
    _start_round(dlg, [InstallItem(InstallKind.OLLAMA, DEFAULT_OCR_MODEL)])

    dlg._round._phase = InstallKind.OLLAMA
    dlg._round.item_started.emit(InstallItem(InstallKind.OLLAMA, DEFAULT_OCR_MODEL))
    dlg._round.pull_progress.emit("pulling", 45.0, 100.0)

    assert dlg.status._status.text() == f"Downloading {DEFAULT_OCR_MODEL} (step 1 of 1), 45 %"
    assert dlg.status._dl_progress.value() == 45


def test_the_status_line_says_what_a_finished_round_installed(qtbot) -> None:
    """
    Given  a round that installed two items
    When   it finishes
    Then   the line says so, and the button is back to its own label and enabled
    """
    dlg = _make_dialog(qtbot)
    _start_round(dlg, [SPACY_ITEM, GERMAN_NAMES])
    dlg._round.item_done.emit(SPACY_ITEM)
    dlg._round.item_done.emit(GERMAN_NAMES)

    dlg._round._running = False
    dlg._round.finished.emit(True)

    assert dlg.status._status.text() == "✓ 2 installed"
    assert dlg.status._dl_install_btn.text() == txt.ADDONS_INSTALL
    assert dlg.status._dl_install_btn.isEnabled()


def test_a_failed_step_says_what_failed_and_opens_the_details(qtbot) -> None:
    """
    Given  a round whose model download fails
    When   the round reports the failure
    Then   the line names the item and the reason in one sentence, and the log is open
    """
    dlg = _make_dialog(qtbot)
    _start_round(dlg, [GERMAN_NAMES])
    dlg._round._phase = InstallKind.SPACY
    dlg._round.item_started.emit(GERMAN_NAMES)

    dlg._round.failed.emit(GERMAN_NAMES, "No space left on the disk.")

    assert dlg.status._status.text() == "✗ the German model failed: No space left on the disk."
    assert not dlg.status._dl_log.isHidden()
    assert dlg.status._details_btn.text() == txt.ADDONS_HIDE_DETAILS


def test_the_details_are_hidden_until_they_are_asked_for(qtbot) -> None:
    """
    Given  the Add-ons page
    When   it is built, and then the teacher clicks "Show details"
    Then   the log is hidden at first and visible afterwards, and Copy copies it
    """
    dlg = _make_dialog(qtbot)
    assert dlg.status._dl_log.isHidden()
    assert dlg.status._copy_btn.isHidden()

    dlg.status._details_btn.click()

    assert not dlg.status._dl_log.isHidden()
    assert not dlg.status._copy_btn.isHidden()
    assert dlg.status._details_btn.text() == txt.ADDONS_HIDE_DETAILS

    dlg.status._dl_log_append("Collecting spacy")
    dlg.status._copy_btn.click()

    assert "Collecting spacy" in QApplication.clipboard().text()


def test_a_pending_package_asks_for_a_restart(qtbot) -> None:
    """
    Given  a package that waits for the next start
    When   the page shows its status
    Then   the line asks for the restart, in the warning colour
    """
    service = _StubService()
    service.pending_addon_packages = lambda: ["spacy"]  # ty: ignore[invalid-assignment]
    dlg = _make_dialog(qtbot, service=service)

    dlg.status.refresh()

    assert dlg.status._status.text() == txt.ADDONS_RESTART_NEEDED
    assert "#e67e22" in dlg.status._status.styleSheet()


def test_the_hint_names_the_package_pip_is_working_on(qtbot) -> None:
    """
    Given  the Add-ons page
    When   pip's log names a package, then a wheel file, and then a line that names none
    Then   the hint shows the package (a wheel file loses its version), and the unknown line
           changes nothing
    """
    dlg = _make_dialog(qtbot)

    dlg.status._dl_log_append("Collecting spacy")
    assert dlg.status._hint.text() == "spacy"
    assert not dlg.status._hint.isHidden()

    dlg.status._dl_log_append("Downloading numpy-2.0.0-cp312-cp312-manylinux_x86_64.whl (16.1 MB)")
    assert dlg.status._hint.text() == "numpy"

    dlg.status._dl_log_append("  Building wheel for blis (pyproject.toml) ... done")
    assert dlg.status._hint.text() == "numpy"


def test_the_install_button_stays_visible_and_says_installing(qtbot) -> None:
    """
    Given  a round that is running
    When   the page is shown
    Then   the button is still there, disabled, and says "Installing…"
    """
    dlg = _make_dialog(qtbot)
    dlg.show()

    _start_round(dlg, [SPACY_ITEM])

    assert not dlg.status._dl_install_btn.isHidden()
    assert not dlg.status._dl_install_btn.isEnabled()
    assert dlg.status._dl_install_btn.text() == txt.ADDONS_INSTALLING


def test_the_action_row_fits_a_laptop_screen(qtbot) -> None:
    """
    Given  the Add-ons page in a window the size of a 1366x768 laptop screen
    When   it is shown
    Then   the install button lies inside the visible area
    """
    dlg = _make_dialog(qtbot)
    dlg.resize(1000, 708)
    dlg.show()
    qtbot.waitExposed(dlg)

    button = dlg.status._dl_install_btn
    bottom = button.mapTo(dlg, button.rect().bottomLeft()).y()

    assert 0 < bottom <= dlg.height()


def test_only_the_model_tree_scrolls(qtbot) -> None:
    """
    Given  the Add-ons page
    When   its tabs are inspected
    Then   the local-models tab is not a scroll area (the tree takes the free height), while
           the short checkbox tabs keep theirs
    """
    dlg = _make_dialog(qtbot)

    assert not isinstance(dlg._tabs.widget(dlg.TAB_LOCAL_MODELS), QScrollArea)
    assert isinstance(dlg._tabs.widget(dlg.TAB_ONLINE_SERVICES), QScrollArea)
