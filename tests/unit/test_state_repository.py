"""Unit tests for teachers_teammate.infrastructure.state_repository."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import dataclasses

from teachers_teammate.infrastructure.state_repository import (
    DocumentState,
    OcrResultRecord,
    RuntimeConfigSnapshot,
    StateRepository,
    compute_config_hash,
    compute_file_hash,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _repo(tmp_path: Path) -> StateRepository:
    return StateRepository(tmp_path / "state")


def _source_file(tmp_path: Path, name: str = "doc.txt", content: str = "content") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ── compute_file_hash ──────────────────────────────────────────────────────


def test_compute_file_hash_returns_hex_string(tmp_path: Path) -> None:
    """
    Given  a file with known content
    When   compute_file_hash() is called
    Then   a non-empty hex string is returned
    """
    f = _source_file(tmp_path, content="hello")
    h = compute_file_hash(f)
    assert len(h) == 64  # SHA-256 hex digest
    assert all(c in "0123456789abcdef" for c in h)


def test_compute_file_hash_is_deterministic(tmp_path: Path) -> None:
    """
    Given  the same file read twice
    When   compute_file_hash() is called both times
    Then   both results are identical
    """
    f = _source_file(tmp_path, content="deterministic")
    assert compute_file_hash(f) == compute_file_hash(f)


def test_compute_file_hash_differs_for_different_content(tmp_path: Path) -> None:
    """
    Given  two files with different content
    When   compute_file_hash() is called on each
    Then   the hashes are different
    """
    f1 = _source_file(tmp_path, "a.txt", "alpha")
    f2 = _source_file(tmp_path, "b.txt", "beta")
    assert compute_file_hash(f1) != compute_file_hash(f2)


# ── compute_config_hash ────────────────────────────────────────────────────


def test_compute_config_hash_order_matters() -> None:
    """
    Given  two calls with the same parts in different order
    When   compute_config_hash() is called
    Then   the results differ
    """
    h1 = compute_config_hash("a", "b")
    h2 = compute_config_hash("b", "a")
    assert h1 != h2


# ── load_or_create ─────────────────────────────────────────────────────────


def test_load_or_create_creates_new_state_when_none_exists(tmp_path: Path) -> None:
    """
    Given  a source file with no prior state on disk
    When   load_or_create() is called
    Then   a fresh DocumentState is returned with ocr_done=False
    """
    source = _source_file(tmp_path)
    repo = _repo(tmp_path)
    state = repo.load_or_create(source, "somehash")
    assert state.ocr_done is False
    assert state.raw_text == ""


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_load_or_create_returns_existing_state_after_save(tmp_path: Path) -> None:
    """
    Given  a state has been persisted via record_ocr_result
    When   load_or_create() is called again with the same source
    Then   the persisted state (with raw_text) is returned
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = repo.create(source, source_hash)
    repo.record_ocr_result(
        source,
        state,
        OcrResultRecord(
            source_hash=source_hash,
            raw_text="hello world",
            preview_img="",
            source_image="",
            ocr_prompt="",
            correction_prompt="",
            evaluate_prompt="",
            ocr_config_hash="h1",
            preview_config_hash="",
        ),
    )
    loaded = repo.load_or_create(source, source_hash)
    assert loaded.raw_text == "hello world"
    assert loaded.ocr_done is True


# ── record_ocr_result ─────────────────────────────────────────────────────


