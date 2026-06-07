"""Unit tests for teachers_teammate.infrastructure.workflow.cache_service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.config import DEFAULTS
from teachers_teammate.infrastructure.state_repository import DocumentState, OcrResultRecord
from teachers_teammate.infrastructure.workflow.cache_service import (
    CacheContext,
    CacheReconciliationService,
)
from tests.conftest import make_config


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_state(source_path: str = "doc.png") -> DocumentState:
    return DocumentState(schema_version=2, source_path=source_path, source_hash="abc123")


def _make_ctx(state: DocumentState | None = None) -> CacheContext:
    return CacheContext(
        state=state or _make_state(),
        source_hash="abc123",
        ocr_prompt="Transcribe the text.",
        ocr_config_hash="ocr_h",
        correction_config_hash="corr_h",
        eval_config_hash="eval_h",
        preview_config_hash="prev_h",
    )


def _make_svc(
    tmp_path: Path, **config_overrides: object
) -> tuple[CacheReconciliationService, MagicMock, MagicMock]:
    repo = MagicMock()
    artifacts = MagicMock()
    cfg = make_config(tmp_path, **config_overrides)
    svc = CacheReconciliationService(state_repo=repo, artifact_store=artifacts, config=cfg)
    return svc, repo, artifacts


# ── prepare() ─────────────────────────────────────────────────────────────


def test_prepare_calls_load_or_create_and_reconcile(tmp_path: Path) -> None:
    """
    Given  a source file and a new cache service
    When   prepare() is called
    Then   repo.load_or_create and repo.reconcile_runtime_config are both called,
           and the returned CacheContext contains populated hash fields
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"fake image data")

    svc, repo, _ = _make_svc(tmp_path)
    reconciled_state = _make_state()
    repo.load_or_create.return_value = _make_state()
    repo.reconcile_runtime_config.return_value = reconciled_state

    with patch(
        "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
        return_value="filehash",
    ):
        ctx = svc.prepare(src)

    repo.load_or_create.assert_called_once_with(src, "filehash")
    repo.reconcile_runtime_config.assert_called_once()
    assert ctx.source_hash == "filehash"
    assert ctx.state is reconciled_state
    assert ctx.ocr_prompt != ""
    assert ctx.ocr_config_hash != ""
    assert ctx.preview_config_hash != ""


def test_prepare_correction_hash_empty_when_correction_disabled(tmp_path: Path) -> None:
    """
    Given  a config with correction_enabled=False
    When   prepare() is called
    Then   ctx.correction_config_hash is empty string
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"data")

    svc, repo, _ = _make_svc(tmp_path, correction_enabled=False)
    repo.load_or_create.return_value = _make_state()
    repo.reconcile_runtime_config.return_value = _make_state()

    with patch(
        "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
        return_value="h",
    ):
        ctx = svc.prepare(src)

    assert ctx.correction_config_hash == ""


def test_prepare_eval_hash_empty_when_evaluation_disabled(tmp_path: Path) -> None:
    """
    Given  a config with evaluation_enabled=False
    When   prepare() is called
    Then   ctx.eval_config_hash is empty string
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"data")

    svc, repo, _ = _make_svc(tmp_path, evaluation_enabled=False)
    repo.load_or_create.return_value = _make_state()
    repo.reconcile_runtime_config.return_value = _make_state()

    with patch(
        "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
        return_value="h",
    ):
        ctx = svc.prepare(src)

    assert ctx.eval_config_hash == ""


# ── Hash determinism ───────────────────────────────────────────────────────


def test_ocr_config_hash_is_deterministic(tmp_path: Path) -> None:
    """
    Given  the same config object
    When   _ocr_config_hash is called twice with the same prompt
    Then   both calls return the same string
    """
    svc, _, _ = _make_svc(tmp_path)
    h1 = svc._ocr_config_hash("Transcribe.")
    h2 = svc._ocr_config_hash("Transcribe.")
    assert h1 == h2
    assert h1 != ""


def test_ocr_config_hash_changes_with_engine(tmp_path: Path) -> None:
    """
    Given  two services with different ocr_engine values
    When   _ocr_config_hash is called with the same prompt
    Then   the two hashes are different
    """
    svc_a, _, _ = _make_svc(tmp_path, ocr_engine="tesseract")
    svc_b, _, _ = _make_svc(tmp_path, ocr_engine="ollama")
    assert svc_a._ocr_config_hash("p") != svc_b._ocr_config_hash("p")


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_correction_config_hash_independent_of_ocr_engine(tmp_path: Path) -> None:
    """
    Given  two services differing only in ocr_engine (all correction params identical)
    When   _correction_config_hash() is called on each
    Then   the two hashes are equal (OCR engine does not affect correction hash)
    """
    svc_a, _, _ = _make_svc(
        tmp_path, ocr_engine="tesseract", correction_enabled=True, correction_provider="openai"
    )
    svc_b, _, _ = _make_svc(
        tmp_path, ocr_engine="paddleocr", correction_enabled=True, correction_provider="openai"
    )
    assert svc_a._correction_config_hash() == svc_b._correction_config_hash()


