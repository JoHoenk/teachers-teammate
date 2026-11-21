"""Where output files go and which output folders are allowed (pure path rules, no I/O).

Outputs keep the input's subfolder structure: ``input/class1/essay.png`` and
``input/class2/essay.png`` become ``output/class1/essay.docx`` and ``output/class2/essay.docx``
instead of one replacing the other in a flat output folder.

Callers pass resolved (absolute) paths; nothing here touches the file system.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

DOCX_SUFFIX = ".docx"
TEXT_SUFFIX = ".txt"
ANONYMIZED_TEXT_SUFFIX = "_anonymized.txt"

TEXT_IN_INPUT_FOLDER = (
    "Text files cannot be saved in the input folder: the next run would read them as new "
    "documents. Choose a different output folder."
)


class DocxFormat(StrEnum):
    """The layout of a generated Word document."""

    TABLE = "table"
    """Three columns: image | extracted text | proofread text."""

    COMMENTS = "comments"
    """Two columns, with the corrections as Word comments."""


class FolderProblem(StrEnum):
    """Why a run cannot start with the folders it was given."""

    NO_INPUT = "no_input"
    """No folder with documents was chosen."""

    NO_OUTPUT = "no_output"
    """Files are written, but no folder to write them to was chosen."""

    INPUT_MISSING = "input_missing"
    """The chosen input folder is not there."""


def folder_problem(
    input_text: str,
    output_text: str,
    *,
    input_exists: bool,
    writes_output_files: bool,
) -> FolderProblem | None:
    """Return why a run cannot start with these folders, or ``None`` when it can.

    Pure: the caller says whether the input folder exists.  The order is the one a teacher
    meets it — choose the documents, choose where the results go, then the folder must be
    there — so the first thing to fix is the one reported.
    """
    if not input_text.strip():
        return FolderProblem.NO_INPUT
    if not output_text.strip() and writes_output_files:
        return FolderProblem.NO_OUTPUT
    if not input_exists:
        return FolderProblem.INPUT_MISSING
    return None


def output_path(source: Path, input_dir: Path | None, output_dir: Path, suffix: str) -> Path:
    """Return the output file for *source*: the same subfolder below *output_dir* as below
    *input_dir*, named ``<stem><suffix>``.

    A *source* outside *input_dir* (or no *input_dir*) goes straight into *output_dir*; that
    includes a symlink that leads out of it, since callers pass the resolved path.
    """
    relative = Path()
    if input_dir is not None and source.parent.is_relative_to(input_dir):
        relative = source.parent.relative_to(input_dir)
    return output_dir / relative / (source.stem + suffix)


def feeds_inputs(folder: Path, input_dir: Path, *, recursive: bool) -> bool:
    """Return whether files written to *folder* would be found as inputs of *input_dir*."""
    return folder == input_dir or (recursive and input_dir in folder.parents)


def output_folder_refusal(
    *, writes_text_files: bool, output_dir: Path, input_dir: Path, recursive: bool
) -> str:
    """Return why a run must not use *output_dir*, or ``""`` when it is fine.

    Text files (unlike Word documents) are input types, so saving them where the run looks for
    inputs would make the next run read them as new documents.
    """
    if writes_text_files and feeds_inputs(output_dir, input_dir, recursive=recursive):
        return TEXT_IN_INPUT_FOLDER
    return ""
