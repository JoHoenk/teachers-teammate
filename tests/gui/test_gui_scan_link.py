"""GUI tests for the scan link: the sentence under the cursor, marked on the page."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QPixmap, QTextCursor

from teachers_teammate.application.check_document import CheckDocument, check_document_of
from teachers_teammate.application.document_view import DocumentStateView
from teachers_teammate.domain.page_layout import ScanBox
from teachers_teammate.domain.proofreading.edits import edits
from teachers_teammate.domain.proofreading.review import CorrectionReview, edit_note
from teachers_teammate.gui.check._masking import Masking
from teachers_teammate.gui.check._page import CheckBody, ViewKind

RAW = "Der bunte Hund bellt. Die Katze mauzt."
"""The transcription as the recognition read it, in two sentences."""
CORRECTED = "Der braune Hund bellt. Die Katze mauzt."
"""The same in the proofread text: one word changed, nothing added."""


def _corners(top: float) -> tuple[tuple[float, float], ...]:
    """Return the four corners of a line that sits at *top* of the page."""
    return ((0.1, top), (0.9, top), (0.9, top + 0.08), (0.1, top + 0.08))


def _place(start: int, end: int, confidence: float, top: float) -> ScanBox:
    """Return one placed line of the scan, standing on *start*..*end* of the reading."""
    return ScanBox(page=0, start=start, end=end, confidence=confidence, boxes=(_corners(top),))


def _places() -> tuple[ScanBox, ...]:
    """Return three lines placed in :data:`RAW`: the first sentence read worse than the second."""
    second = RAW.index("Die")
    return (
        _place(0, 15, 0.9, 0.1),
        _place(15, second - 1, 0.35, 0.2),
        _place(second, len(RAW), 0.95, 0.3),
    )


def _document(
    *,
    raw: str = RAW,
    corrected: str = CORRECTED,
    layout: tuple[ScanBox, ...] | None = None,
    pages: tuple = (),
    text_format: str = "plain",
) -> CheckDocument:
    """Return the Check tab's data for a stored document whose lines are placed."""
    review = (
        CorrectionReview(
            edits=(
                edit_note(edits(raw, corrected)[0], category="word_choice", explanation="Farbe."),
            )
        )
        if corrected.strip() and text_format == "plain"
        else None
    )
    return check_document_of(
        DocumentStateView(
            source_id="/tmp/Anna.png",
            name="Anna.png",
            preview_img="",
            raw_text=raw,
            machine_raw_text=raw,
            raw_layout=_places() if layout is None else layout,
            correction_text=corrected,
            evaluation_text="",
            ocr_done=True,
            correction_done=bool(corrected.strip()),
            evaluation_done=False,
            original_img="",
            preprocess_steps=(),
            raw_text_format=text_format,
            correction_review=review,
            correction_warning="",
            sent={},
            page_images=tuple(str(page) for page in pages),
            original_images=(),
        )
    )


def _page_file(tmp_path):
    """Return a one-page image to show beside the texts."""
    pix = QPixmap(400, 900)
    pix.fill(QColor("#ffffff"))
    path = tmp_path / "page0.png"
    assert pix.save(str(path))
    return path


def _body(qtbot, doc: CheckDocument) -> CheckBody:
    """Return a shown body that shows *doc*."""
    body = CheckBody()
    qtbot.addWidget(body)
    body.resize(1400, 800)
    body.show()
    qtbot.wait(20)
    body.show_document(doc, Masking())
    return body


def _put_cursor(view, offset: int) -> None:
    """Put the cursor of *view* at the plain *offset* of the text it shows."""
    cursor = QTextCursor(view.document())
    cursor.setPosition(view._map.position(offset))
    view.setTextCursor(cursor)


def _drawn(body: CheckBody) -> list:
    """Return the polygons the scan draws over its pages."""
    return body.scan.view._mark_items


@pytest.mark.gui
def test_the_cursor_in_the_final_text_marks_the_sentence_it_is_in(qtbot, tmp_path) -> None:
    """
    Given  a document whose lines are placed, shown on *Final text*
    When   the cursor stands in the second sentence
    Then   the scan draws the boxes of that sentence only
    """
    body = _body(qtbot, _document(pages=(_page_file(tmp_path),)))
    body.set_view(ViewKind.FINAL)
    assert len(_drawn(body)) == 2  # the first sentence stands on two lines

    _put_cursor(body.final_view, RAW.index("Katze"))

    assert len(_drawn(body)) == 1
    assert {page for page, _box in body.scan.view._mark.polygons} == {0}


@pytest.mark.gui
def test_the_shade_says_how_hard_the_line_was_to_read(qtbot, tmp_path) -> None:
    """
    Given  a document whose first sentence was read badly and whose second one was read well
    When   the cursor moves from the second sentence to the first
    Then   the mark it draws is the stronger shade of the two
    """
    body = _body(qtbot, _document(pages=(_page_file(tmp_path),)))
    body.set_view(ViewKind.FINAL)
    _put_cursor(body.final_view, RAW.index("Katze"))
    sure = body.scan.view._mark.shade

    _put_cursor(body.final_view, RAW.index("bunte"))

    assert body.scan.view._mark.shade > sure


