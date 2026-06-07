"""Unit tests for teachers_teammate.infrastructure.ocr_text_cleaner."""

from __future__ import annotations

import pytest

from teachers_teammate.infrastructure.ocr_text_cleaner import clean_ocr_text


# ── Reasoning blocks ────────────────────────────────────────────────────────


def test_paired_think_block_removed() -> None:
    """
    Given  output with a paired <think>…</think> reasoning block
    When   clean_ocr_text is called
    Then   the block and tags are removed and the real text is preserved
    """
    raw = "<think>Let me read the image.\nIt says hello.</think>Hello world"

    assert clean_ocr_text(raw) == "Hello world"


def test_dangling_think_close_drops_prefix() -> None:
    """
    Given  output where the opening <think> was swallowed, leaving a lone </think>
    When   clean_ocr_text is called
    Then   everything up to and including the first </think> is dropped
    """
    raw = "I should transcribe this carefully.</think>\n\nThe actual answer"

    assert clean_ocr_text(raw) == "The actual answer"


def test_think_tag_case_insensitive() -> None:
    """
    Given  reasoning tags in mixed case
    When   clean_ocr_text is called
    Then   the block is still removed
    """
    raw = "<THINK>reasoning</THINK>result"

    assert clean_ocr_text(raw) == "result"


# ── Special / chat-template tokens ──────────────────────────────────────────


def test_fullwidth_pipe_tokens_removed() -> None:
    """
    Given  DeepSeek full-width-pipe control tokens around the text
    When   clean_ocr_text is called
    Then   each token is removed and the text remains
    """
    raw = (
        "<\uff5cbegin\u2581of\u2581sentence\uff5c><\uff5cUser\uff5c>"
        "Dear diary<\uff5cAssistant\uff5c><\uff5cend\u2581of\u2581sentence\uff5c>"
    )

    assert clean_ocr_text(raw) == "Dear diary"


@pytest.mark.parametrize("token", ["<|im_end|>", "<|im_start|>", "<|endoftext|>"])
def test_ascii_chatml_tokens_removed(token: str) -> None:
    """
    Given  an ASCII ChatML control token adjacent to the text
    When   clean_ocr_text is called
    Then   the token is removed and the text remains
    """
    raw = f"Lesson notes{token}"

    assert clean_ocr_text(raw) == "Lesson notes"


# ── Mixed / real-world ──────────────────────────────────────────────────────


def test_mixed_reasoning_and_tokens() -> None:
    """
    Given  output mixing a reasoning block, control tokens and real text
    When   clean_ocr_text is called
    Then   only the transcribed text survives, whitespace-trimmed
    """
    raw = (
        "<think>The handwriting is hard to read.</think>"
        "<\uff5cAssistant\uff5c>\n\nMy summer holiday\nwas great.<|im_end|>"
    )

    assert clean_ocr_text(raw) == "My summer holiday\nwas great."


def test_blank_lines_collapsed() -> None:
    """
    Given  text whose internal blank runs exceed two newlines after removals
    When   clean_ocr_text is called
    Then   runs of 3+ newlines collapse to a single blank line
    """
    raw = "para one\n\n\n\npara two"

    assert clean_ocr_text(raw) == "para one\n\npara two"


# ── Pass-through / edge cases ───────────────────────────────────────────────


def test_clean_input_unchanged() -> None:
    """
    Given  already-clean OCR text
    When   clean_ocr_text is called
    Then   it is returned unchanged (modulo surrounding whitespace)
    """
    raw = "Line one\nLine two"

    assert clean_ocr_text(raw) == "Line one\nLine two"


@pytest.mark.parametrize("raw", ["", "   ", "\n\n"])
def test_empty_or_whitespace(raw: str) -> None:
    """
    Given  empty or whitespace-only input
    When   clean_ocr_text is called
    Then   the result is empty (empty string passes straight through)
    """
    assert clean_ocr_text(raw) == ""
