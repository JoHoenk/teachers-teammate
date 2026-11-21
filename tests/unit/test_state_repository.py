"""Unit tests for teachers_teammate.infrastructure.state_repository."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import dataclasses

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote
from teachers_teammate.domain.data_location import STAGE_CORRECTION, STAGE_EVALUATION, SentRecord
from teachers_teammate.application.document_view import to_view
from teachers_teammate.domain.freshness import StageHashes, first_stale_stage_for_hashes
from teachers_teammate.domain.stages import STAGE_ORDER
from teachers_teammate.infrastructure.state_repository import (
    DocumentState,
    OcrResultRecord,
    StateRepository,
    compute_config_hash,
    compute_file_hash,
)
from teachers_teammate.infrastructure.storage_root import document_stem


# ── Helpers ────────────────────────────────────────────────────────────────


def _repo(tmp_path: Path) -> StateRepository:
    return StateRepository(tmp_path / "state")


def _source_file(tmp_path: Path, name: str = "doc.txt", content: str = "content") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _sent(provider: str = "ollama", names_hidden: int = 2) -> SentRecord:
    return SentRecord(
        provider=provider,
        model="qwen3.5:9b",
        endpoint="http://127.0.0.1:11434",
        names_hidden=names_hidden,
        hiding_on=names_hidden > 0,
        privacy="privacy-hash",
    )


def _processed_state(repo: StateRepository, source: Path, source_hash: str) -> DocumentState:
    """Return a state with both text stages done and a sent record for each."""
    return dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="raw",
        correction_done=True,
        correction_text="corrected",
        correction_sent_text="raw with <PERSON_1>",
        correction_sent_map={"<PERSON_1>": "Anna"},
        correction_sent=_sent().to_dict(),
        evaluation_done=True,
        evaluation_text="evaluated",
        evaluation_sent=_sent(names_hidden=0).to_dict(),
    )


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


# ── reconcile ─────────────────────────────────────────────────────────────


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_reconcile_marks_stale_when_ocr_hash_changes(tmp_path: Path) -> None:
    """
    Given  a state with ocr_done=True and a stored ocr_config_hash
    When   reconcile() is called with a stale reading
    Then   ocr_done is reset to False (the OCR stage is stale)
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash), ocr_done=True, raw_text="raw", ocr_config_hash="old-hash"
    )

    reconciled = _reconcile(repo, source, state, expected_ocr_config_hash="new-hash")

    assert reconciled.ocr_done is False


def test_reconcile_clears_every_stage_after_the_stale_one(tmp_path: Path) -> None:
    """
    Given  a state with all three stages stored and a stale reading
    When   reconcile() is called
    Then   the proofreading and the review go with the reading: no later stage outlives it
    """
    source = _source_file(tmp_path)
    repo = _repo(tmp_path)
    state = _processed_state(repo, source, compute_file_hash(source))
    state = dataclasses.replace(state, ocr_config_hash="old-hash", eval_config_hash="eval-h")

    reconciled = _reconcile(repo, source, state, expected_ocr_config_hash="new-hash")

    assert not reconciled.ocr_done
    assert not reconciled.correction_done
    assert reconciled.correction_text == ""
    assert not reconciled.evaluation_done
    assert reconciled.evaluation_text == ""
    assert reconciled.evaluation_sent is None


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_reconcile_leaves_state_unchanged_when_hashes_match(tmp_path: Path) -> None:
    """
    Given  a state with all stages done and matching config hashes
    When   reconcile() is called with nothing stale
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

    reconciled = _reconcile(repo, source, state)

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


# ── How a stored result was sent ──────────────────────────────────────────


def test_record_correction_result_stores_the_text_that_was_sent(tmp_path: Path) -> None:
    """
    Given  a state whose text was recognised
    When   record_correction_result() is called with the sent text, its map and the record
    Then   all three are stored with the proofreading and the view reports how it was sent
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(repo.create(source, source_hash), ocr_done=True, raw_text="raw")

    repo.record_correction_result(
        source,
        state,
        correction_text="corrected",
        correction_config_hash="c-hash",
        sent_text="raw with <PERSON_1>",
        sent_map={"<PERSON_1>": "Anna"},
        sent=_sent(),
    )

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.correction_sent_text == "raw with <PERSON_1>"
    assert loaded.correction_sent_map == {"<PERSON_1>": "Anna"}
    assert loaded.correction_sent == _sent().to_dict()

    view = to_view(source, loaded)
    assert view is not None
    assert view.sent[STAGE_CORRECTION].record == _sent()
    assert view.sent[STAGE_CORRECTION].other_privacy_settings is False