def test_record_ocr_result_sets_ocr_done_and_persists(tmp_path: Path) -> None:
    """
    Given  a new state and a source file
    When   record_ocr_result() is called with raw_text
    Then   ocr_done is True, correction_done is False, and the state is persisted to disk
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = repo.create(source, source_hash)

    repo.record_ocr_result(
        source,
        state,
        OcrResultRecord(
            source_hash=source_hash,
            raw_text="extracted text",
            preview_img="",
            source_image="",
            ocr_prompt="",
            correction_prompt="",
            evaluate_prompt="",
            ocr_config_hash="ocr-hash",
            preview_config_hash="",
        ),
    )

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.ocr_done is True
    assert loaded.raw_text == "extracted text"
    assert loaded.correction_done is False


@pytest.mark.use_case("Stage_Specific_Rerun")
def test_record_ocr_result_clears_correction_and_evaluation(tmp_path: Path) -> None:
    """
    Given  a state with correction_done=True and evaluation_done=True
    When   record_ocr_result() is called with new OCR text
    Then   correction_done and evaluation_done are both reset to False
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        correction_done=True,
        evaluation_done=True,
        correction_text="old correction",
        evaluation_text="old eval",
    )

    repo.record_ocr_result(
        source,
        state,
        OcrResultRecord(
            source_hash=source_hash,
            raw_text="new ocr",
            preview_img="",
            source_image="",
            ocr_prompt="",
            correction_prompt="",
            evaluate_prompt="",
            ocr_config_hash="",
            preview_config_hash="",
        ),
    )

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.correction_done is False
    assert loaded.evaluation_done is False
    assert loaded.correction_text == ""
    assert loaded.evaluation_text == ""


# ── record_correction_result ──────────────────────────────────────────────


def test_record_correction_result_sets_correction_done(tmp_path: Path) -> None:
    """
    Given  a state with ocr_done=True
    When   record_correction_result() is called with non-empty correction_text
    Then   correction_done is True and the text is persisted
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(repo.create(source, source_hash), ocr_done=True, raw_text="raw")

    repo.record_correction_result(
        source,
        state,
        correction_text="corrected text",
        correction_prompt="",
        correction_config_hash="c-hash",
    )

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.correction_done is True
    assert loaded.correction_text == "corrected text"
    assert loaded.evaluation_done is False


# ── invalidate_from_stage ─────────────────────────────────────────────────


@pytest.mark.use_case("Stage_Specific_Rerun")
def test_invalidate_from_stage_correction_clears_correction_and_eval(tmp_path: Path) -> None:
    """
    Given  a state with all three stages done
    When   invalidate_from_stage('correction') is called
    Then   correction and evaluation are cleared but OCR data is preserved
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="raw",
        ocr_config_hash="ocr-h",
        correction_done=True,
        correction_text="corrected",
        correction_config_hash="corr-h",
        evaluation_done=True,
        evaluation_text="evaluated",
        eval_config_hash="eval-h",
    )
    repo.save(source, state)

    repo.invalidate_from_stage(source, "correction")

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.ocr_done is True
    assert loaded.raw_text == "raw"
    assert loaded.correction_done is False
    assert loaded.correction_text == ""
    assert loaded.evaluation_done is False
    assert loaded.evaluation_text == ""


@pytest.mark.use_case("Stage_Specific_Rerun")
def test_invalidate_from_stage_ocr_clears_all_stages(tmp_path: Path) -> None:
    """
    Given  a fully processed state
    When   invalidate_from_stage('ocr') is called
    Then   OCR, correction, and evaluation data are all cleared
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="raw",
        correction_done=True,
        correction_text="corrected",
        evaluation_done=True,
        evaluation_text="evaluated",
    )
    repo.save(source, state)

    repo.invalidate_from_stage(source, "ocr")

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.ocr_done is False
    assert loaded.raw_text == ""
    assert loaded.correction_done is False
    assert loaded.evaluation_done is False


def test_invalidate_from_stage_returns_none_when_no_state(tmp_path: Path) -> None:
    """
    Given  a source file with no persisted state
    When   invalidate_from_stage() is called
    Then   None is returned without raising
    """
    source = _source_file(tmp_path)
    repo = _repo(tmp_path)
    result = repo.invalidate_from_stage(source, "ocr")
    assert result is None


# ── load_valid ────────────────────────────────────────────────────────────


def test_load_valid_returns_none_when_no_state_file(tmp_path: Path) -> None:
    """
    Given  a source file with no state persisted to disk
    When   load_valid() is called
    Then   None is returned
    """
    source = _source_file(tmp_path)
    repo = _repo(tmp_path)
    assert repo.load_valid(source) is None


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_load_valid_returns_state_matching_current_file_hash(tmp_path: Path) -> None:
    """
    Given  a state saved with the current file hash
    When   load_valid() is called
    Then   the state is returned
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash), ocr_done=True, raw_text="hello", ocr_config_hash="h1"
    )
    repo.save(source, state)

    loaded = repo.load_valid(source)
    assert loaded is not None
    assert loaded.raw_text == "hello"


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_load_valid_returns_none_when_file_content_changed(tmp_path: Path) -> None:
    """
    Given  a state saved with an old file hash and the source file modified afterward
    When   load_valid() is called
    Then   None is returned (hash mismatch)
    """
    source = _source_file(tmp_path, content="original")
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = repo.create(source, source_hash)
    repo.save(source, state)

    source.write_text("modified content", encoding="utf-8")

    assert repo.load_valid(source) is None


