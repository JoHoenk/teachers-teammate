"""Unit tests for the Check tab's one query (``application/check_document.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.application.check_document import (
    CheckDocument,
    PreprocessInfo,
    SentKind,
    check_document_of,
    sent_view_of,
)
from teachers_teammate.application.check_items import ItemKind
from teachers_teammate.domain.data_location import SentRecord, SentStatus
from teachers_teammate.domain.proofreading.edits import edits
from teachers_teammate.domain.proofreading.review import CorrectionReview, align, edit_note
from teachers_teammate.domain.stages import Stage
from teachers_teammate.application.document_view import DocumentStateView

RAW = "Der bunte Hund bellt laut."
CORRECTED = "Der braune Hund bellt laut."
SENT = "Der <PERSON_1> bellt laut."
"""What the anonymizer made of :data:`RAW` when the proofreading went out."""
SENT_MAP = {"<PERSON_1>": "bunten Hund"}
PREVIEW = "Der <PERSON_2> bellt laut."
"""What the anonymizer makes of the transcription now, in another numbering."""


def _review() -> CorrectionReview:
    """Return the review of :data:`RAW` against :data:`CORRECTED`."""
    return CorrectionReview(
        edits=(edit_note(edits(RAW, CORRECTED)[0], category="word_choice", explanation="Farbe."),)
    )


def _view(**overrides: object) -> DocumentStateView:
    """Return a stored document with a proofreading, as the state store hands it over."""
    fields: dict[str, object] = {
        "source_id": "/tmp/Anna.png",
        "name": "Anna.png",
        "preview_img": "/tmp/Anna_prepared.png",
        "raw_text": RAW,
        "correction_text": CORRECTED,
        "evaluation_text": "",
        "ocr_done": True,
        "correction_done": True,
        "evaluation_done": False,
        "original_img": "/tmp/Anna_original.png",
        "preprocess_steps": ("grayscale",),
        "raw_text_format": "plain",
        "correction_review": _review(),
        "correction_warning": "",
        "sent": {},
        "page_images": ("/tmp/Anna_prepared.png",),
        "original_images": ("/tmp/Anna_original.png",),
    }
    fields.update(overrides)
    return DocumentStateView(**fields)  # ty: ignore[invalid-argument-type]


def test_a_stored_document_carries_its_texts_and_its_items() -> None:
    """
    Given  a stored document with a proofreading and its review
    When   the Check tab's data is built
    Then   it has both texts, both plain texts, the aligned review and its items
    """
    doc = check_document_of(_view())

    assert doc.source_id == "/tmp/Anna.png"
    assert doc.name == "Anna.png"
    assert doc.raw_text == RAW
    assert doc.corrected_text == CORRECTED
    assert doc.raw_plain == RAW
    assert doc.corrected_plain == CORRECTED
    assert doc.aligned is not None
    assert [item.kind for item in doc.items] == [ItemKind.CORRECTION]
    assert doc.has_proofreading is True


def test_a_document_without_a_proofreading_has_no_aligned_review() -> None:
    """
    Given  a stored document that was only recognized
    When   the Check tab's data is built
    Then   there is no proofreading, no aligned review and no item
    """
    doc = check_document_of(_view(correction_text="", correction_review=None))

    assert doc.has_proofreading is False
    assert doc.aligned is None
    assert doc.items == ()


def test_a_proofreading_without_a_review_still_has_items() -> None:
    """
    Given  a stored document whose proofreading has no review
    When   the Check tab's data is built
    Then   the changes are items without explanation, not the teacher's own
    """
    doc = check_document_of(_view(correction_review=None))

    assert doc.aligned is not None
    assert [item.kind for item in doc.items] == [ItemKind.UNEXPLAINED]


def test_a_warning_makes_unknown_hunks_unexplained() -> None:
    """
    Given  a stored document whose review was reported incomplete
    When   the Check tab's data is built
    Then   a change the review does not know is a correction without explanation
    """
    doc = check_document_of(_view(correction_warning="Die Erklärungen fehlen."))

    assert [item.kind for item in doc.items] == [ItemKind.CORRECTION]


def test_the_pages_and_the_originals_are_carried_in_page_order() -> None:
    """
    Given  a stored document with three prepared pages and the original of the first
    When   the Check tab's data is built
    Then   the scan has three pages and only the first has an original
    """
    doc = check_document_of(_view(page_images=("/tmp/a.png", "/tmp/b.png", "/tmp/c.png")))

    assert doc.pages == (Path("/tmp/a.png"), Path("/tmp/b.png"), Path("/tmp/c.png"))
    assert doc.original_pages == (Path("/tmp/Anna_original.png"), None, None)


def test_every_original_is_carried_in_page_order() -> None:
    """
    Given  a stored document with three prepared pages and three originals
    When   the Check tab's data is built
    Then   each page has its own original
    """
    doc = check_document_of(
        _view(
            page_images=("/tmp/a.png", "/tmp/b.png", "/tmp/c.png"),
            original_images=("/tmp/a_o.png", "/tmp/b_o.png", "/tmp/c_o.png"),
        )
    )

    assert doc.original_pages == (Path("/tmp/a_o.png"), Path("/tmp/b_o.png"), Path("/tmp/c_o.png"))


def test_a_page_without_a_kept_original_has_none() -> None:
    """
    Given  a stored document whose originals stop after the second page
    When   the Check tab's data is built
    Then   the third page has no original
    """
    doc = check_document_of(
        _view(
            page_images=("/tmp/a.png", "/tmp/b.png", "/tmp/c.png"),
            original_images=("/tmp/a_o.png", "/tmp/b_o.png"),
        )
    )

    assert doc.original_pages == (Path("/tmp/a_o.png"), Path("/tmp/b_o.png"), None)


def test_a_document_without_pages_has_none() -> None:
    """
    Given  a stored document with no preview image at all
    When   the Check tab's data is built
    Then   the scan has no page and no original
    """
    doc = check_document_of(_view(preview_img="", page_images=(), original_img=""))

    assert doc.pages == ()
    assert doc.original_pages == ()


def test_the_preparation_and_the_content_review_are_carried() -> None:
    """
    Given  a stored document with preprocessing steps and a content review
    When   the Check tab's data is built
    Then   both are there, and how the result was sent
    """
    sent = {"correction": SentStatus(SentRecord(privacy="v2|online|Mistral||0|eu"))}

    doc = check_document_of(_view(evaluation_text="Gut geschrieben.", sent=sent))

    assert doc.preprocess == PreprocessInfo("/tmp/Anna_original.png", ("grayscale",))
    assert doc.content_review == "Gut geschrieben."
    assert doc.sent == sent


def test_a_change_the_teacher_typed_is_their_own_and_not_the_machines() -> None:
    """
    Given  a stored document whose texts the teacher typed over, with the machine's own text
           still what the store holds
    When   the Check tab's data is built
    Then   the explained change is the model's, and the one the teacher typed is their own
    """
    doc = check_document_of(
        _view(
            raw_text="Der bunte Hund bellt laut. Er lauft.",
            correction_text="Der braune Hund bellt laut. Er läuft.",
            machine_correction_text=CORRECTED,
        )
    )

    assert doc.machine_corrected_text == CORRECTED
    assert [item.kind for item in doc.items] == [ItemKind.CORRECTION, ItemKind.OWN]


def test_a_correction_the_teacher_undid_is_a_taken_out_item() -> None:
    """
    Given  a stored document whose proofread text was typed back to what the teacher wants
    When   the Check tab's data is built
    Then   the correction the review explains is a taken-out item
    """
    doc = check_document_of(_view(correction_text=RAW))

    assert [item.kind for item in doc.items] == [ItemKind.TAKEN_OUT]


def test_a_markdown_document_is_aligned_on_its_plain_text() -> None:
    """
    Given  a stored document in Markdown whose review is anchored on the plain text
    When   the Check tab's data is built
    Then   the plain texts are the ones without marks, and the item is found
    """
    raw = "Der **bunte** Hund bellt."
    corrected = "Der **braune** Hund bellt."
    review = CorrectionReview(
        edits=(
            edit_note(
                edits("Der bunte Hund bellt.", "Der braune Hund bellt.")[0],
                category="word_choice",
                explanation="Farbe.",
            ),
        )
    )

    doc = check_document_of(
        _view(
            raw_text=raw,
            correction_text=corrected,
            raw_text_format="markdown",
            correction_review=review,
        )
    )

    assert doc.text_format == "markdown"
    assert doc.raw_plain == "Der bunte Hund bellt."
    assert doc.corrected_plain == "Der braune Hund bellt."
    assert [item.kind for item in doc.items] == [ItemKind.CORRECTION]


def test_aligning_the_same_texts_twice_gives_the_same_review() -> None:
    """
    Given  the same stored document read twice
    When   the Check tab's data is built for each read
    Then   the second answer is the first one, so reopening a document does not re-diff it
    """
    first = check_document_of(_view())
    second = check_document_of(_view())

    assert first.aligned is second.aligned
    assert first.aligned == align(RAW, CORRECTED, _review())


def test_the_original_of_a_page_that_was_not_kept_is_none() -> None:
    """
    Given  a stored document whose preparation changed nothing
    When   the Check tab's data is built
    Then   the page has no original, and the steps say none were applied
    """
    doc = check_document_of(_view(original_img="", original_images=(), preprocess_steps=()))

    assert doc.original_pages == (None,)
    assert doc.preprocess == PreprocessInfo("", ())


# ── What the box of *What was read* holds ─────────────────────────────────


def _sent_doc(
    *, proofread: bool = True, hiding_on: bool = True, sent_text: str = "", map: bool = True
) -> CheckDocument:
    """Return the tab's data for a stored document and how its proofreading was sent."""
    return check_document_of(
        _view(
            correction_text=CORRECTED if proofread else "",
            correction_review=_review() if proofread else None,
            correction_done=proofread,
            sent_text=sent_text,
            sent_map=dict(SENT_MAP) if map and sent_text else {},
            sent={
                Stage.CORRECTION: SentStatus(
                    SentRecord(provider="ollama", names_hidden=1, hiding_on=hiding_on)
                )
            }
            if proofread
            else {},
        )
    )


