"""GUI tests for settings dialog behavior and value serialization."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from teachers_teammate.config import DEFAULTS
from teachers_teammate.gui._settings_dialog import (
    CorrectionSettingsDialog,
    EvaluationSettingsDialog,
    OCRSettingsDialog,
    OutputSettingsDialog,
    SettingsDialog,
)


class _DummyAppService:
    """Minimal app-service stub for settings dialog tests."""

    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def list_ocr_engines(self) -> list[str]:
        return ["ollama", "tesseract", "paddleocr", "langchain"]

    def default_preprocess_for_engine(self, engine: str) -> str:
        return {
            "ollama": "grayscale",
            "tesseract": "clahe",
            "paddleocr": "grayscale",
            "langchain": "grayscale",
        }.get(engine, "")

    def get_provider_info(self, provider: str) -> dict:
        if provider == "openai":
            return {
                "models": ["gpt-4o", "gpt-4o-mini"],
                "default_model": "gpt-4o-mini",
                "needs_api_key": True,
                "env_key": "OPENAI_API_KEY",
            }
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
        return None

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        pass

    def is_module_importable(self, module: str) -> bool:
        return True


class _CachingDummyAppService(_DummyAppService):
    def __init__(self) -> None:
        self._cache: dict[tuple, list[str]] = {}

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        return self._cache.get((provider, base_url))

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        self._cache.pop((provider, base_url), None)

    def seed_cache(self, provider: str, models: list[str], base_url: str = "") -> None:
        self._cache[(provider, base_url)] = models


# ── SettingsDialog (Connections & Credentials) ─────────────────────────────────


@pytest.fixture
def settings_dialog(qtbot) -> SettingsDialog:
    dlg = SettingsDialog({}, app_service=_DummyAppService())
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


@pytest.mark.gui
def test_settings_dialog_is_connections_only(settings_dialog) -> None:
    """
    Given  the SettingsDialog (now Connections & Credentials)
    When   it is created
    Then   it has an Ollama URL field and a credentials table (no tab widget)
    """
    assert hasattr(settings_dialog, "_ollama_url")
    assert hasattr(settings_dialog, "_cred_table")
    assert not hasattr(settings_dialog, "_ocr_engine")
    assert not hasattr(settings_dialog, "_correction_provider")
    assert not hasattr(settings_dialog, "_evaluation_provider")


@pytest.mark.gui
def test_settings_dialog_no_test_connection_buttons(settings_dialog) -> None:
    """
    Given  a SettingsDialog
    When   it is created
    Then   the old 'Test Connection' buttons are not present
    """
    assert not hasattr(settings_dialog, "_ocr_check_btn")
    assert not hasattr(settings_dialog, "_corr_check_btn")
    assert not hasattr(settings_dialog, "_eval_check_btn")


@pytest.mark.gui
def test_settings_dialog_pull_model_in_connections(settings_dialog) -> None:
    """
    Given  a SettingsDialog
    When   it is created
    Then   the 'Pull Model' button is present
    """
    assert hasattr(settings_dialog, "_pull_model_btn")
    assert settings_dialog._pull_model_btn is not None


@pytest.mark.gui
def test_settings_dialog_get_values_returns_ollama_url(settings_dialog) -> None:
    """
    Given  a SettingsDialog with a custom Ollama URL
    When   get_values() is called
    Then   it returns a dict containing only the ollama_url key
    """
    settings_dialog._ollama_url.setText("http://custom:11434")
    values = settings_dialog.get_values()
    assert "ollama_url" in values
    assert values["ollama_url"] == "http://custom:11434"
    assert "ocr_engine" not in values
    assert "correction_provider" not in values


@pytest.mark.gui
def test_settings_dialog_get_values_applies_default_url(settings_dialog) -> None:
    """
    Given  a SettingsDialog with a blank Ollama URL field
    When   get_values() is called
    Then   the default Ollama URL is returned
    """
    settings_dialog._ollama_url.setText("")
    assert settings_dialog.get_values()["ollama_url"] == DEFAULTS["ollama_url"]


# ── Credentials panel ──────────────────────────────────────────────────────────


def _find_cred_field(dlg: SettingsDialog, env_key: str):
    from PySide6.QtWidgets import QComboBox, QLineEdit  # noqa: PLC0415

    for row in range(dlg._cred_table.rowCount()):
        combo = dlg._cred_table.cellWidget(row, 0)
        if isinstance(combo, QComboBox):
            data = combo.currentData()
            if isinstance(data, tuple) and len(data) == 2 and data[1] == env_key:
                widget = dlg._cred_table.cellWidget(row, 1)
                return widget if isinstance(widget, QLineEdit) else None
    return None


@pytest.mark.gui
def test_settings_dialog_credentials_panel_has_row_for_keyed_provider(qtbot, monkeypatch) -> None:
    """
    Given  a SettingsDialog where one provider declares needs_api_key=True and env_key
           and that env_key is already set in the environment
    When   the dialog is created
    Then   a credential input field exists for that provider's env_key
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-pre-existing")
    dlg = SettingsDialog({}, app_service=_DummyAppService())
    qtbot.addWidget(dlg)
    assert _find_cred_field(dlg, "OPENAI_API_KEY") is not None


