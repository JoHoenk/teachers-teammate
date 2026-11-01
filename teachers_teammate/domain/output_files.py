"""Where output files go and which output folders are allowed (pure path rules, no I/O).

Outputs keep the input's subfolder structure: ``input/class1/essay.png`` and
``input/class2/essay.png`` become ``output/class1/essay.docx`` and ``output/class2/essay.docx``
instead of one replacing the other in a flat output folder.

Callers pass resolved (absolute) paths; nothing here touches the file system.
"""

from __future__ import annotations

from pathlib import Path

DOCX_SUFFIX = ".docx"
TEXT_SUFFIX = ".txt"
ANONYMIZED_TEXT_SUFFIX = "_anonymized.txt"

TEXT_IN_INPUT_FOLDER = (
    "Text files cannot be saved in the input folder: the next run would read them as new "
    "documents. Choose a different output folder."
)


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
