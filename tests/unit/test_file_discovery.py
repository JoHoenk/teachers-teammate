"""Unit tests for teachers_teammate.infrastructure.file_discovery."""
# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from teachers_teammate.infrastructure.file_discovery import FileDiscovery
from tests.conftest import make_config


@pytest.fixture
def discovery() -> FileDiscovery:
    return FileDiscovery()


@pytest.mark.use_case("Batch_OCR_Processing")
def test_collect_input_files_finds_png(
    tmp_path: Path, sample_png: Path, discovery: FileDiscovery
) -> None:
    """
    Given  an input directory containing one PNG file
    When   collect_input_files() is called
    Then   the PNG file appears in the result
    """
    in_dir = tmp_path / "input"
    in_dir.mkdir()
    shutil.copy(sample_png, in_dir / "image.png")
    cfg = make_config(tmp_path, input_dir=in_dir)
    files = discovery.collect_input_files(cfg)
    assert any(f.name == "image.png" for f in files)


@pytest.mark.use_case("Batch_OCR_Processing")
def test_collect_input_files_finds_pdf(
    tmp_path: Path, sample_pdf: Path, discovery: FileDiscovery
) -> None:
    """
    Given  an input directory containing one PDF file
    When   collect_input_files() is called
    Then   the PDF file appears in the result
    """
    in_dir = tmp_path / "input"
    in_dir.mkdir()
    shutil.copy(sample_pdf, in_dir / "doc.pdf")
    cfg = make_config(tmp_path, input_dir=in_dir)
    files = discovery.collect_input_files(cfg)
    assert any(f.name == "doc.pdf" for f in files)


@pytest.mark.use_case("Batch_OCR_Processing")
def test_collect_input_files_finds_jpg(tmp_path: Path, discovery: FileDiscovery) -> None:
    """
    Given  an input directory containing one JPEG file
    When   collect_input_files() is called
    Then   the JPEG file appears in the result
    """
    from PIL import Image  # noqa: PLC0415

    in_dir = tmp_path / "input"
    in_dir.mkdir(exist_ok=True)
    Image.new("RGB", (10, 10)).save(in_dir / "photo.jpg")
    cfg = make_config(tmp_path, input_dir=in_dir)
    files = discovery.collect_input_files(cfg)
    assert any(f.name == "photo.jpg" for f in files)


@pytest.mark.use_case("Batch_OCR_Processing")
def test_collect_input_files_includes_txt(tmp_path: Path, discovery: FileDiscovery) -> None:
    """
    Given  an input directory containing only a .txt file
    When   collect_input_files() is called
    Then   the .txt file appears in the result
    """
    in_dir = tmp_path / "input"
    in_dir.mkdir(exist_ok=True)
    (in_dir / "notes.txt").write_text("hello")
    cfg = make_config(tmp_path, input_dir=in_dir)
    files = discovery.collect_input_files(cfg)
    assert any(f.suffix == ".txt" for f in files)


def test_collect_input_files_non_recursive_misses_nested(
    tmp_path: Path, sample_png: Path, discovery: FileDiscovery
) -> None:
    """
    Given  a PNG in a subdirectory and recursive=False in the config
    When   collect_input_files() is called
    Then   the nested file does not appear in the result
    """
    in_dir = tmp_path / "input"
    sub = in_dir / "sub"
    sub.mkdir(parents=True)
    shutil.copy(sample_png, sub / "nested.png")
    cfg = make_config(tmp_path, input_dir=in_dir, recursive=False)
    files = discovery.collect_input_files(cfg)
    assert not any(f.name == "nested.png" for f in files)


def test_collect_input_files_recursive_finds_nested(
    tmp_path: Path, sample_png: Path, discovery: FileDiscovery
) -> None:
    """
    Given  a PNG in a subdirectory and recursive=True in the config
    When   collect_input_files() is called
    Then   the nested file is included in the result
    """
    in_dir = tmp_path / "input"
    sub = in_dir / "sub"
    sub.mkdir(parents=True)
    shutil.copy(sample_png, sub / "nested.png")
    cfg = make_config(tmp_path, input_dir=in_dir, recursive=True)
    files = discovery.collect_input_files(cfg)
    assert any(f.name == "nested.png" for f in files)


def test_collect_input_files_returns_sorted_list(tmp_path: Path, discovery: FileDiscovery) -> None:
    """
    Given  an input directory containing PNG files with unsorted names (c, a, b)
    When   collect_input_files() is called
    Then   the returned list is sorted alphabetically by filename
    """
    from PIL import Image  # noqa: PLC0415

    in_dir = tmp_path / "input"
    in_dir.mkdir(exist_ok=True)
    for name in ("c.png", "a.png", "b.png"):
        Image.new("RGB", (10, 10)).save(in_dir / name)
    cfg = make_config(tmp_path, input_dir=in_dir)
    files = discovery.collect_input_files(cfg)
    names = [f.name for f in files]
    assert names == sorted(names)


def test_collect_input_files_empty_dir_returns_empty(
    tmp_path: Path, discovery: FileDiscovery
) -> None:
    """
    Given  an empty input directory
    When   collect_input_files() is called
    Then   an empty list is returned
    """
    in_dir = tmp_path / "input"
    in_dir.mkdir()
    cfg = make_config(tmp_path, input_dir=in_dir)
    assert discovery.collect_input_files(cfg) == []


def test_collect_input_files_nonexistent_dir_returns_empty(
    tmp_path: Path, discovery: FileDiscovery
) -> None:
    """
    Given  a Config whose input_dir points to a path that does not exist
    When   collect_input_files() is called
    Then   an empty list is returned without raising
    """
    cfg = make_config(tmp_path, input_dir=tmp_path / "does_not_exist")
    assert discovery.collect_input_files(cfg) == []
