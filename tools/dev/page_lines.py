"""Developer tool: do the reader's rows line up with the written lines of a page.

NOT run in CI - it needs the built-in reader's models and the developer's own pages.

    bazel run //tools/dev:page_lines -- --pages ~/pages

``--pages`` names folders; the folder a page sits in is its group (``handwritten``,
``photographed``, ``printed``, …).  Beside every page image sits its transcription, with one
reference line per written line: ``s01.png`` is read against ``s01.gt.txt``.

A PDF is read page by page, each page rendered the way a run renders it.  A one-page PDF names
its transcription like an image (``s01.pdf`` → ``s01.gt.txt``); a page of a longer one names the
page (``scan.pdf`` page 2 → ``scan_p2.gt.txt``).

Each page is read with the built-in reader on the app's own preparation (the preset ``R``, the
automatic steps), and the reader's words are aligned with the transcription's words
(``domain/proofreading/word_diff.py``).  A row is

* *lined up* when at least 90 % of its aligned words come from one reference line and that line
  sends at least 90 % of its own aligned words back to this row;
* *merged* when its words come from two reference lines (one row, two written lines);
* *split* when its words come from one line that also sends words to another row;
* *unaligned* when none of its words was read as any reference word.

A row *has an error* when any of its words is not read exactly.

The report gives, per group, the pages, the rows and their kinds, and, for confidence thresholds
0.50 … 0.95, how many rows a *Check first* mark would show at that threshold, how many of those
hold an error, and how many error rows it would catch.  Numbers only: no page's text is printed.

Exit code: 0 when every page with a transcription was read, 1 when there was nothing to read or
the reader failed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import sys
import tempfile
from typing import Literal

from teachers_teammate.config import OcrConfig
from teachers_teammate.domain.data_location import ENGINE_PPOCR
from teachers_teammate.domain.page_layout import ReadLine
from teachers_teammate.domain.proofreading.word_diff import diff_opcodes, tokenize
from teachers_teammate.exceptions import ModelMissingError, OCRError
from teachers_teammate.infrastructure.input_providers import PdfInputProvider, pdf_page_count
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.ppocr.engine import PpOcrProcessor
from teachers_teammate.infrastructure.preprocessing.recipes import steps_for
from teachers_teammate.infrastructure.preprocessing.steps import StepPipeline
from teachers_teammate.interfaces import ImageUnit
from teachers_teammate.texts import install

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff"})
_PDF_SUFFIXES = frozenset({".pdf"})
_REFERENCE_SUFFIX = ".gt.txt"
"""The transcription beside a page ``s01.png``: ``s01.gt.txt``."""

LINED_UP_SHARE = 0.9
"""The share of its words a row and one reference line must send each other to line up."""

THRESHOLDS = tuple(round(0.50 + 0.05 * step, 2) for step in range(10))
"""The confidence thresholds the sweep tries, 0.50 to 0.95."""

RowKind = Literal["lined_up", "merged", "split", "unaligned"]


@dataclass(frozen=True)
class Row:
    """One of the reader's rows on one page, scored against the transcription."""

    kind: RowKind
    error: bool
    """One of its words was not read exactly."""
    confidence: float


@dataclass(frozen=True)
class Page:
    """The rows one page was read as, and the group the page belongs to."""

    group: str
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Case:
    """One page and the transcription beside it: a page image, or one page of a PDF."""

    image: Path
    reference: str
    group: str
    page: int | None = None
    """Which page of the PDF *image* is, from 0; ``None`` when the file is the page itself."""


@dataclass(frozen=True)
class Sweep:
    """What one confidence threshold would mark as unsure, and what that finds."""

    threshold: float
    flagged: int
    flagged_with_error: int
    error_rows: int


def _words(lines: Sequence[ReadLine]) -> list[tuple[str, int]]:
    """Return the reader's words, each with the index of the line it sits in."""
    return [
        (token, index)
        for index, line in enumerate(lines)
        for token in tokenize(line.text)
        if token.strip()
    ]


