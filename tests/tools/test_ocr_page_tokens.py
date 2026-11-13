"""Tests for the developer tool that measures what a reading model costs on one page."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.ocr_page_tokens import Reading, _catalog_lines, _pages, _reading, _report, _spread
from teachers_teammate.infrastructure.ollama_utils import ChatStats


def _stats(answer_tokens: int, words: int, *, done_reason: str = "stop") -> ChatStats:
    """Return what Ollama reports for a page read in 2 s and answered in 4 s."""
    return ChatStats(
        done_reason=done_reason,
        prompt_eval_count=1200,
        eval_count=answer_tokens,
        total_duration=6_000_000_000,
        load_duration=0,
        prompt_eval_duration=2_000_000_000,
        eval_duration=4_000_000_000,
    )


def _reading_of(answer_tokens: int, words: int, *, page: str = "page-1.png") -> Reading:
    """Return the record of one page of *words* words answered with *answer_tokens* tokens."""
    text = " ".join(["Wort"] * words)
    return _reading("glm-ocr:q8_0", page, text, _stats(answer_tokens, words))


@pytest.mark.use_case("Model_Eval_Report")
def test_a_page_reports_the_tokens_of_the_image_the_answer_and_the_words() -> None:
    """
    Given  a page whose answer was 900 tokens and 300 words, read in 2 s and written in 4 s
    When   the record is built
    Then   it carries the image tokens, the answer tokens, the words and both rates
    """
    reading = _reading_of(900, 300)

    assert reading.image_tokens == 1200
    assert reading.answer_tokens == 900
    assert reading.words == 300
    assert reading.read_tps == 600.0
    assert reading.write_tps == 225.0
    assert reading.truncated is False


@pytest.mark.use_case("Model_Eval_Report")
def test_an_answer_that_stopped_at_the_token_cap_is_marked_as_cut_off() -> None:
    """
    Given  a page whose answer stopped at the token cap
    When   the record is built
    Then   it is marked as cut off, so the figure is read as a lower bound
    """
    reading = _reading("glm-ocr:q8_0", "page-1.png", "Wort", _stats(2048, 1, done_reason="length"))

    assert reading.truncated is True


@pytest.mark.use_case("Model_Eval_Report")
def test_the_spread_is_the_middle_eighty_percent_of_the_pages() -> None:
    """
    Given  eleven pages of 100, 200, ... 1100 answer tokens
    When   the spread is computed
    Then   it is the 10th percentile, the median and the 90th percentile, not the ends
    """
    assert _spread([float(value) for value in range(100, 1200, 100)]) == (200, 600, 1000)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_report_names_every_page_and_the_figures_of_each_model() -> None:
    """
    Given  two pages read by one model
    When   the report is built
    Then   the table has a row per page and the summary names the answer tokens and the words
    """
    rendered = _report([_reading_of(900, 300), _reading_of(1100, 320, page="page-2.png")])

    assert (
        "| `glm-ocr:q8_0` | page-1.png | 1200 | 900 | 300 | 600 | 225 | the end of the page |"
        in (rendered)
    )
    assert "answer tokens per page 920 / 1000 / 1080" in rendered
    assert "words per page 302 / 310 / 318" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_the_catalog_lines_give_the_answer_length_and_the_words_of_a_page() -> None:
    """
    Given  two pages read by one model
    When   the catalog lines are built
    Then   they hold the answer_length to paste and the page's word count
    """
    rendered = _catalog_lines([_reading_of(900, 300), _reading_of(1100, 320, page="page-2.png")])

    assert "answer_length=AnswerLength(low=920, typical=1000, high=1080)" in rendered
    assert "TYPICAL_PAGE_WORDS = 310" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_a_report_without_a_page_says_so() -> None:
    """
    Given  no page was read
    When   the report and the catalog lines are built
    Then   both say there is nothing to show
    """
    assert _report([]) == "No page was read."
    assert _catalog_lines([]) == ""


@pytest.mark.use_case("Model_Eval_Report")
def test_the_pages_are_the_images_of_the_directories_and_the_files_named(tmp_path: Path) -> None:
    """
    Given  a directory with two images and a text file, and one image named directly
    When   the pages are collected
    Then   the three images are found and the text file is not
    """
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "a.png").write_bytes(b"")
    (pages / "b.JPG").write_bytes(b"")
    (pages / "notes.txt").write_text("kein Bild")
    other = tmp_path / "c.webp"
    other.write_bytes(b"")

    found = _pages([pages, other])

    assert [path.name for path in found] == ["a.png", "b.JPG", "c.webp"]