def test_a_stored_sent_text_comes_with_the_map_of_its_placeholders() -> None:
    """
    Given  a stored proofreading that went out with a name replaced
    When   the Check tab's data is built
    Then   it carries the text that was sent and the map of its placeholder
    """
    doc = _sent_doc(sent_text=SENT)

    assert doc.sent_text == SENT
    assert dict(doc.sent_map) == SENT_MAP


def test_the_box_holds_the_stored_string_and_not_a_remasked_copy() -> None:
    """
    Given  a proofreading stored with the text it was sent as, where the model read the name
    differently from the transcription and so a re-masking would miss it
    When   the box of *What was read* is asked what it shows with names hidden
    Then   it holds exactly the stored string, with the name that was found behind its chip
    """
    doc = _sent_doc(sent_text=SENT)

    sent = sent_view_of(doc, available=True, hiding=True)

    assert sent.kind is SentKind.SENT
    assert sent.text == SENT
    assert sent.placeholders == ("<PERSON_1>",)
    assert dict(sent.names) == SENT_MAP


@pytest.mark.parametrize(
    "case",
    [
        {"proofread": True, "hiding": True, "sent": SENT, "kind": SentKind.SENT, "text": SENT},
        {"proofread": True, "hiding": True, "kind": SentKind.SENT, "text": RAW},
        {"proofread": True, "hiding": False, "sent": SENT, "kind": SentKind.NAMES_SHOWN},
        {"proofread": True, "hiding_on": False, "hiding": True, "kind": SentKind.AS_WRITTEN},
        {"proofread": False, "preview": True, "kind": SentKind.WILL_BE_SENT, "text": PREVIEW},
        {"proofread": False, "kind": SentKind.WORKING},
        {"proofread": False, "hiding": False, "kind": SentKind.NAMES_SHOWN},
        {"proofread": False, "available": False, "kind": SentKind.UNAVAILABLE},
    ],
    ids=[
        "sent-nothing-hidden",
        "sent-nothing-found",
        "sent-but-names-shown",
        "sent-as-written",
        "not-sent-yet-the-preview",
        "not-sent-yet-working",
        "names-shown-hiding-off",
        "names-cannot-be-hidden",
    ],
)
def test_the_box_of_what_was_read_follows_one_rule(case: dict) -> None:
    """
    Given  a stored document, and what the window knows about hiding
    When   the box of *What was read* is asked what it shows
    Then   it holds the one text and names the one reason the rule gives for it
    """
    doc = _sent_doc(
        proofread=case.get("proofread", True),
        hiding_on=case.get("hiding_on", True),
        sent_text=case.get("sent", ""),
    )
    preview = (PREVIEW, {"<PERSON_2>": "bunte Hund"}) if case.get("preview") else None

    sent = sent_view_of(
        doc,
        available=case.get("available", True),
        hiding=case.get("hiding", True),
        preview=preview,
    )

    assert sent.kind is case["kind"]
    assert sent.text == case.get("text", RAW)
