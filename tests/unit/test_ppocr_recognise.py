"""Tests for the built-in reader's recognition (pure, no model files)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from teachers_teammate.infrastructure.ppocr.detect import RotatedBox
from teachers_teammate.infrastructure.ppocr.recognise import (
    REC_MAX_WIDTH,
    REC_MIN_WIDTH,
    crop_line,
    ctc_decode,
    load_characters,
    recogniser_input,
)

_CHARS = ["<blank>", "a", "b", "l", "o", " "]


def _steps(*ids: int, probability: float = 0.9) -> np.ndarray:
    out = np.zeros((1, len(ids), len(_CHARS)), np.float32)
    for t, i in enumerate(ids):
        out[0, t, i] = probability
    return out


def test_ctc_merges_repeats_and_drops_blanks() -> None:
    """
    Given  steps a a blank b b
    When   decoded
    Then   the text is "ab"
    """
    assert ctc_decode(_steps(1, 1, 0, 2, 2), _CHARS)[0] == "ab"


def test_ctc_keeps_a_doubled_letter_split_by_a_blank() -> None:
    """
    Given  steps l blank l
    When   decoded
    Then   the text is "ll"
    """
    assert ctc_decode(_steps(3, 0, 3), _CHARS)[0] == "ll"


def test_ctc_decodes_the_space_class_and_confidence() -> None:
    """
    Given  steps a space b with probability 0.8
    When   decoded
    Then   the text is "a b" and the confidence 0.8
    """
    text, confidence = ctc_decode(_steps(1, 5, 2, probability=0.8), _CHARS)

    assert text == "a b"
    assert confidence == pytest.approx(0.8)


def test_ctc_all_blank_line_is_empty_with_zero_confidence() -> None:
    """
    Given  a line of blanks only
    When   decoded
    Then   the text is empty and the confidence 0.0
    """
    assert ctc_decode(_steps(0, 0, 0), _CHARS) == ("", 0.0)


@pytest.mark.parametrize(
    ("width", "expected"),
    [(60, REC_MIN_WIDTH), (480, 480), (4800, REC_MAX_WIDTH)],
    ids=["short line is padded", "normal line", "overlong line is capped"],
)
def test_recogniser_input_widths(width: int, expected: int) -> None:
    """
    Given  a line of height 48 and the given width
    When   the recogniser input is made
    Then   the blob is 48 high and as wide as expected, in NCHW float32
    """
    blob = recogniser_input(np.full((48, width, 3), 255, np.uint8))

    assert blob.shape == (1, 3, 48, expected)
    assert blob.dtype == np.float32
    assert blob[0, 0, 0, 0] == pytest.approx(1.0)


def test_recogniser_input_pads_with_zeros_on_the_right() -> None:
    """
    Given  a short white line
    When   the recogniser input is made
    Then   the columns after the line are zero
    """
    blob = recogniser_input(np.full((48, 60, 3), 255, np.uint8))

    assert np.all(blob[..., 60:] == 0.0)


def test_crop_line_straightens_a_tilted_box() -> None:
    """
    Given  a dark bar drawn at 10 degrees
    When   its box is cropped
    Then   the strip is upright: about as wide as the bar and as high as the bar is thick
    """
    image = np.full((300, 400, 3), 255, np.uint8)
    box = RotatedBox((200, 150), (200, 30), 10.0)
    cv2.fillPoly(image, [box.corners().astype(np.int32)], (0, 0, 0))

    strip = crop_line(image, box)

    assert strip.shape[1] == pytest.approx(200, abs=3)
    assert strip.shape[0] == pytest.approx(30, abs=3)
    assert strip.mean() < 30


@pytest.mark.parametrize("angle", [45.0, -45.0, 44.9, 90.0])
def test_crop_line_reads_a_bar_at_a_diagonal_angle(angle: float) -> None:
    """
    Given  a dark bar drawn at 45 degrees (where the corners tie on x + y or y - x)
    When   its box is cropped
    Then   the strip is the whole bar, not a degenerate warp: its long side is the bar's length
    """
    image = np.full((400, 400, 3), 255, np.uint8)
    box = RotatedBox((200, 200), (160, 30), angle)
    cv2.fillPoly(image, [box.corners().astype(np.int32)], (0, 0, 0))

    strip = crop_line(image, box)

    assert max(strip.shape[:2]) == pytest.approx(160, abs=3)
    assert min(strip.shape[:2]) == pytest.approx(30, abs=3)
    assert strip.mean() < 30


def test_crop_line_turns_a_tall_box() -> None:
    """
    Given  a box that is much taller than wide
    When   it is cropped
    Then   the strip is turned to be wider than high
    """
    image = np.full((300, 300, 3), 255, np.uint8)

    strip = crop_line(image, RotatedBox((150, 150), (30, 120), 0.0))

    assert strip.shape[1] > strip.shape[0]


def test_load_characters_puts_blank_first_and_space_last(tmp_path: Path) -> None:
    """
    Given  an inference.yml with a character_dict
    When   the characters are loaded
    Then   the list is blank, the dictionary, then a space
    """
    path = tmp_path / "inference.yml"
    path.write_text("PostProcess:\n  character_dict:\n  - a\n  - '0'\n  - ä\n", encoding="utf-8")

    assert load_characters(path) == ["<blank>", "a", "0", "ä", " "]
