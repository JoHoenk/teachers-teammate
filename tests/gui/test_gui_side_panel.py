"""GUI tests for the Check tab's side panel: the card of the chosen item and the list."""

from __future__ import annotations

import pytest
import qdarkstyle
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication, QBoxLayout

from teachers_teammate.application.check_document import CheckDocument, check_document_of
from teachers_teammate.application.check_items import ItemKind
from teachers_teammate.domain.proofreading.edits import edits
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    UnclearNote,
    edit_note,
)
from teachers_teammate.texts import review as txt
from teachers_teammate.gui.check import _style
from teachers_teammate.gui.check._masking import Masking
from teachers_teammate.domain.teacher_work import WorkStatus
from teachers_teammate.gui.check._side_panel import (
    FILTER_LEFT_OUT,
    FILTER_UNCLEAR,
    FILTER_UNSEEN,
    SidePanel,
    _BAR_WIDTH,
    _category_of,
    _column_rects,
    _icon_of,
    _row_font,
)
from teachers_teammate.application.document_view import DocumentStateView
from teachers_teammate.texts import check as check_txt

RAW = "Der bunte Hund bellt. Die Katze mauzt."
CORRECTED = "Der braune Hund bellt. Die Katze mauzt."


def _note(raw: str, corrected: str, *, category: str = "word_choice") -> CorrectionReview:
    """Return a review that explains the first edit from *raw* to *corrected*."""
    return CorrectionReview(
        edits=(edit_note(edits(raw, corrected)[0], category=category, explanation="Farbe."),)
    )


def _document(
    *,
    source_id: str = "/tmp/Anna.png",
    raw: str = RAW,
    corrected: str = CORRECTED,
    machine: str = "",
    review: CorrectionReview | None = None,
    warning: str = "",
    content_review: str = "",
    work: WorkStatus | None = None,
) -> CheckDocument:
    """Return the Check tab's data for a stored document."""
    return check_document_of(
        DocumentStateView(
            source_id=source_id,
            name="Anna.png",
            preview_img="",
            raw_text=raw,
            correction_text=corrected,
            evaluation_text=content_review,
            ocr_done=True,
            correction_done=True,
            evaluation_done=bool(content_review),
            original_img="",
            preprocess_steps=(),
            raw_text_format="plain",
            machine_correction_text=machine,
            correction_review=review,
            correction_warning=warning,
            sent={},
            page_images=(),
            original_images=(),
            work=work or WorkStatus(),
        )
    )


def _panel(qtbot, doc: CheckDocument, masking: Masking | None = None) -> SidePanel:
    """Return a shown side panel of *doc*."""
    panel = SidePanel()
    qtbot.addWidget(panel)
    panel.resize(320, 600)
    panel.show()
    qtbot.wait(20)
    panel.set_document(doc, masking or Masking())
    return panel


def _buttons(panel: SidePanel) -> list[str]:
    """The buttons the card offers, in card order."""
    return [button.text() for button in panel._card._buttons if not button.isHidden()]


def _replace_field(panel: SidePanel) -> str:
    """What the card's replace field says, or ``""`` when the card offers none."""
    field = panel._card.replace
    return field.text() if not field.isHidden() else ""


def _card_text(panel: SidePanel) -> str:
    """The headline and the body of the card, as one line."""
    return f"{panel._card._title.text()} | {panel._card._body.text()}"


# ── The list ──────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_list_has_one_row_per_item(qtbot) -> None:
    """
    Given  a document with two corrections
    When   the side panel shows it
    Then   the list has one row per correction and the footer counts them
    """
    doc = _document(review=_note(RAW, CORRECTED))

    panel = _panel(qtbot, doc)

    assert panel.row_count == len(doc.items) == 1
    assert panel._counts.text() == "1 correction \u00b7 0 of 1 looked at"


@pytest.mark.gui
def test_the_footer_counts_corrections_and_unclear_passages(qtbot) -> None:
    """
    Given  a document with one correction and one unclear passage
    When   the side panel shows it
    Then   the footer names both counts
    """
    review = CorrectionReview(
        edits=_note(RAW, CORRECTED).edits,
        unclear=(UnclearNote(4, 9, "bunte", "Farbe unklar.", "bunte Farbe"),),
    )

    panel = _panel(qtbot, _document(review=review))

    assert panel._counts.text() == "1 correction · 1 unclear · 0 of 2 looked at"


@pytest.mark.gui
def test_the_first_item_is_reported_as_shown_when_the_list_opens(qtbot) -> None:
    """
    Given  a document with two items
    When   the side panel shows it
    Then   the page is told which item the card shows, so it counts as looked at
    """
    review = CorrectionReview(
        edits=_note(RAW, CORRECTED).edits,
        unclear=(UnclearNote(4, 9, "bunte", "Farbe unklar.", "bunte Farbe"),),
    )
    panel = SidePanel()
    qtbot.addWidget(panel)
    shown: list[str] = []
    panel.item_shown.connect(shown.append)

    panel.set_document(_document(review=review), Masking())

    assert shown == ["edit:1"]


@pytest.mark.gui
def test_an_item_not_looked_at_has_the_bullet(qtbot) -> None:
    """
    Given  a document nothing was looked at yet
    When   the side panel shows it
    Then   its row has the bullet in front of it
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    assert _icon_of(panel._rows[0], looked_at=frozenset()) == _style.TODO_DOT


@pytest.mark.gui
def test_a_looked_at_item_has_neither_the_bullet_nor_the_bold(qtbot) -> None:
    """
    Given  a document whose correction was looked at
    When   the side panel shows it
    Then   its row has no bullet, is drawn plain rather than bold, and the footer counts it
    """
    doc = _document(review=_note(RAW, CORRECTED), work=WorkStatus(looked_at=frozenset({"edit:1"})))

    panel = _panel(qtbot, doc)

    assert panel._delegate.looked_at == frozenset({"edit:1"})
    assert _icon_of(panel._rows[0], looked_at=panel._delegate.looked_at) == ""
    assert _row_font(panel._list.font(), looked_at=True).bold() is False
    assert panel._counts.text() == "1 correction \u00b7 1 of 1 looked at"


@pytest.mark.gui
def test_a_row_not_looked_at_is_drawn_bold(qtbot) -> None:
    """
    Given  a correction nobody looked at yet
    When   its row is drawn
    Then   the words are bold, so a row left to look at stands out from the rows already read
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    assert _row_font(panel._list.font(), looked_at=False).bold() is True
    assert _row_font(panel._list.font(), looked_at=True).bold() is False


@pytest.mark.gui
def test_the_teachers_own_change_never_has_the_bullet(qtbot) -> None:
    """
    Given  a document whose only change is the teacher's own
    When   the side panel shows it
    Then   the row has no bullet, since there is nothing for the teacher to look at
    """
    panel = _panel(qtbot, _document(review=CorrectionReview(), machine=RAW))

    assert panel._rows[0].kind is ItemKind.OWN
    assert _icon_of(panel._rows[0], looked_at=frozenset()) == ""


@pytest.mark.gui
def test_every_minor_change_counts_on_its_own(qtbot) -> None:
    """
    Given  a document with two spelling corrections
    When   the side panel shows it, and one of them is looked at
    Then   the footer counts both, since each is a row of its own
    """
    panel = _panel(qtbot, _minor_document())

    assert panel._counts.text() == "2 corrections \u00b7 0 of 2 looked at"

    panel.set_looked_at(frozenset({"edit:1"}), (1, 2))

    assert panel._counts.text() == "2 corrections \u00b7 1 of 2 looked at"