# ── reconcile_runtime_config ──────────────────────────────────────────────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_reconcile_runtime_config_marks_stale_when_ocr_hash_changes(tmp_path: Path) -> None:
    """
    Given  a state with ocr_done=True and a stored ocr_config_hash
    When   reconcile_runtime_config() is called with a different ocr_config_hash
    Then   ocr_done is reset to False (the OCR stage is stale)
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash), ocr_done=True, raw_text="raw", ocr_config_hash="old-hash"
    )

    reconciled = repo.reconcile_runtime_config(
        source,
        state,
        RuntimeConfigSnapshot(
            source_hash=source_hash,
            ocr_prompt="",
            correction_prompt="",
            evaluate_prompt="",
            ocr_config_hash="new-hash",
            correction_config_hash="",
            eval_config_hash="",
            preview_config_hash="",
        ),
    )

    assert reconciled.ocr_done is False


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_reconcile_runtime_config_leaves_state_unchanged_when_hashes_match(tmp_path: Path) -> None:
    """
    Given  a state with all stages done and matching config hashes
    When   reconcile_runtime_config() is called with the same hashes
    Then   all stages remain done (no stale detection triggered)
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="raw",
        ocr_config_hash="ocr-h",
        correction_done=True,
        correction_text="corrected",
        correction_config_hash="corr-h",
        evaluation_done=True,
        evaluation_text="eval",
        eval_config_hash="eval-h",
    )

    reconciled = repo.reconcile_runtime_config(
        source,
        state,
        RuntimeConfigSnapshot(
            source_hash=source_hash,
            ocr_prompt="",
            correction_prompt="",
            evaluate_prompt="",
            ocr_config_hash="ocr-h",
            correction_config_hash="corr-h",
            eval_config_hash="eval-h",
            preview_config_hash="",
        ),
    )

    assert reconciled.ocr_done is True
    assert reconciled.correction_done is True
    assert reconciled.evaluation_done is True


# ── invalidate_from_stage (evaluation) ────────────────────────────────────


@pytest.mark.use_case("Stage_Specific_Rerun")
def test_invalidate_from_stage_evaluation_clears_only_eval(tmp_path: Path) -> None:
    """
    Given  a fully processed state with all three stages done
    When   invalidate_from_stage('evaluation') is called
    Then   evaluation_done=False but ocr_done and correction_done remain True
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="raw",
        ocr_config_hash="ocr-h",
        correction_done=True,
        correction_text="corrected",
        correction_config_hash="corr-h",
        evaluation_done=True,
        evaluation_text="evaluated",
        eval_config_hash="eval-h",
    )
    repo.save(source, state)

    repo.invalidate_from_stage(source, "evaluation")

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.ocr_done is True
    assert loaded.raw_text == "raw"
    assert loaded.correction_done is True
    assert loaded.correction_text == "corrected"
    assert loaded.evaluation_done is False
    assert loaded.evaluation_text == ""


def test_invalidate_from_stage_invalid_stage_raises_value_error(tmp_path: Path) -> None:
    """
    Given  a persisted state
    When   invalidate_from_stage() is called with an unrecognised stage name
    Then   ValueError is raised
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(repo.create(source, source_hash), ocr_done=True, raw_text="raw")
    repo.save(source, state)

    with pytest.raises(ValueError, match="Invalid stage"):
        repo.invalidate_from_stage(source, "nonexistent")