def _reference_words(reference: str) -> list[tuple[str, int]]:
    """Return the transcription's words, each with the index of its written line."""
    return [
        (token, index)
        for index, line in enumerate(reference.splitlines())
        for token in tokenize(line)
        if token.strip()
    ]


def _aligned(
    reader: Sequence[tuple[str, int]], reference: Sequence[tuple[str, int]]
) -> list[int | None]:
    """Return, for each reader word, the index of the reference word it was read as."""
    matched: list[int | None] = [None] * len(reader)
    opcodes = diff_opcodes([word for word, _ in reader], [word for word, _ in reference])
    for tag, i1, i2, j1, _ in opcodes:
        if tag != "equal":
            continue
        for offset in range(i2 - i1):
            matched[i1 + offset] = j1 + offset
    return matched


def _rows(lines: Sequence[ReadLine], reference: str) -> tuple[Row, ...]:
    """Return one scored row per line the reader gave for the page.

    A word matches when the word diff pairs it with a reference word; the words a reference
    line sends decide whether a row and that line point at each other.
    """
    reader = _words(lines)
    words = _reference_words(reference)
    matched = _aligned(reader, words)
    matched_of_line: Counter[int] = Counter()
    for target in matched:
        if target is not None:
            matched_of_line[words[target][1]] += 1

    rows: list[Row] = []
    for row, line in enumerate(lines):
        heard = 0
        hits: list[int] = []
        for index, (_, at) in enumerate(reader):
            if at != row:
                continue
            heard += 1
            if (target := matched[index]) is not None:
                hits.append(target)
        error = len(hits) < heard
        if not hits:
            rows.append(Row("unaligned", error, line.confidence))
            continue
        lines_of_word = Counter(words[target][1] for target in hits)
        written, sent = lines_of_word.most_common(1)[0]
        lined_up = (
            sent / len(hits) >= LINED_UP_SHARE and sent / matched_of_line[written] >= LINED_UP_SHARE
        )
        kind: RowKind = (
            "lined_up" if lined_up else ("merged" if len(lines_of_word) > 1 else "split")
        )
        rows.append(Row(kind, error, line.confidence))
    return tuple(rows)


def _sweep(rows: Sequence[Row], threshold: float) -> Sweep:
    """Return what *threshold* would mark and find among *rows*."""
    flagged = [row for row in rows if row.confidence < threshold]
    return Sweep(
        threshold=threshold,
        flagged=len(flagged),
        flagged_with_error=sum(1 for row in flagged if row.error),
        error_rows=sum(1 for row in rows if row.error),
    )


def _share(count: int, total: int) -> str:
    """Return *count* of *total* as a count and a share, or ``0`` when there is nothing to divide."""
    return f"{count} ({count / total:.0%})" if total else "0"


