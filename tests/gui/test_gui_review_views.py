"""GUI tests for the proofreading review: annotated diff, notes pane and its wiring.

The widgets only render what the application layer aligned, so every test builds a
:class:`~teachers_teammate.domain.correction_review.CorrectionReview` and lets the panel
align it against the two texts.
"""

# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name

from __future__ import annotations

from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QPixmap, QTextCharFormat

from teachers_teammate.application.correction_review import CorrectionReviewPresenter
from teachers_teammate.domain.correction_review import (
    ChangeNote,
    CorrectionReview,
    SentenceNote,
    UnclearNote,
)
from teachers_teammate.gui import _user_text as txt
from teachers_teammate.gui._preview_panel import _NARROW_PANEL_WIDTH, PreviewPanel
from teachers_teammate.gui._types import StageStatus
from teachers_teammate.gui._review_views import (
    FILTER_ALL,
    FILTER_CHANGES,
    FILTER_UNCLEAR,
    _FOCUS_CHANGE_BACKGROUND,
    _FOCUS_TEXT,
    _FOCUS_UNCLEAR_BACKGROUND,
    _NOTE_FOCUS_STYLE,
    _UNCLEAR_BACKGROUND,
    _UNCLEAR_TEXT,
    ReviewDiffView,
    ReviewNotesView,
)

RAW = (
    "Yesterday she have gone to the cinema with his friend. "
    "It was very nice because they was there for the first time."
)
CORRECTED = (
    "Yesterday she has gone to the cinema with her friend. "
    "It was very nice because they were there for the first time."
)

REVIEW = CorrectionReview(
    sentences=(
        SentenceNote(
            original="Yesterday she have gone to the cinema with his friend.",
            corrected="Yesterday she has gone to the cinema with her friend.",
            changes=(
                ChangeNote(
                    "have",
                    "has",
                    "grammar",
                    '"she" needs the 3rd-person form.',
                ),
                ChangeNote("his", "her", "word_choice", '"she" refers to a woman.'),
            ),
        ),
        SentenceNote(
            original="because they was there for the first time",
            corrected="because they were there for the first time",
            changes=(ChangeNote("was", "were", "grammar", '"they" is plural.'),),
        ),
    ),
    unclear=(
        UnclearNote(
            expression="It was very nice",
            why='"It" can mean the film or the visit.',
            recommendation="The film was very nice",
            is_suggestion=True,
        ),
        UnclearNote(
            expression="not in the text at all",
            why="No wording matches.",
            recommendation="Something else",
        ),
    ),
)


def _pixmap() -> QPixmap:
    pix = QPixmap(20, 20)
    pix.fill()
    return pix


def _panel(qtbot, **load_kwargs) -> PreviewPanel:
    """Return a shown panel loaded with :data:`RAW`/:data:`CORRECTED`."""
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.load(_pixmap(), RAW, CORRECTED, "", **load_kwargs)
    return panel


# ── annotated diff ────────────────────────────────────────────────────────


@pytest.mark.gui
def test_review_diff_numbers_changes_and_marks_unclear_spans(qtbot) -> None:
    """
    Given  a review with three changes and one unclear expression found in the text
    When   the panel loads the file
    Then   every change links to its note, the change tabs count three changes and two
           unclear expressions, and the unclear span is highlighted and links to its own note
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    html_text = panel._review_diff._html()

    assert "href='note:1'" in html_text
    assert "href='note:2'" in html_text
    assert "href='note:3'" in html_text
    assert "href='unclear:1'" in html_text
    assert "class='unclear'" in html_text
    assert panel._review_notes._changes_btn.text() == txt.filter_changes_label(3)
    assert panel._review_notes._unclear_btn.text() == txt.filter_unclear_label(2)
    assert panel._text_tabs.tabText(panel._text_tabs.indexOf(panel._changes_tab)) == (
        txt.changes_tab_title(panel._aligned)
    )


@pytest.mark.gui
def test_review_diff_links_the_changed_and_unclear_words_themselves(qtbot) -> None:
    """
    Given  a review with changes and an unclear expression found in the text
    When   the diff is rendered
    Then   the changed words and the unclear words are the links, with no footnote numbers
           or ``?`` markers next to them
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    html_text = panel._review_diff._html()

    assert "<a name='note:1' href='note:1' style='background:" in html_text
    assert "<a href='note:1' style='background:#d7ffd7;color:#1e8449;'>has</a>" in html_text
    assert "<a href='unclear:1' class='unclear'" in html_text
    assert ">t was very nice</a>" in html_text
    assert "<sup>" not in html_text