def test_ocr_config_hash_independent_of_correction_provider(tmp_path: Path) -> None:
    """
    Given  two services differing only in correction_provider (all OCR params identical)
    When   _ocr_config_hash() is called on each with the same prompt
    Then   the two hashes are equal (correction provider does not affect OCR hash)
    """
    svc_a, _, _ = _make_svc(tmp_path, ocr_engine="tesseract", correction_provider="openai")
    svc_b, _, _ = _make_svc(tmp_path, ocr_engine="tesseract", correction_provider="anthropic")
    assert svc_a._ocr_config_hash("Transcribe.") == svc_b._ocr_config_hash("Transcribe.")


@pytest.mark.use_case("Multi_Provider_Stage_Configuration")
def test_three_stage_hashes_are_all_different_when_providers_differ(tmp_path: Path) -> None:
    """
    Given  ocr_engine='tesseract', correction_provider='openai', evaluate_provider='anthropic'
    When   each stage hash method is called
    Then   all three hashes are non-empty and mutually distinct
    """
    svc, _, _ = _make_svc(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=True,
        correction_provider="openai",
        evaluation_enabled=True,
        evaluate_provider="anthropic",
    )
    ocr_h = svc._ocr_config_hash("Transcribe.")
    corr_h = svc._correction_config_hash()
    eval_h = svc._eval_config_hash()
    assert ocr_h != ""
    assert corr_h != ""
    assert eval_h != ""
    assert ocr_h != corr_h
    assert corr_h != eval_h
    assert ocr_h != eval_h


def test_ocr_config_hash_changes_with_temperature(tmp_path: Path) -> None:
    """
    Given  two services differing only in ocr_temperature
    When   _ocr_config_hash() is called with the same prompt
    Then   the two hashes differ (temperature affects OCR cache invalidation)
    """
    svc_a, _, _ = _make_svc(tmp_path, ocr_temperature=0.0)
    svc_b, _, _ = _make_svc(tmp_path, ocr_temperature=0.7)
    assert svc_a._ocr_config_hash("p") != svc_b._ocr_config_hash("p")


def test_correction_config_hash_changes_with_temperature(tmp_path: Path) -> None:
    """
    Given  two services differing only in correction_temperature
    When   _correction_config_hash() is called on each
    Then   the two hashes differ (temperature affects correction cache invalidation)
    """
    svc_a, _, _ = _make_svc(tmp_path, correction_enabled=True, correction_temperature=0.0)
    svc_b, _, _ = _make_svc(tmp_path, correction_enabled=True, correction_temperature=0.5)
    assert svc_a._correction_config_hash() != svc_b._correction_config_hash()


def test_eval_config_hash_changes_with_temperature(tmp_path: Path) -> None:
    """
    Given  two services differing only in evaluate_temperature
    When   _eval_config_hash() is called on each
    Then   the two hashes differ (temperature affects evaluation cache invalidation)
    """
    svc_a, _, _ = _make_svc(tmp_path, evaluation_enabled=True, evaluate_temperature=0.0)
    svc_b, _, _ = _make_svc(tmp_path, evaluation_enabled=True, evaluate_temperature=0.5)
    assert svc_a._eval_config_hash() != svc_b._eval_config_hash()


def test_correction_config_hash_changes_with_model(tmp_path: Path) -> None:
    """
    Given  two services differing only in correction_model (regression guard)
    When   _correction_config_hash() is called on each
    Then   the two hashes differ — confirms a correction-model change invalidates the cache
    """
    svc_a, _, _ = _make_svc(tmp_path, correction_enabled=True, correction_model="gpt-oss:20b")
    svc_b, _, _ = _make_svc(tmp_path, correction_enabled=True, correction_model="llama3.1:8b")
    assert svc_a._correction_config_hash() != svc_b._correction_config_hash()


# ── record_ocr() ──────────────────────────────────────────────────────────


def test_record_ocr_delegates_to_repo_and_preserves_hashes(tmp_path: Path) -> None:
    """
    Given  a CacheContext and OCR text
    When   record_ocr() is called
    Then   repo.record_ocr_result is called with the correct kwargs and the returned
           CacheContext preserves all hash fields from the original context
    """
    file = tmp_path / "doc.png"
    svc, repo, _ = _make_svc(tmp_path)
    new_state = _make_state()
    repo.record_ocr_result.return_value = new_state

    ctx = _make_ctx()
    result = svc.record_ocr(
        file, ctx, raw_text="text", preview_img="prev.png", source_image="src.png"
    )

    repo.record_ocr_result.assert_called_once_with(
        file,
        ctx.state,
        OcrResultRecord(
            source_hash=ctx.source_hash,
            raw_text="text",
            preview_img="prev.png",
            source_image="src.png",
            ocr_prompt=ctx.ocr_prompt,
            correction_prompt="",
            evaluate_prompt=DEFAULTS["evaluate_prompt"],
            ocr_config_hash=ctx.ocr_config_hash,
            preview_config_hash=ctx.preview_config_hash,
        ),
    )
    assert result.state is new_state
    assert result.ocr_config_hash == ctx.ocr_config_hash
    assert result.correction_config_hash == ctx.correction_config_hash
    assert result.eval_config_hash == ctx.eval_config_hash
    assert result.preview_config_hash == ctx.preview_config_hash


