"""The scan's marks from end to end: a run reads a two-page scan, and the Check tab marks it.

A real ``OCRPipeline`` stores the reading with the places of its lines; the application's own
services load the document as the Check tab does and mark the sentence under a cursor.  Only the
readers are stand-ins: the built-in one answers with lines and boxes, the Ollama one with text.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from teachers_teammate.application.check_document import CheckDocument, mark_for_cursor
from teachers_teammate.application.composition import build_app_services
from teachers_teammate.config import Config
from teachers_teammate.domain.page_layout import Box, PageReading, ReadLine
from teachers_teammate.infrastructure.file_discovery import FileDiscovery
from teachers_teammate.infrastructure.ollama_client import OllamaClient
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.pipeline import OCRPipeline
from teachers_teammate.infrastructure.teacher_work_store import TeacherWorkStore
from tests.conftest import fake_reader_store, make_config, pipeline_dependencies
from tests.fakes.fake_reader import mock_reader

_CAT = "Die Katze sitzt auf der Matte."
_DOG = "Der Hund bellt laut."


def _box(top: float) -> Box:
    """Return the box of a line whose top edge is at *top* of the page."""
    return ((0.1, top), (0.9, top), (0.9, top + 0.05), (0.1, top + 0.05))


#: What the built-in reader sees on each page: one line, with its box and confidence.
_PAGES = (
    PageReading(_CAT, (ReadLine(_CAT, 0.97, (_box(0.2),), box_texts=(_CAT,)),)),
    PageReading(_DOG, (ReadLine(_DOG, 0.72, (_box(0.6),), box_texts=(_DOG,)),)),
)


def _scan(input_dir: Path) -> Path:
    """Write a PDF of two pages without a text layer, so both are read, and return its path."""
    from PIL import Image  # noqa: PLC0415

    pages = [Image.new("RGB", (200, 280), "white") for _ in _PAGES]
    path = input_dir / "essay.pdf"
    pages[0].save(path, save_all=True, append_images=pages[1:])
    return path


def _built_in_reader() -> MagicMock:
    """Return a built-in reader that gives the lines of :data:`_PAGES`, page by page."""
    reader = MagicMock()
    reader.read_page.side_effect = list(_PAGES)
    return reader


def _config(tmp_path: Path, **overrides: object) -> Config:
    return make_config(
        tmp_path,
        correction_enabled=False,
        docx_enabled=False,
        preprocess_method="none",
        **overrides,
    )


def _run(tmp_path: Path, cfg: Config, store: ModelStore) -> int:
    return (
        OCRPipeline(
            cfg,
            FileDiscovery().collect_input_files(cfg),
            dependencies=pipeline_dependencies(tmp_path, model_store=store),
        )
        .run()
        .exit_code
    )


def _check_document(cfg: Config, source: Path, store: ModelStore) -> CheckDocument:
    """Return the document as the Check tab loads it."""
    services = build_app_services(model_store=store, work_store=TeacherWorkStore())
    doc = services.documents.check_document(cfg, source)
    assert doc is not None
    return doc


def _assert_marks_each_page(doc: CheckDocument) -> None:
    """Assert that each sentence is marked on its own page, over its own line."""
    cat = mark_for_cursor(doc, doc.raw_plain.index("Katze"))
    dog = mark_for_cursor(doc, doc.raw_plain.index("Hund"))

    assert cat is not None
    assert cat.polygons == ((0, _box(0.2)),)
    assert cat.shade == 0
    assert dog is not None
    assert dog.polygons == ((1, _box(0.6)),)
    assert dog.shade > cat.shade


def test_a_run_with_the_built_in_reader_marks_each_sentence_on_its_page(tmp_path: Path) -> None:
    """
    Given  a two-page scan read by the built-in reader, which gives each page's line and box
    When   the run stores the reading and the Check tab loads it
    Then   the cursor in each sentence marks that sentence's line on its own page, shaded by how
           sure the reader was
    """
    cfg = _config(tmp_path, ocr_engine="ppocr")
    source = _scan(cfg.input_dir)
    store = ModelStore()

    with patch(
        "teachers_teammate.infrastructure.stage_builder.PpOcrProcessor",
        return_value=_built_in_reader(),
    ):
        assert _run(tmp_path, cfg, store) == 0

    doc = _check_document(cfg, source, store)

    assert doc.raw_plain == f"Page 1:\n{_CAT}\nPage 2:\n{_DOG}"
    _assert_marks_each_page(doc)


def test_a_run_with_an_ollama_reader_marks_through_the_built_in_reader(tmp_path: Path) -> None:
    """
    Given  a two-page scan read by an Ollama reader, which misreads one word and gives no lines,
           and the built-in reader's models in place
    When   the run stores the reading and the Check tab loads it
    Then   the built-in reader read each page alongside, and the cursor in each sentence of the
           Ollama reading marks that sentence's line on its own page
    """
    cfg = _config(tmp_path, ocr_engine="ollama", ocr_model="my-model")
    source = _scan(cfg.input_dir)
    store = fake_reader_store(tmp_path)
    ollama = mock_reader()
    ollama.process_image.side_effect = [_CAT, "Der Hund belt laut."]
    built_in = _built_in_reader()

    with (
        patch.object(OllamaClient, "check_model"),
        patch(
            "teachers_teammate.infrastructure.stage_builder.OllamaOCRProcessor",
            return_value=ollama,
        ),
        patch(
            "teachers_teammate.infrastructure.stage_builder.PpOcrProcessor",
            return_value=built_in,
        ),
    ):
        assert _run(tmp_path, cfg, store) == 0

    doc = _check_document(cfg, source, store)

    assert built_in.read_page.call_count == len(_PAGES)
    assert "belt" in doc.raw_plain
    _assert_marks_each_page(doc)


def test_a_run_with_the_marks_switched_off_marks_nothing(tmp_path: Path) -> None:
    """
    Given  a two-page scan read by the built-in reader, with the scan's marks switched off
    When   the run stores the reading and the Check tab loads it
    Then   the document has no places, and no cursor marks anything
    """
    cfg = _config(tmp_path, ocr_engine="ppocr", scan_marks=False)
    source = _scan(cfg.input_dir)
    store = ModelStore()

    with patch(
        "teachers_teammate.infrastructure.stage_builder.PpOcrProcessor",
        return_value=_built_in_reader(),
    ):
        assert _run(tmp_path, cfg, store) == 0

    doc = _check_document(cfg, source, store)

    assert doc.scan_boxes == ()
    assert mark_for_cursor(doc, doc.raw_plain.index("Katze")) is None
