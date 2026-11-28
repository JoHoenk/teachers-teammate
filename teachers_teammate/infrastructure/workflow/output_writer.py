"""The output pass: what one file produced, written beside its source.

One :class:`OutputWriter` belongs to one run: an output file is claimed once for the whole run,
so two documents cannot silently write over each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

from ...domain.formatted_text import TextFormat
from ...domain.output_files import (
    ANONYMIZED_TEXT_SUFFIX,
    DOCX_SUFFIX,
    TEXT_SUFFIX,
    feeds_inputs,
    output_path,
)
from ...domain.proofreading.review import CorrectionReview
from ...domain.task_text import Segment
from ...exceptions import StageError, StageErrorKind
from ...interfaces import Anonymizer, DocumentCreator
from ...messages import (
    output_overwrites_input,
    output_read_back_as_input,
    output_replaces_another,
)
from ...texts.run import RESULTS_CACHED, docx_failed, output_not_saved, text_files_failed
from ..ocr_formats import plain_text_of
from .processing_result import ProcessingResult


@dataclass(frozen=True)
class OutputTexts:
    """The three texts one output pass writes: source, proofread and evaluation."""

    raw: str
    corrected: str
    evaluation: str
    task: tuple[Segment, ...] = ()
    """Where the task text stands in the plain *corrected* text; empty when nothing is left alone."""


class OutputWriter:
    """Writes the outputs of one file and keeps one output file to one document per run."""

    def __init__(
        self,
        *,
        output_dir: Path,
        input_dir: Path | None = None,
        recursive: bool = True,
        write_text: bool = False,
        text_anonymizer: Anonymizer | None = None,
        doc_creator: DocumentCreator | None = None,
    ) -> None:
        self._output_dir = output_dir
        self._input_dir = input_dir.resolve() if input_dir is not None else None
        self._recursive = recursive
        self._write_text = write_text
        self._text_anonymizer = text_anonymizer
        self._doc_creator = doc_creator
        # Output file -> the input document that owns it, for the whole run.
        self._claimed: dict[Path, Path] = {}
        self._claim_lock = threading.Lock()

    def write(
        self,
        file: Path,
        texts: OutputTexts,
        preview_img: str,
        source_image: Path | None,
        warnings: tuple[str, ...] = (),
        *,
        text_format: TextFormat = "plain",
        review: CorrectionReview | None = None,
        anonymized_raw: str | None = None,
    ) -> ProcessingResult:
        """Write every planned output of *file*; return the result a teacher reads."""

        def failed(message: str) -> StageError:
            return StageError(message, kind=StageErrorKind.OUTPUT)

        targets = self._planned_outputs(file)
        try:
            self._claim_outputs(file, targets)
        except ValueError as exc:
            raise failed(output_not_saved(exc)) from exc

        saved: list[str] = []
        try:
            if self._doc_creator is not None:
                docx_file = targets[DOCX_SUFFIX]
                docx_file.parent.mkdir(parents=True, exist_ok=True)
                self._doc_creator.create(
                    texts.raw,
                    texts.corrected,
                    docx_file,
                    docx_file.stem,
                    source_image=source_image,
                    text_format=text_format,
                    review=review,
                    task=texts.task,
                )
                saved.append(self._shown(docx_file))
        except Exception as exc:  # python-docx / PIL errors vary widely; surface the message
            raise failed(docx_failed(exc)) from exc
        try:
            self._write_text_files(targets, texts.raw, text_format, saved, anonymized_raw)
        except Exception as exc:  # NER / file-system errors vary; surface the message
            raise failed(text_files_failed(exc, saved)) from exc
        return ProcessingResult(
            True,
            ", ".join(saved) or RESULTS_CACHED,
            preview_img=preview_img,
            raw_text=texts.raw,
            correction_text=texts.corrected,
            evaluation_text=texts.evaluation,
            warnings=warnings,
            text_format=text_format,
            correction_review=review,
        )

    def _output_file(self, file: Path, suffix: str) -> Path:
        return output_path(file.resolve(), self._input_dir, self._output_dir, suffix)

    def _planned_outputs(self, file: Path) -> dict[str, Path]:
        """Return ``{suffix: path}`` for every output file this run writes for *file*."""
        suffixes: list[str] = []
        if self._doc_creator is not None:
            suffixes.append(DOCX_SUFFIX)
        if self._write_text:
            suffixes.append(TEXT_SUFFIX)
        if self._text_anonymizer is not None:
            suffixes.append(ANONYMIZED_TEXT_SUFFIX)
        return {suffix: self._output_file(file, suffix) for suffix in suffixes}

    def _shown(self, target: Path) -> str:
        """Name an output file by its path below the output folder (subfolders can repeat names)."""
        return target.relative_to(self._output_dir).as_posix()

    def _claim_outputs(self, file: Path, targets: dict[str, Path]) -> None:
        """Reserve *targets* for *file*; refuse targets that would harm inputs or other outputs.

        Nothing is written for a refused file, and nothing is reserved.

        Raises:
            ValueError: a text file would replace an input document or land where the run reads
                inputs, or another document of this run already writes the same output file.
        """
        source = file.resolve()
        for suffix, target in targets.items():
            if suffix == DOCX_SUFFIX:  # Word documents are not an input type
                continue
            if target.resolve() == source:
                raise ValueError(output_overwrites_input(target.name))
            if self._input_dir is not None and feeds_inputs(
                target.parent.resolve(), self._input_dir, recursive=self._recursive
            ):
                raise ValueError(output_read_back_as_input(self._shown(target)))
        with self._claim_lock:
            for target in targets.values():
                owner = self._claimed.get(target.resolve())
                if owner is not None and owner != source:
                    raise ValueError(output_replaces_another(self._shown(target), owner.name))
            for target in targets.values():
                self._claimed[target.resolve()] = source

    def _write_text_files(
        self,
        targets: dict[str, Path],
        raw_text: str,
        text_format: TextFormat,
        saved: list[str],
        anonymized_raw: str | None = None,
    ) -> None:
        """Save the extracted text (plain and/or anonymized) as UTF-8 text.

        Like the Export menu: the OCR text without markup; anonymizing runs on the text in
        its stored notation, and the placeholder mapping is dropped. Every file is appended
        to *saved* as soon as it is written, so a failure still reports what already exists.

        *anonymized_raw* is the anonymized *raw_text* of the pre-pass, when the same anonymizer
        produced it; otherwise the text file anonymizes on its own.
        """
        jobs: list[tuple[Path, str]] = []
        if TEXT_SUFFIX in targets:
            jobs.append((targets[TEXT_SUFFIX], raw_text))
        if ANONYMIZED_TEXT_SUFFIX in targets and self._text_anonymizer is not None:
            if anonymized_raw is None:
                anonymized_raw, _mapping = self._text_anonymizer.anonymize(raw_text)
            jobs.append((targets[ANONYMIZED_TEXT_SUFFIX], anonymized_raw))
        if jobs and text_format == "markdown":
            # Only Markdown needs the parser (and its dependencies); plain text is written as is.
            jobs = [(target, plain_text_of(text, text_format)) for target, text in jobs]
        for target, text in jobs:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            saved.append(self._shown(target))