# ── record_correction() ───────────────────────────────────────────────────


def test_record_correction_delegates_to_repo(tmp_path: Path) -> None:
    """
    Given  a CacheContext and correction text
    When   record_correction() is called
    Then   repo.record_correction_result is called, and correction_config_hash is forwarded
    """
    file = tmp_path / "doc.png"
    svc, repo, _ = _make_svc(tmp_path)
    new_state = _make_state()
    repo.record_correction_result.return_value = new_state

    ctx = _make_ctx()
    result = svc.record_correction(file, ctx, correction_text="corrected")

    repo.record_correction_result.assert_called_once_with(
        file,
        ctx.state,
        correction_text="corrected",
        correction_prompt="",  # make_config default
        correction_config_hash=ctx.correction_config_hash,
    )
    assert result.state is new_state
    assert result.correction_config_hash == ctx.correction_config_hash


# ── record_evaluation() ───────────────────────────────────────────────────


def test_record_evaluation_delegates_to_repo(tmp_path: Path) -> None:
    """
    Given  a CacheContext and evaluation text
    When   record_evaluation() is called
    Then   repo.record_evaluation_result is called with correct kwargs
    """
    file = tmp_path / "doc.png"
    svc, repo, _ = _make_svc(tmp_path)
    new_state = _make_state()
    repo.record_evaluation_result.return_value = new_state

    ctx = _make_ctx()
    result = svc.record_evaluation(file, ctx, evaluation_text="quality: good")

    repo.record_evaluation_result.assert_called_once_with(
        file,
        ctx.state,
        evaluation_text="quality: good",
        evaluate_prompt=DEFAULTS["evaluate_prompt"],
        eval_config_hash=ctx.eval_config_hash,
    )
    assert result.state is new_state


# ── persist_preview_artifact() ────────────────────────────────────────────


def test_persist_preview_artifact_copies_and_records(tmp_path: Path) -> None:
    """
    Given  a source image path and a CacheContext
    When   persist_preview_artifact() is called
    Then   artifact_store.persist_preview_image is called with stem and source_path,
           repo.record_preview_artifact is called, and the return value contains the stable path string
    """
    file = tmp_path / "doc.png"
    source_path = tmp_path / "tmp_preview.png"
    stable_path = tmp_path / "artifacts" / "doc_preprocessed.png"

    svc, repo, artifacts = _make_svc(tmp_path)
    new_state = _make_state()
    artifacts.persist_preview_image.return_value = stable_path
    repo.record_preview_artifact.return_value = new_state

    ctx = _make_ctx()
    preview_img, new_ctx = svc.persist_preview_artifact(file, ctx, source_path)

    artifacts.persist_preview_image.assert_called_once_with(stem=file.stem, source_path=source_path)
    repo.record_preview_artifact.assert_called_once_with(
        file,
        ctx.state,
        preview_img=str(stable_path),
        source_image=str(stable_path),
        preview_config_hash=ctx.preview_config_hash,
    )
    assert preview_img == str(stable_path)
    assert new_ctx.state is new_state
    assert new_ctx.preview_config_hash == ctx.preview_config_hash


def test_persist_preview_artifact_preserves_all_hash_fields(tmp_path: Path) -> None:
    """
    Given  a CacheContext with specific hash values
    When   persist_preview_artifact() is called
    Then   all hash fields are forwarded unchanged to the new CacheContext
    """
    file = tmp_path / "doc.png"
    svc, repo, artifacts = _make_svc(tmp_path)
    artifacts.persist_preview_image.return_value = tmp_path / "stable.png"
    repo.record_preview_artifact.return_value = _make_state()

    ctx = _make_ctx()
    _, new_ctx = svc.persist_preview_artifact(file, ctx, tmp_path / "src.png")

    assert new_ctx.source_hash == ctx.source_hash
    assert new_ctx.ocr_prompt == ctx.ocr_prompt
    assert new_ctx.ocr_config_hash == ctx.ocr_config_hash
    assert new_ctx.correction_config_hash == ctx.correction_config_hash
    assert new_ctx.eval_config_hash == ctx.eval_config_hash
    assert new_ctx.preview_config_hash == ctx.preview_config_hash
