"""Unit tests for the Check tab's one query (``application/check_document.py``)."""

from __future__ import annotations

from pathlib import Path

from teachers_teammate.application.check_document import PreprocessInfo, check_document_of
from teachers_teammate.application.check_items import ItemKind
from teachers_teammate.domain.data_location import SentRecord, SentStatus
from teachers_teammate.domain.proofreading.edits import edits
from teachers_teammate.domain.proofreading.review import CorrectionReview, align, edit_note
from teachers_teammate.infrastructure.state_repository import DocumentStateView

RAW = "Der bunte Hund bellt laut."
CORRECTED = "Der braune Hund bellt laut."


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
        "cache_status_label": "up to date",
        "loaded_at": "2026-01-01T00:00:00+00:00",
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


def test_realigned_keeps_the_machines_own_text() -> None:
    """
    Given  a stored document and two new texts typed into it
    When   the document is realigned
    Then   the machine's own text stays what the store holds, so a change the model made
           without an explanation is still the model's and the teacher's own is theirs
    """
    doc = check_document_of(_view())
    new_raw = "Der bunte Hund bellt laut. Er lauft."
    new_corrected = "Der braune Hund bellt laut. Er läuft."

    realigned = doc.realigned(new_raw, new_corrected)

    assert realigned.machine_corrected_text == doc.machine_corrected_text
    assert realigned.raw_plain == new_raw
    assert realigned.corrected_plain == new_corrected
    assert [item.kind for item in realigned.items] == [ItemKind.CORRECTION, ItemKind.OWN]


def test_realigned_keeps_the_pages_and_the_content_review() -> None:
    """
    Given  a stored document with pages and a content review
    When   it is realigned after a typed edit
    Then   the pages, the originals and the content review are unchanged
    """
    doc = check_document_of(_view(evaluation_text="Gut geschrieben."))

    realigned = doc.realigned(RAW, "Der braune Hund bellt laut. Er läuft.")

    assert realigned.pages == doc.pages
    assert realigned.original_pages == doc.original_pages
    assert realigned.content_review == "Gut geschrieben."
    assert realigned.preprocess == doc.preprocess


def test_realigned_marks_a_taken_out_correction() -> None:
    """
    Given  a stored document whose correction the teacher undid by typing
    When   it is realigned against the student's text
    Then   the correction is a taken-out item
    """
    doc = check_document_of(_view())

    realigned = doc.realigned(RAW, RAW)

    assert [item.kind for item in realigned.items] == [ItemKind.TAKEN_OUT]


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
    Given  a stored document
    When   the same texts are realigned twice
    Then   the second answer is the first one, so typing does not re-diff needlessly
    """
    doc = check_document_of(_view())

    first = doc.realigned(RAW, CORRECTED)
    second = doc.realigned(RAW, CORRECTED)

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
