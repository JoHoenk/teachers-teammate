"""GUI tests for settings dialog behavior and value serialization."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock

from PySide6.QtCore import Qt
import pytest

from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.gui import _settings_dialog
from teachers_teammate.gui._settings_dialog import (
    CorrectionSettingsDialog,
    EvaluationSettingsDialog,
    OCRSettingsDialog,
    OutputSettingsDialog,
    SettingsDialog,
)

from ._service_stubs import RecommendationServiceStub


class _DummyAppService(RecommendationServiceStub):
    """Minimal app-service stub for settings dialog tests."""

    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def list_ocr_engines(self) -> list[str]:
        return ["ollama", "tesseract", "paddleocr"]

    def default_preprocess_for_engine(self, engine: str) -> str:
        return {
            "ollama": "grayscale",
            "tesseract": "clahe",
            "paddleocr": "grayscale",
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

    def list_ocr_models(self, base_url: str = "") -> list[str]:
        return self.list_provider_models("ollama", base_url=base_url)

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        return None

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        pass

    def is_module_importable(self, module: str) -> bool:
        return True


class _CachingDummyAppService(_DummyAppService):
    def __init__(self) -> None:
        super().__init__()
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
    assert hasattr(settings_dialog, "_cred_rows")
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
    return dlg._credential_field(env_key)


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


def _credentials_dialog(qtbot, monkeypatch) -> SettingsDialog:
    for name in ("OPENAI_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("teachers_teammate.gui._settings_dialog.QSettings", _EmptySettings)
    dlg = SettingsDialog({}, app_service=_ProviderAppService())
    qtbot.addWidget(dlg)
    dlg.resize(520, 700)
    dlg.show()
    return dlg


class _EmptySettings:
    """QSettings stand-in with nothing stored, so the dialog starts without key rows."""

    def __init__(self, *_args) -> None:
        pass

    def beginGroup(self, _name: str) -> None:
        pass

    def endGroup(self) -> None:
        pass

    def childKeys(self) -> list[str]:
        return []

    def value(self, _key: str, default: object = None) -> object:
        return default

    def setValue(self, _key: str, _value: object) -> None:
        pass

    def remove(self, _key: str) -> None:
        pass


@pytest.mark.gui
def test_adding_a_key_row_gives_a_field_wide_enough_to_paste_into(qtbot, monkeypatch) -> None:
    """
    Given  a Connections dialog with no key saved yet
    When   a key row is added for Mistral
    Then   the field is wide (the old table squeezed it to a sliver), the service is named in
           full above it, and the Remove button is not cut off
    """
    dlg = _credentials_dialog(qtbot, monkeypatch)
    dlg._cred_add_combo.setCurrentIndex(dlg._cred_add_combo.findData("MISTRAL_API_KEY"))

    dlg._cred_add_btn.click()

    row = dlg._cred_rows[0]
    assert row.field.width() >= 200
    assert row.field.placeholderText() == "Paste your API key here"
    labels = " ".join(label.text() for label in row.findChildren(type(dlg._ollama_url_label)))
    assert "Mistral · Mistral (EU)" in labels
    remove = next(b for b in row.findChildren(_settings_dialog.QPushButton) if b.text() == "Remove")
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
    dlg = _credentials_dialog(qtbot, monkeypatch)
    keys = [dlg._cred_add_combo.itemData(i) for i in range(dlg._cred_add_combo.count())]
    assert keys.count("MISTRAL_API_KEY") == 1

    dlg._cred_add_combo.setCurrentIndex(keys.index("MISTRAL_API_KEY"))
    dlg._cred_add_btn.click()

    assert [row.env_key for row in dlg._cred_rows] == ["MISTRAL_API_KEY"]
    assert dlg._cred_add_combo.findData("MISTRAL_API_KEY") < 0

    dlg._remove_cred_row(dlg._cred_rows[0])

    assert dlg._cred_rows == []
    assert dlg._cred_add_combo.findData("MISTRAL_API_KEY") >= 0


@pytest.mark.gui
def test_the_key_can_be_shown_to_check_what_was_pasted(qtbot, monkeypatch) -> None:
    """
    Given  a key row with a pasted key
    When   Show is pressed and then Hide
    Then   the key is readable while shown and masked again afterwards
    """
    dlg = _credentials_dialog(qtbot, monkeypatch)
    dlg._cred_add_btn.click()
    row = dlg._cred_rows[0]
    row.field.setText("sk-abc")
    assert row.field.echoMode() == _settings_dialog.QLineEdit.EchoMode.Password

    row._reveal.click()
    assert row.field.echoMode() == _settings_dialog.QLineEdit.EchoMode.Normal
    assert row._reveal.text() == "Hide"

    row._reveal.click()
    assert row.field.echoMode() == _settings_dialog.QLineEdit.EchoMode.Password
    assert row._reveal.text() == "Show"


@pytest.mark.gui
def test_the_picker_disappears_when_every_service_has_a_key(qtbot, monkeypatch) -> None:
    """
    Given  a Connections dialog
    When   a row is added for every service that needs a key
    Then   the picker is hidden (there is nothing left to add)
    """
    dlg = _credentials_dialog(qtbot, monkeypatch)
    while dlg._cred_add_combo.count():
        dlg._cred_add_btn.click()

    assert dlg._cred_add_row.isHidden()


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
    Then   the OCR config selector exists and has engine combo populated
    """
    assert ocr_dialog._ocr_config_selector is not None
    assert ocr_dialog._ocr_config_selector._ocr_engine is not None
    assert ocr_dialog._ocr_config_selector._ocr_engine.count() > 0


