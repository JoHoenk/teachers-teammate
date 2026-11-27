"""Tests for the add-ons: what of them is installed, and what installing one does."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from teachers_teammate.application import providers as providers_module
from teachers_teammate.application.composition import build_app_services
from teachers_teammate.domain.install_item import (
    PAGE_MODELS_ITEM,
    READER_ITEM,
    InstallItem,
    InstallKind,
)
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.domain.ollama_snapshot import InstalledModel, ServerSnapshot
from teachers_teammate.infrastructure.onnx.store import ModelStore
from tests.conftest import fake_bundle, fake_reader_store


def _as_listing(fake):
    """Wrap a fake that returns model names as the checked listing the service asks for."""
    return lambda provider, **kwargs: ModelListing(list(fake(provider, **kwargs)))


# ── What is installed ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_installation_status_reports_the_built_in_reader(tmp_path: Path) -> None:
    """
    Given  one service with the reader's model files and one without
    When   get_installation_status() is called
    Then   the reader's item is True for the first and False for the second
    """
    with_models = build_app_services(model_store=fake_reader_store(tmp_path / "a")).addons
    without = build_app_services(
        model_store=fake_reader_store(tmp_path / "b", installed=False)
    ).addons

    assert with_models.get_installation_status()[READER_ITEM] is True
    assert without.get_installation_status()[READER_ITEM] is False


@pytest.mark.unit
def test_get_installation_status_says_which_models_the_app_carries_itself(
    tmp_path: Path,
) -> None:
    """
    Given  a store whose bundle holds the reader and the page models, and one whose bundle is empty
    When   get_installation_status() is called
    Then   the bundled flags say so, and a bundled model counts as installed without a download
    """
    from teachers_teammate.domain.local_models import PAGE_MODELS, READER_MODELS  # noqa: PLC0415

    bundle = fake_bundle(tmp_path / "bundle", (*READER_MODELS, *PAGE_MODELS))
    carried = build_app_services(
        model_store=ModelStore(tmp_path / "downloads", bundled_root=bundle)
    ).addons
    plain = build_app_services(model_store=ModelStore(tmp_path / "downloads")).addons

    status = carried.get_installation_status()

    assert status[READER_ITEM.bundled] is True
    assert status[PAGE_MODELS_ITEM.bundled] is True
    assert status[READER_ITEM] is True
    assert status[PAGE_MODELS_ITEM] is True
    assert plain.get_installation_status()[READER_ITEM.bundled] is False


@pytest.mark.unit
def test_get_installation_status_includes_langchain_packages(monkeypatch) -> None:
    """
    Given  get_installation_status() is called with a langchain_packages dict
    When   importlib.util.find_spec returns a spec for one module and None for another
    Then   the result contains 'langchain:{key}' entries matching the spec outcomes
    """
    import importlib.util  # noqa: PLC0415

    present_modules = {"langchain_core"}
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: object() if name in present_modules else None,
    )
    status = build_app_services().addons.get_installation_status(
        langchain_packages={"core": "langchain_core", "openai": "langchain_openai"},
    )

    assert status[InstallItem(InstallKind.LANGCHAIN, "core")] is True
    assert status[InstallItem(InstallKind.LANGCHAIN, "openai")] is False


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_installation_status_compares_normalised_tags(monkeypatch) -> None:
    """
    Given  an installed model "llama3.2:latest"
    When   the installation status asks for "llama3.2" (no suffix)
    Then   it counts as installed
    """
    monkeypatch.setattr(
        "teachers_teammate.infrastructure.ollama_utils.OllamaClient.snapshot",
        lambda _self: ServerSnapshot(models=(InstalledModel("llama3.2:latest"),)),
    )
    status = build_app_services().addons.get_installation_status(
        ollama_models=["llama3.2", "other:1b"], ollama_url="http://127.0.0.1:11434"
    )

    assert status[InstallItem(InstallKind.OLLAMA, "llama3.2")] is True
    assert status[InstallItem(InstallKind.OLLAMA, "other:1b")] is False


@pytest.mark.unit
def test_is_module_importable_returns_true_when_spec_found(monkeypatch) -> None:
    """
    Given  importlib.util.find_spec returns a non-None spec for a module name
    When   is_module_importable() is called with that module name
    Then   True is returned
    """
    import importlib.util  # noqa: PLC0415

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    assert build_app_services().addons.is_module_importable("some_package") is True


@pytest.mark.unit
def test_is_module_importable_returns_false_when_spec_missing(monkeypatch) -> None:
    """
    Given  importlib.util.find_spec returns None (module not installed)
    When   is_module_importable() is called
    Then   False is returned
    """
    import importlib.util  # noqa: PLC0415

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    assert build_app_services().addons.is_module_importable("missing_package") is False


@pytest.mark.unit
def test_is_module_importable_refreshes_the_import_caches() -> None:
    """
    Given  a package installed while the app is running
    When   is_module_importable() is asked
    Then   the import caches are invalidated first, so the new package is found at once
    """
    with patch("teachers_teammate.application.addons.importlib.invalidate_caches") as refresh:
        build_app_services().addons.is_module_importable("json")

    refresh.assert_called_once()


# ── Installing ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_addon_packages_dir_delegates(tmp_path: Path) -> None:
    """
    Given  the add-on service
    When   addon_packages_dir() is called
    Then   it delegates to addon_manager.get_packages_dir and returns a Path
    """
    with patch(
        "teachers_teammate.infrastructure.addon_manager.get_packages_dir",
        return_value=tmp_path,
    ):
        result = build_app_services().addons.addon_packages_dir()

    assert result == tmp_path


@pytest.mark.unit
def test_update_addons_hands_the_log_to_the_add_on_manager(monkeypatch) -> None:
    """
    Given  the add-on service
    When   the add-ons are updated
    Then   the add-on manager does it, with the caller's log callback
    """
    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    seen: list[object] = []
    monkeypatch.setattr(addon_manager, "update_installed_addons", seen.append)
    log = lambda _line: None  # noqa: E731

    build_app_services().addons.update_addons(log)

    assert seen == [log]
