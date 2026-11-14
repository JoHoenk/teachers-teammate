"""Tests for the pinned model catalogue (domain/local_models.py)."""

from __future__ import annotations

import re
from unittest.mock import patch

from teachers_teammate.domain import local_models
from teachers_teammate.domain.local_models import (
    PPOCR_SMALL_DET,
    READER_MODELS,
    READER_SIZE_LABEL,
    reader_fingerprint,
)


def test_every_hash_and_revision_has_its_full_length() -> None:
    """
    Given  the catalogue
    When   each file and revision is read
    Then   hashes are 64 hex and revisions 40
    """
    for model in READER_MODELS:
        assert re.fullmatch(r"[0-9a-f]{40}", model.revision)
        for file in model.files:
            assert re.fullmatch(r"[0-9a-f]{64}", file.sha256)
            assert file.size > 0


def test_url_has_the_documented_form() -> None:
    """
    Given  a model file
    When   its url is asked
    Then   it points at the pinned revision
    """
    file = PPOCR_SMALL_DET.files[0]

    assert PPOCR_SMALL_DET.url(file) == (
        "https://huggingface.co/PaddlePaddle/PP-OCRv6_small_det_onnx/resolve/"
        f"{PPOCR_SMALL_DET.revision}/inference.onnx"
    )


def test_size_is_the_sum_of_the_files_and_matches_the_label() -> None:
    """
    Given  the reader's models
    When   their sizes are added
    Then   they are the labelled 31 MB
    """
    total = sum(m.size for m in READER_MODELS)

    assert total == 31_191_354
    assert f"{round(total / 1_000_000)} MB" == READER_SIZE_LABEL


def test_short_revision_is_eight_characters() -> None:
    """
    Given  a model
    When   short_revision is read
    Then   it is the first 8 characters
    """
    assert PPOCR_SMALL_DET.short_revision == "28fe5895"


def test_fingerprint_changes_with_version_and_revision() -> None:
    """
    Given  the fingerprint
    When   READER_VERSION or a revision changes
    Then   it changes
    """
    base = reader_fingerprint()

    with patch.object(local_models, "READER_VERSION", 2):
        assert reader_fingerprint() != base
    other = local_models.LocalModel("x", "a/b", "f" * 40, PPOCR_SMALL_DET.files)
    with patch.object(local_models, "PPOCR_SMALL_DET", other):
        assert reader_fingerprint() != base
    assert base == "PP-OCRv6_small@28fe5895+b8f84f0b/v1"
