"""Developer tool: write the reference transcriptions of the line measure from a Chandra OCR run.

NOT run in CI - it reads the output of a third-party model, run by the developer themselves:

    bazel run //tools/dev:chandra_gt -- --chandra ~/chandra-out --pages ~/pages

Chandra (``datalab-to/chandra``) writes one folder per input file, named after the file:

    chandra-out/s01/s01_chunks.json   the layout blocks: their boxes, labels and HTML
    chandra-out/s01/s01.md            the same as Markdown

One reference line per written line: every block's text in reading order, broken where the block
breaks a line (``<br>``, a paragraph, a list item, a table row).  Blocks that hold a picture -
image, figure, diagram, chemistry - are left out: they are no written text.

The transcription is written where the measure looks for it, named as ``tools/dev/page_lines.py``
names it (``s01.gt.txt``, or ``scan_p2.gt.txt`` for a page of a longer PDF).  A transcription that
is already there is kept: a page transcribed by hand is worth more than this one, so replacing it
takes ``--overwrite``.

Chandra joins written lines into paragraphs where it can - its prompt asks for ``<br>`` only when
it is absolutely necessary - so a page it read as paragraphs gives a reference of paragraphs.  The
measure shows that: the page's rows stay *split* instead of *lined up*, and its transcription has
to be done by hand.  A transcription written here is a reference, not a truth.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import html
import json
from pathlib import Path
import re
import sys

from tools.dev.page_lines import page_count, page_files, transcription

_SKIPPED_LABELS = frozenset({"image", "figure", "diagram", "chemical-block", "blank-page"})
"""Block labels of a picture: a reference line holds written text, and these do not."""

_LINE_BREAK = re.compile(r"(?i)<br\s*/?>|</(?:p|li|h[1-6]|div|tr|caption|pre|table)>")
_CELL_BREAK = re.compile(r"(?i)</t[dh]>")
_TAG = re.compile(r"<[^>]+>")

_PAGE_BREAK = re.compile(r"^\s*\d+-{10,}\s*$")
"""Chandra's page separator in a paginated Markdown output: its number, then dashes."""

_MARKDOWN_MARK = re.compile(r"^(?:#{1,6}\s+|>\s+|[-*+]\s+|\d+[.)]\s+)")


def lines_of(content: str) -> list[str]:
    """Return the written lines of one block's HTML: a line per line break it holds."""
    text = _LINE_BREAK.sub("\n", content)
    text = _CELL_BREAK.sub(" ", text)
    text = _TAG.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    return [line.strip() for line in text.splitlines() if line.strip()]


def reference_of_blocks(blocks: Sequence[dict]) -> str:
    """Return the transcription of one page: the lines of its blocks, in reading order."""
    lines = [
        line
        for block in blocks
        if str(block.get("label", "")).strip().lower() not in _SKIPPED_LABELS
        for line in lines_of(str(block.get("content", "")))
    ]
    return "\n".join(lines) + "\n" if lines else ""


def references_of_markdown(text: str, pages: int) -> list[str] | None:
    """Return one transcription per page of a Markdown output, or ``None`` if it names no pages.

    Markdown is the fallback for a Chandra run that saved no chunks: its page separators are the
    only way to say which line belongs to which page, so a multi-page output without them is
    refused rather than cut by guess.
    """
    parts: list[list[str]] = [[]]
    for line in text.splitlines():
        if _PAGE_BREAK.match(line):
            parts.append([])
            continue
        stripped = _MARKDOWN_MARK.sub("", line).strip()
        if stripped:
            parts[-1].append(stripped)
    if len(parts) != pages:
        return None
    return ["\n".join(lines) + "\n" if lines else "" for lines in parts]


def _references(folder: Path, stem: str, pages: int) -> list[str] | None:
    """Return one transcription per page of *stem*, from Chandra's output in *folder*."""
    chunks_path = folder / f"{stem}_chunks.json"
    if chunks_path.is_file():
        try:
            blocks = json.loads(chunks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            print(f"{chunks_path.name}: {error}, read as no blocks", file=sys.stderr)
            blocks = []
        by_page: dict[int, list[dict]] = {}
        for number, page in enumerate(blocks):
            by_page[int(page.get("page_num", number))] = list(page.get("chunks", []))
        return [reference_of_blocks(by_page.get(number, [])) for number in range(pages)]
    markdown_path = folder / f"{stem}.md"
    if not markdown_path.is_file():
        return None
    return references_of_markdown(markdown_path.read_text(encoding="utf-8"), pages)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Return the parsed command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--chandra",
        required=True,
        type=Path,
        help="the folder Chandra wrote: one subfolder per file, named after the file",
    )
    parser.add_argument(
        "--pages",
        nargs="+",
        required=True,
        type=Path,
        help="the pages the transcriptions are written beside",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace a transcription that is already there",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Write one transcription per page; return the exit code."""
    args = _parse(argv)
    written = 0
    for file in (file for path in args.pages for file in page_files(path)):
        count = page_count(file)
        if not count:
            print(f"{file.name}: cannot be read, skipped", file=sys.stderr)
            continue
        folder = args.chandra / file.stem
        if not folder.is_dir():
            print(f"{file.name}: no Chandra output under {folder}, skipped", file=sys.stderr)
            continue
        references = _references(folder, file.stem, count)
        if references is None:
            print(
                f"{file.name}: no chunks and no Markdown naming its {count} page(s), skipped",
                file=sys.stderr,
            )
            continue
        for number, reference in enumerate(references):
            target = transcription(file, number, count)
            if target.exists() and not args.overwrite:
                print(
                    f"{target.name}: already there, kept (--overwrite replaces it)", file=sys.stderr
                )
                continue
            target.write_text(reference, encoding="utf-8")
            written += 1
            where = file.name if count == 1 else f"{file.name} page {number + 1}"
            print(f"{where}: {len(reference.splitlines())} lines -> {target.name}")
    print(f"{written} transcription(s) written")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
