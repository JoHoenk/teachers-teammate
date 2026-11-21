"""Unit tests for teachers_teammate.application.export."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.application.composition import AppServices, build_app_services
from teachers_teammate.application.export import ExportService
from teachers_teammate.application.service import AnonymizerConfig
from teachers_teammate.domain.teacher_work import text_identity
from teachers_teammate.infrastructure.state_repository import (
    OcrResultRecord,
    StateRepository,
    compute_file_hash,
)
from tests.conftest import fake_reader_store, make_config


def _services(repo: StateRepository | None = None, *, tmp_path: Path | None = None) -> AppServices:
    """Return the services of an entry point, over *repo* when the test has a stored document."""
    return build_app_services(
        discovery=MagicMock(),
        pipeline_factory=MagicMock(),
        state_repository_factory=(lambda _path: repo) if repo else MagicMock(),
        model_store=fake_reader_store(tmp_path) if tmp_path else None,
    )


def _services_with_document(
    tmp_path: Path,
    *,
    raw_text: str = "hello world",
    raw_text_format: str = "plain",
    corrected: str = "",
    review: dict | None = None,
) -> tuple[AppServices, Path]:
    """Services over a real state store holding one document the machine read."""
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
    return _services(repo, tmp_path=tmp_path / "reader"), source


# ── export_docx ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_export_docx_keeps_markdown_formatting(tmp_path: Path) -> None:
    """
    Given  a stored Markdown transcription with a crossed-out word
    When   export_docx() is called
    Then   the DOCX run for that word is struck through and no markers are written
    """
    from docx import Document  # noqa: PLC0415

    services, source = _services_with_document(
        tmp_path, raw_text="my ~~backpack~~ bag", raw_text_format="markdown"
    )
    out = tmp_path / "out" / "doc.docx"

    services.export.export_docx(make_config(tmp_path), source, out)

    cells = Document(str(out)).tables[0].rows[1].cells
    runs = [run for para in cells[1].paragraphs for run in para.runs]
    assert any(run.text == "backpack" and run.font.strike for run in runs)
    assert "~~" not in cells[1].text


@pytest.mark.unit
def test_export_docx_quotes_the_stored_review(tmp_path: Path) -> None:
    """
    Given  a stored review that explains one change
    When   export_docx() is called in the table layout
    Then   the explanation is written into the document
    """
    from docx import Document  # noqa: PLC0415

    from teachers_teammate.domain.proofreading.edits import EditPart  # noqa: PLC0415
    from teachers_teammate.domain.proofreading.review import (  # noqa: PLC0415
        CorrectionReview,
        EditNote,
    )

    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(4, 8, "have", "has"),),
                sentences=(),
                category="grammar",
                explanation='"she" is 3rd person singular.',
            ),
        )
    )
    services, source = _services_with_document(
        tmp_path,
        raw_text="She have a dog.",
        corrected="She has a dog.",
        review=review.to_dict(),
    )
    out = tmp_path / "doc.docx"

    services.export.export_docx(make_config(tmp_path), source, out)

    text = " ".join(para.text for para in Document(str(out)).paragraphs)
    assert '"she" is 3rd person singular.' in text


@pytest.mark.unit
def test_export_docx_defaults_to_plain_text(tmp_path: Path) -> None:
    """
    Given  a stored plain text that contains Markdown-looking characters
    When   export_docx() is called
    Then   the characters are written verbatim
    """
    from docx import Document  # noqa: PLC0415

    services, source = _services_with_document(tmp_path, raw_text="~~keep~~")
    out = tmp_path / "doc.docx"

    services.export.export_docx(make_config(tmp_path), source, out)

    assert "~~keep~~" in Document(str(out)).tables[0].rows[1].cells[1].text


@pytest.mark.unit
def test_export_docx_writes_the_teachers_corrections(tmp_path: Path) -> None:
    """
    Given  a document whose corrections the teacher changed
    When   export_docx() is called
    Then   the file carries their text, not the machine's
    """
    from docx import Document  # noqa: PLC0415

    services, source = _services_with_document(tmp_path, raw_text="hello world", corrected="Hello world.")
    config = make_config(tmp_path)
    services.commands.edit_corrected_text(config, source=source, text="Hello, world!")
    out = tmp_path / "doc.docx"

    services.export.export_docx(config, source, out)

    cells = Document(str(out)).tables[0].rows[1].cells
    assert cells[1].text == "hello world"
    assert cells[2].text == "Hello, world!"


@pytest.mark.unit
def test_export_docx_writes_the_teachers_transcription(tmp_path: Path) -> None:
    """
    Given  a transcription the teacher fixed
    When   export_docx() is called
    Then   the file carries their text, not the machine's
    """
    from docx import Document  # noqa: PLC0415

    services, source = _services_with_document(tmp_path, raw_text="hello world")
    config = make_config(tmp_path)
    services.commands.edit_transcription(config, source=source, text="hello, world")
    out = tmp_path / "doc.docx"

    services.export.export_docx(config, source, out)

    assert Document(str(out)).tables[0].rows[1].cells[1].text == "hello, world"


@pytest.mark.unit
def test_export_docx_refuses_a_document_without_text(tmp_path: Path) -> None:
    """
    Given  a document the app has no text for
    When   export_docx() is called
    Then   a ValueError says so instead of writing an empty file
    """
    services = _services(StateRepository(tmp_path / "state"))

    with pytest.raises(ValueError, match="No OCR text"):
        services.export.export_docx(make_config(tmp_path), tmp_path / "doc.png", tmp_path / "doc.docx")


# ── export_text / is_in_input_folder ──────────────────────────────────────


@pytest.mark.unit
def test_export_text_writes_plain_text_unchanged(tmp_path: Path) -> None:
    """
    Given  a stored plain transcription
    When   export_text() is called without anonymization
    Then   the file holds exactly that text, UTF-8 encoded, and the folder is created
    """
    services, source = _services_with_document(tmp_path, raw_text="Grüße, Anna")
    out = tmp_path / "out" / "scan.txt"

    services.export.export_text(make_config(tmp_path), source, out)

    assert out.read_text(encoding="utf-8") == "Grüße, Anna"


@pytest.mark.unit
def test_export_text_writes_markdown_as_plain_text(tmp_path: Path) -> None:
    """
    Given  a transcription stored as Markdown
    When   export_text() is called
    Then   the file holds the text without Markdown markup
    """
    services, source = _services_with_document(
        tmp_path, raw_text="my **bold** bag", raw_text_format="markdown"
    )
    out = tmp_path / "scan.txt"

    services.export.export_text(make_config(tmp_path), source, out)

    assert out.read_text(encoding="utf-8") == "my bold bag"


@pytest.mark.unit
def test_export_text_writes_the_teachers_transcription(tmp_path: Path) -> None:
    """
    Given  a transcription the teacher fixed
    When   export_text() is called
    Then   the file carries their text, not the machine's
    """
    services, source = _services_with_document(tmp_path, raw_text="hello world")
    config = make_config(tmp_path)
    services.commands.edit_transcription(config, source=source, text="hello, world")
    out = tmp_path / "scan.txt"

    services.export.export_text(config, source, out)

    assert out.read_text(encoding="utf-8") == "hello, world"


@pytest.mark.unit
def test_export_text_anonymizes_with_the_given_language_and_config(tmp_path: Path) -> None:
    """
    Given  an anonymizer that replaces "Anna" with a placeholder
    When   export_text() is called with anonymize=True
    Then   the anonymized text is written, the anonymizer got the language and config,
           and the placeholder mapping appears nowhere in the file
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("Dear [PERSON_1]", {"[PERSON_1]": "Anna"})
    config = AnonymizerConfig()
    out = tmp_path / "scan_anonymized.txt"
    services, source = _services_with_document(tmp_path, raw_text="Dear Anna")

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ) as ctor:
        services.export.export_text(
            make_config(tmp_path),
            source,
            out,
            anonymize=True,
            language="German",
            anonymizer_config=config,
        )

    ctor.assert_called_once_with("German", config)
    anonymizer.anonymize.assert_called_once_with("Dear Anna")
    assert out.read_text(encoding="utf-8") == "Dear [PERSON_1]"
    assert "Anna" not in out.read_text(encoding="utf-8")