def test_record_evaluation_result_stores_how_it_was_sent(tmp_path: Path) -> None:
    """
    Given  a corrected state
    When   record_evaluation_result() is called with a sent record
    Then   the record is stored with the review and the view reports it
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

    repo.record_evaluation_result(
        source,
        state,
        evaluation_text="evaluated",
        eval_config_hash="e-hash",
        sent=_sent(names_hidden=0),
    )

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.evaluation_sent == _sent(names_hidden=0).to_dict()

    view = to_view(source, loaded)
    assert view is not None
    assert view.sent[STAGE_EVALUATION].record == _sent(names_hidden=0)


def test_a_state_without_a_sent_record_reports_none(tmp_path: Path) -> None:
    """
    Given  a state whose results were stored before the record existed
    When   the view is built
    Then   it reports no sent status for either stage, so the window says nothing
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

    view = to_view(source, state)

    assert view is not None
    assert view.sent == {}


@pytest.mark.use_case("Stage_Specific_Rerun")
def test_record_ocr_result_clears_the_sent_records(tmp_path: Path) -> None:
    """
    Given  a fully processed state with a sent record for both text stages
    When   record_ocr_result() is called with new OCR text
    Then   the sent text, its map and both records are gone with the results
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = _processed_state(repo, source, source_hash)

    repo.record_ocr_result(
        source,
        state,
        OcrResultRecord(
            source_hash=source_hash,
            raw_text="new ocr",
            preview_img="",
            source_image="",
            ocr_config_hash="",
            preview_config_hash="",
        ),
    )

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.correction_sent_text == ""
    assert loaded.correction_sent_map is None
    assert loaded.correction_sent is None
    assert loaded.evaluation_sent is None


@pytest.mark.use_case("Stage_Specific_Rerun")
def test_invalidate_from_stage_correction_clears_the_sent_records(tmp_path: Path) -> None:
    """
    Given  a fully processed state with a sent record for both text stages
    When   invalidate_from_stage('correction') is called
    Then   the sent text, its map and both records are gone with the results
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    repo.save(source, _processed_state(repo, source, source_hash))

    repo.invalidate_from_stage(source, "correction")

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.correction_sent_text == ""
    assert loaded.correction_sent_map is None
    assert loaded.correction_sent is None
    assert loaded.evaluation_sent is None


def test_record_correction_result_clears_the_review_sent_record(tmp_path: Path) -> None:
    """
    Given  a fully processed state with a sent record for both text stages
    When   a new proofreading result is recorded
    Then   the review's record is gone with the review, and the new one is stored
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = _processed_state(repo, source, source_hash)

    repo.record_correction_result(
        source,
        state,
        correction_text="corrected again",
        correction_config_hash="c-hash",
        sent=_sent(names_hidden=0),
    )

    loaded = repo.load(source)
    assert loaded is not None
    assert loaded.correction_sent == _sent(names_hidden=0).to_dict()
    assert loaded.evaluation_sent is None


@pytest.mark.parametrize(
    "field",
    ["correction_sent", "evaluation_sent"],
    ids=["proofreading", "review"],
)
def test_load_valid_drops_a_sent_record_without_its_result_and_keeps_the_rest(
    tmp_path: Path, field: str
) -> None:
    """
    Given  a state that carries a sent record for a stage whose result is missing, next to
           recognised text
    When   load_valid() is called
    Then   the state is still served (the recognised text is not read again) without the
           orphan record
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="raw",
        **{field: _sent().to_dict()},
    )
    repo.save(source, state)

    loaded = repo.load_valid(source)

    assert loaded is not None
    assert loaded.ocr_done is True
    assert loaded.raw_text == "raw"
    assert getattr(loaded, field) is None


