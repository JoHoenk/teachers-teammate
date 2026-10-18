"""GUI tests for the shared OcrConfigSelector widget."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import html

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.domain.data_location import DataLocation, StageLocation
from teachers_teammate.domain.model_catalog import DEFAULT_OCR_MODEL
from teachers_teammate.gui._ocr_config_selector import OcrConfigSelector

from ._service_stubs import RecommendationServiceStub


class _DummyAppService(RecommendationServiceStub):
    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def list_ocr_engines(self) -> list[str]:
        return ["ollama", "tesseract", "paddleocr"]

    def default_preprocess_for_engine(self, engine: str) -> str:
        return {"ollama": "grayscale", "tesseract": "clahe"}.get(engine, "")

    def get_provider_info(self, provider: str) -> dict:
        return {
            "models": [f"{provider}-model"],
            "default_model": f"{provider}-model",
            "needs_api_key": False,
            "env_key": "",
        }

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        _ = base_url
        return [f"{provider}-model"]

    def list_ocr_models(self, base_url: str = "") -> list[str]:
        return [
            m for m in self.list_provider_models("ollama", base_url=base_url) if "cloud" not in m
        ]

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        return [f"{provider}-cached"]

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        _ = (provider, base_url)

    def is_module_importable(self, module: str) -> bool:
        _ = module
        return True


@pytest.fixture
def selector(qtbot) -> OcrConfigSelector:
    widget = OcrConfigSelector(app_service=_DummyAppService())
    qtbot.addWidget(widget)
    return widget


@pytest.mark.gui
def test_get_ocr_config_for_native_engine(selector) -> None:
    """
    Given  the selector set to a native engine (tesseract)
    When   get_ocr_config() is called
    Then   it returns an OcrConfig with that engine
    """
    selector._ocr_engine.setCurrentText("tesseract")
    ocr = selector.get_ocr_config()
    assert ocr.engine == "tesseract"


@pytest.mark.gui
def test_online_providers_are_not_offered_as_recognition_methods(selector) -> None:
    """
    Given  an app service that also lists online LLM providers (openai)
    When   the recognition-method choices are read
    Then   only engines that run on the user's computer are offered
    """
    engines = [selector._ocr_engine.itemText(i) for i in range(selector._ocr_engine.count())]
    assert engines == ["ollama", "tesseract", "paddleocr"]


@pytest.mark.gui
def test_load_ocr_config_round_trips(selector) -> None:
    """
    Given  an OcrConfig loaded into the selector
    When   get_ocr_config() is read back
    Then   the engine, preprocess method, and temperature are preserved
    """
    selector.load_ocr_config(
        OcrConfig(engine="tesseract", model="", preprocess_method="none", temperature=0.3)
    )
    ocr = selector.get_ocr_config()
    assert ocr.engine == "tesseract"
    assert ocr.preprocess_method == "none"
    assert ocr.temperature == 0.3


@pytest.mark.gui
@pytest.mark.use_case("Preview_Preprocessing")
def test_load_ocr_config_round_trips_preprocessing_fields(selector) -> None:
    """
    Given  an OcrConfig with pre-steps and a non-default PDF DPI loaded into the selector
    When   get_ocr_config() is read back
    Then   every preprocessing checkbox and the PDF DPI spin box round-trip
    """
    selector.load_ocr_config(
        OcrConfig(
            engine="tesseract",
            pdf_render_dpi=150,
            dewarp=True,
            deskew=False,
            border_crop=True,
            denoise=False,
            gamma=True,
        )
    )
    ocr = selector.get_ocr_config()
    assert ocr.pdf_render_dpi == 150
    assert ocr.dewarp is True
    assert ocr.deskew is False
    assert ocr.border_crop is True
    assert ocr.denoise is False
    assert ocr.gamma is True


@pytest.mark.gui
@pytest.mark.use_case("Preview_Preprocessing")
def test_preview_button_emits_full_ocr_config(selector, qtbot) -> None:
    """
    Given  the selector with deskew and border_crop checked (not just a preprocess method)
    When   the Preview… button is clicked
    Then   preprocess_preview_requested carries the complete OcrConfig, pre-steps included

    Regression guard: previously the signal emitted only the preprocess-method string, so
    the preview dialog silently ignored every checked pre-step checkbox.
    """
    from PySide6.QtWidgets import QPushButton  # noqa: PLC0415

    selector._deskew.setChecked(True)
    selector._border_crop.setChecked(True)

    preview_btn = next(
        w for w in selector.findChildren(QPushButton) if w.text().startswith("Preview")
    )
    with qtbot.waitSignal(selector.preprocess_preview_requested, timeout=1000) as blocker:
        preview_btn.click()

    (emitted,) = blocker.args
    assert isinstance(emitted, OcrConfig)
    assert emitted.deskew is True
    assert emitted.border_crop is True
    assert emitted == selector.get_ocr_config()


@pytest.mark.gui
def test_native_non_ollama_engine_hides_model_row(selector) -> None:
    """
    Given  the selector
    When   a non-ollama native engine is selected
    Then   the model row is hidden (tesseract/paddle take no model name)
    """
    selector._ocr_engine.setCurrentText("tesseract")
    assert selector._ocr_model_row.isHidden() is True
    selector._ocr_engine.setCurrentText("ollama")
    assert selector._ocr_model_row.isHidden() is False


@pytest.mark.gui
def test_prestep_checkboxes_disabled_under_preprocess_method_none(selector) -> None:
    """
    Given  the selector
    When   Image preparation is switched to "none" and then back to an active method
    Then   the pre-step checkboxes are disabled under "none" and re-enabled otherwise

    "none" skips every pre-step (HandwritingPreprocessor.preprocess returns the
    original file unchanged), so a checked box under it would silently have no effect.
    """
    checkboxes = [
        selector._dewarp,
        selector._deskew,
        selector._border_crop,
        selector._denoise,
        selector._gamma,
    ]
    selector._preprocess.setCurrentText("none")
    assert all(not cb.isEnabled() for cb in checkboxes)

    selector._preprocess.setCurrentText("grayscale")
    assert all(cb.isEnabled() for cb in checkboxes)


@pytest.mark.gui
def test_load_ocr_config_disables_prestep_checkboxes_for_method_none(selector) -> None:
    """
    Given  an OcrConfig with preprocess_method="none"
    When   load_ocr_config() populates the selector
    Then   the pre-step checkboxes are disabled
    """
    selector.load_ocr_config(OcrConfig(engine="tesseract", preprocess_method="none"))
    assert selector._preprocess.currentText() == "none"
    assert selector._deskew.isEnabled() is False


# ── Ollama model guidance (sorting, hints, use-best button) ────────────────


class _OllamaCatalogAppService(_DummyAppService):
    """Dummy service whose Ollama server has catalog models installed."""

    def __init__(self, models: list[str]) -> None:
        super().__init__()
        self._models = models

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        _ = base_url
        return list(self._models) if provider == "ollama" else [f"{provider}-model"]


_INSTALLED = [DEFAULT_OCR_MODEL, "qwen3-vl:8b", "glm-ocr:q8_0", "qwen3.5:9b", "mistral:latest"]


def _ollama_selector(
    qtbot, models: list[str] | None = None, open_downloads=None
) -> OcrConfigSelector:
    widget = OcrConfigSelector(
        app_service=_OllamaCatalogAppService(models if models is not None else _INSTALLED),
        hardware_profile="8gb",
        open_downloads=open_downloads,
    )
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget._hw_selector.resolved_changed, timeout=5000):
        pass
    widget.refresh()  # starts the (always threaded) Ollama model fetch
    installed = models if models is not None else _INSTALLED
    qtbot.waitUntil(lambda: widget._guidance._models == list(installed), timeout=5000)
    thread = widget._fetch_thread
    assert thread is not None
    thread.wait(5000)
    return widget


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_ollama_models_sorted_best_first_with_raw_tags(qtbot) -> None:
    """
    Given  the selector with a mixed set of installed Ollama models
    When   the model list is populated
    Then   the BEST reading model comes first and the combo text stays the raw tag (AC8)
    """
    selector = _ollama_selector(qtbot)
    combo = selector._ocr_model
    assert combo.itemText(0) == DEFAULT_OCR_MODEL
    # No decoration anywhere: raw tags only; GOOD models follow, sorted by name.
    assert [combo.itemText(i) for i in range(combo.count())] == [
        DEFAULT_OCR_MODEL,
        "glm-ocr:q8_0",
        "qwen3-vl:8b",
        "qwen3.5:9b",  # writing model that also reads scans → GOOD
        "mistral:latest",  # general model → WRONG_KIND, sorted last
    ]
    combo.setCurrentText("qwen3-vl:8b")
    assert selector.get_ocr_config().model == "qwen3-vl:8b"


@pytest.mark.use_case("Model_Category_Guidance")
def test_selecting_general_model_shows_wrong_kind_hint(qtbot) -> None:
    """
    Given  the selector with a general model installed
    When   the general model is selected for the reading task
    Then   the hint warns that the model cannot read scans (AC6)
    """
    selector = _ollama_selector(qtbot)
    selector._ocr_model.setCurrentText("mistral:latest")
    hint = selector._guidance._hint_lbl.text()
    assert "cannot read scans" in hint
    assert DEFAULT_OCR_MODEL in hint  # suggests a reading model example


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_use_best_choice_selects_installed_best_model(qtbot) -> None:
    """
    Given  the BEST reading model is installed
    When   the "Use best choice" button is clicked
    Then   the combo selects that model
    """
    selector = _ollama_selector(qtbot)
    selector._ocr_model.setCurrentText("glm-ocr:q8_0")
    selector._guidance._best_btn.click()
    assert selector._ocr_model.currentText() == DEFAULT_OCR_MODEL


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_download_best_choice_calls_open_downloads(qtbot) -> None:
    """
    Given  the BEST reading model is not installed and a callback is injected
    When   the "Download best choice…" button is clicked
    Then   the callback receives the BEST model tag
    """
    received: list[list[str]] = []

    def open_downloads(tags: list[str], parent) -> str | None:
        _ = parent
        received.append(tags)
        return None

    selector = _ollama_selector(
        qtbot, models=["glm-ocr:q8_0", "qwen3.5:9b"], open_downloads=open_downloads
    )
    selector._guidance._best_btn.click()
    assert received == [[DEFAULT_OCR_MODEL]]


@pytest.mark.use_case("Model_Category_Guidance")
def test_non_ollama_engines_have_no_hint_and_button(qtbot) -> None:
    """
    Given  the selector set to tesseract or paddleocr
    When   the engine is switched
    Then   no hint line and no "Use best choice" button are shown
    """
    selector = _ollama_selector(qtbot)
    selector._ocr_engine.setCurrentText("tesseract")
    assert selector._guidance.isHidden() is True
    selector._ocr_engine.setCurrentText("paddleocr")
    assert selector._guidance.isHidden() is True
    selector._ocr_engine.setCurrentText("ollama")
    assert selector._guidance.isHidden() is False


@pytest.mark.use_case("Model_Category_Guidance")
def test_ollama_cloud_models_are_not_offered_for_reading_pages(qtbot) -> None:
    """
    Given  an Ollama server that also lists a cloud model (processed on ollama.com)
    When   the reading-model list is populated
    Then   the cloud model is left out, so pages cannot be sent there by accident
    """
    models = ["qwen3-vl:8b", "qwen3-vl:235b-cloud"]
    selector = OcrConfigSelector(
        app_service=_OllamaCatalogAppService(models), hardware_profile="8gb"
    )
    qtbot.addWidget(selector)
    with qtbot.waitSignal(selector._hw_selector.resolved_changed, timeout=5000):
        pass
    selector.refresh()
    qtbot.waitUntil(lambda: selector._guidance._models == ["qwen3-vl:8b"], timeout=5000)
    thread = selector._fetch_thread
    assert thread is not None
    thread.wait(5000)

    combo = selector._ocr_model
    assert [combo.itemText(i) for i in range(combo.count())] == ["qwen3-vl:8b"]


class _LocationAppService(_DummyAppService):
    """Service reporting a chosen place for text recognition."""

    def __init__(self, location: StageLocation) -> None:
        super().__init__()
        self._location = location

    def describe_ocr_location(self, engine: str, model: str, ollama_url: str) -> StageLocation:
        _ = engine, model, ollama_url
        return self._location

    def describe_ocr_refusal(self, engine: str, model: str, ollama_url: str) -> str:
        _ = engine, model, ollama_url
        if self._location.location is DataLocation.ONLINE:
            return "The model runs on Ollama's servers, not on your computer."
        return ""


@pytest.mark.gui
@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (StageLocation(DataLocation.THIS_COMPUTER, "Ollama"), "pages never leave it"),
        (
            StageLocation(DataLocation.OTHER_COMPUTER, "Ollama", "school-server"),
            "pages are sent there",
        ),
    ],
)
def test_selector_says_where_pages_are_read(qtbot, location, expected) -> None:
    """
    Given  the selector and a service that reports where the engine runs
    When   the hint is rendered
    Then   it names the place and what that means for the pages
    """
    widget = OcrConfigSelector(app_service=_LocationAppService(location))
    qtbot.addWidget(widget)

    text = widget._where_hint.text()

    assert location.label in text
    assert expected in text


@pytest.mark.gui
def test_selector_refuses_a_hosted_ollama_server_even_for_a_plain_model(qtbot) -> None:
    """
    Given  a service that places the engine online because the server is Ollama's hosted API
    When   the selector shows a model without a cloud tag
    Then   the hint says it is not allowed, as it does for cloud models
    """
    widget = OcrConfigSelector(
        app_service=_LocationAppService(StageLocation(DataLocation.ONLINE, "Ollama cloud"))
    )
    qtbot.addWidget(widget)

    widget._ocr_model.setCurrentText("qwen3-vl:8b")

    assert "not allowed" in html.unescape(widget._where_hint.text())


@pytest.mark.gui
def test_selector_warns_when_a_cloud_model_is_typed_in(qtbot) -> None:
    """
    Given  the selector with Ollama chosen
    When   an Ollama cloud model is typed into the model box
    Then   the hint says it is not allowed and its tooltip explains the model runs on
           Ollama's servers
    """
    widget = OcrConfigSelector(
        app_service=_LocationAppService(StageLocation(DataLocation.ONLINE, "Ollama cloud"))
    )
    qtbot.addWidget(widget)

    widget._ocr_model.setCurrentText("qwen3-vl:235b-cloud")

    assert "not allowed" in html.unescape(widget._where_hint.text())
    assert "Ollama's servers" in widget._where_hint.toolTip()