@pytest.mark.unit
def test_export_text_refuses_to_overwrite_the_source(tmp_path: Path) -> None:
    """
    Given  a .txt input document
    When   export_text() is asked to write to the source file itself
    Then   a ValueError is raised and the source keeps its content
    """
    source = tmp_path / "essay.txt"
    source.write_text("original", encoding="utf-8")

    with pytest.raises(ValueError, match="input document"):
        _services().export.export_text(make_config(tmp_path), source, source)

    assert source.read_text(encoding="utf-8") == "original"


@pytest.mark.unit
def test_export_text_refuses_to_overwrite_another_input_document(tmp_path: Path) -> None:
    """
    Given  scan.png and scan.txt are both inputs of the same run
    When   export_text() for scan.png targets scan.txt, with scan.txt listed as protected
    Then   a ValueError is raised and scan.txt keeps its content
    """
    sibling = tmp_path / "scan.txt"
    sibling.write_text("original", encoding="utf-8")

    with pytest.raises(ValueError, match="input document"):
        _services().export.export_text(
            make_config(tmp_path),
            tmp_path / "scan.png",
            sibling,
            protected=[sibling, tmp_path / "x.pdf"],
        )

    assert sibling.read_text(encoding="utf-8") == "original"


@pytest.mark.unit
def test_export_text_anonymizes_markdown_before_converting_it_to_plain_text(
    tmp_path: Path,
) -> None:
    """
    Given  Markdown text and an anonymizer that hides "Anna"
    When   export_text() is called with anonymize=True
    Then   the anonymizer sees the stored Markdown (as in the privacy preview) and the file
           holds the anonymized text without markup, with the placeholder intact
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("my **[PERSON_1]** bag", {"[PERSON_1]": "Anna"})
    out = tmp_path / "scan_anonymized.txt"
    services, source = _services_with_document(
        tmp_path, raw_text="my **Anna** bag", raw_text_format="markdown"
    )

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        services.export.export_text(make_config(tmp_path), source, out, anonymize=True)

    anonymizer.anonymize.assert_called_once_with("my **Anna** bag")
    assert out.read_text(encoding="utf-8") == "my [PERSON_1] bag"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_text", "text_format", "anonymize"),
    [
        ("Grüße, Anna", "plain", False),
        ("my **bold** bag", "markdown", False),
        ("my **Anna** bag", "markdown", True),
    ],
)
def test_text_for_copy_is_what_export_text_writes(
    tmp_path: Path, raw_text: str, text_format: str, anonymize: bool
) -> None:
    """
    Given  a text in a notation, with and without names to hide
    When   text_for_copy() is asked for it and export_text() writes the same document
    Then   the clipboard text and the file are the same string
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.side_effect = lambda text: (text.replace("Anna", "[PERSON_1]"), {})
    out = tmp_path / "scan.txt"
    services, source = _services_with_document(tmp_path, raw_text=raw_text, raw_text_format=text_format)
    config = make_config(tmp_path)

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        view = services.documents.load_view(config, source)
        assert view is not None
        copied = services.export.text_for_copy(view, anonymize=anonymize, language="German")
        services.export.export_text(config, source, out, anonymize=anonymize, language="German")

    assert copied == out.read_text(encoding="utf-8")