def test_load_valid_drops_the_sent_text_without_its_result_and_keeps_the_rest(
    tmp_path: Path,
) -> None:
    """
    Given  a state that carries the text a proofreading was made from, but no proofreading
    When   load_valid() is called
    Then   the text and its map are dropped, because they belong to a result that is not
           there, and the recognised text stays
    """
    source = _source_file(tmp_path)
    source_hash = compute_file_hash(source)
    repo = _repo(tmp_path)
    state = dataclasses.replace(
        repo.create(source, source_hash),
        ocr_done=True,
        raw_text="raw",
        correction_sent_text="raw with <PERSON_1>",
        correction_sent_map={"<PERSON_1>": "Anna"},
    )
    repo.save(source, state)

    loaded = repo.load_valid(source)

    assert loaded is not None
    assert loaded.raw_text == "raw"
    assert loaded.correction_sent_text == ""
    assert loaded.correction_sent_map is None


def test_the_view_has_no_sent_line_for_a_stage_that_is_not_done(tmp_path: Path) -> None:
    """
    Given  a state with both results and records, of which the caller marks the review stale
           (evaluation_done turned off on top of the loaded state)
    When   to_view() is called
    Then   the proofreading keeps its line and the review, which has no result now, has none
    """
    source = _source_file(tmp_path)
    repo = _repo(tmp_path)
    state = _processed_state(repo, source, compute_file_hash(source))

    view = to_view(source, dataclasses.replace(state, evaluation_done=False))

    assert set(view.sent) == {STAGE_CORRECTION}


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


def test_a_state_of_the_previous_schema_loads_as_none(tmp_path: Path) -> None:
    """
    Given  a JSON state file on disk with schema_version=2, the one before the stored review
           changed its offsets
    When   load() is called
    Then   None is returned, so the document is read again instead of showing a review whose
           offsets belong to another text
    """
    source = _source_file(tmp_path)
    repo = _repo(tmp_path)
    stale = {
        "schema_version": 2,
        "source_path": str(source.resolve()),
        "source_hash": "some-hash",
        "ocr_done": True,
        "raw_text": "stale ocr text",
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
        original_images=[str(original)],
        preprocess_steps=["grayscale", "deskew"],
    )

    loaded = repo.load_valid(src)
    assert loaded is not None
    assert loaded.original_img == str(original)
    assert loaded.preprocess_steps == ["grayscale", "deskew"]
    view = to_view(src, loaded)
    assert view.original_img == str(original)
    assert view.preprocess_steps == ("grayscale", "deskew")


def test_state_without_preprocess_info_loads_with_unknown_steps(tmp_path: Path) -> None:
    """
    Given  a state file written before the original pages and the steps existed
    When   it is loaded
    Then   there are no originals and preprocess_steps is None (unknown, not "none applied")
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = repo.create(src, compute_file_hash(src))
    data = dataclasses.asdict(state)
    del data["original_images"]
    del data["preprocess_steps"]
    repo.state_path_for_input(src).write_text(json.dumps(data), encoding="utf-8")

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.original_img == ""
    assert loaded.preprocess_steps is None
    assert to_view(src, loaded).preprocess_steps is None


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
        page_images=["p.png"],
        source_image="p.png",
        original_images=["o.png"],
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
            original_images=["o.png"],
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
            page_images=[str(preview)],
            original_images=[str(tmp_path / "gone.png")],
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
            page_images=[str(tmp_path / "gone_preprocessed.png")],
            original_images=[str(original)],
            preprocess_steps=["grayscale"],
        ),
    )

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.preview_img == ""
    assert loaded.original_img == ""
    assert loaded.preprocess_steps is None


# ── every prepared page (page_images) ─────────────────────────────────────


def _pages(tmp_path: Path, count: int = 3) -> list[Path]:
    """Write *count* page files and return their paths."""
    pages = []
    for number in range(1, count + 1):
        page = tmp_path / ("doc_preprocessed.png" if number == 1 else f"doc_p{number}.png")
        page.write_bytes(b"png")
        pages.append(page)
    return pages


def test_record_preview_artifact_persists_every_page(tmp_path: Path) -> None:
    """
    Given  a repository and a fresh state
    When   record_preview_artifact stores three pages
    Then   they survive a save/load round trip and appear on the UI view in order
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    state = repo.create(src, compute_file_hash(src))

    repo.record_preview_artifact(
        src,
        state,
        preview_img=str(pages[0]),
        source_image=str(pages[0]),
        preview_config_hash="ph",
        page_images=[str(page) for page in pages],
    )

    loaded = repo.load_valid(src)
    assert loaded is not None
    assert loaded.page_images == [str(page) for page in pages]
    assert loaded.pages == tuple(str(page) for page in pages)
    assert to_view(src, loaded).page_images == tuple(str(page) for page in pages)