@pytest.mark.gui
def test_ocr_settings_dialog_has_preprocessing_options(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog
    When   it is created
    Then   it has all the preprocessing options available in the selector
    """
    selector = ocr_dialog._ocr_config_selector
    # Check that all preprocessing checkboxes exist
    assert selector._dewarp is not None
    assert selector._deskew is not None
    assert selector._border_crop is not None
    assert selector._denoise is not None
    assert selector._gamma is not None
    # Check that PDF DPI spinner exists
    assert selector._pdf_dpi is not None


@pytest.mark.gui
def test_ocr_settings_dialog_get_values_returns_dict(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog
    When   get_values() is called
    Then   it returns the expected OCR-specific keys including preprocessing options
    """
    values = ocr_dialog.get_values()
    assert "ocr_engine" in values
    assert "ocr_model" in values
    assert "preprocess_method" in values
    assert "debug" in values
    # Check that new preprocessing options are included
    assert "preprocess_dewarp" in values
    assert "preprocess_deskew" in values
    assert "preprocess_border_crop" in values
    assert "preprocess_denoise" in values
    assert "preprocess_gamma" in values
    assert "pdf_render_dpi" in values
    assert "correction_provider" not in values


@pytest.mark.gui
def test_ocr_settings_dialog_preprocessing_values_loaded(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog
    When   it is created with preprocessing values
    Then   the preprocessing options are loaded correctly
    """
    # The dialog should have been created with default values
    values = ocr_dialog.get_values()
    # Check that preprocessing values are boolean
    assert isinstance(values["preprocess_dewarp"], bool)
    assert isinstance(values["preprocess_deskew"], bool)
    assert isinstance(values["preprocess_border_crop"], bool)
    assert isinstance(values["preprocess_denoise"], bool)
    assert isinstance(values["preprocess_gamma"], bool)
    assert isinstance(values["pdf_render_dpi"], int)


@pytest.mark.gui
def test_ocr_settings_dialog_preprocessing_checkboxes_default_values(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog
    When   it is created
    Then   dewarp, deskew and denoise are pre-selected; border crop and gamma are not
    """
    values = ocr_dialog.get_values()
    assert values["preprocess_dewarp"] is True
    assert values["preprocess_deskew"] is True
    assert values["preprocess_border_crop"] is False
    assert values["preprocess_denoise"] is True
    assert values["preprocess_gamma"] is False


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
    items = [correction_dialog._model.itemText(i) for i in range(correction_dialog._model.count())]
    assert "openai-model-1" in items


@pytest.mark.gui
def test_correction_settings_dialog_shows_a_fetched_listing_in_the_combo(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog
    When   a live listing is shown
    Then   the correction model combo is updated and status shows green
    """
    correction_dialog._show_listing(ModelListing(["gpt-4o", "gpt-3.5-turbo"]))
    items = [correction_dialog._model.itemText(i) for i in range(correction_dialog._model.count())]
    assert "gpt-4o" in items
    assert "#27ae60" in correction_dialog._status_lbl.text()


@pytest.mark.gui
def test_correction_settings_dialog_status_red_on_empty_fetch(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog
    When   _on_models_fetched is called with an empty list
    Then   the status label shows a red error indicator
    """
    correction_dialog._on_models_fetched([])
    assert "#c0392b" in correction_dialog._status_lbl.text()


@pytest.mark.gui
def test_correction_settings_dialog_cache_hit_skips_new_thread_for_cloud_provider(
    qtbot, monkeypatch
) -> None:
    """
    Given  models are already cached for a cloud provider (openai)
    When   _auto_fetch is called
    Then   the cached list is used (no new thread is started)
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    svc = _CachingDummyAppService()
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "openai", "ollama_url": DEFAULTS["ollama_url"]},
        prompt="",
        app_service=svc,
    )
    qtbot.addWidget(dlg)
    if dlg._fetch_thread is not None:
        dlg._fetch_thread.wait(5000)
    svc.seed_cache("openai", ["gpt-4o"], "")
    old_thread = dlg._fetch_thread
    dlg._auto_fetch()
    assert dlg._fetch_thread is old_thread


@pytest.mark.gui
def test_correction_settings_dialog_ollama_always_fetches_in_a_thread(qtbot) -> None:
    """
    Given  Ollama models are already cached
    When   _auto_fetch is called
    Then   a thread is started anyway: classifying custom models must never run
           on the GUI thread, so Ollama has no synchronous cache shortcut
    """
    svc = _CachingDummyAppService()
    url = DEFAULTS["ollama_url"]
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "ollama", "ollama_url": url},
        prompt="",
        app_service=svc,
    )
    qtbot.addWidget(dlg)
    first = dlg._fetch_thread
    assert first is not None
    first.wait(5000)
    svc.seed_cache("ollama", ["llama3:latest"], url)
    dlg._auto_fetch()
    second = dlg._fetch_thread
    assert second is not None
    assert second is not first
    second.wait(5000)


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


# ── AI service: provider switch, model list source, install hint ──────────────


class _ProviderAppService(_CachingDummyAppService):
    """Service with Mistral and Mistral (EU) whose model answers and missing package are set."""

    def __init__(self) -> None:
        super().__init__()
        self.checked_calls: list[str] = []
        self.invalidated: list[str] = []
        self.listing = ModelListing(["glm-live", "mistral-large-latest"], "live")
        self.missing: dict[str, str] = {}

    def list_providers(self) -> list[str]:
        return ["mistral", "mistral_eu", "ollama", "openai"]

    def get_provider_info(self, provider: str) -> dict:
        if provider in ("mistral", "mistral_eu"):
            return {
                "display_name": "Mistral" if provider == "mistral" else "Mistral (EU)",
                "models": ["mistral-small-latest", "open-mistral-nemo"],
                "default_model": "mistral-small-latest",
                "needs_api_key": True,
                "env_key": "MISTRAL_API_KEY",
            }
        return super().get_provider_info(provider)

    def list_provider_models_checked(self, provider: str, *, base_url: str = "") -> ModelListing:
        self.checked_calls.append(provider)
        return self.listing

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        self.invalidated.append(provider)
        super().invalidate_model_cache(provider, base_url)

    def missing_provider_package(self, provider: str) -> str | None:
        return self.missing.get(provider)


def _wait_for_fetch(qtbot, dlg, svc: _ProviderAppService, calls: int) -> None:
    """Wait until the dialog asked the service *calls* times and showed the answer."""
    qtbot.waitUntil(lambda: len(svc.checked_calls) >= calls, timeout=5000)
    thread = dlg._fetch_thread
    if thread is not None:
        thread.wait(5000)
    qtbot.waitUntil(
        lambda: "●" in dlg._status_lbl.text() and "loading" not in dlg._status_lbl.text()
    )


@pytest.mark.gui
@pytest.mark.parametrize("dialog_cls", [CorrectionSettingsDialog, EvaluationSettingsDialog])
def test_switching_provider_asks_the_new_service_and_drops_the_old_model_name(
    qtbot, monkeypatch, dialog_cls
) -> None:
    """
    Given  a dialog on Ollama with an Ollama model typed in, and a Mistral key set
    When   the user picks Mistral as the AI service
    Then   Mistral is asked for its models (the live list replaces the built-in one) and the
           Ollama tag does not stay in the model field
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = _ProviderAppService()
    key = "correction" if dialog_cls is CorrectionSettingsDialog else "evaluate"
    dlg = dialog_cls(
        {f"{key}_provider": "ollama", f"{key}_model": "qwen3.5:9b"},
        prompt="",
        app_service=svc,
    )
    qtbot.addWidget(dlg)
    assert dlg._model.currentText() == "qwen3.5:9b"

    dlg._provider.setCurrentText("mistral")
    _wait_for_fetch(qtbot, dlg, svc, calls=1)

    assert svc.checked_calls == ["mistral"]
    items = [dlg._model.itemText(i) for i in range(dlg._model.count())]
    assert items == ["glm-live", "mistral-large-latest"]
    assert dlg._model.currentText() == "mistral-small-latest"  # the default, not the Ollama tag
    assert "#27ae60" in dlg._status_lbl.text()


@pytest.mark.gui
def test_a_failed_listing_without_any_models_still_says_why(qtbot, monkeypatch) -> None:
    """
    Given  a service whose answer failed and carries no built-in models to fall back on
    When   the dialog shows it
    Then   the status line gives the reason and "Try again", not just a dash
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = _ProviderAppService()
    svc.listing = ModelListing([], "failed", "the model list could not be read")
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "mistral", "correction_model": ""}, prompt="", app_service=svc
    )
    qtbot.addWidget(dlg)
    _wait_for_fetch(qtbot, dlg, svc, calls=1)

    status = dlg._status_lbl.text()
    assert "no models to show" in status
    assert "the model list could not be read" in status
    assert "Try again" in status


@pytest.mark.gui
def test_picking_an_online_provider_does_not_throw_away_its_remembered_model_list(
    qtbot, monkeypatch
) -> None:
    """
    Given  a dialog on Ollama and a Mistral key
    When   the user picks Mistral
    Then   the remembered Mistral list is not dropped (only "Try again" does that), so a list
           fetched a moment ago is shown again without asking the online service once more
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = _ProviderAppService()
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "ollama", "correction_model": ""}, prompt="", app_service=svc
    )
    qtbot.addWidget(dlg)

    dlg._provider.setCurrentText("mistral")
    _wait_for_fetch(qtbot, dlg, svc, calls=1)

    assert "mistral" not in svc.invalidated


@pytest.mark.gui
def test_opening_the_dialog_asks_the_service_once(qtbot, monkeypatch) -> None:
    """
    Given  a saved provider that is not the first in the list
    When   the dialog opens
    Then   the service is asked exactly once, for the saved provider (selecting it while
           loading must not start a fetch of its own)
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = _ProviderAppService()
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "mistral_eu", "correction_model": "glm-live"},
        prompt="",
        app_service=svc,
    )
    qtbot.addWidget(dlg)

    _wait_for_fetch(qtbot, dlg, svc, calls=1)

    assert svc.checked_calls == ["mistral_eu"]
    assert dlg._model.currentText() == "glm-live"


@pytest.mark.gui
def test_a_late_answer_for_the_previous_service_is_ignored(qtbot, monkeypatch) -> None:
    """
    Given  a dialog that has moved from OpenAI to Mistral
    When   the answer of the earlier OpenAI request arrives afterwards
    Then   it does not touch the model list or the status of Mistral
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = _ProviderAppService()
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "mistral", "correction_model": ""}, prompt="", app_service=svc
    )
    qtbot.addWidget(dlg)
    _wait_for_fetch(qtbot, dlg, svc, calls=1)
    before = [dlg._model.itemText(i) for i in range(dlg._model.count())]
    status = dlg._status_lbl.text()

    dlg._on_listing_ready("openai", ModelListing(["gpt-late"]))

    assert [dlg._model.itemText(i) for i in range(dlg._model.count())] == before
    assert dlg._status_lbl.text() == status