@pytest.mark.gui
def test_the_cursor_in_what_was_read_marks_its_own_sentence(qtbot, tmp_path) -> None:
    """
    Given  a document that was only read, its lines placed
    When   the cursor stands in the second sentence of *What was read*
    Then   the scan marks that sentence there too
    """
    body = _body(qtbot, _document(corrected="", pages=(_page_file(tmp_path),)))
    assert body.view is ViewKind.READ
    _put_cursor(body.read_view, 3)

    assert len(_drawn(body)) == 2

    _put_cursor(body.read_view, RAW.index("mauzt"))

    assert len(_drawn(body)) == 1


@pytest.mark.gui
def test_a_struck_word_in_with_corrections_marks_the_sentence_it_replaced(qtbot, tmp_path) -> None:
    """
    Given  *With corrections*, which puts the student's word in front of the word that replaced it
    When   the cursor stands inside those struck words
    Then   the sentence they belong to is marked, not nothing
    """
    body = _body(qtbot, _document(pages=(_page_file(tmp_path),)))
    body.set_view(ViewKind.MARKED)
    replaced = body.marked_view._map.insertion(CORRECTED.index("braune"))
    cursor = QTextCursor(body.marked_view.document())
    cursor.setPosition(replaced + 2)  # inside the struck words in front of the replacement

    body.marked_view.setTextCursor(cursor)

    assert body.marked_view.corrected_offset() == CORRECTED.index("braune")
    assert len(_drawn(body)) == 2


@pytest.mark.gui
def test_the_original_pages_carry_no_mark(qtbot, tmp_path) -> None:
    """
    Given  a scan that marks the sentence under the cursor
    When   the teacher switches to the pages as they were scanned, and back
    Then   the mark is gone there and back on the prepared pages
    """
    body = _body(qtbot, _document(pages=(_page_file(tmp_path),)))
    assert len(_drawn(body)) == 2

    body.scan.view.show_originals(True)
    assert _drawn(body) == []

    body.scan.view.show_originals(False)

    assert len(_drawn(body)) == 2


@pytest.mark.gui
def test_a_reading_without_places_marks_nothing(qtbot, tmp_path) -> None:
    """
    Given  a document whose reading was never placed — the reader's models were not there
    When   it is shown and the cursor moves
    Then   the scan draws nothing, and says nothing about it
    """
    body = _body(qtbot, _document(layout=(), pages=(_page_file(tmp_path),)))
    body.set_view(ViewKind.FINAL)

    _put_cursor(body.final_view, RAW.index("Katze"))

    assert _drawn(body) == []
    assert body.scan.view._mark is None


@pytest.mark.gui
def test_hidden_names_still_mark_the_sentence(qtbot, tmp_path) -> None:
    """
    Given  a document whose corrected text is shown with a placeholder where a name stands
    When   the cursor stands inside that placeholder
    Then   the sentence the name is in is marked
    """
    masking = Masking()
    masking.set_available(True)
    masking.set_enabled(True)
    masking.set_result({"<PERSON_1>": "Katze"}, text="")
    body = _body(qtbot, _document(pages=(_page_file(tmp_path),)))
    body.set_masking(masking)
    body.set_view(ViewKind.FINAL)
    placeholder = body.final_view.plain.index("<PERSON_1>")

    _put_cursor(body.final_view, placeholder + 3)

    assert len(_drawn(body)) == 1


@pytest.mark.gui
def test_an_edited_corrected_text_still_marks(qtbot, tmp_path) -> None:
    """
    Given  a document whose lines are placed, and a corrected text the proofreading made longer
    When   the cursor stands behind the words it added
    Then   the scan still marks the sentence the cursor is in
    """
    longer = "Der braune Hund bellt sehr laut. Die Katze mauzt."
    body = _body(qtbot, _document(corrected=longer, pages=(_page_file(tmp_path),)))
    body.set_view(ViewKind.FINAL)

    _put_cursor(body.final_view, longer.index("Katze"))

    assert len(_drawn(body)) == 1


@pytest.mark.gui
def test_hidden_names_in_a_markdown_text_mark_the_sentence_after_them(qtbot, tmp_path) -> None:
    """
    Given  a Markdown document whose first sentence ends in a name shown as a longer placeholder
    When   the cursor stands at the start of the second sentence
    Then   the second sentence is marked: the cursor is carried back through the plain text
    """
    raw = "**Der** bunte Hund bellt laut. Die Katze mauzt."
    corrected = "**Der** braune Hund bellt laut. Die Katze mauzt."
    plain = "Der bunte Hund bellt laut. Die Katze mauzt."
    second = plain.index("Die")
    layout = (_place(0, second - 1, 0.35, 0.1), _place(second, len(plain), 0.95, 0.3))
    masking = Masking()
    masking.set_available(True)
    masking.set_enabled(True)
    masking.set_result({"<PERSON_1>": "laut"}, text="")
    body = _body(
        qtbot,
        _document(
            raw=raw,
            corrected=corrected,
            layout=layout,
            pages=(_page_file(tmp_path),),
            text_format="markdown",
        ),
    )
    body.set_masking(masking)
    body.set_view(ViewKind.FINAL)

    _put_cursor(body.final_view, body.final_view.plain.index("Die"))

    assert body.scan.view._mark is not None
    assert body.scan.view._mark.confidence == pytest.approx(0.95)