def _report(pages: Sequence[Page]) -> str:
    """Return the per-group table and, per group, the threshold sweep."""
    if not pages:
        return "No page was read."
    report: list[str] = [
        "| Group | Pages | Rows | Lined up | Merged | Split | Unaligned | Rows with an error |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for group in dict.fromkeys(page.group for page in pages):
        mine = [page for page in pages if page.group == group]
        rows = [row for page in mine for row in page.rows]
        kinds = Counter(row.kind for row in rows)
        report.append(
            f"| {group} | {len(mine)} | {len(rows)} | {_share(kinds['lined_up'], len(rows))} "
            f"| {_share(kinds['merged'], len(rows))} | {_share(kinds['split'], len(rows))} "
            f"| {_share(kinds['unaligned'], len(rows))} "
            f"| {_share(sum(1 for row in rows if row.error), len(rows))} |"
        )
    for group in dict.fromkeys(page.group for page in pages):
        rows = [row for page in pages if page.group == group for row in page.rows]
        report += [
            "",
            f"{group}: {len(rows)} rows, "
            f"{sum(1 for row in rows if row.error)} of them with an error",
            "",
            "| Threshold | Rows marked | Of those, with an error | Error rows caught |",
            "| --- | --- | --- | --- |",
        ]
        for threshold in THRESHOLDS:
            sweep = _sweep(rows, threshold)
            report.append(
                f"| {sweep.threshold:.2f} | {_share(sweep.flagged, len(rows))} "
                f"| {_share(sweep.flagged_with_error, sweep.flagged)} "
                f"| {_share(sweep.flagged_with_error, sweep.error_rows)} |"
            )
    return "\n".join(report)


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
        return file.with_name(file.stem + _REFERENCE_SUFFIX)
    return file.with_name(f"{file.stem}_p{page + 1}{_REFERENCE_SUFFIX}")


def _cases(paths: Sequence[Path]) -> list[Case]:
    """Return every page with its transcription, the folder it sits in its group."""
    cases: list[Case] = []
    for file in (file for path in paths for file in page_files(path)):
        count = page_count(file)
        if not count:
            print(f"{file.name}: cannot be read, skipped", file=sys.stderr)
            continue
        is_pdf = file.suffix.lower() in _PDF_SUFFIXES
        for number in range(count):
            reference = transcription(file, number, count)
            if not reference.is_file():
                where = file.name if count == 1 else f"{file.name} page {number + 1}"
                print(f"{where}: no {reference.name}, skipped", file=sys.stderr)
                continue
            case_page = number if is_pdf else None
            cases.append(
                Case(file, reference.read_text(encoding="utf-8"), file.parent.name, case_page)
            )
    return cases


def _rendered(pdf: Path, tmp_dir: Path) -> list[Path]:
    """Return every page of *pdf*, rendered the way a run renders it."""
    payload = PdfInputProvider(tmp_dir, text_layer="never").load(pdf)
    return [unit.path for unit in payload.units if isinstance(unit, ImageUnit)]


def _prepared(image: Path, tmp_dir: Path, ocr: OcrConfig, store: ModelStore) -> Path:
    """Return *image* prepared the way a run prepares it: the preset ``R``."""
    preprocessor = StepPipeline(steps_for(ocr, store), tmp_dir=tmp_dir)
    prepared, _steps = preprocessor.preprocess(image)
    return prepared


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Return the parsed command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pages",
        nargs="+",
        required=True,
        type=Path,
        help="the pages: image or PDF files, or folders of them, a folder's name being a group",
    )
    parser.add_argument("--language", default="English", help="the language of the pages")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Read every page, score its rows and print the report; return the exit code."""
    args = _parse(argv)
    install()
    cases = _cases(args.pages)
    if not cases:
        named = ", ".join(str(path) for path in args.pages)
        print(f"no page with a {_REFERENCE_SUFFIX} below {named}", file=sys.stderr)
        return 1
    ocr = OcrConfig(engine=ENGINE_PPOCR)
    store = ModelStore()
    reader = PpOcrProcessor(store)
    pages: list[Page] = []
    rendered: dict[Path, list[Path]] = {}
    with tempfile.TemporaryDirectory(prefix="page-lines-") as tmp:
        # One page at a time: two groups may hold a page of the same name, and the prepared
        # page is read before the next one is written.
        for case in cases:
            source = case.image
            if case.page is not None:
                if case.image not in rendered:
                    try:
                        rendered[case.image] = _rendered(case.image, Path(tmp))
                    except Exception as error:  # noqa: BLE001  # a damaged PDF is the developer's
                        print(f"{case.image}: {error}", file=sys.stderr)
                        return 1
                source = rendered[case.image][case.page]
            try:
                reading = reader.read_page(_prepared(source, Path(tmp), ocr, store), args.language)
            except (ModelMissingError, OCRError) as error:
                print(str(error), file=sys.stderr)
                return 1
            pages.append(Page(case.group, _rows(reading.lines, case.reference)))
    print(_report(pages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