@pytest.mark.gui
def test_a_failed_listing_is_shown_as_the_built_in_list_with_the_reason(qtbot, monkeypatch) -> None:
    """
    Given  Mistral cannot be asked because the key is refused
    When   the dialog shows the models
    Then   the status says it is the built-in list and why (not a green "N models"), and
           offers to try again, which asks the service again
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-wrong")
    svc = _ProviderAppService()
    svc.listing = ModelListing(
        ["mistral-small-latest", "open-mistral-nemo"], "failed", "Mistral did not accept the key"
    )
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "mistral", "correction_model": ""}, prompt="", app_service=svc
    )
    qtbot.addWidget(dlg)
    _wait_for_fetch(qtbot, dlg, svc, calls=1)

    status = dlg._status_lbl.text()
    assert "built-in list of 2 models" in status
    assert "Mistral did not accept the key" in status
    assert "Try again" in status
    assert "#27ae60" not in status

    dlg._status_lbl.linkActivated.emit("retry")
    _wait_for_fetch(qtbot, dlg, svc, calls=2)

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
    When   the dialog shows it
    Then   the first is a green count and the second says "built-in list" in grey
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = _ProviderAppService()
    dlg = CorrectionSettingsDialog({"correction_provider": "mistral"}, prompt="", app_service=svc)
    qtbot.addWidget(dlg)
    _wait_for_fetch(qtbot, dlg, svc, calls=1)

    dlg._show_listing(listing)

    assert expected in dlg._status_lbl.text()


@pytest.mark.gui
def test_a_missing_provider_package_offers_the_install_in_downloads(qtbot, monkeypatch) -> None:
    """
    Given  Mistral is chosen while its package is not installed, and Downloads can be opened
    When   the dialog is shown and the Install button is pressed
    Then   the hint says Mistral support is not installed yet (no "download the full release"),
           Downloads is opened on the Mistral package, and afterwards the hint is checked again
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    svc = _ProviderAppService()
    svc.missing = {"mistral": "langchain-mistralai"}
    opened: list[list[str]] = []

    def open_downloads(preselect: list[str], parent) -> str | None:
        _ = parent
        opened.append(list(preselect))
        svc.missing = {}  # the user installed it
        return None

    dlg = CorrectionSettingsDialog(
        {"correction_provider": "mistral"},
        prompt="",
        app_service=svc,
        open_downloads=open_downloads,
    )
    qtbot.addWidget(dlg)
    dlg.show()

    hint = dlg._package_hint
    assert "Mistral support is not installed yet." in hint.message()
    assert "full-featured release" not in hint.message()

    hint._button.click()

    assert opened == [["provider:mistral"]]
    assert hint.isHidden()