@pytest.mark.unit
def test_export_text_reports_a_missing_spacy_model(tmp_path: Path) -> None:
    """
    Given  a spaCy model that is not downloaded
    When   export_text() is called with anonymize=True
    Then   the OSError reaches the caller and no file is written
    """
    out = tmp_path / "scan_anonymized.txt"
    services, source = _services_with_document(tmp_path, raw_text="text")

    with (
        patch(
            "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer",
            side_effect=OSError("model missing"),
        ),
        pytest.raises(OSError, match="model missing"),
    ):
        services.export.export_text(make_config(tmp_path), source, out, anonymize=True)

    assert not out.exists()


@pytest.mark.unit
def test_is_in_input_folder(tmp_path: Path) -> None:
    """
    Given  an input folder with a subfolder, and a sibling folder
    When   is_in_input_folder() is asked about each
    Then   the folder itself always counts, a subfolder only when scanning is recursive,
           and a sibling never
    """
    inputs = tmp_path / "in"
    (inputs / "sub").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    check = ExportService.is_in_input_folder

    assert check(inputs, inputs, recursive=False)
    assert check(inputs / "sub", inputs, recursive=True)
    assert not check(inputs / "sub", inputs, recursive=False)
    assert not check(tmp_path / "out", inputs, recursive=True)


@pytest.mark.unit
def test_export_target_keeps_input_subfolder(tmp_path: Path) -> None:
    """
    Given  two same-named documents in different subfolders of the input folder
    When   export_target() places them in one export folder
    Then   each keeps its subfolder, so they do not collide
    """
    inputs = tmp_path / "in"
    target = ExportService.export_target

    first = target(inputs / "class1" / "essay.png", inputs, tmp_path / "out", ".docx")
    second = target(inputs / "class2" / "essay.png", inputs, tmp_path / "out", ".docx")

    assert first == tmp_path / "out" / "class1" / "essay.docx"
    assert second == tmp_path / "out" / "class2" / "essay.docx"
