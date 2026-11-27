"""GUI tests for the Check tab's body: the switch, the banners, the lock and the card actions."""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QTextCursor
from PySide6.QtWidgets import QToolTip

from teachers_teammate.application.check_document import CheckDocument, check_document_of
from teachers_teammate.application.check_items import ItemKind
from teachers_teammate.domain.data_location import SentRecord, SentStatus
from teachers_teammate.domain.proofreading.carry import ReadingFix
from teachers_teammate.domain.proofreading.edits import edits
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    UnclearNote,
    edit_note,
)
from teachers_teammate.domain.stages import Stage
from teachers_teammate.texts import check as check_text
from teachers_teammate.texts import review as txt
from teachers_teammate.gui.check._masking import Masking
from teachers_teammate.gui.check._side_panel import _SIDE_BY_SIDE_WIDTH
from teachers_teammate.gui.check._page import CheckBody, CheckLayout, CheckPage, ViewKind
from teachers_teammate.application.document_view import DocumentStateView

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
    machine: str = "",
    text_format: str = "plain",
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
            original_img="",
            preprocess_steps=(),
            raw_text_format=text_format,
            correction_review=review,
            machine_correction_text=machine,
            correction_warning=warning,
            sent={},
            page_images=(),
            original_images=(),
        )
    )


def _sent_document(
    *,
    raw: str = RAW,
    corrected: str = CORRECTED,
    review: CorrectionReview | None = None,
    hiding_on: bool = False,
    sent_text: str = "",
    sent_map: dict[str, str] | None = None,
    text_format: str = "plain",
) -> CheckDocument:
    """Return the Check tab's data for a document whose proofreading was sent like this."""
    doc = _document(
        raw=raw, corrected=corrected, review=review, text_format=text_format
    )
    return dataclasses.replace(
        doc,
        sent=_sent_status(hiding_on) if corrected.strip() else {},
        sent_text=sent_text,
        sent_map=dict(sent_map or {}),
    )


def _sent_status(hiding_on: bool) -> dict[str, SentStatus]:
    """Return how a stored proofreading that hid (or did not hide) names was sent."""
    record = SentRecord(provider="ollama", names_hidden=1 if hiding_on else 0, hiding_on=hiding_on)
    return {Stage.CORRECTION: SentStatus(record)}


def _retyped(
    doc: CheckDocument, *, raw: str | None = None, corrected: str | None = None
) -> CheckDocument:
    """Return *doc* as the store hands it back after the teacher typed in one of its texts."""
    return _document(
        source_id=doc.source_id,
        raw=doc.raw_text if raw is None else raw,
        corrected=doc.corrected_text if corrected is None else corrected,
        review=doc.review,
        warning=doc.warning,
        content_review=doc.content_review,
        machine=doc.machine_corrected_text,
    )


def _masking(*, enabled: bool = True, error: str = "") -> Masking:
    """Return a masking that can hide names, with no name in the text."""
    masking = Masking()
    masking.set_available(True)
    masking.set_enabled(enabled)
    masking.set_result({}, error=error)
    return masking


def _body(qtbot, doc: CheckDocument, masking: Masking | None = None) -> CheckBody:
    """Return a shown body that shows *doc*, wired to itself the way the window wires it."""
    body = CheckBody()
    qtbot.addWidget(body)
    body.resize(1400, 800)
    body.show()
    qtbot.wait(20)
    body.corrected_text_edited.connect(
        lambda text: body.update_document(_retyped(doc, corrected=text))
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

    page.show_document(_retyped(doc, raw="Getippt.", corrected=""), 1, 1, typed=True)

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
def test_the_page_offers_proofread_this_document_while_there_is_none(qtbot) -> None:
    """
    Given  a page
    When   a document with a transcription but no proofreading is shown
    Then   the next-step strip offers to proofread it, and stops offering once it is done
    """
    page = _page(qtbot)

    page.show_document(_document(corrected=""), 1, 1)
    assert page._next_step.isHidden() is False

    page.show_document(_document(review=_review(RAW, CORRECTED)), 1, 1)
    assert page._next_step.isHidden() is True


@pytest.mark.gui
def test_the_page_offers_proofread_again_over_a_proofread_document(qtbot) -> None:
    """
    Given  a page
    When   a proofread document is shown, then held by a run
    Then   *Proofread again* is in the header, and goes while the run holds it
    """
    page = _page(qtbot)

    page.show_document(_document(corrected=""), 1, 1)
    assert page._again_btn.isHidden() is True

    page.show_document(_document(review=_review(RAW, CORRECTED)), 1, 1)
    assert page._again_btn.isHidden() is False

    page.set_lock(txt.WAITING_FOR_RUN)
    assert page._again_btn.isHidden() is True


@pytest.mark.gui
def test_ticking_hide_names_asks_for_the_placeholders(qtbot) -> None:
    """
    Given  a document shown with the names as written
    When   the names are to be hidden
    Then   the window is asked for the placeholders, once
    """
    masking = Masking()
    masking.set_available(True)
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)), masking)
    asked: list[str] = []
    body.anonymization_needed.connect(asked.append)

    masking.set_enabled(True)
    body.set_masking(masking)
    body.set_masking(masking)

    assert asked == [RAW]


