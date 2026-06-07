"""Unit tests for teachers_teammate.application.service."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.application import service as service_module
from teachers_teammate.application.commands import ApplicationCommands
from teachers_teammate.application.service import ProcessingApplicationService
from teachers_teammate.infrastructure.state_repository import DocumentState


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_svc(
    *,
    pipeline_factory=None,
    state_repository_factory=None,
    discovery=None,
) -> ProcessingApplicationService:
    mock_pipeline_factory = pipeline_factory or MagicMock(return_value=MagicMock(run=lambda: 0))
    mock_state_factory = state_repository_factory or MagicMock(return_value=MagicMock())
    mock_discovery = discovery or MagicMock()
    return ProcessingApplicationService(
        discovery=mock_discovery,
        pipeline_factory=mock_pipeline_factory,
        state_repository_factory=mock_state_factory,
    )


def _minimal_state(tmp_path: Path, source: Path) -> DocumentState:
    return DocumentState(
        schema_version=2,
        source_path=str(source.resolve()),
        source_hash="abc123",
    )


# ── run_selected / run_preview_only ───────────────────────────────────────


@pytest.mark.unit
def test_run_selected_delegates_to_pipeline_factory(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService with an injected pipeline_factory
    When   run_selected() is called
    Then   pipeline_factory is called once and its run() return value is forwarded
    """
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(tmp_path)
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = 42
    factory = MagicMock(return_value=mock_pipeline)
    svc = _make_svc(pipeline_factory=factory)

    result = svc.run_selected(cfg)

    factory.assert_called_once()
    mock_pipeline.run.assert_called_once()
    assert result == 42