def test_record_preview_artifact_without_pages_replaces_the_stored_pages(tmp_path: Path) -> None:
    """
    Given  a document with three stored pages
    When   record_preview_artifact is called with a preview image and no page list
    Then   the document has that one page, not the new one on top of two old ones
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    state = dataclasses.replace(
        repo.create(src, compute_file_hash(src)), page_images=[str(page) for page in pages]
    )

    new_state = repo.record_preview_artifact(
        src,
        state,
        preview_img="new.png",
        source_image="new.png",
        preview_config_hash="ph",
    )

    assert new_state.page_images == ["new.png"]


def test_a_record_with_only_a_preview_shows_its_page(tmp_path: Path) -> None:
    """
    Given  a state file written before the app kept every page: one preview_img, no page list
    When   it is loaded
    Then   the document has the one page and the one original it had
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    preview = tmp_path / "doc_preprocessed.png"
    preview.write_bytes(b"png")
    data = dataclasses.asdict(repo.create(src, compute_file_hash(src)))
    data["preview_img"] = str(preview)
    data["original_img"] = str(preview)
    repo.state_path_for_input(src).write_text(json.dumps(data), encoding="utf-8")

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.page_images == [str(preview)]
    assert loaded.pages == (str(preview),)
    assert loaded.original_images == [str(preview)]
    assert to_view(src, loaded).page_images == (str(preview),)


def test_a_page_missing_on_disk_leaves_the_one_page_it_had(tmp_path: Path) -> None:
    """
    Given  a state whose second page file is gone
    When   load_valid is called
    Then   the pages are dropped and the document has its first page
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    repo.save(
        src,
        dataclasses.replace(
            repo.create(src, compute_file_hash(src)),
            page_images=[str(page) for page in pages],
        ),
    )
    pages[1].unlink()

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.page_images == [str(pages[0])]
    assert loaded.pages == (str(pages[0]),)


def test_a_missing_preview_leaves_no_pages(tmp_path: Path) -> None:
    """
    Given  a state whose preview image is gone
    When   load_valid is called
    Then   pages is empty
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    repo.save(
        src,
        dataclasses.replace(
            repo.create(src, compute_file_hash(src)),
            page_images=[str(page) for page in pages],
        ),
    )
    pages[0].unlink()

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.pages == ()


def test_invalidate_preview_artifact_clears_the_pages(tmp_path: Path) -> None:
    """
    Given  a state with three pages
    When   invalidate_preview_artifact is called
    Then   the pages are cleared with the preview paths
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    state = dataclasses.replace(
        repo.create(src, compute_file_hash(src)),
        source_image=str(pages[0]),
        page_images=[str(page) for page in pages],
    )

    cleared = repo.invalidate_preview_artifact(src, state)

    assert cleared.page_images == []


def test_resetting_from_ocr_clears_the_pages(tmp_path: Path) -> None:
    """
    Given  a state with OCR text and three pages
    When   invalidate_from_stage("ocr") runs
    Then   the pages are cleared with the preview paths
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    repo.save(
        src,
        dataclasses.replace(
            repo.create(src, compute_file_hash(src)),
            raw_text="text",
            ocr_done=True,
            page_images=[str(page) for page in pages],
        ),
    )

    cleared = repo.invalidate_from_stage(src, "ocr")

    assert cleared is not None
    assert cleared.page_images == []