@pytest.mark.gui
def test_linked_words_keep_the_diff_colours_without_underline(qtbot) -> None:
    """
    Given  a loaded review
    When   Qt lays out the diff
    Then   the linked words carry the diff's own colours, the old words stay struck through,
           and no link is underlined
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    # Qt splits a link into several fragments (its named first character is one of them).
    linked: list[tuple[str, str, QTextCharFormat]] = []
    block = panel._review_diff.document().begin()
    while block.isValid():
        fragments = block.begin()
        while not fragments.atEnd():
            fragment = fragments.fragment()
            fmt = fragment.charFormat()
            if fmt.isAnchor() and fmt.anchorHref():
                linked.append((fmt.anchorHref(), fragment.text(), fmt))
            fragments += 1
        block = block.next()

    def formats_of(href: str, text: str) -> list[QTextCharFormat]:
        assert "".join(t for h, t, _f in linked if h == href).count(text) == 1, linked
        return [fmt for h, _t, fmt in linked if h == href]

    assert linked
    assert not any(fmt.fontUnderline() for _h, _t, fmt in linked)
    old_and_new = formats_of("note:1", "havehas")
    assert old_and_new[0].fontStrikeOut()
    assert old_and_new[-1].foreground().color().name() == "#1e8449"
    for fmt in formats_of("unclear:1", "It was very nice"):
        assert fmt.foreground().color().name() == _UNCLEAR_TEXT
        assert fmt.background().color().name() == _UNCLEAR_BACKGROUND


@pytest.mark.gui
def test_highlights_set_a_dark_text_colour(qtbot) -> None:
    """
    Given  the app's dark theme, whose default text colour is light
    When   an unclear expression and its note are focused
    Then   both highlights set a dark text colour so the words stay readable
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    panel._review_diff.focus_unclear(1)
    diff = panel._review_diff._html()
    notes = panel._review_notes._html(("unclear", 1))
    selections = panel._review_diff.extraSelections()

    assert f"background:{_UNCLEAR_BACKGROUND};color:{_UNCLEAR_TEXT};" in diff
    assert selections
    assert all(sel.format.foreground().color().name() == _FOCUS_TEXT for sel in selections)
    assert _NOTE_FOCUS_STYLE in notes


@pytest.mark.gui
def test_review_notes_lists_explanations_and_recommendations(qtbot) -> None:
    """
    Given  a loaded review
    When   the notes pane is read
    Then   it names every change with its category and explanation, and shows the unclear
           expression with a suggestion labelled as such
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    notes = panel._review_notes.browser().toPlainText()

    assert "have → has" in notes
    assert txt.CHANGE_CATEGORY_LABELS["grammar"] in notes
    assert '"she" needs the 3rd-person form.' in notes
    assert "his → her" in notes
    assert "It was very nice" in notes
    assert txt.SUGGESTION_PREFIX in notes
    assert txt.MEANING_NOT_CERTAIN in notes


@pytest.mark.gui
def test_clicking_a_change_number_focuses_its_note(qtbot) -> None:
    """
    Given  a loaded review with the notes pane on *All*
    When   the teacher clicks the words of change 2 in the diff
    Then   the notes pane highlights that note and keeps the *All* filter
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    panel._review_diff.anchorClicked.emit(QUrl("note:2"))

    notes = panel._review_notes
    assert notes.current_filter() == FILTER_ALL
    assert notes._last_key is not None and notes._last_key[2] == ("note", 2)
    assert "<div class='note focused'" in notes._html(("note", 2))


@pytest.mark.gui
def test_clicking_a_note_scrolls_the_diff_to_its_change(qtbot) -> None:
    """
    Given  a loaded review
    When   the teacher clicks the note of unclear expression 1 in the notes pane
    Then   the diff view focuses that span so the two views stay in step
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    panel._review_notes.browser().anchorClicked.emit(QUrl("unclear:1"))

    assert panel._review_diff._focus_unclear == 1
    assert panel._review_diff._focus_change is None


def _selected_words(view: ReviewDiffView) -> str:
    """Return the text the diff's extra selections cover."""
    return "".join(sel.cursor.selectedText() for sel in view.extraSelections())


