"""
DOCX document creator: implements :class:`~teachers_teammate.interfaces.DocumentCreator`.

Produces a landscape Word document in one of two layouts:

* ``table``    — three columns: Original Image | OCR Result | Correction Proposal
* ``comments`` — two columns: Original Image | OCR Result, with Word comments

Text stored as Markdown (``text_format="markdown"``) keeps its formatting in the table layout:
bold, italic, underline, strikethrough, super-/subscript, headings, lists and tables become Word
runs and paragraphs.  The comments layout writes the plain text, because a comment has to sit on
the words it is about and the offsets of the review are offsets in that plain text.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import logging
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from lxml import etree

from ..domain.formatted_text import (
    Block,
    FormattedDocument,
    Style,
    TextFormat,
    list_markers,
    normalize_text_format,
)
from ..domain.proofreading.review import (
    CorrectionReview,
    EditNote,
    ExplainedSentence,
    align,
    explained_sentences,
)
from ..interfaces import DocumentCreator
from .docx_comments import BODY_PT, marks_for, write_marked_text
from .ocr_formats import plain_text_of

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
_BODY_PT = BODY_PT
_HEADING_PT = 12
_LIST_INDENT_PT = 14
_MUTED_COLOUR = RGBColor(0x66, 0x66, 0x66)

# Headings, column titles and category names of the review sections, per explanation language.
# Keyed like the correction presets; ``_labels_for`` falls back to English for anything else.
_LABELS: dict[str, dict[str, str]] = {
    "english": {
        "explanations": "Explanations",
        "unclear": "Unclear or ambiguous expressions",
        "original_expression": "Original expression",
        "why_unclear": "Why it is unclear",
        "recommended_version": "Recommended version",
        "unclear_prefix": "Unclear",
        "recommended_prefix": "Recommended",
        "suggestion_prefix": "Suggestion (meaning not certain)",
        "grammar": "Grammar",
        "spelling": "Spelling",
        "punctuation": "Punctuation",
        "word_choice": "Word choice",
        "word_order": "Word order",
        "other": "Other",
    },
    "german": {
        "explanations": "Erläuterungen",
        "unclear": "Unklare oder mehrdeutige Formulierungen",
        "original_expression": "Ursprüngliche Formulierung",
        "why_unclear": "Warum sie unklar ist",
        "recommended_version": "Empfohlene Version",
        "unclear_prefix": "Unklar",
        "recommended_prefix": "Empfohlen",
        "suggestion_prefix": "Vorschlag (Bedeutung nicht sicher)",
        "grammar": "Grammatik",
        "spelling": "Rechtschreibung",
        "punctuation": "Zeichensetzung",
        "word_choice": "Wortwahl",
        "word_order": "Wortstellung",
        "other": "Sonstiges",
    },
    "french": {
        "explanations": "Explications",
        "unclear": "Formulations peu claires ou ambiguës",
        "original_expression": "Formulation d'origine",
        "why_unclear": "Pourquoi elle est peu claire",
        "recommended_version": "Version recommandée",
        "unclear_prefix": "Peu clair",
        "recommended_prefix": "Recommandé",
        "suggestion_prefix": "Suggestion (sens incertain)",
        "grammar": "Grammaire",
        "spelling": "Orthographe",
        "punctuation": "Ponctuation",
        "word_choice": "Choix des mots",
        "word_order": "Ordre des mots",
        "other": "Autre",
    },
    "spanish": {
        "explanations": "Explicaciones",
        "unclear": "Expresiones poco claras o ambiguas",
        "original_expression": "Expresión original",
        "why_unclear": "Por qué es poco clara",
        "recommended_version": "Versión recomendada",
        "unclear_prefix": "Poco claro",
        "recommended_prefix": "Recomendado",
        "suggestion_prefix": "Sugerencia (significado incierto)",
        "grammar": "Gramática",
        "spelling": "Ortografía",
        "punctuation": "Puntuación",
        "word_choice": "Elección de palabras",
        "word_order": "Orden de las palabras",
        "other": "Otro",
    },
    "italian": {
        "explanations": "Spiegazioni",
        "unclear": "Espressioni poco chiare o ambigue",
        "original_expression": "Espressione originale",
        "why_unclear": "Perché è poco chiara",
        "recommended_version": "Versione consigliata",
        "unclear_prefix": "Poco chiaro",
        "recommended_prefix": "Consigliato",
        "suggestion_prefix": "Suggerimento (significato incerto)",
        "grammar": "Grammatica",
        "spelling": "Ortografia",
        "punctuation": "Punteggiatura",
        "word_choice": "Scelta delle parole",
        "word_order": "Ordine delle parole",
        "other": "Altro",
    },
    "portuguese": {
        "explanations": "Explicações",
        "unclear": "Expressões pouco claras ou ambíguas",
        "original_expression": "Expressão original",
        "why_unclear": "Por que é pouco clara",
        "recommended_version": "Versão recomendada",
        "unclear_prefix": "Pouco claro",
        "recommended_prefix": "Recomendado",
        "suggestion_prefix": "Sugestão (significado incerto)",
        "grammar": "Gramática",
        "spelling": "Ortografia",
        "punctuation": "Pontuação",
        "word_choice": "Escolha de palavras",
        "word_order": "Ordem das palavras",
        "other": "Outro",
    },
    "dutch": {
        "explanations": "Toelichting",
        "unclear": "Onduidelijke of dubbelzinnige formuleringen",
        "original_expression": "Oorspronkelijke formulering",
        "why_unclear": "Waarom ze onduidelijk is",
        "recommended_version": "Aanbevolen versie",
        "unclear_prefix": "Onduidelijk",
        "recommended_prefix": "Aanbevolen",
        "suggestion_prefix": "Suggestie (betekenis niet zeker)",
        "grammar": "Grammatica",
        "spelling": "Spelling",
        "punctuation": "Interpunctie",
        "word_choice": "Woordkeuze",
        "word_order": "Woordvolgorde",
        "other": "Overig",
    },
}

# Language names and ISO codes, as the app, the CLI and the config file may spell them.
_LANGUAGE_ALIASES: dict[str, str] = {
    "en": "english",
    "de": "german",
    "fr": "french",
    "es": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "nl": "dutch",
    "deutsch": "german",
    "français": "french",
    "español": "spanish",
    "italiano": "italian",
    "português": "portuguese",
    "nederlands": "dutch",
}


def labels_for(explanation_language: str) -> dict[str, str]:
    """Return the review-section labels for *explanation_language* (English when unknown)."""
    key = explanation_language.strip().lower()
    return _LABELS.get(_LANGUAGE_ALIASES.get(key, key), _LABELS["english"])


def _change_summary(change: EditNote) -> str:
    """Return one change as ``"old" -> "new"``, dropping the empty side."""
    if not change.original:
        return f'\u2192 "{change.corrected}"'
    if not change.corrected:
        return f'"{change.original}" \u2192'
    return f'"{change.original}" \u2192 "{change.corrected}"'


def _explanation_line(change: EditNote, labels: dict[str, str]) -> str:
    """Return a change as one bullet of the explanations section."""
    summary = _change_summary(change)
    if not change.explanation:
        return summary
    category = labels.get(change.category, change.category.replace("_", " "))
    return f"{summary} \u00b7 {category}: {change.explanation}"


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


class DocxDocumentCreator(DocumentCreator):
    """Builds a landscape Word document with an image + OCR + correction table."""

    def __init__(self, *, fmt: str = "table", labels_language: str = "") -> None:
        self._fmt = fmt
        self._labels_language = labels_language
        """Language of the review headings and column titles (empty: English)."""

    def create(
        self,
        raw_text: str,
        corrected_text: str | None,
        out_path: Path,
        title: str,
        *,
        source_image: str | Path | None = None,
        text_format: TextFormat = "plain",
        review: CorrectionReview | None = None,
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
            review:         Explanations and unclear expressions, added as comments and
                            tables; ``None`` writes the document exactly as before.
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
                review=review,
            )
        else:
            self._build_table(
                raw_text,
                corrected_text,
                out_path,
                title,
                image_path=source_image,
                text_format=text_format,
                review=review,
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
        review: CorrectionReview | None = None,
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
            review:         Explanations and unclear expressions appended below the table.
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

        if review is not None:
            labels = labels_for(self._labels_language)
            if corrected_text:
                raw_plain = plain_text_of(raw_text, text_format)
                corrected_plain = plain_text_of(corrected_text, text_format)
                aligned = align(raw_plain, corrected_plain, review)
                self._add_explanations(
                    doc, explained_sentences(aligned, raw_plain, corrected_plain), labels
                )
            self._add_unclear_table(doc, review, labels)

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
        review: CorrectionReview | None = None,
    ) -> None:
        """
        Write a landscape .docx to *out_path* (two-column: image + OCR text).

        The column shows the extracted text as plain text, one paragraph per line.  When
        *corrected_text* is provided, every change is highlighted in yellow and commented with
        what it does to the words, and every unclear expression is commented on its own words,
        so reviewers can accept or reject each change directly in Word.

        Args:
            raw_text:       Raw OCR output.
            corrected_text: Proofread text used to compute the changes, or None.
            out_path:       Destination path (parent directory must exist).
            title:          Document heading.
            image_path:     Source image embedded in column 1.
            review:         Explanations and unclear expressions, attached as comments and
                            appended as a table; ``None`` writes the text without comments.
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

        labels = labels_for(self._labels_language)
        pending: list[tuple[int, str]] = []
        raw_plain = plain_text_of(raw_text, text_format)
        # Without a corrected text the two texts are the same: there is no change to mark, but the
        # unclear expressions of the review are still commented on their words.
        corrected_plain = (
            plain_text_of(corrected_text, text_format) if corrected_text else raw_plain
        )
        aligned = align(raw_plain, corrected_plain, review or CorrectionReview())
        write_marked_text(content[1], raw_plain, marks_for(aligned, labels, raw_plain), pending)
        if review is not None:
            self._add_unclear_table(doc, review, labels)
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

    def _add_unclear_table(
        self, doc: Any, review: CorrectionReview, labels: dict[str, str]
    ) -> None:
        """Append the *Unclear or ambiguous expressions* heading and its three-column table."""
        notes = review.unclear_notes  # a stretch the model left out is not for the student
        if not notes:
            return
        heading = doc.add_heading(labels["unclear"], level=2)
        for run in heading.runs:
            run.font.color.rgb = _HEADING_COLOUR
        table = doc.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        header = table.rows[0].cells
        for cell, key in zip(
            header, ("original_expression", "why_unclear", "recommended_version"), strict=True
        ):
            self._fill_cell(cell, labels[key], bold=True)
            self._shade_cell(cell, _HEADER_FILL)
        for note in notes:
            row = table.add_row().cells
            self._fill_cell(row[0], note.expression, italic=True)
            self._fill_cell(row[1], note.why)
            prefix = (
                labels["suggestion_prefix"] if note.is_suggestion else labels["recommended_prefix"]
            )
            self._fill_cell(row[2], f"{prefix}: {note.recommendation}")

    def _add_explanations(
        self, doc: Any, sentences: Sequence[ExplainedSentence], labels: dict[str, str]
    ) -> None:
        """Append the numbered *Explanations* list, one block per changed sentence.

        Only explanations of changes that are in the corrected text are listed (notes whose edit
        is not there, and sentences that could not be placed, are left out), and each sentence is
        quoted as it reads in the corrected text now.  Spelling and punctuation edits explain
        themselves and are not listed (the comments layout still carries their explanation).
        """
        if not sentences:
            return
        heading = doc.add_heading(labels["explanations"], level=2)
        for run in heading.runs:
            run.font.color.rgb = _HEADING_COLOUR
        for number, sentence in enumerate(sentences, start=1):
            para = doc.add_paragraph()
            bold = para.add_run(f"{number}. ")
            bold.bold = True
            bold.font.size = Pt(_BODY_PT)
            corrected = para.add_run(sentence.text)
            corrected.italic = True
            corrected.font.size = Pt(_BODY_PT)
            for note in sentence.notes:
                bullet = doc.add_paragraph(style="List Bullet")
                self._add_run(bullet, _explanation_line(note, labels))

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