@pytest.mark.gui
def test_the_teachers_own_changes_are_not_counted(qtbot) -> None:
    """
    Given  a document whose only change is the teacher's own
    When   the side panel shows it
    Then   the footer counts nothing to look at
    """
    panel = _panel(qtbot, _document(review=CorrectionReview(), machine=RAW))

    assert panel._counts.text() == "1 correction"


@pytest.mark.gui
def test_a_reload_keeps_what_was_looked_at(qtbot) -> None:
    """
    Given  a side panel whose item was looked at
    When   the same document is shown again after a stored edit
    Then   the item is still marked as looked at
    """
    doc = _document(review=_note(RAW, CORRECTED))
    panel = _panel(qtbot, doc)
    panel.set_looked_at(frozenset({"edit:1"}), (1, 1))

    panel.set_document(
        _document(review=_note(RAW, CORRECTED), work=WorkStatus(looked_at=frozenset({"edit:1"}))),
        Masking(),
    )

    assert panel._delegate.looked_at == frozenset({"edit:1"})
    assert panel._counts.text() == "1 correction \u00b7 1 of 1 looked at"


@pytest.mark.gui
def test_selecting_a_row_tells_the_page_which_item_it_is(qtbot) -> None:
    """
    Given  a side panel with two rows
    When   the teacher selects the second one
    Then   the panel says which item was chosen
    """
    review = CorrectionReview(
        edits=_note(RAW, CORRECTED).edits,
        unclear=(UnclearNote(4, 9, "bunte", "Farbe unklar.", "bunte Farbe"),),
    )
    panel = _panel(qtbot, _document(review=review))
    chosen: list[str] = []
    panel.item_chosen.connect(chosen.append)

    panel._list.setCurrentRow(1)

    assert chosen == [panel.chosen]
    assert panel.chosen.startswith("unclear:")


@pytest.mark.gui
def test_choosing_an_item_selects_its_row_without_telling(qtbot) -> None:
    """
    Given  a side panel with two rows
    When   the page chooses the second item, as a click on its mark does
    Then   its row is selected and nothing is reported back
    """
    review = CorrectionReview(
        edits=_note(RAW, CORRECTED).edits,
        unclear=(UnclearNote(4, 9, "bunte", "Farbe unklar.", "bunte Farbe"),),
    )
    panel = _panel(qtbot, _document(review=review))
    chosen: list[str] = []
    panel.item_chosen.connect(chosen.append)
    key = panel._rows[1].key

    panel.set_chosen(key)

    assert panel.chosen == key
    assert chosen == []


@pytest.mark.gui
def test_the_chosen_item_stays_chosen_when_the_list_is_rebuilt(qtbot) -> None:
    """
    Given  a side panel whose second row is chosen
    When   the same document is shown again after a stored edit
    Then   the same item is still chosen
    """
    review = CorrectionReview(
        edits=_note(RAW, CORRECTED).edits,
        unclear=(UnclearNote(4, 9, "bunte", "Farbe unklar.", "bunte Farbe"),),
    )
    doc = _document(review=review)
    panel = _panel(qtbot, doc)
    key = panel._rows[1].key
    panel.set_chosen(key)

    panel.set_document(doc, Masking())

    assert panel.chosen == key


@pytest.mark.gui
def test_a_gone_item_leaves_its_neighbour_chosen(qtbot) -> None:
    """
    Given  a side panel whose second row is chosen
    When   the document is shown again with that item gone
    Then   the row at the same place is chosen
    """
    review = CorrectionReview(
        edits=_note(RAW, CORRECTED).edits,
        unclear=(UnclearNote(4, 9, "bunte", "Farbe unklar.", "bunte Farbe"),),
    )
    panel = _panel(qtbot, _document(review=review))
    panel.set_chosen(panel._rows[1].key)

    panel.set_document(_document(review=_note(RAW, CORRECTED)), Masking())

    assert panel.chosen == "edit:1"


# ── The card ──────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_card_of_a_correction_offers_take_out_and_change(qtbot) -> None:
    """
    Given  a document with one explained correction
    When   the side panel shows it
    Then   the card names the words, the category and the explanation, and offers both actions
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    assert "bunte \u2192 braune" in _card_text(panel)
    assert "Word choice" in _card_text(panel)
    assert "Farbe." in _card_text(panel)
    assert _buttons(panel) == [txt.TAKE_OUT]
    assert _replace_field(panel) == "braune"


@pytest.mark.gui
def test_the_card_of_an_unexplained_correction_says_so(qtbot) -> None:
    """
    Given  a proofreading whose review was lost
    When   the side panel shows it
    Then   the card says no explanation was made, and offers the same actions
    """
    panel = _panel(qtbot, _document(review=None))

    assert panel._rows[0].kind is ItemKind.UNEXPLAINED
    assert txt.NO_EXPLANATION_MADE in _card_text(panel)
    assert _buttons(panel) == [txt.TAKE_OUT]
    assert _replace_field(panel) == "braune"


@pytest.mark.gui
def test_the_card_of_the_teachers_own_change_offers_take_out_only(qtbot) -> None:
    """
    Given  a change the review does not know and the machine did not make
    When   the side panel shows it
    Then   the card says it is the teacher's change and offers only Take out
    """
    panel = _panel(qtbot, _document(review=CorrectionReview(), machine=RAW))

    assert panel._rows[0].kind is ItemKind.OWN
    assert txt.YOUR_CHANGE in _card_text(panel)
    assert _buttons(panel) == [txt.TAKE_OUT]
    assert _replace_field(panel) == ""


@pytest.mark.gui
def test_the_card_of_a_changed_correction_says_you_changed_this(qtbot) -> None:
    """
    Given  a correction the teacher changed again
    When   the side panel shows it
    Then   the card says so, offers Take out, and starts the replace field from their words
    """
    review = _note(RAW, "Der dunkelbraune Hund bellt. Die Katze mauzt.")

    panel = _panel(qtbot, _document(review=review))

    assert panel._rows[0].kind is ItemKind.CHANGED
    assert txt.YOUR_CHANGE in _card_text(panel)
    assert _buttons(panel) == [txt.TAKE_OUT]
    assert _replace_field(panel) == "braune"


@pytest.mark.gui
def test_the_card_of_a_taken_out_correction_offers_put_back(qtbot) -> None:
    """
    Given  a correction whose words are no longer changed
    When   the side panel shows it
    Then   the card strikes the model's words, says they are taken out, and offers Put back
    """
    panel = _panel(qtbot, _document(corrected=RAW, review=_note(RAW, CORRECTED)))

    assert panel._rows[0].kind is ItemKind.TAKEN_OUT
    assert "<s>braune</s>" in panel._card._title.text()
    assert txt.TAKEN_OUT in _card_text(panel)
    assert _buttons(panel) == [txt.PUT_BACK]


@pytest.mark.gui
def test_the_card_of_an_unclear_passage_offers_the_suggestion(qtbot) -> None:
    """
    Given  a document with one unclear passage the model only suggests wording for
    When   the side panel shows it
    Then   the card gives the reason, the suggestion with its caption, and Use suggestion
    """
    review = CorrectionReview(
        unclear=(UnclearNote(4, 9, "bunte", "Farbe unklar.", "bunte Farbe", is_suggestion=True),)
    )

    panel = _panel(qtbot, _document(corrected=RAW, review=review))

    assert panel._rows[0].kind is ItemKind.UNCLEAR
    assert "Farbe unklar." in _card_text(panel)
    assert f"{txt.SUGGESTION_PREFIX} bunte Farbe" in _card_text(panel)
    assert txt.MEANING_NOT_CERTAIN in _card_text(panel)
    assert _buttons(panel) == [txt.USE_SUGGESTION]


@pytest.mark.gui
def test_the_card_of_a_question_is_not_offered_as_wording(qtbot) -> None:
    """
    Given  an unclear passage whose recommendation is the question to ask the student
    When   the side panel shows it
    Then   the card still offers Use suggestion: the app cannot tell the two apart
    """
    review = CorrectionReview(
        unclear=(UnclearNote(4, 9, "bunte", "Farbe unklar.", "Welche Farbe?"),)
    )

    panel = _panel(qtbot, _document(corrected=RAW, review=review))

    assert f"{txt.RECOMMENDED_PREFIX} Welche Farbe?" in _card_text(panel)
    assert _buttons(panel) == [txt.USE_SUGGESTION]


# ── The spelling and punctuation changes ──────────────────────────────────


def _minor_document() -> CheckDocument:
    """Return a document with two spelling corrections."""
    raw = "Der bunte Hund bellt. Die Katz mauzt."
    corrected = "Der braune Hund bellt. Die Katze mauzt."
    review = CorrectionReview(
        edits=tuple(
            edit_note(edit, category="spelling", explanation="Schreibweise.")
            for edit in edits(raw, corrected)
        )
    )
    return _document(raw=raw, corrected=corrected, review=review)


def _minor_after_a_correction() -> CheckDocument:
    """Return a document with one word choice and two spelling corrections after it."""
    raw = "Der bunte Hund bellt. Die Katz mauzt. Er lauft weg."
    corrected = "Der braune Hund bellt. Die Katze mauzt. Er läuft weg."
    review = CorrectionReview(
        edits=tuple(
            edit_note(edit, category=category, explanation="Weil.")
            for edit, category in zip(
                edits(raw, corrected), ("word_choice", "spelling", "spelling"), strict=False
            )
        )
    )
    return _document(raw=raw, corrected=corrected, review=review)


@pytest.mark.gui
def test_every_minor_change_is_a_row_of_its_own(qtbot) -> None:
    """
    Given  a document with a word choice and two spelling corrections after it
    When   the side panel shows it
    Then   the list has one row per change, and nothing is hidden
    """
    panel = _panel(qtbot, _minor_after_a_correction())

    assert [row.key for row in panel._rows] == ["edit:1", "edit:2", "edit:3"]
    assert _shown_rows(panel) == ["edit:1", "edit:2", "edit:3"]


@pytest.mark.gui
def test_a_minor_change_offers_take_out_all_beside_its_own_take_out(qtbot) -> None:
    """
    Given  a document with two spelling corrections
    When   the card of one of them is shown
    Then   it offers Take out all, labelled with how many changes it takes out
    """
    panel = _panel(qtbot, _minor_document())

    assert _buttons(panel) == [txt.TAKE_OUT, txt.take_out_all_label(2)]
    assert panel._card.take_out_all.toolTip() == txt.take_out_all_tooltip(2)


@pytest.mark.gui
def test_a_correction_that_is_not_minor_offers_no_take_out_all(qtbot) -> None:
    """
    Given  a document with one word choice
    When   the side panel shows it
    Then   the card offers Take out only: there is no run of small changes to take out
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    assert _buttons(panel) == [txt.TAKE_OUT]


