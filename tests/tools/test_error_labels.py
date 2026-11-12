"""Unit tests for the corpus error labels attached to the reference changes of a test text."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.domain.correction_review import (
    ChangeNote,
    CorrectionReview,
    SentenceNote,
)
from tools.model_eval.error_labels import (
    UNLABELLED,
    M2Edit,
    M2Sentence,
    ReferenceLabel,
    category_agreement,
    category_of,
    label_reference,
    locate,
    main_type,
    read_m2,
    split_into_texts,
)

_M2 = """\
S Die Studenten kriegt nichts .
A 2 3|||R:VERB:SVA|||kriegen|||REQUIRED|||-NONE-|||0

S Sie sind aber von geringem Wert .
A -1 -1|||noop|||-NONE-|||REQUIRED|||-NONE-|||0

S Ohne Ausbildung geht nichts
A 3 3|||M:PUNCT|||.|||REQUIRED|||-NONE-|||0
A 1 2|||UNK|||-NONE-|||REQUIRED|||-NONE-|||0
A 0 1|||R:PREP|||Mit|||REQUIRED|||-NONE-|||1
"""


def _sentence(*tokens: str, edits: tuple[M2Edit, ...] = ()) -> M2Sentence:
    """Return an M2 sentence of *tokens* with *edits*."""
    return M2Sentence(tokens=tuple(tokens), edits=edits)


@pytest.mark.use_case("Model_Eval_Labels")
def test_an_m2_file_is_read_with_the_edits_of_the_first_annotator(tmp_path: Path) -> None:
    """
    Given  an M2 file with a noop sentence, an unknown error and an edit of a second annotator
    When   it is read
    Then   every sentence is there, and only the real edits of annotator 0 are kept
    """
    path = tmp_path / "x.m2"
    path.write_text(_M2, encoding="utf-8")

    sentences = read_m2(path)

    assert [s.tokens[0] for s in sentences] == ["Die", "Sie", "Ohne"]
    assert sentences[0].edits == (M2Edit(2, 3, "R:VERB:SVA"),)
    assert sentences[1].edits == ()
    assert sentences[2].edits == (M2Edit(3, 3, "M:PUNCT"),)


@pytest.mark.use_case("Model_Eval_Labels")
def test_read_m2_marks_a_sentence_with_an_uncorrected_error(tmp_path: Path) -> None:
    """
    Given  an M2 file whose third sentence carries an UNK annotation
    When   it is read
    Then   that sentence is marked uncorrected and still has no edit, so it does not look unchanged
    """
    path = tmp_path / "x.m2"
    path.write_text(_M2, encoding="utf-8")

    sentences = read_m2(path)

    assert [sentence.uncorrected for sentence in sentences] == [False, False, True]
    assert sentences[2].edits == (M2Edit(3, 3, "M:PUNCT"),)


@pytest.mark.use_case("Model_Eval_Labels")
def test_the_tokens_of_a_text_are_found_one_after_another() -> None:
    """
    Given  a text and the M2 sentences it is made of, tokenised differently from the text
    When   the tokens are located
    Then   each token has the character span where it stands in the text
    """
    text = "It's late.  Go home."
    sentences = [_sentence("It", "'s", "late", "."), _sentence("Go", "home", ".")]

    located = locate(text, sentences)

    assert located is not None
    assert [text[a:b] for a, b in located[0]] == ["It", "'s", "late", "."]
    assert [text[a:b] for a, b in located[1]] == ["Go", "home", "."]


@pytest.mark.use_case("Model_Eval_Labels")
def test_locating_stops_when_the_text_is_used_up() -> None:
    """
    Given  more M2 sentences than the text holds
    When   the tokens are located
    Then   only the sentences the text is made of are returned
    """
    sentences = [_sentence("One", "."), _sentence("Two", "."), _sentence("Three", ".")]

    located = locate("One. Two.", sentences)

    assert located is not None
    assert len(located) == 2


@pytest.mark.use_case("Model_Eval_Labels")
def test_a_typographic_apostrophe_matches_the_normalised_token() -> None:
    """
    Given  a text with a typographic apostrophe and an M2 token with the plain one
    When   the tokens are located
    Then   they match, the way the corpus conversion normalised them before tokenising
    """
    located = locate("It\u2019s", [_sentence("It", "'s")])

    assert located == [((0, 2), (2, 4))]


@pytest.mark.use_case("Model_Eval_Labels")
def test_sentences_that_are_not_those_of_the_text_are_not_located() -> None:
    """
    Given  M2 sentences of another text, and a text that is longer than its sentences
    When   the tokens are located
    Then   nothing is returned in either case, so a wrong text never gets labels
    """
    assert locate("Hello world.", [_sentence("Goodbye", "world", ".")]) is None
    assert locate("Hello world. And more.", [_sentence("Hello", "world", ".")]) is None


@pytest.mark.use_case("Model_Eval_Labels")
def test_each_text_finds_its_sentences_in_a_file_that_does_not_mark_where_texts_end() -> None:
    """
    Given  the sentences of two texts one after another, and a first text no sentences fit
    When   the texts are split
    Then   the second text is still found, and the first is reported as not found
    """
    sentences = [_sentence("Text", "one", "."), _sentence("Text", "two", ".")]

    found = split_into_texts(["Text one.", "Text two."], sentences)
    lost = split_into_texts(["Something else.", "Text two."], sentences)

    assert [entry[0] for entry in found if entry] == [0, 1]
    assert lost[0] is None
    assert lost[1] is not None
    assert lost[1][0] == 1


def _labelled(source: str, reference: str, *sentences: M2Sentence) -> tuple[ReferenceLabel, ...]:
    """Return the labels of *source* → *reference*, locating *sentences* in *source* first."""
    located = locate(source, sentences)
    assert located is not None
    return label_reference(source, reference, list(zip(sentences, located, strict=True)))


@pytest.mark.use_case("Model_Eval_Labels")
def test_a_replaced_word_takes_the_label_of_the_edit_it_overlaps() -> None:
    """
    Given  a reference that replaces one word, and an M2 edit on that word
    When   the reference changes are labelled
    Then   the one change carries the code of the M2 edit
    """
    sentence = _sentence("Er", "gehen", "heim", ".", edits=(M2Edit(1, 2, "R:VERB:SVA"),))

    label = _labelled("Er gehen heim.", "Er geht heim.", sentence)[0]

    assert label.code == "R:VERB:SVA"
    assert label.replacement == ("geht",)


@pytest.mark.use_case("Model_Eval_Labels")
def test_an_inserted_mark_is_labelled_whichever_side_of_the_space_it_falls() -> None:
    """
    Given  a comma inserted after a word, and an M2 insertion at the next token
    When   the reference change is labelled
    Then   it takes the label, because a space is not a position
    """
    sentence = _sentence("Ja", "ich", "komme", edits=(M2Edit(1, 1, "M:PUNCT"),))

    label = _labelled("Ja ich komme", "Ja, ich komme", sentence)[0]

    assert label.code == "M:PUNCT"


@pytest.mark.use_case("Model_Eval_Labels")
def test_a_change_without_an_annotated_edit_nearby_stays_unlabelled() -> None:
    """
    Given  a reference change far from the only annotated edit
    When   the reference changes are labelled
    Then   the far change keeps an empty code, so a report counts it as unlabelled
    """
    sentence = _sentence(
        "Wir",
        "gehen",
        "heute",
        "sehr",
        "gern",
        "nach",
        "Hause",
        "!",
        edits=(M2Edit(0, 1, "R:PRON"),),
    )

    labels = _labelled(
        "Wir gehen heute sehr gern nach Hause!", "Ihr gehen heute sehr gern nach Haus!", sentence
    )

    assert [label.code for label in labels] == ["R:PRON", ""]


@pytest.mark.use_case("Model_Eval_Labels")
def test_a_stored_label_comes_back_unchanged() -> None:
    """
    Given  a label as it is stored in a test set (a list instead of a tuple)
    When   it is read back
    Then   it equals the label it was made from
    """
    label = ReferenceLabel(2, 3, ("ist",), "R:VERB:TENSE")

    stored = {"start": 2, "end": 3, "replacement": ["ist"], "code": "R:VERB:TENSE"}

    assert ReferenceLabel.from_dict(stored) == label
    assert label.edit == (2, 3, ("ist",))


@pytest.mark.use_case("Model_Eval_Labels")
@pytest.mark.parametrize(
    ("code", "category", "kind"),
    [
        ("M:PUNCT", "punctuation", "PUNCT"),
        ("R:SPELL", "spelling", "SPELL"),
        ("R:ORTH", "spelling", "ORTH"),
        ("R:WO", "word_order", "WO"),
        ("R:DET:FORM", "grammar", "DET"),
        ("U:PREP", "grammar", "PREP"),
        ("R:ADP", "grammar", "ADP"),
        ("R:VERB:SVA", "grammar", "VERB"),
        ("R:NOUN:NUM", "grammar", "NOUN"),
        ("M:VERB", "grammar", "VERB"),
        ("R:VERB", "word_choice", "VERB"),
        ("R:ADJ", "word_choice", "ADJ"),
        ("U:ADJ", "other", "ADJ"),
        ("R:OTHER", "other", "OTHER"),
        ("", "", UNLABELLED),
    ],
)
def test_a_corpus_label_maps_to_the_apps_category(code: str, category: str, kind: str) -> None:
    """
    Given  a label of the corpora's error scheme
    When   its category and its word type are asked for
    Then   they are the app's category and the type without operation and subtype
    """
    assert category_of(code) == category
    assert main_type(code) == kind


def _review(original: str, corrected: str, category: str) -> CorrectionReview:
    """Return a review with one sentence that carries one change note."""
    note = ChangeNote(original=original, corrected=corrected, category=category, explanation="x")
    return CorrectionReview(
        sentences=(SentenceNote(original=original, corrected=corrected, changes=(note,)),)
    )


@pytest.mark.use_case("Model_Eval_Labels")
def test_a_note_with_the_category_of_the_label_agrees() -> None:
    """
    Given  an exactly fixed error labelled as a grammar error, and a note saying "grammar"
    When   the category agreement is counted
    Then   one error was checked and the note agrees
    """
    labels = (ReferenceLabel(2, 3, ("geht",), "R:VERB:SVA"),)
    review = _review("gehen", "geht", "grammar")

    result = category_agreement("Er gehen heim.", "Er geht heim.", "Er geht heim.", review, labels)

    assert (result.checked, result.agreed) == (1, 1)


@pytest.mark.use_case("Model_Eval_Labels")
def test_a_note_with_another_category_is_checked_but_does_not_agree() -> None:
    """
    Given  the same fixed error, and a note saying "word_choice"
    When   the category agreement is counted
    Then   one error was checked and the note does not agree
    """
    labels = (ReferenceLabel(2, 3, ("geht",), "R:VERB:SVA"),)
    review = _review("gehen", "geht", "word_choice")

    result = category_agreement("Er gehen heim.", "Er geht heim.", "Er geht heim.", review, labels)

    assert (result.checked, result.agreed) == (1, 0)


@pytest.mark.use_case("Model_Eval_Labels")
def test_an_error_the_model_fixed_differently_is_not_checked() -> None:
    """
    Given  a model that fixed the labelled error with another word
    When   the category agreement is counted
    Then   nothing is checked, because the note cannot be tied to the reference error
    """
    labels = (ReferenceLabel(2, 3, ("geht",), "R:VERB:SVA"),)
    review = _review("gehen", "ging", "grammar")

    result = category_agreement("Er gehen heim.", "Er ging heim.", "Er geht heim.", review, labels)

    assert (result.checked, result.agreed) == (0, 0)


@pytest.mark.use_case("Model_Eval_Labels")
def test_errors_labelled_other_are_not_checked_for_a_category() -> None:
    """
    Given  an exactly fixed error whose label is OTHER
    When   the category agreement is counted
    Then   nothing is checked, because any category could be right for it
    """
    labels = (ReferenceLabel(2, 3, ("geht",), "R:OTHER"),)
    review = _review("gehen", "geht", "grammar")

    result = category_agreement("Er gehen heim.", "Er geht heim.", "Er geht heim.", review, labels)

    assert result.checked == 0


@pytest.mark.use_case("Model_Eval_Labels")
def test_without_a_review_there_is_no_category_to_compare() -> None:
    """
    Given  a model answer in corrections-only mode (no review)
    When   the category agreement is counted
    Then   nothing is checked
    """
    labels = (ReferenceLabel(2, 3, ("geht",), "R:VERB:SVA"),)

    result = category_agreement("Er gehen heim.", "Er geht heim.", "Er geht heim.", None, labels)

    assert (result.checked, result.agreed) == (0, 0)
