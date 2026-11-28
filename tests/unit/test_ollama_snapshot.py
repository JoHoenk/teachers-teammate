"""Unit tests for teachers_teammate.domain.ollama_snapshot — one answer of a server."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.system_facts import LoadedModel
from teachers_teammate.domain.ollama_snapshot import InstalledModel, ServerSnapshot


def _snapshot() -> ServerSnapshot:
    """Return a server that answered: two models, one of them asked without ``:latest``."""
    return ServerSnapshot(
        version="0.12.3",
        models=(
            InstalledModel(tag="gemma4:e4b", digest="sha256:aaa", size_bytes=5_000_000_000),
            InstalledModel(tag="glm-ocr:latest", digest="sha256:bbb", size_bytes=2_000_000_000),
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("gemma4:e4b", "sha256:aaa"),
        ("glm-ocr", "sha256:bbb"),
        ("glm-ocr:latest", "sha256:bbb"),
        ("  gemma4:e4b  ", "sha256:aaa"),
        ("unknown:1b", ""),
    ],
)
def test_the_digest_of_a_tag_is_found_with_or_without_its_latest_suffix(
    tag: str, expected: str
) -> None:
    """
    Given  a server holding ``gemma4:e4b`` and ``glm-ocr:latest`` with their digests
    When   the digest of a tag is asked for, spelled with or without ``:latest``
    Then   the digest of that model comes back, and nothing for a model it does not hold
    """
    assert _snapshot().digest_of(tag) == expected


@pytest.mark.unit
def test_the_tags_and_the_sizes_are_what_the_server_listed() -> None:
    """
    Given  a server that answered with two models of known size
    When   its tags and its sizes are read
    Then   both keep the server's order, and each size belongs to its tag
    """
    snapshot = _snapshot()

    assert snapshot.tags == ["gemma4:e4b", "glm-ocr:latest"]
    assert snapshot.sizes() == {"gemma4:e4b": 5_000_000_000, "glm-ocr:latest": 2_000_000_000}


@pytest.mark.unit
def test_the_facts_of_a_snapshot_carry_the_version_and_the_digests_only() -> None:
    """
    Given  a server that answered with a version and two digests
    When   its facts are read, as a stored measurement compares itself to them
    Then   the version and the digests are there, and nothing is claimed about memory
    """
    facts = _snapshot().facts()

    assert facts.version == "0.12.3"
    assert facts.digest_of("glm-ocr") == "sha256:bbb"
    assert facts.loaded == ()


@pytest.mark.unit
def test_a_server_that_did_not_answer_lists_no_model() -> None:
    """
    Given  a snapshot taken of a server that could not be reached
    When   its tags, its sizes and its facts are read
    Then   it holds nothing and says why, so no caller mistakes it for an empty server
    """
    snapshot = ServerSnapshot(unreachable="✗ Cannot connect to Ollama at http://host:11434")

    assert snapshot.tags == []
    assert snapshot.sizes() == {}
    assert snapshot.digest_of("gemma4:e4b") == ""
    assert snapshot.facts().version == ""
    assert snapshot.unreachable.startswith("✗ Cannot connect")
    assert snapshot.reason == "Cannot connect to Ollama at http://host:11434"


@pytest.mark.unit
def test_the_facts_carry_what_the_caller_found_in_memory() -> None:
    """
    Given  a held answer, and a caller that asked the server what is in memory right now
    When   the facts are read with those models
    Then   they carry the answer's version and digests together with what is loaded
    """
    loaded = (LoadedModel(name="gemma4:e4b", size_bytes=1, size_vram_bytes=1),)

    facts = _snapshot().facts(loaded=loaded)

    assert (facts.version, facts.loaded) == ("0.12.3", loaded)
