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
