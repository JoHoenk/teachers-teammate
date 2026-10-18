"""
DOCX document creator: implements :class:`~teachers_teammate.interfaces.DocumentCreator`.

Produces a landscape Word document in one of two layouts:

* ``table``    — three columns: Original Image | OCR Result | Correction Proposal
* ``comments`` — two columns: Original Image | OCR Result, with Word diff-comments

Text stored as Markdown (``text_format="markdown"``) keeps its formatting: bold, italic,
underline, strikethrough, super-/subscript, headings, lists and tables become Word runs
and paragraphs.  Plain text is written exactly as before.
"""

from __future__ import annotations

from datetime import UTC, datetime
import difflib
from itertools import groupby, zip_longest
import logging
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from lxml import etree

from ..domain.formatted_text import (
    Block,
    FormattedDocument,
    Span,
    Style,
    TextFormat,
    flat_chunks,
    list_markers,
    normalize_text_format,
)
from ..interfaces import DocumentCreator

_logger = logging.getLogger(__name__)

_HEADER_FILL = "D9D9D9"  # light grey
_HEADING_COLOUR = RGBColor(0x1F, 0x49, 0x7D)  # Word dark-blue
_IMG_COL_WIDTH = Inches(2.8)  # original-image column
_TXT_COL_WIDTH = Inches(3.2)  # each text column
# Comments format (2-column)
_COMMENTS_WIDE_COL = _IMG_COL_WIDTH + _TXT_COL_WIDTH
_COMMENTS_URI = PackURI("/word/comments.xml")
_COMMENTS_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
_COMMENTS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_COMMENTS_AUTHOR = "Teacher's Teammate"
_BODY_PT = 10
_HEADING_PT = 12
_LIST_INDENT_PT = 14
_MUTED_COLOUR = RGBColor(0x66, 0x66, 0x66)

_Word = tuple[str, Style]
"""A whitespace-free word and the style it was written in."""


def _apply_style(run: Any, style: Style) -> None:
    """Set the character formatting of *run* from *style* (unset flags stay inherited)."""
    if Style.BOLD in style:
        run.bold = True
    if Style.ITALIC in style:
        run.italic = True
    if Style.UNDERLINE in style:
        run.underline = True
    if Style.STRIKE in style:
        run.font.strike = True
    if Style.SUPERSCRIPT in style:
        run.font.superscript = True
    if Style.SUBSCRIPT in style:
        run.font.subscript = True


def _words(spans: tuple[Span, ...] | list[Span]) -> list[_Word]:
    return [(word, span.style) for span in spans for word in span.text.split()]


def _style_groups(words: list[_Word]) -> list[tuple[str, Style]]:
    """Join consecutive words of one style into ``(text, style)`` runs."""
    return [
        (" ".join(word for word, _ in group), style)
        for style, group in groupby(words, key=lambda word: word[1])
    ]


def _split_lines(spans: tuple[Span, ...]) -> list[tuple[Span, ...]]:
    """Split *spans* at ``"\n"`` into one span tuple per line."""
    lines: list[list[Span]] = [[]]
    for span in spans:
        pieces = span.text.split("\n")
        for index, piece in enumerate(pieces):
            if index:
                lines.append([])
            if piece:
                lines[-1].append(Span(piece, span.style))
    return [tuple(line) for line in lines]


def _styled_lines(doc: FormattedDocument) -> list[tuple[Span, ...]]:
    """Flatten *doc* to lines of styled spans, with a blank line between blocks."""
    lines: list[tuple[Span, ...]] = []
    for spans, tight in flat_chunks(doc):
        if lines and not tight:
            lines.append(())
        lines.extend(_split_lines(spans))
    return lines


def _lines_of(text: str, text_format: TextFormat) -> list[tuple[Span, ...]]:
    """Return stored *text* as lines of styled spans (plain text: one unstyled span per line)."""
    if text_format != "markdown":
        return [(Span(line),) if line else () for line in text.splitlines()]
    from .ocr_formats import parse_stored_text  # noqa: PLC0415

    return _styled_lines(parse_stored_text(text, "markdown"))


