"""Unit tests for the install vocabulary: one item, one key."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.install_item import (
    PAGE_MODELS_ITEM,
    READER_ITEM,
    InstallItem,
    InstallKind,
)


@pytest.mark.unit
def test_an_item_is_named_by_its_kind_and_its_name() -> None:
    """
    Given  an item of a kind that has a name, and one that has none
    When   each is read as one string
    Then   the key names both parts, or only the kind when there is no name
    """
    assert InstallItem(InstallKind.PROVIDER, "mistral").key == "provider:mistral"
    assert InstallItem(InstallKind.UPDATE).key == "update"
    assert str(InstallItem(InstallKind.OLLAMA, "qwen3:8b")) == "ollama:qwen3:8b"


@pytest.mark.unit
def test_the_bundled_item_names_the_item_it_is_asked_about() -> None:
    """
    Given  the reader's item and the page models' item
    When   their bundled items are taken
    Then   each says "bundled" and carries the other's key as its name
    """
    assert READER_ITEM.bundled == InstallItem(InstallKind.BUNDLED, "reader:ppocr")
    assert PAGE_MODELS_ITEM.bundled == InstallItem(InstallKind.BUNDLED, "page_models:all")