@pytest.mark.gui
def test_take_out_all_counts_and_offers_only_the_corrections_still_standing(qtbot) -> None:
    """
    Given  two spelling corrections, the first of which the teacher took out
    When   the card of the one still standing, and of the one taken out, is shown
    Then   the first counts one change, and the taken-out one offers Put back and no Take out all
    """
    raw = "Der Hunt bellt. Die Katz mauzt."
    corrected = "Der Hunt bellt. Die Katze mauzt."
    review = CorrectionReview(
        edits=tuple(
            edit_note(edit, category="spelling", explanation="Schreibweise.")
            for edit in edits(raw, "Der Hund bellt. Die Katze mauzt.")
        )
    )
    panel = _panel(qtbot, _document(raw=raw, corrected=corrected, review=review))

    panel.set_chosen("edit:1")
    assert _buttons(panel) == [txt.PUT_BACK]

    panel.set_chosen("edit:2")
    assert _buttons(panel) == [txt.TAKE_OUT, txt.take_out_all_label(1)]


@pytest.mark.gui
def test_the_teachers_own_minor_change_offers_no_take_out_all(qtbot) -> None:
    """
    Given  a spelling correction of the model and a comma the teacher added
    When   the card of the comma is shown
    Then   it offers Take out only: Take out all is the model's changes, not theirs
    """
    raw = "Der Hunt bellt. Die Katz mauzt."
    machine = "Der Hund bellt. Die Katz mauzt."
    teacher = "Der Hund bellt. Die Katz, mauzt."
    review = CorrectionReview(
        edits=(edit_note(edits(raw, machine)[0], category="spelling", explanation="Typo."),)
    )
    panel = _panel(qtbot, _document(raw=raw, corrected=teacher, machine=machine, review=review))
    own = next(row for row in panel._rows if row.kind is ItemKind.OWN)
    assert own.is_minor is True

    panel.set_chosen(own.key)

    assert _buttons(panel) == [txt.TAKE_OUT]


@pytest.mark.gui
def test_a_model_change_without_a_category_is_filed_under_other(qtbot) -> None:
    """
    Given  a proofreading whose review was lost, so its change has no category
    When   the side panel shows it and the *Other* box is ticked
    Then   the row has the *Other* badge, the box counts it, and the row stays in the list
    """
    panel = _panel(qtbot, _document(review=None))
    assert panel._rows[0].kind is ItemKind.UNEXPLAINED

    other = panel._filter_boxes["other"]
    assert not other.isHidden()
    assert other.text() == check_txt.filter_label(txt.category_label("other"), 1)
    assert _category_of(panel._rows[0]) == txt.category_label("other")

    panel.set_filter("other")

    assert _shown_rows(panel) == [panel._rows[0].key]


# ── The actions ───────────────────────────────────────────────────────────


@pytest.mark.gui
@pytest.mark.use_case("Correction_Take_Out_And_Put_Back")
def test_the_buttons_ask_for_their_action(qtbot) -> None:
    """
    Given  a side panel with a chosen correction
    When   the teacher presses Take out
    Then   the panel asks for that action on that item
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))
    asked: list[str] = []
    panel.take_out_requested.connect(asked.append)

    panel._card.take_out.click()

    assert asked == ["edit:1"]


@pytest.mark.gui
def test_take_out_all_asks_for_every_minor_change(qtbot) -> None:
    """
    Given  a side panel whose card shows a spelling change
    When   the teacher presses Take out all
    Then   the panel asks for it once, without an item
    """
    panel = _panel(qtbot, _minor_document())
    asked: list[bool] = []
    panel.take_out_all_requested.connect(lambda: asked.append(True))

    panel._card.take_out_all.click()

    assert asked == [True]


@pytest.mark.gui
def test_the_actions_are_refused_with_a_reason(qtbot) -> None:
    """
    Given  a side panel whose texts may not be changed
    When   the teacher looks at the card
    Then   its buttons are disabled and say why
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    panel.set_editable(False, reason=txt.SHOW_NAMES_TO_CHANGE)

    assert not panel._card.take_out.isEnabled()
    assert panel._card.take_out.toolTip() == txt.SHOW_NAMES_TO_CHANGE