@pytest.mark.use_case("Manual_Evaluation_On_Demand")
def test_invalidate_from_stage_evaluation_preserves_ocr_and_correction_text(tmp_path: Path) -> None:
    """
    Given  a state with raw_text="ocr" and correction_text="fixed" and all stages done
    When   invalidate_from_stage('evaluation') is called
    Then   raw_text and correction_text are unchanged after invalidation
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="ocr",
        ocr_config_hash="ocr-h",
        correction_done=True,
        correction_text="fixed",
        correction_config_hash="corr-h",
        evaluation_done=True,
        evaluation_text="evaluated",
        eval_config_hash="eval-h",
    )
    repo.save(source, state)

    repo.invalidate_from_stage(source, "evaluation")

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.raw_text == "ocr"
    assert loaded.correction_text == "fixed"


# ── record_manual_ocr_edit ────────────────────────────────────────────────


@pytest.mark.use_case("Manual_Text_Editing")
def test_record_manual_ocr_edit_invalidates_correction_and_evaluation(tmp_path: Path) -> None:
    """
    Given  a fully processed state
    When   record_manual_ocr_edit() is called with new_text
    Then   correction_done=False, evaluation_done=False, ocr_done=True, raw_text=new_text
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="old ocr",
        correction_done=True,
        correction_text="corrected",
        evaluation_done=True,
        evaluation_text="evaluated",
    )

    repo.record_manual_ocr_edit(source, state, raw_text="new text", preview_img="")

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.ocr_done is True
    assert loaded.raw_text == "new text"
    assert loaded.correction_done is False
    assert loaded.correction_text == ""
    assert loaded.evaluation_done is False
    assert loaded.evaluation_text == ""


@pytest.mark.use_case("Manual_Text_Editing")
def test_record_manual_ocr_edit_clears_downstream_config_hashes(tmp_path: Path) -> None:
    """
    Given  a state with ocr_config_hash, correction_config_hash, and eval_config_hash set
    When   record_manual_ocr_edit() is called
    Then   all three config hashes are cleared to empty strings
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_config_hash="ocr-h",
        correction_config_hash="corr-h",
        eval_config_hash="eval-h",
    )

    repo.record_manual_ocr_edit(source, state, raw_text="some text", preview_img="")

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.ocr_config_hash == ""
    assert loaded.correction_config_hash == ""
    assert loaded.eval_config_hash == ""


def test_record_manual_ocr_edit_with_empty_text(tmp_path: Path) -> None:
    """
    Given  an existing state with OCR text
    When   record_manual_ocr_edit() is called with empty raw_text
    Then   raw_text="" and ocr_done=False (no valid OCR result)
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash), ocr_done=True, raw_text="something"
    )

    repo.record_manual_ocr_edit(source, state, raw_text="", preview_img="")

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.raw_text == ""
    assert loaded.ocr_done is False


# ── record_manual_correction_edit ─────────────────────────────────────────


@pytest.mark.use_case("Manual_Text_Editing")
def test_record_manual_correction_edit_invalidates_evaluation_preserves_ocr(tmp_path: Path) -> None:
    """
    Given  a fully processed state
    When   record_manual_correction_edit() is called with new correction text
    Then   evaluation_done=False but ocr_done=True and raw_text is unchanged
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="raw ocr",
        correction_done=True,
        correction_text="old correction",
        evaluation_done=True,
        evaluation_text="evaluated",
    )

    repo.record_manual_correction_edit(source, state, correction_text="new correction")

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.ocr_done is True
    assert loaded.raw_text == "raw ocr"
    assert loaded.correction_done is True
    assert loaded.correction_text == "new correction"
    assert loaded.evaluation_done is False
    assert loaded.evaluation_text == ""


def test_record_manual_correction_edit_with_empty_text(tmp_path: Path) -> None:
    """
    Given  a state with correction_done=True
    When   record_manual_correction_edit() is called with empty correction_text
    Then   correction_done=False
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="raw",
        correction_done=True,
        correction_text="corrected",
    )

    repo.record_manual_correction_edit(source, state, correction_text="")

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.correction_done is False
    assert loaded.correction_text == ""


# ── schema version ────────────────────────────────────────────────────────


