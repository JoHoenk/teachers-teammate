"""GUI tests for the Check tab's body: the switch, the banners, the lock and the card actions."""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

import dataclasses

import pytest

from teachers_teammate.application.check_document import CheckDocument, check_document_of
from teachers_teammate.application.check_items import ItemKind
from teachers_teammate.domain.proofreading.edits import edits
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    UnclearNote,
    edit_note,
)
from teachers_teammate.gui import _user_text as txt
from teachers_teammate.gui.check._masking import Masking
from teachers_teammate.gui.check._page import CheckBody, CheckPage, ViewKind
from teachers_teammate.infrastructure.state_repository import DocumentStateView

RAW = "Der bunte Hund bellt. Die Katze mauzt."
CORRECTED = "Der braune Hund bellt. Die Katze mauzt."
WARNING = "The explanations of this review are missing."


def _review(raw: str, corrected: str, *, category: str = "word_choice") -> CorrectionReview:
    """Return a review that explains the first edit from *raw* to *corrected*."""
    return CorrectionReview(
        edits=(edit_note(edits(raw, corrected)[0], category=category, explanation="Farbe."),)
    )


def _document(
    *,
    source_id: str = "/tmp/Anna.png",
    raw: str = RAW,
    corrected: str = CORRECTED,
    review: CorrectionReview | None = None,
    warning: str = "",
    content_review: str = "",
) -> CheckDocument:
    """Return the Check tab's data for a stored document."""
    return check_document_of(
        DocumentStateView(
            source_id=source_id,
            name=source_id.rsplit("/", 1)[-1],
            preview_img="",
            raw_text=raw,
            correction_text=corrected,
            evaluation_text=content_review,
            ocr_done=True,
            correction_done=bool(corrected.strip()),
            evaluation_done=bool(content_review),
            cache_status_label="",
            loaded_at="",
            original_img="",
            preprocess_steps=(),
            raw_text_format="plain",
            correction_review=review,
            correction_warning=warning,
            sent={},
            page_images=(),
            original_images=(),
        )
    )


def _masking(*, enabled: bool = True) -> Masking:
    """Return a masking that can hide names, with no name in the text."""
    masking = Masking()
    masking.set_available(True)
    masking.set_enabled(enabled)
    masking.set_result({})
    return masking


def _body(qtbot, doc: CheckDocument, masking: Masking | None = None) -> CheckBody:
    """Return a shown body that shows *doc*, wired to itself the way the window wires it."""
    body = CheckBody()
    qtbot.addWidget(body)
    body.resize(1400, 800)
    body.show()
    qtbot.wait(20)
    body.corrected_text_edited.connect(
        lambda text: body.update_document(doc.realigned(doc.raw_text, text))
    )
    body.show_document(doc, masking or Masking())
    return body


def _page(qtbot) -> CheckPage:
    """Return a shown Check page."""
    page = CheckPage()
    qtbot.addWidget(page)
    page.resize(1400, 800)
    page.show()
    qtbot.wait(20)
    return page


def _checked(page: CheckPage) -> ViewKind:
    """The view whose switch button is checked."""
    return next(view for view, button in page._switch_buttons.items() if button.isChecked())


# ── The switch ────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_switch_opens_on_with_corrections_when_there_is_a_proofreading(qtbot) -> None:
    """
    Given  a document that was proofread
    When   the page shows it
    Then   the switch is there and *With corrections* is in front
    """
    page = _page(qtbot)

    page.show_document(_document(review=_review(RAW, CORRECTED)), 1, 1)

    assert page._switch.isHidden() is False
    assert page.body.view is ViewKind.MARKED
    assert _checked(page) is ViewKind.MARKED


@pytest.mark.gui
def test_recognition_only_has_no_switch_and_shows_what_was_read(qtbot) -> None:
    """
    Given  a document that was only read, not proofread
    When   the page shows it
    Then   there is no switch and *What was read* is in front
    """
    page = _page(qtbot)

    page.show_document(_document(corrected=""), 1, 1)

    assert page._switch.isHidden() is True
    assert page.body.view is ViewKind.READ


@pytest.mark.gui
def test_the_choice_holds_across_documents(qtbot) -> None:
    """
    Given  a page that shows a proofread document on *Final text*
    When   the next proofread document is shown
    Then   it opens on *Final text* too
    """
    page = _page(qtbot)
    page.show_document(_document(review=_review(RAW, CORRECTED)), 1, 2)

    page.set_view(ViewKind.FINAL)
    page.show_document(_document(review=_review(RAW, CORRECTED)), 2, 2)

    assert page.body.view is ViewKind.FINAL
    assert _checked(page) is ViewKind.FINAL


