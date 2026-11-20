"""GUI tests for the Check tab's side panel: the card of the chosen item and the list."""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage

from teachers_teammate.application.check_document import CheckDocument, check_document_of
from teachers_teammate.application.check_items import ItemKind
from teachers_teammate.domain.proofreading.edits import edits
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    UnclearNote,
    edit_note,
)
from teachers_teammate.gui import _user_text as txt
from teachers_teammate.gui.check import _style
from teachers_teammate.gui.check._masking import Masking
from teachers_teammate.domain.teacher_work import WorkStatus
from teachers_teammate.gui.check._side_panel import (
    FILTER_ALL,
    FILTER_UNSEEN,
    GROUP_KEY,
    SidePanel,
    _column_rects,
    _icon_of,
    rows_of,
)
from teachers_teammate.infrastructure.state_repository import DocumentStateView
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
            cache_status_label="",
            loaded_at="",
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
def test_a_looked_at_item_has_a_filled_icon(qtbot) -> None:
    """
    Given  a document whose correction was looked at
    When   the side panel shows it
    Then   its row has the filled icon and the footer counts it
    """
    doc = _document(review=_note(RAW, CORRECTED), work=WorkStatus(looked_at=frozenset({"edit:1"})))

    panel = _panel(qtbot, doc)

    assert panel._delegate.looked_at == frozenset({"edit:1"})
    assert _icon_of(panel._rows[0], expanded=False, looked_at=panel._delegate.looked_at) == "\u25cf"
    assert panel._counts.text() == "1 correction \u00b7 1 of 1 looked at"


@pytest.mark.gui
def test_an_item_not_looked_at_has_an_open_icon(qtbot) -> None:
    """
    Given  a document nothing was looked at yet
    When   the side panel shows it
    Then   its row has the open icon
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    assert _icon_of(panel._rows[0], expanded=False, looked_at=frozenset()) == "\u25cb"


@pytest.mark.gui
def test_the_folded_minor_changes_count_once(qtbot) -> None:
    """
    Given  a document with two spelling corrections
    When   the side panel shows it, and the group row is looked at
    Then   the footer counts the group as one thing, not one per correction
    """
    panel = _panel(qtbot, _minor_document())

    assert panel._counts.text() == "2 corrections \u00b7 0 of 1 looked at"

    panel.set_looked_at(frozenset({GROUP_KEY}), (1, 1))

    assert panel._counts.text() == "2 corrections \u00b7 1 of 1 looked at"


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
    Given  a change the review does not know and the machine did not make
    When   the side panel shows it
    Then   the card says it is the teacher's change and offers only Take out
    """
    panel = _panel(qtbot, _document(review=CorrectionReview(), machine=RAW))

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
def test_the_minor_changes_are_one_row_that_opens(qtbot) -> None:
    """
    Given  a document with a word choice and two spelling corrections after it
    When   the side panel shows it, and the group row is chosen
    Then   one row folds the spelling changes, its items are hidden until it is chosen, and then
           they show
    """
    doc = _minor_after_a_correction()
    panel = _panel(qtbot, doc)
    rows = rows_of(doc.items)

    assert [row.key for row in rows] == ["edit:1", GROUP_KEY, "edit:2", "edit:3"]
    assert panel._list.item(2).isHidden() and panel._list.item(3).isHidden()

    panel._list.setCurrentRow(1)

    assert not panel._list.item(2).isHidden() and not panel._list.item(3).isHidden()
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
    panel.set_chosen(panel._rows[1].item.key)
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
    """Return a document with two corrections, a folded group and the teacher's own change."""
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
        panel.set_looked_at(frozenset(looked_at), (len(looked_at), 3))

    panel.item_shown.connect(_mark)
    panel.set_document(doc, Masking())
    return panel


@pytest.mark.gui
def test_next_walks_every_row_that_counts_once_and_then_says_all_looked_at(qtbot) -> None:
    """
    Given  a document with two corrections, a folded group of two and the teacher's own change
    When   *Next* is pressed until it stops
    Then   it visits the two corrections and the group once each, and then reads All looked at
    """
    panel = _walking_panel(qtbot, _walk_document())
    assert [row.key for row in panel._rows] == [
        "edit:1",
        GROUP_KEY,
        "edit:2",
        "edit:3",
        "edit:4",
        "own:95-99",
    ]
    assert panel.chosen_row == "edit:1"
    walked = [panel.chosen_row]

    for _ in range(2):
        panel._next_button.click()
        walked.append(panel.chosen_row)

    assert walked == ["edit:1", GROUP_KEY, "edit:4"]
    assert panel._next_button.text() == txt.ALL_LOOKED_AT
    assert panel._next_button.isEnabled() is False
    assert panel._counts.text().endswith("3 of 3 looked at")


