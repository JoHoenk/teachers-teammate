"""The page set a comparison is read against: its pages, their transcriptions, its version.

The set is the developer's own pages, outside the repository, in the layout
``tools/dev/page_lines.py`` reads: a folder per group (``handwritten_scan``, ``printed_photo``,
…), and beside every page image its transcription: ``s01.png`` is read against ``s01.gt.txt``.
A PDF's pages are counted like the line measure counts them.  The folder is the page's group.

Beside the groups sits ``pages.jsonl``, one object per page, and every page of the set is in it::

    {"id": "s01", "writer": "w3", "language": "de"}

The ids are neutral on purpose: a record holds them and nothing else that names a page.  The
language, a code or a name, is what the page's prompt names; a page without one is asked in the
run's language.

A transcription writes one reference line per written line, in reading order, letter for letter
with the student's mistakes, crossed-out text left out.  A misspelt word is wrapped in braces as
the student wrote it (``{Fehler}``); ``[?]`` stands for a word nobody can read.

The set has a version: the hash of its images and transcriptions.  Runs of different versions are
not compared, so two campaigns cannot quietly mix.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import sys

from teachers_teammate.domain.language import language_name
from teachers_teammate.infrastructure.input_providers import PdfInputProvider, pdf_page_count
from teachers_teammate.interfaces import ImageUnit
from tools.evaluation.core.hashing import file_sha256, sha256_json

from .records import ALL, CHECK, TUNE

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff"})
_PDF_SUFFIXES = frozenset({".pdf"})
REFERENCE_SUFFIX = ".gt.txt"
"""The transcription beside a page ``s01.png``: ``s01.gt.txt``."""

METADATA_NAME = "pages.jsonl"
"""What a page is, beside the groups: one object per page id."""

SPLIT_SEED = 20261128
"""The seed the set is split with; the split is made once and does not follow a run."""

TUNE_SHARE = 0.7
"""The share of the pages a campaign may choose on; the rest is the check set."""


@dataclass(frozen=True)
class Case:
    """One page and the transcription beside it: a page image, or one page of a PDF."""

    image: Path
    """The file the page is read from; the file itself, or one page of it rendered."""
    reference: str
    group: str
    """The folder the page sits in."""
    page_id: str
    """The id a record names it by: the file's stem, or ``scan_p2`` for a page of a longer PDF."""
    page: int | None = None
    """Which page of the PDF *image* is, from 0; ``None`` when the file is the page itself."""


@dataclass(frozen=True)
class PageMeta:
    """What ``pages.jsonl`` says about one page id."""

    id: str
    writer: str
    """The neutral id of the hand; a page that names none is its own writer."""
    language: str = ""
    """The language name the page's prompt names (``de`` is read as ``German``), or ``""``."""


def page_files(path: Path) -> list[Path]:
    """Return the page files *path* names, a folder contributing every image and PDF below it."""
    suffixes = _IMAGE_SUFFIXES | _PDF_SUFFIXES
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.suffix.lower() in suffixes)
    return [path] if path.suffix.lower() in suffixes else []


def page_count(file: Path) -> int:
    """Return how many pages *file* holds: a PDF's pages, one for an image, 0 if unreadable."""
    if file.suffix.lower() in _PDF_SUFFIXES:
        return pdf_page_count(file)
    return 1


def transcription(file: Path, page: int, pages: int) -> Path:
    """Return the transcription page *page* of *file* is read against.

    ``s01.png`` and a one-page ``s01.pdf`` sit beside ``s01.gt.txt``; the pages of a longer PDF
    are counted, ``scan.pdf`` page 2 → ``scan_p2.gt.txt``.
    """
    if pages == 1:
        return file.with_name(file.stem + REFERENCE_SUFFIX)
    return file.with_name(f"{file.stem}_p{page + 1}{REFERENCE_SUFFIX}")


def rendered(file: Path, folder: Path) -> tuple[Path, ...]:
    """Return every page of *file*, rendered into *folder* the way a run renders it.

    A PDF is rendered at the app's OCR resolution by the app's own provider; the caller keeps
    *folder* alive while it reads the pages.  Raises :exc:`ValueError` for a file that has no
    pages as images.
    """
    payload = PdfInputProvider(folder, text_layer="never").load(file)
    return tuple(unit.path for unit in payload.units if isinstance(unit, ImageUnit))


def page_id(file: Path, page: int, pages: int) -> str:
    """Return the id a record names page *page* of *file* by: the stem, or ``scan_p2``."""
    return file.stem if pages == 1 else f"{file.stem}_p{page + 1}"


def cases(paths: Sequence[Path]) -> list[Case]:
    """Return every page with its transcription; the folder a page sits in is its group.

    A page without a transcription is skipped and said so on stderr: it cannot be scored, and a
    set that silently loses pages would compare two different things.
    """
    found: list[Case] = []
    for file in (file for path in paths for file in page_files(path)):
        count = page_count(file)
        if not count:
            print(f"{file.name}: cannot be read, skipped", file=sys.stderr)
            continue
        for number in range(count):
            reference = transcription(file, number, count)
            if not reference.is_file():
                where = file.name if count == 1 else f"{file.name} page {number + 1}"
                print(f"{where}: no {reference.name}, skipped", file=sys.stderr)
                continue
            found.append(
                Case(
                    image=file,
                    reference=reference.read_text(encoding="utf-8"),
                    group=file.parent.name,
                    page_id=page_id(file, number, count),
                    page=number if file.suffix.lower() in _PDF_SUFFIXES else None,
                )
            )
    return found


