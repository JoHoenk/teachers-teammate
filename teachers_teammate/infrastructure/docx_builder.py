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

import copy
from datetime import UTC, datetime
from itertools import groupby, zip_longest
import logging
from pathlib import Path
import re
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.text.run import Run
from lxml import etree

from ..domain.correction_review import (
    AlignedReview,
    ChangeNote,
    CorrectionReview,
    UnclearNote,
    align,
    best_note_index,
    strip_marks,
    text_tokens,
)
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
from ..domain.proofreading.word_diff import diff_opcodes
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

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
"""Matches a run of word characters; used to compare wording without punctuation."""

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


def _norm_key(text: str) -> str:
    """Return *text* as a lookup key: notation marks stripped, whitespace collapsed."""
    return " ".join(strip_marks(text).split())


def _whole_word_pattern(expression: str) -> str:
    """Return a regex matching *expression* word for word, never inside a longer word.

    Whitespace in the expression matches any whitespace run; where the expression begins (ends)
    with a letter or digit, the match must not be preceded (followed) by one, so ``"he"`` does not
    match inside ``"then"``.
    """
    body = r"\s+".join(re.escape(word) for word in expression.split())
    before = r"(?<![^\W_])" if expression[:1].isalnum() else ""
    after = r"(?![^\W_])" if expression[-1:].isalnum() else ""
    return f"{before}{body}{after}"


def _contains_words(text: str, expression: str) -> bool:
    """Whether *text* contains *expression* as a run of whole words.

    Punctuation is ignored on both sides, so an expression that ends a sentence
    (``"very nice"`` in ``"... very nice."``) is found as well as one in the middle.
    """
    haystack = _WORD_RE.findall(text)
    needle = _WORD_RE.findall(expression)
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[start : start + len(needle)] == needle
        for start in range(len(haystack) - len(needle) + 1)
    )


def _change_summary(change: ChangeNote) -> str:
    """Return one change as ``"old" → "new"``, dropping the empty side."""
    if not change.original:
        return f'\u2192 "{change.corrected}"'
    if not change.corrected:
        return f'"{change.original}" \u2192'
    return f'"{change.original}" \u2192 "{change.corrected}"'


def _comment_text(base: str, note: ChangeNote | None, labels: dict[str, str]) -> str:
    """Return *base* plus the note's category and explanation (``base`` when there is none)."""
    if note is None or not note.explanation:
        return base
    category = labels.get(note.category, note.category.replace("_", " "))
    return f"{base} \u00b7 {category}: {note.explanation}"


def _explanation_line(change: ChangeNote, labels: dict[str, str]) -> str:
    """Return a change as one bullet of the explanations section."""
    summary = _change_summary(change)
    if not change.explanation:
        return summary
    category = labels.get(change.category, change.category.replace("_", " "))
    return f"{summary} \u00b7 {category}: {change.explanation}"


class _NoteSource:
    """The explanations of a review, looked up by the words of the diff part they explain.

    The comments layout computes its own word diff, whose parts do not line up one-to-one with
    the token hunks of ``align()`` (``his friend.`` is one part, ``his`` and ``friend`` two
    hunks).  A part therefore asks the domain for the note that changes the most of its words
    (:func:`~teachers_teammate.domain.correction_review.best_note_index`).
    """

    def __init__(self, aligned: AlignedReview | None, labels: dict[str, str]) -> None:
        self._labels = labels
        self._notes: list[ChangeNote] = []
        self._used: set[int] = set()
        seen: set[int] = set()
        for entry in aligned.changes if aligned is not None else ():
            if entry.note is not None and id(entry.note) not in seen:
                seen.add(id(entry.note))
                self._notes.append(entry.note)

    def comment(self, base: str, original: str, corrected: str) -> str:
        """Return the comment text for a diff part: *base*, plus its explanation if known."""
        part = text_tokens(original) | text_tokens(corrected)
        best = best_note_index(self._notes, self._used, part)
        if best < 0:
            return base
        self._used.add(best)
        return _comment_text(base, self._notes[best], self._labels)


def _aligned_review(
    review: CorrectionReview | None,
    raw_text: str,
    corrected_text: str | None,
    text_format: TextFormat,
) -> AlignedReview | None:
    """Match *review*'s notes to the local word diff of *raw_text* against *corrected_text*.

    ``None`` without a review or without a corrected text (nothing to explain then).
    """
    if review is None or not corrected_text:
        return None
    from .ocr_formats import plain_text_of  # noqa: PLC0415

    return align(
        plain_text_of(raw_text, text_format),
        plain_text_of(corrected_text, text_format),
        review,
    )