def test_a_new_ocr_result_keeps_the_pages(tmp_path: Path) -> None:
    """
    Given  a state with three pages
    When   record_ocr_result stores the text of the same pages
    Then   the pages are kept
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    state = dataclasses.replace(
        repo.create(src, compute_file_hash(src)),
        page_images=[str(page) for page in pages],
    )

    recorded = repo.record_ocr_result(
        src,
        state,
        OcrResultRecord(
            source_hash=compute_file_hash(src),
            raw_text="text",
            preview_img=str(pages[0]),
            source_image=str(pages[0]),
            ocr_config_hash="ocr-hash",
            preview_config_hash="ph",
        ),
    )

    assert recorded.page_images == [str(page) for page in pages]


# ── the original of every page (original_images) ──────────────────────────


def _originals(tmp_path: Path, count: int = 3) -> list[Path]:
    """Write *count* original page files and return their paths."""
    originals = []
    for number in range(1, count + 1):
        name = "doc_original.png" if number == 1 else f"doc_o{number}.png"
        original = tmp_path / name
        original.write_bytes(b"png")
        originals.append(original)
    return originals


def test_record_preview_artifact_persists_every_original(tmp_path: Path) -> None:
    """
    Given  a repository and a fresh state
    When   record_preview_artifact stores three originals
    Then   they survive a save/load round trip and appear on the UI view in order
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    originals = _originals(tmp_path)
    state = repo.create(src, compute_file_hash(src))

    repo.record_preview_artifact(
        src,
        state,
        preview_img=str(pages[0]),
        source_image=str(pages[0]),
        preview_config_hash="ph",
        page_images=[str(page) for page in pages],
        original_images=[str(page) for page in originals],
    )

    loaded = repo.load_valid(src)
    assert loaded is not None
    assert loaded.original_images == [str(page) for page in originals]
    assert loaded.originals == tuple(str(page) for page in originals)
    assert to_view(src, loaded).original_images == tuple(str(page) for page in originals)


def test_state_without_originals_has_the_one_original_it_had(tmp_path: Path) -> None:
    """
    Given  a state file written before the app kept every original: one original_img
    When   it is loaded
    Then   the document has the one original it had
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    original = tmp_path / "doc_original.png"
    original.write_bytes(b"png")
    data = dataclasses.asdict(
        dataclasses.replace(repo.create(src, compute_file_hash(src)), page_images=[str(pages[0])])
    )
    data["original_img"] = str(original)
    repo.state_path_for_input(src).write_text(json.dumps(data), encoding="utf-8")

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.original_images == [str(original)]
    assert loaded.originals == (str(original),)
    assert to_view(src, loaded).original_images == (str(original),)


def test_an_original_missing_on_disk_leaves_the_one_original_it_had(tmp_path: Path) -> None:
    """
    Given  a state whose second original file is gone
    When   load_valid is called
    Then   the list is dropped and the document has its first original
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    originals = _originals(tmp_path)
    repo.save(
        src,
        dataclasses.replace(
            repo.create(src, compute_file_hash(src)),
            page_images=[str(page) for page in pages],
            original_images=[str(page) for page in originals],
        ),
    )
    originals[1].unlink()

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.original_images == [str(originals[0])]
    assert loaded.originals == (str(originals[0]),)


def test_a_missing_first_original_leaves_no_originals(tmp_path: Path) -> None:
    """
    Given  a state whose first original is gone
    When   load_valid is called
    Then   originals is empty
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    originals = _originals(tmp_path)
    repo.save(
        src,
        dataclasses.replace(
            repo.create(src, compute_file_hash(src)),
            page_images=[str(page) for page in pages],
            original_images=[str(page) for page in originals],
        ),
    )
    originals[0].unlink()

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.originals == ()


def test_invalidate_preview_artifact_clears_the_originals(tmp_path: Path) -> None:
    """
    Given  a state with three originals
    When   invalidate_preview_artifact is called
    Then   the originals are cleared with the preview paths
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    originals = _originals(tmp_path)
    state = dataclasses.replace(
        repo.create(src, compute_file_hash(src)),
        source_image=str(pages[0]),
        page_images=[str(page) for page in pages],
        original_images=[str(page) for page in originals],
    )

    cleared = repo.invalidate_preview_artifact(src, state)

    assert cleared.original_images == []


