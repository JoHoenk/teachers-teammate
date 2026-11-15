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