@pytest.mark.gui
def test_a_document_without_a_proofreading_does_not_change_the_choice(qtbot) -> None:
    """
    Given  a page whose teacher chose *Final text*, and a document without a proofreading in between
    When   the next proofread document is shown after it
    Then   it opens on *Final text* again, not on *What was read*
    """
    page = _page(qtbot)
    page.show_document(_document(review=_review(RAW, CORRECTED)), 1, 3)
    page.set_view(ViewKind.FINAL)
    page.show_document(_document(source_id="/tmp/B.png", corrected=""), 2, 3)
    assert page.body.view is ViewKind.READ

    page.show_document(_document(source_id="/tmp/C.png", review=_review(RAW, CORRECTED)), 3, 3)

    assert page.body.view is ViewKind.FINAL


@pytest.mark.gui
def test_a_reloaded_document_shows_the_transcription_the_run_made(qtbot) -> None:
    """
    Given  a page that shows a document, and a run that read the same document again
    When   the document is shown again with the new transcription
    Then   *What was read* shows the new text, so the next keystroke cannot store the old one
    """
    page = _page(qtbot)
    page.show_document(_document(raw="Alter Text.", corrected=""), 1, 1)
    stored: list[str] = []
    page.raw_text_edited.connect(stored.append)

    page.show_document(_document(raw="Neuer Text.", corrected=""), 1, 1)
    page.body.read_view.insertPlainText("!")
    page.body.read_view.flush()

    assert len(stored) == 1
    assert "Neuer Text." in stored[0]
    assert "Alter" not in stored[0]


@pytest.mark.gui
def test_a_stored_edit_of_the_transcription_keeps_the_text_and_the_scan(qtbot, tmp_path) -> None:
    """
    Given  a page that shows a scan of two pages, scrolled down, and a transcription being typed
    When   the stored edit comes back as the same document with typed=True
    Then   the scan is not loaded again and the text the teacher typed stays as it is
    """
    from PySide6.QtGui import QColor, QPixmap  # noqa: PLC0415

    pages = []
    for index in range(2):
        pix = QPixmap(400, 900)
        pix.fill(QColor("#ffffff"))
        path = tmp_path / f"page{index}.png"
        assert pix.save(str(path))
        pages.append(path)
    doc = _document(corrected="")
    doc = dataclasses.replace(doc, pages=tuple(pages), original_pages=(None, None))
    page = _page(qtbot)
    page.show_document(doc, 1, 1)
    bar = page.body.scan.view.verticalScrollBar()
    bar.setValue(bar.maximum())
    scrolled = bar.value()
    assert scrolled > 0
    page.body.read_view.setPlainText("Getippt.")
    cursor_text = page.body.read_view.toPlainText()

    page.show_document(doc.realigned("Getippt.", ""), 1, 1, typed=True)

    assert bar.value() == scrolled
    assert page.body.read_view.toPlainText() == cursor_text


@pytest.mark.gui
def test_a_document_without_a_proofreading_falls_back_to_what_was_read(qtbot) -> None:
    """
    Given  a page that shows a proofread document on *Final text*
    When   a document without a proofreading is shown
    Then   it falls back to *What was read* and the switch goes away
    """
    page = _page(qtbot)
    page.show_document(_document(review=_review(RAW, CORRECTED)), 1, 2)
    page.set_view(ViewKind.FINAL)

    page.show_document(_document(corrected=""), 2, 2)

    assert page.body.view is ViewKind.READ
    assert page._switch.isHidden() is True


@pytest.mark.gui
def test_the_header_offers_proofread_this_document_while_there_is_none(qtbot) -> None:
    """
    Given  a page
    When   a document with a transcription but no proofreading is shown
    Then   the header offers to proofread it, and stops offering once it is done
    """
    page = _page(qtbot)

    page.show_document(_document(corrected=""), 1, 1)
    assert page._proofread_btn.isHidden() is False

    page.show_document(_document(review=_review(RAW, CORRECTED)), 1, 1)
    assert page._proofread_btn.isHidden() is True


