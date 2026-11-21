"""Handing a document to the teacher: the Word file and the text file they save.

An export carries what the teacher sees and never the machine's texts behind it: the
transcription they fixed, their corrections, the review as it was stored.  The text the
clipboard copies and the text the text export writes come from one function
(:meth:`ExportService.text_for_copy`), so a copy and a saved file of the same document never
differ.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..domain.output_files import feeds_inputs, output_path
from .anonymization import AnonymizationService
from .document_view import DocumentStateView
from .documents import DocumentQueries
from .formatting import TextFormatter


class NothingToExport(ValueError):
    """The document has no text to export yet."""


class WouldOverwriteInput(ValueError):
    """The export's target is an input document, which the export would replace."""

    def __init__(self, target: Path) -> None:
        super().__init__(f"{target.name} is an input document")
        self.target = target


@dataclass(frozen=True)
class DocxExport:
    """Presentation options for :meth:`ExportService.export_docx`."""

    docx_format: str = "table"
    """``"table"`` (three columns) or ``"comments"`` (diff hunks as Word comments)."""
    labels_language: str = ""
    """Language of the review headings and column titles (empty: English)."""


class ExportService:
    """Writes a document out: the Word file, the text file, and the text for the clipboard."""

    def __init__(
        self,
        *,
        documents: DocumentQueries,
        anonymization: AnonymizationService,
    ) -> None:
        # Read-only queries over the store: an export writes what the view holds.
        self._documents = documents
        # Personal-info removal, for the anonymized copy and the anonymized text file.
        self._anonymization = anonymization

    def export_docx(
        self,
        config: Config,
        source: Path,
        out_path: Path,
        *,
        options: DocxExport | None = None,
    ) -> None:
        """Write the Word file of *source* to *out_path*, from the texts it has now.

        The texts, their notation and the review come from the view, so an export carries what
        the teacher sees — their transcription and their corrections — and never the machine's
        texts behind them.  *options* selects the layout and the language of the headings.

        Raises:
            NothingToExport: the document has no text to export.
        """
        from ..infrastructure.docx_builder import DocxDocumentCreator  # noqa: PLC0415

        view = self._documents.load_view(config, source)
        if view is None or not view.raw_text.strip():
            raise NothingToExport(source.name)
        chosen = options or DocxExport()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        creator = DocxDocumentCreator(
            fmt=chosen.docx_format, labels_language=chosen.labels_language
        )
        creator.create(
            view.raw_text,
            view.correction_text,
            out_path,
            source.stem,
            source_image=view.preview_img or None,
            text_format=TextFormatter.normalize(view.raw_text_format),
            review=view.correction_review,
        )

    def text_for_copy(
        self,
        view: DocumentStateView,
        *,
        corrected: bool = False,
        anonymize: bool = False,
        language: str = "English",
        anonymizer_config: object | None = None,
    ) -> str:
        """Return the text of *view* as plain text, the way a text export writes it.

        One function for the clipboard and for the saved file, so a copy and an export of the
        same document never differ.  *corrected* takes the proofread text instead of the
        transcription.  With *anonymize* the personal data is replaced by placeholders
        (``[PERSON_1]``, ...) on the text in its stored notation, as the pipeline and the
        privacy preview do.

        Raises:
            ImportError: *anonymize* is set and spacy is not installed.
            OSError: a required spacy model is not downloaded.
        """
        text = view.correction_text if corrected else view.raw_text
        if anonymize:
            text, _mapping = self._anonymization.anonymize_preview(
                text, language, anonymizer_config
            )
        return TextFormatter.plain_text(text, view.raw_text_format)

    def export_text(
        self,
        config: Config,
        source: Path,
        out_path: Path,
        *,
        anonymize: bool = False,
        language: str = "English",
        anonymizer_config: object | None = None,
        protected: Collection[Path] = (),
    ) -> None:
        """Write the transcription of *source* to *out_path* as a UTF-8 text file.

        The text and its notation come from the view, so the file carries the teacher's
        transcription where they fixed or checked it.  Markdown is written as plain text.  With
        *anonymize* the personal data is replaced by placeholders (``[PERSON_1]``, ...) using
        the local anonymizer, on the text in its stored notation (as the pipeline and the
        privacy preview do); the placeholder mapping is dropped, so the original names cannot
        be recovered from the file.

        Args:
            protected: Further documents that must not be overwritten (the other inputs of
                the same export); *source* itself is always protected.

        Raises:
            NothingToExport: the document has no text.
            WouldOverwriteInput: *out_path* is *source* or one of *protected* (an input
                document).
            ImportError: *anonymize* is set and spacy is not installed.
            OSError: the file cannot be written, or a required spacy model is not downloaded.
        """
        target = out_path.resolve()
        if target == source.resolve() or target in {p.resolve() for p in protected}:
            raise WouldOverwriteInput(out_path)
        view = self._documents.load_view(config, source)
        if view is None or not view.raw_text.strip():
            raise NothingToExport(source.name)
        text = self.text_for_copy(
            view,
            anonymize=anonymize,
            language=language,
            anonymizer_config=anonymizer_config,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")

    @staticmethod
    def is_in_input_folder(folder: Path, input_dir: Path, *, recursive: bool) -> bool:
        """Return whether files written to *folder* would be found as inputs of *input_dir*."""
        return feeds_inputs(folder.resolve(), input_dir.resolve(), recursive=recursive)

    @staticmethod
    def export_target(source: Path, input_dir: Path | None, folder: Path, suffix: str) -> Path:
        """Return where a folder export saves *source*: the subfolder it has below
        *input_dir*, recreated below *folder* (so same-named files in different subfolders
        do not replace each other).
        """
        return output_path(
            source.resolve(), input_dir.resolve() if input_dir else None, folder, suffix
        )
