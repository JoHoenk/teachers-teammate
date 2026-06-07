"""Unit tests for the append-only benchmark run store."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.infrastructure.benchmark.run_store import (
    BenchmarkRunStore,
    NewRunRequest,
)


def _save(
    store: BenchmarkRunStore,
    *,
    doc_hash: str = "doc1",
    text: str = "hello",
    cfg_hash: str = "abc12345",
    preview: Path | None = None,
):
    return store.save(
        NewRunRequest(
            document_hash=doc_hash,
            document_path="/docs/sample.pdf",
            display_name="sample.pdf",
            ocr=OcrConfig(engine="tesseract", model="", preprocess_method="none"),
            language="English",
            ocr_config_hash=cfg_hash,
            raw_text=text,
            elapsed_s=1.5,
            preview_src=preview,
        )
    )


def test_save_and_list_round_trip(tmp_path: Path) -> None:
    """
    Given  a run saved for a document
    When   list_for() is called with the document hash
    Then   the run is returned with its text and OCR config intact
    """
    store = BenchmarkRunStore(tmp_path)
    saved = _save(store, text="recognised text")

    runs = store.list_for("doc1")
    assert len(runs) == 1
    assert runs[0].run_id == saved.run_id
    assert runs[0].raw_text == "recognised text"
    assert runs[0].ocr.engine == "tesseract"
    assert runs[0].language == "English"


@pytest.mark.use_case("OCR_Run_History")
def test_save_keeps_timestamped_history(tmp_path: Path) -> None:
    """
    Given  two saves of the same OCR config for one document
    When   list_for() is called
    Then   both are kept as separate timestamped entries (append-only, no overwrite)
    """
    store = BenchmarkRunStore(tmp_path)
    _save(store, text="first")
    _save(store, text="second")

    runs = store.list_for("doc1")
    assert len(runs) == 2
    assert {r.raw_text for r in runs} == {"first", "second"}


def test_save_copies_preview_image(tmp_path: Path) -> None:
    """
    Given  a preview image source path
    When   a run is saved
    Then   the image is copied into the store and the run records its path
    """
    src = tmp_path / "preview.png"
    src.write_bytes(b"\x89PNG\r\n")
    store = BenchmarkRunStore(tmp_path)

    run = _save(store, preview=src)
    assert run.preview_img
    assert Path(run.preview_img).is_file()


@pytest.mark.use_case("Benchmark_Run_Deletion")
def test_delete_removes_run_and_preview(tmp_path: Path) -> None:
    """
    Given  a saved run with a preview image
    When   delete() is called for that run
    Then   both the JSON and the preview image are removed
    """
    src = tmp_path / "preview.png"
    src.write_bytes(b"\x89PNG\r\n")
    store = BenchmarkRunStore(tmp_path)
    run = _save(store, preview=src)

    store.delete("doc1", run.run_id)
    assert store.list_for("doc1") == []
    assert not Path(run.preview_img).exists()


def test_delete_all_for_clears_document(tmp_path: Path) -> None:
    """
    Given  multiple runs for a document
    When   delete_all_for() is called
    Then   the document has no runs and is dropped from list_documents()
    """
    store = BenchmarkRunStore(tmp_path)
    _save(store, text="a")
    _save(store, text="b")

    store.delete_all_for("doc1")
    assert store.list_for("doc1") == []
    assert store.list_documents() == []


@pytest.mark.use_case("OCR_Run_History")
def test_keep_last_n_evicts_oldest(tmp_path: Path) -> None:
    """
    Given  a store with keep_last=2 and three saved runs
    When   the third run is saved
    Then   only the two newest runs remain
    """
    store = BenchmarkRunStore(tmp_path, keep_last=2)
    _save(store, text="one")
    _save(store, text="two")
    _save(store, text="three")

    runs = store.list_for("doc1")
    assert len(runs) == 2
    assert {r.raw_text for r in runs} == {"two", "three"}


def test_list_documents_reports_display_name(tmp_path: Path) -> None:
    """
    Given  a saved run for a document
    When   list_documents() is called
    Then   it returns the document hash and its display name
    """
    store = BenchmarkRunStore(tmp_path)
    _save(store)
    assert store.list_documents() == [("doc1", "sample.pdf")]