@pytest.mark.gui
def test_next_stays_in_place_whatever_the_card_says(qtbot) -> None:
    """
    Given  a document whose rows have cards of different heights (a correction, a group)
    When   *Next* walks from row to row until all are looked at
    Then   the button does not move, so the teacher can keep clicking at the same spot
    """
    panel = _walking_panel(qtbot, _walk_document())
    places = set()

    for _ in range(3):
        qtbot.wait(10)
        geometry = panel._next_button.geometry()
        places.add((geometry.right(), geometry.top()))
        panel._next_button.click()

    assert len(places) == 1


@pytest.mark.gui
def test_a_document_opens_at_the_first_correction_not_looked_at(qtbot) -> None:
    """
    Given  a document whose first correction and group row were looked at
    When   the side panel shows it
    Then   the first correction not looked at is chosen, so the teacher continues where they were
    """
    panel = _walking_panel(qtbot, _walk_document(looked_at=frozenset({"edit:1", GROUP_KEY})))

    assert panel.chosen == "edit:4"


@pytest.mark.gui
def test_a_document_whose_rows_were_all_looked_at_opens_at_the_first(qtbot) -> None:
    """
    Given  a document whose every row that counts was looked at
    When   the side panel shows it
    Then   the first row is chosen and *Next* says there is nothing left
    """
    panel = _walking_panel(
        qtbot, _walk_document(looked_at=frozenset({"edit:1", GROUP_KEY, "edit:4"}))
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
    panel.set_chosen("edit:4")

    panel.set_document(doc, Masking())

    assert panel.chosen == "edit:4"
    panel._next_button.click()
    assert panel.chosen_row == GROUP_KEY


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

    kinds = [row.item.kind for row in panel._rows]
    assert kinds == [ItemKind.LEFT_OUT, ItemKind.UNCLEAR]
    panel.set_chosen(panel._rows[0].key)
    assert "Die KI hat diese Stelle ausgelassen." in _card_text(panel)
    assert _buttons(panel) == [txt.CHANGE]
    assert panel._counts.text() == "1 unclear · 1 left out by the AI · 0 of 2 looked at"


# ── The card keeps its place ──────────────────────────────────────────────


@pytest.mark.gui
def test_the_card_keeps_its_height_whatever_it_says(qtbot) -> None:
    """
    Given  a document whose rows have cards of different lengths (a correction, a group)
    When   *Next* walks from row to row
    Then   the card and its buttons stay where they are, so nothing moves under the pointer
    """
    panel = _walking_panel(qtbot, _walk_document())
    places = set()

    for _ in range(3):
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
def test_a_row_without_words_in_the_text_has_no_context_line(qtbot) -> None:
    """
    Given  the folded row of the spelling and punctuation changes
    When   the side panel shows it
    Then   the card has no context line, because there is no one sentence to quote
    """
    panel = _panel(qtbot, _minor_document())
    panel.set_chosen(GROUP_KEY)

    assert panel._card._context.isHidden()


# ── The category and the notes ────────────────────────────────────────────


@pytest.mark.gui
def test_the_card_shows_the_category_as_a_coloured_chip(qtbot) -> None:
    """
    Given  an explained correction of a known category
    When   the side panel shows it
    Then   the card shows the category in the colour the list uses for it
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED)))

    background, foreground = _style.category_colours("word_choice")
    body = panel._card._body.text()

    assert f"background-color:{background}" in body
    assert f"color:{foreground}" in body
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

    assert f"background-color:{_style.TAG_BACKGROUND}" in body
    assert txt.OPTIONAL_CHANGE in body
    assert txt.AI_NOT_SURE in body
    assert "Farbe." in body
    assert "<i>Farbe.</i>" not in body


# ── The filter row ────────────────────────────────────────────────────────


def _shown_rows(panel: SidePanel) -> list[str]:
    """The keys of the rows the list shows, in list order."""
    return [
        row.key for index, row in enumerate(panel._rows) if not panel._list.item(index).isHidden()
    ]


@pytest.mark.gui
def test_the_filter_row_has_a_chip_per_category_with_its_count(qtbot) -> None:
    """
    Given  a document with two word choices and two spelling changes
    When   the side panel shows it
    Then   every chip says how many rows it would show, and a category the text lacks is gone
    """
    panel = _panel(qtbot, _walk_document())

    labels = {
        key: button.text() for key, button in panel._filter_buttons.items() if not button.isHidden()
    }

    assert labels[FILTER_ALL] == check_txt.FILTER_ALL
    assert labels[FILTER_UNSEEN] == check_txt.filter_label(check_txt.FILTER_NOT_LOOKED_AT, 3)
    assert labels["word_choice"] == check_txt.filter_label("Word choice", 2)
    assert labels["spelling"] == check_txt.filter_label("Spelling", 2)
    assert "grammar" not in labels


@pytest.mark.gui
def test_a_category_filter_shows_only_that_category(qtbot) -> None:
    """
    Given  a document with two word choices and two spelling changes
    When   the word choice chip is chosen
    Then   only the word choice rows are in the list
    """
    panel = _panel(qtbot, _walk_document())

    panel.set_filter("word_choice")

    assert _shown_rows(panel) == ["edit:1", "edit:4"]


@pytest.mark.gui
def test_a_category_filter_shows_the_folded_changes_one_by_one(qtbot) -> None:
    """
    Given  a document whose spelling changes are folded into one row
    When   the spelling chip is chosen
    Then   the group row gives way to its two items, since the list is short already
    """
    panel = _panel(qtbot, _walk_document())

    panel.set_filter("spelling")

    assert _shown_rows(panel) == ["edit:2", "edit:3"]


@pytest.mark.gui
def test_the_not_looked_at_filter_shows_what_is_left(qtbot) -> None:
    """
    Given  a document whose first correction was looked at
    When   the *Not looked at* chip is chosen
    Then   only the rows that count and were not looked at are in the list
    """
    panel = _panel(qtbot, _walk_document(looked_at=frozenset({"edit:1"})))

    panel.set_filter(FILTER_UNSEEN)

    assert _shown_rows(panel) == [GROUP_KEY, "edit:4"]


@pytest.mark.gui
def test_the_not_looked_at_filter_drops_a_row_once_it_is_looked_at(qtbot) -> None:
    """
    Given  a list narrowed to what is left
    When   the teacher looks at the row they are on and moves on
    Then   the row they left is gone, and the one they are on stays under their pointer
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_filter(FILTER_UNSEEN)

    panel.set_looked_at(frozenset({"edit:1"}), (1, 3))
    assert _shown_rows(panel) == ["edit:1", GROUP_KEY, "edit:4"]

    panel.set_chosen(GROUP_KEY)
    panel.set_looked_at(frozenset({"edit:1", GROUP_KEY}), (2, 3))

    assert _shown_rows(panel) == [GROUP_KEY, "edit:4"]


@pytest.mark.gui
def test_choosing_all_shows_every_row_again(qtbot) -> None:
    """
    Given  a list narrowed to one category
    When   the *All* chip is chosen
    Then   every row is back, the folded ones still folded
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_filter("word_choice")

    panel.set_filter(FILTER_ALL)

    assert _shown_rows(panel) == ["edit:1", GROUP_KEY, "edit:4", "own:95-99"]


@pytest.mark.gui
def test_next_stays_inside_the_filter(qtbot) -> None:
    """
    Given  a list narrowed to the word choices
    When   *Next* is pressed
    Then   it goes to the next word choice, not to a row the filter hides
    """
    panel = _walking_panel(qtbot, _walk_document())
    panel.set_filter("word_choice")
    walked = [panel.chosen_row]

    panel._next_button.click()
    walked.append(panel.chosen_row)

    assert walked == ["edit:1", "edit:4"]


@pytest.mark.gui
def test_the_counts_cover_the_whole_document_with_a_filter_on(qtbot) -> None:
    """
    Given  a list narrowed to one category
    When   the side panel shows it
    Then   the counts above the list still count every row of the document
    """
    panel = _walking_panel(qtbot, _walk_document())

    panel.set_filter("word_choice")

    assert panel._counts.text().endswith("of 3 looked at")


@pytest.mark.gui
def test_a_filter_that_hides_the_chosen_row_chooses_the_first_it_shows(qtbot) -> None:
    """
    Given  a list whose chosen row is a spelling change
    When   the word choice chip is chosen
    Then   the first word choice is chosen, so the card never shows a hidden row
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_chosen("edit:2")

    panel.set_filter("word_choice")

    assert panel.chosen == "edit:1"


@pytest.mark.gui
def test_a_new_document_opens_with_every_row_shown(qtbot) -> None:
    """
    Given  a list narrowed to one category
    When   another document is opened
    Then   the filter is back to *All*, so nothing of the new document is hidden unseen
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_filter("word_choice")

    panel.set_document(_document(source_id="/tmp/Ben.png"), Masking())

    assert panel.filter == FILTER_ALL
    assert panel._filter_buttons[FILTER_ALL].isChecked()


@pytest.mark.gui
def test_the_filter_chips_wrap_when_the_panel_is_narrow(qtbot) -> None:
    """
    Given  a panel narrower than its chips side by side
    When   the filter row is laid out
    Then   the chips wrap onto more than one line instead of running out of the panel
    """
    panel = _panel(qtbot, _walk_document())

    panel.resize(150, 600)
    qtbot.wait(20)

    tops = {
        button.geometry().top()
        for button in panel._filter_buttons.values()
        if not button.isHidden()
    }

    assert len(tops) > 1


# ── How a row is drawn ────────────────────────────────────────────────────


def _row_image(panel: SidePanel, index: int) -> QImage:
    """The pixels of the row at *index*."""
    rect = panel._list.visualItemRect(panel._list.item(index))
    return panel._list.viewport().grab(rect).toImage()


def _darkest(image: QImage) -> int:
    """The lightness of the darkest pixel of *image*."""
    return min(
        QColor(image.pixel(x, y)).lightness()
        for x in range(image.width())
        for y in range(image.height())
    )


def _has_colour(image: QImage, colour: QColor) -> bool:
    """Whether *image* has a pixel of exactly *colour*."""
    return any(
        image.pixel(x, y) == colour.rgb()
        for x in range(image.width())
        for y in range(image.height())
    )


@pytest.mark.gui
def test_a_row_the_teacher_looked_at_is_drawn_dimmed(qtbot) -> None:
    """
    Given  a correction the teacher has not looked at
    When   it is marked as looked at
    Then   its words are drawn faded, so the open rows stand out without being hidden
    """
    panel = _panel(qtbot, _walk_document())
    plain = _darkest(_row_image(panel, 0))

    panel.set_looked_at(frozenset({"edit:1"}), (1, 3))
    qtbot.wait(10)

    assert _darkest(_row_image(panel, 0)) > plain


@pytest.mark.gui
def test_a_row_shows_its_category_as_a_coloured_badge(qtbot) -> None:
    """
    Given  a correction of a known category
    When   the list draws its row
    Then   the category is a badge in the colour the card's chip uses
    """
    panel = _panel(qtbot, _walk_document())
    background, _ = _style.category_colours("word_choice")

    assert _has_colour(_row_image(panel, 0), QColor(background))


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
def test_next_walks_the_folded_changes_under_their_category_filter(qtbot) -> None:
    """
    Given  a document whose two spelling changes are folded into one row
    When   the spelling chip is chosen and *Next* is pressed
    Then   it is on, and walks the two changes one by one, then says all looked at
    """
    panel = _walking_panel(qtbot, _walk_document())

    panel.set_filter("spelling")
    walked = [panel.chosen_row]
    assert panel._next_button.isEnabled()
    panel._next_button.click()
    walked.append(panel.chosen_row)

    assert walked == ["edit:2", "edit:3"]
    assert not panel._next_button.isEnabled()
    assert panel._next_button.text() == txt.ALL_LOOKED_AT


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

    assert panel.chosen_row == "edit:4"
    assert "edit:4" in _shown_rows(panel)
    assert panel.filter == FILTER_UNSEEN


@pytest.mark.gui
def test_a_category_that_is_gone_after_an_edit_gives_the_filter_back_to_all(qtbot) -> None:
    """
    Given  a list narrowed to the spelling changes
    When   the same document comes back with no spelling change left
    Then   the filter is *All*, so the list is not empty behind a chip that is gone
    """
    panel = _panel(qtbot, _walk_document())
    panel.set_filter("spelling")

    panel.set_document(_walk_document(categories=("word_choice",) * 4), Masking())

    assert panel.filter == FILTER_ALL
    assert panel._filter_buttons[FILTER_ALL].isChecked()
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

    assert panel.chosen_row == "edit:1"
    assert "edit:1" in _shown_rows(panel)


@pytest.mark.gui
def test_a_category_the_chips_lack_is_filed_under_other(qtbot) -> None:
    """
    Given  a correction whose category is none of the known ones
    When   the side panel shows it
    Then   the *Other* chip counts it and shows it
    """
    panel = _panel(qtbot, _document(review=_note(RAW, CORRECTED, category="typo")))

    other = panel._filter_buttons["other"]
    assert not other.isHidden()
    assert other.text() == check_txt.filter_label(txt.category_label("other"), 1)
    panel.set_filter("other")
    assert _shown_rows(panel) == ["edit:1"]