@pytest.mark.gui
def test_the_card_hides_the_names(qtbot) -> None:
    """
    Given  a document that mentions a name, and hiding turned on
    When   the side panel shows it
    Then   the card shows the placeholder instead of the name
    """
    raw = "Anna sagt, der bunte Hund bellt."
    corrected = "Anna sagt, der braune Hund bellt."
    review = CorrectionReview(
        edits=(
            edit_note(
                edits(raw, corrected)[0],
                category="word_choice",
                explanation="Anna schreibt die Farbe falsch.",
            ),
        )
    )
    masking = Masking()
    masking.set_available(True)
    masking.set_enabled(True)
    masking.set_result({"[PERSON_1]": "Anna"})

    panel = _panel(qtbot, _document(raw=raw, corrected=corrected, review=review), masking)

    assert "[PERSON_1]" in _card_text(panel)
    assert "Anna" not in _card_text(panel)


# ── The content review ────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_content_review_has_a_tab_of_its_own(qtbot) -> None:
    """
    Given  a document with a content review
    When   the side panel shows it
    Then   the review sits in a second tab beside the corrections, and shows the review
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED), content_review="Ein Bericht."))

    assert panel._tabs.count() == 2
    assert panel._tabs.tabText(0) == check_txt.TAB_CORRECTIONS
    assert panel._tabs.tabText(1) == check_txt.TAB_CONTENT
    assert panel._tabs.isTabVisible(1)

    panel.show_content()

    assert panel._tabs.currentWidget() is panel._content_view
    assert panel._content_view.toPlainText().strip() == "Ein Bericht."


@pytest.mark.gui
def test_a_document_without_a_content_review_has_one_tab(qtbot) -> None:
    """
    Given  a document without a content review
    When   the side panel shows it
    Then   the content tab is not there, so the list has the whole panel
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    assert not panel._tabs.isTabVisible(1)
    assert panel._tabs.currentIndex() == 0


@pytest.mark.gui
def test_asking_for_the_content_of_a_document_without_one_keeps_the_list(qtbot) -> None:
    """
    Given  a document without a content review
    When   the page asks for the content tab, as it does on *What was read*
    Then   the list of corrections stays in front
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    panel.show_content()

    assert panel._tabs.currentIndex() == 0


@pytest.mark.gui
def test_an_empty_panel_says_nothing(qtbot) -> None:
    """
    Given  a side panel that showed a document
    When   it is cleared
    Then   the list, the card and the counts are empty
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    panel.clear()

    assert panel.row_count == 0
    assert panel.chosen == ""
    assert panel._counts.text() == ""
    assert panel._card.isHidden()


@pytest.mark.gui
def test_a_note_stays_in_place_of_the_card_when_the_actions_change(qtbot) -> None:
    """
    Given  a side panel that says the marks could not be placed
    When   the card's actions are allowed or refused
    Then   the note is still what the card says
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))
    panel.set_note(txt.COULD_NOT_MARK)

    panel.set_editable(False, reason=txt.SHOW_NAMES_TO_CHANGE)

    assert panel._card._body.text() == txt.COULD_NOT_MARK


@pytest.mark.gui
def test_a_list_rebuilt_after_an_edit_does_not_report_a_new_choice(qtbot) -> None:
    """
    Given  a side panel whose chosen item is gone after a stored edit
    When   the list is rebuilt and falls back to the nearest item
    Then   the card shows that item but the page is not told, so it does not select words
    """
    review = CorrectionReview(
        edits=_note(RAW, CORRECTED).edits,
        unclear=(UnclearNote(4, 9, "bunte", "Farbe unklar.", "bunte Farbe"),),
    )
    doc = _document(review=review)
    panel = _panel(qtbot, doc)
    panel.set_chosen(panel._rows[1].key)
    chosen: list[str] = []
    panel.item_chosen.connect(chosen.append)

    panel.set_document(_document(), Masking())  # the unclear passage is gone

    assert panel.chosen != ""
    assert chosen == []


# ── Walking the corrections ───────────────────────────────────────────────

WALK_RAW = (
    "Der bunte Hund bellt laut. Die Katz mauzt leise. Er lauft schnell weg. "
    "Wir gehen heim. Das ist fein."
)
WALK_CORRECTED = (
    "Der braune Hund bellt laut. Die Katze mauzt leise. Er läuft schnell weg. "
    "Wir gehen nach Hause. Das ist schön."
)
WALK_MACHINE = (
    "Der braune Hund bellt laut. Die Katze mauzt leise. Er läuft schnell weg. "
    "Wir gehen nach Hause. Das ist fein."
)


def _walk_document(
    *,
    looked_at: frozenset[str] = frozenset(),
    categories: tuple[str, ...] = ("word_choice", "spelling", "spelling", "word_choice"),
) -> CheckDocument:
    """Return a document with four corrections and the teacher's own change."""
    found = edits(WALK_RAW, WALK_CORRECTED)
    review = CorrectionReview(
        edits=tuple(
            edit_note(edit, category=category, explanation="Weil.")
            for edit, category in zip(found, categories, strict=False)
        )
    )
    return _document(
        raw=WALK_RAW,
        corrected=WALK_CORRECTED,
        machine=WALK_MACHINE,
        review=review,
        work=WorkStatus(looked_at=looked_at),
    )


def _walking_panel(qtbot, doc: CheckDocument) -> SidePanel:
    """Return a shown side panel that marks what it shows as looked at, as the window does."""
    panel = SidePanel()
    qtbot.addWidget(panel)
    panel.resize(320, 600)
    panel.show()
    qtbot.wait(20)
    looked_at = set(doc.work.looked_at)

    def _mark(key: str) -> None:
        looked_at.add(key)
        panel.set_looked_at(frozenset(looked_at), (len(looked_at), 4))

    panel.item_shown.connect(_mark)
    panel.set_document(doc, Masking())
    return panel


@pytest.mark.gui
def test_next_walks_every_row_that_counts_once_and_then_says_all_looked_at(qtbot) -> None:
    """
    Given  a document with four corrections and the teacher's own change
    When   *Next* is pressed until it stops
    Then   it visits the four corrections once each, and then reads All looked at
    """
    panel = _walking_panel(qtbot, _walk_document())
    assert [row.key for row in panel._rows] == [
        "edit:1",
        "edit:2",
        "edit:3",
        "edit:4",
        "own:95-99",
    ]
    assert panel.chosen == "edit:1"
    walked = [panel.chosen]

    for _ in range(3):
        panel._next_button.click()
        walked.append(panel.chosen)

    assert walked == ["edit:1", "edit:2", "edit:3", "edit:4"]
    assert panel._next_button.text() == txt.ALL_LOOKED_AT
    assert panel._next_button.isEnabled() is False
    assert panel._counts.text().endswith("4 of 4 looked at")


@pytest.mark.gui
def test_next_stays_in_place_whatever_the_card_says(qtbot) -> None:
    """
    Given  a document whose rows have cards of different heights
    When   *Next* walks from row to row until all are looked at
    Then   the button does not move, so the teacher can keep clicking at the same spot
    """
    panel = _walking_panel(qtbot, _walk_document())
    places = set()

    for _ in range(4):
        qtbot.wait(10)
        geometry = panel._next_button.geometry()
        places.add((geometry.right(), geometry.top()))
        panel._next_button.click()

    assert len(places) == 1


@pytest.mark.gui
def test_a_document_opens_at_the_first_correction_not_looked_at(qtbot) -> None:
    """
    Given  a document whose first two corrections were looked at
    When   the side panel shows it
    Then   the first correction not looked at is chosen, so the teacher continues where they were
    """
    panel = _walking_panel(qtbot, _walk_document(looked_at=frozenset({"edit:1", "edit:2"})))

    assert panel.chosen == "edit:3"