@pytest.mark.gui
def test_focusing_a_change_highlights_its_words_without_rendering_again(qtbot) -> None:
    """
    Given  a diff showing changes without focus
    When   change 1 is focused
    Then   the words of the change (old and new) carry a strong highlight as an extra
           selection, the page is not rendered again, and focusing another change moves it
    """
    panel = _panel(qtbot, correction_review=REVIEW)
    diff = panel._review_diff
    assert diff.extraSelections() == []
    html_before = diff.toHtml()

    diff.focus_change(1)

    assert _selected_words(diff) == "havehas"
    assert all(
        sel.format.background().color().name() == _FOCUS_CHANGE_BACKGROUND
        for sel in diff.extraSelections()
    )
    assert diff.toHtml() == html_before

    diff.focus_change(3)
    assert _selected_words(diff) == "waswere"


@pytest.mark.gui
def test_focusing_an_unclear_expression_uses_its_own_colour(qtbot) -> None:
    """
    Given  a diff with an unclear expression
    When   it is focused
    Then   its words are highlighted in the unclear colour, not the change colour
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    panel._review_diff.focus_unclear(1)

    assert _selected_words(panel._review_diff) == "It was very nice"
    assert panel._review_diff.extraSelections()[0].format.background().color().name() == (
        _FOCUS_UNCLEAR_BACKGROUND
    )


@pytest.mark.gui
def test_a_new_content_clears_the_highlight(qtbot) -> None:
    """
    Given  a diff with a highlighted change
    When   new content is set
    Then   no stale highlight is left
    """
    panel = _panel(qtbot, correction_review=REVIEW)
    panel._review_diff.focus_change(1)

    panel._review_diff.set_content(RAW, CORRECTED, panel._aligned)

    assert panel._review_diff.extraSelections() == []


def _link_position(view, href: str) -> QPoint:
    """Return a point in *view* that a real mouse click would hit as the link *href*."""
    viewport = view.viewport()
    for y in range(0, viewport.height(), 2):
        for x in range(0, viewport.width(), 2):
            if view.anchorAt(QPoint(x, y)) == href:
                return QPoint(x, y)
    raise AssertionError(f"no visible link {href}")


@pytest.mark.gui
def test_a_real_click_on_a_list_number_marks_the_note_and_the_text(qtbot) -> None:
    """
    Given  a loaded review with the notes pane on *All*
    When   the teacher really clicks the number of change 2 in the list (a mouse click, not a
           faked signal)
    Then   the clicked entry gets the focus background in the list, and the words of the change
           are highlighted in the text
    """
    panel = _panel(qtbot, correction_review=REVIEW)
    panel._text_tabs.setCurrentWidget(panel._changes_tab)
    browser = panel._review_notes.browser()

    qtbot.mouseClick(
        browser.viewport(), Qt.MouseButton.LeftButton, pos=_link_position(browser, "note:2")
    )

    assert panel._review_notes._last_key[2] == ("note", 2)  # ty: ignore[not-subscriptable]
    assert "#fff4c2" in browser.toHtml()  # the focus background of the clicked entry
    assert _selected_words(panel._review_diff) == "hisher"


@pytest.mark.gui
def test_a_real_click_on_changed_words_marks_the_note(qtbot) -> None:
    """
    Given  a loaded review
    When   the teacher really clicks the changed words in the text
    Then   the note of that change is marked in the list and the words are highlighted
    """
    panel = _panel(qtbot, correction_review=REVIEW)
    panel._text_tabs.setCurrentWidget(panel._changes_tab)
    diff = panel._review_diff

    qtbot.mouseClick(diff.viewport(), Qt.MouseButton.LeftButton, pos=_link_position(diff, "note:3"))

    assert panel._review_notes._last_key[2] == ("note", 3)  # ty: ignore[not-subscriptable]
    assert _selected_words(diff) == "waswere"


@pytest.mark.gui
def test_filter_buttons_switch_between_changes_and_unclear(qtbot) -> None:
    """
    Given  a loaded review
    When   the *Changes* filter is chosen, then *Unclear*, then *All*
    Then   only the matching notes are listed, and *All* lists both again
    """
    panel = _panel(qtbot, correction_review=REVIEW)
    notes = panel._review_notes

    notes.set_filter(FILTER_CHANGES)
    changes_only = notes.browser().toPlainText()
    assert "have → has" in changes_only
    assert "It was very nice" not in changes_only

    notes.set_filter(FILTER_UNCLEAR)
    unclear_only = notes.browser().toPlainText()
    assert "It was very nice" in unclear_only
    assert "have → has" not in unclear_only

    notes.set_filter(FILTER_ALL)
    both = notes.browser().toPlainText()
    assert "have → has" in both
    assert "It was very nice" in both


@pytest.mark.gui
def test_unanchored_unclear_expression_is_still_listed(qtbot) -> None:
    """
    Given  a review whose second unclear expression does not occur in the text
    When   the *Unclear* filter is shown
    Then   both expressions are listed although only the first is marked in the diff
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    panel._review_notes.set_filter(FILTER_UNCLEAR)

    unclear = panel._review_notes.browser().toPlainText()
    assert "It was very nice" in unclear
    assert "not in the text at all" in unclear
    assert "unclear:2" not in panel._review_diff._html()


