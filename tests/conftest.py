"""Shared pytest fixtures for the professor's-pet test suite."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil

import pytest

# Use headless Qt in CI/local Bazel test runs unless explicitly overridden.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

# ── Image / PDF fixtures ───────────────────────────────────────────────────


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    """A white PNG rendering legible black text ("Hello World").

    The text is large enough that a real OCR engine (e.g. Tesseract) returns a
    non-empty result, which the integration tests rely on. Other consumers only
    need a valid, openable image.
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    img = Image.new("RGB", (400, 120), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=48)
    draw.text((20, 30), "Hello World", fill=(0, 0, 0), font=font)
    path = tmp_path / "sample.png"
    img.save(path)
    return path


@pytest.fixture
def sample_pdf(tmp_path: Path, sample_png: Path) -> Path:  # pylint: disable=redefined-outer-name
    """A single-page PDF wrapping the sample PNG."""
    from PIL import Image  # noqa: PLC0415

    pdf_path = tmp_path / "sample.pdf"
    Image.open(sample_png).convert("RGB").save(str(pdf_path), "PDF")
    return pdf_path


# ── Config helper ──────────────────────────────────────────────────────────


def make_config(tmp_path: Path, **overrides: object):
    """Return a minimal :class:`~teachers_teammate.config.Config` suitable for testing.

    ``output_dir`` is created automatically.  Any field can be overridden via
    keyword arguments.
    """
    from teachers_teammate.config import Config, OcrConfig  # noqa: PLC0415

    output_dir = overrides.pop("output_dir", tmp_path / "output")
    assert isinstance(output_dir, Path)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Accept flat OCR overrides (ocr_engine=, ocr_model=, ...) for backwards
    # compatibility and assemble them into the nested OcrConfig value object.
    ocr_field_map = {
        "ocr_engine": "engine",
        "ocr_model": "model",
        "ocr_provider": "provider",
        "preprocess_method": "preprocess_method",
        "ocr_temperature": "temperature",
    }
    ocr_defaults: dict = {"engine": "tesseract", "model": "", "preprocess_method": "none"}
    for flat_key, ocr_key in ocr_field_map.items():
        if flat_key in overrides:
            ocr_defaults[ocr_key] = overrides.pop(flat_key)
    ocr = overrides.pop("ocr", OcrConfig(**ocr_defaults))

    defaults: dict = {
        "input_dir": tmp_path / "input",
        "output_dir": output_dir,
        "recursive": False,
        "debug": False,
        "ocr": ocr,
        "language": "English",
        "ollama_url": "http://127.0.0.1:11434",
        "correction_enabled": False,
        "correction_provider": "openai",
        "correction_model": "",
        "ocr_timeout": 60,
    }
    defaults.update(overrides)
    input_dir = defaults["input_dir"]
    assert isinstance(input_dir, Path)
    input_dir.mkdir(parents=True, exist_ok=True)
    return Config(**defaults)


# ── Pytest markers ─────────────────────────────────────────────────────────


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: pure unit test with no external dependencies")
    config.addinivalue_line("markers", "needs_tesseract: requires the tesseract binary on PATH")
    config.addinivalue_line("markers", "needs_ollama: requires a running Ollama instance")
    config.addinivalue_line("markers", "gui: exercises PySide6 widgets/event loop")
    config.addinivalue_line(
        "markers", "use_case: links test to a named use case from use_cases.trlc"
    )


_has_tesseract = shutil.which("tesseract") is not None
_has_pytesseract = importlib.util.find_spec("pytesseract") is not None

skip_no_tesseract = pytest.mark.skipif(
    not (_has_tesseract and _has_pytesseract),
    reason="tesseract binary or pytesseract Python package not available",
)