@pytest.mark.gui
def test_the_zoom_buttons_resize_all_three_texts(qtbot) -> None:
    """
    Given  a page with a proofread document
    When   the larger-text and smaller-text buttons are used
    Then   all three texts follow and the size is reported for storing
    """
    page = _page(qtbot)
    page.show_document(_document(review=_review(RAW, CORRECTED)), 1, 1)
    sizes: list[int] = []
    page.text_zoom_changed.connect(sizes.append)
    before = page.body.read_view.point_size

    page.body.zoom_by(1)
    page.body.zoom_by(-1)
    page.body.zoom_by(-1)

    views = (page.body.read_view, page.body.marked_view, page.body.final_view)
    assert [view.point_size for view in views] == [before - 1] * 3
    assert sizes == [before + 1, before, before - 1]


# ── The banners ───────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_banner_shows_the_most_important_reason_first(qtbot) -> None:
    """
    Given  a proofread document with a warning, names hidden and a run holding it
    When   the body shows it, and then each reason is taken away in turn
    Then   the banner says the lock, then the reading fix, then the warning
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED), warning=WARNING), _masking())
    body.set_lock(txt.WAITING_FOR_RUN)

    assert body.banner.text == txt.WAITING_FOR_RUN
    assert body.banner.action == ""

    body.set_lock(None)
    assert body.banner.text == txt.READING_FIX_WARNING
    assert body.banner.action == txt.EDIT_ANYWAY

    body.banner.action_clicked.emit()
    assert body.banner.text == WARNING
    assert body.banner.action == ""


@pytest.mark.gui
def test_a_failed_hide_stays_in_the_banner(qtbot) -> None:
    """
    Given  a document whose names could not be hidden
    When   the body shows it
    Then   the banner says so, because the names stand in the text while the toggle says hidden
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)), _masking(error="no model"))

    assert body.banner.text == txt.HIDING_PERSONAL_INFO_FAILED.format(reason="no model")
    assert body.banner.action == txt.SHOW_NAMES


@pytest.mark.gui
def test_the_warning_shows_while_the_names_are_hidden(qtbot) -> None:
    """
    Given  a document that was only read, with a warning, and the names hidden
    When   the body shows it
    Then   the banner carries the warning, since hiding is no reason of its own
    """
    body = _body(qtbot, _document(corrected="", warning=WARNING), _masking())

    assert body.banner.text == WARNING


@pytest.mark.gui
def test_toggling_privacy_leaves_the_banner_and_the_views_where_they_are(qtbot) -> None:
    """
    Given  a proofread document with a warning, shown with the names hidden
    When   the names are shown again
    Then   the banner says the same and the two texts keep their height
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED), warning=WARNING), _masking())
    before = (body.banner.text, body.read_view.height(), body.final_view.height())

    body.set_masking(Masking())
    qtbot.wait(20)

    assert (body.banner.text, body.read_view.height(), body.final_view.height()) == before


@pytest.mark.gui
def test_edit_anyway_is_greyed_out_while_the_names_are_hidden(qtbot) -> None:
    """
    Given  a proofread document whose transcription is locked, with the names hidden
    When   the body shows it, and then the names are shown again
    Then   the button is there but disabled with the reason, and works again once they show
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)), _masking())

    assert body.banner.action == txt.EDIT_ANYWAY
    assert body.banner._button.isEnabled() is False
    assert body.banner._button.toolTip() == txt.SHOW_NAMES_TO_CHANGE

    body.set_masking(Masking())

    assert body.banner._button.isEnabled() is True
    assert body.banner._button.toolTip() == ""


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
def test_a_fix_that_keeps_the_proofreading_only_shows_the_reading_fix_banner(qtbot) -> None:
    """
    Given  a page that shows a proofread document
    When   the store hands back the same document after a fix it carried into the proofreading
    Then   the corrections stay in front with their item, and nothing says the proofreading fell
    """
    doc = _document(review=_review(RAW, CORRECTED))
    page = _page(qtbot)
    page.show_document(doc, 1, 1)

    page.show_document(
        _retyped(doc, raw="Der bunter Hund bellt. Die Katze mauzt."), 1, 1, typed=True
    )

    assert page.body.view is ViewKind.MARKED
    assert page.body.panel._list.count() == 1
    assert page.body.banner.text == txt.READING_FIX_WARNING
    assert txt.READING_FIX_FELL_BACK not in page.body.banner.text


