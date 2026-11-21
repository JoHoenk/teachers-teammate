"""Unit tests for teachers_teammate.application.reader."""

from __future__ import annotations

from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.application.composition import build_app_services
from teachers_teammate.config import OcrConfig
from teachers_teammate.domain.local_models import PAGE_MODELS
from teachers_teammate.domain.setting_values import PreprocessMethod
from tests.conftest import fake_page_models_store, fake_reader_store


@pytest.mark.unit
def test_list_ocr_engines_returns_known_engines() -> None:
    """
    Given  a ReaderService
    When   list_ocr_engines() is called
    Then   the returned list includes 'ollama' and 'ppocr'
    """
    engines = build_app_services().reader.list_ocr_engines()

    assert "ollama" in engines
    assert "ppocr" in engines


@pytest.mark.unit
def test_list_ocr_models_leaves_out_ollama_cloud_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  an Ollama server with a downloaded model and a cloud model
    When   the models usable for text recognition are listed
    Then   only the downloaded model is offered, since the cloud model would receive the pages
    """
    services = build_app_services()
    monkeypatch.setattr(
        services.providers,
        "list_provider_models",
        lambda *_a, **_k: ["qwen3-vl:8b", "qwen3-vl:235b-cloud"],
    )

    listed = services.reader.list_ocr_models("http://127.0.0.1:11434")

    assert listed == ["qwen3-vl:8b"]


@pytest.mark.unit
def test_describe_ocr_refusal_names_the_reason_for_cloud_models_only() -> None:
    """
    Given  a cloud model and a downloaded model on the local Ollama server
    When   the refusal for each is asked for
    Then   only the cloud model has a reason, which says it runs on Ollama's servers
    """
    reader = build_app_services().reader
    url = "http://127.0.0.1:11434"

    assert "Ollama's servers" in reader.describe_ocr_refusal("ollama", "qwen3-vl:235b-cloud", url)
    assert reader.describe_ocr_refusal("ollama", "qwen3-vl:8b", url) == ""
    assert reader.describe_ocr_refusal("ppocr", "", url) == ""


@pytest.mark.unit
def test_preprocess_preview_uses_injected_component_factory(
    tmp_path: Path, sample_png: Path
) -> None:
    """
    Given  a ReaderService constructed with a custom component_factory
    When   preprocess_preview() is called
    Then   the factory's build_preprocessor is used instead of the module default

    Regression guard: preprocess_preview() used to call
    stage_builder.default_build_preprocessor directly, so the preview silently
    diverged from a pipeline configured with a non-default factory.
    """
    from teachers_teammate.infrastructure.stage_builder import (  # noqa: PLC0415
        PipelineComponentFactory,
    )

    class _StubPreprocessor:
        def preprocess(self, image_path):
            return image_path, ["stub_step"]

    build_preprocessor = MagicMock(return_value=_StubPreprocessor())
    factory = PipelineComponentFactory(build_preprocessor=build_preprocessor)
    store = MagicMock()
    reader = build_app_services(model_store=store, component_factory=factory).reader
    ocr = OcrConfig(preprocess_method=PreprocessMethod.AUTO)

    _original, _processed, steps = reader.preprocess_preview(sample_png, ocr, tmp_path)

    assert steps == ["stub_step"]
    build_preprocessor.assert_called_once_with(tmp_path, False, ocr, store)


@pytest.mark.unit
def test_the_page_models_are_installed_when_the_store_has_them(tmp_path: Path) -> None:
    """
    Given  a model store without the page models, and one that holds them
    When   page_models_installed() is asked
    Then   it is False for the first and True for the second
    """
    empty = build_app_services(
        model_store=fake_page_models_store(tmp_path / "a", installed=False)
    ).reader
    full = build_app_services(model_store=fake_page_models_store(tmp_path / "b")).reader

    assert not empty.page_models_installed()
    assert full.page_models_installed()


@pytest.mark.unit
def test_download_page_models_downloads_the_page_model_sets() -> None:
    """
    Given  a reader with a model store
    When   download_page_models() is called
    Then   the store is asked to download exactly the two page models, with the callbacks
    """
    store = MagicMock()
    reader = build_app_services(model_store=store).reader
    progress = MagicMock()

    reader.download_page_models(progress, None)

    store.download.assert_called_once_with(PAGE_MODELS, on_progress=progress, cancel=None)


@pytest.mark.unit
def test_reader_downloads_through_the_store(tmp_path: Path) -> None:
    """
    Given  a reader whose store is empty and a fake download
    When   download_reader() is called with progress and cancel
    Then   the store is asked to download the reader's two model sets
    """
    store = MagicMock()
    store.missing.return_value = [1]
    store.root = tmp_path
    reader = build_app_services(model_store=store).reader
    cancel = threading.Event()

    reader.download_reader(None, cancel)

    assert store.download.call_args.kwargs == {"on_progress": None, "cancel": cancel}
    assert len(list(store.download.call_args.args[0])) == 2
    assert reader.reader_installed() is False
    assert reader.models_dir() == tmp_path


# ── check_engine ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.use_case("Service_Availability_Check")
def test_check_connection_ollama_engine_returns_true_when_models_present(monkeypatch) -> None:
    """
    Given  the ollama engine and OllamaClient.check_connection reports one model available
    When   the reader's check for the ollama engine is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(
        ollama_utils.OllamaClient,
        "check_connection",
        lambda self, model="": (True, True, "✓ Connected — 1 model(s) available"),
    )
    services = build_app_services()
    ok, _, msg = services.reader.check_engine("ollama")
    assert ok is True
    assert "1" in msg


@pytest.mark.unit
def test_check_connection_ollama_engine_returns_false_when_no_models() -> None:
    """
    Given  Ollama is reachable but has no models pulled
    When   the reader's check for the ollama engine is called
    Then   returns (connected=True, model_ok=False, <message mentioning Ollama>)
    """
    services = build_app_services()
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    with patch.object(
        ollama_utils.OllamaClient,
        "check_connection",
        return_value=(True, False, "✗ Connected to Ollama but no models are pulled"),
    ):
        connected, model_ok, msg = services.reader.check_engine("ollama")
    assert connected is True
    assert model_ok is False
    assert "Ollama" in msg or "ollama" in msg.lower()


# ── check_connection — provider checks ────────────────────────────────────


@pytest.mark.unit
def test_check_connection_reader_reports_whether_it_is_downloaded(tmp_path: Path) -> None:
    """
    Given  a service with and one without the reader's model files
    When   the reader's check for the built-in reader is called
    Then   the first is connected, the second says the reader is not downloaded
    """
    ok, _, msg = build_app_services(
        model_store=fake_reader_store(tmp_path / "a")
    ).reader.check_engine("ppocr")
    missing, _, missing_msg = build_app_services(
        model_store=fake_reader_store(tmp_path / "b", installed=False)
    ).reader.check_engine("ppocr")

    assert ok is True and "built-in reader ready" in msg
    assert missing is False and "not downloaded" in missing_msg and "31 MB" in missing_msg