@pytest.mark.gui
def test_a_document_whose_rows_were_all_looked_at_opens_at_the_first(qtbot) -> None:
    """
    Given  a document whose every row that counts was looked at
    When   the side panel shows it
    Then   the first row is chosen and *Next* says there is nothing left
    """
    panel = _walking_panel(
        qtbot, _walk_document(looked_at=frozenset({"edit:1", "edit:2", "edit:3", "edit:4"}))
    )

    assert panel.chosen == "edit:1"
    assert panel._next_button.text() == txt.ALL_LOOKED_AT


@pytest.mark.gui
def test_a_stored_edit_does_not_move_the_choice(qtbot) -> None:
    """
    Given  a side panel whose third row is chosen
    When   the same document comes back after a stored edit
    Then   the choice stays, and *Next* walks on from there
    """
    doc = _walk_document()
    panel = _walking_panel(qtbot, doc)
    panel.set_chosen("edit:3")

    panel.set_document(doc, Masking())

    assert panel.chosen == "edit:3"
    panel._next_button.click()
    assert panel.chosen == "edit:4"


@pytest.mark.gui
def test_the_card_of_a_stretch_the_ai_left_out_says_so_and_offers_no_wording(qtbot) -> None:
    """
    Given  a document with one stretch the model left out of its answer
    When   the side panel shows it, with an unclear passage beside it
    Then   the list has a row for it, the card names the stretch and says why it is listed, there
           is no suggestion to use but Change leads to the words, and the footer counts it apart
    """
    review = CorrectionReview(
        unclear=(
            UnclearNote(
                0,
                17,
                "Der bunte Hund bellt.",
                "Die KI hat diese Stelle ausgelassen.",
                "",
                left_out=True,
            ),
            UnclearNote(22, 27, "Katze", "Welche Katze?", "die Katze"),
        )
    )

    panel = _panel(qtbot, _document(corrected=RAW, review=review))

    kinds = [row.kind for row in panel._rows]
    assert kinds == [ItemKind.LEFT_OUT, ItemKind.UNCLEAR]
    panel.set_chosen(panel._rows[0].key)
    assert "Die KI hat diese Stelle ausgelassen." in _card_text(panel)
    assert _buttons(panel) == []
    assert _replace_field(panel) == ""
    assert panel._counts.text() == "1 unclear · 1 left out by the AI · 0 of 2 looked at"


# ── The card keeps its place ──────────────────────────────────────────────


@pytest.mark.gui
def test_the_card_keeps_its_height_whatever_it_says(qtbot) -> None:
    """
    Given  a document whose rows have cards of different lengths
    When   *Next* walks from row to row
    Then   the card and its buttons stay where they are, so nothing moves under the pointer
    """
    panel = _walking_panel(qtbot, _walk_document())
    places = set()

    for _ in range(4):
        qtbot.wait(10)
        places.add((panel._card.height(), panel._card._button_row.geometry().top()))
        panel._next_button.click()

    assert len(places) == 1


@pytest.mark.gui
def test_a_long_explanation_scrolls_inside_the_card(qtbot) -> None:
    """
    Given  a correction whose explanation is longer than the card shows
    When   the side panel shows it
    Then   the text is taller than the card and scrolls, instead of making the card taller
    """
    review = CorrectionReview(
        edits=(
            edit_note(
                edits(RAW, CORRECTED)[0],
                category="word_choice",
                explanation="Ein langer Satz. " * 40,
            ),
        )
    )

    panel = _panel(qtbot, _document(review=review))
    qtbot.wait(20)

    assert panel._card._scroll.widget().height() > panel._card._scroll.height()
    assert panel._card._scroll.verticalScrollBar().maximum() > 0


# ── The sentence around the change ────────────────────────────────────────


@pytest.mark.gui
def test_the_card_shows_the_sentence_around_the_change(qtbot) -> None:
    """
    Given  a correction in the first sentence of a two-sentence text
    When   the side panel shows it
    Then   the card quotes that sentence and highlights the student's words in it
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    context = panel._card._context.text()

    assert context.startswith("Der <span")
    assert ">bunte</span>" in context
    assert context.endswith("Hund bellt.")


@pytest.mark.gui
def test_the_sentence_around_the_change_hides_the_names(qtbot) -> None:
    """
    Given  a sentence that mentions a name, and hiding turned on
    When   the side panel shows the correction in it
    Then   the context line shows the placeholder instead of the name
    """
    raw = "Anna sagt, der bunte Hund bellt."
    corrected = "Anna sagt, der braune Hund bellt."
    review = CorrectionReview(
        edits=(edit_note(edits(raw, corrected)[0], category="word_choice", explanation="Farbe."),)
    )
    masking = Masking()
    masking.set_available(True)
    masking.set_enabled(True)
    masking.set_result({"[PERSON_1]": "Anna"})

    panel = _panel(qtbot, _document(raw=raw, corrected=corrected, review=review), masking)

    assert "[PERSON_1] sagt" in panel._card._context.text()
    assert "Anna" not in panel._card._context.text()


@pytest.mark.gui
def test_a_row_whose_words_are_not_in_the_text_has_no_context_line(qtbot) -> None:
    """
    Given  an unclear passage the model quoted but the text does not contain
    When   the side panel shows it
    Then   the card has no context line, because there is no sentence to quote
    """
    review = CorrectionReview(
        unclear=(UnclearNote(500, 505, "bunte", "Farbe unklar.", "bunte Farbe"),)
    )

    panel = _panel(qtbot, _document(corrected=RAW, review=review))

    assert panel._card._context.isHidden()


# ── The category and the notes ────────────────────────────────────────────


@pytest.mark.gui
def test_the_card_shows_the_category_as_a_neutral_chip(qtbot) -> None:
    """
    Given  an explained correction of a known category
    When   the side panel shows it
    Then   the card names the category in the grey every chip shares, not in a colour of its own
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))
    body = panel._card._body.text()

    assert f"background-color:{_style.CHIP_BACKGROUND}" in body
    assert f"color:{_style.CHIP_TEXT}" in body
    assert "Word choice" in body


@pytest.mark.gui
def test_the_notes_of_a_change_are_tags(qtbot) -> None:
    """
    Given  a correction the model is not sure about and that is only a matter of style
    When   the side panel shows it
    Then   both notes are grey tags, and the explanation stays plain text
    """
    review = CorrectionReview(
        edits=(
            edit_note(
                edits(RAW, CORRECTED)[0],
                category="word_choice",
                explanation="Farbe.",
                optional=True,
                needed=False,
            ),
        )
    )

    panel = _panel(qtbot, _document(review=review))
    body = panel._card._body.text()

    assert f"background-color:{_style.CHIP_BACKGROUND}" in body
    assert txt.OPTIONAL_CHANGE in body
    assert txt.AI_NOT_SURE in body
    assert "Farbe." in body
    assert "<i>Farbe.</i>" not in body


# ── The filter boxes ──────────────────────────────────────────────────────


def _shown_rows(panel: SidePanel) -> list[str]:
    """The keys of the rows the list shows, in list order."""
    return [
        row.key for index, row in enumerate(panel._rows) if not panel._list.item(index).isHidden()
    ]


@pytest.mark.gui
def test_the_filter_row_has_a_box_per_category_with_its_count(qtbot) -> None:
    """
    Given  a document with two word choices and two spelling changes
    When   the side panel shows it
    Then   every box says how many rows it would show, and a category the text lacks is gone
    """
    panel = _panel(qtbot, _walk_document())

    labels = {key: box.text() for key, box in panel._filter_boxes.items() if not box.isHidden()}

    assert labels[FILTER_UNSEEN] == check_txt.filter_label(check_txt.FILTER_NOT_LOOKED_AT, 4)
    assert labels["word_choice"] == check_txt.filter_label("Word choice", 2)
    assert labels["spelling"] == check_txt.filter_label("Spelling", 2)
    assert "grammar" not in labels
    assert FILTER_UNCLEAR not in labels
    assert FILTER_LEFT_OUT not in labels