def metadata(folder: Path) -> dict[str, PageMeta]:
    """Return what ``pages.jsonl`` in *folder* says, by page id (empty when there is none).

    Raises:
        ValueError: naming a line that is not an object with an id.
    """
    path = folder / METADATA_NAME
    if not path.is_file():
        return {}
    found: dict[str, PageMeta] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        entry = _parsed(line, path, number)
        found[str(entry["id"])] = PageMeta(
            id=str(entry["id"]),
            writer=str(entry.get("writer") or entry["id"]),
            language=language_name(str(entry.get("language", ""))),
        )
    return found


def _parsed(line: str, path: Path, number: int) -> dict:
    """Return one line of ``pages.jsonl`` as an object, refusing a line that is not one."""
    try:
        entry = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path.name} line {number}: {error}") from error
    if not isinstance(entry, dict) or "id" not in entry:
        raise ValueError(f"{path.name} line {number}: no id")
    return entry


def set_version(pages: Sequence[Case]) -> str:
    """Return the version of a set: the hash of its pages' ids, images and transcriptions.

    Two runs are compared only when this is the same, so an edited transcription or a replaced
    image never pools with the figures of the old one.
    """
    parts = sorted(
        (
            case.page_id,
            str(case.page or 0),
            file_sha256(case.image),
            sha256_json(case.reference),
        )
        for case in pages
    )
    return sha256_json(parts)


def split(
    pages: Sequence[Case],
    meta: Mapping[str, PageMeta],
    *,
    seed: int = SPLIT_SEED,
    tune_share: float = TUNE_SHARE,
) -> dict[str, str]:
    """Return which side of the set each page id is on, whole writers held out.

    The writers are ordered by a hash of the seed and their name, so the same set and seed give
    the same split whatever the run asks; writers are taken for the tune set until it holds
    *tune_share* of the pages.  Both sides get a page when there are two writers to divide.
    Every page of *pages* is in *meta*.
    """
    writers = sorted({meta[case.page_id].writer for case in pages})
    ranked = sorted(writers, key=lambda writer: sha256_json([seed, writer]))
    counts = dict.fromkeys(writers, 0)
    for case in pages:
        counts[meta[case.page_id].writer] += 1
    wanted = tune_share * len(pages)
    tune: set[str] = set()
    taken = 0
    for writer in ranked[:-1]:
        if taken >= wanted:
            break
        tune.add(writer)
        taken += counts[writer]
    if not tune:
        tune.add(ranked[0])
    return {case.page_id: TUNE if meta[case.page_id].writer in tune else CHECK for case in pages}


@dataclass(frozen=True)
class PageSet:
    """A page set as a run reads it: its pages, what ``pages.jsonl`` says, its sides, its version."""

    cases: tuple[Case, ...]
    meta: Mapping[str, PageMeta]
    sides: Mapping[str, str]
    """The side of the split each page id is on: ``tune`` or ``check``."""
    version: str

    def side(self, wanted: str) -> tuple[Case, ...]:
        """Return the pages on the side *wanted*, or every page for ``all``.

        Raises:
            ValueError: when that side holds no page.
        """
        found = tuple(
            case for case in self.cases if wanted == ALL or self.sides[case.page_id] == wanted
        )
        if not found:
            raise ValueError(f"no page on the {wanted} side of this set")
        return found


def load(root: Path) -> PageSet:
    """Return the page set at *root*, checked so that every page can be scored and named.

    Raises:
        ValueError: if no page has a transcription, two pages bear one id, there is no
            ``pages.jsonl``, or it leaves a page out.
    """
    found = cases([root])
    if not found:
        raise ValueError(f"{root}: no page with a {REFERENCE_SUFFIX} beside it")
    duplicated = _duplicated_id(found)
    if duplicated:
        raise ValueError(duplicated)
    meta = metadata(root if root.is_dir() else root.parent)
    if not meta:
        raise ValueError(
            f"{root}: no {METADATA_NAME}; a page's writer is what the split and the report's"
            " intervals are made of"
        )
    unnamed = sorted(case.page_id for case in found if case.page_id not in meta)
    if unnamed:
        raise ValueError(
            f"{METADATA_NAME} names no page {', '.join(unnamed)}; every page of the set is in it,"
            " so a record holds only the ids it names"
        )
    return PageSet(
        cases=tuple(found),
        meta=meta,
        sides=split(found, meta),
        version=set_version(found),
    )


def _duplicated_id(found: Sequence[Case]) -> str:
    """Return what to say about two pages of one id, naming both files; ``""`` when there is none.

    A record names a page by its id, so a set that holds one id twice cannot be scored.  Two groups
    of the developer's set may well name their pages alike.
    """
    seen: dict[str, str] = {}
    for case in found:
        where = f"{case.group}/{case.image.name}"
        if case.page_id in seen:
            return (
                f"{case.page_id}: two pages bear this id, {seen[case.page_id]} and {where};"
                " a record names a page by its id, so a set names each page once"
            )
        seen[case.page_id] = where
    return ""