def test_resetting_from_ocr_clears_the_originals(tmp_path: Path) -> None:
    """
    Given  a state with OCR text and three originals
    When   invalidate_from_stage("ocr") runs
    Then   the originals are cleared with the preview paths
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    pages = _pages(tmp_path)
    originals = _originals(tmp_path)
    repo.save(
        src,
        dataclasses.replace(
            repo.create(src, compute_file_hash(src)),
            raw_text="text",
            ocr_done=True,
            page_images=[str(page) for page in pages],
            original_images=[str(page) for page in originals],
        ),
    )

    cleared = repo.invalidate_from_stage(src, "ocr")

    assert cleared is not None
    assert cleared.original_images == []


# ── Text notation (raw_text_format) ───────────────────────────────────────


def _ocr_record(source_hash: str, **overrides: str) -> OcrResultRecord:
    fields = {
        "source_hash": source_hash,
        "raw_text": "**x**",
        "preview_img": "",
        "source_image": "",
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

    assert loaded is not None
    assert loaded.raw_text_format == "plain"
    assert to_view(src, loaded).raw_text_format == "plain"


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
    assert loaded is not None
    assert loaded.raw_text_format == "markdown"
    assert to_view(src, loaded).raw_text_format == "markdown"


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
        correction_config_hash="c",
    )

    loaded = repo.load_valid(src)
    assert loaded is not None
    view = to_view(src, loaded)
    assert view.correction_text == "~~a~~ fixed"
    assert view.raw_text_format == "markdown"


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


def test_save_writes_through_a_temp_file_and_leaves_none_behind(tmp_path: Path) -> None:
    """
    Given  a repository and a stored state
    When   the state is saved again with new content
    Then   the file holds the new content, is valid JSON, and no ``.tmp`` file is left (the
           swap is atomic, so a process that ends during a save cannot leave half a file)
    """
    repo = _repo(tmp_path)
    source = _source_file(tmp_path)
    first = repo.load_or_create(source, "somehash")
    repo.save(source, first)

    second = dataclasses.replace(first, raw_text="new text", ocr_done=True)
    repo.save(source, second)

    path = repo.state_path_for_input(source)
    assert json.loads(path.read_text(encoding="utf-8"))["raw_text"] == "new text"
    assert list(path.parent.glob("*.tmp")) == []


def test_two_saves_never_share_a_temporary_file(tmp_path: Path) -> None:
    """
    Given  one document saved from two threads at once
    When   both saves run many times
    Then   none fails and the stored state is valid JSON
    """
    import threading  # noqa: PLC0415

    repo = _repo(tmp_path)
    source = _source_file(tmp_path)
    state = repo.load_or_create(source, "somehash")
    errors: list[BaseException] = []

    def work(text: str) -> None:
        try:
            for _ in range(30):
                repo.save(source, dataclasses.replace(state, raw_text=text))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(t,)) for t in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert repo.load(source).raw_text in {"a", "b"}


def test_the_state_file_is_named_after_the_document_stem(tmp_path: Path) -> None:
    """
    Given  a document in a folder
    When   its state path is asked for
    Then   the file is named with the document stem, the same name the images carry
    """
    repo = _repo(tmp_path)
    (tmp_path / "input").mkdir()
    source = _source_file(tmp_path / "input", name="Anna.pdf")

    assert repo.state_path_for_input(source).name == f"{document_stem(source)}.json"


def test_two_documents_of_one_name_get_two_state_files(tmp_path: Path) -> None:
    """
    Given  two files called Anna.pdf in different folders
    When   each is saved
    Then   each keeps its own state, and neither overwrites the other
    """
    repo = _repo(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _source_file(tmp_path / "a", name="Anna.pdf", content="one")
    second = _source_file(tmp_path / "b", name="Anna.pdf", content="two")

    repo.save(first, dataclasses.replace(repo.load_or_create(first, "h1"), raw_text="first"))
    repo.save(second, dataclasses.replace(repo.load_or_create(second, "h2"), raw_text="second"))

    assert repo.load(first).raw_text == "first"
    assert repo.load(second).raw_text == "second"


# ── the transcription a proofreading was made from ────────────────────────


def test_record_correction_result_stores_the_source_it_was_made_from(tmp_path: Path) -> None:
    """
    Given  a state whose reading is done
    When   a proofreading is recorded with the identity of the text it was made from
    Then   the state holds that identity
    """
    repo = _repo(tmp_path)
    source = _source_file(tmp_path)
    state = dataclasses.replace(repo.load_or_create(source, "h"), ocr_done=True, raw_text="raw")

    updated = repo.record_correction_result(
        source,
        state,
        correction_text="corrected",
        correction_config_hash="corr-h",
        source_hash="text-identity",
    )

    assert updated.correction_source_hash == "text-identity"


def test_record_ocr_result_clears_the_source_of_the_proofreading(tmp_path: Path) -> None:
    """
    Given  a state with a proofreading made from a text
    When   a new reading is recorded
    Then   the identity of the old text is gone with the proofreading
    """
    repo = _repo(tmp_path)
    source = _source_file(tmp_path)
    state = dataclasses.replace(
        repo.load_or_create(source, "h"),
        ocr_done=True,
        raw_text="raw",
        correction_done=True,
        correction_text="corrected",
        correction_source_hash="text-identity",
    )

    updated = repo.record_ocr_result(
        source,
        state,
        OcrResultRecord(
            source_hash="h",
            raw_text="new raw",
            preview_img="",
            source_image="",
            ocr_config_hash="ocr-h",
            preview_config_hash="",
        ),
    )

    assert updated.correction_source_hash == ""


def test_resetting_the_correction_clears_the_source_it_was_made_from(tmp_path: Path) -> None:
    """
    Given  a state with a proofreading made from a text
    When   the correction stage is reset
    Then   the identity of that text is cleared with the proofreading
    """
    repo = _repo(tmp_path)
    source = _source_file(tmp_path)
    state = dataclasses.replace(
        repo.load_or_create(source, "h"),
        ocr_done=True,
        raw_text="raw",
        correction_done=True,
        correction_text="corrected",
        correction_source_hash="text-identity",
    )

    updated = repo.reset_from_stage(source, state, "correction")

    assert updated.correction_source_hash == ""


#: The ``DocumentState`` fields that belong to each stage, by their prefix.  A field added for
#: a stage must be named here, so the reset table cannot silently miss it.
_STAGE_FIELD_PREFIXES: dict[str, tuple[str, ...]] = {
    "ocr": (
        "ocr_",
        "raw_text",
        "preview_",
        "page_images",
        "original_images",
        "source_image",
        "preprocess_steps",
    ),
    "correction": ("correction_",),
    "evaluation": ("evaluation_", "eval_"),
}


def _stage_fields(stage: str) -> set[str]:
    """Return the ``DocumentState`` fields that belong to *stage*."""
    prefixes = _STAGE_FIELD_PREFIXES[stage]
    return {
        name
        for name in (field.name for field in dataclasses.fields(DocumentState))
        if any(name.startswith(prefix) for prefix in prefixes)
    }


def _full_state(repo: StateRepository, source: Path) -> DocumentState:
    """Return a state with every stage done and every stage field set."""
    return dataclasses.replace(
        repo.create(source, compute_file_hash(source)),
        raw_text="raw",
        raw_text_format="markdown",
        ocr_done=True,
        ocr_config_hash="ocr-h",
        preview_config_hash="prev-h",
        page_images=["p1.png"],
        original_images=["o1.png"],
        source_image="p1.png",
        preprocess_steps=["grayscale"],
        correction_done=True,
        correction_text="corrected",
        correction_review={"edits": []},
        correction_warning="warning",
        correction_sent_text="sent",
        correction_sent_map={"<PERSON_1>": "Anna"},
        correction_sent=_sent().to_dict(),
        correction_config_hash="corr-h",
        correction_source_hash="text-id",
        evaluation_done=True,
        evaluation_text="eval",
        evaluation_sent=_sent().to_dict(),
        eval_config_hash="eval-h",
    )


@pytest.mark.parametrize("stage", STAGE_ORDER)
def test_a_reset_clears_every_field_of_that_stage_and_the_later_ones(
    tmp_path: Path, stage: str
) -> None:
    """
    Given  a state with every stage done and every stage field set
    When   it is reset from *stage*
    Then   every field of that stage and of every later stage is back at its empty value
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = _full_state(repo, src)

    cleared = repo.reset_from_stage(src, state, stage)

    blank = repo.create(src, state.source_hash)
    for later in STAGE_ORDER[STAGE_ORDER.index(stage) :]:
        for name in _stage_fields(later):
            assert getattr(cleared, name) == getattr(blank, name), name