def _isolate_runs(para: Any, start: int, end: int) -> list[Any]:
    """Split the runs of *para* at the character offsets *start* and *end*; return those between.

    The split-off parts keep the formatting of the run they came from.  Offsets count the
    characters of ``para.text``.
    """
    for offset in (end, start):  # the later cut first, so the earlier offset stays valid
        position = 0
        for run in para.runs:
            length = len(run.text)
            if position < offset < position + length:
                tail = copy.deepcopy(run._r)  # noqa: SLF001  # python-docx has no public API to split a run
                run._r.addnext(tail)  # noqa: SLF001
                head_text = run.text[: offset - position]
                tail_run = Run(tail, run._parent)  # noqa: SLF001
                tail_run.text = run.text[offset - position :]
                run.text = head_text
                break
            position += length
    inside: list[Any] = []
    position = 0
    for run in para.runs:
        length = len(run.text)
        if length and position >= start and position + length <= end:
            inside.append(run)
        position += length
    return inside


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
            aligned = _aligned_review(review, raw_text, corrected_text, text_format)
            self._add_explanations(doc, aligned, labels)
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

        When *corrected_text* is provided, differences against the raw OCR are
        highlighted in yellow and attached as Word comments so reviewers can
        accept or reject each change directly in Word.

        Args:
            raw_text:       Raw OCR output.
            corrected_text: Proofread text used to compute diffs, or None.
            out_path:       Destination path (parent directory must exist).
            title:          Document heading.
            image_path:     Source image embedded in column 1.
            review:         Explanations and unclear expressions, attached as comments and
                            appended as a table; ``None`` writes the comments as before.
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
        if corrected_text:
            self._fill_cell_with_styled_diffs(
                content[1],
                _lines_of(raw_text, text_format),
                _lines_of(corrected_text, text_format),
                pending,
                _NoteSource(_aligned_review(review, raw_text, corrected_text, text_format), labels),
            )
        elif text_format == "markdown":
            self._fill_cell_formatted(content[1], raw_text)
        else:
            self._fill_cell(content[1], raw_text)
        if review is not None:
            self._add_unclear_comments(content[1], review, labels, pending)
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

    def _fill_cell_with_styled_diffs(
        self,
        cell: Any,
        raw_lines: list[tuple[Span, ...]],
        corr_lines: list[tuple[Span, ...]],
        comments: list[tuple[int, str]],
        notes: _NoteSource,
    ) -> None:
        """Fill *cell* with the styled *raw_lines*, commenting where *corr_lines* differ.

        *notes* supplies the explanation for each commented word diff.

        Lines are diffed by their words and styles; a word whose style the correction changed
        (a crossed-out word that lost its strikethrough) is commented like a changed word.
        """
        opcodes = diff_opcodes(
            [_line_key(line) for line in raw_lines],
            [_line_key(line) for line in corr_lines],
        )
        first = True
        for tag, i1, i2, j1, j2 in opcodes:
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
                    self._add_styled_line_with_word_diff(para, raw_line, corr_line, comments, notes)

    def _add_styled_line_with_word_diff(
        self,
        para: Any,
        raw_spans: tuple[Span, ...],
        corr_spans: tuple[Span, ...],
        comments: list[tuple[int, str]],
        notes: _NoteSource,
    ) -> None:
        """Add the styled *raw_spans* to *para*, commenting word diffs against *corr_spans*."""
        raw_words = _words(raw_spans)
        corr_words = _words(corr_spans)
        if not raw_words and not corr_words:
            return
        opcodes = diff_opcodes([word for word, _ in raw_words], [word for word, _ in corr_words])
        parts: list[tuple[list[_Word], str | None, str, str]] = []
        for tag, i1, i2, j1, j2 in opcodes:
            raw_chunk = raw_words[i1:i2]
            old_chunk = " ".join(word for word, _ in raw_chunk)
            corr_chunk = " ".join(word for word, _ in corr_words[j1:j2])
            if tag == "equal":
                pairs = zip(raw_chunk, corr_words[j1:j2], strict=True)
                for same_style, group in groupby(pairs, key=lambda pair: pair[0][1] == pair[1][1]):
                    words = [raw for raw, _corr in group]
                    if same_style:
                        parts.append((words, None, "", ""))
                    else:
                        parts.append((words, "\u2192 (formatting changed)", "", ""))
            elif tag == "replace":
                parts.append((raw_chunk, f'\u2192 "{corr_chunk}"', old_chunk, corr_chunk))
            elif tag == "delete":
                parts.append((raw_chunk, "\u2192 (delete)", old_chunk, ""))
            else:  # insert — no raw anchor; annotate as inline marker with a comment
                parts.append(
                    (
                        [(f"[+{corr_chunk}]", Style.NONE)],
                        f'\u2192 (insert "{corr_chunk}")',
                        "",
                        corr_chunk,
                    )
                )
        for idx, (words, base, original, corrected) in enumerate(parts):
            comment_text = None if base is None else notes.comment(base, original, corrected)
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
                self._add_commented_runs(para, groups, comments, comment_text)

    @staticmethod
    def _add_plain_run(para: Any, text: str, style: Style = Style.NONE) -> None:
        run = para.add_run(text)
        run.font.size = Pt(_BODY_PT)
        _apply_style(run, style)

    @classmethod
    def _add_commented_runs(
        cls,
        para: Any,
        groups: list[tuple[str, Style]],
        comments: list[tuple[int, str]],
        text: str,
    ) -> None:
        """Add yellow-highlighted runs in *para* and register *text* as their comment."""
        if not groups:
            return
        runs = []
        for group_text, style in groups:
            run = para.add_run(group_text)
            run.font.size = Pt(_BODY_PT)
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            _apply_style(run, style)
            runs.append(run)
        cls._anchor_comment(runs, comments, text)

    @staticmethod
    def _anchor_comment(runs: list[Any], comments: list[tuple[int, str]], text: str) -> None:
        """Wrap *runs* in a comment range and register *text* as the comment's body."""
        if not runs:
            return
        comment_id = len(comments)
        comments.append((comment_id, text))
        range_start = OxmlElement("w:commentRangeStart")
        range_start.set(qn("w:id"), str(comment_id))
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

    @classmethod
    def _add_unclear_comments(
        cls,
        cell: Any,
        review: CorrectionReview,
        labels: dict[str, str],
        comments: list[tuple[int, str]],
    ) -> None:
        """Attach one comment per unclear expression, anchored to the expression itself.

        The paragraph that contains the expression is searched word for word; the runs around it
        are split so the comment covers exactly those words.  An expression that is not found
        word for word (it spans a line break, or the model changed punctuation) is anchored to
        the whole paragraph that contains its words, as long as one does.
        """
        paragraphs = list(cell.paragraphs)
        for note in review.unclear:
            expression = _norm_key(note.expression)
            if not expression:
                continue
            pattern = re.compile(_whole_word_pattern(expression))
            for para in paragraphs:
                found = pattern.search(para.text)
                if found is not None:
                    runs = _isolate_runs(para, found.start(), found.end())
                elif _contains_words(_norm_key(para.text), expression):
                    runs = list(para.runs)
                else:
                    continue
                cls._anchor_comment(runs, comments, cls._unclear_comment(note, labels))
                break

    @staticmethod
    def _unclear_comment(note: UnclearNote, labels: dict[str, str]) -> str:
        """Return the comment text of one unclear expression."""
        recommendation = (
            f"{labels['suggestion_prefix']}: {note.recommendation}"
            if note.is_suggestion
            else f"{labels['recommended_prefix']}: {note.recommendation}"
        )
        return f"{labels['unclear_prefix']}: {note.why} \u2014 {recommendation}"

    def _add_unclear_table(
        self, doc: Any, review: CorrectionReview, labels: dict[str, str]
    ) -> None:
        """Append the *Unclear or ambiguous expressions* heading and its three-column table."""
        if not review.unclear:
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
        for note in review.unclear:
            row = table.add_row().cells
            self._fill_cell(row[0], note.expression, italic=True)
            self._fill_cell(row[1], note.why)
            prefix = (
                labels["suggestion_prefix"] if note.is_suggestion else labels["recommended_prefix"]
            )
            self._fill_cell(row[2], f"{prefix}: {note.recommendation}")

    def _add_explanations(
        self, doc: Any, aligned: AlignedReview | None, labels: dict[str, str]
    ) -> None:
        """Append the numbered *Explanations* list, one block per changed sentence.

        Only explanations of changes that are in the corrected text are listed (notes whose edit
        is not there, and sentences that could not be placed, are left out), and each sentence is
        quoted as it reads in the corrected text now.  Spelling and punctuation edits explain
        themselves and are not listed (the comments layout still carries their explanation).
        """
        if aligned is None:
            return
        by_sentence: dict[int, list[ChangeNote]] = {}
        for entry in aligned.changes:
            if entry.note is None or entry.is_minor:
                continue
            notes = by_sentence.setdefault(entry.sentence, [])
            if all(entry.note is not other for other in notes):
                notes.append(entry.note)
        if not by_sentence:
            return
        heading = doc.add_heading(labels["explanations"], level=2)
        for run in heading.runs:
            run.font.color.rgb = _HEADING_COLOUR
        for number, sentence in enumerate(sorted(by_sentence), start=1):
            para = doc.add_paragraph()
            bold = para.add_run(f"{number}. ")
            bold.bold = True
            bold.font.size = Pt(_BODY_PT)
            corrected = para.add_run(aligned.sentence_texts[sentence])
            corrected.italic = True
            corrected.font.size = Pt(_BODY_PT)
            for change in by_sentence[sentence]:
                bullet = doc.add_paragraph(style="List Bullet")
                self._add_run(bullet, _explanation_line(change, labels))

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