# ── teacher edits ─────────────────────────────────────────────────────────


@pytest.mark.gui
def test_reverting_a_change_moves_its_note_to_not_in_the_text(qtbot) -> None:
    """
    Given  a loaded review
    When   the teacher restores the original wording in *Proofread Text*
    Then   the diff shows no change, and the explanations are reported as not in the text
           instead of claiming an edit that is not there
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    panel._correction_text.setPlainText(RAW)
    panel.flush_pending_changes()

    diff = panel._review_diff._html()
    assert "href='note:1'" not in diff
    notes = panel._review_notes.browser().toPlainText()
    assert txt.ORPHAN_HEADING in notes
    assert txt.NOT_IN_TEXT in notes


@pytest.mark.gui
def test_teachers_own_edit_is_shown_as_your_change(qtbot) -> None:
    """
    Given  a loaded review
    When   the teacher replaces a word the model left untouched
    Then   the new hunk appears as the teacher's own change, without an explanation
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    panel._correction_text.setPlainText(CORRECTED.replace("very nice", "really nice"))
    panel.flush_pending_changes()

    notes = panel._review_notes.browser().toPlainText()
    assert txt.YOUR_CHANGE in notes


@pytest.mark.gui
def test_typing_realigns_the_review_once_after_a_pause(qtbot) -> None:
    """
    Given  a loaded review and a teacher typing several edits in a row
    When   the edits arrive faster than the typing pause
    Then   the review is aligned once, after the pause, not once per edit
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    with patch.object(
        CorrectionReviewPresenter, "aligned_plain", wraps=CorrectionReviewPresenter.aligned_plain
    ) as aligned:
        for edit in ("a", "ab", "abc"):
            panel._correction_text.setPlainText(CORRECTED + edit)
        assert aligned.call_count == 0
        qtbot.waitUntil(lambda: aligned.call_count == 1, timeout=2000)

    assert aligned.call_count == 1


# ── without a review ──────────────────────────────────────────────────────


@pytest.mark.gui
def test_without_review_only_the_plain_diff_is_shown(qtbot) -> None:
    """
    Given  a file proofread in *Corrections only* mode
    When   the panel loads it without a review
    Then   the diff has no note numbers and the notes pane is hidden
    """
    panel = _panel(qtbot)

    diff = panel._review_diff._html()
    assert "href='note:1'" not in diff
    assert ">has<" in diff
    assert panel._review_notes.isHidden()
    assert panel._text_tabs.tabText(panel._text_tabs.indexOf(panel._changes_tab)) == (
        txt.CHANGES_TAB
    )


@pytest.mark.gui
def test_fallback_warning_is_shown_as_a_banner(qtbot) -> None:
    """
    Given  a file whose explanations had to be dropped
    When   the panel loads it with a correction warning
    Then   the warning is shown above the plain diff and disappears with the next file
    """
    panel = _panel(qtbot, correction_warning="Explanations could not be produced.")

    assert not panel._review_banner.isHidden()
    assert "could not be produced" in panel._review_banner.text()

    panel.load(_pixmap(), RAW, CORRECTED, "")

    assert panel._review_banner.isHidden()


# ── layout and zoom ───────────────────────────────────────────────────────


@pytest.mark.gui
def test_splitter_turns_vertical_on_a_narrow_panel(qtbot) -> None:
    """
    Given  a panel showing the changes tab
    When   the panel is made narrower than the switch width and then wide again
    Then   the notes pane moves below the diff and returns to its side afterwards
    """
    panel = _panel(qtbot, correction_review=REVIEW)

    panel.resize(_NARROW_PANEL_WIDTH + 200, 600)
    qtbot.waitExposed(panel)
    assert panel._review_splitter.orientation() == Qt.Orientation.Horizontal

    panel.resize(_NARROW_PANEL_WIDTH - 200, 600)
    qtbot.waitExposed(panel)
    assert panel._review_splitter.orientation() == Qt.Orientation.Vertical

    panel.resize(_NARROW_PANEL_WIDTH + 200, 600)
    qtbot.waitExposed(panel)
    assert panel._review_splitter.orientation() == Qt.Orientation.Horizontal


@pytest.mark.gui
def test_text_zoom_applies_to_both_review_views(qtbot) -> None:
    """
    Given  a loaded review
    When   the teacher zooms the text
    Then   the annotated diff and the notes pane both render at the new size
    """
    panel = _panel(qtbot, correction_review=REVIEW)
    before = panel._review_diff._font_pt

    panel._change_text_zoom(1)

    assert panel._review_diff._font_pt == before + 1
    assert panel._review_notes._font_pt == before + 1


# ── the views on their own ────────────────────────────────────────────────


@pytest.mark.gui
def test_review_diff_without_texts_asks_for_the_missing_stage(qtbot) -> None:
    """
    Given  a diff view loaded with nothing, then with only the raw text
    When   it is asked for its page
    Then   it explains which stage is missing instead of showing nothing
    """
    view = ReviewDiffView()
    qtbot.addWidget(view)

    view.set_content("", "", None)
    assert txt.NO_OCR_TEXT in view.toPlainText()

    view.set_content(RAW, "", None)
    assert txt.NO_CORRECTION_TEXT in view.toPlainText()


@pytest.mark.gui
def test_notes_view_is_empty_without_a_review(qtbot) -> None:
    """
    Given  a notes view that has received no review
    When   it renders with each filter
    Then   every filter reports its own empty message
    """
    view = ReviewNotesView()
    qtbot.addWidget(view)

    view.set_content(None)
    assert txt.NO_NOTES in view.browser().toPlainText()

    view.set_filter(FILTER_UNCLEAR)
    assert txt.NO_UNCLEAR_NOTES in view.browser().toPlainText()

    view.set_filter(FILTER_CHANGES)
    assert txt.NO_CHANGE_NOTES in view.browser().toPlainText()


@pytest.mark.gui
def test_markers_in_the_diff_show_the_explanation_as_a_tooltip(qtbot, monkeypatch) -> None:
    """
    Given  a loaded review with explained changes and an unclear expression
    When   the mouse rests on a change marker and on an unclear marker
    Then   the tooltip names category and explanation, respectively the reason, with quotes
           shown as quotes
    """
    from PySide6.QtCore import QEvent  # noqa: PLC0415
    from PySide6.QtGui import QHelpEvent  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication, QToolTip  # noqa: PLC0415

    panel = _panel(qtbot, correction_review=REVIEW)
    view = panel._review_diff
    shown: list[str] = []
    monkeypatch.setattr(QToolTip, "showText", lambda _pos, text, _widget=None: shown.append(text))

    for target in ("note:1", "unclear:1"):
        monkeypatch.setattr(view, "anchorAt", lambda _pos, target=target: target)
        event = QHelpEvent(QEvent.Type.ToolTip, QPoint(5, 5), QPoint(5, 5))
        QApplication.sendEvent(view.viewport(), event)

    assert shown == [
        "<p>Grammar: &quot;she&quot; needs the 3rd-person form.</p>",
        "<p>&quot;It&quot; can mean the film or the visit.</p>",
    ]
    assert view.tooltip_for("note:1") == 'Grammar: "she" needs the 3rd-person form.'
    assert view.tooltip_for("note:99") == ""
    assert view.tooltip_for("nonsense") == ""


@pytest.mark.gui
def test_unclear_filter_shows_the_three_column_table(qtbot) -> None:
    """
    Given  a loaded review with unclear expressions
    When   the Unclear filter is chosen
    Then   the pane shows a table with the columns Original expression, Why it is unclear and
           Recommended version, and the suggestion is labelled as such
    """
    panel = _panel(qtbot, correction_review=REVIEW)
    notes = panel._review_notes

    notes.set_filter(FILTER_UNCLEAR)
    html_text = notes._html(None)

    assert "<table" in html_text
    for title in (txt.COLUMN_EXPRESSION, txt.COLUMN_WHY, txt.COLUMN_RECOMMENDED):
        assert f"<th align='left'>{title}</th>" in html_text
    assert "It was very nice" in html_text
    assert f"{txt.SUGGESTION_PREFIX} The film was very nice" in html_text
    assert f"{txt.RECOMMENDED_PREFIX} Something else" in html_text


@pytest.mark.gui
def test_notes_that_are_not_in_the_text_are_collapsed_until_opened(qtbot) -> None:
    """
    Given  a review with a note whose edit is not in the text
    When   the notes are shown, the toggle is clicked, and clicked again
    Then   the section is closed first, then lists the note, then closes again
    """
    stray = CorrectionReview(
        sentences=(
            SentenceNote("Fine one.", "Fine one.", (ChangeNote("x", "y", "other", "Phantom."),)),
        )
    )
    panel = _panel(qtbot, correction_review=stray)
    notes = panel._review_notes

    assert "Phantom." not in notes._html(None)
    assert txt.trailing_toggle(1, expanded=False) in notes._html(None)

    notes._on_anchor_clicked(QUrl("toggle:trailing"))
    assert "x \u2192 y" in notes._html(None)
    assert txt.trailing_toggle(1, expanded=True) in notes._html(None)

    notes._on_anchor_clicked(QUrl("toggle:trailing"))
    assert txt.trailing_toggle(1, expanded=False) in notes._html(None)


@pytest.mark.gui
def test_unexplained_model_change_is_not_labelled_as_the_teachers(qtbot) -> None:
    """
    Given  a review that explains one edit of a sentence, a second model edit without a note,
           and a teacher's edit elsewhere in the proofread text
    When   the notes pane renders the changes
    Then   the model's unexplained edit reads "no explanation" and only the teacher's own edit
           reads "your change"
    """
    review = CorrectionReview(
        sentences=(
            SentenceNote(
                "She have gone home.",
                "She has gone back.",
                (ChangeNote("have", "has", "grammar", "Agreement."),),
            ),
        )
    )
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.load(
        _pixmap(),
        "She have gone home. We was there.",
        "She has gone back. We were there.",
        "",
        correction_review=review,
    )

    html_text = panel._review_notes._html(None)

    assert html_text.count(f"<span class='muted'>{txt.NO_EXPLANATION}</span>") == 1
    assert html_text.count(f"<span class='muted'>{txt.YOUR_CHANGE}</span>") == 1


@pytest.mark.gui
def test_all_counts_come_from_the_diff(qtbot) -> None:
    """
    Given  a review whose single note explains an edit that the word diff splits in two hunks
    When   the panel loads it
    Then   the tab title, the navigation status and the filter button all count two changes
    """
    review = CorrectionReview(
        sentences=(
            SentenceNote(
                "She met his friend.",
                "She met her friends.",
                (ChangeNote("his friend", "her friends", "word_choice", "Agreement."),),
            ),
        )
    )
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.load(
        _pixmap(), "She met his friend.", "She met her friends.", "", correction_review=review
    )
    panel.update_stage_statuses(
        "doc", StageStatus.DONE, StageStatus.DONE, StageStatus.PENDING, True, True
    )

    assert panel._text_tabs.tabText(panel._text_tabs.indexOf(panel._changes_tab)) == (
        f"{txt.CHANGES_TAB} (2)"
    )
    assert panel._review_notes._changes_btn.text() == txt.filter_changes_label(2)
    assert "2 changes" in panel._correction_nav._status_lbl.text()


# ── spelling and punctuation stay out of the overview ─────────────────────

MINOR_RAW = "She have a dogs, and he run home"
MINOR_CORRECTED = "She has a dog, and he runs home."
MINOR_REVIEW = CorrectionReview(
    sentences=(
        SentenceNote(
            original=MINOR_RAW,
            corrected=MINOR_CORRECTED,
            changes=(
                ChangeNote("have", "has", "grammar", "Third person."),
                ChangeNote("dogs", "dog", "spelling", "Singular after a."),
                ChangeNote("run", "runs", "grammar", "Third person."),
                ChangeNote("home", "home.", "punctuation", "Full stop."),
            ),
        ),
    ),
)


def _minor_panel(qtbot) -> PreviewPanel:
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.load(_pixmap(), MINOR_RAW, MINOR_CORRECTED, "", correction_review=MINOR_REVIEW)
    return panel


@pytest.mark.gui
def test_minor_changes_are_not_listed_one_by_one(qtbot) -> None:
    """
    Given  a review with two grammar changes, a spelling change and a missing full stop
    When   the notes pane and the tab title are read
    Then   only the grammar changes are listed (numbered 1 and 2), the spelling and punctuation
           explanations are not shown, and the counts say two changes
    """
    panel = _minor_panel(qtbot)

    notes = panel._review_notes.browser().toPlainText()

    assert "have → has" in notes and "run → runs" in notes
    assert "Singular after a." not in notes
    assert "Full stop." not in notes
    assert panel._review_notes._changes_btn.text() == txt.filter_changes_label(2)
    assert panel._text_tabs.tabText(panel._text_tabs.indexOf(panel._changes_tab)) == (
        f"{txt.CHANGES_TAB} (2)"
    )


@pytest.mark.gui
def test_the_list_numbers_have_no_gaps_but_link_to_the_diff_hunks(qtbot) -> None:
    """
    Given  a review whose second hunk is a spelling change
    When   the notes are rendered
    Then   the entries are numbered 1, 2 while the second one still links to hunk 3 in the text
    """
    panel = _minor_panel(qtbot)

    html_text = panel._review_notes._html(None)

    assert "href='note:1'>1</a>" in html_text
    assert "href='note:3'>2</a>" in html_text
    assert "href='note:2'>" not in html_text.split("toggle:minor")[0]


@pytest.mark.gui
def test_minor_changes_are_one_collapsed_line_that_expands(qtbot) -> None:
    """
    Given  a review with two minor changes
    When   the notes are shown, and the line is clicked
    Then   one collapsed line names the count, and expanding it lists the short entries
           (old → new and category) without explanations
    """
    panel = _minor_panel(qtbot)
    notes = panel._review_notes
    collapsed = txt.minor_toggle(2, expanded=False)
    assert collapsed in notes.browser().toPlainText()
    assert "dogs → dog" not in notes.browser().toPlainText()

    notes.browser().anchorClicked.emit(QUrl("toggle:minor"))

    text = notes.browser().toPlainText()
    assert txt.minor_toggle(2, expanded=True) in text
    assert "dogs → dog" in text and "Spelling" in text
    assert "Singular after a." not in text


@pytest.mark.gui
def test_clicking_a_minor_change_in_the_text_opens_its_entry(qtbot) -> None:
    """
    Given  a review with a spelling change inside the collapsed minor section
    When   the teacher clicks its words in the text
    Then   the section opens and the entry is marked, and the words stay highlighted in the text
    """
    panel = _minor_panel(qtbot)

    panel._review_diff.anchorClicked.emit(QUrl("note:2"))

    assert panel._review_notes._show_minor
    assert "dogs → dog" in panel._review_notes.browser().toPlainText()
    assert _selected_words(panel._review_diff) == "dogsdog"


@pytest.mark.gui
def test_the_diff_still_marks_minor_changes(qtbot) -> None:
    """
    Given  a review with a spelling change
    When   the diff is rendered
    Then   the changed words are still linked and coloured, only the list leaves them out
    """
    panel = _minor_panel(qtbot)

    assert "href='note:2'" in panel._review_diff._html()
    assert panel._review_diff.tooltip_for("note:2") == "Spelling: Singular after a."