@pytest.mark.gui
def test_a_ticked_category_shows_only_that_category(qtbot) -> None:
    """
    Given  a document with two word choices and two spelling changes
    When   the word choice box is ticked
    Then   only the word choice rows are in the list
    """
    panel = _panel(qtbot, _walk_document())

    panel.set_filter("word_choice")

    assert _shown_rows(panel) == ["edit:1", "edit:4"]


@pytest.mark.gui
def test_two_ticked_categories_combine(qtbot) -> None:
    """
    Given  a document with two word choices and two spelling changes
    When   the word choice and the spelling boxes are both ticked
    Then   the list shows the rows of both, and nothing else
    """
    panel = _panel(qtbot, _walk_document())

    panel.set_filter("word_choice")
    panel.set_filter("spelling")

    assert _shown_rows(panel) == ["edit:1", "edit:2", "edit:3", "edit:4"]


@pytest.mark.gui
def test_the_unclear_box_shows_the_unclear_passages(qtbot) -> None:
    """
    Given  a document with one correction and one unclear passage
    When   the Unclear box is ticked
    Then   only the unclear passage is in the list
    """
    review = CorrectionReview(
        edits=_note(RAW, CORRECTED).edits,
        unclear=(UnclearNote(4, 9, "bunte", "Farbe unklar.", "bunte Farbe"),),
    )
    panel = _panel(qtbot, _document(review=review))
    assert not panel._filter_boxes[FILTER_UNCLEAR].isHidden()

    panel.set_filter(FILTER_UNCLEAR)

    assert _shown_rows(panel) == ["unclear:1"]


@pytest.mark.gui
def test_the_not_looked_at_box_shows_what_is_left(qtbot) -> None:
    """
    Given  a document whose first correction was looked at
    When   the *Not looked at* box is ticked
    Then   only the rows that count and were not looked at are in the list
    """
    panel = _panel(qtbot, _walk_document(looked_at=frozenset({"edit:1"})))

    panel.set_filter(FILTER_UNSEEN)

    assert _shown_rows(panel) == ["edit:2", "edit:3", "edit:4"]


@pytest.mark.gui
def test_the_not_looked_at_box_narrows_the_ticked_categories(qtbot) -> None:
    """
    Given  a document whose first word choice was looked at
    When   the word choice box and *Not looked at* are both ticked
    Then   only the word choice that is left is in the list
    """
    panel = _panel(qtbot, _walk_document(looked_at=frozenset({"edit:1"})))

    panel.set_filter("word_choice")
    panel.set_filter(FILTER_UNSEEN)

    assert _shown_rows(panel) == ["edit:4"]


@pytest.mark.gui
def test_the_not_looked_at_box_drops_a_row_once_it_is_looked_at(qtbot) -> None:
    """
    Given  a list narrowed to what is left
    When   the teacher looks at the row they are on and moves on
    Then   the row they left is gone, and the one they are on stays under their pointer
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_filter(FILTER_UNSEEN)

    panel.set_looked_at(frozenset({"edit:1"}), (1, 4))
    assert _shown_rows(panel) == ["edit:1", "edit:2", "edit:3", "edit:4"]

    panel.set_chosen("edit:2")
    panel.set_looked_at(frozenset({"edit:1", "edit:2"}), (2, 4))

    assert _shown_rows(panel) == ["edit:2", "edit:3", "edit:4"]


@pytest.mark.gui
def test_unticking_every_box_shows_every_row_again(qtbot) -> None:
    """
    Given  a list narrowed to one category
    When   that box is unticked again
    Then   every row is back, since nothing ticked shows everything
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_filter("word_choice")

    panel.set_filter("word_choice", False)

    assert _shown_rows(panel) == ["edit:1", "edit:2", "edit:3", "edit:4", "own:95-99"]
    assert panel.ticked == frozenset()


@pytest.mark.gui
def test_next_stays_inside_the_ticked_categories(qtbot) -> None:
    """
    Given  a list narrowed to the word choices
    When   *Next* is pressed
    Then   it goes to the next word choice, not to a row the boxes hide
    """
    panel = _walking_panel(qtbot, _walk_document())
    panel.set_filter("word_choice")
    walked = [panel.chosen]

    panel._next_button.click()
    walked.append(panel.chosen)

    assert walked == ["edit:1", "edit:4"]


@pytest.mark.gui
def test_next_says_all_looked_at_when_the_ticked_category_is_done(qtbot) -> None:
    """
    Given  a list narrowed to the spelling changes, both looked at
    When   the side panel shows it
    Then   *Next* says there is nothing left, although other rows are not looked at
    """
    panel = _walking_panel(qtbot, _walk_document(looked_at=frozenset({"edit:2", "edit:3"})))

    panel.set_filter("spelling")

    assert not panel._next_button.isEnabled()
    assert panel._next_button.text() == txt.ALL_LOOKED_AT


@pytest.mark.gui
def test_the_counts_cover_the_whole_document_with_a_filter_on(qtbot) -> None:
    """
    Given  a list narrowed to one category
    When   the side panel shows it
    Then   the counts above the list still count every row of the document
    """
    panel = _walking_panel(qtbot, _walk_document())

    panel.set_filter("word_choice")

    assert panel._counts.text().endswith("of 4 looked at")


@pytest.mark.gui
def test_a_filter_that_hides_the_chosen_row_chooses_the_first_it_shows(qtbot) -> None:
    """
    Given  a list whose chosen row is a spelling change
    When   the word choice box is ticked
    Then   the first word choice is chosen, so the card never shows a hidden row
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_chosen("edit:2")

    panel.set_filter("word_choice")

    assert panel.chosen == "edit:1"


@pytest.mark.gui
def test_a_new_document_opens_with_every_row_shown(qtbot) -> None:
    """
    Given  a list narrowed to one category and to what is left
    When   another document is opened
    Then   no box is ticked, so nothing of the new document is hidden unseen
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_filter("word_choice")
    panel.set_filter(FILTER_UNSEEN)

    panel.set_document(_document(source_id="/tmp/Ben.png"), Masking())

    assert panel.ticked == frozenset()
    assert panel.unseen_only is False
    assert not panel._filter_boxes["word_choice"].isChecked()


@pytest.mark.gui
def test_the_filter_boxes_wrap_when_the_panel_is_narrow(qtbot) -> None:
    """
    Given  a document with four categories, in a panel as narrow as it goes
    When   the filter row is laid out
    Then   the boxes wrap onto more than one line instead of running out of the panel
    """
    panel = _panel(
        qtbot, _walk_document(categories=("word_choice", "spelling", "grammar", "punctuation"))
    )

    panel.resize(150, 600)
    qtbot.wait(20)

    tops = {box.geometry().top() for box in panel._filter_boxes.values() if not box.isHidden()}

    assert len(tops) > 1


# ── How a row is drawn ────────────────────────────────────────────────────


def _row_image(panel: SidePanel, index: int) -> QImage:
    """The pixels of the row at *index*."""
    rect = panel._list.visualItemRect(panel._list.item(index))
    return panel._list.viewport().grab(rect).toImage()


def _has_colour(image: QImage, colour: QColor) -> bool:
    """Whether *image* has a pixel of exactly *colour*."""
    return any(
        image.pixel(x, y) == colour.rgb()
        for x in range(image.width())
        for y in range(image.height())
    )