def _after_a_fix(qtbot, fix: ReadingFix, *, more_typing: int = 0) -> CheckPage:
    """Return a page that showed a proofread document and was handed one without it after *fix*."""
    doc = _document(review=_review(RAW, CORRECTED))
    page = _page(qtbot)
    page.show_document(doc, 1, 1)
    bare = _document(source_id=doc.source_id, raw="Der braune Hund bellt.", corrected="")
    page.show_document(bare, 1, 1, typed=True, fix=fix)
    for _ in range(more_typing):
        page.show_document(bare, 1, 1, typed=True, fix=ReadingFix.NONE)
    return page


@pytest.mark.gui
def test_a_fix_that_took_the_proofreading_says_so_in_the_banner(qtbot) -> None:
    """
    Given  a page that shows a proofread document
    When   a fix in or next to a correction leaves a document with no proofreading
    Then   the banner says the proofreading is gone because of the correction, and *What was
           read* is in front
    """
    page = _after_a_fix(qtbot, ReadingFix.TOUCHED_CORRECTION)

    assert page.body.view is ViewKind.READ
    assert page.body.banner.text == txt.READING_FIX_FELL_BACK
    assert page.body.banner.action == ""


@pytest.mark.gui
def test_a_fix_that_could_not_be_carried_says_so_without_blaming_a_correction(qtbot) -> None:
    """
    Given  a page that shows a proofread document
    When   a fix that touched no correction still leaves a document with no proofreading
    Then   the banner gives the other reason
    """
    page = _after_a_fix(qtbot, ReadingFix.NOT_CARRIED)

    assert page.body.banner.text == txt.READING_FIX_NOT_CARRIED


@pytest.mark.gui
def test_the_notice_stays_while_the_teacher_keeps_typing(qtbot) -> None:
    """
    Given  a page whose proofreading a fix took, with the strip saying so
    When   the next saves of the typing hand back the document, which has no proofreading to lose
    Then   the strip still says why, so it does not come and go under the cursor
    """
    page = _after_a_fix(qtbot, ReadingFix.TOUCHED_CORRECTION, more_typing=3)

    assert page.body.banner.text == txt.READING_FIX_FELL_BACK


@pytest.mark.gui
def test_the_notice_goes_with_the_document(qtbot) -> None:
    """
    Given  a page whose proofreading a fix took, with the strip saying so
    When   another document is shown
    Then   the strip says nothing about it
    """
    page = _after_a_fix(qtbot, ReadingFix.TOUCHED_CORRECTION)

    page.show_document(_document(source_id="other", corrected=""), 1, 1)

    assert page.body.banner.isHidden() is True


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
    Then   the texts cannot be typed in, a keystroke and the card say why, and the item stays
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    body.panel.set_chosen("edit:1")

    body.set_masking(_masking())

    assert body.chosen == "edit:1"
    assert body.final_view.isReadOnly() is True
    assert body.final_view.toolTip() != txt.SHOW_NAMES_TO_CHANGE
    assert body.read_view.toolTip() != txt.SHOW_NAMES_TO_CHANGE
    QToolTip.hideText()
    qtbot.keyClick(body.final_view, Qt.Key.Key_X)
    assert QToolTip.text() == txt.SHOW_NAMES_TO_CHANGE
    assert body.panel._card.take_out.toolTip() == txt.SHOW_NAMES_TO_CHANGE