def test_a_record_with_the_dropped_fields_still_serves_its_results(tmp_path: Path) -> None:
    """
    Given  a stored v3 record that still carries the fields the app no longer reads
    When   it is loaded
    Then   it loads and its results are served
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    data = dataclasses.asdict(_full_state(repo, src))
    data["correction_invalidated"] = True
    data["evaluation_invalidated"] = True
    data["ocr_prompt"] = "Transcribe."
    data["evaluate_prompt"] = "Review."
    repo.state_path_for_input(src).write_text(json.dumps(data), encoding="utf-8")

    loaded = repo.load_valid(src)

    assert loaded is not None
    assert loaded.raw_text == "raw"
    assert loaded.correction_text == "corrected"
    assert loaded.evaluation_text == "eval"


def _reconcile(
    repo: StateRepository,
    source: Path,
    state: DocumentState,
    *,
    preview_config_hash: str | None = None,
    **expected: object,
) -> DocumentState:
    """Reconcile *state* the way a run does: the policy decides, the store applies.

    *expected* overrides the hashes the current settings ask for; every hash not named is the
    one the state holds, so nothing is stale unless a test says so.
    """
    values: dict[str, object] = {
        "ocr_done": state.ocr_done,
        "ocr_config_hash": state.ocr_config_hash,
        "expected_ocr_config_hash": state.ocr_config_hash,
        "correction_done": state.correction_done,
        "correction_config_hash": state.correction_config_hash,
        "expected_correction_config_hash": state.correction_config_hash,
        "evaluation_done": state.evaluation_done,
        "eval_config_hash": state.eval_config_hash,
        "expected_eval_config_hash": state.eval_config_hash,
        "correction_source": state.correction_source_hash,
        "expected_correction_source": state.correction_source_hash,
    }
    values.update(expected)
    return repo.reconcile(
        source,
        state,
        source_hash=state.source_hash,
        stale_stage=first_stale_stage_for_hashes(StageHashes(**values)),  # ty: ignore[invalid-argument-type]
        preview_config_hash=(
            state.preview_config_hash if preview_config_hash is None else preview_config_hash
        ),
    )


def _done_state(repo: StateRepository, source: Path) -> DocumentState:
    """Return a state whose reading and proofreading are done and current."""
    return dataclasses.replace(
        repo.load_or_create(source, "h"),
        ocr_done=True,
        raw_text="raw",
        ocr_config_hash="ocr-h",
        correction_done=True,
        correction_text="corrected",
        correction_config_hash="corr-h",
        correction_source_hash="text-identity",
    )


def test_a_frozen_reading_is_not_reset_by_changed_settings(tmp_path: Path) -> None:
    """
    Given  a document whose transcription the teacher fixed, and changed reading settings
    When   the runtime config is reconciled
    Then   the reading stays: the teacher's text is what the document says
    """
    repo = _repo(tmp_path)
    source = _source_file(tmp_path)
    state = _done_state(repo, source)

    reconciled = _reconcile(repo, source, state, expected_ocr_config_hash="other", ocr_frozen=True)

    assert reconciled.ocr_done is True
    assert reconciled.raw_text == "raw"


def test_a_proofreading_the_teacher_worked_on_is_not_reset_by_changed_settings(
    tmp_path: Path,
) -> None:
    """
    Given  a document whose corrections the teacher worked on, and changed proofreading settings
    When   the runtime config is reconciled
    Then   the proofreading stays
    """
    repo = _repo(tmp_path)
    source = _source_file(tmp_path)
    state = _done_state(repo, source)

    reconciled = _reconcile(
        repo,
        source,
        state,
        expected_correction_config_hash="other",
        expected_correction_source="text-identity",
        correction_kept=True,
    )

    assert reconciled.correction_done is True
    assert reconciled.correction_text == "corrected"


def test_a_proofreading_made_from_another_text_is_reset_even_when_kept(tmp_path: Path) -> None:
    """
    Given  a document whose corrections the teacher worked on, and a transcription that changed
    When   the runtime config is reconciled
    Then   the proofreading is reset: it was made from a text the document no longer has
    """
    repo = _repo(tmp_path)
    source = _source_file(tmp_path)
    state = _done_state(repo, source)

    reconciled = _reconcile(
        repo, source, state, expected_correction_source="another-text", correction_kept=True
    )

    assert reconciled.correction_done is False
    assert reconciled.correction_source_hash == ""


@pytest.mark.parametrize("stage", STAGE_ORDER)
def test_reconcile_clears_the_stale_stage_and_the_later_ones(tmp_path: Path, stage: str) -> None:
    """
    Given  a state with every stage done and every stage field set
    When   it is reconciled with *stage* as the earliest stale stage, for a run that asks for
           no image preparation
    Then   every field of that stage and of every later stage is back at its empty value
    """
    repo = _repo(tmp_path)
    src = _source_file(tmp_path)
    state = _full_state(repo, src)

    reconciled = repo.reconcile(
        src,
        state,
        source_hash=state.source_hash,
        stale_stage=stage,  # ty: ignore[invalid-argument-type]
        preview_config_hash="",
    )

    blank = repo.create(src, state.source_hash)
    for later in STAGE_ORDER[STAGE_ORDER.index(stage) :]:
        for name in _stage_fields(later):
            assert getattr(reconciled, name) == getattr(blank, name), name