def _has_colour_like(image: QImage, colour: QColor, *, tolerance: int = 24) -> bool:
    """Whether *image* has a pixel close to *colour*.

    The words of a row are thin strokes that antialiasing blends with the background, so a row of
    words holds few pixels of the exact colour; a badge or a bar fills whole pixels.
    """
    wanted = (colour.red(), colour.green(), colour.blue())

    def close(pixel: int) -> bool:
        found = ((pixel >> 16) & 0xFF, (pixel >> 8) & 0xFF, pixel & 0xFF)
        return all(
            abs(value - want) <= tolerance for value, want in zip(found, wanted, strict=True)
        )

    return any(
        close(image.pixel(x, y)) for x in range(image.width()) for y in range(image.height())
    )


def _luminance(colour: QColor) -> float:
    """The relative luminance of *colour*, as WCAG defines it."""

    def channel(value: int) -> float:
        share = value / 255
        return share / 12.92 if share <= 0.03928 else ((share + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(colour.red())
        + 0.7152 * channel(colour.green())
        + 0.0722 * channel(colour.blue())
    )


def _contrast(first: QColor, second: QColor) -> float:
    """The contrast ratio between two colours, from 1 (none) to 21 (black on white)."""
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.fixture
def dark_theme():
    """Give the session the theme the app runs with, and take it away again."""
    app = QApplication.instance()
    before = app.styleSheet()
    app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api="pyside6"))
    yield
    app.setStyleSheet(before)


@pytest.mark.gui
def test_a_looked_at_row_stays_readable(qtbot, dark_theme) -> None:
    """
    Given  a correction the teacher has looked at, on the theme the app runs with
    When   the list draws its row
    Then   its words are the theme's secondary grey, and that grey reads on the list
    """
    panel = _panel(qtbot, _walk_document(looked_at=frozenset({"edit:1"})))
    palette = panel._list.palette()
    colour = _style.row_text(palette, looked_at=True, selected=False)

    assert _has_colour_like(_row_image(panel, 0), colour)
    assert _contrast(colour, palette.color(QPalette.ColorRole.Base)) >= 4.5


@pytest.mark.gui
def test_the_chosen_row_is_drawn_in_the_selection_colours(qtbot, dark_theme) -> None:
    """
    Given  a correction the teacher has looked at
    When   its row is the chosen one
    Then   its words are the theme's selection text, which reads on the selection background
    """
    panel = _panel(qtbot, _walk_document(looked_at=frozenset({"edit:1"})))
    palette = panel._list.palette()
    colour = _style.row_text(palette, looked_at=True, selected=True)
    panel._list.setCurrentRow(0)
    qtbot.wait(10)

    assert _has_colour_like(_row_image(panel, 0), colour)
    assert _contrast(colour, palette.color(QPalette.ColorRole.Highlight)) >= 4.5


def _ink_runs(image: QImage, background: QColor, *, after: int) -> list[tuple[int, int]]:
    """The runs of columns of *image* right of *after* that hold a pixel unlike *background*."""
    columns = [
        x
        for x in range(after, image.width())
        if any(image.pixel(x, y) != background.rgb() for y in range(image.height()))
    ]
    runs: list[tuple[int, int]] = []
    for x in columns:
        if runs and x == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], x)
        else:
            runs.append((x, x))
    return runs


@pytest.mark.gui
def test_a_row_has_the_accent_of_its_kind_at_the_left_edge(qtbot, dark_theme) -> None:
    """
    Given  a correction and an unclear passage
    When   the list draws their rows
    Then   the bar at the left edge of each is the accent of its kind
    """
    panel = _panel(qtbot, _walk_document(categories=("word_choice",) * 4))
    green = QColor(_style.MARK_ACCENTS[ItemKind.CORRECTION])

    assert _has_colour(_row_image(panel, 1), green)


@pytest.mark.gui
def test_the_accent_bar_stays_on_the_chosen_row(qtbot, dark_theme) -> None:
    """
    Given  a correction, which is the chosen row
    When   the list draws it on the selection background
    Then   the bar at its left edge is still the accent of its kind
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))
    image = _row_image(panel, 0)
    accent = QColor(_style.MARK_ACCENTS[ItemKind.CORRECTION])

    assert panel._list.currentRow() == 0
    assert image.pixelColor(0, image.height() // 2) == accent


@pytest.mark.gui
def test_a_taken_out_correction_says_taken_out_rather_than_its_category(qtbot) -> None:
    """
    Given  a word choice correction the teacher took out
    When   the list draws its row
    Then   the badge says *taken out*, so it is not read as a category still standing
    """
    panel = _panel(qtbot, _document(corrected=RAW, review=_note(RAW, CORRECTED)))

    assert panel._rows[0].kind is ItemKind.TAKEN_OUT
    assert panel._rows[0].category == "word_choice"
    assert _category_of(panel._rows[0]) == txt.TAKEN_OUT


def _one_row_with_a_bullet_and_one_without() -> CheckDocument:
    """Return a document whose two rows hold the same words, one the teacher's own change."""
    raw = "Der bunte Hund bellt. Der bunte Hund bellt."
    corrected = "Der braune Hund bellt. Der braune Hund bellt."
    review = CorrectionReview(
        edits=(edit_note(edits(raw, corrected)[0], category="word_choice", explanation="Farbe."),)
    )
    return _document(raw=raw, corrected=corrected, machine=raw, review=review)


@pytest.mark.gui
def test_the_words_of_every_row_start_at_the_same_x(qtbot, dark_theme) -> None:
    """
    Given  a row with the bullet and one without, holding the same words
    When   the list draws both
    Then   the words start at the same x, so a bullet never shifts the words of a row
    """
    panel = _panel(qtbot, _one_row_with_a_bullet_and_one_without())
    panel._list.setCurrentRow(-1)
    qtbot.wait(10)
    bullet, own = _row_image(panel, 0), _row_image(panel, 1)
    assert [row.kind for row in panel._rows] == [ItemKind.CORRECTION, ItemKind.OWN]

    def words_start(image: QImage, *, has_bullet: bool) -> int:
        """The x of the first letter of the words; the bullet, where there is one, is not a word."""
        background = image.pixelColor(_BAR_WIDTH + 1, 0)
        runs = _ink_runs(image, background, after=_BAR_WIDTH + 1)
        return runs[1][0] if has_bullet else runs[0][0]

    assert words_start(bullet, has_bullet=True) == words_start(own, has_bullet=False)


@pytest.mark.gui
def test_the_badge_keeps_its_colour_on_a_looked_at_row(qtbot, dark_theme) -> None:
    """
    Given  a correction of a known category
    When   the list draws its row before and after the teacher looked at it
    Then   the badge is the same grey in both, so the category still reads
    """
    panel = _panel(qtbot, _walk_document())
    badge = QColor(_style.CHIP_BACKGROUND)
    assert _has_colour(_row_image(panel, 0), badge)

    panel.set_looked_at(frozenset({"edit:1"}), (1, 3))
    qtbot.wait(10)

    assert _has_colour(_row_image(panel, 0), badge)


@pytest.mark.gui
def test_a_grammar_row_and_a_spelling_row_draw_the_same_badge(qtbot) -> None:
    """
    Given  a grammar change and a spelling change
    When   the list draws their rows
    Then   both badges are the same grey, so a badge never says a colour's meaning
    """
    panel = _panel(qtbot, _walk_document(categories=("grammar", "spelling", "spelling", "grammar")))

    assert _has_colour(_row_image(panel, 0), QColor(_style.CHIP_BACKGROUND))
    assert _has_colour(_row_image(panel, 1), QColor(_style.CHIP_BACKGROUND))


