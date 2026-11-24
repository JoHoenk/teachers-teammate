"""Unit tests for teachers_teammate.infrastructure.workflow.preprocess_service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from teachers_teammate.exceptions import PipelineInputError, RecognitionSwitchedOffError
from teachers_teammate.infrastructure.workflow.preprocess_service import PreprocessService
from teachers_teammate.interfaces import ImageUnit, InputPayload, TextUnit
from teachers_teammate.messages import MessageKey

# ── Helpers ────────────────────────────────────────────────────────────────


def _image_unit(image_path: Path) -> ImageUnit:
    return ImageUnit(image_path)


def _text_unit(text: str) -> TextUnit:
    return TextUnit(text)


def _make_svc(
    *,
    tmp_path: Path,
    payload: InputPayload | None = None,
    preprocess_return: tuple[Path, list[str]] | None = None,
) -> tuple[PreprocessService, MagicMock, MagicMock]:
    provider = MagicMock()
    preprocessor = MagicMock()

    if payload is not None:
        provider.load.return_value = payload

    if preprocess_return is not None:
        preprocessor.preprocess.return_value = preprocess_return

    provider_factory = MagicMock(return_value=provider)
    svc = PreprocessService(
        tmp_dir=tmp_path / "tmp",
        preprocessor=preprocessor,
        provider_factory=provider_factory,
    )
    return svc, provider, preprocessor


# ── Image unit path ────────────────────────────────────────────────────────


def test_preprocess_input_image_unit_calls_preprocessor(tmp_path: Path) -> None:
    """
    Given  an InputPayload with one image unit
    When   preprocess_input is called
    Then   preprocessor.preprocess is called with the image_path and the preprocessed path is returned
    """
    img = tmp_path / "raw.png"
    proc_img = tmp_path / "processed.png"
    payload = InputPayload(units=(_image_unit(img),), source_image=img)
    svc, _, preprocessor = _make_svc(
        tmp_path=tmp_path,
        payload=payload,
        preprocess_return=(proc_img, ["grayscale"]),
    )

    prepared = svc.preprocess_input(tmp_path / "doc.png")

    preprocessor.preprocess.assert_called_once_with(img)
    assert proc_img in prepared.ocr_inputs
    assert prepared.steps == ["grayscale"]
    assert prepared.raw_text_hint is None
    assert prepared.source_image == img


def test_preprocess_input_multiple_image_units_keep_their_own_steps(tmp_path: Path) -> None:
    """
    Given  two image units in the payload
    When   preprocess_input is called
    Then   preprocessor.preprocess is called twice and each page keeps its own steps
    """
    img1 = tmp_path / "p1.png"
    img2 = tmp_path / "p2.png"
    proc1 = tmp_path / "p1_proc.png"
    proc2 = tmp_path / "p2_proc.png"
    payload = InputPayload(units=(_image_unit(img1), _image_unit(img2)), source_image=img1)
    preprocessor = MagicMock()
    preprocessor.preprocess.side_effect = [(proc1, ["step_a"]), (proc2, ["step_b"])]
    provider = MagicMock(return_value=MagicMock(load=MagicMock(return_value=payload)))
    svc = PreprocessService(
        tmp_dir=tmp_path / "tmp",
        preprocessor=preprocessor,
        provider_factory=lambda suffix, td: provider(),
    )

    prepared = svc.preprocess_input(tmp_path / "doc.pdf")

    assert prepared.ocr_inputs == [proc1, proc2]
    assert prepared.steps_per_page == [("step_a",), ("step_b",)]
    assert prepared.steps == ["step_a", "step_b"]


# ── Text unit path ─────────────────────────────────────────────────────────


def test_preprocess_input_text_unit_returns_raw_text_hint(tmp_path: Path) -> None:
    """
    Given  an InputPayload with one text unit
    When   preprocess_input is called
    Then   raw_text_hint is the unit's text and preprocessed list is empty
    """
    payload = InputPayload(units=(_text_unit("hello world"),), source_image=None)
    svc, _, preprocessor = _make_svc(tmp_path=tmp_path, payload=payload)

    prepared = svc.preprocess_input(tmp_path / "doc.txt")

    assert prepared.ocr_inputs == []
    assert prepared.raw_text_hint == "hello world"
    assert prepared.source_image is None
    preprocessor.preprocess.assert_not_called()


def test_preprocess_input_multiple_text_units_joined(tmp_path: Path) -> None:
    """
    Given  two text units in the payload
    When   preprocess_input is called
    Then   raw_text_hint is the joined text of both units
    """
    payload = InputPayload(units=(_text_unit("first"), _text_unit("second")), source_image=None)
    svc, _, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    prepared = svc.preprocess_input(tmp_path / "doc.txt")

    assert prepared.raw_text_hint is not None
    assert "first" in prepared.raw_text_hint
    assert "second" in prepared.raw_text_hint


def test_preprocess_input_keeps_each_page_as_it_was_loaded(tmp_path: Path) -> None:
    """
    Given  two image units
    When   preprocess_input is called
    Then   the pages as they were loaded are handed on, in the order of the prepared pages
    """
    img1 = tmp_path / "p1.png"
    img2 = tmp_path / "p2.png"
    proc1 = tmp_path / "p1_proc.png"
    proc2 = tmp_path / "p2_proc.png"
    payload = InputPayload(units=(_image_unit(img1), _image_unit(img2)), source_image=img1)
    preprocessor = MagicMock()
    preprocessor.preprocess.side_effect = [(proc1, ["step_a"]), (proc2, ["step_b"])]
    provider = MagicMock(return_value=MagicMock(load=MagicMock(return_value=payload)))
    svc = PreprocessService(
        tmp_dir=tmp_path / "tmp",
        preprocessor=preprocessor,
        provider_factory=lambda suffix, td: provider(),
    )

    prepared = svc.preprocess_input(tmp_path / "doc.pdf")

    assert prepared.ocr_inputs == [proc1, proc2]
    assert prepared.original_pages == (img1, img2)


def test_preprocess_input_keeps_the_pages_rendered_for_the_scan(tmp_path: Path) -> None:
    """
    Given  a text unit whose payload also carries the pages rendered for the scan
    When   preprocess_input is called
    Then   the pages are handed on, in page order
    """
    pages = [tmp_path / "typed_page0.png", tmp_path / "typed_page1.png"]
    payload = InputPayload(
        units=(_text_unit("typed essay"),), source_image=pages[0], page_images=tuple(pages)
    )
    svc, _, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    prepared = svc.preprocess_input(tmp_path / "typed.pdf")

    assert prepared.page_images == tuple(pages)


# ── Error paths ────────────────────────────────────────────────────────────


def test_preprocess_input_empty_units_raises_value_error(tmp_path: Path) -> None:
    """
    Given  a payload with no units
    When   preprocess_input is called
    Then   PipelineInputError (a ValueError) with the no-units key is raised
    """
    payload = InputPayload(units=(), source_image=None)
    svc, _, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    with pytest.raises(PipelineInputError) as raised:
        svc.preprocess_input(tmp_path / "doc.png")

    assert raised.value.args[0].key is MessageKey.INPUT_WITHOUT_UNITS


def test_preprocess_input_no_content_raises_value_error(tmp_path: Path) -> None:
    """
    Given  a text unit whose text is empty (falsy)
    When   preprocess_input is called and produces no images and no text
    Then   PipelineInputError with the no-content key is raised
    """
    unit = TextUnit("")
    payload = InputPayload(units=(unit,), source_image=None)
    svc, _, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    with pytest.raises(PipelineInputError) as raised:
        svc.preprocess_input(tmp_path / "empty.txt")

    assert raised.value.args[0].key is MessageKey.INPUT_WITHOUT_CONTENT


# ── load_input ─────────────────────────────────────────────────────────────


def test_load_input_delegates_to_provider_factory(tmp_path: Path) -> None:
    """
    Given  a source file with suffix '.png'
    When   load_input is called
    Then   the provider_factory is called with the suffix and tmp_dir, and provider.load is called
    """
    payload = InputPayload(units=(_text_unit("hi"),), source_image=None)
    svc, provider, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    file = tmp_path / "image.png"
    result = svc.load_input(file)

    provider.load.assert_called_once_with(file)
    assert result == payload


# ── Text recognition switched off ──────────────────────────────────────────


def test_preprocess_input_image_unit_with_recognition_off_is_refused_before_preprocessing(
    tmp_path: Path,
) -> None:
    """
    Given  an input made of page images and text recognition switched off (allow_images=False)
    When   preprocess_input is called
    Then   RecognitionSwitchedOffError is raised and no page was preprocessed
    """
    payload = InputPayload(units=(_image_unit(tmp_path / "p1.png"),))
    svc, _, preprocessor = _make_svc(tmp_path=tmp_path, payload=payload)

    with pytest.raises(RecognitionSwitchedOffError, match="switched off"):
        svc.preprocess_input(tmp_path / "scan.pdf", allow_images=False)

    preprocessor.preprocess.assert_not_called()


def test_preprocess_input_text_unit_passes_with_recognition_off(tmp_path: Path) -> None:
    """
    Given  a text input (a text file or a PDF with a text layer) and recognition switched off
    When   preprocess_input is called with allow_images=False
    Then   the text is returned as it is
    """
    payload = InputPayload(units=(_text_unit("typed essay"),))
    svc, _, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    prepared = svc.preprocess_input(tmp_path / "essay.txt", allow_images=False)

    assert prepared.raw_text_hint == "typed essay"