# ── The banners ───────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_banner_shows_the_most_important_reason_first(qtbot) -> None:
    """
    Given  a proofread document with a warning, names hidden and a run holding it
    When   the body shows it, and then each reason is taken away in turn
    Then   the banner says the lock, then the names, then the reading fix, then the warning
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED), warning=WARNING), _masking())
    body.set_lock(txt.WAITING_FOR_RUN)

    assert body.banner.text == txt.WAITING_FOR_RUN
    assert body.banner.action == ""

    body.set_lock(None)
    assert body.banner.text == txt.TURN_OFF_HIDING_TO_EDIT
    assert body.banner.action == txt.SHOW_NAMES

    body.set_masking(Masking())
    assert body.banner.text == txt.READING_FIX_WARNING
    assert body.banner.action == txt.EDIT_ANYWAY

    body.banner.action_clicked.emit()
    assert body.banner.text == WARNING
    assert body.banner.action == ""


@pytest.mark.gui
def test_a_document_without_a_proofreading_has_no_reading_fix_banner(qtbot) -> None:
    """
    Given  a document that was only read
    When   the body shows it
    Then   there is no banner and the transcription can be typed in
    """
    body = _body(qtbot, _document(corrected=""))

    assert body.banner.isHidden() is True
    assert body.read_view.isReadOnly() is False


@pytest.mark.gui
def test_edit_anyway_frees_the_transcription_once_per_document(qtbot) -> None:
    """
    Given  a proofread document whose transcription is locked by the reading fix
    When   *Edit anyway* is clicked, and then another document is shown
    Then   the transcription is editable, and the next document asks again
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    assert body.read_view.isReadOnly() is True

    body.banner.action_clicked.emit()
    assert body.read_view.isReadOnly() is False
    assert body.banner.isHidden() is True

    body.show_document(
        _document(source_id="/tmp/Ben.png", review=_review(RAW, CORRECTED)), Masking()
    )
    assert body.read_view.isReadOnly() is True
    assert body.banner.text == txt.READING_FIX_WARNING


@pytest.mark.gui
def test_the_lock_makes_both_texts_read_only(qtbot) -> None:
    """
    Given  a body that shows a document
    When   the run takes it
    Then   neither text can be typed in, and the lock is lifted again afterwards
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))

    body.set_lock(txt.BEING_PROOFREAD)

    assert body.read_view.isReadOnly() is True
    assert body.final_view.isReadOnly() is True

    body.set_lock(None)

    assert body.final_view.isReadOnly() is False


@pytest.mark.gui
def test_hiding_the_names_blocks_typing_and_keeps_the_chosen_item(qtbot) -> None:
    """
    Given  a body that shows a document with a chosen item
    When   the names are hidden
    Then   the texts cannot be typed in, the card says why, and the item stays chosen
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    body.panel.set_chosen("edit:1")

    body.set_masking(_masking())

    assert body.chosen == "edit:1"
    assert body.final_view.isReadOnly() is True
    assert body.panel._card.take_out.toolTip() == txt.SHOW_NAMES_TO_CHANGE


# ── The layout ────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_side_panel_is_hidden_on_what_was_read(qtbot) -> None:
    """
    Given  a body that shows a proofread document
    When   *What was read* is in front, and then *With corrections*
    Then   the side panel is hidden on the first and shown on the second
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))

    body.set_view(ViewKind.READ)
    assert body.panel.isHidden() is True

    body.set_view(ViewKind.MARKED)
    assert body.panel.isHidden() is False


@pytest.mark.gui
def test_a_content_review_keeps_the_side_panel_on_what_was_read(qtbot) -> None:
    """
    Given  a document with a content review
    When   *What was read* is in front
    Then   the side panel stays, because the content review lives in it
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED), content_review="Gut lesbar."))

    body.set_view(ViewKind.READ)

    assert body.panel.isHidden() is False