def test_load_returns_none_when_schema_version_mismatches(tmp_path: Path) -> None:
    """
    Given  a JSON state file on disk with schema_version=1 (outdated)
    When   load() is called
    Then   None is returned (version mismatch)
    """
    source = _source_file(tmp_path)
    repo = _repo(tmp_path)
    stale = {
        "schema_version": 1,
        "source_path": str(source.resolve()),
        "source_hash": "some-hash",
    }
    repo.state_path_for_input(source).parent.mkdir(parents=True, exist_ok=True)
    repo.state_path_for_input(source).write_text(json.dumps(stale), encoding="utf-8")

    assert repo.load(source) is None


def test_load_or_create_returns_fresh_state_on_schema_version_mismatch(tmp_path: Path) -> None:
    """
    Given  a state file with an outdated schema_version on disk
    When   load_or_create() is called
    Then   a fresh DocumentState (ocr_done=False) is returned rather than the stale one
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    stale = {
        "schema_version": 1,
        "source_path": str(source.resolve()),
        "source_hash": source_hash,
        "raw_text": "stale ocr text",
        "ocr_done": True,
    }
    repo.state_path_for_input(source).parent.mkdir(parents=True, exist_ok=True)
    repo.state_path_for_input(source).write_text(json.dumps(stale), encoding="utf-8")

    state = repo.load_or_create(source, source_hash)

    assert state.ocr_done is False
    assert state.raw_text == ""


# ── original image + preprocessing steps ───────────────────────────────────


def test_record_preview_artifact_persists_original_and_steps(tmp_path: Path) -> None:
    """
    Given  a repository and a fresh state
    When   record_preview_artifact stores an original image and steps
    Then   they survive a save/load round trip and appear on the UI view
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    preview = tmp_path / "doc_preprocessed.png"
    original = tmp_path / "doc_original.png"
    preview.write_bytes(b"p")
    original.write_bytes(b"o")
    state = repo.create(src, compute_file_hash(src))

    repo.record_preview_artifact(
        src,
        state,
        preview_img=str(preview),
        source_image=str(preview),
        preview_config_hash="ph",
        original_img=str(original),
        preprocess_steps=["grayscale", "deskew"],
    )

    loaded = repo.load_valid(src)
    assert loaded is not None
    assert loaded.original_img == str(original)
    assert loaded.preprocess_steps == ["grayscale", "deskew"]
    view = repo.to_view(src, loaded)
    assert view.original_img == str(original)
    assert view.preprocess_steps == ("grayscale", "deskew")


def test_state_without_preprocess_info_loads_with_unknown_steps(tmp_path: Path) -> None:
    """
    Given  a state file written before original_img/preprocess_steps existed
    When   it is loaded
    Then   original_img is empty and preprocess_steps is None (unknown, not "none applied")
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = repo.create(src, compute_file_hash(src))
    data = dataclasses.asdict(state)
    del data["original_img"]
    del data["preprocess_steps"]
    repo.state_path_for_input(src).write_text(json.dumps(data), encoding="utf-8")

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.original_img == ""
    assert loaded.preprocess_steps is None
    assert repo.to_view(src, loaded).preprocess_steps is None


def test_invalidate_preview_artifact_clears_original_and_steps(tmp_path: Path) -> None:
    """
    Given  a state with preview, original image and steps
    When   invalidate_preview_artifact is called
    Then   original_img and preprocess_steps are reset together with the preview paths
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = dataclasses.replace(
        repo.create(src, compute_file_hash(src)),
        preview_img="p.png",
        source_image="p.png",
        original_img="o.png",
        preprocess_steps=["grayscale"],
    )

    cleared = repo.invalidate_preview_artifact(src, state)

    assert cleared.original_img == ""
    assert cleared.preprocess_steps is None
    assert cleared.preview_img == ""


