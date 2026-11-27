"""Shared helpers for the unit tests of the application services.

Every test file of ``test_run_service`` / ``test_documents_service`` / ``test_commands_service``
builds its services the way the entry points do (:func:`build_app_services`), so a test overrides
only the part it is about.  The helpers here are the ones more than one of those files needs.
"""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import MagicMock, patch
import pytest
from teachers_teammate.config import Config
from teachers_teammate.application.composition import AppServices, build_app_services
from teachers_teammate.application.commands import DocumentCommands
from teachers_teammate.domain.document_state import machine_texts
from teachers_teammate.domain.teacher_work import text_identity
from teachers_teammate.infrastructure.file_discovery import FileDiscovery
from teachers_teammate.infrastructure.input_provider_factory import supported_suffixes
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.preview_image_store import PreviewImageStore
from teachers_teammate.infrastructure.state_repository import (
    OcrResultRecord,
    StateRepository,
    compute_file_hash,
)
from teachers_teammate.infrastructure.teacher_work_store import TeacherWorkStore
from teachers_teammate.infrastructure.workflow.cache_service import CacheReconciliationService
from tests.conftest import fake_reader_store, make_config, make_key_facts


def _discovery_of(*names: str) -> MagicMock:
    """File discovery that finds the files called *names* (none by default)."""
    return MagicMock(
        collect_input_files=MagicMock(return_value=[Path(n) for n in names]),
        supported_suffixes=MagicMock(return_value=supported_suffixes()),
    )


#: The module the Ollama program lookup lives in, as the tests patch it.
_INSTALL = "teachers_teammate.infrastructure.ollama_install"
#: The reader's model files as the tests find them: in place, unless a test passes its own store.
_DEFAULT_STORE: ModelStore | None = None


def _build_services(**overrides) -> AppServices:
    """Return the services built the way the entry points build them."""
    return build_app_services(**overrides)


def _make_svc(
    *,
    pipeline_factory=None,
    state_repository_factory=None,
    discovery=None,
    model_store=None,
) -> AppServices:
    mock_pipeline_factory = pipeline_factory or MagicMock(return_value=MagicMock(run=lambda: 0))
    mock_state_factory = state_repository_factory or MagicMock(
        return_value=MagicMock(load_valid=MagicMock(return_value=None))
    )
    mock_discovery = discovery or _discovery_of()
    return _build_services(
        discovery=mock_discovery,
        pipeline_factory=mock_pipeline_factory,
        state_repository_factory=mock_state_factory,
        model_store=model_store or _DEFAULT_STORE,
    )


def _svc_with_document(
    tmp_path: Path,
    *,
    raw_text: str = "hello world",
    raw_text_format: str = "plain",
    corrected: str = "",
    review: dict | None = None,
) -> tuple[AppServices, Path]:
    """A service over a real state store holding one document the machine read."""
    source = tmp_path / "scan.png"
    source.write_text("x")
    repo = StateRepository(tmp_path / "state")
    state = repo.record_ocr_result(
        source,
        repo.load_or_create(source, compute_file_hash(source)),
        OcrResultRecord(
            source_hash=compute_file_hash(source),
            raw_text=raw_text,
            preview_img="",
            source_image="",
            ocr_config_hash="ocr",
            preview_config_hash="",
            raw_text_format=raw_text_format,
        ),
    )
    if corrected:
        repo.record_correction_result(
            source,
            state,
            correction_text=corrected,
            correction_config_hash="corr",
            correction_review=review,
            source_hash=text_identity(raw_text),
        )
    return _make_svc(state_repository_factory=lambda _path: repo), source


# ── run_selected / run_preview_only ───────────────────────────────────────


def _repo_with_document(
    tmp_path: Path, *, corrected: str = "", review: dict | None = None
) -> tuple[StateRepository, Path]:
    """A state store holding one document the machine read, and proofread when asked."""
    source = tmp_path / "input" / "note.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("hello world")
    repo = StateRepository(tmp_path / "state")
    state = repo.record_ocr_result(
        source,
        repo.load_or_create(source, compute_file_hash(source)),
        OcrResultRecord(
            source_hash=compute_file_hash(source),
            raw_text="hello world",
            preview_img="",
            source_image="",
            ocr_config_hash="ocr",
            preview_config_hash="",
        ),
    )
    if corrected:
        repo.record_correction_result(
            source,
            state,
            correction_text=corrected,
            correction_config_hash="corr",
            correction_review=review,
            source_hash=text_identity("hello world"),
        )
    return repo, source


