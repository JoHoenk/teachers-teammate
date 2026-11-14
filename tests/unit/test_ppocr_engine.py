"""Tests for the built-in reader as an OCR processor (fake nets, no model files)."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

import cv2
import numpy as np
import pytest

from teachers_teammate.domain.local_models import LocalModel
from teachers_teammate.exceptions import ModelMissingError, OCRError, RunStoppedError
from teachers_teammate.infrastructure.ppocr.detect import DetectorSettings
from teachers_teammate.infrastructure.ppocr.engine import PpOcrProcessor

_CHARS = ["a", "b", "c"]  # classes: blank, a, b, c, space → 5


class _FakeStore:
    """Stands in for ModelStore: files are in place unless *missing* is set."""

    def __init__(self, folder: Path, *, missing: bool = False) -> None:
        self._folder = folder
        self._missing = missing
        (folder / "inference.yml").write_text(
            "PostProcess:\n  character_dict: [a, b, c]\n", encoding="utf-8"
        )

    def file(self, _model: LocalModel, name: str) -> Path:
        """Return the planted file, or raise as the real store does."""
        if self._missing:
            raise ModelMissingError("not downloaded")
        return self._folder / name


class _FakeDetector:
    """Answers with a map that has one bright bar per entry of *bars* (y0, y1, x0, x1)."""

    def __init__(self, bars: list[tuple[int, int, int, int]]) -> None:
        self._bars = bars

    def run(self, blob: np.ndarray) -> np.ndarray:
        """Return a ``[1, 1, H, W]`` map of the blob's size."""
        prob = np.zeros(blob.shape[2:], np.float32)
        for y0, y1, x0, x1 in self._bars:
            prob[y0:y1, x0:x1] = 0.9
        return prob[None, None]


class _FakeRecogniser:
    """Answers with the next of *words* (one class id each), for every call."""

    def __init__(self, words: list[int], *, classes: int = 5, on_call: Any = None) -> None:
        self._words = list(words)
        self._classes = classes
        self._on_call = on_call
        self.calls = 0

    def run(self, blob: np.ndarray) -> np.ndarray:
        """Return three steps that read as one letter."""
        letter = self._words[self.calls % len(self._words)]
        self.calls += 1
        if self._on_call:
            self._on_call(self.calls)
        out = np.zeros((1, 3, self._classes), np.float32)
        out[0, :, letter] = 0.9
        return out


def _page(tmp_path: Path) -> Path:
    path = tmp_path / "page.png"
    cv2.imwrite(str(path), np.full((320, 640, 3), 255, np.uint8))
    return path


def _reader(
    tmp_path: Path,
    recogniser: _FakeRecogniser,
    bars: list[tuple[int, int, int, int]] | None = None,
    *,
    stop: threading.Event | None = None,
    missing: bool = False,
    built: list[Path] | None = None,
) -> PpOcrProcessor:
    detector = _FakeDetector(bars or [(60, 90, 40, 300), (200, 230, 40, 300)])

    def factory(path: Path) -> Any:
        if built is not None:
            built.append(path)
        return detector if path.name == "det.onnx" else recogniser

    store = _FakeStore(tmp_path, missing=missing)
    # The fake store returns <folder>/<name>; give the two nets their own names.
    original = store.file

    def file(model: LocalModel, name: str) -> Path:
        original(model, name)
        if name == "inference.onnx":
            return tmp_path / ("det.onnx" if "det" in model.key else "rec.onnx")
        return tmp_path / name

    store.file = file  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
    return PpOcrProcessor(
        store,  # ty: ignore[invalid-argument-type]
        stop_event=stop,
        net_factory=factory,
        settings=DetectorSettings(line_end_pad=0.0),
    )


def test_two_lines_are_read_in_order(tmp_path: Path) -> None:
    """
    Given  a fake detector with two lines and a fake recogniser answering "a" then "b"
    When   the page is read
    Then   the text is the two lines in order
    """
    reader = _reader(tmp_path, _FakeRecogniser([1, 2]))

    assert reader.process_image(_page(tmp_path)) == "a\nb"


def test_words_of_a_row_are_joined_with_a_space(tmp_path: Path) -> None:
    """
    Given  two boxes in one row
    When   the page is read
    Then   they are one line, left to right, separated by a space
    """
    bars = [(60, 90, 40, 200), (60, 90, 300, 500)]
    reader = _reader(tmp_path, _FakeRecogniser([1, 3]), bars)

    assert reader.process_image(_page(tmp_path)) == "a c"


def test_empty_lines_are_dropped(tmp_path: Path) -> None:
    """
    Given  a recogniser that reads only blanks for the first line
    When   the page is read
    Then   only the second line is in the text
    """
    reader = _reader(tmp_path, _FakeRecogniser([0, 2]))

    assert reader.process_image(_page(tmp_path)) == "b"


def test_stop_after_the_first_line_raises_run_stopped(tmp_path: Path) -> None:
    """
    Given  a stop event that is set while the first line is read
    When   the page is read
    Then   RunStoppedError is raised before the second line
    """
    stop = threading.Event()
    recogniser = _FakeRecogniser([1, 2], on_call=lambda n: stop.set() if n == 1 else None)
    reader = _reader(tmp_path, recogniser, stop=stop)

    with pytest.raises(RunStoppedError):
        reader.process_image(_page(tmp_path))

    assert recogniser.calls == 1


def test_missing_models_raise_model_missing(tmp_path: Path) -> None:
    """
    Given  models that are not downloaded
    When   a page is read
    Then   ModelMissingError says how to get the reader
    """
    reader = _reader(tmp_path, _FakeRecogniser([1]), missing=True)

    with pytest.raises(ModelMissingError, match="not downloaded"):
        reader.process_image(_page(tmp_path))


def test_wrong_class_count_raises_ocr_error(tmp_path: Path) -> None:
    """
    Given  a recogniser whose output has another class count than the character list
    When   a page is read
    Then   OCRError says the model files don't match this version
    """
    reader = _reader(tmp_path, _FakeRecogniser([1], classes=9))

    with pytest.raises(OCRError, match="don't match this version"):
        reader.process_image(_page(tmp_path))


def test_language_does_not_change_the_result(tmp_path: Path) -> None:
    """
    Given  the same page
    When   it is read with two different languages
    Then   the text is the same
    """
    page = _page(tmp_path)

    first = _reader(tmp_path, _FakeRecogniser([1, 2])).process_image(page, "German")
    second = _reader(tmp_path, _FakeRecogniser([1, 2])).process_image(page, "English")

    assert first == second


def test_models_load_once_for_two_pages(tmp_path: Path) -> None:
    """
    Given  one reader
    When   two pages are read
    Then   the two nets were built once
    """
    built: list[Path] = []
    reader = _reader(tmp_path, _FakeRecogniser([1]), built=built)

    reader.process_image(_page(tmp_path))
    reader.process_image(_page(tmp_path))

    assert len(built) == 2


def test_unreadable_image_raises_ocr_error(tmp_path: Path) -> None:
    """
    Given  a file that is not an image
    When   it is read
    Then   OCRError says the image cannot be read
    """
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")

    with pytest.raises(OCRError, match="Cannot read image"):
        _reader(tmp_path, _FakeRecogniser([1])).process_image(bad)