@pytest.mark.gui
def test_a_row_without_a_category_says_what_kind_it_is(qtbot) -> None:
    """
    Given  a document with an unclear passage and a stretch the model left out
    When   the list draws their rows
    Then   each row names its kind in the grey badge every row shares
    """
    review = CorrectionReview(
        unclear=(
            UnclearNote(4, 9, "bunte", "Farbe unklar.", "bunte Farbe"),
            UnclearNote(22, 27, "Katze", "Die KI hat diese Stelle ausgelassen.", "", left_out=True),
        )
    )
    panel = _panel(qtbot, _document(corrected=RAW, review=review))
    assert [row.kind for row in panel._rows] == [ItemKind.UNCLEAR, ItemKind.LEFT_OUT]

    assert [_category_of(row) for row in panel._rows] == [
        check_txt.BADGE_UNCLEAR,
        check_txt.BADGE_LEFT_OUT,
    ]
    assert _has_colour(_row_image(panel, 0), QColor(_style.CHIP_BACKGROUND))
    assert _has_colour(_row_image(panel, 1), QColor(_style.CHIP_BACKGROUND))


@pytest.mark.gui
def test_a_correction_still_shows_its_category(qtbot) -> None:
    """
    Given  a correction of a known category
    When   the list draws its row
    Then   the badge names the category, not the kind of thing it is
    """
    panel = _panel(qtbot, _walk_document())

    assert _category_of(panel._rows[0]) == txt.category_label("word_choice")


def test_the_two_word_columns_are_the_same_width() -> None:
    """
    Given  the width of a row's words and of the arrow between them
    When   the columns are laid out
    Then   the student's words and the corrected ones get the same width, so they line up
    """
    before, arrow, after = _column_rects(QRect(0, 0, 200, 24), 20)

    assert before.width() == after.width() == 90
    assert before.right() < arrow.left()
    assert arrow.right() < after.left()


@pytest.mark.gui
def test_a_stored_edit_keeps_the_chosen_row_under_the_not_looked_at_filter(qtbot) -> None:
    """
    Given  a list narrowed to what is left, with a row chosen that was looked at
    When   the same document comes back after a stored edit
    Then   the chosen row is still in the list, so the card and the list agree
    """
    doc = _walk_document()
    panel = _walking_panel(qtbot, doc)
    panel.set_filter(FILTER_UNSEEN)
    panel.set_chosen("edit:4")
    panel.set_looked_at(frozenset({"edit:4"}), (1, 3))

    panel.set_document(_walk_document(looked_at=frozenset({"edit:4"})), Masking())

    assert panel.chosen == "edit:4"
    assert "edit:4" in _shown_rows(panel)
    assert panel.unseen_only is True


@pytest.mark.gui
def test_a_category_that_is_gone_after_an_edit_unticks_every_box(qtbot) -> None:
    """
    Given  a list narrowed to the spelling changes
    When   the same document comes back with no spelling change left
    Then   no box is ticked, so the list is not empty behind a box that is gone
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_filter("spelling")

    panel.set_document(_walk_document(categories=("word_choice",) * 4), Masking())

    assert panel.ticked == frozenset()
    assert not panel._filter_boxes["spelling"].isChecked()
    assert _shown_rows(panel)


@pytest.mark.gui
def test_choosing_a_row_the_filter_hides_keeps_it_shown(qtbot) -> None:
    """
    Given  a list narrowed to the spelling changes
    When   the page chooses a word choice, as a click on its mark does
    Then   that row is in the list and is the chosen one
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_filter("spelling")

    panel.set_chosen("edit:1")

    assert panel.chosen == "edit:1"
    assert "edit:1" in _shown_rows(panel)


@pytest.mark.gui
def test_a_category_the_chips_lack_is_filed_under_other(qtbot) -> None:
    """
    Given  a correction whose category is none of the known ones
    When   the side panel shows it
    Then   the *Other* chip counts it and shows it
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED, category="typo")))

    other = panel._filter_boxes["other"]
    assert not other.isHidden()
    assert other.text() == check_txt.filter_label(txt.category_label("other"), 1)
    panel.set_filter("other")
    assert _shown_rows(panel) == ["edit:1"]


# ── The card's room ───────────────────────────────────────────────────────


def _two_items_of_different_length() -> CheckDocument:
    """Return a document with a short correction and one with a long explanation."""
    raw = "Der bunte Hund bellt. Die Katz mauzt."
    corrected = "Der braune Hund bellt. Die Katze mauzt."
    first, second = edits(raw, corrected)
    review = CorrectionReview(
        edits=(
            edit_note(first, category="word_choice", explanation="Farbe."),
            edit_note(
                second, category="grammar", explanation="Der Satz braucht ein Subjekt. " * 20
            ),
        )
    )
    return _document(raw=raw, corrected=corrected, review=review)


@pytest.mark.gui
def test_the_buttons_stay_where_they_are_while_stepping_through_items(qtbot) -> None:
    """
    Given  a document with a short correction and one with a long explanation
    When   the teacher steps from the first to the second
    Then   the card's buttons and *Next* are where they were
    """
    panel = _panel(qtbot, _two_items_of_different_length())
    qtbot.wait(20)
    buttons = panel._card._button_row.mapTo(panel, QPoint(0, 0)).y()
    next_button = panel._next_button.mapTo(panel, QPoint(0, 0)).y()

    panel.set_chosen("edit:2")
    qtbot.wait(20)

    assert panel.chosen == "edit:2"
    assert panel._card._button_row.mapTo(panel, QPoint(0, 0)).y() == buttons
    assert panel._next_button.mapTo(panel, QPoint(0, 0)).y() == next_button


@pytest.mark.gui
def test_the_card_opens_with_room_for_five_lines(qtbot) -> None:
    """
    Given  a document with a correction
    When   the side panel shows it
    Then   the card is tall enough for five lines of text, not only for its buttons
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))
    qtbot.wait(20)

    assert panel._card.height() >= panel._card._default_height()


@pytest.mark.gui
def test_the_card_puts_change_and_why_side_by_side_when_it_is_wide(qtbot) -> None:
    """
    Given  a side panel as narrow as the Check tab opens it
    When   the teacher drags it wide enough for both columns
    Then   *Change* and *Why* stand side by side instead of one above the other
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))
    assert panel._card._columns.direction() == QBoxLayout.Direction.TopToBottom

    panel.resize(700, 600)
    qtbot.wait(20)

    assert panel._card._columns.direction() == QBoxLayout.Direction.LeftToRight


@pytest.mark.gui
def test_the_card_keeps_the_height_the_teacher_dragged_it_to(qtbot) -> None:
    """
    Given  a card the teacher made taller
    When   another document is shown
    Then   the card is still that tall
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))
    qtbot.wait(20)
    before = panel._card.height()
    panel.set_card_split((before + 100, max(panel._split.height() - before - 100, 1)))
    qtbot.wait(20)
    taller = panel._card.height()
    assert taller > before

    panel.set_document(_document(review=_note(RAW, CORRECTED), source_id="/tmp/Ben.png"), Masking())
    qtbot.wait(20)

    assert panel._card.height() == taller


@pytest.mark.gui
def test_dragging_the_card_tells_the_window_its_height(qtbot) -> None:
    """
    Given  a side panel that shows a document
    When   the teacher drags the handle under the card
    Then   the panel says how tall the card and the list are now
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))
    qtbot.wait(20)  # the card has no height before the document is laid out
    told: list[tuple[int, ...]] = []
    panel.card_split_changed.connect(told.append)

    panel._split.moveSplitter(panel._card.height() + 40, 1)

    assert told == [panel.card_split]
