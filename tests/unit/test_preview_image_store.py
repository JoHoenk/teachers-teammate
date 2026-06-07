"""Unit tests for teachers_teammate.infrastructure.preview_image_store."""

from __future__ import annotations

from pathlib import Path

from teachers_teammate.infrastructure.preview_image_store import PreviewImageStore


# ── Construction ───────────────────────────────────────────────────────────


def test_init_creates_artifact_root(tmp_path: Path) -> None:
    """
    Given  a path that does not yet exist
    When   PreviewImageStore is constructed with that path
    Then   the directory is created on disk
    """
    root = tmp_path / "artifacts" / "nested"
    store = PreviewImageStore(root)
    assert root.is_dir()
    assert store.root == root


def test_root_property_returns_constructed_path(tmp_path: Path) -> None:
    """
    Given  a PreviewImageStore constructed with a specific root
    When   .root is accessed
    Then   the exact path passed to __init__ is returned
    """
    root = tmp_path / "store"
    store = PreviewImageStore(root)
    assert store.root == root


# ── paths_for_stem ─────────────────────────────────────────────────────────


def test_paths_for_stem_returns_preprocessed_png(tmp_path: Path) -> None:
    """
    Given  a stem 'my_doc'
    When   paths_for_stem is called
    Then   preview_img ends with 'my_doc_preprocessed.png'
    """
    store = PreviewImageStore(tmp_path / "root")
    paths = store.paths_for_stem("my_doc")
    assert paths.preview_img.name == "my_doc_preprocessed.png"


def test_paths_for_stem_is_under_root(tmp_path: Path) -> None:
    """
    Given  a store with a specific root
    When   paths_for_stem is called
    Then   the returned path is a child of the store root
    """
    root = tmp_path / "root"
    store = PreviewImageStore(root)
    paths = store.paths_for_stem("doc")
    assert paths.preview_img.parent == root


# ── persist_preview_image ──────────────────────────────────────────────────


def test_persist_preview_image_copies_to_canonical_path(tmp_path: Path) -> None:
    """
    Given  a source file at a temporary location
    When   persist_preview_image is called
    Then   the file is copied to the canonical target path and that path is returned
    """
    root = tmp_path / "artifacts"
    store = PreviewImageStore(root)
    source = tmp_path / "tmp_preview.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header

    result = store.persist_preview_image(stem="document", source_path=source)

    expected = root / "document_preprocessed.png"
    assert result == expected
    assert expected.exists()
    assert expected.read_bytes() == source.read_bytes()


def test_persist_preview_image_noop_when_source_equals_target(tmp_path: Path) -> None:
    """
    Given  source_path and the canonical target resolve to the same path
    When   persist_preview_image is called
    Then   no copy is performed (file is not modified) and the target path is returned
    """
    root = tmp_path / "artifacts"
    store = PreviewImageStore(root)  # __init__ already creates root

    canonical = root / "doc_preprocessed.png"
    canonical.write_bytes(b"original content")
    mtime_before = canonical.stat().st_mtime_ns

    result = store.persist_preview_image(stem="doc", source_path=canonical)

    assert result == canonical
    assert canonical.stat().st_mtime_ns == mtime_before  # file was not touched


# ── cleanup_intermediate_images ────────────────────────────────────────────


def test_cleanup_keep_debug_true_leaves_all_files(tmp_path: Path) -> None:
    """
    Given  step images and page images are present
    When   cleanup_intermediate_images(keep_debug=True) is called
    Then   all files remain on disk
    """
    root = tmp_path / "artifacts"
    root.mkdir()
    step_file = root / "doc_step1.jpg"
    page_file = root / "doc_page1.png"
    preview_file = root / "doc_preprocessed.png"
    for f in (step_file, page_file, preview_file):
        f.write_bytes(b"content")

    store = PreviewImageStore(root)
    store.cleanup_intermediate_images(keep_debug=True)

    assert step_file.exists()
    assert page_file.exists()
    assert preview_file.exists()


def test_cleanup_keep_debug_false_deletes_step_and_page_files(tmp_path: Path) -> None:
    """
    Given  step images (*_step*.jpg) and page images (*_page*.png) are present
    When   cleanup_intermediate_images(keep_debug=False) is called
    Then   those files are deleted
    """
    root = tmp_path / "artifacts"
    root.mkdir()
    step_file = root / "doc_step1.jpg"
    page_file = root / "doc_page2.png"
    for f in (step_file, page_file):
        f.write_bytes(b"tmp")

    store = PreviewImageStore(root)
    store.cleanup_intermediate_images(keep_debug=False)

    assert not step_file.exists()
    assert not page_file.exists()


def test_cleanup_preserves_preprocessed_png(tmp_path: Path) -> None:
    """
    Given  both intermediate files and canonical preview files exist
    When   cleanup_intermediate_images(keep_debug=False) is called
    Then   *_preprocessed.png files are NOT deleted
    """
    root = tmp_path / "artifacts"
    root.mkdir()
    preview = root / "doc_preprocessed.png"
    step = root / "doc_step0.jpg"
    preview.write_bytes(b"keep me")
    step.write_bytes(b"delete me")

    store = PreviewImageStore(root)
    store.cleanup_intermediate_images(keep_debug=False)

    assert preview.exists()
    assert not step.exists()
