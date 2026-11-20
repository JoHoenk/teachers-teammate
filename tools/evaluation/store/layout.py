"""Where the new store keeps its files, and what a run file is called.

The store is one directory with three parts::

    <root>/testsets/<id>/<language>.jsonl     one record per text, per language
    <root>/testsets/<id>/manifest.json        what pins the set without holding its text
    <root>/runs/<folder>/<name>.run.jsonl     one run file, header first
    <root>/marks/<marker>__<question>.jsonl   one marker's verdicts on one set

``<folder>`` is the folder a converted run came from (``results-exchange``).  It is provenance and
not part of the format: a run file names its test set in its header, and its own name is a label
derived from the axes, so nothing reads the folder back.  Keeping it makes the conversion
checkable per folder and leaves the old folders recognisable beside the new ones.

The committed export has no folders: :func:`~..publish.export.export` writes
``<out>/runs/<name>.run.jsonl``, because a local folder name means nothing in the repository and
the name is unique by run key.  So :func:`run_path` is called without a folder there, and
:func:`~..report.load.load_export` reads one level only.

The old store (``~/gec-testset/results-*``, ``set-2026-12``) stays untouched as the backup until
the acceptance tests pass; this module only ever names paths below the new root.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

STORE_DIR = "store"
"""The directory of the new store, below the workspace the tools are pointed at."""

TESTSETS_DIR = "testsets"
RUNS_DIR = "runs"
MARKS_DIR = "marks"
"""The three parts of the store."""

MANIFEST_NAME = "manifest.json"
"""What pins a test set: its id, its hashes and one entry per text, without holding text."""

ACCEPTED_NAME = "accepted.jsonl"
"""What the accepted alternatives of a test set are called, beside its records and its manifest."""

RUN_SUFFIX = ".run.jsonl"
"""The extension of a run file, so a folder of them is one glob and a stray file is not a run."""

MARKS_SEPARATOR = "__"
"""What separates the marker from the question hash in a marks file's name."""


def store_root(path: Path) -> Path:
    """Return the store root of *path*: the directory that holds ``testsets/``, ``runs/``, ``marks/``.

    A path inside the store resolves to the store it is in; any other path is taken as the root
    itself, so a command line can name either the store or a file in it.
    """
    resolved = path.expanduser()
    for parent in (resolved, *resolved.parents):
        if parent.name == STORE_DIR:
            return parent
    return resolved


def testset_dir(root: Path, testset: str) -> Path:
    """Return the directory of test set *testset*."""
    return root / TESTSETS_DIR / testset


def testset_file(root: Path, testset: str, language: str) -> Path:
    """Return the record file of one language of *testset*."""
    return testset_dir(root, testset) / f"{language}.jsonl"


def manifest_path(root: Path, testset: str) -> Path:
    """Return the manifest that pins *testset*."""
    return testset_dir(root, testset) / MANIFEST_NAME


def accepted_path(root: Path, testset: str) -> Path:
    """Return the file of the accepted alternatives of *testset*."""
    return testset_dir(root, testset) / ACCEPTED_NAME


def run_dir(root: Path, folder: str = "") -> Path:
    """Return the directory of run files, or one folder of them."""
    return root / RUNS_DIR / folder if folder else root / RUNS_DIR


def run_path(root: Path, name: str, folder: str = "") -> Path:
    """Return the file of the run called *name*, which already carries :data:`RUN_SUFFIX`."""
    return run_dir(root, folder) / name


def run_files(root: Path, folder: str | None = None) -> list[Path]:
    """Return every run file of the store, in path order; *folder* narrows it to one folder.

    Run files sit one level down, in the folder they came from, so the whole store is searched
    recursively; a folder is searched at its own level, which is where its files are.  Only files
    count: a directory that happens to end in :data:`RUN_SUFFIX` is not a run.
    """
    base = run_dir(root, folder) if folder is not None else root / RUNS_DIR
    return sorted(path for path in base.rglob(f"*{RUN_SUFFIX}") if path.is_file())


def run_folders(root: Path) -> list[str]:
    """Return the folders of run files, in name order, so a conversion can be checked per folder."""
    base = root / RUNS_DIR
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())


def marks_path(root: Path, marker: str, question_sha256: str) -> Path:
    """Return the marks file of one marker on one test set, named by the set's question hash."""
    return root / MARKS_DIR / f"{marker}{MARKS_SEPARATOR}{question_sha256}.jsonl"


def marks_files(root: Path) -> list[Path]:
    """Return every marks file of the store, in path order."""
    return sorted((root / MARKS_DIR).glob("*.jsonl"))


def marker_of(path: Path) -> str:
    """Return the marker a marks file belongs to, from its name."""
    return path.stem.split(MARKS_SEPARATOR, 1)[0]


def question_of(path: Path) -> str:
    """Return the question hash a marks file belongs to, from its name."""
    parts = path.stem.split(MARKS_SEPARATOR, 1)
    return parts[1] if len(parts) > 1 else ""


def languages_of(root: Path, testset: str) -> list[str]:
    """Return the languages a test set holds, in the table's order, from the files it has.

    Only a file named like a language code is one: a set's folder also holds ``accepted.jsonl``,
    and a person may leave a note beside it.
    """
    from ..core.languages import is_language_code, ordered  # noqa: PLC0415  # import-light

    directory = testset_dir(root, testset)
    if not directory.is_dir():
        return []
    return ordered(path.stem for path in directory.glob("*.jsonl") if is_language_code(path.stem))


def testsets(root: Path) -> list[str]:
    """Return the ids of the test sets the store holds, in name order."""
    base = root / TESTSETS_DIR
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())


def missing(root: Path, names: Iterable[str]) -> list[str]:
    """Return the names of *names* that the store does not have, so a caller can refuse early."""
    return [name for name in names if not testset_dir(root, name).is_dir()]


__all__ = [
    "ACCEPTED_NAME",
    "MANIFEST_NAME",
    "MARKS_DIR",
    "MARKS_SEPARATOR",
    "RUNS_DIR",
    "RUN_SUFFIX",
    "STORE_DIR",
    "TESTSETS_DIR",
    "accepted_path",
    "languages_of",
    "manifest_path",
    "marker_of",
    "marks_files",
    "marks_path",
    "missing",
    "question_of",
    "run_dir",
    "run_files",
    "run_folders",
    "run_path",
    "store_root",
    "testset_dir",
    "testset_file",
    "testsets",
]
