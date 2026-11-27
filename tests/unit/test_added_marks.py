"""Unit tests for ``domain/proofreading/marks.py``: the Markdown marks a model adds."""

from __future__ import annotations

from teachers_teammate.domain.proofreading.marks import added_marks, strip_added_marks

MARKDOWN_SOURCE = "Er **geht** heim."


def test_a_mark_the_source_does_not_have_is_reported() -> None:
    """
    Given  a plain source and an answer that bolds a word
    When   the added marks are checked
    Then   the answer is flagged, because a teacher receives the asterisks
    """
    assert added_marks("Er geht heim.", "Er **geht** heim.") is True


def test_a_mark_the_source_already_has_is_not_reported() -> None:
    """
    Given  a Markdown source and an answer that keeps its bold text
    When   the added marks are checked
    Then   nothing is reported, because the model added nothing
    """
    assert added_marks(MARKDOWN_SOURCE, MARKDOWN_SOURCE) is False


def test_stripping_removes_the_marks_the_source_does_not_have() -> None:
    """
    Given  a plain source and an answer that bolds a word and strikes another
    When   the marks the source does not have are stripped
    Then   the words stay and only the marks go
    """
    assert strip_added_marks("Er geht heim.", "Er **geht** ~~heim~~.") == "Er geht heim."


def test_stripping_leaves_a_mark_the_source_already_has() -> None:
    """
    Given  a Markdown source and an answer that adds a second bold word
    When   the marks the source does not have are stripped
    Then   the source's own bold text stays, because it is part of the text
    """
    output = "Er **geht** gleich heim."

    assert strip_added_marks(MARKDOWN_SOURCE, output) == output