def test_invalidate_from_stage_ocr_clears_original_and_steps(tmp_path: Path) -> None:
    """
    Given  a state with OCR text, an original image and steps
    When   invalidate_from_stage("ocr") runs
    Then   original_img and preprocess_steps are cleared
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    repo.save(
        src,
        dataclasses.replace(
            repo.create(src, compute_file_hash(src)),
            raw_text="text",
            ocr_done=True,
            original_img="o.png",
            preprocess_steps=["grayscale"],
        ),
    )

    cleared = repo.invalidate_from_stage(src, "ocr")

    assert cleared is not None
    assert cleared.original_img == ""
    assert cleared.preprocess_steps is None


def test_load_valid_drops_original_image_missing_on_disk(tmp_path: Path) -> None:
    """
    Given  a saved state with an existing preview whose original_img file no longer exists
    When   load_valid is called
    Then   original_img is reset to "" while the steps are kept
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    preview = tmp_path / "doc_preprocessed.png"
    preview.write_bytes(b"png")
    repo.save(
        src,
        dataclasses.replace(
            repo.create(src, compute_file_hash(src)),
            preview_img=str(preview),
            original_img=str(tmp_path / "gone.png"),
            preprocess_steps=["grayscale"],
        ),
    )

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.original_img == ""
    assert loaded.preprocess_steps == ["grayscale"]


def test_load_valid_drops_original_and_steps_when_preview_missing(tmp_path: Path) -> None:
    """
    Given  a saved state whose preview image is gone but original_img and steps are recorded
    When   load_valid is called
    Then   original_img and preprocess_steps are cleared together with preview_img
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    original = tmp_path / "doc_original.png"
    original.write_bytes(b"png")
    repo.save(
        src,
        dataclasses.replace(
            repo.create(src, compute_file_hash(src)),
            preview_img=str(tmp_path / "gone_preprocessed.png"),
            original_img=str(original),
            preprocess_steps=["grayscale"],
        ),
    )

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.preview_img == ""
    assert loaded.original_img == ""
    assert loaded.preprocess_steps is None


# ── Text notation (raw_text_format) ───────────────────────────────────────


def _ocr_record(source_hash: str, **overrides: str) -> OcrResultRecord:
    fields = {
        "source_hash": source_hash,
        "raw_text": "**x**",
        "preview_img": "",
        "source_image": "",
        "ocr_prompt": "",
        "correction_prompt": "",
        "evaluate_prompt": "",
        "ocr_config_hash": "ocr-hash",
        "preview_config_hash": "",
    }
    fields.update(overrides)
    return OcrResultRecord(**fields)


def _write_legacy_state(repo: StateRepository, src: Path, raw_text: str) -> None:
    """Write a state JSON without raw_text_format, as older versions did."""
    state = repo.create(src, compute_file_hash(src))
    legacy = dataclasses.asdict(
        dataclasses.replace(state, raw_text=raw_text, ocr_done=True, correction_done=False)
    )
    del legacy["raw_text_format"]
    repo.state_path_for_input(src).write_text(json.dumps(legacy), encoding="utf-8")


def test_plain_state_written_before_text_format_fields_loads_as_plain(tmp_path: Path) -> None:
    """
    Given  a state JSON on disk that lacks raw_text_format and holds ordinary plain text
    When   load_valid() and load_view() are called
    Then   it still loads, and the text is treated as plain
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    _write_legacy_state(repo, src, "Das ist ein Satz.\nUnd noch einer.")

    loaded = repo.load_valid(src)
    view = repo.load_view(src)

    assert loaded is not None
    assert loaded.raw_text_format == "plain"
    assert view is not None
    assert view.raw_text_format == "plain"


@pytest.mark.parametrize(
    "markup",
    ["**fett** und ~~weg~~", '<div data-bbox="0 0 1 1" data-label="Text"><p>Hallo</p></div>'],
)
def test_markup_state_written_before_text_format_fields_is_dropped(
    tmp_path: Path, markup: str
) -> None:
    """
    Given  a state JSON without raw_text_format whose OCR text is raw Markdown / layout HTML
    When   load_valid() and load_view() are called
    Then   no state is returned, so the document is processed again instead of showing the
           markup as literal text
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    _write_legacy_state(repo, src, markup)

    assert repo.load_valid(src) is None
    assert repo.load_view(src) is None


def test_markup_state_with_text_format_field_is_kept(tmp_path: Path) -> None:
    """
    Given  a current state JSON (with raw_text_format) whose plain text contains "**"
    When   load_valid() is called
    Then   the state is kept: only states without the field are treated as legacy
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = repo.create(src, compute_file_hash(src))
    repo.save(
        src, dataclasses.replace(state, raw_text="**x**", raw_text_format="plain", ocr_done=True)
    )

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.raw_text == "**x**"