@pytest.mark.gui
def test_a_read_only_view_says_nothing_until_the_teacher_types(qtbot) -> None:
    """
    Given  a proofread document whose *What was read* is read-only
    When   the pointer is over the text, and then a key is typed
    Then   no tooltip covers the text, and the key is answered with the reason
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))

    assert body.read_view.isReadOnly() is True
    assert body.read_view.toolTip() != txt.READING_FIX_WARNING
    QToolTip.hideText()
    qtbot.keyClick(body.read_view, Qt.Key.Key_X)
    assert QToolTip.text() == txt.READING_FIX_WARNING


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
def test_what_was_read_puts_the_content_review_in_front(qtbot) -> None:
    """
    Given  a document with a content review
    When   *What was read* is in front, and then *With corrections*
    Then   the panel shows the content review there and the list of corrections here
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED), content_review="Gut lesbar."))

    body.set_view(ViewKind.READ)
    assert body.panel._tabs.currentWidget() is body.panel._content_view

    body.set_view(ViewKind.MARKED)
    assert body.panel._tabs.currentIndex() == 0


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
def test_replace_writes_the_typed_words_and_keeps_the_view(qtbot) -> None:
    """
    Given  a body that shows a document on *With corrections*
    When   the card's replace field is filled in and OK is pressed
    Then   the typed words stand in the final text, and *With corrections* is still in front
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    body.set_view(ViewKind.MARKED)
    body.panel.set_chosen("edit:1")

    body.panel._card.replace.setText("dunkelbraune")
    body.panel._card.replace_ok.click()

    assert body.view is ViewKind.MARKED
    assert body.final_view.toPlainText() == "Der dunkelbraune Hund bellt. Die Katze mauzt."


@pytest.mark.gui
def test_replace_on_an_added_word_replaces_it(qtbot) -> None:
    """
    Given  a correction that only added a word
    When   other words are written in place of the added word
    Then   the added word is gone and the typed words stand in its place
    """
    raw, corrected = "Ich gehe Schule.", "Ich gehe zur Schule."
    body = _body(qtbot, _document(raw=raw, corrected=corrected, review=_review(raw, corrected)))
    body.panel.set_chosen("edit:1")

    body.panel._card.replace.setText("in die")
    body.panel._card.replace_ok.click()

    assert body.final_view.toPlainText() == "Ich gehe in die Schule."


@pytest.mark.gui
def test_replace_does_nothing_while_the_card_is_locked(qtbot) -> None:
    """
    Given  a document the card may not act on, because a run holds it
    When   Enter is pressed in the card's replace field
    Then   the field is disabled and the final text is untouched
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    body.set_lock("A run is working on this document.")
    body.panel.set_chosen("edit:1")

    card = body.panel._card
    card.replace.setText("dunkelbraune")
    card.replace.returnPressed.emit()

    assert not card.replace.isEnabled()
    assert body.final_view.toPlainText() == CORRECTED


@pytest.mark.gui
def test_take_out_keeps_the_view_and_the_other_struck_words(qtbot) -> None:
    """
    Given  a document with two corrections, shown on *With corrections*
    When   *Take out* is pressed for the first one
    Then   the view stays, and the student's words of the other one are still struck through
    """
    raw = "Der bunte Hund bellt. Die Katze mauzt."
    corrected = "Der braune Hund bellt. Die Katze mauzt."
    review = CorrectionReview(
        edits=tuple(
            edit_note(edit, category="word_choice", explanation="Farbe.")
            for edit in edits(raw, corrected)
        )
    )
    body = _body(qtbot, _document(raw=raw, corrected=corrected, review=review))
    body.set_view(ViewKind.MARKED)

    body.panel.take_out_requested.emit("edit:1")

    assert body.view is ViewKind.MARKED
    assert body.final_view.toPlainText() == raw
    assert "bunte" in body.marked_view.toPlainText()


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
    page.raw_text_edited.connect(
        lambda text: page.update_document(_retyped(doc, raw=text, corrected=""))
    )

    page.body.read_view.setPlainText("Der bunte Hund bellt laut.")
    page.body.read_view.flush()

    assert page.body.view is ViewKind.READ
    assert page._switch.isHidden() is True
    assert page._next_step.isHidden() is False


