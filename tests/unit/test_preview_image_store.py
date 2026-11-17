"""Unit tests for teachers_teammate.infrastructure.preview_image_store."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

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


# ── persist_original_image / delete with original ──────────────────────────


def test_persist_original_image_copies_with_original_suffix_kept(tmp_path: Path) -> None:
    """
    Given  a JPEG source page
    When   persist_original_image is called
    Then   it is copied to <stem>_original.jpg in the store root and the source is untouched
    """
    store = PreviewImageStore(tmp_path / "store")
    src = tmp_path / "scan.JPG"
    src.write_bytes(b"jpegdata")

    target = store.persist_original_image(stem="scan", source_path=src)

    assert target == tmp_path / "store" / "scan_original.jpg"
    assert target.read_bytes() == b"jpegdata"
    assert src.exists()


def test_persist_original_image_is_noop_when_source_is_target(tmp_path: Path) -> None:
    """
    Given  a source page that already lives at the canonical original path
    When   persist_original_image is called
    Then   the file is left as-is and its path is returned
    """
    store = PreviewImageStore(tmp_path / "store")
    src = tmp_path / "store" / "scan_original.png"
    src.write_bytes(b"png")

    assert store.persist_original_image(stem="scan", source_path=src) == src
    assert src.read_bytes() == b"png"


def test_delete_preview_artifacts_also_removes_original(tmp_path: Path) -> None:
    """
    Given  preview, source and original image files
    When   delete_preview_artifacts is called with all three paths
    Then   all of them are deleted
    """
    store = PreviewImageStore(tmp_path / "store")
    files = [tmp_path / name for name in ("a_preprocessed.png", "a_original.png")]
    for f in files:
        f.write_bytes(b"x")

    store.delete_preview_artifacts(str(files[0]), str(files[0]), str(files[1]))

    assert not any(f.exists() for f in files)


def test_persist_original_image_hard_links_instead_of_copying(tmp_path: Path) -> None:
    """
    Given  a source page on a filesystem that supports hard links
    When   persist_original_image is called
    Then   the stored original shares the source's data (no second copy on disk)
    """
    store = PreviewImageStore(tmp_path / "store")
    src = tmp_path / "scan.png"
    src.write_bytes(b"pngdata")

    target = store.persist_original_image(stem="scan", source_path=src)

    assert os.path.samefile(src, target)


def test_persist_original_image_falls_back_to_copy_when_link_fails(tmp_path: Path) -> None:
    """
    Given  a filesystem where os.link raises OSError
    When   persist_original_image is called
    Then   the page is copied instead
    """
    store = PreviewImageStore(tmp_path / "store")
    src = tmp_path / "scan.png"
    src.write_bytes(b"pngdata")

    with patch("os.link", side_effect=OSError("cross-device")):
        target = store.persist_original_image(stem="scan", source_path=src)

    assert target.read_bytes() == b"pngdata"
    assert not os.path.samefile(src, target)


def test_persist_original_image_does_not_write_through_to_source(tmp_path: Path) -> None:
    """
    Given  an earlier run that hard-linked the original to its source page
    When   a different page is persisted for the same stem
    Then   the new content replaces the stored original and the old source is unchanged
    """
    store = PreviewImageStore(tmp_path / "store")
    old_src = tmp_path / "old.png"
    old_src.write_bytes(b"old")
    new_src = tmp_path / "new.png"
    new_src.write_bytes(b"new")
    store.persist_original_image(stem="scan", source_path=old_src)

    target = store.persist_original_image(stem="scan", source_path=new_src)

    assert target.read_bytes() == b"new"
    assert old_src.read_bytes() == b"old"


def test_persist_original_image_removes_stale_original_with_other_suffix(tmp_path: Path) -> None:
    """
    Given  an earlier original stored as <stem>_original.png
    When   a JPEG original is persisted for the same stem
    Then   the stale .png original is removed
    """
    store = PreviewImageStore(tmp_path / "store")
    stale = tmp_path / "store" / "scan_original.png"
    stale.write_bytes(b"old")
    src = tmp_path / "scan.jpg"
    src.write_bytes(b"jpg")

    target = store.persist_original_image(stem="scan", source_path=src)

    assert target.suffix == ".jpg"
    assert not stale.exists()


def test_persist_original_images_stores_every_page_in_order(tmp_path: Path) -> None:
    """
    Given  three pages as they were scanned
    When   persist_original_images is called
    Then   page 1 keeps the name it always had and the others are numbered
    """
    store = PreviewImageStore(tmp_path / "store")
    sources = [tmp_path / f"page{number}.png" for number in (1, 2, 3)]
    for source in sources:
        source.write_bytes(source.name.encode())

    stored = store.persist_original_images(stem="essay", sources=sources)

    assert [path.name for path in stored] == [
        "essay_original.png",
        "essay_o2.png",
        "essay_o3.png",
    ]
    assert [path.read_bytes() for path in stored] == [b"page1.png", b"page2.png", b"page3.png"]


def test_persist_original_images_keeps_the_suffix_of_each_page(tmp_path: Path) -> None:
    """
    Given  a JPEG second page
    When   persist_original_images is called
    Then   the stored page keeps its suffix
    """
    store = PreviewImageStore(tmp_path / "store")
    first = tmp_path / "page1.png"
    first.write_bytes(b"png")
    second = tmp_path / "page2.jpg"
    second.write_bytes(b"jpg")

    stored = store.persist_original_images(stem="essay", sources=[first, second])

    assert [path.name for path in stored] == ["essay_original.png", "essay_o2.jpg"]


def test_persist_original_images_drops_the_pages_of_an_earlier_longer_document(
    tmp_path: Path,
) -> None:
    """
    Given  a document whose originals were stored for three pages
    When   persist_original_images is called with two pages
    Then   the third page's original is gone
    """
    store = PreviewImageStore(tmp_path / "store")
    sources = [tmp_path / f"page{number}.png" for number in (1, 2, 3)]
    for source in sources:
        source.write_bytes(b"x")
    store.persist_original_images(stem="essay", sources=sources)

    stored = store.persist_original_images(stem="essay", sources=sources[:2])

    assert [path.name for path in stored] == ["essay_original.png", "essay_o2.png"]
    assert not (tmp_path / "store" / "essay_o3.png").exists()


def test_persist_original_images_keeps_the_pages_stored_before_a_failing_one(
    tmp_path: Path,
) -> None:
    """
    Given  a third page that cannot be stored
    When   persist_original_images is called
    Then   the first two pages are kept and returned
    """
    store = PreviewImageStore(tmp_path / "store")
    sources = [tmp_path / f"page{number}.png" for number in (1, 2, 3)]
    for source in sources:
        source.write_bytes(b"x")
    real_link_or_copy = store._link_or_copy  # the failing page is the point

    def fail_on_the_third(source: Path, target: Path) -> Path:
        if target.name == "essay_o3.png":
            raise OSError("disk full")
        return real_link_or_copy(source, target)

    with patch.object(store, "_link_or_copy", side_effect=fail_on_the_third):
        stored = store.persist_original_images(stem="essay", sources=sources)

    assert [path.name for path in stored] == ["essay_original.png", "essay_o2.png"]
    assert not (tmp_path / "store" / "essay_o3.png").exists()


def test_persist_original_images_links_instead_of_copying(tmp_path: Path) -> None:
    """
    Given  a page on a filesystem that supports hard links
    When   persist_original_images is called
    Then   the stored pages share the source's data
    """
    store = PreviewImageStore(tmp_path / "store")
    sources = [tmp_path / f"page{number}.png" for number in (1, 2)]
    for source in sources:
        source.write_bytes(b"x")

    stored = store.persist_original_images(stem="essay", sources=sources)

    assert all(
        os.path.samefile(source, target) for source, target in zip(sources, stored, strict=True)
    )


def test_persist_original_images_falls_back_to_copy_when_link_fails(tmp_path: Path) -> None:
    """
    Given  a filesystem where os.link raises OSError
    When   persist_original_images is called with two pages
    Then   both pages are copied instead
    """
    store = PreviewImageStore(tmp_path / "store")
    sources = [tmp_path / f"page{number}.png" for number in (1, 2)]
    for source in sources:
        source.write_bytes(b"x")

    with patch("os.link", side_effect=OSError("cross-device")):
        stored = store.persist_original_images(stem="essay", sources=sources)

    assert [path.read_bytes() for path in stored] == [b"x", b"x"]
    assert not any(
        os.path.samefile(source, target) for source, target in zip(sources, stored, strict=True)
    )


def test_prune_original_images_keeps_the_files_of_a_document_named_like_a_page(
    tmp_path: Path,
) -> None:
    """
    Given  a document 'Anna' with three originals and a document called 'Anna_o2'
    When   the originals of 'Anna' are pruned to one page
    Then   the files of 'Anna_o2' stay
    """
    root = tmp_path / "store"
    store = PreviewImageStore(root)
    for name in (
        "Anna_original.png",
        "Anna_o2.png",
        "Anna_o3.png",
        "Anna_o2_preprocessed.png",
        "Anna_o2_original.png",
        "Anna_o2_p2.png",
    ):
        (root / name).write_bytes(b"x")

    store.prune_original_images("Anna", keep=[root / "Anna_original.png"])

    assert (root / "Anna_original.png").exists()
    assert not (root / "Anna_o2.png").exists()
    assert not (root / "Anna_o3.png").exists()
    assert (root / "Anna_o2_preprocessed.png").exists()
    assert (root / "Anna_o2_original.png").exists()
    assert (root / "Anna_o2_p2.png").exists()


def test_delete_preview_artifacts_also_removes_every_original(tmp_path: Path) -> None:
    """
    Given  a document with three originals
    When   delete_preview_artifacts is called with them
    Then   all of them are deleted
    """
    store = PreviewImageStore(tmp_path / "store")
    files = [tmp_path / name for name in ("a_original.png", "a_o2.png", "a_o3.png")]
    for file in files:
        file.write_bytes(b"x")

    store.delete_preview_artifacts(
        str(files[0]), str(files[0]), str(files[0]), (), [str(file) for file in files]
    )

    assert not any(file.exists() for file in files)


def test_prune_original_images_leaves_other_stems_alone(tmp_path: Path) -> None:
    """
    Given  originals for two different stems
    When   prune_original_images is called for one stem without keep
    Then   only that stem's originals are removed
    """
    root = tmp_path / "store"
    store = PreviewImageStore(root)
    mine = root / "a_original.png"
    other = root / "b_original.png"
    mine.write_bytes(b"x")
    other.write_bytes(b"x")

    store.prune_original_images("a")

    assert not mine.exists()
    assert other.exists()


# ── pages ──────────────────────────────────────────────────────────────────


def test_paths_for_page_keeps_the_first_page_name(tmp_path: Path) -> None:
    """
    Given  a stem 'my_doc'
    When   paths_for_page is asked for pages 1, 2 and 3
    Then   page 1 is the preview image and the others are numbered
    """
    store = PreviewImageStore(tmp_path / "root")

    assert store.paths_for_page("my_doc", 1).name == "my_doc_preprocessed.png"
    assert store.paths_for_page("my_doc", 2).name == "my_doc_p2.png"
    assert store.paths_for_page("my_doc", 3).name == "my_doc_p3.png"


def test_persist_page_images_stores_every_page_in_order(tmp_path: Path) -> None:
    """
    Given  three prepared pages
    When   persist_page_images is called
    Then   three files exist and the returned paths are in page order
    """
    store = PreviewImageStore(tmp_path / "store")
    sources = []
    for number in (1, 2, 3):
        source = tmp_path / f"page{number}.png"
        source.write_bytes(f"page {number}".encode())
        sources.append(source)

    stored = store.persist_page_images(stem="essay", sources=sources)

    assert [path.name for path in stored] == [
        "essay_preprocessed.png",
        "essay_p2.png",
        "essay_p3.png",
    ]
    assert [path.read_bytes() for path in stored] == [b"page 1", b"page 2", b"page 3"]


def test_persist_page_images_drops_pages_of_an_earlier_longer_document(tmp_path: Path) -> None:
    """
    Given  a document stored with three pages
    When   it is stored again with two
    Then   the third page file is gone
    """
    store = PreviewImageStore(tmp_path / "store")
    sources = []
    for number in (1, 2, 3):
        source = tmp_path / f"page{number}.png"
        source.write_bytes(b"x")
        sources.append(source)
    store.persist_page_images(stem="essay", sources=sources)

    store.persist_page_images(stem="essay", sources=sources[:2])

    assert not (tmp_path / "store" / "essay_p3.png").exists()
    assert (tmp_path / "store" / "essay_p2.png").exists()


def test_pruning_a_stem_leaves_the_pages_of_a_similar_stem(tmp_path: Path) -> None:
    """
    Given  the pages of the documents 'Anna' and 'Anna_p1'
    When   the pages of 'Anna' are pruned to one
    Then   the pages of 'Anna_p1' are untouched
    """
    root = tmp_path / "store"
    store = PreviewImageStore(root)
    for name in (
        "Anna_preprocessed.png",
        "Anna_p2.png",
        "Anna_p1_preprocessed.png",
        "Anna_p1_p2.png",
    ):
        (root / name).write_bytes(b"x")

    store.prune_page_images("Anna", keep=1)

    assert not (root / "Anna_p2.png").exists()
    assert (root / "Anna_p1_preprocessed.png").exists()
    assert (root / "Anna_p1_p2.png").exists()


def test_persist_page_images_links_instead_of_copying(tmp_path: Path) -> None:
    """
    Given  a prepared second page on a filesystem that supports hard links
    When   persist_page_images is called
    Then   the stored page shares the source's data
    """
    store = PreviewImageStore(tmp_path / "store")
    first = tmp_path / "page1.png"
    second = tmp_path / "page2.png"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    stored = store.persist_page_images(stem="essay", sources=[first, second])

    assert os.path.samefile(second, stored[1])


def test_persist_page_images_falls_back_to_copy_when_link_fails(tmp_path: Path) -> None:
    """
    Given  a filesystem where os.link raises OSError
    When   persist_page_images is called with two pages
    Then   the second page is copied and survives deleting the source
    """
    store = PreviewImageStore(tmp_path / "store")
    first = tmp_path / "page1.png"
    second = tmp_path / "page2.png"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    with patch("os.link", side_effect=OSError("cross-device")):
        stored = store.persist_page_images(stem="essay", sources=[first, second])

    second.unlink()
    assert stored[1].read_bytes() == b"two"


def test_a_later_page_that_cannot_be_stored_leaves_only_the_first(tmp_path: Path) -> None:
    """
    Given  three prepared pages and a filesystem that refuses the third
    When   persist_page_images is called
    Then   only page 1 is returned and no second page file is left behind
    """
    store = PreviewImageStore(tmp_path / "store")
    sources = []
    for number in (1, 2, 3):
        source = tmp_path / f"page{number}.png"
        source.write_bytes(b"x")
        sources.append(source)

    real_link_or_copy = PreviewImageStore._link_or_copy
    calls: list[int] = []

    def link_or_copy(source: Path, target: Path) -> Path:
        calls.append(1)
        if len(calls) == 2:  # the third page
            raise OSError("disk full")
        return real_link_or_copy(source, target)

    with patch.object(PreviewImageStore, "_link_or_copy", side_effect=link_or_copy):
        stored = store.persist_page_images(stem="essay", sources=sources)

    assert [path.name for path in stored] == ["essay_preprocessed.png"]
    assert not (tmp_path / "store" / "essay_p2.png").exists()
    assert not (tmp_path / "store" / "essay_p3.png").exists()


def test_delete_preview_artifacts_removes_every_page(tmp_path: Path) -> None:
    """
    Given  a document stored with three pages
    When   delete_preview_artifacts is called with them
    Then   every page file is gone
    """
    root = tmp_path / "store"
    store = PreviewImageStore(root)
    pages = [root / name for name in ("a_preprocessed.png", "a_p2.png", "a_p3.png")]
    for page in pages:
        page.write_bytes(b"x")

    store.delete_preview_artifacts(str(pages[0]), str(pages[0]), "", [str(p) for p in pages])

    assert not any(page.exists() for page in pages)


def test_cleanup_deletes_the_runs_own_page_images_only(tmp_path: Path) -> None:
    """
    Given  the run's intermediate page images and stored pages of documents whose names
           contain '_page'
    When   cleanup_intermediate_images(keep_debug=False) is called
    Then   only the run's own intermediates are deleted
    """
    root = tmp_path / "store"
    root.mkdir()
    deleted = [
        "x_page0.png",
        "x_page0_preprocessed.png",
        "x_step1.jpg",
        # Known and pinned: a stem ending in '_page<digits>' looks like an intermediate.
        "week_page2_preprocessed.png",
    ]
    kept = [
        "week_page_preprocessed.png",
        "week_page_p2.png",
        "scan_page_original.png",
    ]
    for name in deleted + kept:
        (root / name).write_bytes(b"x")

    PreviewImageStore(root).cleanup_intermediate_images(keep_debug=False)

    assert [name for name in deleted if (root / name).exists()] == []
    assert [name for name in kept if not (root / name).exists()] == []
