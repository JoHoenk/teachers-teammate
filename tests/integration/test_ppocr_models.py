"""The built-in reader on its real, pinned model files (fetched by Bazel).

Skipped under plain pytest: the files are only there when the Bazel target provides them.
"""

from __future__ import annotations

from pathlib import Path
import shutil

import numpy as np
import pytest
import yaml

from teachers_teammate.domain.local_models import PPOCR_SMALL_DET, PPOCR_SMALL_REC
from teachers_teammate.infrastructure.onnx.runner import CvDnnNet
from teachers_teammate.infrastructure.ppocr import detect as detect_module
from teachers_teammate.infrastructure.ppocr import recognise as recognise_module
from teachers_teammate.infrastructure.ppocr.detect import DetectorSettings
from teachers_teammate.infrastructure.ppocr.engine import PpOcrProcessor
from teachers_teammate.infrastructure.ppocr.recognise import load_characters
from tests.conftest import reader_store, skip_no_reader_models

pytestmark = [pytest.mark.needs_reader_models, skip_no_reader_models]

_FIXTURES = Path(__file__).parent.parent / "fixtures"

_LINES = [
    "The quick brown fox jumps over the lazy dog.",
    "We met at 7:30 and talked for an hour.",
    "Pack my box with five dozen liquor jugs.",
    "Homework: read pages 12 to 15, then write a summary.",
    "How vexingly quick daft zebras jump!",
    "Class 7b, room 204, Friday 14 November.",
    "Please bring a pencil, a ruler and your notebook.",
    "This sentence is written in English.",
]


def _yml(store, model) -> dict:
    with store.file(model, "inference.yml").open("rb") as handle:
        return yaml.safe_load(handle)


def test_constants_match_the_pinned_inference_yml(tmp_path: Path) -> None:
    """
    Given  the pinned detector and recogniser configuration files
    When   they are compared with the constants in the reader
    Then   thresholds, normalisation and sizes agree (a new revision that changes them fails)
    """
    store = reader_store(tmp_path)
    det = _yml(store, PPOCR_SMALL_DET)
    rec = _yml(store, PPOCR_SMALL_REC)
    defaults = DetectorSettings()

    post = det["PostProcess"]
    assert post["thresh"] == defaults.thresh
    assert post["box_thresh"] == defaults.box_thresh
    assert post["unclip_ratio"] == defaults.unclip_ratio
    assert post["max_candidates"] == defaults.max_candidates
    normalise = next(
        op["NormalizeImage"] for op in det["PreProcess"]["transform_ops"] if "NormalizeImage" in op
    )
    assert tuple(normalise["mean"]) == detect_module.MEAN
    assert tuple(normalise["std"]) == detect_module.STD
    shape = next(
        op["RecResizeImg"] for op in rec["PreProcess"]["transform_ops"] if "RecResizeImg" in op
    )["image_shape"]
    assert shape[1] == recognise_module.REC_HEIGHT
    assert shape[2] == recognise_module.REC_MIN_WIDTH


def test_class_count_is_the_models_output_size(tmp_path: Path) -> None:
    """
    Given  the recogniser and its character list
    When   a blank line is run through the model
    Then   the list has 18,710 classes, as many as the model's output
    """
    store = reader_store(tmp_path)
    characters = load_characters(store.file(PPOCR_SMALL_REC, "inference.yml"))
    net = CvDnnNet(store.file(PPOCR_SMALL_REC, "inference.onnx"))

    out = net.run(recognise_module.recogniser_input(np.full((48, 200, 3), 255, np.uint8)))

    assert len(characters) == 18_710
    assert out.shape[2] == 18_710


def test_net_loads_from_a_folder_with_umlauts(tmp_path: Path) -> None:
    """
    Given  a model file in a folder named "Jörg Müller"
    When   it is loaded through CvDnnNet
    Then   it loads and runs (the way a German Windows user folder would)
    """
    store = reader_store(tmp_path)
    folder = tmp_path / "Jörg Müller"
    folder.mkdir()
    target = folder / "inference.onnx"
    shutil.copy(store.file(PPOCR_SMALL_DET, "inference.onnx"), target)

    net = CvDnnNet(target)

    assert net.run(np.zeros((1, 3, 64, 64), np.float32)).shape == (1, 1, 64, 64)


def test_a_drawn_page_is_read_exactly(tmp_path: Path) -> None:
    """
    Given  a page drawn with Pillow: 8 lines of black text, about 10 pt at 300 DPI
    When   the built-in reader reads it
    Then   the text is exactly the 8 lines
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    page = Image.new("RGB", (2480, 3508), "white")
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=42)
    for i, text in enumerate(_LINES):
        draw.text((200, 300 + i * 160), text, font=font, fill="black")
    path = tmp_path / "page.png"
    page.save(path)

    text = PpOcrProcessor(reader_store(tmp_path)).process_image(path)

    assert text.split("\n") == _LINES


def test_umlauts_and_sharp_s_are_decoded(tmp_path: Path) -> None:
    """
    Given  a stored image of the line "Größe Übung für Äpfel"
    When   the built-in reader reads it
    Then   the umlauts and the ß come out right
    """
    text = PpOcrProcessor(reader_store(tmp_path)).process_image(_FIXTURES / "umlaut_line.png")

    assert text == "Größe Übung für Äpfel"
