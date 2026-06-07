"""Unit tests for teachers_teammate.infrastructure.workflow.preprocess_service."""
# pylint: disable=W0404  # reimported — monkeypatch/patch blocks locally reimport the patched symbol

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from teachers_teammate.infrastructure.workflow.preprocess_service import PreprocessService
from teachers_teammate.interfaces import InputPayload, InputUnit


# ── Helpers ────────────────────────────────────────────────────────────────


def _image_unit(image_path: Path) -> InputUnit:
    return InputUnit(kind="image", image_path=image_path)


def _text_unit(text: str) -> InputUnit:
    return InputUnit(kind="text", text=text)


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
    payload = InputPayload(units=[_image_unit(img)], source_image=img)
    svc, _, preprocessor = _make_svc(
        tmp_path=tmp_path,
        payload=payload,
        preprocess_return=(proc_img, ["grayscale"]),
    )

    paths, steps, source_image, raw_text_hint = svc.preprocess_input(tmp_path / "doc.png")

    preprocessor.preprocess.assert_called_once_with(img)
    assert proc_img in paths
    assert steps == ["grayscale"]
    assert raw_text_hint is None
    assert source_image == img


def test_preprocess_input_multiple_image_units_only_first_steps_kept(tmp_path: Path) -> None:
    """
    Given  two image units in the payload
    When   preprocess_input is called
    Then   preprocessor.preprocess is called twice but only the first page's steps are returned
    """
    img1 = tmp_path / "p1.png"
    img2 = tmp_path / "p2.png"
    proc1 = tmp_path / "p1_proc.png"
    proc2 = tmp_path / "p2_proc.png"
    payload = InputPayload(units=[_image_unit(img1), _image_unit(img2)], source_image=img1)
    preprocessor = MagicMock()
    preprocessor.preprocess.side_effect = [(proc1, ["step_a"]), (proc2, ["step_b"])]
    provider = MagicMock(return_value=MagicMock(load=MagicMock(return_value=payload)))
    svc = PreprocessService(
        tmp_dir=tmp_path / "tmp",
        preprocessor=preprocessor,
        provider_factory=lambda suffix, td: provider(),
    )

    paths, steps, _, _ = svc.preprocess_input(tmp_path / "doc.pdf")

    assert paths == [proc1, proc2]
    assert steps == ["step_a"]  # only first page steps


# ── Text unit path ─────────────────────────────────────────────────────────


def test_preprocess_input_text_unit_returns_raw_text_hint(tmp_path: Path) -> None:
    """
    Given  an InputPayload with one text unit
    When   preprocess_input is called
    Then   raw_text_hint is the unit's text and preprocessed list is empty
    """
    payload = InputPayload(units=[_text_unit("hello world")], source_image=None)
    svc, _, preprocessor = _make_svc(tmp_path=tmp_path, payload=payload)

    paths, steps, source_image, raw_text_hint = svc.preprocess_input(tmp_path / "doc.txt")

    assert paths == []
    assert raw_text_hint == "hello world"
    assert source_image is None
    preprocessor.preprocess.assert_not_called()


def test_preprocess_input_multiple_text_units_joined(tmp_path: Path) -> None:
    """
    Given  two text units in the payload
    When   preprocess_input is called
    Then   raw_text_hint is the joined text of both units
    """
    payload = InputPayload(units=[_text_unit("first"), _text_unit("second")], source_image=None)
    svc, _, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    _, _, _, raw_text_hint = svc.preprocess_input(tmp_path / "doc.txt")

    assert raw_text_hint is not None
    assert "first" in raw_text_hint  # pylint: disable=unsupported-membership-test
    assert "second" in raw_text_hint  # pylint: disable=unsupported-membership-test


# ── Error paths ────────────────────────────────────────────────────────────


def test_preprocess_input_empty_units_raises_value_error(tmp_path: Path) -> None:
    """
    Given  a payload with no units
    When   preprocess_input is called
    Then   ValueError is raised
    """
    payload = InputPayload(units=[], source_image=None)
    svc, _, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    with pytest.raises(ValueError, match="no units"):
        svc.preprocess_input(tmp_path / "doc.png")


def test_preprocess_input_image_unit_without_path_raises_value_error(tmp_path: Path) -> None:
    """
    Given  an image unit where image_path is None
    When   preprocess_input is called
    Then   ValueError is raised mentioning missing image_path
    """
    unit = InputUnit(kind="image", image_path=None)
    payload = InputPayload(units=[unit], source_image=None)
    svc, _, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    with pytest.raises(ValueError, match="image_path"):
        svc.preprocess_input(tmp_path / "doc.png")


def test_preprocess_input_unsupported_unit_kind_raises_value_error(tmp_path: Path) -> None:
    """
    Given  a unit with an unsupported kind
    When   preprocess_input is called
    Then   ValueError is raised mentioning the unsupported kind
    """
    from unittest.mock import MagicMock as _MM  # noqa: PLC0415

    unit = _MM(kind="audio", image_path=None, text=None)
    payload = InputPayload(units=[unit], source_image=None)
    svc, _, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    with pytest.raises(ValueError, match="audio"):
        svc.preprocess_input(tmp_path / "doc.wav")


def test_preprocess_input_no_content_raises_value_error(tmp_path: Path) -> None:
    """
    Given  a text unit whose text is empty (falsy)
    When   preprocess_input is called and produces no images and no text
    Then   ValueError is raised
    """
    unit = InputUnit(kind="text", text="")
    payload = InputPayload(units=[unit], source_image=None)
    svc, _, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    with pytest.raises(ValueError, match="no usable"):
        svc.preprocess_input(tmp_path / "empty.txt")


# ── load_input ─────────────────────────────────────────────────────────────


def test_load_input_delegates_to_provider_factory(tmp_path: Path) -> None:
    """
    Given  a source file with suffix '.png'
    When   load_input is called
    Then   the provider_factory is called with the suffix and tmp_dir, and provider.load is called
    """
    payload = InputPayload(units=[_text_unit("hi")], source_image=None)
    svc, provider, _ = _make_svc(tmp_path=tmp_path, payload=payload)

    file = tmp_path / "image.png"
    result = svc.load_input(file)

    provider.load.assert_called_once_with(file)
    assert result == payload