@pytest.mark.gui
def test_settings_dialog_save_api_key_sets_env_and_qsettings(qtbot, monkeypatch) -> None:
    """
    Given  a SettingsDialog with a credentials row for OPENAI_API_KEY
    When   _apply_credentials is called after entering a key value
    Then   os.environ is updated with the key and QSettings.setValue is called
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old")
    dlg = SettingsDialog({}, app_service=_DummyAppService())
    qtbot.addWidget(dlg)

    field = _find_cred_field(dlg, "OPENAI_API_KEY")
    if field is None:
        pytest.skip("No OPENAI_API_KEY field in test service")

    field.setText("sk-test-key")
    mock_qs = MagicMock()
    mock_qs.beginGroup = MagicMock()
    mock_qs.endGroup = MagicMock()
    mock_qs.childKeys = MagicMock(return_value=[])
    monkeypatch.setattr("teachers_teammate.gui._settings_dialog.QSettings", lambda *_: mock_qs)
    dlg._apply_credentials()

    mock_qs.setValue.assert_called_with("credentials/OPENAI_API_KEY", "sk-test-key")
    assert os.environ.get("OPENAI_API_KEY") == "sk-test-key"


@pytest.mark.gui
def test_settings_dialog_clear_api_key_removes_from_env(qtbot, monkeypatch) -> None:
    """
    Given  an env key is set and tracked in QSettings
    When   _apply_credentials is called with an empty field for that key
    Then   os.environ no longer contains the key
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old")
    dlg = SettingsDialog({}, app_service=_DummyAppService())
    qtbot.addWidget(dlg)

    field = _find_cred_field(dlg, "OPENAI_API_KEY")
    if field is None:
        pytest.skip("No OPENAI_API_KEY field in test service")

    field.setText("")
    mock_qs = MagicMock()
    mock_qs.beginGroup = MagicMock()
    mock_qs.endGroup = MagicMock()
    mock_qs.childKeys = MagicMock(return_value=["OPENAI_API_KEY"])
    monkeypatch.setattr("teachers_teammate.gui._settings_dialog.QSettings", lambda *_: mock_qs)
    dlg._apply_credentials()

    assert "OPENAI_API_KEY" not in os.environ
    mock_qs.remove.assert_called_with("credentials/OPENAI_API_KEY")


# ── OCRSettingsDialog ──────────────────────────────────────────────────────────


@pytest.fixture
def ocr_dialog(qtbot) -> OCRSettingsDialog:
    dlg = OCRSettingsDialog(
        {"ocr_engine": "tesseract"},
        app_service=_DummyAppService(),
    )
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