@pytest.mark.gui
def test_no_install_hint_when_the_package_is_installed(qtbot, monkeypatch) -> None:
    """
    Given  a provider whose package is installed
    When   the dialog is shown
    Then   no install hint appears
    """
    monkeypatch.setenv("MISTRAL_API_KEY", "k-test")
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "mistral"}, prompt="", app_service=_ProviderAppService()
    )
    qtbot.addWidget(dlg)
    dlg.show()

    assert dlg._package_hint.isHidden()


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
    items = [evaluation_dialog._model.itemText(i) for i in range(evaluation_dialog._model.count())]
    assert "openai-eval-1" in items


@pytest.mark.gui
def test_evaluation_settings_dialog_on_models_fetched_updates_combo(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog
    When   _on_models_fetched is called with a list
    Then   the evaluation model combo is updated and status shows green
    """
    evaluation_dialog._on_models_fetched(["claude-3-haiku", "claude-3-opus"])
    items = [evaluation_dialog._model.itemText(i) for i in range(evaluation_dialog._model.count())]
    assert "claude-3-haiku" in items
    assert "#27ae60" in evaluation_dialog._status_lbl.text()


@pytest.mark.gui
def test_evaluation_settings_dialog_status_red_on_empty_fetch(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog
    When   _on_models_fetched is called with an empty list
    Then   the evaluation status label shows a red error indicator
    """
    evaluation_dialog._on_models_fetched([])
    assert "#c0392b" in evaluation_dialog._status_lbl.text()


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
    dlg.show()

    # Set to tesseract and verify model controls are hidden
    dlg._ocr_config_selector._ocr_engine.setCurrentText("tesseract")
    assert dlg._ocr_config_selector._ocr_model_label.isVisible() is False
    assert dlg._ocr_config_selector._ocr_model_row.isVisible() is False

    # Set to ollama and verify model controls are visible
    dlg._ocr_config_selector._ocr_engine.setCurrentText("ollama")
    assert dlg._ocr_config_selector._ocr_model_label.isVisible() is True
    assert dlg._ocr_config_selector._ocr_model_row.isVisible() is True


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
def test_output_settings_dialog_round_trips_text_file_options(qtbot) -> None:
    """
    Given  an OutputSettingsDialog seeded with both text-file options on
    When   get_values() is called
    Then   both options are returned as set, next to the Word option
    """
    dlg = OutputSettingsDialog(
        {"output_dir": "/tmp/reports", "text_enabled": True, "text_anonymized_enabled": True}
    )
    qtbot.addWidget(dlg)

    values = dlg.get_values()

    assert values["text_enabled"] is True
    assert values["text_anonymized_enabled"] is True
    assert "docx_enabled" in values


@pytest.mark.gui
def test_output_settings_dialog_needs_folder_for_text_files(qtbot) -> None:
    """
    Given  an OutputSettingsDialog with text files on and no output folder
    When   OK is pressed
    Then   a warning is shown and the dialog stays open
    """
    from unittest.mock import patch  # noqa: PLC0415

    dlg = OutputSettingsDialog({"text_enabled": True})
    qtbot.addWidget(dlg)

    with patch("teachers_teammate.gui._settings_dialog.QMessageBox.warning") as warn:
        dlg._on_accept()

    warn.assert_called_once()
    assert dlg.result() != dlg.DialogCode.Accepted


@pytest.mark.gui
def test_downloads_dialog_has_one_tab_per_section(qtbot) -> None:
    """
    Given  the standalone DownloadsDialog
    When   it is constructed
    Then   it exposes one tab per downloadable section, Ollama Models first
    """
    from PySide6.QtWidgets import QTabWidget  # noqa: PLC0415

    from teachers_teammate.gui._downloads_dialog import DownloadsDialog  # noqa: PLC0415

    dlg = DownloadsDialog(
        ollama_url="http://127.0.0.1:11434",
        app_service=_DummyAppService(),  # ty: ignore[invalid-argument-type]  # test stub for the concrete service
    )
    qtbot.addWidget(dlg)

    tabs = dlg.findChild(QTabWidget)
    labels = [tabs.tabText(i) for i in range(tabs.count())]
    assert labels == [
        "Ollama Models",
        "OCR Engines",
        "LLM Providers",
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
    items = [correction_dialog._model.itemText(i) for i in range(correction_dialog._model.count())]
    assert "\u2014 default \u2014" not in items
    # Unknown models get a stable, name-sorted order under the Ollama guidance.
    assert items == ["gpt-3.5", "gpt-4o"]


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
    correction_dialog._model.setCurrentText("")
    correction_dialog._on_provider_changed("openai")
    assert correction_dialog._model.currentText() == "openai-model-2"


@pytest.mark.gui
def test_correction_get_values_returns_selected_model(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog with the first model selected
    When   get_values() is called
    Then   the selected model name is returned (no placeholder-to-empty mapping)
    """
    correction_dialog._on_models_fetched(["gpt-4o"])
    correction_dialog._model.setCurrentIndex(0)
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
    correction_dialog._model.setCurrentText("gpt-4o")
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
    items = [evaluation_dialog._model.itemText(i) for i in range(evaluation_dialog._model.count())]
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
    evaluation_dialog._model.setCurrentIndex(0)
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
    assert hasattr(ocr_dialog._ocr_config_selector, "_ocr_temperature")
    assert ocr_dialog._ocr_config_selector._ocr_temperature.value() == DEFAULTS["ocr_temperature"]


@pytest.mark.gui
def test_ocr_settings_dialog_get_values_includes_temperature(ocr_dialog) -> None:
    """
    Given  an OCRSettingsDialog with temperature set to 0.5
    When   get_values() is called
    Then   the returned dict contains ocr_temperature=0.5
    """
    ocr_dialog._ocr_config_selector._ocr_temperature.setValue(0.5)
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
    assert hasattr(correction_dialog, "_temperature")
    assert correction_dialog._temperature.value() == DEFAULTS["correction_temperature"]


@pytest.mark.gui
def test_correction_settings_dialog_get_values_includes_temperature(correction_dialog) -> None:
    """
    Given  a CorrectionSettingsDialog with temperature set to 0.3
    When   get_values() is called
    Then   the returned dict contains correction_temperature=0.3
    """
    correction_dialog._temperature.setValue(0.3)
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
    assert hasattr(evaluation_dialog, "_temperature")
    assert evaluation_dialog._temperature.value() == DEFAULTS["evaluate_temperature"]


@pytest.mark.gui
def test_evaluation_settings_dialog_get_values_includes_temperature(evaluation_dialog) -> None:
    """
    Given  an EvaluationSettingsDialog with temperature set to 1.0
    When   get_values() is called
    Then   the returned dict contains evaluate_temperature=1.0
    """
    evaluation_dialog._temperature.setValue(1.0)
    values = evaluation_dialog.get_values()
    assert "evaluate_temperature" in values
    assert values["evaluate_temperature"] == pytest.approx(1.0)


# ── hardware profile & model guidance ──────────────────────────────────────


class _CatalogAppService(_CachingDummyAppService):
    """Dummy service whose Ollama server has real catalog models installed."""

    def __init__(self, models: list[str]) -> None:
        super().__init__()
        self._models = models

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        _ = base_url
        return self._models if provider == "ollama" else [f"{provider}-model"]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("memory_gb", "fragment", "warning"),
    [
        (32.0, "slower", False),  # fits with the computer's memory: informative, not an alarm
        (None, "very slow", True),  # memory unknown: the warning as before
    ],
)
def test_hint_for_a_model_too_big_for_the_card_depends_on_the_computers_memory(
    qtbot, memory_gb: float | None, fragment: str, warning: bool
) -> None:
    """
    Given  an 8 GB graphics profile and an installed gpt-oss:20b (about 14 GB)
    When   the model is selected in the Proofreading dialog
    Then   with 32 GB of computer memory the hint explains it runs slower (no warning); without
           that figure it still warns that the model may be very slow
    """
    service = _CatalogAppService(["gpt-oss:20b", "qwen3.5:9b"])
    service.system_memory_gb = memory_gb
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "ollama", "correction_model": "qwen3.5:9b"},
        prompt="Fix.",
        app_service=service,
        hardware_profile="8gb",
    )
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: len(dlg._guidance._models) == 2, timeout=5000)
    with qtbot.waitSignal(dlg._hw_selector.resolved_changed, timeout=5000):
        dlg._hw_selector.set_setting("8gb")  # resolve again, now with the memory known

    dlg._model.setCurrentText("gpt-oss:20b")
    hint = dlg._guidance._hint_lbl.text()

    assert fragment in hint.lower()
    assert hint.startswith("⚠") is warning


@pytest.mark.use_case("Hardware_Profile_Override")
def test_settings_dialog_get_values_contains_hardware_profile(settings_dialog) -> None:
    """
    Given  the Connections dialog with its "My computer" selector
    When   get_values() is called
    Then   the hardware_profile setting is included (AC9)
    """
    values = settings_dialog.get_values()
    assert values["hardware_profile"] in ("auto", "cpu", "8gb", "16gb")


@pytest.mark.use_case("Model_Category_Guidance")
def test_correction_dialog_reading_model_shows_warning_hint(qtbot) -> None:
    """
    Given  the Proofreading dialog with a reading model installed
    When   the reading model is selected
    Then   the hint is a warning that it is a reading model and names a writing model;
           the item is flagged in the list as "Reading model only"
    """
    service = _CatalogAppService(["qwen3-vl:8b", "qwen3.5:9b"])
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "ollama", "correction_model": "qwen3.5:9b"},
        prompt="Fix.",
        app_service=service,
        hardware_profile="8gb",
    )
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: len(dlg._guidance._models) == 2, timeout=5000)
    dlg._model.setCurrentText("qwen3-vl:8b")
    hint = dlg._guidance._hint_lbl.text()
    assert hint.startswith("⚠")
    assert "reading model" in hint
    assert "qwen3.5:9b" in hint  # the BEST writing model on 8 GB
    assert "cannot read scans" not in hint
    index = dlg._model.findText("qwen3-vl:8b")
    assert "Reading model only" in dlg._model.itemData(index, Qt.ItemDataRole.ToolTipRole)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize("dialog_cls", [CorrectionSettingsDialog, EvaluationSettingsDialog])
@pytest.mark.parametrize(
    ("profile", "same_models", "expected_visible"),
    [
        ("8gb", False, True),
        ("8gb", True, False),
        ("16gb", False, False),
    ],
)
def test_same_model_tip_visibility(
    qtbot, dialog_cls, profile: str, same_models: bool, expected_visible: bool
) -> None:
    """
    Given  a Proofreading or Content Review dialog on a cpu/8gb profile
    When   both tasks use Ollama
    Then   the same-model tip is shown only when the two models differ
           and hidden on larger profiles
    """
    own_model = "qwen3.5:9b"
    other_model = own_model if same_models else "mistral:latest"
    if dialog_cls is CorrectionSettingsDialog:
        values = {
            "correction_provider": "ollama",
            "correction_model": own_model,
            "evaluate_provider": "ollama",
            "evaluate_model": other_model,
        }
        dlg = CorrectionSettingsDialog(
            values, prompt="Fix.", app_service=_DummyAppService(), hardware_profile=profile
        )
    else:
        values = {
            "evaluate_provider": "ollama",
            "evaluate_model": own_model,
            "correction_provider": "ollama",
            "correction_model": other_model,
        }
        dlg = EvaluationSettingsDialog(
            values, prompt="Check.", app_service=_DummyAppService(), hardware_profile=profile
        )
    qtbot.addWidget(dlg)
    with qtbot.waitSignal(dlg._hw_selector.resolved_changed, timeout=5000):
        pass
    # The dialog is not shown in tests, so query the explicit hide flag.
    assert dlg._same_model_tip_lbl.isHidden() is not expected_visible


# ── Connections: URL editing and the shared "My computer" setting ──────────


@pytest.mark.use_case("Hardware_Profile_Override")
def test_typing_the_ollama_url_does_not_redetect_until_editing_finished(qtbot) -> None:
    """
    Given  the Connections dialog in automatic hardware mode
    When   the user types a new Ollama URL character by character
    Then   no detection runs per keystroke; it runs once when editing finished
           (review finding #3: a half-typed URL used to re-probe the GPU)
    """
    svc = _DummyAppService()
    dlg = SettingsDialog(
        {"ollama_url": DEFAULTS["ollama_url"], "hardware_profile": "auto"}, app_service=svc
    )
    qtbot.addWidget(dlg)
    with qtbot.waitSignal(dlg._hardware_selector.resolved_changed, timeout=5000):
        pass
    calls_before = len(svc.detect_calls)

    for partial in ("h", "ht", "http", "http://192.168", "http://192.168.1.50:11434"):
        dlg._ollama_url.setText(partial)
    qtbot.wait(100)
    assert len(svc.detect_calls) == calls_before

    with qtbot.waitSignal(dlg._hardware_selector.resolved_changed, timeout=5000):
        dlg._ollama_url.editingFinished.emit()
    assert svc.detect_calls[-1][0] == "http://192.168.1.50:11434"
    assert len(svc.detect_calls) == calls_before + 1


@pytest.mark.use_case("Hardware_Profile_Override")
def test_detect_gpu_button_uses_the_shared_service(qtbot) -> None:
    """
    Given  the Connections dialog with an injected service
    When   the "Detect GPU" button runs
    Then   the injected service answers (so its cached probe is reused) instead of
           a freshly constructed one probing the machine again
    """

    class _Gpu:
        name = "RTX 4060"
        vendor = "nvidia"

    class _GpuService(_DummyAppService):
        def detect_gpus(self, *, refresh: bool = False) -> list:
            super().detect_gpus(refresh=refresh)
            return [_Gpu()]

    svc = _GpuService()
    dlg = SettingsDialog({"ollama_url": DEFAULTS["ollama_url"]}, app_service=svc)
    qtbot.addWidget(dlg)

    dlg._on_detect_gpu()
    qtbot.waitUntil(lambda: "RTX 4060" in dlg._gpu_status_lbl.text(), timeout=5000)
    assert dlg._gpu_detect_thread is not None
    dlg._gpu_detect_thread.wait(5000)


@pytest.mark.use_case("Hardware_Profile_Override")
def test_detect_gpu_button_probes_again_and_refreshes_my_computer(qtbot) -> None:
    """
    Given  the Connections dialog whose first probe already ran
    When   the user clicks "Detect GPU"
    Then   the service is asked to probe again (refresh=True — a driver may have been
           installed since) and "My computer" resolves again from that fresh probe
    """
    svc = _DummyAppService()
    dlg = SettingsDialog(
        {"ollama_url": DEFAULTS["ollama_url"], "hardware_profile": "auto"}, app_service=svc
    )
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: len(svc.detect_calls) == 1, timeout=5000)

    dlg._on_detect_gpu()
    qtbot.waitUntil(
        lambda: dlg._gpu_status_lbl.text() == "No NVIDIA or AMD GPU detected.", timeout=5000
    )
    assert svc.gpu_calls == [True]
    qtbot.waitUntil(lambda: len(svc.detect_calls) == 2, timeout=5000)
    assert svc.detect_calls[-1][1] is False  # re-resolved from the probe, no second probe
    assert dlg._gpu_detect_thread is not None
    dlg._gpu_detect_thread.wait(5000)


@pytest.mark.use_case("Hardware_Profile_Override")
def test_connections_dialog_detects_hardware_for_the_configured_url(qtbot) -> None:
    """
    Given  saved settings with Ollama on another computer
    When   the Connections dialog opens
    Then   the first "My computer" detection already uses that URL (not the default), so
           the status text describes the right machine
    """
    url = "http://192.168.1.50:11434"
    svc = _DummyAppService()
    dlg = SettingsDialog({"ollama_url": url, "hardware_profile": "auto"}, app_service=svc)
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: len(svc.detect_calls) >= 1, timeout=5000)
    assert svc.detect_calls[0][0] == url
    assert dlg._ollama_url.text() == url


@pytest.mark.use_case("Model_Category_Guidance")
def test_closing_a_dialog_parks_a_running_fetch_thread_without_waiting(qtbot) -> None:
    """
    Given  a Proofreading dialog whose model list is still being fetched
    When   the dialog is closed
    Then   closing does not block on the thread, the thread is parked (so it is never
           destroyed while running) and its late answer no longer reaches the dialog
    """
    release = threading.Event()

    class _SlowService(_DummyAppService):
        def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
            _ = base_url
            release.wait(10)
            return [f"{provider}-model"]

    dlg = CorrectionSettingsDialog(
        {"correction_provider": "ollama", "ollama_url": DEFAULTS["ollama_url"]},
        prompt="",
        app_service=_SlowService(),
        hardware_profile="8gb",
    )
    qtbot.addWidget(dlg)
    thread = dlg._fetch_thread
    assert thread is not None and thread.isRunning()

    dlg.done(0)
    assert thread in _settings_dialog._RETIRED_THREADS

    release.set()
    qtbot.waitUntil(thread.isFinished, timeout=5000)
    qtbot.wait(50)  # let a (disconnected) late result be delivered, if it were
    assert dlg._guidance._models == []
    _settings_dialog._purge_retired_threads()
    assert thread not in _settings_dialog._RETIRED_THREADS


@pytest.mark.use_case("Model_Category_Guidance")
def test_typed_model_without_tag_counts_as_installed_latest(qtbot) -> None:
    """
    Given  a Proofreading dialog whose server lists "mistral:latest"
    When   the user types "mistral" (the tag Ollama would resolve to :latest)
    Then   the hint does not claim the model is not downloaded
    """
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "ollama", "ollama_url": DEFAULTS["ollama_url"]},
        prompt="",
        app_service=_CatalogAppService(["mistral:latest"]),
        hardware_profile="8gb",
        open_downloads=lambda tags, parent: None,
    )
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg._guidance._models == ["mistral:latest"], timeout=5000)

    dlg._model.setCurrentText("mistral")
    assert "not downloaded" not in dlg._guidance._hint_lbl.text()
    dlg._model.setCurrentText("mistral-nemo:12b")
    assert "not downloaded" in dlg._guidance._hint_lbl.text()


@pytest.mark.use_case("Hardware_Profile_Override")
def test_dialog_follows_profile_chosen_in_downloads(qtbot) -> None:
    """
    Given  a Proofreading dialog whose best model is not installed
    When   the user opens Downloads via "Download best choice…" and changes
           "My computer" to 16 GB there
    Then   Downloads was opened on top of the dialog and the dialog's hints now
           follow the 16 GB profile (review finding #9)
    """
    received: list[tuple[list[str], object]] = []

    def open_downloads(tags: list[str], parent) -> str | None:
        received.append((tags, parent))
        return "16gb"

    svc = _DummyAppService()
    dlg = CorrectionSettingsDialog(
        {"correction_provider": "ollama", "ollama_url": DEFAULTS["ollama_url"]},
        prompt="",
        app_service=svc,
        hardware_profile="8gb",
        open_downloads=open_downloads,
    )
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg._guidance._models == ["ollama-model"], timeout=5000)
    first_thread = dlg._fetch_thread
    assert first_thread is not None
    first_thread.wait(5000)
    assert dlg._guidance._best_btn.text() == "Download best choice…"

    dlg._guidance._best_btn.click()

    assert received == [(["qwen3.5:9b"], dlg)]
    assert dlg._hw_selector.setting() == "16gb"
    qtbot.waitUntil(lambda: dlg._guidance.active_profile() == "16gb", timeout=5000)
    if dlg._fetch_thread is not None:
        dlg._fetch_thread.wait(5000)  # the refetch after Downloads closed
