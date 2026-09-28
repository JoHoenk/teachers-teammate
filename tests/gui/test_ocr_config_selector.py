"""GUI tests for the shared OcrConfigSelector widget."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.gui._ocr_config_selector import OcrConfigSelector


class _DummyAppService:
    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def list_ocr_engines(self) -> list[str]:
        return ["ollama", "tesseract", "paddleocr", "langchain"]

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
    Then   it returns an OcrConfig with that engine and no langchain provider
    """
    selector._ocr_engine.setCurrentText("tesseract")
    ocr = selector.get_ocr_config()
    assert ocr.engine == "tesseract"
    assert ocr.provider == ""


@pytest.mark.gui
def test_get_ocr_config_for_provider_engine_uses_langchain(selector) -> None:
    """
    Given  the selector set to a provider engine (openai)
    When   get_ocr_config() is called
    Then   the engine is 'langchain' and provider records the chosen provider
    """
    selector._ocr_engine.setCurrentText("openai")
    ocr = selector.get_ocr_config()
    assert ocr.engine == "langchain"
    assert ocr.provider == "openai"


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