@pytest.mark.unit
def test_run_preview_only_delegates_to_pipeline_factory(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService with an injected pipeline_factory
    When   run_preview_only() is called
    Then   pipeline_factory is called and run_preview_only() return value is forwarded
    """
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(tmp_path)
    mock_pipeline = MagicMock()
    mock_pipeline.run_preview_only.return_value = 0
    factory = MagicMock(return_value=mock_pipeline)
    svc = _make_svc(pipeline_factory=factory)

    result = svc.run_preview_only(cfg)

    factory.assert_called_once_with(cfg)
    mock_pipeline.run_preview_only.assert_called_once()
    assert result == 0


# ── check_connection — engine checks ──────────────────────────────────────


@pytest.mark.unit
@pytest.mark.use_case("Service_Availability_Check")
def test_check_connection_ollama_engine_returns_true_when_models_present(monkeypatch) -> None:
    """
    Given  the ollama engine and OllamaClient.check_connection reports one model available
    When   check_connection(engine='ollama') is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(
        ollama_utils.OllamaClient,
        "check_connection",
        lambda self, model="": (True, True, "✓ Connected — 1 model(s) available"),
    )
    svc = _make_svc()
    ok, _, msg = svc.check_connection(engine="ollama")
    assert ok is True
    assert "1" in msg


@pytest.mark.unit
def test_check_connection_ollama_engine_returns_false_when_no_models() -> None:
    """
    Given  Ollama is reachable but has no models pulled
    When   check_connection(engine='ollama') is called
    Then   returns (connected=True, model_ok=False, <message mentioning Ollama>)
    """
    svc = _make_svc()
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    with patch.object(
        ollama_utils.OllamaClient,
        "check_connection",
        return_value=(True, False, "✗ Connected to Ollama but no models are pulled"),
    ):
        connected, model_ok, msg = svc.check_connection(engine="ollama")
    assert connected is True
    assert model_ok is False
    assert "Ollama" in msg or "ollama" in msg.lower()


@pytest.mark.unit
@pytest.mark.use_case("Service_Availability_Check")
def test_check_connection_tesseract_returns_true_when_binary_found(monkeypatch) -> None:
    """
    Given  shutil.which returns a non-None path for 'tesseract'
    When   check_connection(engine='tesseract') is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    import shutil  # noqa: PLC0415

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tesseract")
    svc = _make_svc()
    ok, _, msg = svc.check_connection(engine="tesseract")
    assert ok is True
    assert "tesseract" in msg.lower()


@pytest.mark.unit
def test_check_connection_tesseract_returns_false_when_binary_missing(monkeypatch) -> None:
    """
    Given  shutil.which returns None for 'tesseract'
    When   check_connection(engine='tesseract') is called
    Then   returns (connected=False, model_ok=False, <error message>)
    """
    import shutil  # noqa: PLC0415

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    svc = _make_svc()
    ok, _, msg = svc.check_connection(engine="tesseract")
    assert ok is False
    assert "tesseract" in msg.lower()


@pytest.mark.unit
def test_check_connection_paddleocr_returns_true_when_installed(monkeypatch) -> None:
    """
    Given  importlib.util.find_spec returns a non-None spec for 'paddleocr'
    When   check_connection(engine='paddleocr') is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    import importlib.util  # noqa: PLC0415

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    svc = _make_svc()
    ok, _, msg = svc.check_connection(engine="paddleocr")
    assert ok is True
    assert "paddleocr" in msg.lower()


@pytest.mark.unit
def test_check_connection_paddleocr_returns_false_when_not_installed(monkeypatch) -> None:
    """
    Given  importlib.util.find_spec returns None for 'paddleocr'
    When   check_connection(engine='paddleocr') is called
    Then   returns (connected=False, model_ok=False, <error message>)
    """
    import importlib.util  # noqa: PLC0415

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    svc = _make_svc()
    ok, _, msg = svc.check_connection(engine="paddleocr")
    assert ok is False
    assert "paddleocr" in msg.lower()


# ── check_connection — provider checks ────────────────────────────────────


@pytest.mark.unit
def test_check_connection_provider_ollama_success(monkeypatch) -> None:
    """
    Given  provider='ollama' and OllamaClient.check_connection reports success
    When   check_connection(provider='ollama') is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(
        ollama_utils.OllamaClient,
        "check_connection",
        lambda self, model="": (True, True, "✓ Connected — 1 model(s) available"),
    )
    svc = _make_svc()
    ok, _, msg = svc.check_connection(provider="ollama")
    assert ok is True


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("google", "GOOGLE_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
        ("cohere", "COHERE_API_KEY"),
    ],
)
@pytest.mark.unit
@pytest.mark.use_case("Service_Availability_Check")
def test_check_connection_provider_returns_true_when_env_var_set(
    provider: str, env_var: str, monkeypatch
) -> None:
    """
    Given  a known LLM provider and its API key env var is set to a non-empty value
    When   check_connection(provider=<name>) is called
    Then   returns (connected=True, model_ok=True, <message containing the env var name>)
    """
    monkeypatch.setenv(env_var, "sk-test")
    svc = _make_svc()
    ok, _, msg = svc.check_connection(provider=provider)
    assert ok is True
    assert env_var in msg


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
    ],
)
@pytest.mark.unit
def test_check_connection_provider_returns_false_when_env_var_missing(
    provider: str, env_var: str, monkeypatch
) -> None:
    """
    Given  a known LLM provider and its API key env var is not set
    When   check_connection(provider=<name>) is called
    Then   returns (connected=False, model_ok=False, <message containing the env var name>)
    """
    monkeypatch.delenv(env_var, raising=False)
    svc = _make_svc()
    ok, _, msg = svc.check_connection(provider=provider)
    assert ok is False
    assert env_var in msg


@pytest.mark.unit
def test_check_connection_no_engine_no_provider_returns_false() -> None:
    """
    Given  neither engine nor provider are supplied
    When   check_connection() is called with no arguments
    Then   returns (connected=False, model_ok=False, <message>)
    """
    svc = _make_svc()
    ok, _, msg = svc.check_connection()
    assert ok is False
    assert len(msg) > 0


# ── list_ocr_engines / list_supported_suffixes / default_preprocess_for_engine


@pytest.mark.unit
def test_list_ocr_engines_returns_known_engines() -> None:
    """
    Given  a ProcessingApplicationService
    When   list_ocr_engines() is called
    Then   the returned list includes 'ollama' and 'tesseract'
    """
    svc = _make_svc()
    engines = svc.list_ocr_engines()
    assert "ollama" in engines
    assert "tesseract" in engines


@pytest.mark.unit
def test_list_supported_suffixes_returns_frozenset_with_images() -> None:
    """
    Given  a ProcessingApplicationService
    When   list_supported_suffixes() is called
    Then   the returned frozenset includes .png, .pdf, and .txt
    """
    svc = _make_svc()
    suffixes = svc.list_supported_suffixes()
    assert ".png" in suffixes
    assert ".pdf" in suffixes
    assert ".txt" in suffixes


@pytest.mark.unit
def test_default_preprocess_for_engine_returns_string() -> None:
    """
    Given  a known OCR engine name
    When   default_preprocess_for_engine() is called
    Then   a non-empty string is returned
    """
    svc = _make_svc()
    result = svc.default_preprocess_for_engine("tesseract")
    assert isinstance(result, str)
    assert len(result) > 0


# ── _invalidate_from_stage (static helper) ────────────────────────────────


@pytest.mark.unit
def test_invalidate_from_stage_static_matches_by_resolved_source_id(tmp_path: Path) -> None:
    """
    Given  a list of files and selected_source_ids matching one file by resolved path
    When   _invalidate_from_stage() is called
    Then   the matched file is invalidated in the store and its source_id is returned
    """
    source = tmp_path / "doc.png"
    source.touch()
    mock_store = MagicMock()
    mock_store.invalidate_from_stage.return_value = MagicMock()

    run_ids, failures, nothing_matched = ApplicationCommands._invalidate_from_stage(
        selected_source_ids=[str(source.resolve())],
        stage="ocr",
        files=[source],
        store=mock_store,
    )

    mock_store.invalidate_from_stage.assert_called_once_with(source, "ocr")
    assert str(source.resolve()) in run_ids
    assert failures == []
    assert nothing_matched is False


@pytest.mark.unit
def test_invalidate_from_stage_static_returns_nothing_matched_when_no_files_provided() -> None:
    """
    Given  an empty files list
    When   _invalidate_from_stage() is called
    Then   empty lists are returned and nothing_matched is True
    """
    run_ids, failures, nothing_matched = ApplicationCommands._invalidate_from_stage(
        selected_source_ids=["any"],
        stage="correction",
        files=[],
        store=MagicMock(),
    )
    assert run_ids == []
    assert failures == []
    assert nothing_matched is True


@pytest.mark.unit
def test_invalidate_from_stage_static_accumulates_oserror_failures(tmp_path: Path) -> None:
    """
    Given  a store whose invalidate_from_stage raises OSError
    When   _invalidate_from_stage() is called
    Then   the failure is recorded in the failures list and run_ids still contains the source
    """
    source = tmp_path / "bad.png"
    source.touch()
    mock_store = MagicMock()
    mock_store.invalidate_from_stage.side_effect = OSError("disk full")

    run_ids, failures, nothing_matched = ApplicationCommands._invalidate_from_stage(
        selected_source_ids=[str(source.resolve())],
        stage="ocr",
        files=[source],
        store=mock_store,
    )
    assert "bad.png" in failures[0]
    assert str(source.resolve()) in run_ids


# ── record_manual_ocr_edit ────────────────────────────────────────────────


@pytest.mark.unit
def test_record_manual_ocr_edit_delegates_to_store(tmp_path: Path) -> None:
    """
    Given  a valid source file and a store that returns a state
    When   record_manual_ocr_edit() is called
    Then   the store receives the edited text and the updated state is returned
    """
    from tests.conftest import make_config  # noqa: PLC0415

    source = tmp_path / "page.png"
    source.write_text("x")
    state = _minimal_state(tmp_path, source)
    updated = MagicMock()
    mock_store = MagicMock()
    mock_store.load_or_create.return_value = state
    mock_store.record_manual_ocr_edit.return_value = updated

    with patch(
        "teachers_teammate.application.commands.compute_file_hash",
        return_value="abc123",
    ):
        svc = ProcessingApplicationService(
            state_repository_factory=lambda _: mock_store,
        )
        result = svc.record_manual_ocr_edit(
            make_config(tmp_path),
            source=source,
            edited_text="hello world",
            preview_img="/tmp/preview.png",
        )

    mock_store.record_manual_ocr_edit.assert_called_once()
    assert result is updated


@pytest.mark.unit
def test_record_manual_ocr_edit_returns_none_on_oserror(tmp_path: Path) -> None:
    """
    Given  compute_file_hash raises OSError (file unreadable)
    When   record_manual_ocr_edit() is called
    Then   None is returned without raising
    """
    from tests.conftest import make_config  # noqa: PLC0415

    source = tmp_path / "missing.png"

    with patch(
        "teachers_teammate.application.commands.compute_file_hash",
        side_effect=OSError("no such file"),
    ):
        svc = ProcessingApplicationService(
            state_repository_factory=lambda _: MagicMock(),
        )
        result = svc.record_manual_ocr_edit(
            make_config(tmp_path),
            source=source,
            edited_text="text",
            preview_img="",
        )

    assert result is None


# ── record_manual_correction_edit ─────────────────────────────────────────


@pytest.mark.unit
def test_record_manual_correction_edit_persists_text(tmp_path: Path) -> None:
    """
    Given  a valid source file and a store holding an existing OCR-done state
    When   record_manual_correction_edit() is called
    Then   the store records the correction and the updated state is returned
    """
    from tests.conftest import make_config  # noqa: PLC0415

    source = tmp_path / "page.png"
    source.write_text("x")
    state = dataclasses.replace(_minimal_state(tmp_path, source), ocr_done=True, raw_text="raw")
    updated = MagicMock()
    mock_store = MagicMock()
    mock_store.load_or_create.return_value = state
    mock_store.record_manual_correction_edit.return_value = updated

    with patch(
        "teachers_teammate.application.commands.compute_file_hash",
        return_value="abc123",
    ):
        svc = ProcessingApplicationService(
            state_repository_factory=lambda _: mock_store,
        )
        result = svc.record_manual_correction_edit(
            make_config(tmp_path),
            source=source,
            raw_text="raw",
            preview_img="",
            edited_text="corrected",
        )

    mock_store.record_manual_correction_edit.assert_called_once()
    assert result is updated


@pytest.mark.unit
def test_manual_ocr_edit_survives_reconcile(tmp_path: Path) -> None:
    """
    Given  a manual OCR edit recorded for a source file
    When   the cache is reconciled against the same (unchanged) runtime config
    Then   the edited text is preserved rather than flagged stale and wiped
    """
    from tests.conftest import make_config  # noqa: PLC0415

    from teachers_teammate.infrastructure.file_discovery import (  # noqa: PLC0415
        FileDiscovery,
    )
    from teachers_teammate.infrastructure.preview_image_store import (  # noqa: PLC0415
        PreviewImageStore,
    )
    from teachers_teammate.infrastructure.state_repository import (  # noqa: PLC0415
        StateRepository,
    )
    from teachers_teammate.infrastructure.workflow.cache_service import (  # noqa: PLC0415
        CacheReconciliationService,
    )

    source = tmp_path / "note.txt"
    source.write_text("hello")
    cfg = make_config(tmp_path, correction_enabled=False)

    repo = StateRepository(tmp_path / "state")
    commands = ApplicationCommands(discovery=FileDiscovery(), state_store_factory=lambda _c: repo)
    commands.record_manual_ocr_edit(cfg, source=source, edited_text="MANUAL", preview_img="")

    recon = CacheReconciliationService(
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=cfg,
    )
    ctx = recon.prepare(source)

    assert ctx.state.ocr_done is True
    assert ctx.state.raw_text == "MANUAL"


# ── _invalidate_from_stage ValueError ────────────────────────────────────


@pytest.mark.unit
def test_invalidate_from_stage_static_invalid_stage_propagates_value_error(tmp_path: Path) -> None:
    """
    Given  a non-empty files list and an invalid stage name
    When   _invalidate_from_stage() is called
    Then   ValueError is raised (normalize_stage rejects the unknown stage name)
    """
    source = tmp_path / "doc.png"
    source.touch()

    with pytest.raises(ValueError, match="Invalid stage"):
        ApplicationCommands._invalidate_from_stage(
            selected_source_ids=[str(source.resolve())],
            stage="nonexistent",
            files=[source],
            store=MagicMock(),
        )


# ── clear_anonymizer_cache ────────────────────────────────────────────────────


@pytest.mark.unit
def test_clear_anonymizer_cache_empties_dict(tmp_path: Path) -> None:
    """
    Given  a service with cached anonymizer entries
    When   clear_anonymizer_cache() is called
    Then   the internal cache is empty
    """
    svc = _make_svc()
    svc._anonymizer_cache[("English", object())] = MagicMock()
    assert len(svc._anonymizer_cache) == 1

    svc.clear_anonymizer_cache()

    assert len(svc._anonymizer_cache) == 0


# ── New service passthroughs ──────────────────────────────────────────────────


@pytest.mark.unit
def test_detect_gpus_delegates_to_infrastructure(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService
    When   detect_gpus() is called
    Then   it delegates to gpu_detector.detect_gpus and returns the result
    """
    from teachers_teammate.infrastructure.gpu_detector import GpuInfo  # noqa: PLC0415

    fake_gpus = [GpuInfo("nvidia", "Test GPU")]
    svc = _make_svc()

    with patch("teachers_teammate.infrastructure.gpu_detector.detect_gpus", return_value=fake_gpus):
        result = svc.detect_gpus()

    assert result == fake_gpus


@pytest.mark.unit
def test_resolve_config_path_delegates(tmp_path: Path) -> None:
    """
    Given  a config file exists at a known path
    When   resolve_config_path() is called with that explicit path as a string
    Then   it returns the resolved Path without error
    """
    cfg = tmp_path / "ocr.toml"
    cfg.write_text("[settings]\n", encoding="utf-8")
    svc = _make_svc()

    result = svc.resolve_config_path(str(cfg))

    assert result == cfg


@pytest.mark.unit
def test_default_config_path_delegates(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService and a patched storage root
    When   default_config_path() is called
    Then   it delegates to storage_root.default_config_path and returns a Path
    """
    expected = tmp_path / "ocr.toml"
    svc = _make_svc()

    with patch(
        "teachers_teammate.application.service._infra_default_config_path",
        return_value=expected,
    ):
        result = svc.default_config_path()

    assert result == expected


@pytest.mark.unit
def test_addon_packages_dir_delegates(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService
    When   addon_packages_dir() is called
    Then   it delegates to addon_manager.get_packages_dir and returns a Path
    """
    svc = _make_svc()

    with patch(
        "teachers_teammate.infrastructure.addon_manager.get_packages_dir",
        return_value=tmp_path,
    ):
        result = svc.addon_packages_dir()

    assert result == tmp_path


@pytest.mark.unit
def test_get_installation_status_reports_tesseract_and_paddleocr() -> None:
    """
    Given  a ProcessingApplicationService and patched shutil.which + importlib.util.find_spec
    When   get_installation_status() is called
    Then   'tesseract' is True when which returns a path and 'paddleocr' is False when spec is None
    """
    svc = _make_svc()

    with (
        patch(
            "teachers_teammate.application.service.shutil.which", return_value="/usr/bin/tesseract"
        ),
        patch("teachers_teammate.application.service.importlib.util.find_spec", return_value=None),
    ):
        status = svc.get_installation_status()

    assert status["tesseract"] is True
    assert status["paddleocr"] is False


@pytest.mark.unit
def test_is_module_importable_returns_true_when_spec_found(monkeypatch) -> None:
    """
    Given  importlib.util.find_spec returns a non-None spec for a module name
    When   is_module_importable() is called with that module name
    Then   True is returned
    """
    import importlib.util  # noqa: PLC0415

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    svc = _make_svc()
    assert svc.is_module_importable("some_package") is True


@pytest.mark.unit
def test_is_module_importable_returns_false_when_spec_missing(monkeypatch) -> None:
    """
    Given  importlib.util.find_spec returns None (module not installed)
    When   is_module_importable() is called
    Then   False is returned
    """
    import importlib.util  # noqa: PLC0415

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    svc = _make_svc()
    assert svc.is_module_importable("missing_package") is False


@pytest.mark.unit
def test_get_installation_status_includes_langchain_packages(monkeypatch) -> None:
    """
    Given  get_installation_status() is called with a langchain_packages dict
    When   importlib.util.find_spec returns a spec for one module and None for another
    Then   the result contains 'langchain:{key}' entries matching the spec outcomes
    """
    import importlib.util  # noqa: PLC0415

    present_modules = {"langchain_core"}
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: object() if name in present_modules else None,
    )
    svc = _make_svc()
    status = svc.get_installation_status(
        langchain_packages={"core": "langchain_core", "openai": "langchain_openai"},
    )
    assert status["langchain:core"] is True
    assert status["langchain:openai"] is False


@pytest.mark.unit
def test_list_provider_models_uses_ttl_cache() -> None:
    """
    Given  a ProcessingApplicationService whose list_provider_models is called once
    When   list_provider_models() is called a second time within the TTL window
    Then   the underlying infrastructure function is called only once (cache hit)
    """
    svc = _make_svc()
    call_count = 0

    def _fake_list(provider, **_kwargs):
        nonlocal call_count
        call_count += 1
        return ["model-a", "model-b"]

    with patch(
        "teachers_teammate.application.service.list_provider_models", side_effect=_fake_list
    ):
        first = svc.list_provider_models("openai")
        second = svc.list_provider_models("openai")

    assert first == ["model-a", "model-b"]
    assert second == ["model-a", "model-b"]
    assert call_count == 1


@pytest.mark.unit
def test_invalidate_model_cache_forces_refetch() -> None:
    """
    Given  a ProcessingApplicationService with a cached model list for 'openai'
    When   invalidate_model_cache('openai') is called and then list_provider_models again
    Then   the underlying infrastructure function is called a second time
    """
    svc = _make_svc()
    call_count = 0

    def _fake_list(provider, **_kwargs):
        nonlocal call_count
        call_count += 1
        return ["model-a"]

    with patch(
        "teachers_teammate.application.service.list_provider_models", side_effect=_fake_list
    ):
        svc.list_provider_models("openai")
        svc.invalidate_model_cache("openai")
        svc.list_provider_models("openai")

    assert call_count == 2
