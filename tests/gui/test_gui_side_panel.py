"""GUI tests for the Check tab's side panel: the card of the chosen item and the list."""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

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
from teachers_teammate.gui.check._side_panel import GROUP_KEY, SidePanel, rows_of
from teachers_teammate.infrastructure.state_repository import DocumentStateView

RAW = "Der bunte Hund bellt. Die Katze mauzt."
CORRECTED = "Der braune Hund bellt. Die Katze mauzt."


def _note(raw: str, corrected: str, *, category: str = "word_choice") -> CorrectionReview:
    """Return a review that explains the first edit from *raw* to *corrected*."""
    return CorrectionReview(
        edits=(edit_note(edits(raw, corrected)[0], category=category, explanation="Farbe."),)
    )


def _document(
    *,
    raw: str = RAW,
    corrected: str = CORRECTED,
    review: CorrectionReview | None = None,
    warning: str = "",
    content_review: str = "",
) -> CheckDocument:
    """Return the Check tab's data for a stored document."""
    return check_document_of(
        DocumentStateView(
            source_id="/tmp/Anna.png",
            name="Anna.png",
            preview_img="",
            raw_text=raw,
            correction_text=corrected,
            evaluation_text=content_review,
            ocr_done=True,
            correction_done=True,
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
    assert panel._counts.text() == "1 correction"


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

    assert panel._counts.text() == "1 correction · 1 unclear"


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
    key = panel._rows[1].item.key

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
    key = panel._rows[1].item.key
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
    panel.set_chosen(panel._rows[1].item.key)

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
    assert _buttons(panel) == [txt.TAKE_OUT, txt.CHANGE]


@pytest.mark.gui
def test_the_card_of_an_unexplained_correction_says_so(qtbot) -> None:
    """
    Given  a proofreading whose review was lost
    When   the side panel shows it
    Then   the card says no explanation was made, and offers the same actions
    """
    panel = _panel(qtbot, _document(review=None))

    assert panel._rows[0].item.kind is ItemKind.UNEXPLAINED
    assert txt.NO_EXPLANATION_MADE in _card_text(panel)
    assert _buttons(panel) == [txt.TAKE_OUT, txt.CHANGE]


@pytest.mark.gui
def test_the_card_of_the_teachers_own_change_offers_take_out_only(qtbot) -> None:
    """
    Given  a change the review does not know, with a complete review
    When   the side panel shows it
    Then   the card says it is the teacher's change and offers only Take out
    """
    panel = _panel(qtbot, _document(review=CorrectionReview()))

    assert panel._rows[0].item.kind is ItemKind.OWN
    assert txt.YOUR_CHANGE in _card_text(panel)
    assert _buttons(panel) == [txt.TAKE_OUT]


@pytest.mark.gui
def test_the_card_of_a_changed_correction_says_you_changed_this(qtbot) -> None:
    """
    Given  a correction the teacher changed again
    When   the side panel shows it
    Then   the card says so and offers Take out and Change
    """
    review = _note(RAW, "Der dunkelbraune Hund bellt. Die Katze mauzt.")

    panel = _panel(qtbot, _document(review=review))

    assert panel._rows[0].item.kind is ItemKind.CHANGED
    assert txt.YOUR_CHANGE in _card_text(panel)
    assert _buttons(panel) == [txt.TAKE_OUT, txt.CHANGE]


@pytest.mark.gui
def test_the_card_of_a_taken_out_correction_offers_put_back(qtbot) -> None:
    """
    Given  a correction whose words are no longer changed
    When   the side panel shows it
    Then   the card strikes the model's words, says they are taken out, and offers Put back
    """
    panel = _panel(qtbot, _document(corrected=RAW, review=_note(RAW, CORRECTED)))

    assert panel._rows[0].item.kind is ItemKind.TAKEN_OUT
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

    assert panel._rows[0].item.kind is ItemKind.UNCLEAR
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


# ── The folded spelling and punctuation changes ───────────────────────────


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


@pytest.mark.gui
def test_the_minor_changes_are_one_row_that_opens(qtbot) -> None:
    """
    Given  a document with two spelling corrections
    When   the side panel shows it, and the group row is chosen
    Then   one row folds them, its items are hidden until it is chosen, and then they show
    """
    doc = _minor_document()
    panel = _panel(qtbot, doc)
    rows = rows_of(doc.items)

    assert [row.key for row in rows] == [GROUP_KEY, "edit:1", "edit:2"]
    assert panel._list.item(1).isHidden() and panel._list.item(2).isHidden()

    panel._list.setCurrentRow(0)

    assert not panel._list.item(1).isHidden() and not panel._list.item(2).isHidden()
    assert panel.chosen == ""


@pytest.mark.gui
def test_the_group_row_offers_take_out_all(qtbot) -> None:
    """
    Given  a document with two spelling corrections
    When   the group row is chosen
    Then   the card counts them and offers Take out all
    """
    panel = _panel(qtbot, _minor_document())

    panel._list.setCurrentRow(0)

    assert txt.minor_group_label(2) in _card_text(panel)
    assert _buttons(panel) == [txt.TAKE_OUT_ALL]


@pytest.mark.gui
def test_one_minor_change_is_not_folded(qtbot) -> None:
    """
    Given  a document with a single spelling correction
    When   the side panel shows it
    Then   it is a row of its own: a group row over one correction is only noise
    """
    raw = "Der bunte Hund bellt."
    corrected = "Der braune Hund bellt."
    review = CorrectionReview(
        edits=(edit_note(edits(raw, corrected)[0], category="spelling", explanation="."),)
    )

    panel = _panel(qtbot, _document(raw=raw, corrected=corrected, review=review))

    assert [row.key for row in panel._rows] == ["edit:1"]


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
def test_take_out_all_asks_for_the_group(qtbot) -> None:
    """
    Given  a side panel with the group row chosen
    When   the teacher presses Take out all
    Then   the panel asks for it once, without an item
    """
    panel = _panel(qtbot, _minor_document())
    panel._list.setCurrentRow(0)
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
def test_the_content_review_is_a_section_of_its_own(qtbot) -> None:
    """
    Given  a document with a content review
    When   the side panel shows it
    Then   the section is there, closed, and opens on a click
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED), content_review="Ein Bericht."))

    assert not panel._content_section.isHidden()
    assert panel._content_view.isHidden()

    panel._content_toggle.click()

    assert not panel._content_view.isHidden()
    assert panel._content_view.toPlainText().strip() == "Ein Bericht."


@pytest.mark.gui
def test_a_document_without_a_content_review_has_no_section(qtbot) -> None:
    """
    Given  a document without a content review
    When   the side panel shows it
    Then   the section is not there at all
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    assert panel._content_section.isHidden()


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
    panel.set_chosen(panel._rows[1].item.key)
    chosen: list[str] = []
    panel.item_chosen.connect(chosen.append)

    panel.set_document(_document(), Masking())  # the unclear passage is gone

    assert panel.chosen != ""
    assert chosen == []
