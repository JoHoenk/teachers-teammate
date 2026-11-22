"""Widget tests for the Add-ons tabs: each tab owns its rows, its ticks and what it reports.

The page tests (``test_gui_addons_page.py``) check the page as a whole — the tabs, the status
line and the round.  These tests check one tab at a time: a tab is handed a deep link's entries
or the installation check's answer, and answers for itself what the teacher ticked.
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox
import pytest

from teachers_teammate.domain.providers import AddonPackage, ProviderInfo
from teachers_teammate.gui import _user_text as txt
from teachers_teammate.gui._install_round import PACKAGE_PREFIX, PAGE_MODELS_ITEM, READER_ITEM
from teachers_teammate.gui._model_guidance import SPACY_PREFIX
from teachers_teammate.gui.settings.addons import (
    NameDetectionTab,
    OllamaModelsTab,
    ProvidersTab,
    ReadingModelsTab,
)

from ._gui_harness import settings_context
from ._service_stubs import RecommendationServiceStub


class _ProviderPackagesService(RecommendationServiceStub):
    """A service that offers two provider packages, one of them shared by two providers."""

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        return {
            "mistral": ("langchain_mistralai", "langchain-mistralai"),
            "openai": ("langchain_openai", "langchain-openai"),
        }

    def list_providers(self) -> list[str]:
        return ["mistral", "mistral_eu", "openai"]

    def get_provider_info(self, provider: str) -> ProviderInfo:
        # ``mistral_eu`` shares mistral's package, which the list offers once, under mistral.
        package = (
            AddonPackage(module="langchain_mistralai", pip="langchain-mistralai")
            if provider in ("mistral", "mistral_eu")
            else AddonPackage(module="langchain_openai", pip="langchain-openai")
        )
        return ProviderInfo(
            name=provider,
            display_name=provider.replace("_", " ").title(),
            package=package,
        )


def _providers_tab(qtbot) -> ProvidersTab:
    """Return an Online AI services tab over the two-package stub service."""
    context = settings_context(_ProviderPackagesService())
    tab = ProvidersTab(context.app_service)
    qtbot.addWidget(tab)
    return tab


def _ticked(tab: ProvidersTab | NameDetectionTab | ReadingModelsTab) -> list[str]:
    """Return the keys of the boxes *tab* shows ticked."""
    return [key for key, box in tab.checkboxes.items() if _is_ticked(box)]


def _is_ticked(box: QCheckBox) -> bool:
    """Return whether *box* is ticked and could be ticked (installed ones cannot)."""
    return box.isChecked() and box.isEnabled()


def test_a_package_box_names_every_service_that_needs_it(qtbot) -> None:
    """
    Given  a package that two providers share and one that a single provider needs
    When   the Online AI services tab is built
    Then   each box names the services that need the package, then the pip name
    """
    tab = _providers_tab(qtbot)

    assert tab.checkboxes["provider:mistral"].text() == (
        "Mistral and Mistral Eu — langchain-mistralai"
    )
    assert tab.checkboxes["provider:openai"].text() == "Openai — langchain-openai"


def test_an_optional_connector_is_labelled_as_one() -> None:
    """
    Given  the package of a service the app can talk to without it
    When   its box label is built
    Then   it says it is a connector, so it is not read as a requirement of an online service
    """
    label = txt.provider_package_label(["Ollama"], "langchain-ollama", connector=True)

    assert label == "Ollama LangChain connector — langchain-ollama"


def test_the_providers_tab_lists_every_package_once(qtbot) -> None:
    """
    Given  a service whose package list offers each pip package once, under its first provider
    When   the Online AI services tab is built
    Then   there is one box per package, and the provider sharing that package maps onto it
    """
    tab = _providers_tab(qtbot)

    assert list(tab.checkboxes) == ["provider:mistral", "provider:openai"]
    assert tab.key_for("mistral_eu") == "provider:mistral"


def test_the_providers_tab_reports_the_package_of_a_ticked_box(qtbot) -> None:
    """
    Given  the Online AI services tab
    When   the Mistral box is ticked
    Then   the tab asks for its pip package, and only that one
    """
    tab = _providers_tab(qtbot)

    tab.checkboxes["provider:mistral"].setChecked(True)

    assert tab.pip_packages() == ["langchain-mistralai"]


def test_the_name_detection_tab_keeps_spacy_and_its_models_apart(qtbot) -> None:
    """
    Given  the Name detection tab
    When   spaCy's box and the German model's box are ticked
    Then   spaCy is reported as a pip package and the model as a model to download
    """
    context = settings_context(RecommendationServiceStub())
    tab = NameDetectionTab(context.app_service)
    qtbot.addWidget(tab)

    tab.checkboxes["spacy"].setChecked(True)
    tab.checkboxes[f"{SPACY_PREFIX}de_core_news_sm"].setChecked(True)

    assert tab.pip_packages() == ["spacy"]
    assert tab.spacy_models() == ["de_core_news_sm"]


def test_a_deep_link_ticks_the_name_detection_model_it_names(qtbot) -> None:
    """
    Given  the Name detection tab
    When   a deep link names one model only, then a second one names spaCy itself
    Then   the model's box alone is ticked first, and spaCy's with the second link
    """
    context = settings_context(RecommendationServiceStub())
    tab = NameDetectionTab(context.app_service)
    qtbot.addWidget(tab)

    tab.preselect([f"{SPACY_PREFIX}de_core_news_sm"])
    assert _ticked(tab) == [f"{SPACY_PREFIX}de_core_news_sm"]

    tab.preselect([f"{PACKAGE_PREFIX}spacy"])
    assert sorted(_ticked(tab)) == sorted(["spacy", f"{SPACY_PREFIX}de_core_news_sm"])


def test_the_reading_models_tab_reports_the_files_a_deep_link_asked_for(qtbot) -> None:
    """
    Given  the Reading models tab
    When   a deep link asks for the reader, then for the page models
    Then   each box is ticked and reported, in the order the tab lists its items
    """
    context = settings_context(RecommendationServiceStub())
    tab = ReadingModelsTab(context.app_service)
    qtbot.addWidget(tab)

    tab.preselect(["reader:ppocr"])
    assert tab.checked_items() == [READER_ITEM]

    tab.preselect(["page_models:all"])
    assert tab.checked_items() == [READER_ITEM, PAGE_MODELS_ITEM]


def test_an_installed_file_says_included_or_downloaded_and_is_not_checked(qtbot) -> None:
    """
    Given  the Reading models tab and a check that finds both files on this computer, one of
           them carried by the app itself
    When   the tab is told what is installed
    Then   the rows say so, cannot be ticked, and nothing is reported for installation
    """
    context = settings_context(RecommendationServiceStub())
    tab = ReadingModelsTab(context.app_service)
    qtbot.addWidget(tab)
    tab.preselect(["reader:ppocr", "page_models:all"])

    tab.mark_installed(
        {
            "reader:ppocr": True,
            "page_models:all": True,
            "bundled:reader:ppocr": True,
            "bundled:page_models:all": False,
        }
    )

    assert tab.checkboxes[READER_ITEM].text().endswith(txt.INCLUDED)
    assert tab.checkboxes[PAGE_MODELS_ITEM].text().endswith(txt.READER_DOWNLOADED)
    assert _ticked(tab) == []
    assert tab.checked_items() == []


def test_an_installed_package_box_is_left_out_of_the_install(qtbot) -> None:
    """
    Given  the Online AI services tab with the Mistral box ticked
    When   the check reports that Mistral's package can already be imported
    Then   the box says so, is disabled and no longer part of what will be installed
    """
    tab = _providers_tab(qtbot)
    tab.checkboxes["provider:mistral"].setChecked(True)

    tab.mark_installed({"provider:mistral": True})

    assert not tab.checkboxes["provider:mistral"].isEnabled()
    assert "✓" in tab.checkboxes["provider:mistral"].text()
    assert tab.pip_packages() == []


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_the_ollama_tab_lists_the_catalog_and_marks_a_downloaded_model(qtbot) -> None:
    """
    Given  the AI models for this computer tab and a check that finds one model downloaded
    When   the tab is told what is installed
    Then   that row shows a tick in the Size column, cannot be ticked, and the others can
    """
    from teachers_teammate.domain.model_catalog import DEFAULT_OCR_MODEL  # noqa: PLC0415

    context = settings_context(RecommendationServiceStub())
    tab = OllamaModelsTab(
        app_service=context.app_service,
        hardware_setting="8gb",
        ollama_url="http://127.0.0.1:11434",
    )
    qtbot.addWidget(tab)

    assert DEFAULT_OCR_MODEL in tab.all_tags()

    tab.mark_installed(
        {f"ollama:{DEFAULT_OCR_MODEL}": True, f"size:{DEFAULT_OCR_MODEL}": 5_000_000_000}
    )

    item = tab._model_items[DEFAULT_OCR_MODEL]
    assert item.text(3).startswith("✓")
    assert tab.checked_models() == []