@pytest.mark.gui
def test_a_narrow_window_folds_the_scan_and_show_scan_opens_it(qtbot) -> None:
    """
    Given  a body in a wide window
    When   the window becomes narrow, and then *Show scan* is clicked
    Then   the scan folds to a strip and opens again on request
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    assert body.scan.isHidden() is False

    body.resize(1000, 700)
    qtbot.wait(20)

    assert body.scan.isHidden() is True
    assert body._show_scan_btn.isHidden() is False

    body._show_scan_btn.click()

    assert body.scan.isHidden() is False
    assert body._show_scan_btn.isHidden() is True


# ── The card actions ──────────────────────────────────────────────────────


@pytest.mark.gui
def test_take_out_puts_the_students_words_back_and_ctrl_z_restores_them(qtbot) -> None:
    """
    Given  a body that shows a document with one correction
    When   *Take out* is clicked, and then Ctrl+Z
    Then   the stored text is the student's again and the item is taken out, and both come back
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))

    body.panel.take_out_requested.emit("edit:1")

    assert body.final_view.toPlainText() == RAW
    assert body._doc is not None
    assert [item.kind for item in body._doc.items] == [ItemKind.TAKEN_OUT]
    assert body._doc.items[0].key == "edit:1"

    body.final_view.undo()
    body.final_view.flush()

    assert body.final_view.toPlainText() == CORRECTED
    assert body._doc is not None
    assert [item.kind for item in body._doc.items] == [ItemKind.CORRECTION]


@pytest.mark.gui
def test_put_back_restores_a_taken_out_correction(qtbot) -> None:
    """
    Given  a document whose correction was taken out
    When   *Put back* is clicked
    Then   the corrected words stand in the text again and the item is a correction
    """
    body = _body(qtbot, _document(corrected=RAW, review=_review(RAW, CORRECTED)))
    assert body._doc is not None
    assert [item.kind for item in body._doc.items] == [ItemKind.TAKEN_OUT]

    body.panel.put_back_requested.emit("edit:1")

    assert body.final_view.toPlainText() == CORRECTED
    assert body._doc is not None
    assert [item.kind for item in body._doc.items] == [ItemKind.CORRECTION]


@pytest.mark.gui
def test_use_suggestion_writes_the_wording_into_the_text(qtbot) -> None:
    """
    Given  a document with an unclear passage and a suggested wording
    When   *Use suggestion* is clicked
    Then   the wording stands in the text and the change is the teacher's own
    """
    review = CorrectionReview(
        edits=_review(RAW, CORRECTED).edits,
        unclear=(UnclearNote(10, 14, "Hund", "Welches Tier?", "Koeter"),),
    )
    body = _body(qtbot, _document(review=review))

    body.panel.use_suggestion_requested.emit("unclear:1")

    assert body.final_view.toPlainText() == "Der braune Koeter bellt. Die Katze mauzt."
    assert body._doc is not None
    assert ItemKind.OWN in [item.kind for item in body._doc.items]


@pytest.mark.gui
def test_take_out_all_puts_back_every_minor_correction(qtbot) -> None:
    """
    Given  a document with two spelling corrections
    When   *Take out all* is clicked
    Then   the student's spelling stands in the text again
    """
    raw = "Der Hunt bellt. Die Katz mauzt."
    corrected = "Der Hund bellt. Die Katze mauzt."
    review = CorrectionReview(
        edits=tuple(
            edit_note(edit, category="spelling", explanation="Rechtschreibung.")
            for edit in edits(raw, corrected)
        )
    )
    body = _body(qtbot, _document(raw=raw, corrected=corrected, review=review))

    body.panel.take_out_all_requested.emit()

    assert body.final_view.toPlainText() == raw


@pytest.mark.gui
def test_change_goes_to_the_final_text_and_selects_the_words(qtbot) -> None:
    """
    Given  a body that shows a document on *With corrections*
    When   *Change* is clicked for a correction
    Then   *Final text* is in front with the corrected words selected
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))

    body.panel.change_requested.emit("edit:1")

    assert body.view is ViewKind.FINAL
    assert body.final_view.textCursor().selectedText() == "braune"


@pytest.mark.gui
def test_a_reading_fix_removes_the_proofreading_and_offers_proofread_this_document(qtbot) -> None:
    """
    Given  a page that shows a proofread document, with *Edit anyway* clicked
    When   the teacher types into *What was read*
    Then   the stored document has no proofreading, the switch goes and the header offers one
    """
    page = _page(qtbot)
    doc = _document(review=_review(RAW, CORRECTED))
    page.show_document(doc, 1, 1)
    page.body.banner.action_clicked.emit()
    page.raw_text_edited.connect(lambda text: page.update_document(doc.realigned(text, "")))

    page.body.read_view.setPlainText("Der bunte Hund bellt laut.")
    page.body.read_view.flush()

    assert page.body.view is ViewKind.READ
    assert page._switch.isHidden() is True
    assert page._proofread_btn.isHidden() is False