@pytest.mark.gui
def test_a_card_action_does_not_move_the_choice_and_next_walks_on(qtbot) -> None:
    """
    Given  a body that shows a document with a correction and an unclear passage
    When   *Take out* is pressed for the correction, and then *Next*
    Then   the correction stays chosen, and *Next* goes on to the unclear passage
    """
    review = CorrectionReview(
        edits=_review(RAW, CORRECTED).edits,
        unclear=(UnclearNote(10, 14, "Hund", "Welches Tier?", "Koeter"),),
    )
    body = _body(qtbot, _document(review=review))
    assert body.chosen == "edit:1"

    body.panel.take_out_requested.emit("edit:1")

    assert body.chosen == "edit:1"
    assert body._doc is not None
    assert [item.kind for item in body._doc.items] == [ItemKind.TAKEN_OUT, ItemKind.UNCLEAR]

    body.panel._next_button.click()

    assert body.chosen == "unclear:1"


# ── Ctrl+Z from anywhere on the page ──────────────────────────────────────


@pytest.mark.gui
def test_ctrl_z_from_the_list_takes_the_take_out_back(qtbot) -> None:
    """
    Given  a body that shows a document with one correction, and the focus in the list
    When   *Take out* is pressed and then Ctrl+Z
    Then   the correction is back in the text and stored at once, and its item is chosen
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    stored: list[str] = []
    body.corrected_text_edited.connect(stored.append)
    body.panel.take_out_requested.emit("edit:1")
    body.panel._list.setFocus()
    stored.clear()

    qtbot.keyClick(body.panel._list, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)

    assert body.final_view.toPlainText() == CORRECTED
    assert stored == [CORRECTED]
    assert body.chosen == "edit:1"


@pytest.mark.gui
def test_redo_from_the_list_takes_the_correction_out_again(qtbot) -> None:
    """
    Given  a body whose take-out was undone from the list
    When   redo is pressed from the list
    Then   the student's words stand in the text again and are stored
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    body.panel.take_out_requested.emit("edit:1")
    body.panel._list.setFocus()
    qtbot.keyClick(body.panel._list, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    stored: list[str] = []
    body.corrected_text_edited.connect(stored.append)

    qtbot.keySequence(body.panel._list, QKeySequence.StandardKey.Redo)

    assert body.final_view.toPlainText() == RAW
    assert stored == [RAW]


@pytest.mark.gui
def test_ctrl_z_from_the_list_undoes_the_take_out_before_the_typing(qtbot) -> None:
    """
    Given  a body whose final text was typed in and whose correction was then taken out
    When   Ctrl+Z is pressed twice from the list
    Then   the take-out goes first, and then the typing
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    body.set_view(ViewKind.FINAL)
    body.final_view.setFocus()
    cursor = body.final_view.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    body.final_view.setTextCursor(cursor)
    qtbot.keyClicks(body.final_view, "!")
    body.final_view.flush()
    typed = body.final_view.toPlainText()
    body.panel.take_out_requested.emit("edit:1")
    body.panel._list.setFocus()

    qtbot.keyClick(body.panel._list, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert body.final_view.toPlainText() == typed

    qtbot.keyClick(body.panel._list, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert body.final_view.toPlainText() == CORRECTED


@pytest.mark.gui
def test_ctrl_z_in_what_was_read_undoes_the_transcription(qtbot) -> None:
    """
    Given  a body whose transcription may be fixed, with the focus in *What was read*
    When   the teacher types and presses Ctrl+Z
    Then   the transcription's typing is undone and the corrected text is untouched
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    body.banner.action_clicked.emit()
    body.set_view(ViewKind.READ)
    body.read_view.setFocus()
    qtbot.keyClicks(body.read_view, "X")
    body.read_view.flush()

    qtbot.keyClick(body.read_view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)

    assert body.read_view.toPlainText() == RAW
    assert body.final_view.toPlainText() == CORRECTED


@pytest.mark.gui
def test_ctrl_z_in_with_corrections_undoes_the_corrected_text(qtbot) -> None:
    """
    Given  a body whose correction was taken out, with the focus in *With corrections*
    When   Ctrl+Z is pressed there
    Then   the corrected text is what is undone, although that view is read-only
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    body.panel.take_out_requested.emit("edit:1")
    body.set_view(ViewKind.MARKED)
    body.marked_view.setFocus()

    qtbot.keyClick(body.marked_view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)

    assert body.final_view.toPlainText() == CORRECTED


@pytest.mark.gui
def test_the_lock_and_hidden_names_take_ctrl_z_away(qtbot) -> None:
    """
    Given  a body whose take-out can be undone
    When   the run takes the document, and then the names are hidden and shown again
    Then   undo and redo are disabled while either holds, and the history is gone afterwards
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    body.panel.take_out_requested.emit("edit:1")
    assert body._undo_action.isEnabled() is True

    body.set_lock(txt.BEING_PROOFREAD)
    assert body._undo_action.isEnabled() is False
    assert body._redo_action.isEnabled() is False

    body.set_lock(None)
    assert body._undo_action.isEnabled() is True

    body.set_masking(_masking())
    assert body._undo_action.isEnabled() is False

    body.set_masking(Masking())
    assert body._undo_action.isEnabled() is False
    assert body._redo_action.isEnabled() is False


# ── The room the panes take ───────────────────────────────────────────────


@pytest.mark.gui
def test_the_panes_keep_the_widths_the_teacher_dragged_them_to(qtbot) -> None:
    """
    Given  a Check tab whose side panel the teacher dragged wide
    When   it is opened again
    Then   the panel is that wide, enough for the card's two columns
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    body.set_view(ViewKind.MARKED)

    body.set_layout(CheckLayout(split=(200, 400, 800)))
    qtbot.wait(20)

    assert body._splitter.sizes()[2] >= _SIDE_BY_SIDE_WIDTH


@pytest.mark.gui
def test_dragging_a_pane_tells_the_window_the_new_layout(qtbot) -> None:
    """
    Given  a Check tab that shows a document
    When   the teacher drags the handle between the text and the side panel
    Then   the tab says how wide the panes and how tall the card are now
    """
    body = _body(qtbot, _document(review=_review(RAW, CORRECTED)))
    told: list[CheckLayout] = []
    body.layout_changed.connect(told.append)

    body._splitter.moveSplitter(400, 1)
    body.panel._split.moveSplitter(body.panel._card.height() + 40, 1)

    assert told[-1].split == tuple(body._splitter.sizes())
    assert told[-1].card_split == body.panel.card_split


# ── What the box of *What was read* holds ─────────────────────────────────

SENT = "Der <PERSON_1> bellt. Die <PERSON_2> mauzt."
"""What a request of :data:`RAW` was made from, as the anonymizer wrote it."""
SENT_MAP = {"<PERSON_1>": "bunten Hunde", "<PERSON_2>": "Katze"}
"""The map of :data:`SENT`: the model read the first name differently than the scan says."""


def _sent_masking(*, text: str = SENT, mapping: dict[str, str] | None = None) -> Masking:
    """Return a masking that hides names and holds the anonymizer's answer."""
    masking = Masking()
    masking.set_available(True)
    masking.set_enabled(True)
    masking.set_result(SENT_MAP if mapping is None else mapping, text=text)
    return masking


@pytest.mark.gui
def test_the_box_shows_the_string_the_proofreading_was_sent_as(qtbot) -> None:
    """
    Given  a stored proofreading whose request carried placeholders
    When   the Check tab shows it with the names hidden
    Then   the box holds that stored string, and not what masking the transcription gives
    """
    masking = _sent_masking()
    doc = _sent_document(
        review=_review(RAW, CORRECTED), hiding_on=True, sent_text=SENT, sent_map=SENT_MAP
    )

    body = _body(qtbot, doc, masking)
    body.set_view(ViewKind.READ)

    shown = body.read_view.toPlainText()
    # A name the model spelled differently is the one masking the transcription cannot find.
    assert "bunte Hund" in masking.mask(RAW)
    assert "bunte Hund" not in shown
    assert shown == SENT
    assert body.read_view.isReadOnly()
    assert body._sent_caption.text() == check_text.SENT_CAPTION


@pytest.mark.gui
def test_each_placeholder_of_the_box_is_a_chip_with_its_name_behind_it(qtbot) -> None:
    """
    Given  a stored sent text with two placeholders
    When   the teacher hovers the first one
    Then   it is one chip, from its first to its last character, and the tip is its name
    """
    doc = _sent_document(
        review=_review(RAW, CORRECTED), hiding_on=True, sent_text=SENT, sent_map=SENT_MAP
    )
    body = _body(qtbot, doc, _sent_masking())
    body.set_view(ViewKind.READ)
    qtbot.wait(30)
    view = body.read_view
    plain = view.toPlainText()
    start = plain.index("<PERSON_1>")
    end = start + len("<PERSON_1>")

    first, _last = view._map.span(start, end)
    cursor = QTextCursor(view.document())
    cursor.setPosition(first)
    point = view.cursorRect(cursor).center()

    assert view.chip_at_offset(start) == "<PERSON_1>"
    assert view.chip_at_offset(end - 1) == "<PERSON_1>"
    assert view.chip_at_offset(end) == ""
    assert view.chip_at(point) == "<PERSON_1>"
    assert view._tip_at(point) == "bunten Hunde"


@pytest.mark.gui
def test_a_proofreading_sent_as_written_shows_the_transcription_and_can_be_typed_in(qtbot) -> None:
    """
    Given  a stored proofreading whose request carried the names as written
    When   the Check tab shows it with the names hidden
    Then   the box holds the transcription, which the teacher may fix like any other
    """
    doc = _sent_document(review=_review(RAW, CORRECTED), hiding_on=False)

    body = _body(qtbot, doc, _sent_masking())
    body.set_view(ViewKind.READ)
    body._on_banner_action()  # *Edit anyway*: the box holds no placeholder, so it may be typed in

    assert body.read_view.toPlainText() == RAW
    assert body.read_view.isReadOnly() is False
    assert body._sent_caption.text() == check_text.NAMES_SENT_AS_WRITTEN


@pytest.mark.gui
def test_the_names_come_back_when_hiding_is_switched_off(qtbot) -> None:
    """
    Given  a proofreading stored with its names replaced
    When   the teacher shows the names again
    Then   the box holds the transcription again, and the line above it says why
    """
    doc = _sent_document(
        review=_review(RAW, CORRECTED), hiding_on=True, sent_text=SENT, sent_map=SENT_MAP
    )

    body = _body(qtbot, doc, _sent_masking())
    body.set_view(ViewKind.READ)
    masking = Masking()
    masking.set_available(True)
    masking.set_result(SENT_MAP, text=SENT)
    body.show_document(doc, masking)

    assert body.read_view.toPlainText() == RAW
    assert body._sent_caption.text() == check_text.NAMES_SHOWN_CAPTION


@pytest.mark.gui
def test_without_the_add_on_the_line_says_the_names_cannot_be_hidden(qtbot) -> None:
    """
    Given  a window whose Privacy add-on is missing
    When   it shows a text with the names hidden asked for
    Then   the box holds the transcription and the line names what is missing
    """
    doc = _document(raw=RAW, corrected="")
    masking = Masking()
    masking.set_result({})

    body = _body(qtbot, doc, masking)

    assert body.read_view.toPlainText() == RAW
    assert body._sent_caption.text() == check_text.NAMES_NOT_HIDDEN_CAPTION


@pytest.mark.gui
def test_what_will_be_sent_is_shown_before_the_proofreading_runs(qtbot) -> None:
    """
    Given  a text that is not proofread yet, with the names hidden
    When   the anonymizer's answer arrives
    Then   the box holds what the request will be made from, and says so
    """
    doc = _document(raw=RAW, corrected="")

    body = _body(qtbot, doc, _sent_masking())

    assert body.read_view.toPlainText() == SENT
    assert body.read_view.isReadOnly()
    assert body._sent_caption.text() == check_text.WILL_BE_SENT_CAPTION


@pytest.mark.gui
def test_a_sent_text_with_crossed_out_words_says_they_are_not_sent(qtbot) -> None:
    """
    Given  a stored proofreading of a Markdown text whose sent string crosses out a word
    When   the box shows it
    Then   the line above it says that crossed-out words do not leave this computer
    """
    doc = _sent_document(
        raw="Der ~~bunte~~ <PERSON_1> bellt.",
        corrected="Der braune <PERSON_1> bellt.",
        review=_review("Der bunte Hund bellt.", "Der braune Hund bellt."),
        hiding_on=True,
        sent_text="Der ~~bunte~~ <PERSON_1> bellt.",
        sent_map={"<PERSON_1>": "Hund"},
        text_format="markdown",
    )

    body = _body(qtbot, doc, _sent_masking(text="Der ~~bunte~~ <PERSON_1> bellt."))
    body.set_view(ViewKind.READ)

    assert body._sent_caption.text() == "\n".join(
        (check_text.SENT_CAPTION, check_text.CROSSED_OUT_NOT_SENT)
    )