@pytest.mark.gui
def test_ocr_settings_dialog_engine_accessible(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog
    When   it is created
    Then   the OCR engine combo box exists and is populated
    """
    assert ocr_dialog._ocr_engine is not None
    assert ocr_dialog._ocr_engine.count() > 0


@pytest.mark.gui
def test_ocr_settings_dialog_engine_sets_preprocess_default(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog
    When   the OCR engine changes
    Then   a sensible engine-specific preprocessing default is applied
    """
    ocr_dialog._on_engine_changed("ollama")
    assert ocr_dialog._preprocess.currentText() == "grayscale"

    ocr_dialog._on_engine_changed("tesseract")
    assert ocr_dialog._preprocess.currentText() == "clahe"


@pytest.mark.gui
def test_ocr_settings_dialog_get_values_returns_dict(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog
    When   get_values() is called
    Then   it returns the expected OCR-specific keys
    """
    values = ocr_dialog.get_values()
    assert "ocr_engine" in values
    assert "ocr_model" in values
    assert "preprocess_method" in values
    assert "debug" in values
    assert "correction_provider" not in values


@pytest.mark.gui
def test_ocr_settings_dialog_on_models_fetched_updates_combo(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog
    When   _on_models_fetched is called with a non-empty list
    Then   the OCR model combo is updated and status shows green
    """
    ocr_dialog._on_models_fetched(["llama3:latest", "mistral:7b"])

    items = [ocr_dialog._ocr_model.itemText(i) for i in range(ocr_dialog._ocr_model.count())]
    assert "llama3:latest" in items
    assert "#27ae60" in ocr_dialog._ocr_status_lbl.text()


@pytest.mark.gui
def test_ocr_settings_dialog_status_red_on_empty_fetch(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog
    When   _on_models_fetched is called with an empty list
    Then   the status label shows a red error indicator
    """
    ocr_dialog._on_models_fetched([])
    assert "#c0392b" in ocr_dialog._ocr_status_lbl.text()


# ── CorrectionSettingsDialog ───────────────────────────────────────────────────


@pytest.fixture
def correction_dialog(qtbot) -> CorrectionSettingsDialog:
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "ollama", "correction_model": ""},
        prompt="Fix spelling.",
        app_service=_DummyAppService(),
    )
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


@pytest.mark.gui
def test_correction_settings_dialog_shows_prompt(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog with an initial prompt
    When   it is created
    Then   the prompt text area contains the provided prompt
    """
    assert correction_dialog._prompt_edit.toPlainText() == "Fix spelling."


@pytest.mark.gui
def test_correction_settings_dialog_get_prompt_returns_text(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog
    When   get_prompt() is called after editing the prompt
    Then   the updated prompt text is returned
    """
    correction_dialog._prompt_edit.setPlainText("My custom prompt.")
    assert correction_dialog.get_prompt() == "My custom prompt."


@pytest.mark.gui
def test_correction_settings_dialog_get_values_returns_provider_model(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog
    When   get_values() is called
    Then   it returns correction_provider and correction_model keys
    """
    values = correction_dialog.get_values()
    assert "correction_provider" in values
    assert "correction_model" in values
    assert "ocr_engine" not in values
    assert "evaluate_provider" not in values


@pytest.mark.gui
def test_correction_settings_dialog_on_provider_changed_seeds_model(
    correction_dialog, monkeypatch
) -> None:
    """
    Given  a CorrectionSettingsDialog
    When   the provider changes
    Then   the model combo is seeded with provider models
    """
    monkeypatch.setattr(
        correction_dialog._app_service,
        "get_provider_info",
        lambda p: {
            "models": [f"{p}-model-1", f"{p}-model-2"],
            "default_model": f"{p}-model-1",
            "needs_api_key": False,
            "env_key": "",
        },
    )
    correction_dialog._on_provider_changed("openai")
    items = [
        correction_dialog._correction_model.itemText(i)
        for i in range(correction_dialog._correction_model.count())
    ]
    assert "openai-model-1" in items


@pytest.mark.gui
def test_correction_settings_dialog_on_models_fetched_updates_combo(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog
    When   _on_models_fetched is called with a list
    Then   the correction model combo is updated and status shows green
    """
    correction_dialog._on_models_fetched(["gpt-4o", "gpt-3.5-turbo"])
    items = [
        correction_dialog._correction_model.itemText(i)
        for i in range(correction_dialog._correction_model.count())
    ]
    assert "gpt-4o" in items
    assert "#27ae60" in correction_dialog._corr_status_lbl.text()


@pytest.mark.gui
def test_correction_settings_dialog_status_red_on_empty_fetch(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog
    When   _on_models_fetched is called with an empty list
    Then   the status label shows a red error indicator
    """
    correction_dialog._on_models_fetched([])
    assert "#c0392b" in correction_dialog._corr_status_lbl.text()


@pytest.mark.gui
def test_correction_settings_dialog_cache_hit_skips_new_thread(qtbot) -> None:
    """
    Given  models are already cached for the current correction provider (ollama)
    When   _auto_fetch is called
    Then   the cached list is used (no new thread is started)
    """
    svc = _CachingDummyAppService()
    url = DEFAULTS["ollama_url"]
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "ollama", "ollama_url": url},
        prompt="",
        app_service=svc,
    )
    qtbot.addWidget(dlg)
    svc.seed_cache("ollama", ["llama3:latest"], url)
    old_thread = dlg._corr_fetch_thread
    dlg._auto_fetch()
    assert dlg._corr_fetch_thread is old_thread


@pytest.mark.gui
def test_correction_settings_dialog_provider_change_triggers_cache_invalidation(qtbot) -> None:
    """
    Given  a provider's models are cached
    When   _on_provider_changed is called for that provider
    Then   the cache entry is invalidated
    """
    svc = _CachingDummyAppService()
    svc.seed_cache("ollama", ["old-model"], DEFAULTS["ollama_url"])
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "ollama"},
        prompt="",
        app_service=svc,
    )
    qtbot.addWidget(dlg)

    dlg._on_provider_changed("ollama")

    assert svc.get_cached_models("ollama", base_url=DEFAULTS["ollama_url"]) is None


# ── EvaluationSettingsDialog ───────────────────────────────────────────────────


@pytest.fixture
def evaluation_dialog(qtbot) -> EvaluationSettingsDialog:
    dlg = EvaluationSettingsDialog(
        {"evaluate_provider": "ollama", "evaluate_model": ""},
        prompt="Evaluate quality.",
        app_service=_DummyAppService(),
    )
    qtbot.addWidget(dlg)
    dlg.show()
    return dlg


@pytest.mark.gui
def test_evaluation_settings_dialog_shows_prompt(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog with an initial prompt
    When   it is created
    Then   the prompt text area contains the provided prompt
    """
    assert evaluation_dialog._prompt_edit.toPlainText() == "Evaluate quality."


@pytest.mark.gui
def test_evaluation_settings_dialog_get_values_returns_provider_model(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog
    When   get_values() is called
    Then   it returns evaluate_provider and evaluate_model keys
    """
    values = evaluation_dialog.get_values()
    assert "evaluate_provider" in values
    assert "evaluate_model" in values
    assert "correction_provider" not in values


@pytest.mark.gui
def test_evaluation_settings_dialog_on_provider_changed_seeds_model(
    evaluation_dialog, monkeypatch
) -> None:
    """
    Given  an EvaluationSettingsDialog
    When   the provider changes
    Then   the model combo is seeded with provider models
    """
    monkeypatch.setattr(
        evaluation_dialog._app_service,
        "get_provider_info",
        lambda p: {
            "models": [f"{p}-eval-1"],
            "default_model": f"{p}-eval-1",
            "needs_api_key": False,
            "env_key": "",
        },
    )
    evaluation_dialog._on_provider_changed("openai")
    items = [
        evaluation_dialog._evaluation_model.itemText(i)
        for i in range(evaluation_dialog._evaluation_model.count())
    ]
    assert "openai-eval-1" in items


@pytest.mark.gui
def test_evaluation_settings_dialog_on_models_fetched_updates_combo(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog
    When   _on_models_fetched is called with a list
    Then   the evaluation model combo is updated and status shows green
    """
    evaluation_dialog._on_models_fetched(["claude-3-haiku", "claude-3-opus"])
    items = [
        evaluation_dialog._evaluation_model.itemText(i)
        for i in range(evaluation_dialog._evaluation_model.count())
    ]
    assert "claude-3-haiku" in items
    assert "#27ae60" in evaluation_dialog._eval_status_lbl.text()


@pytest.mark.gui
def test_evaluation_settings_dialog_status_red_on_empty_fetch(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog
    When   _on_models_fetched is called with an empty list
    Then   the evaluation status label shows a red error indicator
    """
    evaluation_dialog._on_models_fetched([])
    assert "#c0392b" in evaluation_dialog._eval_status_lbl.text()


# ── OCR engine/provider visibility ─────────────────────────────────────────────


@pytest.mark.gui
def test_ocr_settings_dialog_visibility_reacts_to_engine(qtbot, monkeypatch) -> None:
    """
    Given  an OCRSettingsDialog with OPENAI_API_KEY available
    When   engine is set to tesseract (native, no model row)
    Then   the model row is hidden
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    dlg = OCRSettingsDialog({}, app_service=_DummyAppService())
    qtbot.addWidget(dlg)

    dlg._ocr_engine.setCurrentText("tesseract")
    assert dlg._ocr_model_row.isHidden() is True

    dlg._ocr_engine.setCurrentText("ollama")
    assert dlg._ocr_model_row.isHidden() is False


# ── Output settings dialog ─────────────────────────────────────────────────────


@pytest.mark.gui
def test_settings_dialog_docx_visibility_follows_checkbox(qtbot) -> None:
    """
    Given  an OutputSettingsDialog with DOCX options visible
    When   DOCX generation is disabled
    Then   DOCX format controls are hidden
    """
    dlg = OutputSettingsDialog({"docx_enabled": True, "docx_format": "table"})
    qtbot.addWidget(dlg)

    dlg._docx_enabled.setChecked(False)

    assert dlg._docx_format.isHidden() is True
    assert dlg._docx_format_label.isHidden() is True


@pytest.mark.gui
def test_output_settings_dialog_round_trips_output_dir(qtbot) -> None:
    """
    Given  an OutputSettingsDialog seeded with an output_dir value
    When   get_values() is called
    Then   the same output_dir is returned
    """
    dlg = OutputSettingsDialog({"output_dir": "/tmp/reports"})
    qtbot.addWidget(dlg)

    assert dlg._output_dir.text() == "/tmp/reports"
    assert dlg.get_values()["output_dir"] == "/tmp/reports"


@pytest.mark.gui
def test_downloads_dialog_has_one_tab_per_section(qtbot) -> None:
    """
    Given  the standalone DownloadsDialog
    When   it is constructed
    Then   it exposes one tab per downloadable section
    """
    from PySide6.QtWidgets import QTabWidget  # noqa: PLC0415

    from teachers_teammate.gui._downloads_dialog import DownloadsDialog  # noqa: PLC0415

    dlg = DownloadsDialog(ollama_url="http://127.0.0.1:11434")
    qtbot.addWidget(dlg)

    tabs = dlg.findChild(QTabWidget)
    labels = [tabs.tabText(i) for i in range(tabs.count())]
    assert labels == [
        "OCR Engines",
        "LLM Providers",
        "Ollama Models",
        "spaCy",
        "GPU Add-ons",
    ]


# ── Bug 2: model dropdowns list real models and pre-select the default ────


@pytest.mark.gui
def test_correction_on_models_fetched_has_no_placeholder(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog
    When   _on_models_fetched is called with a list of models
    Then   the combo lists only real models, with no default placeholder row
    """
    correction_dialog._on_models_fetched(["gpt-4o", "gpt-3.5"])
    items = [
        correction_dialog._correction_model.itemText(i)
        for i in range(correction_dialog._correction_model.count())
    ]
    assert "\u2014 default \u2014" not in items
    assert items[0] == "gpt-4o"


@pytest.mark.gui
def test_correction_on_provider_changed_preselects_default_model(
    correction_dialog, monkeypatch
) -> None:
    """
    Given  a CorrectionSettingsDialog with no prior model selection
    When   the provider changes
    Then   the provider's default model is pre-selected in the combo
    """
    monkeypatch.setattr(
        correction_dialog._app_service,
        "get_provider_info",
        lambda p: {
            "models": [f"{p}-model-1", f"{p}-model-2"],
            "default_model": f"{p}-model-2",
            "needs_api_key": False,
            "env_key": "",
        },
    )
    correction_dialog._correction_model.setCurrentText("")
    correction_dialog._on_provider_changed("openai")
    assert correction_dialog._correction_model.currentText() == "openai-model-2"


@pytest.mark.gui
def test_correction_get_values_returns_selected_model(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog with the first model selected
    When   get_values() is called
    Then   the selected model name is returned (no placeholder-to-empty mapping)
    """
    correction_dialog._on_models_fetched(["gpt-4o"])
    correction_dialog._correction_model.setCurrentIndex(0)
    values = correction_dialog.get_values()
    assert values["correction_model"] == "gpt-4o"


@pytest.mark.gui
def test_correction_get_values_preserves_explicit_model(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog with an explicit model selected
    When   get_values() is called
    Then   the selected model name is returned unchanged
    """
    correction_dialog._on_models_fetched(["gpt-4o", "gpt-3.5"])
    correction_dialog._correction_model.setCurrentText("gpt-4o")
    values = correction_dialog.get_values()
    assert values["correction_model"] == "gpt-4o"


@pytest.mark.gui
def test_evaluation_on_models_fetched_has_no_placeholder(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog
    When   _on_models_fetched is called with a list of models
    Then   the combo lists only real models, with no default placeholder row
    """
    evaluation_dialog._on_models_fetched(["claude-3-haiku"])
    items = [
        evaluation_dialog._evaluation_model.itemText(i)
        for i in range(evaluation_dialog._evaluation_model.count())
    ]
    assert "\u2014 default \u2014" not in items
    assert items[0] == "claude-3-haiku"


@pytest.mark.gui
def test_evaluation_get_values_returns_selected_model(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog with the first model selected
    When   get_values() is called
    Then   the selected model name is returned (no placeholder-to-empty mapping)
    """
    evaluation_dialog._on_models_fetched(["claude-3-haiku"])
    evaluation_dialog._evaluation_model.setCurrentIndex(0)
    values = evaluation_dialog.get_values()
    assert values["evaluate_model"] == "claude-3-haiku"


# ── Bug 3e: temperature spinboxes in all 3 dialogs ────────────────────────


@pytest.mark.gui
def test_ocr_settings_dialog_has_temperature_spinbox(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog
    When   it is created
    Then   it exposes an ocr_temperature spinbox with a sensible default
    """
    assert hasattr(ocr_dialog, "_ocr_temperature")
    assert ocr_dialog._ocr_temperature.value() == DEFAULTS["ocr_temperature"]


@pytest.mark.gui
def test_ocr_settings_dialog_get_values_includes_temperature(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog with temperature set to 0.5
    When   get_values() is called
    Then   the returned dict contains ocr_temperature=0.5
    """
    ocr_dialog._ocr_temperature.setValue(0.5)
    values = ocr_dialog.get_values()
    assert "ocr_temperature" in values
    assert values["ocr_temperature"] == 0.5


@pytest.mark.gui
def test_correction_settings_dialog_has_temperature_spinbox(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog
    When   it is created
    Then   it exposes a _corr_temperature spinbox with a sensible default
    """
    assert hasattr(correction_dialog, "_corr_temperature")
    assert correction_dialog._corr_temperature.value() == DEFAULTS["correction_temperature"]


@pytest.mark.gui
def test_correction_settings_dialog_get_values_includes_temperature(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog with temperature set to 0.3
    When   get_values() is called
    Then   the returned dict contains correction_temperature=0.3
    """
    correction_dialog._corr_temperature.setValue(0.3)
    values = correction_dialog.get_values()
    assert "correction_temperature" in values
    assert values["correction_temperature"] == pytest.approx(0.3)


@pytest.mark.gui
def test_evaluation_settings_dialog_has_temperature_spinbox(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog
    When   it is created
    Then   it exposes an _eval_temperature spinbox with a sensible default
    """
    assert hasattr(evaluation_dialog, "_eval_temperature")
    assert evaluation_dialog._eval_temperature.value() == DEFAULTS["evaluate_temperature"]


@pytest.mark.gui
def test_evaluation_settings_dialog_get_values_includes_temperature(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog with temperature set to 1.0
    When   get_values() is called
    Then   the returned dict contains evaluate_temperature=1.0
    """
    evaluation_dialog._eval_temperature.setValue(1.0)
    values = evaluation_dialog.get_values()
    assert "evaluate_temperature" in values
    assert values["evaluate_temperature"] == pytest.approx(1.0)
