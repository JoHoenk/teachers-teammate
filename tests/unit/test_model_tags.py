"""Unit tests for the model tag helpers (``domain/model_tags.py``)."""

from __future__ import annotations

from teachers_teammate.domain.model_tags import normalize_tag, short_model_name


def test_short_model_name_fits_a_narrow_line() -> None:
    """
    Given  a Hugging Face tag, a ":latest" tag, a plain tag and an empty string
    When   each is shortened
    Then   the repository name, the bare name, the unchanged tag and "" come back
    """
    assert short_model_name("hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0") == "chandra-ocr-2"
    assert short_model_name("hf.co/org/repo:Q4_K_M") == "repo"
    assert short_model_name("gemma4:latest") == "gemma4"
    assert short_model_name("gemma4:e4b") == "gemma4:e4b"
    assert short_model_name("deepseek-ocr") == "deepseek-ocr"
    assert short_model_name("") == ""


def test_normalize_tag_appends_latest_when_missing() -> None:
    """
    Given  a bare model name without a tag suffix
    When   normalize_tag is called
    Then   ":latest" is appended
    """
    assert normalize_tag("deepseek-ocr") == "deepseek-ocr:latest"


def test_normalize_tag_leaves_tagged_names_unchanged() -> None:
    """
    Given  model tags that already carry a ":tag" suffix
    When   normalize_tag is called
    Then   the tags are returned unchanged (apart from whitespace)
    """
    assert normalize_tag("qwen3-vl:8b") == "qwen3-vl:8b"
    assert normalize_tag(" glm-ocr:q8_0 ") == "glm-ocr:q8_0"