def _line_key(line: tuple[Span, ...]) -> tuple[_Word, ...]:
    """Return what makes a line "the same" for diffing: its words and how each is styled."""
    return tuple(_words(line))


class DocxDocumentCreator(DocumentCreator):
    """Builds a landscape Word document with an image + OCR + correction table."""

    def __init__(self, *, fmt: str = "table") -> None:
        self._fmt = fmt

    def create(
        self,
        raw_text: str,
        corrected_text: str | None,
        out_path: Path,
        title: str,
        *,
        source_image: str | Path | None = None,
        text_format: TextFormat = "plain",
    ) -> None:
        """Write a landscape .docx to *out_path*.

        Dispatches to the table or comments layout based on the ``fmt`` passed
        to the constructor.

        Args:
            raw_text:       Raw OCR output.
            corrected_text: Proofread text, or ``None`` when correction was skipped.
            out_path:       Destination path (parent directory must exist).
            title:          Document heading.
            source_image:   Path to the source image shown in column 1.
            text_format:    ``"markdown"`` keeps bold/strikethrough/... as Word formatting.
        """
        text_format = normalize_text_format(text_format)
        if self._fmt == "comments":
            self._build_with_comments(
                raw_text,
                corrected_text,
                out_path,
                title,
                image_path=source_image,
                text_format=text_format,
            )
        else:
            self._build_table(
                raw_text,
                corrected_text,
                out_path,
                title,
                image_path=source_image,
                text_format=text_format,
            )

    def _build_table(
        self,
        raw_text: str,
        corrected_text: str | None,
        out_path: Path,
        title: str,
        *,
        image_path: str | Path | None = None,
        text_format: TextFormat = "plain",
    ) -> None:
        """
        Write a landscape three-column .docx file to *out_path*.

        Args:
            raw_text:       Raw OCR output.
            corrected_text: Proofread text, or None when correction was skipped.
            out_path:       Destination path (parent directory must exist).
            title:          Document heading (typically the source file stem).
            image_path:     Path to the original source image shown in column 1.
                            When ``None``, the image cell is left blank.
        """
        doc = Document()

        # Landscape orientation
        section = doc.sections[0]
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width

        heading = doc.add_heading(title, level=1)
        for run in heading.runs:
            run.font.color.rgb = _HEADING_COLOUR

        table = doc.add_table(rows=2, cols=3)
        table.style = "Table Grid"
        table.columns[0].width = _IMG_COL_WIDTH
        table.columns[1].width = _TXT_COL_WIDTH
        table.columns[2].width = _TXT_COL_WIDTH

        hdr = table.rows[0].cells
        hdr[0].width = _IMG_COL_WIDTH
        hdr[1].width = _TXT_COL_WIDTH
        hdr[2].width = _TXT_COL_WIDTH
        self._fill_cell(hdr[0], "Original", bold=True)
        self._fill_cell(hdr[1], "OCR Result", bold=True)
        self._fill_cell(hdr[2], "Correction Proposal", bold=True)
        for cell in hdr:
            self._shade_cell(cell, _HEADER_FILL)

        content = table.rows[1].cells
        content[0].width = _IMG_COL_WIDTH
        content[1].width = _TXT_COL_WIDTH
        content[2].width = _TXT_COL_WIDTH
        if image_path is not None:
            self._insert_image(content[0], str(image_path), _IMG_COL_WIDTH)
        if text_format == "markdown":
            self._fill_cell_formatted(content[1], raw_text)
            if corrected_text:
                self._fill_cell_formatted(content[2], corrected_text)
            else:
                self._fill_cell(content[2], "(correction skipped)", italic=True)
        else:
            self._fill_cell(content[1], raw_text)
            placeholder = corrected_text if corrected_text else "(correction skipped)"
            self._fill_cell(content[2], placeholder, italic=not corrected_text)

        doc.save(str(out_path))

    def _build_with_comments(
        self,
        raw_text: str,
        corrected_text: str | None,
        out_path: Path,
        title: str,
        *,
        image_path: str | Path | None = None,
        text_format: TextFormat = "plain",
    ) -> None:
        """
        Write a landscape .docx to *out_path* (two-column: image + OCR text).

        When *corrected_text* is provided, differences against the raw OCR are
        highlighted in yellow and attached as Word comments so reviewers can
        accept or reject each change directly in Word.

        Args:
            raw_text:       Raw OCR output.
            corrected_text: Proofread text used to compute diffs, or None.
            out_path:       Destination path (parent directory must exist).
            title:          Document heading.
            image_path:     Source image embedded in column 1.
        """
        doc = Document()
        section = doc.sections[0]
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width

        heading = doc.add_heading(title, level=1)
        for run in heading.runs:
            run.font.color.rgb = _HEADING_COLOUR

        table = doc.add_table(rows=2, cols=2)
        table.style = "Table Grid"
        table.columns[0].width = _IMG_COL_WIDTH
        table.columns[1].width = _COMMENTS_WIDE_COL

        hdr = table.rows[0].cells
        hdr[0].width = _IMG_COL_WIDTH
        hdr[1].width = _COMMENTS_WIDE_COL
        self._fill_cell(hdr[0], "Original", bold=True)
        self._fill_cell(hdr[1], "OCR Result (corrections as comments)", bold=True)
        for cell in hdr:
            self._shade_cell(cell, _HEADER_FILL)

        content = table.rows[1].cells
        content[0].width = _IMG_COL_WIDTH
        content[1].width = _COMMENTS_WIDE_COL
        if image_path is not None:
            self._insert_image(content[0], str(image_path), _IMG_COL_WIDTH)

        pending: list[tuple[int, str]] = []
        if corrected_text:
            self._fill_cell_with_styled_diffs(
                content[1],
                _lines_of(raw_text, text_format),
                _lines_of(corrected_text, text_format),
                pending,
            )
        elif text_format == "markdown":
            self._fill_cell_formatted(content[1], raw_text)
        else:
            self._fill_cell(content[1], raw_text)
        if pending:
            self._attach_comments_part(doc, pending)

        doc.save(str(out_path))

    @staticmethod
    def _shade_cell(cell: Any, fill_hex: str) -> None:
        tc = cell._tc  # noqa: SLF001  # python-docx has no public API for cell shading; _tc is the only access path to the underlying lxml element
        tc_pr = tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill_hex)
        tc_pr.append(shd)

    @staticmethod
    def _insert_image(cell: Any, image_path: str, max_width: Any) -> None:
        """Insert *image_path* into *cell* scaled to *max_width*."""
        paragraph = cell.paragraphs[0]
        run = paragraph.add_run()
        try:
            run.add_picture(image_path, width=max_width)
        except Exception as exc:  # noqa: BLE001  # PIL / python-docx image errors vary by format; log the warning and leave placeholder text
            _logger.warning("Could not insert image '%s': %s", image_path, exc)
            run.text = "(image unavailable)"

    @staticmethod
    def _fill_cell(
        cell: Any,
        text: str,
        bold: bool = False,
        italic: bool = False,
    ) -> None:
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = paragraph.add_run(text)
        run.bold = bold
        run.italic = italic
        run.font.size = Pt(10)

    def _fill_cell_formatted(self, cell: Any, markdown: str) -> None:
        """Fill *cell* with Markdown *markdown*, keeping its formatting as Word runs."""
        from .ocr_formats import parse_stored_text  # noqa: PLC0415

        self._fill_cell_document(cell, parse_stored_text(markdown, "markdown"))

    def _fill_cell_document(self, cell: Any, doc: FormattedDocument) -> None:
        """Write the blocks of *doc* into *cell* as paragraphs, lists and nested tables."""
        first = True
        for block, marker in list_markers(doc.blocks):
            if block.kind == "table":
                if block.rows:
                    self._add_nested_table(cell, block)
                    first = False
                continue
            if block.kind != "figure" and not block.text.strip():
                continue
            para = cell.paragraphs[0] if first else cell.add_paragraph()
            first = False
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
            if block.kind == "list_item":
                para.paragraph_format.left_indent = Pt(_LIST_INDENT_PT * (block.level + 1))
                self._add_run(para, marker)
            if block.kind == "figure":
                run = para.add_run(f"[{block.text.strip() or 'image'}]")
                run.italic = True
                run.font.size = Pt(_BODY_PT)
                run.font.color.rgb = _MUTED_COLOUR
                continue
            for span in block.spans:
                bold = block.kind == "heading"
                size = _HEADING_PT if block.kind == "heading" else _BODY_PT
                self._add_run(para, span.text, span.style, bold=bold, size=size)

    def _add_nested_table(self, cell: Any, block: Block) -> None:
        width = max(len(row) for row in block.rows)
        table = cell.add_table(rows=len(block.rows), cols=width)
        table.style = "Table Grid"
        for row_index, row in enumerate(block.rows):
            for col_index, spans in enumerate(row):
                para = table.cell(row_index, col_index).paragraphs[0]
                for span in spans:
                    self._add_run(para, span.text, span.style, bold=row_index == 0)

    @staticmethod
    def _add_run(
        para: Any,
        text: str,
        style: Style = Style.NONE,
        *,
        bold: bool = False,
        size: int = _BODY_PT,
    ) -> Any:
        run = para.add_run(text)
        run.font.size = Pt(size)
        _apply_style(run, style)
        if bold:
            run.bold = True
        return run

    def _fill_cell_with_styled_diffs(
        self,
        cell: Any,
        raw_lines: list[tuple[Span, ...]],
        corr_lines: list[tuple[Span, ...]],
        comments: list[tuple[int, str]],
    ) -> None:
        """Fill *cell* with the styled *raw_lines*, commenting where *corr_lines* differ.

        Lines are diffed by their words and styles; a word whose style the correction changed
        (a crossed-out word that lost its strikethrough) is commented like a changed word.
        """
        matcher = difflib.SequenceMatcher(
            None,
            [_line_key(line) for line in raw_lines],
            [_line_key(line) for line in corr_lines],
            autojunk=False,
        )
        first = True
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            raw_block = raw_lines[i1:i2]
            corr_block = corr_lines[j1:j2]
            if tag == "equal":
                for line in raw_block:
                    para = cell.paragraphs[0] if first else cell.add_paragraph()
                    first = False
                    for span in line:
                        self._add_plain_run(para, span.text, span.style)
            else:
                for raw_line, corr_line in zip_longest(raw_block, corr_block, fillvalue=()):
                    para = cell.paragraphs[0] if first else cell.add_paragraph()
                    first = False
                    self._add_styled_line_with_word_diff(para, raw_line, corr_line, comments)

    def _add_styled_line_with_word_diff(
        self,
        para: Any,
        raw_spans: tuple[Span, ...],
        corr_spans: tuple[Span, ...],
        comments: list[tuple[int, str]],
    ) -> None:
        """Add the styled *raw_spans* to *para*, commenting word diffs against *corr_spans*."""
        raw_words = _words(raw_spans)
        corr_words = _words(corr_spans)
        if not raw_words and not corr_words:
            return
        matcher = difflib.SequenceMatcher(
            None, [word for word, _ in raw_words], [word for word, _ in corr_words], autojunk=False
        )
        parts: list[tuple[list[_Word], str | None]] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            raw_chunk = raw_words[i1:i2]
            corr_chunk = " ".join(word for word, _ in corr_words[j1:j2])
            if tag == "equal":
                pairs = zip(raw_chunk, corr_words[j1:j2], strict=True)
                for same_style, group in groupby(pairs, key=lambda pair: pair[0][1] == pair[1][1]):
                    words = [raw for raw, _corr in group]
                    parts.append((words, None if same_style else "\u2192 (formatting changed)"))
            elif tag == "replace":
                parts.append((raw_chunk, f'\u2192 "{corr_chunk}"'))
            elif tag == "delete":
                parts.append((raw_chunk, "\u2192 (delete)"))
            else:  # insert — no raw anchor; annotate as inline marker with a comment
                parts.append(
                    ([(f"[+{corr_chunk}]", Style.NONE)], f'\u2192 (insert "{corr_chunk}")')
                )
        for idx, (words, comment_text) in enumerate(parts):
            groups = _style_groups(words)
            # Words of different styles are separate runs, so each run but the line's last
            # carries the blank that separates it from the next word.
            is_last_part = idx == len(parts) - 1
            groups = [
                (text if is_last_part and index == len(groups) - 1 else text + " ", style)
                for index, (text, style) in enumerate(groups)
            ]
            if comment_text is None:
                for text, style in groups:
                    self._add_plain_run(para, text, style)
            else:
                cid = len(comments)
                comments.append((cid, comment_text))
                self._add_commented_runs(para, groups, cid)

    @staticmethod
    def _add_plain_run(para: Any, text: str, style: Style = Style.NONE) -> None:
        run = para.add_run(text)
        run.font.size = Pt(_BODY_PT)
        _apply_style(run, style)

    @staticmethod
    def _add_commented_runs(para: Any, groups: list[tuple[str, Style]], comment_id: int) -> None:
        """Add yellow-highlighted runs in *para* anchored to *comment_id*."""
        if not groups:
            return
        range_start = OxmlElement("w:commentRangeStart")
        range_start.set(qn("w:id"), str(comment_id))
        runs = []
        for text, style in groups:
            run = para.add_run(text)
            run.font.size = Pt(_BODY_PT)
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            _apply_style(run, style)
            runs.append(run)
        runs[0]._r.addprevious(range_start)  # noqa: SLF001  # python-docx has no public API for OOXML comment range anchors; _r exposes the underlying lxml element
        range_end = OxmlElement("w:commentRangeEnd")
        range_end.set(qn("w:id"), str(comment_id))
        runs[-1]._r.addnext(range_end)  # noqa: SLF001  # python-docx has no public API for OOXML comment range anchors; _r exposes the underlying lxml element
        ref_r = OxmlElement("w:r")
        ref_rpr = OxmlElement("w:rPr")
        ref_style = OxmlElement("w:rStyle")
        ref_style.set(qn("w:val"), "CommentReference")
        ref_rpr.append(ref_style)
        ref_r.append(ref_rpr)
        ref_ref = OxmlElement("w:commentReference")
        ref_ref.set(qn("w:id"), str(comment_id))
        ref_r.append(ref_ref)
        range_end.addnext(ref_r)

    @staticmethod
    def _attach_comments_part(doc: Any, comments: list[tuple[int, str]]) -> None:
        """Build word/comments.xml and attach it to *doc* as a package part."""
        root = etree.Element(f"{{{_W_NS}}}comments", nsmap={"w": _W_NS})
        for cid, ctext in comments:
            comment_el = etree.SubElement(root, f"{{{_W_NS}}}comment")
            comment_el.set(f"{{{_W_NS}}}id", str(cid))
            comment_el.set(f"{{{_W_NS}}}author", _COMMENTS_AUTHOR)
            comment_el.set(f"{{{_W_NS}}}date", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
            comment_el.set(f"{{{_W_NS}}}initials", "OCR")
            para_el = etree.SubElement(comment_el, f"{{{_W_NS}}}p")
            run_el = etree.SubElement(para_el, f"{{{_W_NS}}}r")
            t_el = etree.SubElement(run_el, f"{{{_W_NS}}}t")
            t_el.text = ctext
            t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        xml_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        part = Part(_COMMENTS_URI, _COMMENTS_CT, xml_bytes, doc.part.package)
        doc.part.relate_to(part, _COMMENTS_REL)