def test_state_with_fields_dropped_by_newer_versions_still_loads(tmp_path: Path) -> None:
    """
    Given  a state JSON that still carries the removed ocr_source_output / correction_text_format
    When   load_valid() is called
    Then   the unknown keys are ignored and the cached texts are kept
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = repo.create(src, compute_file_hash(src))
    payload = dataclasses.asdict(
        dataclasses.replace(state, raw_text="**x**", raw_text_format="markdown", ocr_done=True)
    )
    payload["ocr_source_output"] = "<b>x</b>"
    payload["correction_text_format"] = "markdown"
    repo.state_path_for_input(src).write_text(json.dumps(payload), encoding="utf-8")

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert (loaded.raw_text, loaded.raw_text_format) == ("**x**", "markdown")


def test_record_ocr_result_persists_the_text_format(tmp_path: Path) -> None:
    """
    Given  an OCR record whose text was parsed into Markdown from layout HTML
    When   record_ocr_result() is called and the state reloaded
    Then   raw_text_format is persisted and exposed on the view
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = repo.create(src, compute_file_hash(src))

    repo.record_ocr_result(
        src, state, _ocr_record(compute_file_hash(src), raw_text_format="markdown")
    )

    loaded = repo.load_valid(src)
    view = repo.load_view(src)
    assert loaded is not None
    assert loaded.raw_text_format == "markdown"
    assert view is not None
    assert view.raw_text_format == "markdown"


def test_correction_shares_the_raw_text_format_on_the_view(tmp_path: Path) -> None:
    """
    Given  a state whose OCR text is Markdown
    When   record_correction_result() is called and the view loaded
    Then   the view reports the correction in the same notation
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = dataclasses.replace(
        repo.create(src, compute_file_hash(src)),
        raw_text="~~a~~",
        raw_text_format="markdown",
        ocr_done=True,
    )

    repo.record_correction_result(
        src,
        state,
        correction_text="~~a~~ fixed",
        correction_prompt="",
        correction_config_hash="c",
    )

    view = repo.load_view(src)
    assert view is not None
    assert view.correction_text == "~~a~~ fixed"
    assert view.raw_text_format == "markdown"


def test_manual_ocr_edit_keeps_the_text_format(tmp_path: Path) -> None:
    """
    Given  a state with Markdown OCR text
    When   the user edits the text (Source view) and record_manual_ocr_edit() is called
    Then   the edited text stays flagged as Markdown
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = dataclasses.replace(
        repo.create(src, compute_file_hash(src)),
        raw_text="**x**",
        raw_text_format="markdown",
        ocr_done=True,
    )

    updated = repo.record_manual_ocr_edit(src, state, raw_text="**y**", preview_img="")

    assert updated.raw_text == "**y**"
    assert updated.raw_text_format == "markdown"


def test_manual_ocr_edit_stores_the_notation_it_is_written_in(tmp_path: Path) -> None:
    """
    Given  a fresh (plain) state and an edit written in Markdown
    When   record_manual_ocr_edit() is called with raw_text_format="markdown"
    Then   the state is flagged Markdown, so the preview does not flip to the source view
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = repo.create(src, compute_file_hash(src))

    updated = repo.record_manual_ocr_edit(
        src, state, raw_text="**y**", preview_img="", raw_text_format="markdown"
    )

    assert updated.raw_text_format == "markdown"


def test_reset_from_ocr_stage_returns_the_format_to_plain(tmp_path: Path) -> None:
    """
    Given  a state with Markdown OCR text and a Markdown correction
    When   the state is reset from the OCR stage
    Then   the format returns to plain and the texts are cleared
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = dataclasses.replace(
        repo.create(src, compute_file_hash(src)),
        raw_text="**x**",
        raw_text_format="markdown",
        correction_text="**x!**",
        ocr_done=True,
        correction_done=True,
    )

    cleared = repo.reset_from_stage(src, state, "ocr")

    assert cleared.raw_text_format == "plain"
    assert (cleared.raw_text, cleared.correction_text) == ("", "")
