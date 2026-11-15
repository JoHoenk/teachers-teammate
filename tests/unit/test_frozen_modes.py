"""The hidden modes of the installed app: the smoke test's checks and the key cleanup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teachers_teammate import frozen_modes
from teachers_teammate.gui import _credential_store
from teachers_teammate.gui._credential_store import KEYRING_SERVICE

from ..gui._settings_stubs import FakeKeyring


@pytest.fixture(name="keychain")
def _keychain(monkeypatch) -> FakeKeyring:
    """A working system keychain (the settings-file fixture runs first and turns it off)."""
    fake = FakeKeyring()
    monkeypatch.setattr(_credential_store, "_keyring", lambda: fake)
    return fake


def test_smoke_test_passes_in_source_mode(tmp_path: Path) -> None:
    """Given the source tree, When the smoke test runs, Then it passes and writes its result."""
    out = tmp_path / "smoke.json"

    exit_code = frozen_modes.run_smoke_test(out)

    assert exit_code == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["failed"] == []
    assert result["versions"]["python"]


def test_smoke_test_names_a_module_that_does_not_import(tmp_path: Path, monkeypatch) -> None:
    """Given a module that cannot be imported, When the smoke test runs, Then it fails and names it."""
    monkeypatch.setattr(
        frozen_modes,
        "_LAZY_MODULES",
        ("qdarkstyle", "teachers_teammate.does_not_exist"),
    )
    out = tmp_path / "smoke.json"

    exit_code = frozen_modes.run_smoke_test(out)

    assert exit_code == 1
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert any("teachers_teammate.does_not_exist" in line for line in result["failed"])


def test_frozen_only_checks_are_skipped_in_source_mode() -> None:
    """Given a source run, When the checks are listed, Then the frozen-only ones are absent."""
    names = [name for name, _ in frozen_modes._checks()]

    assert "assets" not in names
    assert "pip" not in names
    assert "addon constraints" not in names
    assert {"imports", "qt", "reader", "pdf", "docx", "providers"} <= set(names)


def test_forget_keys_removes_every_provider_key(credential_file, keychain) -> None:
    """Given keys of two providers and an unrelated one, When forget_keys runs, Then only the provider keys are gone."""
    credential_file["credentials/OPENAI_API_KEY"] = "sk-openai"
    credential_file["credentials/ANTHROPIC_API_KEY"] = "sk-anthropic"
    credential_file["credentials/UNRELATED"] = "keep me"
    keychain.passwords[(KEYRING_SERVICE, "OPENAI_API_KEY")] = "sk-openai"

    exit_code = frozen_modes.forget_keys()

    assert exit_code == 0
    assert credential_file == {"credentials/UNRELATED": "keep me"}
    assert keychain.passwords == {}


def test_addon_constraints_check_compares_the_bundle_with_what_is_running(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a frozen bundle whose constraints pin the running numpy, pydantic, requests, pillow
    When   the add-on constraints check runs, and again with numpy pinned to another version
    Then   the first passes and the second names numpy; a bundle without the file fails too
    """
    import importlib.metadata  # noqa: PLC0415
    import sys  # noqa: PLC0415

    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    bundle = tmp_path / "bundle"
    pins = bundle / addon_manager.ADDON_CONSTRAINTS_RELATIVE
    pins.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    versions = {
        name: importlib.metadata.version(name)
        for name in frozen_modes._CONSTRAINED_RUNTIME_PACKAGES
    }
    lines = [f"{name}=={version}" for name, version in versions.items()]
    pins.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert frozen_modes._check_addon_constraints() is None

    pins.write_text("\n".join(["numpy==0.0.1", *lines[1:]]) + "\n", encoding="utf-8")
    assert "numpy" in (frozen_modes._check_addon_constraints() or "")

    pins.unlink()
    assert "is not in the bundle" in (frozen_modes._check_addon_constraints() or "")