def _commands_for(repo: StateRepository, **kwargs) -> DocumentCommands:
    """The window's write commands over *repo*, with the teacher's work in a temporary folder."""
    kwargs.setdefault("work_store", TeacherWorkStore())
    return DocumentCommands(
        state_store_factory=lambda _config: repo,
        facts=make_key_facts(),
        **kwargs,
    )


def _empty_store() -> MagicMock:
    """Return a state store that has no result for any file."""
    store = MagicMock()
    store.load_valid.return_value = None
    return store


def _reader_service(tmp_path: Path) -> AppServices:
    """Return a service over an empty reader store that finds the files of a real folder."""
    return _make_svc(
        model_store=fake_reader_store(tmp_path, installed=False),
        discovery=FileDiscovery(),
        state_repository_factory=lambda _path: _empty_store(),
    )


# ── what the recommendation service is asked for ────────────────────────────


def _ollama_config(tmp_path: Path, **overrides):
    """Config whose OCR and proofreading both use Ollama."""
    values: dict = {
        "ocr_engine": "ollama",
        "ocr_model": "qwen3-vl:8b",
        "correction_enabled": True,
        "correction_provider": "ollama",
        "correction_model": "qwen3.5:9b",
    }
    values.update(overrides)
    return make_config(tmp_path, **values)


def _server_answers(models: list[str], error: str = ""):
    """Patch the single Ollama request that reads a server's models with ``(models, error)``."""
    from teachers_teammate.domain.ollama_snapshot import (  # noqa: PLC0415
        InstalledModel,
        ServerSnapshot,
    )

    return patch(
        "teachers_teammate.infrastructure.ollama_utils.OllamaClient.snapshot",
        return_value=ServerSnapshot(
            version="0.12.3",
            models=tuple(InstalledModel(tag=tag) for tag in models),
            unreachable=error,
        ),
    )


def _computer(svc, monkeypatch, *, profile, free_gb, card_gb=None, reason=None) -> None:
    """Make *svc* believe it runs on a computer of *profile* with *free_gb* of free memory."""
    from teachers_teammate.domain.model_catalog import (  # noqa: PLC0415
        DetectionReason,
        HardwareDetection,
    )
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(gpu_detector, "detect_available_memory_gb", lambda: free_gb)
    detection = HardwareDetection(
        profile=profile,
        reason=reason or DetectionReason.GPU_DETECTED,
        memory_gb=card_gb,
        system_memory_gb=64.0,
    )
    monkeypatch.setattr(
        svc.recommendations, "resolve_hardware_profile", lambda _setting, _url: detection
    )


def _proofread(
    tmp_path: Path, *names: str, model: str = "qwen3.5:9b"
) -> tuple[Config, StateRepository, list[Path]]:
    """Return a config, a repository and sources with a stored proofreading each."""
    cfg = make_config(tmp_path, correction_enabled=True, correction_model=model)
    repo = StateRepository(tmp_path / "state")
    recon = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=cfg,
    )
    sources: list[Path] = []
    for name in names:
        source = tmp_path / name
        source.write_text(f"text of {name}")
        ctx = recon.prepare(source)
        ctx = recon.record_ocr(
            source, ctx, raw_text=f"text of {name}", preview_img="", source_image=""
        )
        recon.record_correction(
            source,
            ctx,
            correction_text=f"corrected {name}",
            source_text=f"text of {name}",
        )
        sources.append(source)
    return cfg, repo, sources


def _change_work(source: Path, change) -> None:
    """Apply *change* to the work of *source*, as the window's commands do."""
    TeacherWorkStore().update(compute_file_hash(source), source.name, change)


def _machine_of(repo: StateRepository, source: Path):
    state = repo.load(source)
    assert state is not None
    return machine_texts(state)


def reader_models_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every service built here a store that holds the built-in reader's models."""
    monkeypatch.setattr(
        sys.modules[__name__], "_DEFAULT_STORE", fake_reader_store(tmp_path / "default_reader")
    )
